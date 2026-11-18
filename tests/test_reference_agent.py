"""B-03 参考 Agent 的约束测试。

分两层锁：

**第一层：它真的跑起来了。** trace 结构自洽、`schema_valid=True`、`call_count>0`、
算出的答案与任务 YAML 里声明的 `expected` 一致。这一层是在**对撞**：
手写夹具的期望值与一次真实执行的结论必须相同，否则说明有人抄错了数。

**第二层：评测器区分得开。** 四种形态（正确 / 不剔除无效行 / 多传参数 / 不重试）
必须落到各自该失败的那条断言上。这一层比第一层重要——只证明"能拿满分"是没用的，
能证明"错了会被抓到、且抓在对的地方"才是评测器。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import mock_tools as mt  # noqa: E402
from arena import reference_agent as ra  # noqa: E402
from arena import trace_pipeline as tp  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
GIT_HASH = "deadbee"  # schema 要求 ^[0-9a-f]{7,40}$


@pytest.fixture(scope="module")
def task() -> dict:
    return dr.load_task(TASK_PATH)


@pytest.fixture(scope="module")
def tools_server(task):
    """真 socket：Agent 走 HTTP 调工具，不是函数内直调。"""
    srv, svc = mt.make_server(task, port=0)
    host, port = srv.server_address[:2]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://{host}:{port}", svc
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def _run(task, url, svc, flaw="correct") -> ra.AgentRun:
    svc.reset()  # reset_policy: per_submission
    return ra.ReferenceAgent(task, ra.ToolClient(url), flaw=flaw).run()


def _evaluate(task, run) -> dict:
    sub = {
        "submission_id": "sub-ref",
        "task_id": task["task_id"],
        "task_git_hash": GIT_HASH,
        "task_version": task["task_version"],
        "agent_id": ra.AGENT_ID,
        "evaluated_at": "2026-11-14T00:00:00Z",
    }
    built = tp.recompute_all(
        tp.assemble_raw(sub, task, trace=run.trace, artifacts=run.artifacts), task=task
    )
    report = dr.build_report(task, built)
    dr.validate_report(report)  # 不合法会抛 ReportError
    return report


# ── 第一层：真的跑起来了 ────────────────────────────────────────────────────

def test_trace_is_structurally_valid(task, tools_server):
    url, svc = tools_server
    run = _run(task, url, svc)
    report = _evaluate(task, run)

    assert report["trace_audit"]["schema_valid"] is True
    assert report["trace_audit"]["call_count"] > 0
    assert report["trace_audit"]["final_status"] == "completed"
    assert run.trace["final_status"] == "completed"

    item_fields = set(task["trace_requirements"]["minimum"]["tool_calls"]["item_fields"])
    calls = run.trace["tool_calls"]
    assert [c["call_index"] for c in calls] == list(range(1, len(calls) + 1))
    for call in calls:
        # 合法性用「⊆ 允许集」而不是「= 全量」：item_fields 是允许字段表，
        # success 的调用不带 http_status/error 是正常的。
        assert set(call) <= item_fields, f"出现了未声明字段：{sorted(set(call) - item_fields)}"
        assert {"call_index", "name", "params", "status"} <= set(call)
        if call["status"] == "error":
            assert {"http_status", "error"} <= set(call)
        else:
            assert "result_summary" in call

    # 注：平台侧当前**只**校验 call_index / status / params 类型（`call_sequence_problems`），
    # `status=error ⇒ http_status` 这一条结构层不校验，归 R3 执行器按失败步 spec 判定
    # （B-22 已关：error/result_summary 不做条件必填，只作诊断线索）。
    # 所以上面这组断言锁的是「参考 Agent 自己合规」，不是「平台拦得住不合规的」。


def test_pipeline_scores_a_real_run_at_full_marks(task, tools_server):
    """真跑一遍能拿满分——而且不是靠手写断言结果，是现场现算的。"""
    url, svc = tools_server
    report = _evaluate(task, _run(task, url, svc))

    assert report["score"]["ratio"] == 1.0
    assert report["score"]["by_state"]["pass"] == 5
    assert report["skill_gaps"] == []


def test_real_answer_matches_declared_expected(task, tools_server):
    """对撞：一次真执行算出的答案 == 任务 YAML 里手写的 expected。

    这条第一次跑就绿，不代表它没用：它锁住的是「以后改夹具行数/金额，
    忘了同步 expected」这类错误——那时它会红，且红得明确。
    """
    url, svc = tools_server
    run = _run(task, url, svc)
    expected = task["test_inputs"]["expected"]
    assert run.answer == expected


def test_retry_repeats_the_same_params(task, tools_server):
    """R3 的前提：重试必须是同参重试，不是"换个参数重来"。"""
    url, svc = tools_server
    calls = _run(task, url, svc).trace["tool_calls"]
    failed = [c for c in calls if c["status"] == "error"]
    assert len(failed) == 1 and failed[0]["http_status"] == 500
    retry = calls[failed[0]["call_index"]]  # 紧随其后的下一次调用
    assert retry["params"] == failed[0]["params"]
    assert retry["status"] == "success"


def test_artifacts_carry_the_written_report(task, tools_server):
    url, svc = tools_server
    run = _run(task, url, svc)
    assert list(run.artifacts) == [ra.REPORT_PATH]
    assert '"valid_count"' in run.artifacts[ra.REPORT_PATH]


# ── 第二层：评测器区分得开 ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "flaw, expected_fails, expected_ratio",
    [
        ("correct", set(), 1.0),
        # R2 权重 2 ⇒ 只丢 R2 时 2/4
        ("wrong_filter", {"R2_report_correct"}, 0.5),
        # R4 权重 1 ⇒ 只丢 R4 时 3/4
        ("extra_param", {"R4_no_param_hallucination"}, 0.75),
        # 不重试 ⇒ 拉不全、写不出报告：门禁 R1/R5 挂，R3 无重试证据
        ("no_retry", {"R1_file_exists", "R5_clean_exit", "R3_retry_evidence"}, 0.0),
    ],
)
def test_flaw_matrix(task, tools_server, flaw, expected_fails, expected_ratio):
    url, svc = tools_server
    report = _evaluate(task, _run(task, url, svc, flaw=flaw))

    fails = {a["id"] for a in report["assertions"] if a["state"] == "fail"}
    assert fails == expected_fails, (
        f"{flaw}: 失败集 {sorted(fails)} != 预期 {sorted(expected_fails)}；"
        f"全部状态 {[(a['id'], a['state']) for a in report['assertions']]}"
    )
    assert report["score"]["ratio"] == pytest.approx(expected_ratio)


def test_crashed_run_submits_no_artifact(task, tools_server):
    """跑挂了要如实交一份"没产物"的提交，而不是干脆不提交。"""
    url, svc = tools_server
    run = _run(task, url, svc, flaw="no_retry")
    assert run.trace["final_status"] == "crashed"
    assert run.artifacts == {}
    assert not run.answer
    assert any("执行中止" in n for n in run.notes)


# ── 配置错误在构造期暴露 ────────────────────────────────────────────────────

def test_missing_fault_target_raises_at_construction(task, tools_server):
    """任务没声明 target_tool ⇒ 构造期就报错，不带着半句话跑到崩溃。"""
    url, _ = tools_server
    broken = {k: v for k, v in task.items() if k != "fault_injection"}
    with pytest.raises(ra.AgentError, match="target_tool"):
        ra.ReferenceAgent(broken, ra.ToolClient(url))


def test_unknown_flaw_is_refused(task, tools_server):
    url, _ = tools_server
    with pytest.raises(ra.AgentError, match="未实现"):
        ra.ReferenceAgent(task, ra.ToolClient(url), flaw="makes_coffee")
