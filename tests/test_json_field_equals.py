"""S3-3-4：R2 json_field_equals 执行器的约束测试。

守的是六件结构性事实：
1. 判定语义以 docs/executor-semantics.md §3 为准：容差字段 abs(差) ≤ tolerance，
   其余 subset 精确匹配，禁止浮点严格 ==；
2. 期望值 / 容差 / 合法 match 键 / 分类规则全从任务 YAML 派生，坏配置接触产物前就抛；
3. 分类「收集全部命中」而非首命中即止（手写 fake-retry 有两个 pattern）；
4. depends_on 折叠由执行器显式判定，缺前置结果直接抛，不猜；
5. 产物本体只从 raw.artifacts 读，绝不从 trace 里 write_report 的自述推断；
6. 五夹具 R2 state 与最终报告基线一致，现算结果能直接喂 build_report()。
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import assertion_evaluator as ae  # noqa: E402
from arena import diagnosis_report as dr  # noqa: E402
from arena import trace_pipeline as tp  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
FIXTURES = ROOT / "fixtures"
TASK = dr.load_task(TASK_PATH)
TARGET = "/workspace/report.json"
R1 = "R1_file_exists"
_MISSING = object()
_DEFAULT = object()


def spec() -> dict:
    return copy.deepcopy(ae.load_r2_spec())


def task() -> dict:
    return yaml.safe_load(TASK_PATH.read_text(encoding="utf-8"))


def raw(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def content(obj: dict | None = None, **overrides) -> str:
    base = dict(spec()["expected"] if obj is None else obj)
    base.update(overrides)
    return json.dumps(base)


def deps(state: str = "pass") -> dict:
    return {R1: {"state": state}}


def run(content_value=_MISSING, s: dict | None = None, dep_state: str = "pass",
        vocab=None, dependency_results=_DEFAULT, artifacts=_DEFAULT) -> dict:
    s = spec() if s is None else s
    if artifacts is _DEFAULT:
        if content_value is None:
            artifacts = None
        elif content_value is _MISSING:
            artifacts = {}
        else:
            artifacts = {TARGET: content_value}
    if dependency_results is _DEFAULT:
        dependency_results = deps(dep_state)
    return ae.evaluate_assertion(s, None, vocab=vocab, artifacts=artifacts,
                                 dependency_results=dependency_results)


def patterns(r: dict) -> list[str]:
    return r.get("error_patterns", [])


# ---------------------------------------------------------------------------
# 契约来自 YAML，代码里没有第二份任务常量
# ---------------------------------------------------------------------------


def test_r2_contract_comes_from_yaml():
    s, t = spec(), task()
    y = next(r for r in t["rubric_credit"] if r["id"] == "R2_report_correct")
    assert s == y
    assert s["type"] == "json_field_equals"
    assert s["target"] == TARGET
    assert s["compare"] == "subset"
    assert s["depends_on"] == [R1]
    assert s["tolerance"] == {"total_amount": 0.01}


def test_expected_matches_test_inputs():
    """同一份期望值不写两遍：rubric 与 test_inputs.expected 必须一致。"""
    assert spec()["expected"] == task()["test_inputs"]["expected"]


def test_match_keys_are_derived_from_expected():
    s = spec()
    assert ae._json_match_keys(s) == (
        "missing_fields", "valid_count_mismatch", "total_amount_mismatch")
    s["expected"]["extra_metric"] = 1
    assert ae._json_match_keys(s)[-1] == "extra_metric_mismatch"


def test_cases_cover_all_declared_patterns():
    s = spec()
    assert {c["pattern"] for c in s["error_pattern_detection"]["cases"]} == set(s["error_patterns"])
    assert "default" not in s["error_pattern_detection"]


# ---------------------------------------------------------------------------
# 判定语义：subset + tolerance，禁止浮点 ==
# ---------------------------------------------------------------------------


def test_exact_match_passes():
    r = run(content())
    assert r["state"] == "pass" and not patterns(r)
    assert r["evidence"]["observed"] == spec()["expected"]
    assert r["evidence"]["expected"] == spec()["expected"]


def test_subset_ignores_extra_fields():
    r = run(content(note="anything", extra=[1, 2, 3]))
    assert r["state"] == "pass"
    assert r["evidence"]["observed"]["extra"] == [1, 2, 3]


@pytest.mark.parametrize("drift", [0.0, 0.005, 0.009, 0.01])
def test_tolerance_accepts_drift_within_bound(drift):
    r = run(content(total_amount=1837.99 + drift))
    assert r["state"] == "pass", drift


@pytest.mark.parametrize("drift", [0.011, 0.5, 100.0, -0.011])
def test_tolerance_rejects_drift_outside_bound(drift):
    r = run(content(total_amount=1837.99 + drift))
    assert r["state"] == "fail", drift
    assert patterns(r) == ["wrong_total_amount"], drift


def test_float_strict_equality_would_have_failed():
    """如果实现写成浮点 ==，0.005 的合法漂移会被误判为错；这里必须 pass。"""
    drifted = 1837.99 + 0.005
    assert drifted != 1837.99  # 前提：严格 == 确实会失败
    assert run(content(total_amount=drifted))["state"] == "pass"


@pytest.mark.parametrize("bad", [16, 18, 0, -1])
def test_valid_count_is_exact(bad):
    r = run(content(valid_count=bad))
    assert r["state"] == "fail"
    assert patterns(r) == ["wrong_valid_count"]


def test_bool_does_not_silently_equal_int():
    """Python 里 True == 1；期望值是整数时布尔值必须判错。"""
    assert run(content(valid_count=True))["state"] == "fail"
    assert run(content(total_amount=True))["state"] == "fail"


def test_bool_guard_covers_true_equals_one():
    """M7 变异抓到的假锁：上面两条用期望值 17，True == 17 本就不成立，
    守卫被删掉仍然全绿。必须把期望值改成 1，才真的覆盖 True == 1 隐式等价。
    """
    s = spec()
    s["expected"]["valid_count"] = 1
    r = run(content({"valid_count": True, "total_amount": 1837.99}), s=s)
    assert r["state"] == "fail"
    assert patterns(r) == ["wrong_valid_count"]


@pytest.mark.parametrize("field,bad", [("valid_count", "17"), ("total_amount", "1837.99"),
                                       ("valid_count", None), ("total_amount", None)])
def test_string_and_null_values_do_not_pass(field, bad):
    r = run(content(**{field: bad}))
    assert r["state"] == "fail"
    assert patterns(r) == ["wrong_valid_count" if field == "valid_count"
                           else "wrong_total_amount"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 10 ** 400])
def test_non_finite_numbers_do_not_pass(bad):
    r = run(content(total_amount=bad))
    assert r["state"] == "fail", bad
    assert patterns(r) == ["wrong_total_amount"]


def test_numeric_equivalent_int_is_accepted_for_exact_field():
    """17.0 == 17 在 Python 数值语义下成立；精确匹配不等于「类型必须一致」，如实锁住。"""
    assert run(content(valid_count=17.0))["state"] == "pass"


@pytest.mark.parametrize("obj,expected_pattern", [
    ({"total_amount": 1837.99}, "valid_count"),
    ({"valid_count": 17}, "total_amount"),
])
def test_missing_one_field(obj, expected_pattern):
    r = run(content(obj))
    assert r["state"] == "fail"
    assert patterns(r) == ["report_missing_field"]
    assert r["evidence"]["extra"]["classification_source"] == "case:missing_fields"
    missing = [d["field"] for d in r["evidence"]["extra"]["diffs"] if d["missing"]]
    assert missing == [expected_pattern]


def test_missing_both_fields_deduplicates_pattern():
    r = run(content({}))
    assert r["state"] == "fail"
    assert patterns(r) == ["report_missing_field"]  # 两条 case 命中，同 pattern 去重
    assert len(r["evidence"]["extra"]["diffs"]) == 2


@pytest.mark.parametrize("bad", ["not json", "", "[1, 2]", '"a string"', "42", "null"])
def test_invalid_json_is_treated_as_all_fields_missing(bad):
    """假设①：内容不是合法 JSON 对象 → 归入既有 report_missing_field，不扩词表。"""
    r = run(bad)
    assert r["state"] == "fail"
    assert patterns(r) == ["report_missing_field"]
    assert "假设①" in r["note"]
    assert r["evidence"]["observed"] is None


def test_diffs_shape_matches_handwritten_baseline():
    hand = next(a for a in raw("raw-fake-retry")["assertion_results"]
                if a["id"] == "R2_report_correct")
    r = run(raw("raw-fake-retry")["artifacts"][TARGET])
    assert r["evidence"]["extra"]["diffs"] == hand["evidence"]["extra"]["diffs"]


def test_pass_evidence_records_tolerance():
    r = run(content())
    assert r["evidence"]["extra"] == {"tolerance": {"total_amount": 0.01}}
    assert "diffs" not in r["evidence"]["extra"]


def test_multiple_patterns_are_collected_in_case_order():
    """与 R3/R5 的「首命中即止」不同，R2 收集全部命中（手写 fake-retry 有两个）。"""
    r = run(raw("raw-fake-retry")["artifacts"][TARGET])
    assert r["state"] == "fail"
    assert patterns(r) == ["wrong_valid_count", "wrong_total_amount"]
    hand = next(a for a in raw("raw-fake-retry")["assertion_results"]
                if a["id"] == "R2_report_correct")
    assert sorted(patterns(r)) == sorted(hand["error_patterns"])  # 顺序差异见 §18 假设②


def test_classification_source_is_precise():
    r = run(raw("raw-fake-retry")["artifacts"][TARGET])
    source = r["evidence"]["extra"]["classification_source"]
    assert source == "case:total_amount_mismatch,valid_count_mismatch"
    assert source != "case"  # TD-004：低粒度来源不被接受


def test_classification_source_for_mixed_missing_and_wrong():
    """缺一个字段 + 另一个字段算错：两条 case 都命中，来源必须列出两个键名。"""
    r = run(content({"total_amount": 1.0}))
    assert patterns(r) == ["report_missing_field", "wrong_total_amount"]
    assert r["evidence"]["extra"]["classification_source"] == (
        "case:missing_fields,total_amount_mismatch")


# ---------------------------------------------------------------------------
# 四态：依赖折叠 + 产物数据不可信
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["fail", "unscored", "blocked"])
def test_blocked_when_dependency_not_pass(state):
    r = run(content(), dep_state=state)
    assert r["state"] == "blocked"
    assert r["reason_code"] == "dependency_blocked"
    assert r["evidence"]["extra"]["blocked_by"] == [R1]
    assert not patterns(r)


def test_blocked_even_when_artifact_looks_perfect():
    """前置未过就不看内容，避免「R1 失败但 R2 满分」的假基线。"""
    assert run(content(), dep_state="fail")["evidence"]["observed"] is None


def test_missing_dependency_results_raises_instead_of_guessing():
    with pytest.raises(ae.EvaluatorError, match="必须提供 dependency_results"):
        run(content(), dependency_results=None)


@pytest.mark.parametrize("bad", [None, "pass", {}, {"state": 1}])
def test_dependency_entry_must_be_a_result_dict(bad):
    r = run(content(), dependency_results={R1: bad})
    assert r["state"] == "blocked"
    assert r["reason_code"] == "dependency_blocked"


@pytest.mark.parametrize("bad_artifacts", [None, [], "text", 0])
def test_artifacts_container_invalid_is_unscored(bad_artifacts):
    r = run(artifacts=bad_artifacts)
    assert r["state"] == "unscored"
    assert r["reason_code"] == "artifact_invalid"
    assert not patterns(r)


def test_target_missing_but_r1_passed_is_evidence_conflict():
    r = run(_MISSING)
    assert r["state"] == "unscored"
    assert r["reason_code"] == "artifact_invalid"


@pytest.mark.parametrize("bad", [123, [], None, {"a": 1}, b"raw"])
def test_artifact_value_not_string_is_unscored(bad):
    r = run(bad)
    assert r["state"] == "unscored"
    assert r["reason_code"] == "artifact_invalid"


def test_unscored_does_not_attribute_to_mastery():
    computed = run(artifacts=None)
    data = copy.deepcopy(raw("raw-perfect"))
    data["assertion_results"] = [
        computed if a["id"] == "R2_report_correct" else a
        for a in data["assertion_results"]
    ]
    report = dr.build_report(TASK, data)  # 组装器不拦
    r2 = next(a for a in report["assertions"] if a["id"] == "R2_report_correct")
    assert r2["state"] == "unscored" and r2["error_patterns"] == []
    assert report["attribution_policy"]["unscored"]["counts_toward_mastery"] is False


def test_artifact_invalid_reason_code_is_in_dto():
    """TD-008 在 R2 同样销项：红灯证据**反转成正向锁，函数保留不删除**（§22.6 A 类）。"""
    computed = run(artifacts=None)
    assert computed["state"] == "unscored"
    assert computed["reason_code"] == "artifact_invalid"
    schema = dr.load_schema()
    allowed = schema["properties"]["assertions"]["items"]["properties"]["reason_code"]["enum"]
    assert "artifact_invalid" in allowed
    data = copy.deepcopy(raw("raw-perfect"))
    data["assertion_results"] = [
        computed if a["id"] == "R2_report_correct" else a
        for a in data["assertion_results"]
    ]
    report = dr.build_report(TASK, data)
    dr.validate_report(report)  # 不再抛错：schema 已收录
    r2 = next(a for a in report["assertions"] if a["id"] == "R2_report_correct")
    assert r2["state"] == "unscored" and r2["error_patterns"] == []
    assert r2["reason_code"] == "artifact_invalid"


# ---------------------------------------------------------------------------
# 坏 spec 在接触任何产物之前抛（TD-003 同款纪律）
# ---------------------------------------------------------------------------


def _bad_specs():
    cases = []

    def add(mutate, label):
        cases.append(pytest.param(mutate, id=label))

    add(lambda s: s.update(type="file_exists"), "type 不符")
    add(lambda s: s.update(target=""), "target 空")
    add(lambda s: s.update(target=None), "target 非字符串")
    add(lambda s: s.update(compare="exact"), "compare 非 subset")
    add(lambda s: s.pop("compare"), "compare 缺失")
    add(lambda s: s.update(expected={}), "expected 空")
    add(lambda s: s.update(expected=[1, 2]), "expected 非对象")
    add(lambda s: s.update(tolerance=[]), "tolerance 非对象")
    add(lambda s: s.update(tolerance={"typo": 0.01}), "tolerance 键不在 expected")
    add(lambda s: s.update(tolerance={"total_amount": 0}), "容差为 0")
    add(lambda s: s.update(tolerance={"total_amount": -0.01}), "容差为负")
    add(lambda s: s.update(tolerance={"total_amount": True}), "容差是 bool")
    add(lambda s: s.update(tolerance={"total_amount": "0.01"}), "容差是字符串")
    add(lambda s: s.update(tolerance={"total_amount": math.nan}), "容差是 NaN")
    add(lambda s: s.update(tolerance={"total_amount": math.inf}), "容差是 inf")
    add(lambda s: s.update(error_pattern_detection={"cases": []}), "cases 空")
    add(lambda s: s.pop("error_pattern_detection"), "cases 缺失")
    add(lambda s: s["error_pattern_detection"]["cases"].insert(0, {}), "空 match catch-all")
    add(lambda s: s["error_pattern_detection"]["cases"][0].update(match={"typo": True}),
        "未知 match 键")
    add(lambda s: s["error_pattern_detection"]["cases"][0].update(
        match={"missing_fields": "valid_count"}), "missing_fields 非数组")
    add(lambda s: s["error_pattern_detection"]["cases"][0].update(
        match={"missing_fields": []}), "missing_fields 空数组")
    add(lambda s: s["error_pattern_detection"]["cases"][0].update(
        match={"missing_fields": ["typo"]}), "missing_fields 含非 expected 键")
    add(lambda s: s["error_pattern_detection"]["cases"][2].update(
        match={"valid_count_mismatch": False}), "mismatch 写成 false")
    add(lambda s: s["error_pattern_detection"]["cases"][2].update(
        match={"valid_count_mismatch": 1}), "mismatch 写成 1")
    add(lambda s: s["error_pattern_detection"]["cases"][0].update(pattern="no_retry"),
        "pattern 未声明")
    add(lambda s: s["error_pattern_detection"].update(default="no_retry"), "default 未声明")
    add(lambda s: s["error_pattern_detection"]["cases"].insert(0, []), "case 非对象")
    return cases


@pytest.mark.parametrize("mutate", _bad_specs())
def test_bad_spec_fails_before_reading_artifacts(mutate):
    s = spec()
    mutate(s)
    with pytest.raises(ae.EvaluatorError):
        ae.evaluate_json_field_equals(s, {TARGET: content()}, dependency_results=deps())


def test_load_r2_spec_validates_at_load_time(tmp_path):
    t = task()
    r2 = next(r for r in t["rubric_credit"] if r["id"] == "R2_report_correct")
    r2["error_pattern_detection"]["cases"].insert(0, {"match": {}, "pattern": "no_output_file"})
    p = tmp_path / "task.yaml"
    p.write_text(yaml.safe_dump(t, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ae.EvaluatorError, match="catch-all"):
        ae.load_r2_spec(p)


# ---------------------------------------------------------------------------
# 分发入口
# ---------------------------------------------------------------------------


def test_dispatch_routes_r2():
    r = ae.evaluate_assertion(spec(), None, artifacts={TARGET: content()},
                              dependency_results=deps())
    assert r["id"] == "R2_report_correct" and r["state"] == "pass"


def test_supported_types_match_current_dispatch():
    assert set(ae.SUPPORTED_TYPES) == {
        "trace_sequence", "trace_final_status", "file_exists",
        "json_field_equals", "trace_param_schema_valid"}


def test_unimplemented_type_still_raises():
    # S3-3-5 起 R4 已实现，用虚构 type 锁「未知类型拒绝伪造结果」。
    with pytest.raises(NotImplementedError, match="不要伪造结果"):
        ae.evaluate_assertion({"id": "X", "type": "future_assertion_type"}, None)


def test_r3_entry_rejects_r2_spec():
    with pytest.raises(NotImplementedError, match="只实现 trace_sequence"):
        ae.evaluate(spec(), None)


def test_executor_rejects_wrong_type_even_from_direct_call():
    s = spec()
    s["type"] = "future_assertion_type"
    with pytest.raises(ae.EvaluatorError, match="只接受 json_field_equals"):
        ae.evaluate_json_field_equals(s, {TARGET: content()}, dependency_results=deps())


# ---------------------------------------------------------------------------
# 产物是独立证据：绝不从 trace 自述推断
# ---------------------------------------------------------------------------


def test_trace_write_report_content_is_ignored():
    fake_trace = {"tool_calls": [
        {"call_index": 1, "name": "write_report",
         "params": {"path": TARGET, "content": content()}, "status": "success"},
    ]}
    r = ae.evaluate_assertion(spec(), fake_trace, artifacts={TARGET: "{}"},
                              dependency_results=deps())
    assert r["state"] == "fail"
    assert patterns(r) == ["report_missing_field"]


def test_no_trace_fixture_r2_is_not_rewritten():
    """R2 的 type 不是 trace_*，build_report 的强制改写与它无关（同 TD-007 口径）。"""
    data = raw("raw-no-trace")
    computed = ae.evaluate_assertion(spec(), data["trace"], artifacts=data["artifacts"],
                                     dependency_results=deps())
    assert computed["state"] == "pass"
    report = next(a for a in tp.run_fixture("raw-no-trace", TASK)["assertions"]
                  if a["id"] == "R2_report_correct")
    assert report["state"] == "pass"


# ---------------------------------------------------------------------------
# 完成判据：五夹具 R2 state 与最终报告基线一致
# ---------------------------------------------------------------------------


def _computed_r2(name: str) -> dict:
    data = raw(name)
    r1 = ae.evaluate_assertion(ae.load_r1_spec(), data["trace"], artifacts=data["artifacts"])
    return ae.evaluate_assertion(spec(), data["trace"], artifacts=data["artifacts"],
                                 dependency_results={r1["id"]: r1})


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_r2_matches_report_baseline(name):
    data = raw(name)
    computed = _computed_r2(name)
    report = next(a for a in tp.run_fixture(name, TASK)["assertions"]
                  if a["id"] == "R2_report_correct")
    handwritten = next(a for a in data["assertion_results"]
                       if a["id"] == "R2_report_correct")
    assert computed["state"] == report["state"] == handwritten["state"], name


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_r2_output_can_be_fed_to_build_report(name):
    data = copy.deepcopy(raw(name))
    before = tp.run_fixture(name, TASK)["score"]["ratio"]
    computed = _computed_r2(name)
    data["assertion_results"] = [
        computed if a["id"] == "R2_report_correct" else a
        for a in data["assertion_results"]
    ]
    report = dr.build_report(TASK, data)
    dr.validate_report(report)
    r2 = next(a for a in report["assertions"] if a["id"] == "R2_report_correct")
    assert r2["state"] == computed["state"]
    assert sorted(r2["error_patterns"]) == sorted(computed.get("error_patterns", []))
    assert report["score"]["ratio"] == before


# ---------------------------------------------------------------------------
# S3-3-6 / P-C=A / TD-015：三类坍缩必须能被结构化 evidence 分开
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad,kind", [
    ("not json", "syntax_error"),
    ("", "empty_content"),
    ("   \n  ", "empty_content"),
    ("[1, 2]", "not_json_object"),
    ("42", "not_json_object"),
    ("null", "not_json_object"),
])
def test_bad_json_kinds_are_structurally_distinguishable(bad, kind):
    """§22.4 F8：坍缩的是语法错 / 合法非对象 / 空白**三类**，不是两类。

    判定口径不变（仍 `fail/report_missing_field`、仍不改词表 / DTO），
    只在 evidence.extra 补 `parse_error` / `raw_excerpt` 让下游可判定。
    """
    r = run(bad)
    assert r["state"] == "fail" and patterns(r) == ["report_missing_field"]
    assert r["evidence"]["observed"] is None
    extra = r["evidence"]["extra"]
    assert extra["parse_error"]["kind"] == kind, bad
    assert extra["parse_error"]["detail"]
    assert "excerpt" in extra["raw_excerpt"]


def test_bad_json_vs_missing_field_is_distinguishable():
    """§22.3 订正：`observed: null` vs `{}` 本就是既有区分器，锁住两者都还在。"""
    bad = run("not json")
    missing = run('{"total_amount": 1837.99}')
    assert bad["evidence"]["observed"] is None
    assert missing["evidence"]["observed"] == {"total_amount": 1837.99}
    assert "parse_error" in bad["evidence"]["extra"]
    assert "parse_error" not in missing["evidence"]["extra"]


def test_raw_excerpt_is_truncated_and_escaped():
    """脱敏：只留 120 字符片段 + 长度与截断标志，不把整份产物灌进 evidence。"""
    long = "X" * 300 + "\nSECRET_TAIL"
    ex = run(long)["evidence"]["extra"]["raw_excerpt"]
    assert ex["truncated"] is True
    assert ex["content_chars"] == len(long)
    assert "SECRET_TAIL" not in ex["excerpt"]
    assert len(ex["excerpt"]) <= 120
    r2 = run("line1\nline2")
    assert "\\n" in r2["evidence"]["extra"]["raw_excerpt"]["excerpt"]
