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
    load_pattern_skills,
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
        assert score["counterfactual_credit"] == expected
        assert score["denominator_policy"] == "fixed_at_credit_total_weight"
        assert sum(score["by_state"].values()) == len(ALL_IDS)


def test_counterfactual_credit_consistent_with_assertions():
    """counterfactual_credit 与断言逐条对得上——门禁未过时它仍照算，这个值才是诊断用的那个。"""
    for name in NAMES:
        report = _build(name)
        manual = sum(a["weight"] for a in report["assertions"] if a["layer"] == "credit" and a["state"] == "pass")
        assert report["score"]["counterfactual_credit"] == manual


def test_gate_failure_disqualifies():
    """I7：任一门禁 fail ⇒ disqualified，ratio 记 0（不是 0.3、不是 0.5）。"""
    for name in ("raw-empty", "raw-no-file"):
        score = _build(name)["score"]
        assert score["disqualified"] is True, f"{name} 应被门禁拦下"
        assert score["disqualifying_assertions"] == ["R1_file_exists"]
        assert score["ratio"] == 0 and score["weighted_sum"] == 0


def test_gate_unscored_does_not_disqualify():
    """I8：门禁本身无法判定（trace 缺失致 R5 无据）时不拦。

    按 on_unscored=do_not_disqualify：无法证明不合格就不判不合格。
    round-70 起本题要求过程（`submission_requirements.requires_process: true`），
    raw-no-trace 属于「缺过程」⇒ ratio 记 0、不计掌握度，但 **disqualified 仍是 False**
    （缺过程不是门禁失败），反事实得分 2/4 保留下来当诊断。
    """
    report = _build("raw-no-trace")
    score = report["score"]
    assert score["disqualified"] is False
    assert score["disqualifying_assertions"] == []
    assert score["ratio"] == 0, "缺过程 ⇒ 本次不计能力分"
    assert score["counterfactual_credit"] == 2, "断言照常判定，反事实得分是诊断信息"
    assert report["process_fact"] == {"required": True, "submitted": False, "missing": True}


def test_process_missing_suppresses_score_but_keeps_observations():
    """P2 / Q5-D1 的核心：缺过程「不算数」≠「没做对」。

    若把 credit 断言改写成 unscored，raw-no-trace 的 R2 判定会一起消失，
    等于用评分逻辑吞掉诊断（与决策 5 冲突）。这里锁住「断言不动、只压分数与归因」。
    """
    report = _build("raw-no-trace")
    got = _by_id(report)
    assert got["R2_report_correct"]["state"] == "pass", "做对了就得记下来"
    assert report["score"]["counterfactual_credit"] == 2
    assert report["report_attribution"] == {
        "counts_toward_mastery": False, "emit_error_patterns": False
    }
    assert any("流程不完整" in n for n in report["notes"])


def test_optional_process_task_scores_with_answer_only():
    """不需要步骤的题：只交答案即可，缺过程不压制分数（Q5-D1 的另一半）。"""
    task = copy.deepcopy(TASK)
    task["submission_requirements"]["requires_process"] = False
    report = build_report(task, _raw("raw-no-trace"), VOCAB)
    assert report["process_fact"] == {"required": False, "submitted": False, "missing": False}
    assert report["score"]["ratio"] == pytest.approx(0.5), "只交答案的题照常算分"
    assert report["report_attribution"]["counts_toward_mastery"] is True


def test_process_note_counts_as_submitted():
    """人拍板口径：写了「没思路」也算交了步骤，不判「缺过程」。"""
    raw = _raw("raw-no-trace")
    raw["submission"]["process_note"] = "没思路：不知道要按 offset 分页拉"
    report = build_report(TASK, raw, VOCAB)
    assert report["process_fact"]["submitted"] is True
    assert report["process_fact"]["missing"] is False
    assert report["score"]["ratio"] == pytest.approx(0.5)


def test_gate_failure_keeps_credit_observations():
    """方案 Y 的核心：门禁只管分数，不管观测。

    若照"门禁失败就把 credit 全标 blocked"，raw-no-file 的 R3/R4 观测会被抹掉，
    counterfactual_credit 变成 0，诊断信息随分数一起消失。这里锁住它必须保留。
    """
    report = _build("raw-no-file")
    got = _by_id(report)
    assert report["score"]["disqualified"] is True
    assert got["R3_retry_evidence"]["state"] == "pass"
    assert got["R4_no_param_hallucination"]["state"] == "pass"
    assert report["score"]["counterfactual_credit"] == 2, "门禁未过也要照算 credit 通过权重（仅供诊断）"
    assert any("无资格评分" in n for n in report["notes"])


def test_score_orders_by_process_gate_then_accuracy():
    """分数判据（round-70 修订）：先过过程与门禁两道门槛，再按正确性排序。

    旧判据要求「五份夹具分数严格单调上升」，前提是"没报 trace 也能拿 0.5"。
    round-70 起本题要求过程，raw-no-trace 落在「缺过程」⇒ 0 分，与 raw-empty 并列。
    这是口径的直接后果，不是回归：分数不区分「没做」与「做了没交过程」，
    但过程门槛本身可区分（`process_fact`）。

    现行：0 / 0 / 0.25 / 0 / 1.0
      raw-empty      0     没交产物 + 没过程
      raw-no-file    0     有过程，但门禁 R1 失败
      raw-fake-retry 0.25  过了门槛，答错（R2/R3 fail）
      raw-no-trace   0     答对了，但缺过程
      raw-perfect    1.0   全过（上界锚点）
    """
    ratios = dict(zip(NAMES, (_build(n)["score"]["ratio"] for n in NAMES)))
    assert ratios == {
        "raw-empty": 0, "raw-no-file": 0, "raw-fake-retry": 0.25,
        "raw-no-trace": 0, "raw-perfect": 1.0,
    }, ratios
    assert ratios["raw-fake-retry"] < ratios["raw-perfect"], "过门槛后按正确性排序"
    assert ratios["raw-perfect"] == 1.0, "满分必须可达，否则上界没有锚点"
    assert min(ratios.values()) == 0, "下界必须可达"


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


# ---------------------------------------------------------------------------
# TD-034 (C)：评分策略声明的加载期对账（见 review-response-v1.md §38 / §40）
# ---------------------------------------------------------------------------


def _task_with_policy(path: str, value) -> dict:
    """按点分路径改一份任务副本，用来造「声明漂移」（不落盘）。"""
    task = copy.deepcopy(TASK)
    keys = path.split(".")
    node = task
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value
    return task


def test_td034_declared_policy_matches_builder():
    """正向：磁盘上这份 YAML 的 5 组声明与 builder 常量逐项一致。"""
    gate, credit = rubric_layers(TASK)
    assert [r["id"] for r in gate] == GATE_IDS
    assert [r["id"] for r in credit] == CREDIT_IDS
    assert build_report(TASK, _raw("raw-perfect"), VOCAB)["score"]["ratio"] == 1.0


@pytest.mark.parametrize(
    "path,value,keyword",
    [
        ("scoring.score.gate.disqualified_if", "never", "disqualified_if"),
        ("scoring.score.gate.disqualify_ratio", 0.3, "disqualify_ratio"),
        ("scoring.score.gate.on_unscored", "disqualify", "on_unscored"),
        ("scoring.score.credit.denominator", "variable", "denominator"),
        ("scoring.score.credit.contribution",
         {"pass": 0, "fail": 0, "blocked": 0, "unscored": 0}, "contribution"),
    ],
)
def test_td034_rejects_drifted_policy(path, value, keyword):
    """5 组影响分数的声明任一漂移，build_report 必须给出可读 ReportError。"""
    with pytest.raises(ReportError, match=keyword):
        build_report(_task_with_policy(path, value), _raw("raw-perfect"), VOCAB)


def test_td034_rejects_bool_as_zero():
    """类型边界：False == 0 但布尔不是整数，不能混过对账。"""
    task = _task_with_policy("scoring.score.gate.disqualify_ratio", False)
    with pytest.raises(ReportError, match="disqualify_ratio"):
        build_report(task, _raw("raw-perfect"), VOCAB)


def test_td034_rejects_partial_contribution():
    """contribution 少一个状态也算漂移，不能靠少写一行绕过对账。"""
    task = copy.deepcopy(TASK)
    task["scoring"]["score"]["credit"]["contribution"].pop("unscored")
    with pytest.raises(ReportError, match="contribution"):
        build_report(task, _raw("raw-perfect"), VOCAB)


def test_td034_does_not_lock_explanatory_fields():
    """口径锁：formula 与两个 source 是纯说明字段，改值不该被拒（D-TD034-2 = 只锁 5 组）。"""
    task = copy.deepcopy(TASK)
    task["scoring"]["score"]["formula"] = "plain_ratio"
    task["scoring"]["score"]["gate"]["source"] = "rubric_credit"
    task["scoring"]["score"]["credit"]["source"] = "rubric_gate"
    assert build_report(task, _raw("raw-perfect"), VOCAB)["score"]["ratio"] == 1.0


# --- TD-033 第 4 子项：rubric 条目形状（review-response-v1.md §42） ---

def _task_with_bad_item(layer: str, value):
    task = copy.deepcopy(TASK)
    task[f"rubric_{layer}"][0] = value
    return task


@pytest.mark.parametrize(
    "layer,value,typename",
    [("gate", "not-a-dict", "str"), ("credit", 42, "int")],
    ids=["T1-gate-string", "T2-credit-int"],
)
def test_td033_shape_rejects_non_mapping_item(layer, value, typename):
    """§38.3 T1/T2：坏条目必须在加载期给可读 ReportError，而不是裸 TypeError。"""
    with pytest.raises(ReportError, match=f"rubric_{layer} 第 1 条") as exc:
        rubric_layers(_task_with_bad_item(layer, value))
    assert typename in str(exc.value)


@pytest.mark.parametrize(
    "layer,value",
    [("gate", "not-a-dict"), ("credit", 42)],
    ids=["T1-gate-string", "T2-credit-int"],
)
def test_td033_shape_build_report_rejects_non_mapping_item(layer, value):
    """§38.3 的 T2 走的是 build_report() 复现，全链路入口也必须给可读错误。"""
    with pytest.raises(ReportError, match=f"rubric_{layer} 第 1 条"):
        build_report(_task_with_bad_item(layer, value), _raw("raw-perfect"), VOCAB)


def test_td033_shape_current_task_passes():
    """正向锁：现役 YAML 两层条目全是映射，不该被新校验挡住。"""
    gate, credit = rubric_layers(copy.deepcopy(TASK))
    assert gate and credit


def test_td033_shape_rejects_bad_item_at_any_position():
    """不是只查第 1 条：任意位置的坏条目都要点名序号。"""
    task = copy.deepcopy(TASK)
    idx = len(task["rubric_credit"])
    task["rubric_credit"][-1] = None
    with pytest.raises(ReportError, match=f"rubric_credit 第 {idx} 条"):
        rubric_layers(task)


# --- TD-035 ② 案 A：depends_on 后置依赖（review-response-v1.md §46） ---
# 口径：全局 gate+credit 序列 = 执行顺序；被依赖项必须排在引用方之前。
# 环检查仍由 tests/test_task_yaml.py::test_dependencies_declared_and_acyclic
# 静态负责，这里不重复实现（§45.4），由本地裁判 -k acyclic 验证仍绿。

def _task_with_deps(rid, deps, gate_type=None):
    """造一份内存副本：改指定断言的 depends_on（不落盘）。"""
    task = copy.deepcopy(TASK)
    row = next(r for r in task["rubric_gate"] + task["rubric_credit"] if r["id"] == rid)
    row["depends_on"] = deps
    if gate_type:
        row["type"] = gate_type
    return task


def test_td035_order_current_task_passes_with_global_index():
    """现役合法配置必须通过：全局顺序 R1,R5,R2,R3,R4，R2→R1 是 gate 被 credit 引用。"""
    gate, credit = rubric_layers(copy.deepcopy(TASK))
    order = [r["id"] for r in gate + credit]
    assert order == [
        "R1_file_exists", "R5_clean_exit", "R2_report_correct",
        "R3_retry_evidence", "R4_no_param_hallucination",
    ]
    assert credit[0]["depends_on"] == ["R1_file_exists"]
    assert order.index("R1_file_exists") < order.index("R2_report_correct")


def test_td035_order_accepts_cross_layer_forward_reference():
    """跨层前置合法：R2 也可以引用 gate 第二条 R5，不是只认第一条。"""
    task = _task_with_deps("R2_report_correct", ["R5_clean_exit"])
    gate, credit = rubric_layers(task)
    assert credit[0]["depends_on"] == ["R5_clean_exit"]


@pytest.mark.parametrize(
    "dep",
    ["R3_retry_evidence", "R4_no_param_hallucination"],
    ids=["T4-R3", "T4b-R4"],
)
def test_td035_order_rejects_backward_dependency(dep):
    """§38.3 T4：后置依赖在加载期拒绝，而不是让报告层通过、执行器 blocked。"""
    with pytest.raises(ReportError, match="后置断言") as exc:
        rubric_layers(_task_with_deps("R2_report_correct", [dep]))
    assert dep in str(exc.value) and "R2_report_correct" in str(exc.value)


def test_td035_order_build_report_rejects_backward_dependency():
    """全链路入口也要拒：非法配置不能在 build_report() 里重新算出 ratio=1.0。"""
    with pytest.raises(ReportError, match="后置断言"):
        build_report(
            _task_with_deps("R2_report_correct", ["R3_retry_evidence"]),
            _raw("raw-perfect"), VOCAB,
        )


def test_td035_order_error_is_readable():
    """错误信息点名 item / 依赖 / 双方全局序号，不是一句泛泛的「顺序不合法」。"""
    with pytest.raises(ReportError) as exc:
        rubric_layers(_task_with_deps("R2_report_correct", ["R3_retry_evidence"]))
    msg = str(exc.value)
    assert "R2_report_correct" in msg and "R3_retry_evidence" in msg
    assert "第 3 条" in msg and "第 4 条" in msg and "后置" in msg


def test_td035_order_prioritizes_missing_reference():
    """§45.4：既拼错又后置时先报「引用不存在」，不把拼错误报成顺序问题。"""
    task = _task_with_deps("R2_report_correct", ["R3_retry_evidence", "R_typo"])
    with pytest.raises(ReportError) as exc:
        rubric_layers(task)
    assert "引用不存在" in str(exc.value)
    assert "后置" not in str(exc.value)


def test_td035_order_rejects_gate_referencing_credit():
    """反向依赖：gate 引用 credit（R1→R2）按全局 index 也应拒。

    现役被 Step1「非空 dep 只允许 json_field_equals」挡住；这里显式把 gate 改成
    该类型，防未来新增 gate 侧 json_field_equals 时出现意外（§45.4）。
    """
    task = _task_with_deps("R1_file_exists", ["R2_report_correct"], gate_type="json_field_equals")
    with pytest.raises(ReportError, match="后置断言") as exc:
        rubric_layers(task)
    assert "R1_file_exists" in str(exc.value) and "第 1 条" in str(exc.value)


def test_td035_order_checks_every_dependency():
    """不是只查 deps 第一个：合法前置 + 后置混排也要被抓出来。"""
    task = _task_with_deps("R2_report_correct", ["R1_file_exists", "R3_retry_evidence"])
    with pytest.raises(ReportError, match="后置断言"):
        rubric_layers(task)


def test_td035_order_self_dependency_still_reported_by_step1():
    """边界：自依赖仍由 Step1 报「自依赖」，新校验不抢错误优先级。"""
    with pytest.raises(ReportError, match="自依赖") as exc:
        rubric_layers(_task_with_deps("R2_report_correct", ["R2_report_correct"]))
    assert "后置" not in str(exc.value)


def test_td035_order_does_not_change_score():
    """正向锁：加载期硬约束只挡非法配置，现役五夹具分数一字不变。"""
    report = build_report(copy.deepcopy(TASK), _raw("raw-perfect"), VOCAB)
    assert report["score"]["ratio"] == 1.0


# ---------------------------------------------------------------------------
# TD-038：任务版本不一致 ⇒ 本份作废（不算数，但不是「不会」）
#   人拍板的第三种处置：报告照出、分数不算、不计能力短板、写清请重交。
#   与 disqualified 分开记：disqualified = 学生没交东西，voided = 这次不算数。
# ---------------------------------------------------------------------------


def _build_with_version(name: str, version: str | None) -> dict:
    raw = copy.deepcopy(_raw(name))
    if version is None:
        raw["submission"].pop("task_version", None)
    else:
        raw["submission"]["task_version"] = version
    return build_report(TASK, raw, VOCAB)


def test_td038_version_mismatch_voids_report_but_keeps_diagnostics():
    """作废不是「吞掉这单」：分数不算，但断言判定与反事实得分都还在。"""
    report = _build_with_version("raw-perfect", "2020-01-01.1")
    score = report["score"]
    assert score["voided"] is True
    assert score["void_reason"] == "task_version_mismatch"
    assert score["ratio"] == 0 and score["weighted_sum"] == 0
    assert score["counterfactual_credit"] == 4, "反事实得分是诊断信息，作废也不丢"
    assert all(a["state"] == "pass" for a in report["assertions"]), "逐条判定照旧保留"
    assert any("重新提交" in n for n in report["notes"])
    validate_report(report, SCHEMA)


def test_td038_version_match_scores_normally():
    report = _build_with_version("raw-perfect", TASK["task_version"])
    assert report["score"]["voided"] is False
    assert report["score"]["ratio"] == 1.0
    assert "void_reason" not in report["score"], "非作废不带 void_reason（schema 也不要求）"


def test_td038_missing_version_is_not_compared():
    """缺省不比对：五夹具都没带 task_version，行为必须与改动前一致（向后兼容）。"""
    assert all("task_version" not in _raw(n)["submission"] for n in NAMES)
    for name in NAMES:
        assert _build(name)["score"]["voided"] is False


def test_td038_voided_never_counts_toward_mastery():
    """「不记能力短板」必须有执行者，不能只写在 notes 文案里。

    **2026-11-16 补强**：原版只验了报告上的 `report_attribution` 开关（报告级承诺），
    没验计入层真的照办——而 round-88 外审回件正是从这里指出「名字写着永不计入，
    断言只查了一个布尔值」。现在直接问掌握度：一份作废的满分提交必须给不出证据。
    """
    from arena import mastery as m

    voided = _build_with_version("raw-fake-retry", "1999-01-01.1")
    assert voided["report_attribution"] == {
        "counts_toward_mastery": False, "emit_error_patterns": False
    }
    normal = _build("raw-fake-retry")
    assert normal["report_attribution"]["counts_toward_mastery"] is True, "非作废不是恒 false"

    # 把三份夹具的版本都改成不一致：作废后**任何**技能都取不到计入证据。
    for name in NAMES:
        rep = _build_with_version(name, "1999-01-01.1")
        assert rep["score"]["voided"] is True, name
        for skill in ("multi_step_planning", "failure_retry", "tool_param_validation"):
            ev = m.skill_evidence(rep, skill)
            assert not ev.counts, f"{name} 作废后 {skill} 仍有计入证据"
            assert ev.gate_status == "voided"
        # 反面对照：同一份夹具不动作废时，至少有一个技能能给出证据——防止上面的循环恒真。
        assert any(
            m.skill_evidence(_build(name), s).counts
            for s in ("multi_step_planning", "failure_retry", "tool_param_validation")
        ), f"{name} 不动作废也没有任何证据，这条测试就没在测作废"


def test_td038_report_attribution_is_derived():
    """report_attribution 是派生汇总，不是第二真相源：可由 policy + by_state + suppressed 重算。"""
    states = ("pass", "fail", "blocked", "unscored")
    for name in NAMES:
        report = _build(name)
        policy, by_state = report["attribution_policy"], report["score"]["by_state"]
        suppressed = report["score"]["voided"] or report["process_fact"]["missing"]
        if suppressed:
            expect = {"counts_toward_mastery": False, "emit_error_patterns": False}
        else:
            expect = {
                "counts_toward_mastery": any(policy[s]["counts_toward_mastery"] and by_state[s] > 0
                                            for s in states),
                "emit_error_patterns": any(policy[s]["emit_error_patterns"] and by_state[s] > 0
                                          for s in states),
            }
        assert report["report_attribution"] == expect, name


# ---------------------------------------------------------------------------
# B-06 / ROADMAP R-2：错误模式 → 技能缺口映射表接进代码（只作兜底归因）
# ---------------------------------------------------------------------------


def _task_without_skill_id(rid: str) -> dict:
    """造一份"任务作者漏写 skill_id"的任务副本——这不是假想场景：rubric 只要漏一个字段就发生。"""
    task = copy.deepcopy(TASK)
    for layer in ("rubric_gate", "rubric_credit"):
        for item in task[layer]:
            if item["id"] == rid:
                item["skill_id"] = None
                return task
    raise AssertionError(f"rubric 里没有 {rid}")


def _gaps_by_skill(report: dict) -> dict:
    return {g["skill_id"]: g for g in report["skill_gaps"]}


def test_b06_gap_survives_missing_skill_id_via_vocabulary():
    """正向锁（B-06 的判据）：删掉断言的 skill_id，缺口仍能经词表 mapped_skill_ids 产出。

    改动前这条失败只能掉进 unattributed_patterns——"该练失败重试"这个结论就丢了。
    """
    task = _task_without_skill_id("R3_retry_evidence")
    report = build_report(task, _raw("raw-fake-retry"), VOCAB)
    gaps = _gaps_by_skill(report)
    assert set(gaps) == {"multi_step_planning", "failure_retry"}
    assert gaps["failure_retry"]["failed_assertions"] == ["R3_retry_evidence"]
    assert gaps["failure_retry"]["weight_lost"] == 1
    assert gaps["failure_retry"]["attributed_by"] == "mapping", "来源必须可审计，不能只说'有缺口'"
    assert report["unattributed_patterns"] == [], "能归因就不该再进无归属留痕"
    validate_report(report, SCHEMA)


def test_b06_declared_skill_id_is_never_overridden_by_vocabulary():
    """Q10=A 的边界：断言声明了 skill_id 就只认它，词表不得插嘴（TD-001 仍未销项）。

    做法是把词表指向"另一个技能"，看报告会不会被带偏。带偏了就说明归因从"按 spec.skill_id"
    偷偷改成了"按 pattern.mapped_skill_ids"——那正是人拍板说现在不要做的 B 方案。
    """
    hijacked = dict(load_pattern_skills())
    hijacked["retry_wrong_params"] = ("multi_step_planning",)
    report = build_report(TASK, _raw("raw-fake-retry"), VOCAB, pattern_skills=hijacked)
    gaps = _gaps_by_skill(report)
    assert [g["skill_id"] for g in report["skill_gaps"]] == ["multi_step_planning", "failure_retry"]
    assert gaps["failure_retry"]["weight_lost"] == 1, "词表不参与时权重不能被摊薄"
    assert gaps["failure_retry"]["attributed_by"] == "assertion"
    assert gaps["multi_step_planning"]["failed_assertions"] == ["R2_report_correct"]


def test_b06_mapping_one_to_many_splits_weight():
    """兜底路径可能出现一对多：权重按技能数等分，总量不得凭空变大。

    词表当前没有一对多条目（14 条里 9 条填了，全是一对一），所以这条是**机制锁**而非现状描述：
    将来某条模式真的属于两个技能时，这里先保证不会把 1 分的失败记成 2 分。
    """
    task = _task_without_skill_id("R3_retry_evidence")
    split = dict(load_pattern_skills())
    split["retry_wrong_params"] = ("failure_retry", "multi_step_planning")
    report = build_report(task, _raw("raw-fake-retry"), VOCAB, pattern_skills=split)
    gaps = _gaps_by_skill(report)
    assert gaps["failure_retry"]["weight_lost"] == 0.5
    assert gaps["multi_step_planning"]["weight_lost"] == 2.5, "R2 的 2 分 + R3 分摊来的 0.5"
    assert sum(g["weight_lost"] for g in report["skill_gaps"]) == 3 <= CREDIT_TOTAL
    assert gaps["failure_retry"]["failed_assertions"] == ["R3_retry_evidence"]
    validate_report(report, SCHEMA)


def test_b06_missing_mapping_is_loud_not_silent():
    """词表查不到该模式时必须报错，不能当成"没有技能归属"。

    静默降级正是这类映射最常见的坏法：词表改了 pattern_id，兜底悄悄失效，报告仍"看着正常"。
    """
    task = _task_without_skill_id("R3_retry_evidence")
    with pytest.raises(ReportError, match="兜底归因会静默失效"):
        build_report(task, _raw("raw-fake-retry"), VOCAB, pattern_skills={})


def test_b06_five_fixtures_unchanged_declared_path_wins():
    """对账 + 反证条件的实测：现有五夹具的归因与「纯断言路径」逐例相同。

    这不是"改动无害"的自证，而是**反证条件被触发**的证据：本任务四条断言都写了 skill_id，
    所以映射表一次都没参与，`attributed_by` 全是 assertion。也就是说在今天的词表与任务上，
    映射表不带来任何新信息，只值"兜底"两个字——计划书里"自研核心"的说法应据此降级（见 §78）。
    """
    for name in NAMES:
        report = _build(name)
        expect: dict[str, dict] = {}
        for a in report["assertions"]:
            if a["state"] != "fail" or not a["skill_id"]:
                continue
            gap = expect.setdefault(
                a["skill_id"], {"failed_assertions": [], "patterns": [], "weight_lost": 0}
            )
            gap["failed_assertions"].append(a["id"])
            gap["weight_lost"] += a["weight"]
            for code in a["error_patterns"]:
                if code not in gap["patterns"]:
                    gap["patterns"].append(code)
        got = {
            g["skill_id"]: {
                "failed_assertions": g["failed_assertions"],
                "patterns": g["patterns"],
                "weight_lost": g["weight_lost"],
            }
            for g in report["skill_gaps"]
        }
        assert got == expect, name
        assert all(g["attributed_by"] == "assertion" for g in report["skill_gaps"]), name


def test_b06_every_failure_is_accounted_exactly_once():
    """归因守恒：每条失败要么进 skill_gaps、要么进 unattributed_patterns，不许两边都记或都不记。"""
    for name in NAMES:
        report = _build(name)
        in_gaps = {i for g in report["skill_gaps"] for i in g["failed_assertions"]}
        in_unattr = {u["assertion_id"] for u in report["unattributed_patterns"]}
        fails = {a["id"] for a in report["assertions"] if a["state"] == "fail"}
        assert in_gaps | in_unattr == fails, name
        assert not (in_gaps & in_unattr), name
        assert sum(g["weight_lost"] for g in report["skill_gaps"]) <= CREDIT_TOTAL, name
