"""S3-3-5：R4 trace_param_schema_valid 执行器的约束测试。

守的是八件结构性事实：
1. 判定语义以 docs/executor-semantics.md §2 为准：required / type / range /
   additional_properties 四项同时生效，叠加 mode=all_calls_must_be_valid；
2. 工具声明从 expected.schema_ref 指向的任务 YAML 表读取，代码不写死工具名 /
   参数名 / 数值边界（用注入 schema 反证）；
3. 分类按 cases 顺序首命中即止，命中来源写精确键名；多类违规时明细仍全量保留；
4. bool 不当 integer、类型不符时不做 range 比较（避免 Python 3 的 str/int 比较异常）；
5. trace 不可信一律 unscored，绝不把「上报数据坏」记成「能力不足」；
6. 坏配置在接触任何 trace 之前抛（加载期 + 判定期各一道）；
7. 五夹具 R4 state 与**最终报告**基线一致；raw-no-trace 手写 pass 属 S2 假基线
   （TD-011），按 §16 已定判据不改夹具；
8. 现算结果能直接喂 build_report() / validate_report()，四态与归因纪律不变。
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
from arena import trace_pipeline as tp  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
FIXTURES = ROOT / "fixtures"
TASK = dr.load_task(TASK_PATH)
RID = "R4_no_param_hallucination"

FETCH = "fetch_orders"
WRITE = "write_report"
GOOD_PARAMS = {"offset": 0, "limit": 10}
GOOD = ((FETCH, GOOD_PARAMS),)
_MISSING = object()


def spec() -> dict:
    return copy.deepcopy(ae.load_r4_spec())


def schemas() -> dict:
    return ae.load_tool_schemas(spec()["expected"]["schema_ref"])


def raw(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def calls(*items) -> list[dict]:
    """(工具名, params) -> 合规上报的 tool_calls（call_index 连续、status 合法）。"""
    return [{"call_index": i, "name": name, "params": params, "status": "success"}
            for i, (name, params) in enumerate(items, 1)]


def tr(items=_MISSING, **overrides) -> dict:
    base = {"agent_id": "test-agent", "final_status": "completed",
            "tool_calls": calls(*GOOD) if items is _MISSING else items}
    base.update(overrides)
    return base


def run(items=_MISSING, s: dict | None = None, sch: dict | None = None,
        trace=_MISSING, vocab=None) -> dict:
    t = tr(items) if trace is _MISSING else trace
    return ae.evaluate_assertion(s or spec(), t, vocab, schemas=sch)


def violations(r: dict) -> list[dict]:
    observed = r["evidence"]["observed"]
    assert isinstance(observed, dict), f"observed 不是对象：{observed!r}"
    return observed["violations"]


def kinds(r: dict) -> set[str]:
    return {v["kind"] for v in violations(r)}


def patterns(r: dict) -> list[str]:
    return r.get("error_patterns") or []


def source(r: dict) -> str:
    return r["evidence"]["extra"]["classification_source"]


# ---------------------------------------------------------------------------
# 1. 完成判据：五夹具 R4 state 与最终报告基线一致
# ---------------------------------------------------------------------------


def test_r4_spec_comes_from_task_yaml():
    s = spec()
    assert s["id"] == RID and s["type"] == "trace_param_schema_valid"
    assert s["target"] == "tool_calls"
    assert s["expected"]["schema_ref"] in TASK
    assert s["expected"]["check_schema"] is True
    assert s["expected"]["mode"] == "all_calls_must_be_valid"
    assert s["depends_on"] == []


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_five_fixtures_r4_matches_final_report(name):
    """§12.7 完成判据。判据取最终报告 state（§16 已定口径），不取手写夹具。"""
    data = raw(name)
    computed = ae.evaluate_assertion(spec(), data.get("trace"))
    report = tp.run_fixture(name)
    reported = next(a for a in report["assertions"] if a["id"] == RID)
    assert computed["state"] == reported["state"], name


def test_no_trace_handwritten_pass_is_stale_baseline():
    """TD-011：手写 pass 与最终报告 / 现算 unscored 不符，S3-3-6 处置，本步不改夹具。"""
    data = raw("raw-no-trace")
    hand = next(a for a in data["assertion_results"] if a["id"] == RID)
    computed = ae.evaluate_assertion(spec(), data.get("trace"))
    report = next(a for a in tp.run_fixture("raw-no-trace")["assertions"]
                  if a["id"] == RID)
    assert hand["state"] == "pass"
    assert computed["state"] == report["state"] == "unscored"
    assert computed["reason_code"] == report["reason_code"] == "trace_not_reported"


@pytest.mark.parametrize("name,ratio", [("raw-empty", 0.0), ("raw-no-file", 0.0),
                                        ("raw-fake-retry", 0.25), ("raw-no-trace", 0.5),
                                        ("raw-perfect", 1.0)])
def test_score_sequence_unchanged(name, ratio):
    """本步不接全链路，分数序必须纹丝不动。"""
    assert tp.run_fixture(name)["score"]["ratio"] == ratio


def test_zero_calls_is_unscored_by_design():
    """反转【现状】→ 正向：空集不再是平凡 pass，R4 无目标调用可判（F5 / TD-019b）。"""
    r = run(items=[])
    assert r["state"] == "unscored" and r["reason_code"] == "trace_invalid"
    assert r["evidence"]["observed"]["checked_calls"] == 0
    assert r["evidence"]["observed"]["target_call_count"] == 0


def test_perfect_call_mix_passes():
    r = run(items=calls((FETCH, {"offset": 0, "limit": 10}),
                        (FETCH, {"offset": 10, "limit": 10}),
                        (FETCH, {"offset": 20, "limit": 10}),
                        (WRITE, {"path": "/workspace/report.json", "content": "{}"})))
    assert r["state"] == "pass"
    assert r["evidence"]["observed"]["checked_calls"] == 4
    assert "error_patterns" not in r


# ---------------------------------------------------------------------------
# 2. 四项校验：各自命中对应 pattern 与精确来源
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("params,pattern,kind", [
    ({"offset": 0, "limit": 10, "bogus": 1}, "param_out_of_schema", "extra_keys"),
    ({"offset": 0}, "param_missing_required", "missing_required"),
    ({"offset": 0, "limit": "10"}, "param_type_mismatch", "type_mismatch"),
    ({"offset": 0, "limit": 20}, "param_out_of_range", "out_of_range"),
])
def test_four_checks_each_fail_with_declared_pattern(params, pattern, kind):
    r = run(items=calls((FETCH, params)))
    assert r["state"] == "fail"
    assert patterns(r) == [pattern]
    assert kind in kinds(r)
    assert source(r) == "case:" + {
        "extra_keys": "extra_keys_against",
        "missing_required": "missing_required_against",
        "type_mismatch": "type_mismatch_against",
        "out_of_range": "out_of_range_against",
    }[kind]


def test_every_declared_pattern_is_in_vocabulary():
    vocab = dr.load_vocabulary()
    assert set(spec()["error_patterns"]).issubset(vocab)


def test_fail_patterns_are_all_declared():
    declared = set(spec()["error_patterns"])
    for params in ({"offset": 0, "limit": 10, "x": 1}, {"offset": 0},
                   {"offset": 0, "limit": "10"}, {"offset": 0, "limit": 20}):
        assert set(patterns(run(items=calls((FETCH, params))))).issubset(declared)


def test_violation_record_has_call_index_tool_and_param():
    r = run(items=calls((FETCH, {"offset": 0, "limit": 10}),
                        (FETCH, {"offset": 0, "limit": 20, "x": 1})))
    rec = violations(r)
    assert all("call_index" in v and "tool" in v and "param" in v and "kind" in v
               for v in rec)
    assert [v["call_index"] for v in rec] == [2, 2]


def test_checked_calls_counts_all_calls_even_when_only_one_violates():
    r = run(items=calls((FETCH, GOOD_PARAMS), (FETCH, {"offset": 0, "limit": 20})))
    assert r["evidence"]["observed"]["checked_calls"] == 2
    assert len(violations(r)) == 1


# ---------------------------------------------------------------------------
# 3. 边界与陷阱
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("params", [
    {"offset": 0, "limit": 1}, {"offset": 0, "limit": 10},
    {"offset": 0, "limit": 5}, {"offset": 999, "limit": 10},
])
def test_in_range_values_pass(params):
    assert run(items=calls((FETCH, params)))["state"] == "pass"


@pytest.mark.parametrize("params,bound", [
    ({"offset": -1, "limit": 10}, "minimum"),
    ({"offset": 0, "limit": 0}, "minimum"),
    ({"offset": 0, "limit": 11}, "maximum"),
])
def test_out_of_range_values_fail(params, bound):
    r = run(items=calls((FETCH, params)))
    assert r["state"] == "fail" and patterns(r) == ["param_out_of_range"]
    assert "out_of_range" in kinds(r)


@pytest.mark.parametrize("value", [True, False])
def test_bool_is_not_accepted_as_integer(value):
    """Python 里 bool 是 int 子类；True 若被当 1 会静默通过 minimum 校验。"""
    r = run(items=calls((FETCH, {"offset": 0, "limit": value})))
    assert r["state"] == "fail"
    assert patterns(r) == ["param_type_mismatch"]
    assert kinds(r) == {"type_mismatch"}


@pytest.mark.parametrize("value", ["20", None, [], 1.5])
def test_type_mismatch_short_circuits_range_check(value):
    """类型不符不能继续拿去和 minimum/maximum 比（str 与 int 比较会抛）。"""
    r = run(items=calls((FETCH, {"offset": 0, "limit": value})))
    assert kinds(r) == {"type_mismatch"}
    assert patterns(r) == ["param_type_mismatch"]


def test_string_params_are_checked_for_tools_that_declare_them():
    r = run(items=calls((WRITE, {"path": 123, "content": "{}"})))
    assert r["state"] == "fail"
    assert patterns(r) == ["param_type_mismatch"]


def test_write_report_missing_required_content():
    r = run(items=calls((WRITE, {"path": "/workspace/report.json"})))
    assert r["state"] == "fail"
    assert patterns(r) == ["param_missing_required"]


def test_write_report_extra_key_is_violation():
    r = run(items=calls((WRITE, {"path": "p", "content": "c", "mode": "w"})))
    assert patterns(r) == ["param_out_of_schema"]
    assert {v["param"] for v in violations(r)} == {"mode"}


def test_multiple_extra_keys_are_all_recorded_and_sorted():
    r = run(items=calls((FETCH, {"offset": 0, "limit": 10, "z": 1, "a": 2})))
    assert [v["param"] for v in violations(r)] == ["a", "z"]


# ---------------------------------------------------------------------------
# 4. 多类违规：cases 顺序首命中即止，明细全量保留
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("params,top", [
    ({"offset": 0, "limit": 20, "bogus": 1}, "param_out_of_schema"),
    ({"limit": 20}, "param_missing_required"),
    # 类型错 + 缺必填：两类违规，cases 顺序里 missing_required 优先于 type_mismatch。
    # 注意 {"offset":0,"limit":"20"} 只有一类（类型错后不再做 range），不算多类。
    ({"limit": "20"}, "param_missing_required"),
])
def test_mixed_violations_report_highest_priority_case(params, top):
    r = run(items=calls((FETCH, params)))
    assert patterns(r) == [top]
    assert len(kinds(r)) >= 2
    assert len(violations(r)) >= 2
    assert source(r).startswith("case:")


def test_case_priority_follows_yaml_order_not_violation_order():
    """把 cases 顺序倒过来，最优先 pattern 应跟着变（规则来自 YAML，不是代码常量）。"""
    s = spec()
    s["error_pattern_detection"]["cases"].reverse()
    r = run(items=calls((FETCH, {"offset": 0, "limit": 20, "bogus": 1})), s=s)
    assert patterns(r) == ["param_out_of_range"]


def test_single_call_can_produce_both_extra_and_missing():
    r = run(items=calls((FETCH, {"offset": 0, "bogus": 1})))
    assert kinds(r) == {"extra_keys", "missing_required"}
    assert patterns(r) == ["param_out_of_schema"]


def test_failing_call_after_passing_calls_is_caught():
    r = run(items=calls((FETCH, GOOD_PARAMS), (FETCH, GOOD_PARAMS),
                        (FETCH, {"offset": 0, "limit": 99})))
    assert r["state"] == "fail"
    assert violations(r)[0]["call_index"] == 3


# ---------------------------------------------------------------------------
# 5. YAML 派生：注入 schema 反证代码里没有写死任务常量
# ---------------------------------------------------------------------------


def test_injected_schema_changes_parameter_names():
    """参数名改成 cursor/size 后，旧的 offset/limit 反而变成 schema 外参数。"""
    alt = copy.deepcopy(schemas())
    alt[FETCH]["parameters"] = [
        {"name": "cursor", "type": "integer", "required": True, "minimum": 0},
        {"name": "size", "type": "integer", "required": True, "minimum": 1, "maximum": 3},
    ]
    ok = run(items=calls((FETCH, {"cursor": 0, "size": 3})), sch=alt)
    bad = run(items=calls((FETCH, {"cursor": 0, "size": 4})), sch=alt)
    old = run(items=calls((FETCH, GOOD_PARAMS)), sch=alt)
    assert ok["state"] == "pass"
    assert patterns(bad) == ["param_out_of_range"]
    assert "extra_keys" in kinds(old)


def test_additional_properties_true_allows_extra_keys():
    alt = copy.deepcopy(schemas())
    alt[FETCH]["additional_properties"] = True
    params = {"offset": 0, "limit": 10, "bogus": 1}
    assert run(items=calls((FETCH, params)), sch=alt)["state"] == "pass"
    assert patterns(run(items=calls((FETCH, params)))) == ["param_out_of_schema"]


def test_required_false_does_not_trigger_missing_required():
    alt = copy.deepcopy(schemas())
    for p in alt[FETCH]["parameters"]:
        p["required"] = False
    assert run(items=calls((FETCH, {})), sch=alt)["state"] == "pass"


def test_injected_bounds_change_range_result():
    alt = copy.deepcopy(schemas())
    for p in alt[FETCH]["parameters"]:
        if p["name"] == "limit":
            p["maximum"] = 2
    assert run(items=calls((FETCH, {"offset": 0, "limit": 2})), sch=alt)["state"] == "pass"
    assert patterns(run(items=calls((FETCH, {"offset": 0, "limit": 3})), sch=alt)) == [
        "param_out_of_range"]


def test_schema_ref_is_read_not_hardcoded():
    assert spec()["expected"]["schema_ref"] == "tools"
    assert set(ae.load_tool_schemas("tools")) == {FETCH, WRITE}
    with pytest.raises(ae.EvaluatorError, match="缺 'nope' 数组"):
        ae.load_tool_schemas("nope")


def test_injected_schemas_work_through_dispatch_entry():
    alt = copy.deepcopy(schemas())
    alt[FETCH]["additional_properties"] = True
    r = ae.evaluate_assertion(spec(), tr(calls((FETCH, {"offset": 0, "limit": 10,
                                                         "x": 1}))), schemas=alt)
    assert r["state"] == "pass"


# ---------------------------------------------------------------------------
# 6. unscored：数据不可信一律不归因
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trace,code", [
    (None, "trace_not_reported"),
    ("not-an-object", "trace_invalid"),
    ({"tool_calls": None}, "trace_invalid"),
    ({"tool_calls": "x"}, "trace_invalid"),
])
def test_unusable_trace_is_unscored(trace, code):
    r = run(trace=trace)
    assert r["state"] == "unscored" and r["reason_code"] == code
    assert patterns(r) == []


def test_call_index_discontinuity_is_unscored_not_fail():
    bad = calls((FETCH, GOOD_PARAMS))
    bad[0]["call_index"] = 7
    r = run(items=bad)
    assert r["state"] == "unscored" and r["reason_code"] == "trace_invalid"
    assert "call_sequence_problem" in r["evidence"]["observed"]


@pytest.mark.parametrize("field,value", [("status", "sucess"), ("params", None),
                                         ("call_index", True)])
def test_bad_call_shape_is_unscored(field, value):
    c = calls((FETCH, GOOD_PARAMS))
    c[0][field] = value
    r = run(items=c)
    assert r["state"] == "unscored" and r["reason_code"] == "trace_invalid"


def test_unknown_tool_name_is_unscored_by_assumption():
    """假设①：词表的 param_out_of_schema 专指参数名，不擅自扩到工具名。"""
    r = run(items=calls(("hallucinated_tool", {"offset": 0, "limit": 10})))
    assert r["state"] == "unscored" and r["reason_code"] == "trace_invalid"
    assert r["evidence"]["observed"]["unknown_tools"] == [
        {"call_index": 1, "name": "hallucinated_tool"}]


def test_non_string_tool_name_is_unscored_not_crash():
    c = calls((FETCH, GOOD_PARAMS))
    c[0]["name"] = {"weird": 1}
    r = run(items=c)
    assert r["state"] == "unscored"


def test_probe_projection_loss_is_unscored():
    """投影丢调用会让全称命题假 pass，必须判数据不可信。"""
    s = spec()
    s["error_pattern_detection"]["probe"] = "calls[?name=='nobody'].{name: name, params: params}"
    r = run(s=s)
    assert r["state"] == "unscored" and r["reason_code"] == "trace_invalid"


def test_unscored_never_emits_error_patterns():
    for t in (None, {"tool_calls": None}, tr(calls(("nope", {})))):
        r = run(trace=t)
        assert r["state"] == "unscored"
        assert "error_patterns" not in r


def test_unscored_reason_codes_are_in_dto_enum():
    """R4 只用既有码；对比 R1 的 artifact_invalid 缺口（TD-008）不扩大到这里。"""
    schema = json.loads((ROOT / "docs" / "diagnosis-report.schema.json").read_text(encoding="utf-8"))
    enum = schema["properties"]["assertions"]["items"]["properties"]["reason_code"]["enum"]
    assert {"trace_not_reported", "trace_invalid"}.issubset(set(enum))


# ---------------------------------------------------------------------------
# 7. 四态 / DTO 兼容：现算结果可直接喂 build_report()
# ---------------------------------------------------------------------------


def test_calculated_pass_feeds_build_report():
    data = raw("raw-perfect")
    data["assertion_results"] = [
        ae.evaluate_assertion(spec(), data["trace"]) if a["id"] == RID else a
        for a in data["assertion_results"]
    ]
    report = dr.build_report(TASK, data)
    dr.validate_report(report)
    r4 = next(a for a in report["assertions"] if a["id"] == RID)
    assert r4["state"] == "pass" and r4["error_patterns"] == []
    assert report["score"]["ratio"] == 1.0


def test_calculated_fail_feeds_build_report():
    """只验 R4 条目的 DTO 兼容性；其余断言仍是手写基线（S3-3-6 才切全链路）。"""
    data = raw("raw-perfect")
    bad_trace = copy.deepcopy(data["trace"])
    bad_trace["tool_calls"][0]["params"]["limit"] = 20
    data["trace"] = bad_trace
    data["assertion_results"] = [
        ae.evaluate_assertion(spec(), bad_trace) if a["id"] == RID else a
        for a in data["assertion_results"]
    ]
    report = dr.build_report(TASK, data)
    dr.validate_report(report)
    r4 = next(a for a in report["assertions"] if a["id"] == RID)
    assert r4["state"] == "fail"
    assert r4["error_patterns"] == ["param_out_of_range"]
    assert report["score"]["ratio"] == 0.75  # R4 权重 1，分母 4


def test_calculated_unscored_feeds_build_report():
    data = raw("raw-no-trace")
    data["assertion_results"] = [
        ae.evaluate_assertion(spec(), data.get("trace")) if a["id"] == RID else a
        for a in data["assertion_results"]
    ]
    report = dr.build_report(TASK, data)
    dr.validate_report(report)
    r4 = next(a for a in report["assertions"] if a["id"] == RID)
    assert r4["state"] == "unscored" and r4["reason_code"] == "trace_not_reported"
    assert report["attribution_policy"]["unscored"]["counts_toward_mastery"] is False


# ---------------------------------------------------------------------------
# 8. 坏 spec：加载期 + 判定期各一道
# ---------------------------------------------------------------------------


def _bad(mutator):
    s = spec()
    mutator(s)
    return s


@pytest.mark.parametrize("mutator,label", [
    (lambda s: s.update(type="json_field_equals"), "wrong type"),
    (lambda s: s.update(target=""), "empty target"),
    (lambda s: s.update(target=None), "null target"),
    (lambda s: s.update(expected=None), "no expected"),
    (lambda s: s["expected"].update(schema_ref=""), "empty schema_ref"),
    (lambda s: s["expected"].update(check_schema=False), "check_schema false"),
    (lambda s: s["expected"].update(mode="any_call_valid"), "wrong mode"),
    (lambda s: s["error_pattern_detection"].update(probe=""), "empty probe"),
    (lambda s: s["error_pattern_detection"].update(cases=[]), "no cases"),
    (lambda s: s["error_pattern_detection"].update(default="not_declared"), "bad default"),
    (lambda s: s["error_pattern_detection"]["cases"][0].update(match={}), "empty match"),
    (lambda s: s["error_pattern_detection"]["cases"][0].update(
        match={"unknown_against": "tools"}), "unknown key"),
    (lambda s: s["error_pattern_detection"]["cases"][0].update(
        match={"extra_keys_against": "other"}), "wrong schema_ref"),
    (lambda s: s["error_pattern_detection"]["cases"][0].update(
        match={"extra_keys_against": True}), "non-string want"),
    (lambda s: s["error_pattern_detection"]["cases"][0].update(
        pattern="not_declared"), "undeclared pattern"),
    (lambda s: s["error_pattern_detection"]["cases"].pop(), "missing kind case"),
])
def test_bad_spec_rejected_at_load_and_at_judge(mutator, label):
    s = _bad(mutator)
    with pytest.raises(ae.EvaluatorError):
        ae._validate_param_schema_spec(s)
    with pytest.raises(ae.EvaluatorError):
        run(s=s)


def test_bad_spec_fails_before_touching_bad_trace():
    """配置错优先于 trace 问题：不能用 unscored 把坏配置吞掉。"""
    s = _bad(lambda x: x["expected"].update(check_schema=False))
    with pytest.raises(ae.EvaluatorError):
        run(trace=None, s=s)


def test_direct_executor_rejects_wrong_type():
    s = spec()
    s["type"] = "future_assertion_type"
    with pytest.raises(ae.EvaluatorError, match="只接受 trace_param_schema_valid"):
        ae.evaluate_param_schema(s, tr())


def test_cases_must_cover_all_four_kinds():
    s = spec()
    cases = s["error_pattern_detection"]["cases"]
    covered = {k for c in cases for k in c["match"]}
    assert covered == set(ae._PARAM_KIND_BY_KEY)
    assert len(cases) == 4


# ---------------------------------------------------------------------------
# 9. tools 表结构校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mutator", [
    lambda a: a.__setitem__(FETCH, {}),
    lambda a: a[FETCH].__setitem__("parameters", []),
    lambda a: a[FETCH].__setitem__("additional_properties", "false"),
    lambda a: a[FETCH]["parameters"].append(a[FETCH]["parameters"][0]),
    lambda a: a[FETCH]["parameters"][0].update(type="number"),
    lambda a: a[FETCH]["parameters"][0].update(required=None),
    lambda a: a[FETCH]["parameters"][0].update(minimum="zero"),
    lambda a: a[FETCH]["parameters"][0].update(minimum=99, maximum=1),
])
def test_bad_tool_schema_rejected(mutator):
    alt = copy.deepcopy(schemas())
    mutator(alt)
    with pytest.raises(ae.EvaluatorError):
        ae._validate_tool_schemas(alt, RID)
    with pytest.raises(ae.EvaluatorError):
        run(sch=alt)


def test_empty_schema_table_rejected():
    with pytest.raises(ae.EvaluatorError):
        ae._validate_tool_schemas({}, RID)


def test_duplicate_tool_name_in_yaml_is_rejected(tmp_path):
    """真锁 loader 的重复工具名分支；不用文本计数那种脆弱的自指检查。"""
    payload = {"tools": [
        {"name": "dup", "additional_properties": False,
         "parameters": [{"name": "p", "type": "integer", "required": True}]},
        {"name": "dup", "additional_properties": False,
         "parameters": [{"name": "q", "type": "integer", "required": True}]},
    ]}
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ae.EvaluatorError, match="重复"):
        ae.load_tool_schemas("tools", path)


def test_tool_entry_without_name_is_rejected(tmp_path):
    payload = {"tools": [{"additional_properties": False,
                          "parameters": [{"name": "p", "type": "integer",
                                          "required": True}]}]}
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ae.EvaluatorError, match="缺合法 name"):
        ae.load_tool_schemas("tools", path)


def test_unsupported_param_type_raises_from_low_level_helper():
    with pytest.raises(ae.EvaluatorError, match="未支持的参数类型"):
        ae._param_type_ok(1, "number")


# ---------------------------------------------------------------------------
# 10. 分发路由
# ---------------------------------------------------------------------------


def test_dispatch_routes_r4():
    r = ae.evaluate_assertion(spec(), tr())
    assert r["id"] == RID and r["state"] == "pass"


def test_supported_types_match_current_dispatch():
    assert set(ae.SUPPORTED_TYPES) == {
        "trace_sequence", "trace_final_status", "file_exists",
        "json_field_equals", "trace_param_schema_valid"}


def test_unknown_type_still_refuses_to_fake_result():
    with pytest.raises(NotImplementedError, match="不要伪造结果"):
        ae.evaluate_assertion({"id": "X", "type": "future_assertion_type"}, tr())


def test_r3_entry_rejects_r4_spec():
    with pytest.raises(NotImplementedError, match="只实现 trace_sequence"):
        ae.evaluate(spec(), tr())


def test_other_executors_still_reject_r4_spec():
    for fn, args in ((ae.evaluate_file_exists, ({},)),
                     (ae.evaluate_final_status, (tr(),))):
        with pytest.raises(ae.EvaluatorError):
            fn(spec(), *args)


def test_r4_does_not_need_dependency_results():
    """depends_on=[]：调用方不传 dependency_results 也必须能判定。"""
    assert ae.evaluate_assertion(spec(), tr())["state"] == "pass"
