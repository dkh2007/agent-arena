"""P3 / R6 对账判定器的约束测试（round-70）。

锁的是决策 10 的三条边界：
  ① 对账不通过 ≠ 答错——记录里没数值时判 `unverifiable`，不判 mismatch；
  ② 只有 `verified` 才算证据（`counts_as_evidence`），其余两种都不进 error_patterns；
  ③ 出处只认**返回值类字段**，`params` 里的数值不算出处（否则「把参数当结果」就能自证）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import reconciliation as rc  # noqa: E402

VALUES = {"valid_count": 17, "total_amount": 1837.99}


def _trace(**calls):
    return {"tool_calls": list(calls["calls"])}


def test_verified_when_values_traceable():
    trace = _trace(calls=[
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10},
         "result_summary": {"returned": 10, "total": 20}},
        {"call_index": 2, "name": "write_report", "params": {"path": "/workspace/report.json"},
         "result_summary": "wrote 17 valid rows, amount 1837.99"},
    ])
    result = rc.reconcile(VALUES, trace)
    assert result["status"] == "verified"
    assert result["counts_as_evidence"] is True
    assert all(item["found"] for item in result["checked"])
    assert "对账通过" in rc.summarize(result)


def test_mismatch_when_value_not_traceable():
    """编造数值：记录里有数字，但填不上这个出处。"""
    trace = _trace(calls=[
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10},
         "result_summary": {"returned": 10, "total": 20}},
    ])
    result = rc.reconcile(VALUES, trace)
    assert result["status"] == "mismatch"
    assert result["counts_as_evidence"] is False
    assert [i["field"] for i in result["checked"] if not i["found"]] == ["valid_count", "total_amount"]
    assert "疑似编造" in rc.summarize(result)


def test_unverifiable_when_no_numbers_in_trace():
    """数据不足 ≠ 答错：这条决定它不能写进 error_patterns。"""
    for trace in (None, {"tool_calls": []},
                  {"tool_calls": [{"call_index": 1, "name": "write_report",
                                   "params": {"path": "/x"}, "result_summary": None}]}):
        result = rc.reconcile(VALUES, trace)
        assert result["status"] == "unverifiable", trace
        assert result["counts_as_evidence"] is False
        assert "数据不足" in rc.summarize(result)


def test_params_are_not_a_source_of_truth():
    """出处只能是返回值：把答案塞进 params 自证不算通过。"""
    trace = _trace(calls=[
        {"call_index": 1, "name": "write_report",
         "params": {"path": "/workspace/report.json", "content": '{"valid_count": 17, "total_amount": 1837.99}'},
         "result_summary": None},
    ])
    result = rc.reconcile(VALUES, trace)
    assert result["status"] == "unverifiable", "params 里的数值不构成出处"
    assert result["trace_number_count"] == 0


def test_tolerance_boundary():
    trace = _trace(calls=[{"call_index": 1, "name": "write_report",
                           "params": {}, "result_summary": {"valid_count": 17, "amount": 1837.995}}])
    assert rc.reconcile(VALUES, trace)["status"] == "verified"
    assert rc.reconcile(VALUES, trace, tolerance=0.001)["status"] == "mismatch"


def test_bool_is_not_a_number():
    trace = _trace(calls=[{"call_index": 1, "name": "write_report", "params": {},
                           "result_summary": {"valid_count": True, "total_amount": 1837.99}}])
    result = rc.reconcile(VALUES, trace)
    assert result["status"] == "mismatch", "True 不应被当成 1 用来对账"


def test_rejects_empty_or_non_numeric_input():
    with pytest.raises(ValueError, match="非空映射"):
        rc.reconcile({}, {"tool_calls": []})
    with pytest.raises(ValueError, match="必须是数值"):
        rc.reconcile({"valid_count": "17"}, {"tool_calls": []})


# ── round-77：聚合推导值不能被判成编造 ───────────────────────────────────────
#
# 实测背景：唯一任务 dbg-001 的两个答案值（有效条数、总金额）都是聚合推导值，
# 工具返回值里逐字不可能出现，于是完美提交被判 `mismatch`「疑似编造」。
# 这不是实现 bug，是**结论超出了证据能支撑的范围**——补法是让调用方声明推导字段。

def test_derived_fields_are_not_accused_of_fabrication():
    """聚合推导值缺出处 ⇒ unverifiable（数据不足），不是 mismatch（疑似编造）。"""
    trace = _trace(calls=[
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10},
         "result_summary": {"orders": 10, "total": 20}},
    ])
    assert rc.reconcile(VALUES, trace)["status"] == "mismatch", "不声明时行为与旧版一致"

    result = rc.reconcile(VALUES, trace, derived_fields={"valid_count", "total_amount"})
    assert result["status"] == "unverifiable"
    assert result["reason"] == "derived_not_replayable"
    assert result["counts_as_evidence"] is False
    assert result["derived_fields"] == ["total_amount", "valid_count"]
    said = rc.summarize(result)
    assert "聚合推导值" in said and "不是答错" in said
    assert "疑似编造" not in said, "不许对推导值下编造结论"


def test_one_non_derived_miss_is_still_mismatch():
    """混着来：只要有一个**非**推导字段缺出处，仍是 mismatch。"""
    trace = _trace(calls=[
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10},
         "result_summary": {"orders": 10, "total": 20}},
    ])
    result = rc.reconcile(VALUES, trace, derived_fields={"valid_count"})
    assert result["status"] == "mismatch"
    assert result["reason"] == "value_not_traceable"
    assert [i["field"] for i in result["checked"] if not i["found"]] == ["valid_count", "total_amount"]


def test_all_derived_found_stays_verified():
    """推导值恰好也能逐字找到出处时，仍是 verified（不许因为声明了就降级）。"""
    trace = _trace(calls=[
        {"call_index": 1, "name": "write_report", "params": {},
         "result_summary": "wrote 17 valid rows, amount 1837.99"},
    ])
    result = rc.reconcile(VALUES, trace, derived_fields={"valid_count", "total_amount"})
    assert result["status"] == "verified"
    assert result["counts_as_evidence"] is True


def test_unknown_derived_field_rejected():
    """拼错的字段名必须报错——否则声明形同虚设，静默不生效最难查。"""
    with pytest.raises(ValueError, match="未覆盖"):
        rc.reconcile(VALUES, {"tool_calls": []}, derived_fields={"valid_cnt"})
