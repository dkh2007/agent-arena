"""S3-3-2：R5 trace_final_status 执行器 + evaluate_assertion() 分发的约束测试。

守的是三件结构性事实：
1. 值域 / 期望 / 分类规则都从任务 YAML 派生，代码里没有第二份任务常量；
2. trace 未上报或终态越界判 unscored（不判 0、不归因），与 build_report 的强制改写同口径；
3. 分发入口按 type 走，未实现的 type 抛错而不是伪造一个 pass。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import assertion_evaluator as ae  # noqa: E402
from arena import diagnosis_report as dr  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"


def spec() -> dict:
    return copy.deepcopy(ae.load_r5_spec())


def task() -> dict:
    return yaml.safe_load(TASK_PATH.read_text(encoding="utf-8"))


def minimum(t: dict | None = None) -> dict:
    return (t or task())["trace_requirements"]["minimum"]


def tr(value: str | None, key: str = "final_status") -> dict:
    return {"tool_calls": [], **({key: value} if value is not None else {})}


# ---------------------------------------------------------------------------
# 判定与分类
# ---------------------------------------------------------------------------


def test_r5_contract_comes_from_yaml():
    s = spec()
    req = minimum()
    assert s["type"] == "trace_final_status"
    assert s["target"] == "final_status"
    assert s["expected"] in req["final_status"]["enum"]
    assert s["error_patterns"] == ["task_crashed", "task_timeout"]
    assert {c["match"]["final_status"] for c in s["error_pattern_detection"]["cases"]} == \
        set(req["final_status"]["enum"]) - {s["expected"]}


def test_completed_is_pass():
    r = ae.evaluate_assertion(spec(), tr("completed"))
    assert r["state"] == "pass"
    assert r["evidence"] == {"observed": "completed", "expected": "completed"}
    assert not r.get("error_patterns")
    assert "reason_code" not in r


@pytest.mark.parametrize("value,pattern", [("crashed", "task_crashed"),
                                           ("timeout", "task_timeout")])
def test_non_completed_is_fail_with_yaml_pattern(value, pattern):
    r = ae.evaluate_assertion(spec(), tr(value))
    assert r["state"] == "fail"
    assert r["error_patterns"] == [pattern]
    # 与 R3 一样，命中来源必须可精确审计，不能只写「case」
    assert r["evidence"]["extra"]["classification_source"] == "case:final_status"


@pytest.mark.parametrize("bad", [None, "oops", {}, {"final_status": "running"},
                                 {"final_status": None}, {"final_status": 42}])
def test_unusable_trace_is_unscored_not_zero(bad):
    """数据缺失 ≠ 能力不足：unscored，不判 0、不产出错误模式。"""
    r = ae.evaluate_assertion(spec(), bad)
    assert r["state"] == "unscored"
    assert r["reason_code"] in ("trace_not_reported", "trace_invalid")
    assert not r.get("error_patterns")


def test_final_status_not_reported_uses_distinct_reason_code():
    assert ae.evaluate_assertion(spec(), None)["reason_code"] == "trace_not_reported"
    assert ae.evaluate_assertion(spec(), tr("running"))["reason_code"] == "trace_invalid"


# ---------------------------------------------------------------------------
# YAML 是唯一真相源：改 YAML，判定必须跟着变
# ---------------------------------------------------------------------------


def test_value_domain_follows_yaml_enum():
    """把 enum 收窄成不含 timeout，timeout 就不再是「有证据的失败」而是越界数据。"""
    req = minimum()
    req["final_status"]["enum"] = ["completed", "crashed"]
    r = ae.evaluate_assertion(spec(), tr("timeout"), requirements=req)
    assert r["state"] == "unscored" and r["reason_code"] == "trace_invalid"


def test_expected_must_be_in_enum():
    req = minimum()
    req["final_status"]["enum"] = ["crashed", "timeout"]
    with pytest.raises(ae.EvaluatorError, match="不在 final_status 枚举"):
        ae.evaluate_assertion(spec(), tr("crashed"), requirements=req)


def test_expanded_enum_without_case_refuses_to_invent_pattern():
    """枚举新增取值但 cases 没跟上：抛错，不静默编一个错误模式，也不退回 default。"""
    req = minimum()
    req["final_status"]["enum"].append("running")
    with pytest.raises(ae.EvaluatorError, match="未命中任何 case"):
        ae.evaluate_assertion(spec(), tr("running"), requirements=req)


def test_classification_rules_come_from_yaml():
    s = spec()
    s["error_pattern_detection"]["cases"][0]["pattern"] = "task_timeout"
    r = ae.evaluate_assertion(s, tr("crashed"))
    assert r["error_patterns"] == ["task_timeout"]


def test_pattern_must_be_registered_in_vocabulary():
    with pytest.raises(ae.EvaluatorError, match="未登记进错误模式词表"):
        ae.evaluate_assertion(spec(), tr("crashed"), vocab=set())


# ---------------------------------------------------------------------------
# TD-003 同款纪律：配置错在接触任何 trace 之前失败
# ---------------------------------------------------------------------------


BAD_SPECS = {
    "缺 target": lambda s: s.pop("target"),
    "缺 expected": lambda s: s.pop("expected"),
    "缺 cases": lambda s: s["error_pattern_detection"].pop("cases"),
    "cases 为空": lambda s: s["error_pattern_detection"].__setitem__("cases", []),
    "空 match": lambda s: s["error_pattern_detection"]["cases"].insert(
        0, {"match": {}, "pattern": "task_crashed"}),
    "拼错 key": lambda s: s["error_pattern_detection"]["cases"].insert(
        0, {"match": {"final_stauts": "crashed"}, "pattern": "task_crashed"}),
    "多余 key": lambda s: s["error_pattern_detection"]["cases"].insert(
        0, {"match": {"final_status": "crashed", "extra": 1}, "pattern": "task_crashed"}),
    "pattern 未声明": lambda s: s["error_pattern_detection"]["cases"].insert(
        0, {"match": {"final_status": "crashed"}, "pattern": "no_retry"}),
}


@pytest.mark.parametrize("bad_name", list(BAD_SPECS))
def test_bad_spec_fails_before_any_trace(bad_name):
    s = spec()
    BAD_SPECS[bad_name](s)
    # trace=None 是判据：若校验放在 trace 分支之后，会直接返回 unscored 而不抛错
    with pytest.raises(ae.EvaluatorError):
        ae.evaluate_assertion(s, None)
    with pytest.raises(ae.EvaluatorError):
        ae.evaluate_assertion(s, tr("crashed"))


def test_load_r5_spec_validates_at_load_time(tmp_path):
    t = task()
    r5 = next(r for r in t["rubric_gate"] if r["id"] == "R5_clean_exit")
    r5["error_pattern_detection"]["cases"].insert(0, {"pattern": "task_crashed"})
    p = tmp_path / "task.yaml"
    p.write_text(yaml.safe_dump(t, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ae.EvaluatorError, match="必须且只能含"):
        ae.load_r5_spec(p)


# ---------------------------------------------------------------------------
# 分发入口
# ---------------------------------------------------------------------------


def test_dispatch_routes_r3_and_r5_by_type():
    r3 = ae.load_r3_spec()
    r5 = spec()
    assert ae.evaluate_assertion(r3, None)["id"] == "R3_retry_evidence"
    assert ae.evaluate_assertion(r5, None)["id"] == "R5_clean_exit"
    # S3-3-3 注册 file_exists、S3-3-4 注册 json_field_equals、S3-3-5 注册
    # trace_param_schema_valid；这条锁跟着现况走，别把它当历史快照。
    assert ae.SUPPORTED_TYPES == ("trace_sequence", "trace_final_status", "file_exists",
                                  "json_field_equals", "trace_param_schema_valid")


@pytest.mark.parametrize("unsupported", ["future_assertion_type"])
def test_unimplemented_types_raise_instead_of_faking_result(unsupported):
    # 五条断言的执行器都已实现；改用一个虚构 type 锁「未知类型拒绝伪造结果」，
    # 不绑具体步骤号，避免每次注册新类型都要重写这条断言。
    with pytest.raises(NotImplementedError, match="不要伪造结果"):
        ae.evaluate_assertion({"id": "X", "type": unsupported}, tr("completed"))


def test_evaluate_still_refuses_r5_spec():
    """evaluate() 是 R3 专用；直接塞 R5 spec 必须报错，不能走错分类器。"""
    with pytest.raises(NotImplementedError, match="S3-1"):
        ae.evaluate(spec(), tr("completed"))


# ---------------------------------------------------------------------------
# 端到端：执行器结果能被 build_report 无改组装
# ---------------------------------------------------------------------------


def _inject(raw: dict, generated: dict) -> dict:
    raw["assertion_results"] = [
        generated if item["id"] == generated["id"] else item
        for item in raw["assertion_results"]
    ]
    return raw


def test_r5_result_survives_dto_and_gate_semantics():
    task_dict = task()
    base = json.loads((ROOT / "fixtures" / "raw-perfect.json").read_text(encoding="utf-8"))

    ok = dr.build_report(task_dict, _inject(copy.deepcopy(base),
                                            ae.evaluate_assertion(spec(), tr("completed"))))
    dr.validate_report(ok)
    r5 = next(a for a in ok["assertions"] if a["id"] == "R5_clean_exit")
    assert r5["state"] == "pass" and ok["score"]["ratio"] == 1.0

    crashed = dr.build_report(task_dict, _inject(copy.deepcopy(base),
                                                 ae.evaluate_assertion(spec(), tr("crashed"))))
    dr.validate_report(crashed)
    r5b = next(a for a in crashed["assertions"] if a["id"] == "R5_clean_exit")
    assert r5b["state"] == "fail" and r5b["error_patterns"] == ["task_crashed"]
    assert crashed["score"]["disqualified"] is True
    assert crashed["score"]["ratio"] == 0.0
