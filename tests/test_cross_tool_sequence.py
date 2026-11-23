"""`trace_sequence` 的跨工具序列（第三套骨架 tpl-ask-first 的前置能力）。

## 为什么需要它

原实现只支持「同一工具、同一入参重试」：`_filter_expr` 明确拒绝
「各步骤 (name, params_match) 不一致」。这在 R3（失败重试）上是对的，
但表达不出**先做 A 再做 B** 这类过程约束——而"信息不全时先问人再动手"
恰恰只能这么表达。

## 边界（有意收紧，不是漏做）

- 只放宽 **name 可以不同**：`params_match` 仍可留空（该步只锁 name + status）；
- 仍要求 `ordered=true` + `allow_interleaved=true`，无序语义依然未定义；
- **同一个任务里同 name 的多步仍走旧路径**（jmespath 过滤 + 页覆盖），
  以免新匹配器改到 R3 的既有行为。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import assertion_evaluator as ae  # noqa: E402

REQUIREMENTS = {"tool_calls": {"status_enum": ["success", "error", "timeout"]}}


def _spec(steps: list[dict], **detection) -> dict:
    spec = {
        "id": "R6_ask_before_act",
        "type": "trace_sequence",
        "target": "tool_calls",
        "expected": {"ordered": True, "allow_interleaved": True, "calls": steps},
        "depends_on": [],
        "skill_id": "multi_step_planning",
        "error_patterns": ["no_clarification", "clarified_too_late"],
        "error_pattern_detection": detection or {
            "probe": "calls[*].{name: name}",
            "cases": [{"match": {"no_tool_calls": True}, "pattern": "no_clarification"}],
            "default": "clarified_too_late",
        },
        "weight": 1,
    }
    return spec


def _trace(*calls: tuple[str, str]) -> dict:
    return {
        "final_status": "completed",
        "tool_calls": [
            {"call_index": i + 1, "name": n, "params": {}, "status": s}
            for i, (n, s) in enumerate(calls)
        ],
    }


ASK_STEP = {"name": "ask_policy", "status": "success"}
FETCH_STEP = {"name": "fetch_orders", "status": "success"}


def test_ask_before_fetch_passes():
    """先问人再拉数据 ⇒ 通过。"""
    r = ae.evaluate_assertion(_spec([ASK_STEP, FETCH_STEP]),
                              _trace(("ask_policy", "success"), ("fetch_orders", "success")),
                              requirements=REQUIREMENTS)
    assert r["state"] == "pass", r


def test_fetch_before_ask_fails_and_is_named():
    """先拉数据再问人 ⇒ 失败，且错误模式是「问得太晚」而不是「没问」。"""
    r = ae.evaluate_assertion(_spec([ASK_STEP, FETCH_STEP]),
                              _trace(("fetch_orders", "success"), ("ask_policy", "success")),
                              requirements=REQUIREMENTS)
    assert r["state"] == "fail"
    assert r["error_patterns"] == ["clarified_too_late"]


def test_never_asking_is_no_clarification():
    """一次都没问 ⇒ 失败，模式是「没澄清」。这两条模式必须分得开：
    给学习者的建议完全不同（一个教"问在前"，一个教"先别猜"）。
    """
    r = ae.evaluate_assertion(_spec([ASK_STEP, FETCH_STEP]),
                              _trace(("fetch_orders", "success")),
                              requirements=REQUIREMENTS)
    assert r["state"] == "fail"
    assert r["error_patterns"] == ["no_clarification"]


def test_interleaved_calls_are_allowed():
    """中间穿插别的调用不影响：本题只约束"问"在"拉"之前，不约束相邻。"""
    r = ae.evaluate_assertion(
        _spec([ASK_STEP, FETCH_STEP]),
        _trace(("ask_policy", "success"), ("write_report", "error"),
               ("fetch_orders", "success")),
        requirements=REQUIREMENTS,
    )
    assert r["state"] == "pass", r


def test_params_match_is_enforced_per_step():
    """跨工具序列的每一步仍可锁 params，只是不要求各步一致。"""
    spec = _spec([
        {"name": "ask_policy", "params_match": {"topic": "policy"}, "status": "success"},
        {"name": "fetch_orders", "params_match": {"offset": 0}, "status": "success"},
    ])
    trace = {
        "final_status": "completed",
        "tool_calls": [
            {"call_index": 1, "name": "ask_policy", "params": {"topic": "weather"},
             "status": "success"},
            {"call_index": 2, "name": "fetch_orders", "params": {"offset": 0},
             "status": "success"},
        ],
    }
    r = ae.evaluate_assertion(spec, trace, requirements=REQUIREMENTS)
    assert r["state"] == "fail"


def test_missing_status_in_step_is_a_config_error():
    """每步必须写 status，否则"成功才计数"就无从判。配置错在接触 trace 之前爆。"""
    with pytest.raises(ae.EvaluatorError, match="status"):
        ae.evaluate_assertion(_spec([{"name": "ask_policy"}, FETCH_STEP]),
                              _trace(("ask_policy", "success"), ("fetch_orders", "success")),
                              requirements=REQUIREMENTS)


def test_same_tool_spec_still_uses_the_old_path():
    """回归：同 name 的序列仍走 jmespath 过滤 + 页覆盖，行为不变。

    这条守的是"新匹配器不许改到 R3"。R3 的 24 道题全押在这条路径上。
    """
    spec = _spec([
        {"name": "fetch_orders", "params_match": {"offset": 10}, "status": "error"},
        {"name": "fetch_orders", "params_match": {"offset": 10}, "status": "success"},
    ])
    trace = {
        "final_status": "completed",
        "tool_calls": [
            {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10},
             "status": "success"},
            {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10},
             "status": "error", "http_status": 500},
            {"call_index": 3, "name": "fetch_orders", "params": {"offset": 10, "limit": 10},
             "status": "success"},
        ],
    }
    r = ae.evaluate_assertion(spec, trace, requirements=REQUIREMENTS)
    assert r["state"] == "pass", r
    assert r["evidence"]["extra"]["filter_expr"], "同 name 路径必须仍然产出过滤表达式"
