# 诊断报告 DTO 的契约测试（S2 完成判据：用 dbg-001 的假提交产出符合 schema 的 JSON）
# 这里断言的不是"代码能跑"，而是六条不变量（见 docs/diagnosis-report-schema.md §7）。
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena.diagnosis_report import (  # noqa: E402
    ReportError,
    build_report,
    load_schema,
    load_task,
    load_vocabulary,
    validate_report,
)

TASK = load_task(ROOT / "tasks" / "dbg-001-order-report.yaml")
SCHEMA = load_schema()
VOCAB = load_vocabulary()
FIXTURES = ROOT / "fixtures"
NAMES = ("raw-fake-retry", "raw-no-trace", "raw-no-file")


def _raw(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _build(name: str) -> dict:
    return build_report(TASK, _raw(name), VOCAB)


def _by_id(report: dict) -> dict:
    return {a["id"]: a for a in report["assertions"]}


def test_three_fixtures_pass_schema():
    for name in NAMES:
        report = _build(name)
        validate_report(report, SCHEMA)


def test_assertions_match_rubric_exactly():
    """I1：不多、不少、不重复，且顺序跟 rubric 一致（report 要能和 YAML 逐行对照）。"""
    rubric_ids = [r["id"] for r in TASK["rubric"]]
    for name in NAMES:
        ids = [a["id"] for a in _build(name)["assertions"]]
        assert ids == rubric_ids, f"{name} 的断言集合或顺序与 rubric 不一致"
        for a, spec in zip(_by_id(_build(name)).values(), TASK["rubric"]):
            assert a["weight"] == spec["weight"]
            assert a["type"] == spec["type"]


def test_missing_assertion_rejected():
    raw = _raw("raw-fake-retry")
    raw["assertion_results"] = [r for r in raw["assertion_results"] if r["id"] != "R4_no_param_hallucination"]
    with pytest.raises(ReportError, match="不一致"):
        build_report(TASK, raw, VOCAB)


def test_duplicate_assertion_rejected():
    raw = _raw("raw-fake-retry")
    raw["assertion_results"].append(copy.deepcopy(raw["assertion_results"][0]))
    with pytest.raises(ReportError, match="重复"):
        build_report(TASK, raw, VOCAB)


def test_no_trace_forces_unscored_even_if_caller_claims_pass():
    """I2：这是 A2E 的反面。raw 里 R3/R4/R5 全写 pass，也必须被改写成 unscored。"""
    raw = _raw("raw-no-trace")
    assert all(r["state"] == "pass" for r in raw["assertion_results"]), "夹具本身应全是 pass"

    report = _build("raw-no-trace")
    got = _by_id(report)
    for rid in ("R3_retry_evidence", "R4_no_param_hallucination", "R5_clean_exit"):
        assert got[rid]["state"] == "unscored", f"{rid} 应在 trace 缺失时被改写为 unscored"
        assert got[rid]["reason_code"] == "trace_not_reported"
        assert got[rid]["error_patterns"] == []
    # 结果断言不受影响
    assert got["R1_file_exists"]["state"] == "pass"
    assert got["R2_report_correct"]["state"] == "pass"
    assert report["notes"], "trace 缺失必须在 notes 里写明，不能静默"


def test_fail_without_error_patterns_rejected():
    """I3：fail 但说不出错在哪，等于没有诊断价值。"""
    raw = _raw("raw-fake-retry")
    for r in raw["assertion_results"]:
        if r["id"] == "R3_retry_evidence":
            r["error_patterns"] = []
    with pytest.raises(ReportError, match="没有给出错误模式"):
        build_report(TASK, raw, VOCAB)


def test_undeclared_pattern_rejected():
    """I3：R3 上填一个词表里有、但 R3 未声明的模式，必须拒。"""
    raw = _raw("raw-fake-retry")
    for r in raw["assertion_results"]:
        if r["id"] == "R3_retry_evidence":
            r["error_patterns"] = ["wrong_total_amount"]  # 词表有，但属 R2
    with pytest.raises(ReportError, match="未在本断言"):
        build_report(TASK, raw, VOCAB)


def test_blocked_without_failed_dependency_rejected():
    """I4：前置全通过时不许标 blocked，否则它会沦为"看不清就不判"的逃生口。"""
    raw = _raw("raw-no-file")
    for r in raw["assertion_results"]:
        if r["id"] == "R1_file_exists":
            r["state"] = "pass"
            r["error_patterns"] = []
    with pytest.raises(ReportError, match="blocked"):
        build_report(TASK, raw, VOCAB)


def test_score_uses_fixed_denominator():
    """I5 + I6：分母恒为 6，分子只算 pass；ratio 必须与加权和一致。"""
    for name in NAMES:
        report = _build(name)
        score = report["score"]
        weights = {r["id"]: r["weight"] for r in TASK["rubric"]}
        expected = sum(weights[a["id"]] for a in report["assertions"] if a["state"] == "pass")
        assert score["total_weight"] == sum(weights.values()) == 6
        assert score["weighted_sum"] == expected
        assert score["ratio"] == pytest.approx(expected / 6)
        assert score["denominator_policy"] == "fixed_at_total_weight"
        assert sum(score["by_state"].values()) == len(TASK["rubric"])


def test_skill_gaps_only_from_fail_and_aggregated():
    """只聚合并按 weight_lost 降序；skill_id 为 null 的断言不制造缺口。"""
    gaps = _build("raw-fake-retry")["skill_gaps"]
    assert [g["skill_id"] for g in gaps] == ["multi_step_planning", "failure_retry"]
    assert gaps[0]["weight_lost"] == 2 and gaps[0]["failed_assertions"] == ["R2_report_correct"]
    assert gaps[1]["weight_lost"] == 1 and gaps[1]["patterns"] == ["retry_wrong_params"]

    # 没交文件时 R1（skill_id=null）失败，但技能缺口为空——P5 指出的诊断盲区
    assert _build("raw-no-file")["skill_gaps"] == []


def test_attribution_policy_matches_yaml():
    """契约自解释：报告里的归因表必须与任务 YAML 一致，状态层不必回读 YAML。"""
    yaml_attr = TASK["scoring"]["attribution"]
    for name in NAMES:
        policy = _build(name)["attribution_policy"]
        for state in ("pass", "fail", "blocked", "unscored"):
            assert policy[state]["counts_toward_mastery"] == yaml_attr[state]["counts_toward_mastery"]
            assert policy[state]["emit_error_patterns"] == yaml_attr[state]["emit_error_patterns"]


def test_schema_rejects_tampered_report():
    """schema 的有效性检查本身要能抓错——否则"通过 schema"这句话没意义。"""
    report = _build("raw-fake-retry")

    bad_state = copy.deepcopy(report)
    bad_state["assertions"][0]["state"] = "passed"  # 非法枚举
    with pytest.raises(ReportError, match="schema"):
        validate_report(bad_state, SCHEMA)

    extra_key = copy.deepcopy(report)
    extra_key["mastery_delta"] = {"failure_retry": -0.3}  # v1 故意不装的跨层推断
    with pytest.raises(ReportError, match="schema"):
        validate_report(extra_key, SCHEMA)

    bad_policy = copy.deepcopy(report)
    bad_policy["score"]["denominator_policy"] = "exclude_missing"
    with pytest.raises(ReportError, match="schema"):
        validate_report(bad_policy, SCHEMA)
