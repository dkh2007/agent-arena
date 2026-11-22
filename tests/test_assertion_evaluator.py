"""S3-1：R3 断言执行器的约束测试。

守的不是"四个样例判对了"，而是三件结构性事实：
1. 判定表达式与分类规则都从任务 YAML 派生，代码里没有第二份任务常量；
2. trace 缺失判 unscored（不判 0、不归因），符合任务 YAML 的归因纪律；
3. 执行器输出能直接塞进 raw.assertion_results，被 build_report() 无改组装。
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


def spec() -> dict:
    return copy.deepcopy(ae.load_r3_spec())


def call(offset: int, status: str = "success", http_status: int = 200) -> dict:
    return {
        "name": "fetch_orders",
        "params": {"offset": offset, "limit": 10},
        "status": status,
        "http_status": http_status,
    }


def trace(calls: list[dict]) -> dict:
    """补平台侧合规编号，模拟正常上报。

    要测乱序 / 缺号 / 非法 call_index，请直接构造 {"tool_calls": ...}，
    不要用这个 helper——它会自动把编号改成合规的。
    """
    return {"tool_calls": [{**c, "call_index": i + 1} for i, c in enumerate(calls)]}


TRACES = {
    "真重试": [call(0), call(10, "error", 500), call(10)],
    "真重试+穿插": [call(0), call(10, "error", 500),
                {"name": "write_report", "params": {"path": "/workspace/report.json"},
                 "status": "success", "http_status": 200}, call(10)],
    "假重试": [call(0), call(10, "error", 500), call(0)],
    "无重试": [call(0), call(10, "error", 500)],
    "重试耗尽": [call(0), call(10, "error", 500), call(10, "error", 500)],
}
EXPECTED = {
    "真重试": ("pass", None),
    "真重试+穿插": ("pass", None),
    "假重试": ("fail", "retry_wrong_params"),
    "无重试": ("fail", "no_retry"),
    "重试耗尽": ("fail", "retry_exhausted"),
}


@pytest.mark.parametrize("name", list(TRACES))
def test_r3_judgement_and_classification(name):
    """spike 的四类 + 重试耗尽，判定与分类必须同时对。"""
    r = ae.evaluate(spec(), trace(TRACES[name]))
    want_state, want_pattern = EXPECTED[name]
    assert r["state"] == want_state, name
    got = (r.get("error_patterns") or [None])[0]
    assert got == want_pattern, name
    if want_state == "fail":
        assert r["evidence"]["extra"]["classification_source"].startswith("case:"), \
            f"{name} 应命中明写规则，而不是 default 兜底"


def test_interleaving_is_handled_by_filtering():
    """穿插一条无关调用不应改变判定——这是 JMESPath 过滤法的实测结论。"""
    plain = ae.evaluate(spec(), trace(TRACES["真重试"]))
    mixed = ae.evaluate(spec(), trace(TRACES["真重试+穿插"]))
    assert plain["state"] == mixed["state"] == "pass"


def test_filter_expr_derives_from_yaml():
    """把 YAML 的故障点改成 20，判定必须跟着变（防硬编码 10）。

    B-31：覆盖点不再写进 JMESPath（jmespath 没有算术），而是由 `_filter_expr`
    作为第二个返回值交给 Python 侧。所以这里断言的是那个返回值，不是字符串。
    """
    s = spec()
    for step in s["expected"]["calls"]:
        step["params_match"] = {"offset_covers": 20}
    expr, cover = ae._filter_expr(s)
    assert cover == 20
    assert "10" not in expr and "20" not in expr, "覆盖点不该被烤进过滤表达式"
    # 判定也要跟着变：覆盖第 10 条的 trace 不再满足「覆盖第 20 条」的契约
    assert ae.evaluate(s, trace(TRACES["真重试"]))["state"] == "fail"


@pytest.mark.parametrize("limit,page_start", [(10, 10), (5, 10), (4, 8), (3, 9), (2, 10), (1, 10)])
def test_b31_any_legal_page_size_can_pass_r3(limit, page_start):
    """B-31：用任何合法页大小分页、正确重试，R3 都必须判 pass。

    `page_start` 是覆盖第 10 条的页起点（=10 时从 10 开始，limit=3 时是 9，
    limit=4 时是 8）。旧判据要求 offset 恰为 10，只有 limit 能整除 10 才过得了——
    等于把「怎么分页」偷偷写进了评分标准。
    """
    calls = [
        {"name": "fetch_orders", "params": {"offset": o, "limit": limit},
         "status": "success", "http_status": 200}
        for o in range(0, page_start, limit)
    ]
    calls.append({"name": "fetch_orders", "params": {"offset": page_start, "limit": limit},
                  "status": "error", "http_status": 500})
    calls.append({"name": "fetch_orders", "params": {"offset": page_start, "limit": limit},
                  "status": "success", "http_status": 200})
    r = ae.evaluate(spec(), trace(calls))
    assert r["state"] == "pass", (
        f"limit={limit}、在 offset={page_start} 失败后同参重试，"
        f"却判 {r['state']} / {r.get('error_patterns')}"
    )


def test_http_status_constraint_is_enforced():
    """YAML 首步明写 http_status=500；换成 404 的"失败后重试"不能算通过。"""
    s = spec()
    bad = [call(0), call(10, "error", 404), call(10)]
    assert ae.evaluate(s, trace(bad))["state"] == "fail"


def test_classification_rules_come_from_yaml():
    """改 cases 的 pattern，分类结果必须跟着变（YAML 是唯一真相源）。

    B-31 后 cases 全换成相对谓词，第一条（no_tool_calls）与「假重试」无关，
    所以改为掐住真正会命中的那条规则：把 fault_then_moved_on 的 pattern 换掉，
    同一份 trace 的分类必须随之改变。
    """
    s = spec()
    hit = next(c for c in s["error_pattern_detection"]["cases"]
               if "fault_then_moved_on" in (c.get("match") or {}))
    assert hit["pattern"] == "retry_wrong_params"      # 先确认改的是对的那条
    hit["pattern"] = "retry_exhausted"                 # 换成另一个已声明 pattern
    r = ae.evaluate(s, trace(TRACES["假重试"]))
    assert r["error_patterns"] == ["retry_exhausted"]


def test_unknown_predicate_fails_loud():
    """词表新增了没实现的谓词时，宁可报错也不能静默跳过、走 default。"""
    s = spec()
    s["error_pattern_detection"]["cases"][0]["match"]["offsets_contains"] = 10
    with pytest.raises(ae.EvaluatorError, match="未知的分类谓词"):
        ae.evaluate(s, trace(TRACES["假重试"]))
    # TD-003：非法谓词与轨迹无关，trace 没上报时也必须在校验期被拒
    with pytest.raises(ae.EvaluatorError, match="未知的分类谓词"):
        ae.evaluate(s, None)


@pytest.mark.parametrize("bad", [None, {}, {"tool_calls": None}, {"tool_calls": "oops"}])
def test_unusable_trace_is_unscored_not_zero(bad):
    """数据缺失 ≠ 能力不足：判 unscored，且不得产出错误模式。"""
    r = ae.evaluate(spec(), bad)
    assert r["state"] == "unscored"
    assert r["reason_code"] in ("trace_not_reported", "trace_invalid")
    assert not r.get("error_patterns")


# --------------------------------------------------------------------------
# round-8 ①：call_index 乱序反例与前置校验
# --------------------------------------------------------------------------

SHUFFLED = [
    {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10},
     "status": "error", "http_status": 500},
    {"call_index": 1, "name": "fetch_orders", "params": {"offset": 10, "limit": 10},
     "status": "success", "http_status": 200},
]


def test_shuffled_call_index_is_unscored_not_pass():
    """外部评审 ① 的原样反例：乱序上报绝不能判 pass，也不能重排后判 fail。"""
    r = ae.evaluate(spec(), {"tool_calls": copy.deepcopy(SHUFFLED)})
    assert r["state"] == "unscored"
    assert r["reason_code"] == "trace_invalid"
    assert not r.get("error_patterns")
    assert "call_sequence_problem" in r["evidence"]["observed"]


def test_why_we_do_not_sort_by_call_index():
    """驳回「排序预处理」处方的可执行证据。

    同一组调用排序后会判 fail —— 那是拿不可信的数据断定「学生不会重试」，
    把数据问题记成能力不足。实现必须停在 unscored。

    B-31 后具体 pattern 由 retry_wrong_params（走过 default）变成 retry_exhausted
    （两条都覆盖故障页、末次是 error）。本条锁的是**排序后仍判 fail、不得变 pass**，
    pattern 名不在锁的范围内——它随规则集走，写死会让这条测试变成规则表的副本。
    """
    ordered = sorted(copy.deepcopy(SHUFFLED), key=lambda c: c["call_index"])
    r = ae.evaluate(spec(), {"tool_calls": ordered})
    assert r["state"] == "fail"
    assert r["error_patterns"] == ["retry_exhausted"]


@pytest.mark.parametrize("bad_calls", [
    [{"name": "fetch_orders"}],                    # 缺 call_index
    [{"call_index": 2, "name": "fetch_orders"}],   # 不从 1 起
    [{"call_index": 1}, {"call_index": 1}],        # 重复 / 不连续
    [{"call_index": 1}, {"call_index": "2"}],      # 类型非整数
    [{"call_index": True}],                        # bool 不算整数
    ["not-an-object"],                             # 元素非对象
])
def test_malformed_call_index_is_unscored(bad_calls):
    r = ae.evaluate(spec(), {"tool_calls": bad_calls})
    assert r["state"] == "unscored" and r["reason_code"] == "trace_invalid"
    assert not r.get("error_patterns")


def test_new_unscored_branch_survives_schema_validation():
    """新增的 trace_invalid 分支必须能被 build_report 组装并过 JSON Schema。"""
    raw = json.loads((ROOT / "fixtures" / "raw-perfect.json").read_text(encoding="utf-8"))
    generated = ae.evaluate(spec(), {"tool_calls": copy.deepcopy(SHUFFLED)})
    raw["assertion_results"] = [
        generated if item["id"] == "R3_retry_evidence" else item
        for item in raw["assertion_results"]
    ]
    task = dr.load_task(ROOT / "tasks" / "dbg-001-order-report.yaml")
    report = dr.build_report(task, raw)
    dr.validate_report(report)
    r3 = next(a for a in report["assertions"] if a["id"] == "R3_retry_evidence")
    assert r3["state"] == "unscored"
    assert report["score"]["ratio"] == 0.75


def test_unsupported_assertion_type_raises():
    s = spec()
    s["type"] = "json_field_equals"
    with pytest.raises(NotImplementedError, match="S3-1"):
        ae.evaluate(s, trace(TRACES["真重试"]))


@pytest.mark.parametrize("mutate", ["ordered", "allow_interleaved", "params"])
def test_unsupported_contract_shape_refused(mutate):
    """契约要求本实现做不到的语义时，拒绝执行而不是给出看似合理的结果。"""
    s = spec()
    if mutate == "ordered":
        s["expected"]["ordered"] = False
    elif mutate == "allow_interleaved":
        s["expected"]["allow_interleaved"] = False
    else:
        s["expected"]["calls"][1]["params_match"]["offset"] = 20
    with pytest.raises(ae.EvaluatorError):
        ae.evaluate(s, trace(TRACES["真重试"]))


def test_pattern_must_be_declared_and_registered():
    s = spec()
    with pytest.raises(ae.EvaluatorError, match="未登记进错误模式词表"):
        ae.evaluate(s, trace(TRACES["假重试"]), vocab=set())
    s = spec()
    s["error_patterns"] = ["no_retry"]
    with pytest.raises(ae.EvaluatorError, match="不在本断言 error_patterns"):
        ae.evaluate(s, trace(TRACES["假重试"]))


def test_default_is_observable_when_rules_miss():
    """覆盖度证据：cases 都不命中时仍走 default，并把来源写进 evidence。

    B-31 之后线上规则集对「fail」是完备的：故障点被覆盖 0 次 / 1 次 / ≥2 次三种情况
    各有明写规则，而 ≥2 次时若末次不是 error 就该判 pass、根本不会进分类。
    **所以 default 在线上不可达**，不能再拿「某个真实 trace 恰好落兜底」来测机制——
    那只是把规则集的巧合当成断言。这里人工把 cases 收窄成一条，测的是兜底机制本身。
    """
    s = spec()
    s["error_pattern_detection"]["cases"] = [
        {"match": {"no_tool_calls": True}, "pattern": "no_retry"},
    ]
    r = ae.evaluate(s, trace(TRACES["假重试"]))
    assert r["state"] == "fail"
    assert r["evidence"]["extra"]["classification_source"] == "default"
    assert r["error_patterns"] == ["retry_wrong_params"]


def test_zero_calls_is_classified_by_written_rule():
    """Q10-A 验收：上报了 trace 但零调用 → 命中 no_tool_calls 明写规则，不靠 default。

    round-8 时这条把「只能兜底」钉成 Q10 证据；现在缺口已由人拍板选 A 补上。
    按 A 方案仍归 no_retry（与手写夹具一致）；跨技能归因是 TD-001，不在本步做。
    """
    r = ae.evaluate(spec(), trace([]))
    assert r["state"] == "fail"
    assert r["error_patterns"] == ["no_retry"]
    assert r["evidence"]["extra"]["classification_source"] == "case:no_tool_calls"


# --------------------------------------------------------------------------
# Q10-A：四条明写规则的回归锁（用例集里的输入/期望是唯一来源）
# --------------------------------------------------------------------------

Q10A_CASE_NAMES = ("zero_calls", "only_first_page", "skips_page_10",
                   "wrong_offset_after_failure")


def _case(name: str) -> dict:
    data = json.loads((ROOT / "fixtures" / "tool-call-cases.json").read_text(encoding="utf-8"))
    return next(c for c in data["cases"] if c["name"] == name)


@pytest.mark.parametrize("name", Q10A_CASE_NAMES)
def test_q10a_boundary_cases_are_classified_by_written_rules(name):
    """通过标准 ①：四个过去靠 default 的边界必须命中明写规则。

    显式列出名字，防止有人改用例集的名字让参数化静默漏测。
    """
    case = _case(name)
    r = ae.evaluate(spec(), {"tool_calls": case["tool_calls"]})
    assert r["state"] == case["expect"]["state"], name
    assert r["error_patterns"] == [case["expect"]["pattern"]], name
    source = r["evidence"]["extra"]["classification_source"]
    assert source.startswith("case:"), f"{name} 退回 default 兜底：{source}"
    # TD-004：只断言 startswith("case:") 挡不住判据被改坏（变异 C/D 曾 179 全绿），
    # 必须精确比对规则名；同时锁住 probe 的 offsets，防止计数来源被偷换。
    assert source == case["expect"]["source"], f"{name} 分类来源不精确：{source}"
    assert r["evidence"]["observed"]["offsets"] == case["expect"]["observed_offsets"], name


def test_only_first_page_source_is_exact():
    """TD-004 点名回归锁：C/D 两个盲区变异都会把这条改成 case:no_tool_calls。

    - C（`call_count == 0` → `<= 1`）：call_count=1 被误判成零调用；
    - D（`call_count = len(projected)`）：故障点没触达时 projected=[]，
      于是 call_count 从 1 变 0。
    两者都让「只拉首页」错命中 no_tool_calls，但 pattern 仍是 no_retry，
    所以只有精确 source 断言能抓住。
    """
    case = _case("only_first_page")
    r = ae.evaluate(spec(), {"tool_calls": case["tool_calls"]})
    assert r["evidence"]["extra"]["classification_source"] == "case:fault_point_not_reached"
    assert r["evidence"]["observed"]["offsets"] == [0]


def test_q10a_predicates_follow_yaml_fault_point():
    """计数谓词必须由 expected.calls 派生，不能硬编码 offset=10。

    把 YAML 故障点改成 20 后，[0,20] 这组调用（limit=10）就「覆盖了故障点、只碰一次」，
    应命中 fault_point_hit_once；硬编码 10 的实现会误判为 not_reached。
    """
    s = spec()
    for step in s["expected"]["calls"]:
        step["params_match"] = {"offset_covers": 20}
    r = ae.evaluate(s, trace([call(0), call(20, "error", 500)]))
    assert "fault_point_hit_once" in r["evidence"]["extra"]["classification_source"]


def test_count_predicates_reject_silent_misuse():
    """YAML 把布尔谓词写成 false 必须报错，不能静默变成永不命中又退回 default。"""
    s = spec()
    for c in s["error_pattern_detection"]["cases"]:
        if "no_tool_calls" in (c.get("match") or {}):
            c["match"]["no_tool_calls"] = False
    with pytest.raises(ae.EvaluatorError, match="只接受 true"):
        ae.evaluate(s, trace([]))
    # TD-003：配置错不能靠「被求值到」才暴露；trace 未上报时同样拒绝
    with pytest.raises(ae.EvaluatorError, match="只接受 true"):
        ae.evaluate(s, None)


def test_output_feeds_straight_into_dto():
    """端到端：执行器输出替换 raw 里的 R3，build_report() 必须无改组装。"""
    raw = json.loads((ROOT / "fixtures" / "raw-perfect.json").read_text(encoding="utf-8"))
    generated = ae.evaluate(spec(), trace(TRACES["真重试"]))
    raw["assertion_results"] = [
        generated if item["id"] == "R3_retry_evidence" else item
        for item in raw["assertion_results"]
    ]
    task = dr.load_task(ROOT / "tasks" / "dbg-001-order-report.yaml")
    report = dr.build_report(task, raw)
    r3 = next(a for a in report["assertions"] if a["id"] == "R3_retry_evidence")
    assert r3["state"] == generated["state"] == "pass"
    assert report["score"]["ratio"] == 1.0

    # 换成假重试：DTO 应给出 fail + 错误模式，且分数下降
    raw2 = copy.deepcopy(raw)
    bad = ae.evaluate(spec(), trace(TRACES["假重试"]))
    raw2["assertion_results"] = [
        bad if item["id"] == "R3_retry_evidence" else item
        for item in raw2["assertion_results"]
    ]
    report2 = dr.build_report(task, raw2)
    r3b = next(a for a in report2["assertions"] if a["id"] == "R3_retry_evidence")
    assert r3b["state"] == "fail" and r3b["error_patterns"] == ["retry_wrong_params"]
    assert report2["score"]["ratio"] == 0.75


def test_generated_result_survives_schema_validation():
    """执行器的 evidence 结构要通过 JSON Schema，不能只让 build_report 内部自洽。"""
    generated = ae.evaluate(spec(), trace(TRACES["假重试"]))
    raw = json.loads((ROOT / "fixtures" / "raw-perfect.json").read_text(encoding="utf-8"))
    raw["assertion_results"] = [
        generated if item["id"] == "R3_retry_evidence" else item
        for item in raw["assertion_results"]
    ]
    task = dr.load_task(ROOT / "tasks" / "dbg-001-order-report.yaml")
    report = dr.build_report(task, raw)
    dr.validate_report(report)


def test_r3_yaml_contract_is_what_evaluator_reads():
    """YAML 里的 probe / cases / default 必须齐全，否则执行器会拒绝生成。"""
    s = spec()
    assert s["type"] in ae.SUPPORTED_TYPES
    assert s["error_pattern_detection"]["probe"]
    assert s["error_pattern_detection"]["cases"]
    assert s["error_pattern_detection"]["default"] in s["error_patterns"]
    task = yaml.safe_load((ROOT / "tasks" / "dbg-001-order-report.yaml").read_text(encoding="utf-8"))
    assert "http_status" in task["trace_requirements"]["minimum"]["tool_calls"]["item_fields"]


# --------------------------------------------------------------------------
# TD-003：分类 spec 的加载期 / 判定期校验（round-12 外部验收 H1 / V9）
# --------------------------------------------------------------------------


def _prepend_bad_case(s: dict, case: dict) -> None:
    s["error_pattern_detection"]["cases"].insert(0, case)


def _false_count_predicate(s: dict) -> None:
    for c in s["error_pattern_detection"]["cases"]:
        if "no_tool_calls" in (c.get("match") or {}):
            c["match"]["no_tool_calls"] = False
            return
    raise AssertionError("用例集里没有计数谓词 case，测试底座已漂移")


BAD_SPECS = {
    "缺 match": lambda s: _prepend_bad_case(s, {"pattern": "no_retry"}),
    "match 为空": lambda s: _prepend_bad_case(s, {"match": {}, "pattern": "no_retry"}),
    "match 为 null": lambda s: _prepend_bad_case(s, {"match": None, "pattern": "no_retry"}),
    "match 非对象": lambda s: _prepend_bad_case(s, {"match": "oops", "pattern": "no_retry"}),
    "mtach 拼写错": lambda s: _prepend_bad_case(
        s, {"mtach": {"no_tool_calls": True}, "pattern": "no_retry"}),
    "case 非对象": lambda s: _prepend_bad_case(s, ["no_retry"]),
    "计数谓词写 false": _false_count_predicate,
    "未知谓词藏在短路之后": lambda s: s["error_pattern_detection"]["cases"].append(
        {"match": {"never_reached": True}, "pattern": "no_retry"}),
}


@pytest.mark.parametrize("bad_name", list(BAD_SPECS))
def test_bad_classification_spec_fails_before_any_trace(bad_name):
    """配置错必须在看 trace 之前就抛，不能潜伏到某条特定轨迹才暴露。

    判据用 `trace=None`：若校验放在 trace 分支之后，会直接返回 unscored 而不抛错，
    空 match 的 catch-all 与短路藏起来的 false 就能一直潜伏（H1 / V9）。
    """
    s = spec()
    BAD_SPECS[bad_name](s)
    with pytest.raises(ae.EvaluatorError):
        ae.evaluate(s, None)
    with pytest.raises(ae.EvaluatorError):
        ae.evaluate(s, trace(TRACES["假重试"]))


def test_empty_match_is_not_a_catch_all():
    """H1 原样反例：缺 match 的 case 曾被 `or {}` + `all([])` 变成全命中，
    `classification_source` 还记成空的 `case:`。现在必须在任何轨迹前拒绝。
    """
    s = spec()
    s["error_pattern_detection"]["cases"].insert(0, {"pattern": "no_retry"})
    with pytest.raises(ae.EvaluatorError, match="非空 match"):
        ae.evaluate(s, trace([call(10, "error", 500), call(0), call(10, "error", 500)]))


def test_load_r3_spec_validates_at_load_time(tmp_path):
    """D2=A：YAML 里写错配置，加载期就抛，不用等到跑 trace。"""
    task = yaml.safe_load(
        (ROOT / "tasks" / "dbg-001-order-report.yaml").read_text(encoding="utf-8")
    )
    r3 = next(r for r in task["rubric_credit"] if r["id"] == "R3_retry_evidence")
    r3["error_pattern_detection"]["cases"].insert(0, {"pattern": "no_retry"})
    p = tmp_path / "task.yaml"
    p.write_text(yaml.safe_dump(task, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ae.EvaluatorError, match="非空 match"):
        ae.load_r3_spec(p)


# --------------------------------------------------------------------------
# Q12(a)：status 枚举 / params 类型的前置校验（红队 §11.2 两个反例）
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad_call", [
    {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10},
     "status": "sucess", "result_summary": "page 1"},
    {"call_index": 1, "name": "fetch_orders", "params": None,
     "status": "success", "result_summary": "page 1"},
    {"call_index": 1, "name": "fetch_orders", "params": "oops",
     "status": "success", "result_summary": "page 1"},
])
def test_q12_malformed_call_is_unscored_not_fail(bad_call):
    """上报数据坏 ≠ Agent 不会重试。

    改造前 `_validate_calls()` 只查 call_index，JMESPath 投影会静默丢弃
    畸形调用，于是判 fail + 错误模式，把数据问题记成能力不足。
    """
    r = ae.evaluate(spec(), {"tool_calls": [copy.deepcopy(bad_call)]})
    assert r["state"] == "unscored"
    assert r["reason_code"] == "trace_invalid"
    assert not r.get("error_patterns")
    assert "call_sequence_problem" in r["evidence"]["observed"]


def test_q12_status_enum_comes_from_yaml():
    """允许值不能写死在代码里：注入自定义枚举，判定必须跟着变。"""
    good = trace(TRACES["真重试"])
    custom = {"tool_calls": {"status_enum": ["success"]}}
    # 自定义枚举不含 error → 原本 pass 的「真重试」变成数据不可信
    r = ae.evaluate(spec(), good, requirements=custom)
    assert r["state"] == "unscored" and r["reason_code"] == "trace_invalid"


def test_q12_missing_status_enum_refuses_to_guess():
    with pytest.raises(ae.EvaluatorError, match="status_enum 缺失"):
        ae.evaluate(spec(), trace(TRACES["真重试"]), requirements={"tool_calls": {}})
