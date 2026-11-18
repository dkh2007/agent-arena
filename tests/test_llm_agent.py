"""B-03b ② LLM Agent 的约束测试（离线跑，不联网、不花钱）。

分三层，每层锁一件**会静默坏掉**的事：

**第一层：提示的边界。** 提示里不能有期望答案（放了等于让模型抄），不能有故障位
（写了等于告诉它"这页会挂，直接重试"）；情景必须原样照搬 YAML，不许改写。

**第二层：框架不替模型做事。** 这条是本模块最容易犯的错，也最不容易被发现：
只要循环里加一句"5xx 就再发一次"，R3 对**任何**模型都恒为 pass——一条不可能变红的绿，
与任务 YAML 里 `platform_retry: 0` 要防的是同一件事，只是往上挪了一层。
这里的用例不检查分数，检查**调用次数**：3 个决策 → 3 次调用，一次都不许多。

**第三层：端到端。** 剧本模型跑完整流程 → trace 结构合法、R3 pass、产物来自成功的
`write_report` 调用、平台账本与自报逐条一致。以及三条失败路径（接口报错 / 输出不合协议 /
步数用尽）必须落成 `crashed` 而不是"看起来正常"的收尾。
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import llm_agent as la  # noqa: E402
from arena import platform_capture as pc  # noqa: E402
from arena import trace_pipeline as tp  # noqa: E402
from arena.reference_agent import ToolClient  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
GIT_HASH = "deadbee"
REPORT = "/workspace/report.json"
ANSWER = {"valid_count": 17, "total_amount": 1837.99}


@pytest.fixture(scope="module")
def task() -> dict:
    return dr.load_task(TASK_PATH)


@pytest.fixture()
def served(task):
    """平台代理 + mock 服务。一个用例一份新账本（per_submission 口径）。"""
    recorder = pc.CaptureRecorder()
    srv, svc = pc.capture_proxy(task, recorder, port=0)
    host, port = srv.server_address[:2]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://{host}:{port}", svc, recorder
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def _call(tool: str, **params) -> dict:
    return {"action": "call_tool", "tool": tool, "params": params}


def _finish(answer: dict | None = None) -> dict:
    return {"action": "finish", "answer": ANSWER if answer is None else answer}


def _report_call(**over) -> dict:
    content = json.dumps(ANSWER, ensure_ascii=False, sort_keys=True)
    params = {"path": REPORT, "content": content}
    params.update(over)
    return _call("write_report", **params)


# 完整正确的一轮：翻页 → 撞上故障 → 重试 → 写报告 → 结束。
GOOD = [
    _call("fetch_orders", offset=0, limit=10),
    _call("fetch_orders", offset=10, limit=10),
    _call("fetch_orders", offset=10, limit=10),
    _report_call(),
    _finish(),
]


def _run(task, url, script, **kw):
    model = la.ScriptedModel(script)
    agent = la.LLMAgent(task, ToolClient(url), model.client(), **kw)
    return agent.run(), model


def _evaluate(task, run) -> dict:
    sub = {
        "submission_id": "sub-llm",
        "task_id": task["task_id"],
        "task_git_hash": GIT_HASH,
        "task_version": task["task_version"],
        "agent_id": run.trace["agent_id"],
        "evaluated_at": "2026-11-18T00:00:00Z",
    }
    built = tp.recompute_all(
        tp.assemble_raw(sub, task, trace=run.trace, artifacts=run.artifacts), task=task
    )
    report = dr.build_report(task, built)
    dr.validate_report(report)
    return report


def _state(report: dict) -> dict[str, str]:
    return {a["id"].split("_")[0]: a["state"] for a in report["assertions"]}


# ── 第一层：提示的边界 ─────────────────────────────────────────────────────

def test_prompt_does_not_carry_the_expected_answer(task):
    """期望值进提示 = 这道题证明不了"能算出来"。数值、订单号一个都不许出现。"""
    text = "\n".join(m["content"] for m in la.build_messages(task))
    for leak in ("1837.99", "o-001", "o-018", "valid_count\": 17"):
        assert leak not in text, f"提示里出现了期望答案的一部分：{leak!r}"


def test_prompt_does_not_reveal_where_the_fault_is_injected(task):
    """故障位（offset=10、attempt=1、failures）写进提示，R3 就变成照做的结果。"""
    text = "\n".join(m["content"] for m in la.build_messages(task))
    for leak in ("fault_injection", "attempt", "failures", "internal_error"):
        assert leak not in text, f"提示泄漏了故障注入细节：{leak!r}"


def test_tool_specs_carry_bounds_but_not_the_mock(task):
    """模型要知道 limit≤10（否则它没法避免 invalid_limit），但不该看到 mock 与 fixture。"""
    specs = la.tool_specs(task)
    fetch = next(s for s in specs if s["name"] == "fetch_orders")
    limit = next(p for p in fetch["parameters"] if p["name"] == "limit")
    assert limit["maximum"] == 10 and limit["minimum"] == 1

    dumped = json.dumps(specs, ensure_ascii=False)
    for leak in ("mock", "fixture", "pagination", "orders_fixture"):
        assert leak not in dumped


def test_scenario_is_passed_through_verbatim(task):
    """改写情景会造出两份真相源：YAML 改了、提示还是旧的，判出来是"答非所问"。"""
    messages = la.build_messages(task)
    assert messages[1]["content"] == task["scenario"].strip()
    assert messages[0]["role"] == "system"


def test_parse_decision_accepts_a_fenced_object_and_rejects_prose():
    ok = la.parse_decision('```json\n{"action":"finish","answer":{}}\n```')
    assert ok["action"] == "finish"
    with pytest.raises(la.DecisionError):
        la.parse_decision("我决定先调用 fetch_orders")
    with pytest.raises(la.DecisionError):
        la.parse_decision('{"action":"call_tool"}')
    with pytest.raises(la.DecisionError):
        la.parse_decision('{"action":"teleport","tool":"x"}')


# ── 第二层：框架不替模型做事 ───────────────────────────────────────────────

def test_harness_issues_exactly_one_call_per_decision(task, served):
    """3 个调用决策 → 账本 3 条。多一条就说明框架自己重试了，R3 从此恒为 pass。"""
    url, svc, recorder = served
    svc.reset()
    script = GOOD[:3] + [_finish()]
    run, _ = _run(task, url, script)

    assert len(run.trace["tool_calls"]) == 3
    assert len(recorder.trace_items()) == 3
    assert [c["status"] for c in run.trace["tool_calls"]] == ["success", "error", "success"]


def test_a_model_that_does_not_retry_is_judged_as_not_retrying(task, served):
    """不重试的剧本必须拿到 R3 fail —— 分数差别是这条链路唯一的意义所在。"""
    url, svc, _ = served
    svc.reset()
    give_up = [
        _call("fetch_orders", offset=0, limit=10),
        _call("fetch_orders", offset=10, limit=10),
        _finish({}),
    ]
    run, _ = _run(task, url, give_up)
    state = _state(_evaluate(task, run))
    assert state["R3"] == "fail"
    assert run.trace["final_status"] == "completed"


def test_a_retrying_model_gets_r3_pass_on_the_same_harness(task, served):
    """与上一条只差剧本里多一次调用：结论的差别只能来自决策，不能来自框架。"""
    url, svc, _ = served
    svc.reset()
    run, _ = _run(task, url, GOOD)
    state = _state(_evaluate(task, run))
    assert state["R3"] == "pass"


def test_unknown_tool_is_refused_and_never_enters_the_trace(task, served):
    """未发生的调用不能记 —— 账本里没有它，记了会让对账把"幻觉"错报成"篡改"。"""
    url, svc, recorder = served
    svc.reset()
    script = [_call("delete_everything", id=1), _call("fetch_orders", offset=0, limit=10), _finish({})]
    run, _ = _run(task, url, script)

    assert [c["name"] for c in run.trace["tool_calls"]] == ["fetch_orders"]
    assert run.meta["rejected_actions"] == [
        {"step": 1, "tool": "delete_everything", "reason": "未知工具"}
    ]
    assert len(recorder.trace_items()) == 1


# ── 第三层：端到端与失败路径 ───────────────────────────────────────────────

def test_full_run_is_structurally_valid_and_scores(task, served):
    url, svc, _ = served
    svc.reset()
    run, _ = _run(task, url, GOOD)
    report = _evaluate(task, run)

    assert run.trace["final_status"] == "completed"
    assert report["trace_audit"]["schema_valid"] is True
    assert report["trace_audit"]["invalid_reasons"] == []
    assert report["trace_audit"]["call_count"] == 4
    state = _state(report)
    assert state["R1"] == "pass" and state["R2"] == "pass" and state["R3"] == "pass"
    assert state["R5"] == "pass"
    assert run.artifacts == {REPORT: json.dumps(ANSWER, ensure_ascii=False, sort_keys=True)}


def test_self_report_matches_the_platform_ledger(task, served):
    """LLM Agent 的 trace 与平台账本必须逐条一致 —— 模型驱动不改变"记录归平台"。"""
    url, svc, recorder = served
    svc.reset()
    run, _ = _run(task, url, GOOD)
    result = pc.compare_traces(run.trace["tool_calls"], recorder.trace_items())
    assert result.ok, result.mismatches


def test_finishing_without_writing_leaves_no_artifact(task, served):
    """产物只从成功的 write_report 里取，不从模型的自述里取。"""
    url, svc, _ = served
    svc.reset()
    run, _ = _run(task, url, [_call("fetch_orders", offset=0, limit=10), _finish()])
    assert run.artifacts == {}
    assert _state(_evaluate(task, run))["R1"] == "fail"


def test_interface_failure_becomes_crashed_not_completed(task, served):
    url, svc, _ = served
    svc.reset()
    run, model = _run(task, url, [500, 500, 500])  # 三次 5xx：LLMClient 自己重试两轮后放弃
    assert run.trace["final_status"] == "crashed"
    assert len(model.requests) == 3
    assert any("LLMError" in n for n in run.notes)


def test_truncated_output_is_named_as_a_budget_problem(task, served):
    """空正文 + finish_reason=length 是"预算被思考吃光"，不能报成接口故障。"""
    url, svc, _ = served
    svc.reset()
    truncated = lambda _p: (200, {  # noqa: E731 - 剧本里的一次性传输
        "model": "scripted",
        "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        "usage": {"completion_tokens": 4, "completion_tokens_details": {"reasoning_tokens": 4}},
    })
    run, _ = _run(task, url, [truncated])
    assert run.trace["final_status"] == "crashed"
    assert any("max_tokens" in n for n in run.notes)


def test_garbage_output_gets_one_corrective_turn_then_gives_up(task, served):
    url, svc, _ = served
    svc.reset()
    run, model = _run(task, url, ["先看看数据", "我需要更多信息", "无法回答"])
    assert run.trace["final_status"] == "crashed"
    assert run.meta["bad_outputs"] == 3  # max_bad_outputs=2 → 第 3 次放弃
    assert len(model.requests) == 3
    # 纠正消息里不许出现正确决策：喂答案就不是它自己在做
    corrective = model.requests[1]["messages"][-1]["content"]
    assert "call_tool" not in corrective and "fetch_orders" not in corrective


def test_step_budget_exhaustion_is_crashed_and_says_so(task, served):
    url, svc, _ = served
    svc.reset()
    loop = [_call("fetch_orders", offset=0, limit=10)] * 3
    run, _ = _run(task, url, loop, max_steps=3)
    assert run.trace["final_status"] == "crashed"
    assert any("预算用尽" in n for n in run.notes)


def test_meta_pins_what_is_needed_to_reproduce_the_claim(task, served):
    """不钉住模型与提示版本，"这个模型会不会自己重试"下次跑不出来也没法归因。"""
    url, svc, _ = served
    svc.reset()
    run, _ = _run(task, url, GOOD)
    assert run.meta["prompt_version"] == la.PROMPT_VERSION
    assert run.meta["model"] == "scripted-agent-model"
    assert run.meta["temperature"] == 0.0
    assert run.meta["llm_calls"] == 5 and run.meta["bad_outputs"] == 0
