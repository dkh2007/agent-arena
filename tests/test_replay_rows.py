"""B-20b 第一步：账本 → 重放 → 行集 → 数，这条链真的通吗。

`derivation.py` 的测试只回答「规则算得对不对」，`test_replay_premise.py` 只回答
「返回体能不能重建」。两段之间的**接口**（行集怎么拼）此前没有裁判，
它的失效方式全是安静的：

| 失效 | 后果 |
|---|---|
| 把失败页也当行集 | 失败的那一页被凭空补上，Agent 没重试也照样能"推出"答案 |
| 按数组顺序重放而不看 `call_index` | 删掉中间一条 ⇒ 后端序列整体错位，故障额度打到别的调用上 |
| 拼行时去重靠上游 | 同一页拉两次 ⇒ 条数与合计都翻倍，而"重复算一条"只写在规则里 |
| 行集取自 trace 自报的摘要 | 「答案是否来自过程」退化成看运气（`agent_trace.summary` 的原文结论） |

六条判据分别对着上面四行 + 两个"该拒跑"的形状。
"""

from __future__ import annotations

import copy
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import mock_tools as mt  # noqa: E402
from arena import reference_agent as ra  # noqa: E402
from arena import replay as rp  # noqa: E402

REPORT_TASKS = sorted(p for p in ROOT.glob("tasks/*.yaml") if "derivation" in dr.load_task(p))
MAIN = ROOT / "tasks" / "dbg-001-order-report.yaml"


def _record_calls(task: dict) -> list[dict]:
    """真跑一遍参考 Agent，记下**平台实际收到的**有序调用（`call_index` 由平台编号）。

    不用 trace：trace 的 `result_summary` 是 Agent 自报的压缩摘要，而重放的输入必须是
    平台账本里的 `(call_index, name, params)`——两者恰好是本轮要区分的两种东西。
    """
    seen: list[dict] = []

    def on_call(name, params, status, body=None):
        seen.append({"call_index": len(seen) + 1, "name": name, "params": dict(params)})

    srv, svc = mt.make_server(task, port=0, on_call=on_call)
    host, port = srv.server_address[:2]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        svc.reset()
        ra.ReferenceAgent(task, ra.ToolClient(f"http://{host}:{port}")).run()
    finally:
        srv.shutdown()
        srv.server_close()
    return seen


def _main_calls() -> list[dict]:
    return _record_calls(dr.load_task(MAIN))


@pytest.mark.parametrize("path", REPORT_TASKS, ids=lambda p: p.stem)
def test_every_report_task_replays_to_its_answer_key(path):
    """10 个汇报类任务：参考 Agent 真跑 → 重放 → 推导值必须等于答案键。

    只跑母本会漏掉的两类差异：页大小换了（prm-*）之后页边界落在别处；
    故障位换到第 2/4 页（ret-*）之后重放里的失败页位置不同。
    """
    task = dr.load_task(path)
    rows, values = rp.derive_from_calls(task, _record_calls(task))

    assert values == task["test_inputs"]["expected"], (
        f"{path.name}: 重放推导值 {values} 与答案键 {task['test_inputs']['expected']} 不符\n"
        f"  用上的页：{rows.used}；跳过的：{rows.skipped}；行数 {len(rows.rows)}"
    )
    assert rows.used, "参考 Agent 一次成功页都没拉到，重放无从谈起"


def test_a_failed_page_is_recorded_but_not_refilled():
    """失败页只进 `skipped`：不重试、不补行。

    补一次等于平台替 Agent 重试，而"失败之后有没有重试"正是 R3 要判的东西
    （与 YAML `execution.platform_retry: 0` 同一条理由）。
    """
    task = dr.load_task(MAIN)
    calls = [{"call_index": 1, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}}]
    rows, _ = rp.derive_from_calls(task, calls)

    assert rows.rows == [] and rows.used == []
    assert rows.skipped == [{"call_index": 1, "reason": "http_500"}], (
        f"失败页的记录不对：{rows.skipped}——应只有一条 http_500，且不该有任何行进来"
    )


def test_missing_page_shrinks_the_derived_answer():
    """少一页 ⇒ 值必须变。否则「拉全了没有」这件事在重放侧根本看不出来。"""
    task = dr.load_task(MAIN)
    full = _main_calls()
    first_page_only = [c for c in full if c["params"].get("offset") == 0]

    rows, values = rp.derive_from_calls(task, first_page_only)
    expected = task["test_inputs"]["expected"]

    assert values["valid_count"] < expected["valid_count"], (
        f"只拉第一页却仍算出 {values['valid_count']} 条（答案 {expected['valid_count']}）——"
        "说明行集不是从这次调用里来的"
    )
    assert rows.page_count == len(first_page_only)


def test_duplicate_page_is_not_double_counted():
    """同一页拉两次：行会重复进来，但 `distinct_by` 让数不变。

    这条同时说明为什么去重必须写在规则里：重放把分页拼起来时，重复是**必然**会发生的
    （Agent 重拉、页序乱、游标回退），而"重复行算一条还是两条"没有默认答案。
    """
    task = dr.load_task(MAIN)
    full = _main_calls()
    dup = copy.deepcopy(full)
    dup.append({"call_index": len(dup) + 1, "name": "fetch_orders",
                "params": {"offset": 0, "limit": 10}})

    rows, values = rp.derive_from_calls(task, dup)
    _, once = rp.derive_from_calls(task, full)

    assert len(rows.rows) > len(rp.rows_from_calls(task, full).rows), "重复页没有真的进来"
    assert values == once == task["test_inputs"]["expected"]


def test_hole_in_call_index_is_rejected():
    """删掉中间一条 ⇒ 拒跑。按数组顺序重放可能碰巧对，也可能把故障额度打偏。"""
    task = dr.load_task(MAIN)
    full = _main_calls()
    holed = [c for i, c in enumerate(full) if i != 1]      # 去掉第 2 条（那条是失败页）

    with pytest.raises(rp.ReplayError, match="call_index"):
        rp.derive_from_calls(task, holed)


def test_duplicate_call_index_is_rejected():
    """编号重复同样是「顺序没有答案」，不该被静默排序掉。"""
    task = dr.load_task(MAIN)
    full = _main_calls()
    twice = full + [dict(c) for c in full[:1]]

    with pytest.raises(rp.ReplayError, match="call_index"):
        rp.derive_from_calls(task, twice)


def test_task_without_paginated_tool_is_rejected():
    """修复向任务（`fix-*`）的答案不是推导值，不该被这套东西"顺带"重放。"""
    task = dr.load_task(ROOT / "tasks" / "fix-001-token-expiry.yaml")

    with pytest.raises(rp.ReplayError, match="pagination"):
        rp.row_sources(task)


def test_two_paginated_tools_is_rejected():
    """两个分页工具 ⇒「行集该来自哪一个」没有声明，猜一个就是替作者决定。"""
    task = dr.load_task(MAIN)
    other = copy.deepcopy(task["tools"][0])
    other["name"] = "fetch_orders_v2"
    task["tools"].append(other)

    with pytest.raises(rp.ReplayError, match="多个分页工具"):
        rp.row_sources(task)


def test_agent_claim_does_not_affect_the_rows():
    """篡改 trace 里自报的摘要，推导值不变：行来自任务数据 + 参数，不来自自报。

    这是这条链存在的**理由**。若哪天真有人让它读 `result_summary` 里的数，
    这个测试会红——那时的后果是「诚实的和伪造的都能凑出一个相等的数」。
    """
    task = dr.load_task(MAIN)
    calls = _main_calls()
    trace = {
        "tool_calls": [
            {"call_index": c["call_index"], "name": c["name"], "params": c["params"],
             "status": "success", "result_summary": {"orders": 999, "total": 999999}}
            for c in calls
        ]
    }

    rows, values = rp.derive_from_trace(task, trace)
    assert values == task["test_inputs"]["expected"]
    assert len(rows.rows) == len(task["test_inputs"]["orders_fixture"])


def test_successful_page_without_the_declared_key_is_rejected():
    """成功响应里没有声明的行键 ⇒ 报错，不当空页。

    当空页的后果很具体：推导值悄悄变小，看起来像"这个 Agent 少拉了一页"——
    归因直接指错方向（那是工具契约与实现对不上，不是能力问题）。
    """
    task = dr.load_task(MAIN)
    task["tools"][0]["mock"]["pagination"]["response_shape"] = {"items": []}
    calls = [{"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}}]

    with pytest.raises(rp.ReplayError, match="items"):
        rp.rows_from_calls(task, calls)


def test_rows_carry_their_provenance():
    """行集带上「用了哪几页、跳过什么」——只给数值等于把诊断要看的证据藏起来。"""
    task = dr.load_task(MAIN)
    rows, _ = rp.derive_from_calls(task, _main_calls())

    info = rows.as_dict()
    assert info["tool"] == "fetch_orders" and info["key"] == "orders"
    assert info["row_count"] == len(rows.rows) and info["pages"] == rows.used
    assert any(s["reason"] == "http_500" for s in info["skipped"]), (
        "母本的第一条 offset=10 调用必然 500（fault_injection），skipped 里应该有它"
    )
