"""B-03b ① 平台侧采集的约束测试。

分三层：

**第一层：账本记的是平台看到的东西。** 走真 socket 调两次（一次成功、一次故障注入 500），
账本必须是 2 条、编号 1..2、status 分别 success/error——编号由平台给，不是 Agent 给的。

**第二层：对账区分得开。** 逐条比 name / params / status / http_status；删掉失败调用、
改写编号、改参数、改状态、缺 http_status 五种篡改都要被点名到"第几条 + 哪个字段"。
这一层是这条待办的真正判据：**故意上报一份删掉失败调用的 trace，对账必须报不一致。**

**第三层：端到端对撞。** 同一个参考 Agent，先指向平台代理跑一遍 → 平台账本与它自报的
trace 必须一致；再手工删掉那条失败调用 → 必须 mismatch。只测纯函数会漏掉"代理挂错钩子"
这种错（账本记的是别的东西，对账自然永远一致）。
"""

from __future__ import annotations

import copy
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import platform_capture as pc  # noqa: E402
from arena import reference_agent as ra  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"


@pytest.fixture(scope="module")
def task() -> dict:
    return dr.load_task(TASK_PATH)


@pytest.fixture()
def served(task):
    """平台代理：mock 服务 + 记账钩子。每个用例一份新账本（per_submission）。"""
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


def _call(url: str, name: str, params: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{url}/tools/{name}",
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# ── 第一层：账本记的是平台看到的 ─────────────────────────────────────────────


def test_ledger_numbers_calls_from_one_by_arrival_order(served):
    url, _svc, recorder = served
    # offset=0 的页区间是 [0,10)，不含第 10 条，所以不触发故障。
    # 原来写的是 offset=1——B-31 改成覆盖语义后 [1,11) 含第 10 条，会返回 500。
    assert _call(url, "fetch_orders", {"offset": 0, "limit": 10})[0] == 200
    assert _call(url, "fetch_orders", {"offset": 10, "limit": 10})[0] == 500

    items = recorder.trace_items()
    assert [c["call_index"] for c in items] == [1, 2]
    assert [c["status"] for c in items] == ["success", "error"]
    assert items[1]["http_status"] == 500
    assert items[1]["error"] == "internal_error"


def test_reset_starts_a_new_ledger(served):
    url, svc, recorder = served
    _call(url, "fetch_orders", {"offset": 0, "limit": 10})
    assert len(recorder) == 1
    svc.reset()  # reset_policy: per_submission
    recorder.reset()
    _call(url, "fetch_orders", {"offset": 0, "limit": 10})
    assert [c["call_index"] for c in recorder.trace_items()] == [1]


def test_ledger_failure_means_no_result_is_handed_out(task, served):
    """fail closed：记账抛异常 ⇒ 客户端拿不到结果（宁可这次提交报废，也不留空账）。"""
    url, _svc, _recorder = served

    class Exploding(pc.CaptureRecorder):
        def record(self, *a, **kw):
            raise RuntimeError("账本写不进去")

    recorder = Exploding()
    srv, _svc2 = pc.capture_proxy(task, recorder, port=0)
    host, port = srv.server_address[:2]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        req = urllib.request.Request(
            f"http://{host}:{port}/tools/fetch_orders",
            data=b'{"offset": 1, "limit": 10}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(Exception):
            urllib.request.urlopen(req, timeout=5).read()
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


# ── 第二层：对账区分得开（本条待办的判据） ───────────────────────────────────

CAPTURED = [
    {"call_index": 1, "name": "fetch_orders", "params": {"offset": 1, "limit": 10},
     "status": "success", "http_status": 200},
    {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10},
     "status": "error", "http_status": 500, "error": "internal_error"},
    {"call_index": 3, "name": "fetch_orders", "params": {"offset": 10, "limit": 10},
     "status": "success", "http_status": 200},
]


def _reported(**override) -> list[dict]:
    """自报 trace：与平台记录同形，但**成功调用不带 http_status**（Agent 的真实写法）。"""
    out = []
    for c in CAPTURED:
        item = {k: v for k, v in c.items() if k != "http_status"}
        if c["status"] == "error":
            item["http_status"] = c["http_status"]
        item.update(override)
        out.append(item)
    return out


def test_identical_reports_match():
    result = pc.compare_traces(_reported(), CAPTURED)
    assert result.ok, result.mismatches
    assert result.verdict() == "match"
    assert result.compared == 3


def test_agent_dropping_the_failed_call_is_named():
    """最要紧的那种篡改：把重试藏起来，看起来像一次成功拉取。"""
    reported = [_reported()[0], _reported()[2]]
    result = pc.compare_traces(reported, CAPTURED)
    assert not result.ok
    text = "\n".join(result.mismatches)
    assert "条数不符：平台记录 3 条，自报 2 条" in text
    assert "call_index=2 平台有、自报缺" in text
    assert "fetch_orders status=error http_status=500" in text


def test_renumbering_the_failed_call_is_named_not_silently_shifted():
    """把失败调用改成第 3 条、成功那条改成第 2 条——按次序比会当成参数不符，按编号比才说得清。"""
    rep = _reported()
    rep[1]["call_index"], rep[2]["call_index"] = 3, 2
    result = pc.compare_traces(rep, CAPTURED)
    assert not result.ok
    text = "\n".join(result.mismatches)
    assert "call_index=2 status 不符：平台 'error' / 自报 'success'" in text
    assert "call_index=3 status 不符：平台 'success' / 自报 'error'" in text


def test_param_tampering_says_which_key():
    rep = _reported()
    rep[1]["params"] = {"offset": 10, "limit": 5}
    result = pc.compare_traces(rep, CAPTURED)
    text = "\n".join(result.mismatches)
    assert "call_index=2 参数不符" in text
    assert "limit" in text


def test_param_tampering_says_missing_and_extra_keys():
    rep = _reported()
    rep[0]["params"] = {"offset": 1, "page": 10}
    text = "\n".join(pc.compare_traces(rep, CAPTURED).mismatches)
    assert "自报缺 ['limit']" in text
    assert "自报多 ['page']" in text


def test_status_flip_is_named():
    rep = _reported()
    rep[1]["status"] = "success"
    text = "\n".join(pc.compare_traces(rep, CAPTURED).mismatches)
    assert "call_index=2 status 不符：平台 'error' / 自报 'success'" in text


def test_missing_http_status_on_a_failure_is_named():
    rep = _reported()
    rep[1].pop("http_status")
    text = "\n".join(pc.compare_traces(rep, CAPTURED).mismatches)
    assert "call_index=2 自报缺 http_status" in text


def test_missing_http_status_on_success_is_fine():
    """B-22 口径：http_status 只对 `status=error` 必填，成功调用不给不算错。"""
    assert pc.compare_traces(_reported(), CAPTURED).ok


def test_wrong_http_status_is_named():
    rep = _reported()
    rep[1]["http_status"] = 200
    text = "\n".join(pc.compare_traces(rep, CAPTURED).mismatches)
    assert "call_index=2 http_status 不符：平台 500 / 自报 200" in text


def test_invented_call_is_named():
    rep = _reported() + [{"call_index": 4, "name": "write_report",
                          "params": {"path": "/workspace/report.json"}, "status": "success"}]
    text = "\n".join(pc.compare_traces(rep, CAPTURED).mismatches)
    assert "call_index=4 自报有、平台没记录" in text


def test_duplicate_index_is_named():
    rep = _reported()
    rep[2]["call_index"] = 2
    text = "\n".join(pc.compare_traces(rep, CAPTURED).mismatches)
    assert "自报 trace 里 call_index 有重复" in text


def test_empty_traces_match():
    assert pc.compare_traces([], []).ok


# ── 第三层：端到端对撞（代理挂对了没有） ─────────────────────────────────────


def test_real_run_matches_its_own_report(served, task):
    url, svc, recorder = served
    svc.reset()
    run = ra.ReferenceAgent(task, ra.ToolClient(url)).run()

    reported = run.trace["tool_calls"]
    result = pc.compare_traces(reported, recorder.trace_items())
    assert result.ok, result.mismatches
    assert result.compared == len(reported) >= 3  # 至少 3 次：正常页 + 失败 + 重试

    trimmed = [c for c in reported if c.get("status") != "error"]
    tampered = pc.compare_traces(trimmed, recorder.trace_items())
    assert not tampered.ok
    assert "平台有、自报缺" in "\n".join(tampered.mismatches)


def test_fabricated_retry_passes_the_evaluator_but_not_the_capture(served, task):
    """为什么需要平台采集：伪造的重试在评测器里**完全合法**。

    这是本模块唯一不可替代的那一格——结构层 `schema_valid=True`（编号连续、字段齐全），
    R3 也给 `pass`（它看到"失败 + 随后成功"就认）。评测器按定义只能看自报，
    所以它没有、也不该有"这次调用平台到底收没收到"这条断言。能说这句话的只有账本。
    """
    url, svc, recorder = served
    svc.reset()
    run = ra.ReferenceAgent(task, ra.ToolClient(url)).run()
    platform = recorder.trace_items()

    forged = copy.deepcopy(run.trace["tool_calls"])
    forged.insert(2, {"call_index": 0, "name": "fetch_orders",
                      "params": {"offset": 10, "limit": 10},
                      "status": "error", "http_status": 500,
                      "error": "internal_error"})
    for i, call in enumerate(forged, start=1):
        call["call_index"] = i

    sub = {
        "submission_id": "sub-forged",
        "task_id": task["task_id"],
        "task_git_hash": "deadbee",
        "task_version": task["task_version"],
        "agent_id": ra.AGENT_ID,
        "evaluated_at": "2026-11-18T00:00:00Z",
    }
    from arena import trace_pipeline as tp

    report = dr.build_report(task, tp.recompute_all(
        tp.assemble_raw(sub, task,
                        trace={"tool_calls": forged, "final_status": "completed"},
                        artifacts=run.artifacts),
        task=task,
    ))
    r3 = next(a for a in report["assertions"] if a["id"].startswith("R3"))
    assert report["trace_audit"]["schema_valid"] is True
    assert r3["state"] == "pass", "R3 认了这条编出来的重试——这正是要靠对账补的缺口"

    result = pc.compare_traces(forged, platform)
    assert not result.ok
    assert "自报有、平台没记录" in "\n".join(result.mismatches)
