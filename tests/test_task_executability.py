"""上界锚点的推广：**每个任务**都必须能跑满分。

## 为什么需要这一层

`test_diagnosis_report.py::test_score_order_is_monotone_and_anchored` 已经有
`assert ratios["raw-perfect"] == 1.0, "满分必须可达，否则上界没有锚点"`——但它只覆盖
`dbg-001` 一份夹具。另外 11 个任务（B-05 的变体 + 定位题）**没有任何夹具**：
它们的 rubric 期望值（故障位、页大小上限、有效条数）是不是自洽，一条测试都没验过。

这不是理论风险。实测一个人为改动：把 `ret-003` 的 `failures[0].attempt` 从 1 改成 2，
任务声明照旧自洽（`test_fault_declaration_matches_retry_expectation` 仍绿），
但参考 Agent 真实跑一遍之后**故障根本不会触发** ⇒ R3 拿不到"失败后重试"的证据 ⇒ 满分不可达。
只有真跑一次才看得见。

## 这条不变式同时是 B-20 的前置

P3 对账要接进 rubric 成为 credit 断言，分母就会从 4 变 5。若那条断言**谁都过不了**
（实测：五份夹具全都 `unverifiable`），每个任务的满分就会从 1.0 掉到 0.8——一条没人能过的
检查不是检查，是 20% 的普遍税。本文件就是拦这类事情的闸门：**加断言之前，先证明满分还可达**。

## 判据

对每个任务：参考 Agent 真跑（真 socket）→ `final_status=completed` → trace 结构合法 →
**五条断言全 pass、ratio == 1.0、未作废**。跑不通或掉一条就红，且红在具体任务名下。
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

TASK_DIR = ROOT / "tasks"
TASK_PATHS = sorted(TASK_DIR.glob("*.yaml"))
GIT_HASH = "deadbee"


def test_the_task_list_is_not_empty():
    """参数化用例为 0 时 pytest 会"全绿"——这是最安静的假绿，先堵住。"""
    assert len(TASK_PATHS) >= 10, f"只找到 {len(TASK_PATHS)} 个任务，检查 TASK_DIR"
    names = {p.stem for p in TASK_PATHS}
    assert "dbg-001-order-report" in names, "母本不在列表里"


@pytest.mark.parametrize("path", TASK_PATHS, ids=lambda p: p.stem)
def test_reference_agent_scores_full_marks(path: Path):
    """真跑一遍必须拿满分 —— 满分不可达的 rubric 是坏 rubric。"""
    task = dr.load_task(str(path))
    srv, svc = mt.make_server(task, port=0)
    host, port = srv.server_address[:2]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        svc.reset()  # reset_policy: per_submission
        run = ra.ReferenceAgent(task, ra.ToolClient(f"http://{host}:{port}")).run()
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)

    sub = {
        "submission_id": f"sub-{path.stem}",
        "task_id": task["task_id"],
        "task_git_hash": GIT_HASH,
        "task_version": task.get("task_version"),
        "agent_id": ra.AGENT_ID,
        "evaluated_at": "2026-11-18T00:00:00Z",
    }
    raw = tp.assemble_raw(sub, task, trace=run.trace, artifacts=run.artifacts)
    report = dr.build_report(task, tp.recompute_all(raw, task=task))

    assert run.trace["final_status"] == "completed", f"执行没正常结束：{run.notes}"
    assert report["trace_audit"]["schema_valid"] is True, report["trace_audit"]

    states = {a["id"]: a["state"] for a in report["assertions"]}
    failed = {k: v for k, v in states.items() if v != "pass"}
    assert not failed, f"{path.stem} 参考 Agent 未全过：{failed}（说明这条断言的期望值与真实执行不一致）"
    assert report["score"]["ratio"] == 1.0, f"{path.stem} 满分不可达：{report['score']}"
    assert report["score"]["disqualified"] is False
