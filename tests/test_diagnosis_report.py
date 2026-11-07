# 诊断报告 DTO v2 的契约测试（两段式计分：gate 门禁 + credit 积分）
# 这里断言的不是"代码能跑"，而是不变量（见 docs/diagnosis-report-schema.md §7）。
#
# 五份夹具按质量递进排列，分数必须单调：
#   raw-empty(什么都不做) < raw-no-file(做了没交) < raw-fake-retry(交了但错) < raw-no-trace < raw-perfect
# 这条单调性是 P6 的验收判据——旧版四份夹具全落在 0.33~0.5，序是塌的。
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
    rubric_layers,
    validate_report,
)

TASK = load_task(ROOT / "tasks" / "dbg-001-order-report.yaml")
SCHEMA = load_schema()
VOCAB = load_vocabulary()
FIXTURES = ROOT / "fixtures"

# 按质量从低到高；分数必须单调不减
NAMES = ("raw-empty", "raw-no-file", "raw-fake-retry", "raw-no-trace", "raw-perfect")

GATE_IDS = [r["id"] for r in TASK["rubric_gate"]]
CREDIT_IDS = [r["id"] for r in TASK["rubric_credit"]]
ALL_IDS = GATE_IDS + CREDIT_IDS  # 报告里的顺序：先 gate 后 credit（与执行顺序一致）
CREDIT_TOTAL = sum(r["weight"] for r in TASK["rubric_credit"])


def _raw(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _build(name: str) -> dict:
    return build_report(TASK, _raw(name), VOCAB)


def _by_id(report: dict) -> dict:
    return {a["id"]: a for a in report["assertions"]}


# ---------------------------------------------------------------------------
# 结构：断言集合与 rubric 的对应关系
# ---------------------------------------------------------------------------


def test_all_fixtures_pass_schema():
    for name in NAMES:
        validate_report(_build(name), SCHEMA)


def test_assertions_cover_both_layers_in_order():
    """I1：不多、不少、不重复；顺序为先 gate 后 credit，且 weight 与 YAML 一致。

    顺序不是为了好看：执行器先跑 gate，门禁不过就不必跑 credit 的昂贵判定。报告顺序与执行顺序
    一致，读报告的人不会以为 R5 排在 R2 前面是笔误。
    """
    for name in NAMES:
        report = _build(name)
        assert [a["id"] for a in report["assertions"]] == ALL_IDS, f"{name} 的断言集合或顺序不对"

        got = _by_id(report)
        for spec in TASK["rubric_gate"]:
            assert got[spec["id"]]["layer"] == "gate"
            assert got[spec["id"]]["weight"] == 0, "门禁不计分，weight 必须是 0"
        for spec in TASK["rubric_credit"]:
            assert got[spec["id"]]["layer"] == "credit"
            assert got[spec["id"]]["weight"] == spec["weight"]
        assert sum(got[c]["weight"] for c in CREDIT_IDS) == CREDIT_TOTAL == 4


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


# ---------------------------------------------------------------------------
# 分层本身的校验：错的分层要在入口被挡住，不能等算出分数才发现
# ---------------------------------------------------------------------------


def test_layering_rejects_gate_with_weight():
    """门禁带权重 = 偷偷计分，等于把两段式退回一段式。"""
    task = copy.deepcopy(TASK)
    task["rubric_gate"][0]["weight"] = 1
    with pytest.raises(ReportError, match="门禁"):
        rubric_layers(task)


def test_layering_rejects_assertion_in_both_layers():
    task = copy.deepcopy(TASK)
    task["rubric_credit"].append(copy.deepcopy(task["rubric_gate"][0]))
    with pytest.raises(ReportError, match="同时出现"):
        rubric_layers(task)


def test_legacy_single_rubric_rejected():
    """v1 的单数组结构必须显式报错，而不是静默当空数组算出 0 分。"""
    task = copy.deepcopy(TASK)
    task["rubric"] = task.pop("rubric_gate") + task.pop("rubric_credit")
    with pytest.raises(ReportError, match="rubric_gate"):
        rubric_layers(task)


# ---------------------------------------------------------------------------
# 强制改写与依赖折叠
# ---------------------------------------------------------------------------


def test_no_trace_forces_unscored_even_if_caller_claims_pass():
    """I2：这是 A2E 的反面。raw 里 R3/R4/R5 全写 pass，也必须被改写成 unscored。"""
    raw = _raw("raw-no-trace")
    assert all(r["state"] == "pass" for r in raw["assertion_results"]), "夹具本身应全是 pass"

    report = _build("raw-no-trace")
    got = _by_id(report)
    for rid in CREDIT_IDS[1:] + ["R5_clean_exit"]:  # R3/R4 + 门禁 R5
        assert got[rid]["state"] == "unscored", f"{rid} 应在 trace 缺失时被改写为 unscored"
        assert got[rid]["reason_code"] == "trace_not_reported"
        assert got[rid]["error_patterns"] == []
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


# ---------------------------------------------------------------------------
# 两段式计分：这是本次改造的核心
# ---------------------------------------------------------------------------


def test_credit_score_uses_fixed_denominator():
    """I5 + I6：分母恒为 credit 权重和 4，分子只算 credit 层 pass。"""
    for name in NAMES:
        report = _build(name)
        score = report["score"]
        expected = sum(a["weight"] for a in report["assertions"] if a["layer"] == "credit" and a["state"] == "pass")
        assert score["total_weight"] == CREDIT_TOTAL == 4
        assert score["credit_pass_weight"] == expected
        assert score["denominator_policy"] == "fixed_at_credit_total_weight"
        assert sum(score["by_state"].values()) == len(ALL_IDS)


def test_credit_pass_weight_consistent_with_assertions():
    """credit_pass_weight 与断言逐条对得上——门禁未过时它仍照算，这个值才是诊断用的那个。"""
    for name in NAMES:
        report = _build(name)
        manual = sum(a["weight"] for a in report["assertions"] if a["layer"] == "credit" and a["state"] == "pass")
        assert report["score"]["credit_pass_weight"] == manual


def test_gate_failure_disqualifies():
    """I7：任一门禁 fail ⇒ disqualified，ratio 记 0（不是 0.3、不是 0.5）。"""
    for name in ("raw-empty", "raw-no-file"):
        score = _build(name)["score"]
        assert score["disqualified"] is True, f"{name} 应被门禁拦下"
        assert score["disqualifying_assertions"] == ["R1_file_exists"]
        assert score["ratio"] == 0 and score["weighted_sum"] == 0


def test_gate_unscored_does_not_disqualify():
    """I8：门禁本身无法判定（trace 缺失致 R5 无据）时不拦。

    按 on_unscored=do_not_disqualify：无法证明不合格就不判不合格。这不会放水——
    同一份提交的 credit 层 R3/R4 也因 trace 缺失而 unscored，拿不到分。
    """
    score = _build("raw-no-trace")["score"]
    assert score["disqualified"] is False
    assert score["disqualifying_assertions"] == []
    assert score["ratio"] == pytest.approx(0.5)  # 只剩 R2 的 2 分


def test_gate_failure_keeps_credit_observations():
    """方案 Y 的核心：门禁只管分数，不管观测。

    若照"门禁失败就把 credit 全标 blocked"，raw-no-file 的 R3/R4 观测会被抹掉，
    credit_pass_weight 变成 0，诊断信息随分数一起消失。这里锁住它必须保留。
    """
    report = _build("raw-no-file")
    got = _by_id(report)
    assert report["score"]["disqualified"] is True
    assert got["R3_retry_evidence"]["state"] == "pass"
    assert got["R4_no_param_hallucination"]["state"] == "pass"
    assert report["score"]["credit_pass_weight"] == 2, "门禁未过也要照算 credit 通过权重（仅供诊断）"
    assert any("无资格评分" in n for n in report["notes"])


def test_score_is_monotonic_across_fixtures():
    """P6 的验收判据：分数必须随质量单调上升。

    旧版：0.333 / 0.500 / 0.500 / 0.500 —— 三档质量撞在一起，序是塌的。
    新版：0 / 0 / 0.25 / 0.50 / 1.00。
    """
    ratios = [_build(n)["score"]["ratio"] for n in NAMES]
    assert ratios == sorted(ratios), f"分数序塌陷：{dict(zip(NAMES, ratios))}"
    assert ratios[0] == ratios[1] == 0, "两种门禁失败都必须是 0"
    assert ratios[2:] == [0.25, 0.5, 1.0], f"中间档位不对：{ratios[2:]}"
    assert ratios[-1] == 1.0, "满分必须可达，否则上界没有锚点"


# ---------------------------------------------------------------------------
# 归因：技能缺口 + 无归属模式留痕
# ---------------------------------------------------------------------------


def test_skill_gaps_only_from_fail_and_aggregated():
    """只聚合并按 weight_lost 降序；skill_id 为 null 的断言不制造缺口。"""
    gaps = _build("raw-fake-retry")["skill_gaps"]
    assert [g["skill_id"] for g in gaps] == ["multi_step_planning", "failure_retry"]
    assert gaps[0]["weight_lost"] == 2 and gaps[0]["failed_assertions"] == ["R2_report_correct"]
    assert gaps[1]["weight_lost"] == 1 and gaps[1]["patterns"] == ["retry_wrong_params"]


def test_unattributed_patterns_keep_gate_evidence():
    """P5 的修法：R1 失败不属于任何技能，但不能连痕迹都没有。

    旧版无文件时 skill_gaps 是空数组——"没交产物"在报告里完全不留痕，状态层推不出任何复习项。
    现在它出现在 unattributed_patterns，且不进 skill_gaps（避免污染某技能的错误模式统计）。
    """
    report = _build("raw-no-file")
    assert report["skill_gaps"] == [], "R1 无技能归属，不应制造虚假的技能缺口"
    assert report["unattributed_patterns"] == [
        {"assertion_id": "R1_file_exists", "layer": "gate", "patterns": ["no_output_file"]}
    ]

    # 全过的提交不应有无归属模式
    assert _build("raw-perfect")["unattributed_patterns"] == []


def test_attribution_policy_matches_yaml():
    """契约自解释：报告里的归因表必须与任务 YAML 一致，状态层不必回读 YAML。"""
    yaml_attr = TASK["scoring"]["attribution"]
    for name in NAMES:
        policy = _build(name)["attribution_policy"]
        for state in ("pass", "fail", "blocked", "unscored"):
            assert policy[state]["counts_toward_mastery"] == yaml_attr[state]["counts_toward_mastery"]
            assert policy[state]["emit_error_patterns"] == yaml_attr[state]["emit_error_patterns"]


# ---------------------------------------------------------------------------
# schema 自身要能抓错
# ---------------------------------------------------------------------------


def test_schema_rejects_tampered_report():
    """schema 的有效性检查本身要能抓错——否则"通过 schema"这句话没意义。"""
    report = _build("raw-fake-retry")

    bad_state = copy.deepcopy(report)
    bad_state["assertions"][0]["state"] = "passed"  # 非法枚举
    with pytest.raises(ReportError, match="schema"):
        validate_report(bad_state, SCHEMA)

    extra_key = copy.deepcopy(report)
    extra_key["mastery_delta"] = {"failure_retry": -0.3}  # v2 故意不装的跨层推断
    with pytest.raises(ReportError, match="schema"):
        validate_report(extra_key, SCHEMA)

    bad_policy = copy.deepcopy(report)
    bad_policy["score"]["denominator_policy"] = "exclude_missing"
    with pytest.raises(ReportError, match="schema"):
        validate_report(bad_policy, SCHEMA)


def test_schema_rejects_gate_scored_as_credit():
    """schema 也要挡住"门禁偷偷计分"：把 gate 断言的 weight 改成 1 必须被拒。"""
    report = _build("raw-perfect")
    bad = copy.deepcopy(report)
    bad["assertions"][0]["weight"] = 1  # 第一条是 R1，layer=gate
    assert bad["assertions"][0]["layer"] == "gate"
    with pytest.raises(ReportError, match="schema"):
        validate_report(bad, SCHEMA)


def test_schema_rejects_disqualified_with_nonzero_ratio():
    """disqualified=true 但 ratio 不是 0，是自相矛盾的报告。"""
    report = _build("raw-no-file")
    assert report["score"]["disqualified"] is True
    bad = copy.deepcopy(report)
    bad["score"]["ratio"] = 0.5
    with pytest.raises(ReportError, match="schema"):
        validate_report(bad, SCHEMA)
