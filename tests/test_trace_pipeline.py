"""S3-2（Q11）：原始 trace -> R3 执行器 -> 诊断报告 的全链路约束测试。

三件事必须被锁住：
1. 夹具里的 trace 是**合规原始上报**（call_index 连续、字段齐全），不是另一份手写结论；
2. R3 结果确实由执行器现算，不是把 assertion_results 抄一遍；
3. 接线后分数序 / JSON Schema / 四态纪律与 S2 完全一致。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import assertion_evaluator as ae  # noqa: E402
from arena import diagnosis_report as dr  # noqa: E402
from arena import trace_pipeline as tp  # noqa: E402

FIXTURES = ROOT / "fixtures"
TASK = dr.load_task(ROOT / "tasks" / "dbg-001-order-report.yaml")
SCORE_ORDER = [0.0, 0.0, 0.25, 0.5, 1.0]


def raw(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 夹具本身：原始 trace 必须合规
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_every_fixture_declares_trace_explicitly(name):
    """缺 trace 字段和「未上报」是两件事，不允许靠 .get() 默认值混过去。"""
    assert "trace" in raw(name), name


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_trace_matches_audit(name):
    data = raw(name)
    audit, trace = data["trace_audit"], data["trace"]
    assert (trace is not None) == bool(audit["reported"]), name
    if trace is not None:
        assert trace["final_status"] == audit["final_status"], name
        assert len(trace["tool_calls"]) == audit["call_count"], name


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_trace_calls_satisfy_minimum_contract(name):
    """按 tasks YAML 的 item_fields 校验原始上报，含 call_index 连续性。"""
    fields = TASK["trace_requirements"]["minimum"]["tool_calls"]["item_fields"]
    data = raw(name)
    calls = (data["trace"] or {}).get("tool_calls", [])
    for i, call in enumerate(calls):
        assert call["call_index"] == i + 1, (name, i)
        for key in ("name", "params", "status"):
            assert key in call and key in fields, (name, i, key)
        if call["status"] == "error":
            assert "http_status" in call and "error" in call, (name, i)
        else:
            assert "result_summary" in call, (name, i)


# ---------------------------------------------------------------------------
# 接线：R3 必须现算，分数序不能变
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_r3_result_is_generated_by_evaluator(name):
    """现算结果的 evidence.extra 带 filter_expr / matched_steps，手写夹具带的是 matched_pairs。"""
    built = tp.recompute_r3(raw(name))
    r3 = next(r for r in built["assertion_results"] if r["id"] == tp.R3_ID)
    extra = r3["evidence"].get("extra") or {}
    assert "matched_pairs" not in extra, name  # 手写夹具的痕迹必须消失
    if r3["state"] == "unscored":
        # 未上报 trace 走执行器的早退分支，evidence 只有 observed/expected
        assert r3["reason_code"] == "trace_not_reported", name
    else:
        assert "filter_expr" in extra and "matched_steps" in extra, name


def test_recompute_does_not_mutate_input():
    data = raw("raw-perfect")
    before = copy.deepcopy(data)
    tp.recompute_r3(data)
    assert data == before


def test_score_order_unchanged_after_wiring():
    """接线不能改变 S2 已验证的分数序（含地板分与满分锚点）。"""
    ratios = [tp.run_fixture(n, TASK)["score"]["ratio"] for n in tp.FIXTURE_NAMES]
    assert ratios == SCORE_ORDER, ratios


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_full_pipeline_report_passes_schema(name):
    dr.validate_report(tp.run_fixture(name, TASK))


def test_no_trace_fixture_stays_unscored_end_to_end():
    """raw-no-trace 手写 R3 是 pass；现算 + 组装器的强制改写都必须给出 unscored。"""
    built = tp.recompute_r3(raw("raw-no-trace"))
    generated = next(r for r in built["assertion_results"] if r["id"] == tp.R3_ID)
    assert generated["state"] == "unscored"
    assert generated["reason_code"] == "trace_not_reported"
    report = tp.run_fixture("raw-no-trace", TASK)
    r3 = tp._r3(report)
    assert r3["state"] == "unscored" and not r3["error_patterns"]


def test_raw_empty_q10_gap_is_closed():
    """Q10-A 验收 ②：全链路里零调用的分类与手写基线一致，且不再靠 default。

    round-8 时这里刻意把手写 no_retry 与现算 retry_wrong_params(default) 的差异
    钉成 Q10 的可执行证据；人拍板选 A 后差异应当**消失**，本用例随之从
    「暴露缺口」改写成「缺口已修复」的回归锁（见 review-response-v1.md §12.5）。
    """
    handwritten = next(
        r for r in raw("raw-empty")["assertion_results"] if r["id"] == tp.R3_ID
    )
    generated = next(
        r for r in tp.recompute_r3(raw("raw-empty"))["assertion_results"]
        if r["id"] == tp.R3_ID
    )
    assert handwritten["error_patterns"] == ["no_retry"]
    assert generated["error_patterns"] == ["no_retry"]
    assert generated["evidence"]["extra"]["classification_source"].startswith("case:")


# ---------------------------------------------------------------------------
# 真相源一致性与输入防御
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mutate", ["trace_null_but_reported", "trace_present_but_not_reported",
                                    "drop_trace_key", "trace_wrong_type", "duplicate_r3"])
def test_inconsistent_raw_is_refused(mutate):
    data = raw("raw-perfect")
    if mutate == "trace_null_but_reported":
        data["trace"] = None
    elif mutate == "trace_present_but_not_reported":
        data["trace_audit"]["reported"] = False
    elif mutate == "drop_trace_key":
        del data["trace"]
    elif mutate == "trace_wrong_type":
        data["trace"] = "oops"
    else:
        r3 = next(r for r in data["assertion_results"] if r["id"] == tp.R3_ID)
        data["assertion_results"].append(copy.deepcopy(r3))
    with pytest.raises(tp.PipelineError):
        tp.recompute_r3(data)


def test_missing_fixture_is_refused():
    with pytest.raises(tp.PipelineError, match="找不到夹具"):
        tp.run_fixture("does-not-exist", TASK)


# ---------------------------------------------------------------------------
# 边界 trace 用例集：Q10 改分类器时的测试底座
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", tp.load_cases(), ids=lambda c: c["name"])
def test_boundary_cases_match_executor(case):
    r = ae.evaluate(ae.load_r3_spec(), {"tool_calls": case["tool_calls"]})
    e = case["expect"]
    got_pattern = (r.get("error_patterns") or [None])[0]
    source = (r["evidence"].get("extra") or {}).get("classification_source")
    assert r["state"] == e["state"], case["name"]
    assert got_pattern == e["pattern"], case["name"]
    # TD-004：不再把来源归并成 case/default，精确比对完整 classification_source
    assert source == e["source"], case["name"]
    assert r.get("reason_code") == e["reason_code"], case["name"]
    if e["observed_offsets"] is not None:
        assert r["evidence"]["observed"]["offsets"] == e["observed_offsets"], case["name"]


@pytest.mark.parametrize("case", tp.load_cases(), ids=lambda c: c["name"])
def test_boundary_cases_survive_full_dto_pipeline(case):
    """每个边界 trace 都不只是执行器自洽，还要能组装成合法报告。"""
    data = raw("raw-perfect")
    data["trace"]["tool_calls"] = copy.deepcopy(case["tool_calls"])
    # §57：兼容入口现在也走 F1 派生对账，先让手写声明与派生值一致，
    # 否则结构违规 case 会在入口被拒，测不到本用例要测的 R3 分类 / DTO 合法性。
    data["trace_audit"] = tp._derive_trace_audit(
        data["trace"], TASK["trace_requirements"]["minimum"]
    )
    built = tp.recompute_r3(data)
    report = dr.build_report(TASK, built)
    dr.validate_report(report)
    r3 = tp._r3(report)
    assert r3["state"] == case["expect"]["state"], case["name"]


def test_case_names_are_unique():
    names = [c["name"] for c in tp.load_cases()]
    assert len(names) == len(set(names))


REQUIRED_CASES = {
    "true_retry", "interleaved", "fake_retry", "no_retry", "retry_exhausted",
    "zero_calls", "only_first_page", "skips_page_10", "other_tool_after_failure",
    "wrong_offset_after_failure", "shuffled_call_index", "missing_call_index",
    "bad_status_enum", "params_not_object",
}


def test_case_set_covers_required_names():
    """参数化测试的盲区：cases 被清空时参数化为 0 条，反而全绿。

    显式锁住名字集合，保证 Q10 改分类器时这套底座不会悄悄缩水。
    """
    names = {c["name"] for c in tp.load_cases()}
    assert REQUIRED_CASES <= names, f"边界用例集缺：{sorted(REQUIRED_CASES - names)}"


# ---------------------------------------------------------------------------
# S3-3-2：R5 执行器与五夹具报告基线一致（§12.7 完成判据）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_r5_executor_matches_report_baseline(name):
    """五夹具上，R5 执行器现算的 state 必须与报告里的 R5 state 一致。

    口径澄清：raw 手写结果里五份 R5 全是 `pass`，但 raw-no-trace 的
    `trace_audit.reported=false`，`build_report()` 会把 `trace_*` 断言强制
    改写成 `unscored`（任务 YAML L199-200 与 `scoring.gate.on_unscored`）。
    所以基线取**报告里的 R5 state**，不取 raw 手写字段——后者在 no-trace
    夹具上是 S2 遗留的假基线，见下一条测试与 docs/review-response-v1.md §16。
    """
    data = raw(name)
    generated = ae.evaluate_assertion(ae.load_r5_spec(), data.get("trace"))
    r5 = next(a for a in tp.run_fixture(name)["assertions"] if a["id"] == "R5_clean_exit")
    assert generated["state"] == r5["state"], name


def test_raw_no_trace_r5_handwritten_pass_is_overridden():
    """把「手写基线 vs 报告基线」的分歧钉成证据，不擅自改夹具。

    raw-no-trace 的手写 R5 是 pass/observed=null（没有 trace 却声称干净退出），
    与 YAML 契约冲突。本步只让执行器产出正确的 unscored，夹具保持原样，
    留到 S3-3-6 切「五条全现算」时一并处置（登记为 TD-007）。
    """
    data = raw("raw-no-trace")
    handwritten = next(r for r in data["assertion_results"] if r["id"] == "R5_clean_exit")
    assert handwritten["state"] == "pass" and handwritten["evidence"]["observed"] is None

    generated = ae.evaluate_assertion(ae.load_r5_spec(), data["trace"])
    assert generated["state"] == "unscored"
    assert generated["reason_code"] == "trace_not_reported"

    r5 = next(a for a in tp.run_fixture("raw-no-trace")["assertions"]
              if a["id"] == "R5_clean_exit")
    assert r5["state"] == "unscored" and r5["reason_code"] == "trace_not_reported"


# ---------------------------------------------------------------------------
# S3-3-6：五条全现算的验收锁
#   §12.5 口径澄清 / §20.2 第 2 项 / §21.5 P-A·B·D=A / §22.6 A 类
# ---------------------------------------------------------------------------

TARGET = next(s for s in TASK["rubric_gate"] if s["type"] == "file_exists")["target"]
GOOD_REPORT = raw("raw-perfect")["artifacts"][TARGET]
SENTINEL = "STALE_HANDWRITTEN_S3_3_6"


def by_id(report: dict) -> dict:
    return {a["id"]: a for a in report["assertions"]}


def all_ids() -> list[str]:
    gate, credit = dr.rubric_layers(TASK)
    return [s["id"] for s in gate + credit]


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_full_recompute_matches_s2_baseline(name):
    """§20.2 第 2 项：五条全现算后，报告层逐项与 S2 手写基线一致。

    只锁 raw 层会假红（§22.2 §1.7）：`raw-no-trace` 的手写 pass 会被
    `build_report()` 的强制改写抹平。所以锁的是**报告层**的
    state / ratio / disqualified / reason_code 四项，不只锁 state。
    """
    baseline = dr.build_report(TASK, raw(name))
    actual = tp.run_fixture(name, TASK)
    assert actual["score"]["ratio"] == baseline["score"]["ratio"], name
    assert actual["score"]["disqualified"] == baseline["score"]["disqualified"], name
    base, got = by_id(baseline), by_id(actual)
    assert set(got) == set(base) == set(all_ids()), name
    for rid in base:
        assert got[rid]["state"] == base[rid]["state"], (name, rid, "state")
        assert got[rid].get("reason_code") == base[rid].get("reason_code"), (name, rid, "reason")


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_handwritten_results_are_never_read(name):
    """Q5 note 污染锁的加强版：把整条手写结果换成哨兵，报告里不得泄漏。

    `recompute_all()` 只借手写结果的 id 壳核对 rubric 集合，内容一律不读；
    只锁 state/ratio 抓不到「手写 note 泄漏」这种病，本锁直接盯字符串。
    """
    data = copy.deepcopy(raw(name))
    data["assertion_results"] = [
        {"id": rid, "state": "pass", "note": SENTINEL,
         "evidence": {"observed": SENTINEL, "expected": SENTINEL}}
        for rid in [r["id"] for r in data["assertion_results"]]
    ]
    built = tp.recompute_all(data, TASK)
    assert SENTINEL not in json.dumps(built, ensure_ascii=False), name
    report = dr.build_report(TASK, built)
    dr.validate_report(report)
    assert SENTINEL not in json.dumps(report, ensure_ascii=False), name


def test_injected_task_reaches_r3():
    """F2 正向锁：注入 task 改 R3 期望，判定表达式必须跟着变，不能回落默认 YAML。"""
    task = copy.deepcopy(TASK)
    r3 = next(s for s in task["rubric_credit"] if s["id"] == "R3_retry_evidence")
    for step in r3["expected"]["calls"]:
        step["params_match"]["offset"] = 999
    report = tp.run_fixture("raw-perfect", task)
    item = tp._r3(report)
    assert item["state"] == "fail"
    assert "999" in item["evidence"]["extra"]["filter_expr"]


def test_injected_task_tools_reach_r4():
    """F2 第二条尾巴：R4 的工具声明也必须来自注入的 task，不是默认文件。"""
    task = copy.deepcopy(TASK)
    tool = next(t for t in task["tools"] if t["name"] == "fetch_orders")
    next(p for p in tool["parameters"] if p["name"] == "limit")["maximum"] = 5
    report = tp.run_fixture("raw-perfect", task)
    r4 = by_id(report)["R4_no_param_hallucination"]
    assert r4["state"] == "fail"
    assert "param_out_of_range" in r4["error_patterns"]


@pytest.mark.parametrize("mode,ratio,disqualified", [
    ("field_missing", 0.5, False),
    ("empty_dict", 0.0, True),
])
def test_p_a_two_branches_are_pinned(mode, ratio, disqualified):
    """P-A=A / TD-012：两个分支已可判定，把代价显式钉死，不靠自评。

    `artifacts` 字段缺失 = 平台采集故障 → R1 unscored，按
    `on_unscored=do_not_disqualify` 不拦，其余 credit 照常计分得 0.5；
    `artifacts: {}` = 学生确实没交 → R1 fail → DQ / 0.0。
    """
    data = copy.deepcopy(raw("raw-perfect"))
    if mode == "field_missing":
        del data["artifacts"]
    else:
        data["artifacts"] = {}
    report = dr.build_report(TASK, tp.recompute_all(data, TASK))
    dr.validate_report(report)  # TD-008 销项后不再抛 ReportError
    assert report["score"]["ratio"] == ratio, mode
    assert report["score"]["disqualified"] is disqualified, mode
    r1, r2 = by_id(report)["R1_file_exists"], by_id(report)["R2_report_correct"]
    if mode == "field_missing":
        assert r1["state"] == "unscored" and r1["reason_code"] == "artifact_invalid"
        assert r2["state"] == "blocked" and r2["reason_code"] == "dependency_blocked"
        # F6② / TD-021：文案订正后必须如实承认可能出现 0 < ratio < 1
        assert any("0 < ratio < 1" in n for n in report["notes"])
    else:
        assert r1["state"] == "fail" and r2["state"] == "blocked"


def test_artifacts_are_forwarded_to_r1_and_r2():
    """artifacts 正向传递锁：改产物本体会真的改变 R1/R2，不是从 trace 自述推断。"""
    data = copy.deepcopy(raw("raw-perfect"))
    data["artifacts"][TARGET] = '{"valid_count": 3, "total_amount": 1.0}'
    report = dr.build_report(TASK, tp.recompute_all(data, TASK))
    dr.validate_report(report)
    assert by_id(report)["R1_file_exists"]["state"] == "pass"  # 文件仍在
    r2 = by_id(report)["R2_report_correct"]
    assert r2["state"] == "fail" and r2["error_patterns"], "内容错必须被 R2 抓到"


def test_unknown_tool_stays_unscored_with_evidence():
    """P-B=A：维持已拍板口径 `unscored/trace_invalid`，只加回归锁与 evidence。

    `trace_audit.reported/schema_valid` 均为 true 时，`build_report()` 的
    `trace_` 强制改写**不触发**（§21.2 #14），执行器结果原样进报告。
    """
    data = copy.deepcopy(raw("raw-perfect"))
    calls = data["trace"]["tool_calls"]
    calls.append({"call_index": len(calls) + 1, "name": "imaginary_tool",
                  "params": {}, "status": "success", "result_summary": "unknown"})
    data["trace_audit"]["call_count"] = len(calls)
    report = dr.build_report(TASK, tp.recompute_all(data, TASK))
    dr.validate_report(report)
    r4 = by_id(report)["R4_no_param_hallucination"]
    assert r4["state"] == "unscored" and r4["reason_code"] == "trace_invalid"
    assert "imaginary_tool" in json.dumps(r4["evidence"]["observed"], ensure_ascii=False)


def _paged_calls(offsets: list[int]) -> list[dict]:
    calls = [
        {"call_index": i, "name": "fetch_orders",
         "params": {"offset": off, "limit": 8}, "status": "success",
         "result_summary": "8 orders"}
        for i, off in enumerate(offsets, 1)
    ]
    calls.append({"call_index": len(offsets) + 1, "name": "write_report",
                  "params": {"path": TARGET, "content": GOOD_REPORT},
                  "status": "success", "result_summary": "written"})
    return calls


def test_td013_ratio_075_is_pinned():
    """P-D=A / TD-013：`[0,8,17]` 合法重叠分页绕开 offset=10 故障点仍拿 0.75。

    本步**只钉回归锁不修判据**（修会改变五夹具分数序与 Q10-A 词表），
    R6 一致性校验仍挂 P3/M2。
    """
    data = copy.deepcopy(raw("raw-perfect"))
    data["trace"]["tool_calls"] = _paged_calls([0, 8, 17])
    data["trace_audit"]["call_count"] = 4
    report = dr.build_report(TASK, tp.recompute_all(data, TASK))
    dr.validate_report(report)
    assert report["score"]["ratio"] == 0.75
    assert report["score"]["disqualified"] is False
    r3 = tp._r3(report)
    assert r3["state"] == "fail" and r3["error_patterns"] == ["no_retry"]


@pytest.mark.parametrize("mutate", [
    "trace_null_but_reported", "trace_present_but_not_reported",
    "drop_trace_key", "trace_wrong_type", "missing_result", "extra_result",
])
def test_recompute_all_refuses_inconsistent_raw(mutate):
    """T11：真相源冲突与 id 壳不一致都在 pipeline 入口拒绝，不猜哪个对。"""
    data = copy.deepcopy(raw("raw-perfect"))
    if mutate == "trace_null_but_reported":
        data["trace"] = None
    elif mutate == "trace_present_but_not_reported":
        data["trace_audit"]["reported"] = False
    elif mutate == "drop_trace_key":
        del data["trace"]
    elif mutate == "trace_wrong_type":
        data["trace"] = "oops"
    elif mutate == "missing_result":
        data["assertion_results"] = data["assertion_results"][:-1]
    else:
        data["assertion_results"].append({"id": "R9_bogus"})
    with pytest.raises(tp.PipelineError):
        tp.recompute_all(data, TASK)


def test_recompute_all_does_not_mutate_input():
    data = raw("raw-perfect")
    before = copy.deepcopy(data)
    tp.recompute_all(data, TASK)
    assert data == before


# ---------------------------------------------------------------------------
# F1 / TD-016：trace_audit 是 trace 的派生物 —— 正向锁（round-24 反转）
#
# round-22 的三条缺口证据锁断言的是「矛盾会被放行」这一现状；round-24 拍板
# ② 派生物 + R1 严格拒绝后，按 TD-008 惯例**反转成正向锁、函数不删除**
# （去掉失真的 `currently_allowed` 后缀，`-k f1_td016` 前缀不变）。
# ---------------------------------------------------------------------------


def _report_from_raw(data: dict):
    """全链路组装 + schema 校验，返回报告（供 F1 通过路径回归锁复用）。"""
    report = dr.build_report(TASK, tp.recompute_all(data, TASK))
    dr.validate_report(report)  # 不抛 = 放行
    return report


def test_f1_td016_call_count_conflict_is_refused():
    """A 场景：audit.call_count=999 与 trace 实有 5 条冲突，入口拒绝组装。"""
    data = copy.deepcopy(raw("raw-perfect"))
    assert len(data["trace"]["tool_calls"]) == 5
    data["trace_audit"]["call_count"] = 999
    with pytest.raises(tp.PipelineError, match="call_count"):
        tp.recompute_all(data, TASK)


def test_f1_td016_final_status_conflict_is_refused():
    """B 场景：trace.final_status=crashed 与 audit.final_status=completed 冲突，入口拒绝。

    `fail` 计入掌握度，所以不能把损坏字段写成能力失败（回件 §3.2）。
    """
    data = copy.deepcopy(raw("raw-perfect"))
    data["trace"]["final_status"] = "crashed"
    assert data["trace_audit"]["final_status"] == "completed"
    with pytest.raises(tp.PipelineError, match="final_status"):
        tp.recompute_all(data, TASK)


def test_f1_td016_schema_valid_conflict_is_refused():
    """C 场景：call_index 不连续使结构校验失败，audit.schema_valid=true 即冲突。"""
    data = copy.deepcopy(raw("raw-perfect"))
    data["trace"]["tool_calls"][0]["call_index"] = 9
    with pytest.raises(tp.PipelineError, match="schema_valid"):
        tp.recompute_all(data, TASK)


# ---------------------------------------------------------------------------
# round-24 / F1 回归锁：② 派生物 + R1 严格拒绝 + 两条新 allOf
#
# 每条对应 §25.2 / §25.5 的一个可判定命题；活锁由字节级变异裁判证明。
# ---------------------------------------------------------------------------


def _minimum():
    return (TASK.get("trace_requirements") or {})["minimum"]


def _audit_validator():
    schema = dr.load_schema()
    return jsonschema.Draft202012Validator(
        {"$defs": schema.get("$defs", {}), **schema["properties"]["trace_audit"]}
    )


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_f1_report_audit_equals_derived_value(name):
    """报告里的 audit 必须是派生值，且与夹具手写值逐字相等（不改夹具的依据）。"""
    data = raw(name)
    supplied = copy.deepcopy(data["trace_audit"])
    report = _report_from_raw(data)
    derived = tp._derive_trace_audit(data.get("trace"), _minimum())
    assert report["trace_audit"] == derived
    assert report["trace_audit"] == supplied


def test_f1_report_carries_derived_audit(monkeypatch):
    """证明写入报告的确实是派生对象，不是原样复制（删掉赋值行即 FAILED）。"""
    data = copy.deepcopy(raw("raw-perfect"))
    real = tp._derive_trace_audit(data["trace"], _minimum())
    assert real["call_count"] == 5
    fake = dict(real, call_count=7)
    monkeypatch.setattr(tp, "_derive_trace_audit", lambda trace, req: dict(fake))
    monkeypatch.setattr(tp, "_refuse_audit_conflict", lambda supplied, derived: None)
    report = _report_from_raw(data)
    assert report["trace_audit"]["call_count"] == 7


def _p1(d):
    d["trace_audit"].update(schema_valid=False, invalid_reasons=[])


def _p2(d):
    d["trace_audit"].update(schema_valid=False,
                            invalid_reasons=["call_index_not_sequential"])


def _p3(d):
    d["trace"]["final_status"] = "crashed"
    d["trace_audit"].update(schema_valid=False,
                            invalid_reasons=["missing_required_field"])


def _p4(d):
    d["trace_audit"]["final_status"] = "completed"


def _p5_missing(d):
    d["trace"].pop("final_status")


def _p5_weird(d):
    d["trace"]["final_status"] = "weird"


def _p6(d):
    d["trace"]["tool_calls"] = "oops"


@pytest.mark.parametrize("mutate,base,expect", [
    (_p1, "raw-perfect", "schema_valid"),
    (_p2, "raw-perfect", "schema_valid"),
    (_p3, "raw-perfect", "final_status"),
    (_p4, "raw-no-trace", "final_status"),
    (_p5_missing, "raw-perfect", "schema_valid"),
    (_p5_weird, "raw-perfect", "schema_valid"),
    (_p6, "raw-perfect", "call_count"),
], ids=["P1", "P2", "P3", "P4", "P5-missing", "P5-weird", "P6"])
def test_f1_reverse_paths_are_refused(mutate, base, expect):
    """§25.2 / 回件 §6 的 P1–P6：A/B/C 之外的矛盾路径同样在入口被拒。"""
    data = copy.deepcopy(raw(base))
    mutate(data)
    with pytest.raises(tp.PipelineError, match=expect):
        tp.recompute_all(data, TASK)


def test_f1_p7_empty_tool_calls_is_not_refused():
    """TD-013 / F5：空集不是 F1 冲突；F5 落地后 R4 不再平凡 pass，ratio 0.75→0.5。"""
    data = copy.deepcopy(raw("raw-perfect"))
    data["trace"]["tool_calls"] = []
    data["trace_audit"]["call_count"] = 0
    report = _report_from_raw(data)
    assert report["score"]["ratio"] == 0.5
    assert report["trace_audit"]["schema_valid"] is True
    assert by_id(report)["R4_no_param_hallucination"]["state"] == "unscored"


def test_f1_p9_missing_agent_id_is_not_refused():
    """TD-020 ③ 拍板 b：agent_id 改为可选元数据后，缺失仍不拒、不改分数。"""
    data = copy.deepcopy(raw("raw-perfect"))
    data["trace"].pop("agent_id")
    report = _report_from_raw(data)
    assert report["score"]["ratio"] == 1.0


def test_f1_p8_invalid_reasons_conflict_is_refused_at_entry():
    """口径变化：P8 原由 schema A2 拒；F1 落地后在入口先被 invalid_reasons 对账拒。"""
    data = copy.deepcopy(raw("raw-perfect"))
    data["trace_audit"]["invalid_reasons"] = ["not_reported"]
    with pytest.raises(tp.PipelineError, match="invalid_reasons"):
        tp.recompute_all(data, TASK)


def test_f1_semantic_trace_invalid_does_not_flip_schema_valid():
    """H3 / M5：未知工具是语义问题，派生 audit 仍 schema_valid=true，P-B 不被推翻。"""
    data = copy.deepcopy(raw("raw-perfect"))
    calls = data["trace"]["tool_calls"]
    calls.append({"call_index": len(calls) + 1, "name": "imaginary_tool",
                  "params": {}, "status": "success", "result_summary": "unknown"})
    data["trace_audit"]["call_count"] = len(calls)
    built = tp.recompute_all(data, TASK)
    assert built["trace_audit"]["schema_valid"] is True
    assert built["trace_audit"]["invalid_reasons"] == []
    report = dr.build_report(TASK, built)
    dr.validate_report(report)
    r4 = by_id(report)["R4_no_param_hallucination"]
    assert r4["state"] == "unscored" and r4["reason_code"] == "trace_invalid"


def test_f1_compat_entry_recompute_r3_still_bypasses_derivation():
    """TD-024 收敛后的正向锁（函数名是 S3-2 历史遗留，语义由 §57 反转，按 §26 惯例不改名）。

    兼容入口现在与主链路共用 `_reconcile_audit()`：一致 audit 产出派生值、
    矛盾 audit 在入口被拒。顺带补 §55.7 登记的 `recompute_all` 侧覆盖缺口。
    """
    data = copy.deepcopy(raw("raw-perfect"))
    derived = tp._derive_trace_audit(
        data["trace"], TASK["trace_requirements"]["minimum"]
    )
    built = tp.recompute_r3(data)
    assert built["trace_audit"] == derived
    report = dr.build_report(TASK, built)
    dr.validate_report(report)
    assert report["trace_audit"] == derived

    conflicted = copy.deepcopy(raw("raw-perfect"))
    conflicted["trace_audit"]["call_count"] = 999
    with pytest.raises(tp.PipelineError, match="call_count"):
        tp.recompute_r3(conflicted)
    with pytest.raises(tp.PipelineError, match="call_count"):
        tp.recompute_all(conflicted, TASK)

    # §55.7 缺口：主链路的真相源冲突防御此前无专属正向锁，这里补上。
    reported_conflict = copy.deepcopy(raw("raw-perfect"))
    reported_conflict["trace"] = None
    with pytest.raises(tp.PipelineError, match="reported"):
        tp.recompute_all(reported_conflict, TASK)


def _bad_index(d):
    d["trace"]["tool_calls"][0]["call_index"] = 9


def _bad_status(d):
    d["trace"]["tool_calls"][0]["status"] = "weird"


def _bad_params(d):
    d["trace"]["tool_calls"][0]["params"] = "oops"


def _no_final(d):
    d["trace"].pop("final_status")


def _calls_not_array(d):
    d["trace"]["tool_calls"] = "oops"


@pytest.mark.parametrize("mutate,expect", [
    (_bad_index, ["call_index_not_sequential"]),
    (_bad_status, ["field_type_invalid"]),
    (_bad_params, ["field_type_invalid"]),
    (_no_final, ["missing_required_field"]),
    (_calls_not_array, ["field_type_invalid"]),
], ids=["bad-index", "bad-status", "bad-params", "no-final", "calls-not-array"])
def test_f1_derive_audit_reason_codes(mutate, expect):
    """派生器的原因码必须落在 schema 的 traceInvalidReason 枚举内、且可区分。"""
    data = copy.deepcopy(raw("raw-perfect"))
    mutate(data)
    derived = tp._derive_trace_audit(data["trace"], _minimum())
    assert derived["schema_valid"] is False
    assert derived["invalid_reasons"] == expect
    _audit_validator().validate(derived)


def test_f1_derive_audit_for_missing_trace():
    assert tp._derive_trace_audit(None, _minimum()) == {
        "reported": False, "schema_valid": False,
        "invalid_reasons": ["not_reported"], "call_count": 0,
    }


@pytest.mark.parametrize("inst,want", [
    ({"reported": True, "schema_valid": False, "invalid_reasons": [],
      "call_count": 0}, False),
    ({"reported": True, "schema_valid": False,
      "invalid_reasons": ["missing_required_field"], "call_count": 0}, True),
    ({"reported": False, "schema_valid": False, "invalid_reasons": ["not_reported"],
      "call_count": 0, "final_status": "completed"}, False),
    ({"reported": False, "schema_valid": False, "invalid_reasons": ["not_reported"],
      "call_count": 0}, True),
], ids=["A3-false-no-reason", "A3-false-with-reason",
        "A4-no-trace-with-final", "A4-no-trace-clean"])
def test_f1_new_allof_rules(inst, want):
    """round-24 新增两条 allOf：A3 关闭 P1、A4 关闭 P4 / TD-023。"""
    assert _audit_validator().is_valid(inst) is want


# ---------------------------------------------------------------------------
# F3 / TD-018：R3 把「调用畸形 / 数据缺字段」记成「能力不足」—— 缺口证据锁
#
# round-27（P1）/ round-28（P2）已把 ① 三条与 ② 四条全部反转成正向锁，
# 并补 9 条边界锁；函数名保留 `test_f3_td018_` 前缀，沿用 TD-008 / F1 的
# 「反转不删除」处置惯例。`-k f3_td018` 共 16 条。
# 两层次（按 §29 拍板分两步落地，详见 `docs/review-response-v1.md` §29 / §30）：
#   ① **P1**：失败步所需的 params_match 字段缺失 → unscored/trace_invalid；
#      `call_count` 改数目标工具调用条数，不再用投影后剩下的有效 offset 数。
#   ② **P2**：失败步声明的 http_status 缺失/null → 同上；值写错仍判 fail，
#      B1 / B2 在归因层可区分；audit 保持结构合规（§4 分层共存）。
# 已销项：证据层原位保 null（TD-025，§54），序列不再被改短。
# 仍存边界：条件必填仍无全局执行者（TD-026）、专用原因码仍无生产者（TD-027）。
# F3 判定层已闭合，不在本步扩 DTO。
# ---------------------------------------------------------------------------


def _f3_raw(mutate=None) -> dict:
    """从 raw-perfect 出发构造场景；重算 audit 派生值，避开 F1 的对账入口。"""
    data = copy.deepcopy(raw("raw-perfect"))
    if mutate is not None:
        mutate(data)
    data["trace_audit"] = tp._derive_trace_audit(data["trace"], _minimum())
    return data


def _f3_assertion(report: dict, rid: str) -> dict:
    return next(a for a in report["assertions"] if a["id"] == rid)


def _f3_all_hallucinated(d):
    """四条 fetch_orders 的 params.offset 全部误写成 offsets。"""
    for c in d["trace"]["tool_calls"]:
        if c["name"] == "fetch_orders" and "offset" in c["params"]:
            c["params"]["offsets"] = c["params"].pop("offset")


def _f3_only_write_report(d):
    """有工具调用，但不是目标工具。"""
    wr = copy.deepcopy(next(c for c in raw("raw-perfect")["trace"]["tool_calls"]
                           if c["name"] == "write_report"))
    wr["call_index"] = 1
    d["trace"]["tool_calls"] = [wr]


def _f3_partial_hallucinated(d):
    """只把 error 那条（第 2 次调用）的 offset 键误写：该位读不到值，原位保 None。"""
    for c in d["trace"]["tool_calls"]:
        if (c["name"] == "fetch_orders" and c["params"].get("offset") == 10
                and c["status"] == "error"):
            c["params"]["offsets"] = c["params"].pop("offset")
            break


def _f3_drop_http_status(d):
    for c in d["trace"]["tool_calls"]:
        if c["status"] == "error":
            c.pop("http_status", None)


def _f3_wrong_http_status(d):
    for c in d["trace"]["tool_calls"]:
        if c["status"] == "error":
            c["http_status"] = 404


# P1 边界场景 helper：均从 raw-perfect 派生，只改 R3 自己要读的字段。
def _f3_limit_out_of_range(d):
    for c in d["trace"]["tool_calls"]:
        if c["name"] == "fetch_orders" and c["params"].get("offset") == 0:
            c["params"]["limit"] = 20


def _f3_null_offset_first(d):
    for c in d["trace"]["tool_calls"]:
        if c["name"] == "fetch_orders" and c["params"].get("offset") == 0:
            c["params"]["offset"] = None


def _f3_delete_offset_first(d):
    for c in d["trace"]["tool_calls"]:
        if c["name"] == "fetch_orders" and c["params"].get("offset") == 0:
            del c["params"]["offset"]


def _f3_missing_retry_success_param(d):
    for c in d["trace"]["tool_calls"]:
        if (c["name"] == "fetch_orders" and c["params"].get("offset") == 10
                and c["status"] == "success"):
            c["params"]["offsets"] = c["params"].pop("offset")
            break


def _f3_no_retry_with_null_success(d):
    calls = [c for c in d["trace"]["tool_calls"]
             if not (c["name"] == "fetch_orders" and c["status"] == "error")]
    calls[0]["params"]["offset"] = None
    for i, c in enumerate(calls, 1):
        c["call_index"] = i
    d["trace"]["tool_calls"] = calls


def _f3_all_offsets_null_no_error(d):
    calls = [c for c in d["trace"]["tool_calls"]
             if not (c["name"] == "fetch_orders" and c["status"] == "error")]
    for c in calls:
        if c["name"] == "fetch_orders":
            c["params"]["offset"] = None
    for i, c in enumerate(calls, 1):
        c["call_index"] = i
    d["trace"]["tool_calls"] = calls


def _f3_null_error_offset(d):
    for c in d["trace"]["tool_calls"]:
        if c["name"] == "fetch_orders" and c["status"] == "error":
            c["params"]["offset"] = None


def _f3_null_error_http_status(d):
    for c in d["trace"]["tool_calls"]:
        if c["status"] == "error":
            c["http_status"] = None


def _f3_missing_offset_before_and_on_retry(d):
    """更早的 success 调用与失败步候选同时缺字段，用来检验 after 过滤。"""
    _f3_missing_retry_success_param(d)
    for c in d["trace"]["tool_calls"]:
        if c["name"] == "fetch_orders" and c["params"].get("offset") == 0:
            del c["params"]["offset"]


def test_f3_td018_param_projection_loss_is_unscored_not_no_retry():
    """① 参数投影丢值：R4 已抓到参数幻觉，R3 不能把它记成「零调用 / 不会重试」。"""
    data = _f3_raw(_f3_all_hallucinated)
    assert len(data["trace"]["tool_calls"]) == 5
    report = _report_from_raw(data)
    r3 = _f3_assertion(report, "R3_retry_evidence")
    r4 = _f3_assertion(report, "R4_no_param_hallucination")
    assert report["trace_audit"]["call_count"] == 5
    assert r4["state"] == "fail" and "param_out_of_schema" in r4["error_patterns"]
    assert r3["state"] == "unscored" and r3["reason_code"] == "trace_invalid"
    assert r3["error_patterns"] == []
    assert "params.offset" in r3["evidence"]["observed"]["ambiguous_match"]
    # TD-025 已销项：4 次目标工具调用全部缺 offset，每个位置原位保留 None，序列不再被改短。
    assert r3["evidence"]["observed"]["offsets"] == [None, None, None, None]
    assert report["score"]["ratio"] == 0.5


def test_f3_td018_only_write_report_stays_no_tool_calls():
    """① no_tool_calls 的口径是「目标工具零次」，不是「零次工具调用」。"""
    report = _report_from_raw(_f3_raw(_f3_only_write_report))
    r3 = _f3_assertion(report, "R3_retry_evidence")
    r4 = _f3_assertion(report, "R4_no_param_hallucination")
    assert report["trace_audit"]["call_count"] == 1
    # F5 / TD-013 反转：只有无关工具调用，目标工具零次 → R4 无覆盖，不再计掌握度。
    assert r4["state"] == "unscored" and r4["reason_code"] == "trace_invalid"
    assert r3["state"] == "fail" and r3["error_patterns"] == ["no_retry"]
    assert r3["evidence"]["extra"]["classification_source"] == "case:no_tool_calls"
    assert report["score"]["ratio"] == 0.5


def test_f3_td018_partial_projection_loss_is_unscored():
    """① 投影原位保 null 后序列不缩短；失败步所需字段缺失，判据不成立。
    函数名保留历史命名（原指「投影丢值」），语义随 TD-025 销项更新，不改 node id。
    """
    report = _report_from_raw(_f3_raw(_f3_partial_hallucinated))
    r3 = _f3_assertion(report, "R3_retry_evidence")
    assert r3["state"] == "unscored" and r3["reason_code"] == "trace_invalid"
    assert r3["error_patterns"] == []
    assert "params.offset" in r3["evidence"]["observed"]["ambiguous_match"]
    # TD-025 已销项：第 2 次调用（error 那条）缺 offset，None 留在第 2 位。
    assert r3["evidence"]["observed"]["offsets"] == [0, None, 10, 20]
    assert report["score"]["ratio"] == 0.5


def test_f3_td018_missing_http_status_is_unscored():
    """② 条件必填缺失：判据缺输入，属数据不可信，不是能力失败。"""
    report = _report_from_raw(_f3_raw(_f3_drop_http_status))
    r3 = _f3_assertion(report, "R3_retry_evidence")
    assert r3["state"] == "unscored" and r3["reason_code"] == "trace_invalid"
    assert r3["error_patterns"] == []
    assert "http_status" in r3["evidence"]["observed"]["ambiguous_match"]
    assert r3["evidence"]["observed"]["http_statuses"] == [None, None]
    assert report["score"]["ratio"] == 0.75


def test_f3_td018_missing_and_wrong_http_status_are_distinguishable():
    """② 缺字段（unscored）与值写错（fail）必须在归因层可区分，不能只靠 observed。"""
    b1 = _report_from_raw(_f3_raw(_f3_drop_http_status))
    b2 = _report_from_raw(_f3_raw(_f3_wrong_http_status))
    r1 = _f3_assertion(b1, "R3_retry_evidence")
    r2 = _f3_assertion(b2, "R3_retry_evidence")
    assert (r1["state"], r1.get("reason_code"), r1["error_patterns"]) == (
        "unscored", "trace_invalid", [])
    assert (r2["state"], r2.get("reason_code"), r2["error_patterns"]) == (
        "fail", None, ["retry_wrong_params"])
    assert b1["score"]["ratio"] == b2["score"]["ratio"] == 0.75
    assert r1["evidence"]["observed"]["http_statuses"] == [None, None]
    assert r2["evidence"]["observed"]["http_statuses"] == [404, None]
    # 即使遮掉 observed.http_statuses，归因结论仍不同（不是「只有证据字段不同」）。
    b1, b2 = copy.deepcopy(b1), copy.deepcopy(b2)
    for rep in (b1, b2):
        _f3_assertion(rep, "R3_retry_evidence")["evidence"]["observed"]["http_statuses"] = "<ignored>"
    assert b1 != b2


def test_f3_td018_audit_stays_structural_for_conditional_required():
    """② 跨模块：条件必填是执行器语义，不塞进 F1 的结构派生器（executor-semantics §4）。"""
    report = _report_from_raw(_f3_raw(_f3_drop_http_status))
    assert report["trace_audit"]["schema_valid"] is True
    assert report["trace_audit"]["invalid_reasons"] == []
    assert report["trace_audit"]["call_count"] == 5
    r3 = _f3_assertion(report, "R3_retry_evidence")
    assert r3["state"] == "unscored" and r3["reason_code"] == "trace_invalid"


def test_f3_td018_schema_reason_code_stays_reserved():
    """② schema 的专用原因码暂无生产者；R3 复用 assertions 枚举内已有的 trace_invalid。"""
    enum = dr.load_schema()["$defs"]["traceInvalidReason"]["enum"]
    assert "status_error_without_http_status" in enum
    b1 = _f3_assertion(_report_from_raw(_f3_raw(_f3_drop_http_status)), "R3_retry_evidence")
    assert b1["reason_code"] == "trace_invalid"
    seen: set[str] = set()
    for name in tp.FIXTURE_NAMES:
        seen.update(tp.run_fixture(name)["trace_audit"]["invalid_reasons"])
    for mutate in (None, _f3_all_hallucinated, _f3_only_write_report,
                   _f3_partial_hallucinated, _f3_drop_http_status,
                   _f3_wrong_http_status, _f3_null_error_http_status):
        report = _report_from_raw(_f3_raw(mutate))
        seen.update(report["trace_audit"]["invalid_reasons"])
        for a in report["assertions"]:
            if a.get("reason_code"):
                seen.add(a["reason_code"])
    assert "status_error_without_http_status" not in seen


# --- F3-P1 边界锁：证明「限定版」只拒绝失败步候选，不一刀切判数据不可信 ------

def test_f3_td018_unrelated_range_violation_keeps_r3_pass():
    """C1：第一层只校验 R3 自己用到的字段；R4 的 range 违规不该把 R3 判不可信。"""
    report = _report_from_raw(_f3_raw(_f3_limit_out_of_range))
    r3 = _f3_assertion(report, "R3_retry_evidence")
    r4 = _f3_assertion(report, "R4_no_param_hallucination")
    assert r3["state"] == "pass"
    assert r4["state"] == "fail" and r4["error_patterns"] == ["param_out_of_range"]
    assert report["score"]["ratio"] == 0.75


def test_f3_td018_null_offset_outside_retry_pair_keeps_pass():
    """C2 反例：offset=0 的 null 不影响 offset=10 的重试证据，不能一刀切 unscored。"""
    report = _report_from_raw(_f3_raw(_f3_null_offset_first))
    r3 = _f3_assertion(report, "R3_retry_evidence")
    assert r3["state"] == "pass"
    assert r3["evidence"]["observed"]["offsets"] == [None, 10, 10, 20]  # TD-025：第 1 位原位保 null
    r4 = _f3_assertion(report, "R4_no_param_hallucination")
    assert r4["state"] == "fail" and r4["error_patterns"] == ["param_type_mismatch"]
    assert report["score"]["ratio"] == 0.75


def test_f3_td018_deleted_offset_outside_retry_pair_keeps_pass():
    """C5 反例：删掉 offset=0 的键同样不影响重试证据；全局 presence 检查会误伤。"""
    report = _report_from_raw(_f3_raw(_f3_delete_offset_first))
    r3 = _f3_assertion(report, "R3_retry_evidence")
    assert r3["state"] == "pass"
    r4 = _f3_assertion(report, "R4_no_param_hallucination")
    assert r4["state"] == "fail" and r4["error_patterns"] == ["param_missing_required"]


def test_f3_td018_missing_offset_on_success_step_is_unscored():
    """C4：失败落在第二步时，歧义检查也必须看到第二步的缺字段调用。"""
    report = _report_from_raw(_f3_raw(_f3_missing_retry_success_param))
    r3 = _f3_assertion(report, "R3_retry_evidence")
    assert r3["state"] == "unscored" and r3["reason_code"] == "trace_invalid"
    assert "params.offset" in r3["evidence"]["observed"]["ambiguous_match"]


def test_f3_td018_no_retry_with_null_success_stays_fail():
    """C3 反例：没有 error 调用时，null offset 不是失败的候选解释，仍应判 fail。"""
    report = _report_from_raw(_f3_raw(_f3_no_retry_with_null_success))
    r3 = _f3_assertion(report, "R3_retry_evidence")
    assert r3["state"] == "fail"
    assert r3.get("reason_code") is None
    # 具体 pattern 属 Q10-A / TD-001 口径；本步只锁「没有被误判成 unscored」。
    assert r3["error_patterns"] == ["retry_wrong_params"]


def test_f3_td018_zero_valid_offsets_are_not_zero_target_calls():
    """C7 / Q1(a)：call_count 数目标工具调用条数，不是「投影后还剩几个有效 offset」。"""
    report = _report_from_raw(_f3_raw(_f3_all_offsets_null_no_error))
    r3 = _f3_assertion(report, "R3_retry_evidence")
    assert r3["state"] == "fail" and r3["error_patterns"] == ["no_retry"]
    assert r3["evidence"]["extra"]["classification_source"] == "case:fault_point_not_reached"
    r4 = _f3_assertion(report, "R4_no_param_hallucination")
    assert r4["state"] == "fail" and r4["error_patterns"] == ["param_type_mismatch"]


def test_f3_td018_explicit_null_offset_on_failed_step_is_unscored():
    """C8：缺失键与显式 null 同属「判据缺输入」，不能因写法不同落到不同状态。"""
    report = _report_from_raw(_f3_raw(_f3_null_error_offset))
    r3 = _f3_assertion(report, "R3_retry_evidence")
    assert r3["state"] == "unscored" and r3["reason_code"] == "trace_invalid"
    assert "params.offset" in r3["evidence"]["observed"]["ambiguous_match"]


def test_f3_td018_after_filter_targets_only_candidates_after_last_match():
    """M3 反例：更早的同名 success 调用也缺字段时，归因仍须指向失败步候选。"""
    base = _f3_assertion(
        _report_from_raw(_f3_raw(_f3_missing_retry_success_param)), "R3_retry_evidence")
    report = _report_from_raw(_f3_raw(_f3_missing_offset_before_and_on_retry))
    r3 = _f3_assertion(report, "R3_retry_evidence")
    assert r3["state"] == "unscored" and r3["reason_code"] == "trace_invalid"
    assert (r3["evidence"]["observed"]["ambiguous_match"]
            == base["evidence"]["observed"]["ambiguous_match"])
    assert "call_index=1" not in r3["evidence"]["observed"]["ambiguous_match"]


# --- F3-P2 边界锁 ----------------------------------------------------------

def test_f3_td018_explicit_null_http_status_on_failed_step_is_unscored():
    """C9：http_status=null 与缺键同判；否则同一数据缺陷会因写法不同落到不同状态。"""
    report = _report_from_raw(_f3_raw(_f3_null_error_http_status))
    r3 = _f3_assertion(report, "R3_retry_evidence")
    assert r3["state"] == "unscored" and r3["reason_code"] == "trace_invalid"
    assert r3["error_patterns"] == []
    assert "http_status" in r3["evidence"]["observed"]["ambiguous_match"]


# ---------------------------------------------------------------------------
# F4 / TD-019a、F5 / TD-019b：R4 两处缺口的证据锁（只钉现状，未修复）
#
# F4 未知工具屏蔽已观测违规：unknown_tools 非空即整体 unscored，已知工具上
#    已观测到的 violations 被丢弃，observed 只剩 unknown_tools（对照 S1）。
# F5 空集 pass 计入掌握度：checked_calls=0 判 pass，attribution.pass.
#    counts_toward_mastery=true，空集 pass 拿满 R4 权重 1 → TD-028 分数序
#    倒置（A1 尝试目标工具但参数全错 0.5 < 一次目标工具都没调 0.75）。
# 下面断言的是**现状**（缺陷可复现），不是**应然**；修复落地时按 TD-008 /
# F1 / F3 惯例反转成正向锁、函数不删除。修复口径待人拍板（§31）。
# ---------------------------------------------------------------------------


def _f45_items(*pairs) -> list[dict]:
    """(name, params) -> 合规 tool_calls（call_index 连续、status 合法）。"""
    return [{"call_index": i, "name": n, "params": p, "status": "success",
             "result_summary": ""}
            for i, (n, p) in enumerate(pairs, 1)]


def _f45_r4_unit(items: list[dict]) -> dict:
    """只跑 R4 执行器（不组装报告）。"""
    trace = {"agent_id": "probe", "final_status": "completed", "tool_calls": items}
    return ae.evaluate_assertion(
        ae.load_r4_spec(), trace,
        schemas=ae.load_tool_schemas(ae.load_r4_spec()["expected"]["schema_ref"]))


def _f45_report(items: list[dict]):
    """从 raw-perfect 派生全链路报告（audit 重算，避开 F1 对账入口）。"""
    data = copy.deepcopy(raw("raw-perfect"))
    data["trace"]["tool_calls"] = items
    data["trace_audit"] = tp._derive_trace_audit(data["trace"], _minimum())
    return _report_from_raw(data)


# --- F4 / TD-019a ----------------------------------------------------------


def test_f4_td019a_violation_visible_without_unknown_tool():
    """对照基线（应然，修复后不得变）：无未知工具时已观测违规可见。"""
    r = _f45_r4_unit(_f45_items(("fetch_orders", {"offset": 0, "limit": 99})))
    assert r["state"] == "fail"
    assert r["error_patterns"] == ["param_out_of_range"]
    assert [v["kind"] for v in r["evidence"]["observed"]["violations"]] == ["out_of_range"]


def test_f4_td019a_unknown_tool_no_longer_masks_observed_violation():
    """① 反转【现状】→ 正向：能判的判，未知工具降为 evidence 注记。"""
    r = _f45_r4_unit(_f45_items(("fetch_orders", {"offset": 0, "limit": 99}),
                                ("imaginary_tool", {})))
    assert r["state"] == "fail"
    assert r["error_patterns"] == ["param_out_of_range"]
    obs = r["evidence"]["observed"]
    assert [v["kind"] for v in obs["violations"]] == ["out_of_range"]
    assert obs["unknown_tools"] == [{"call_index": 2, "name": "imaginary_tool"}]
    assert obs["checked_calls"] == 1  # 只数对照过 schema 的调用


def test_f4_td019a_all_observed_violations_survive_unknown_tool():
    """多类违规同时存在时仍全量保留，分类只取首命中。"""
    r = _f45_r4_unit(_f45_items(
        ("fetch_orders", {"offset": 0, "limit": 99}),
        ("fetch_orders", {"offset": 0, "limit": "10"}),
        ("fetch_orders", {"offset": -1}),
        ("imaginary_tool", {})))
    assert r["state"] == "fail"
    obs = r["evidence"]["observed"]
    assert sorted({v["kind"] for v in obs["violations"]}) == [
        "missing_required", "out_of_range", "type_mismatch"]
    assert len(obs["unknown_tools"]) == 1


def test_f4_td019a_unknown_tool_does_not_shrink_validation_scope():
    """未知工具不改变受检集合：已知工具上的违规照常判，哪怕它不是目标工具。"""
    r = _f45_r4_unit(_f45_items(("write_report", {"path": 3, "content": "{}"}),
                                ("imaginary_tool", {})))
    assert r["state"] == "fail"
    assert r["error_patterns"] == ["param_type_mismatch"]
    assert r["evidence"]["observed"]["violations"][0]["tool"] == "write_report"


def test_f4_td019a_unknown_tool_alone_is_unscored():
    """P-B 基线：只有未知工具时 unscored/trace_invalid，修复后必须仍成立。"""
    r = _f45_r4_unit(_f45_items(("imaginary_tool", {})))
    assert r["state"] == "unscored" and r["reason_code"] == "trace_invalid"
    assert r["evidence"]["observed"]["unknown_tools"][0]["name"] == "imaginary_tool"


# --- F5 / TD-019b ----------------------------------------------------------


def test_f5_td019b_zero_calls_is_unscored():
    """① 反转【现状】→ 正向：空集不再平凡 pass，判 unscored/trace_invalid。"""
    r = _f45_r4_unit([])
    assert r["state"] == "unscored" and r["reason_code"] == "trace_invalid"
    assert r["evidence"]["observed"] == {
        "target_tools": ["fetch_orders"], "target_call_count": 0, "checked_calls": 0}


def test_f5_td019b_no_target_calls_does_not_count_toward_mastery():
    """② 空集 pass 不再计入掌握度：A0 的 credit_pass_weight 3→2（只剩 R2），ratio 0.75→0.5。"""
    report = _f45_report([])
    assert by_id(report)["R4_no_param_hallucination"]["state"] == "unscored"
    assert by_id(report)["R4_no_param_hallucination"]["error_patterns"] == []
    assert report["score"]["credit_pass_weight"] == 2
    assert report["score"]["ratio"] == 0.5


def test_f5_td019b_score_inversion_a1_a0_a2_are_level():
    """③ TD-028 销项：4 次目标调用全错、零调用、只调无关工具，三者同为 0.5。"""
    a1 = _report_from_raw(_f3_raw(_f3_all_hallucinated))
    a2 = _report_from_raw(_f3_raw(_f3_only_write_report))
    a0 = _f45_report([])
    assert a1["score"]["ratio"] == 0.5
    assert a2["score"]["ratio"] == 0.5
    assert a0["score"]["ratio"] == 0.5
    assert not (a1["score"]["ratio"] < a2["score"]["ratio"]), "分数序倒置必须消失"


def test_f5_td019b_zero_calls_scores_05():
    """③ 新证据反转：零调用不再拿 0.75，落到与 A1 同档。"""
    report = _f45_report([])
    assert report["score"]["ratio"] == 0.5
    r4 = by_id(report)["R4_no_param_hallucination"]
    assert r4["state"] == "unscored" and r4["evidence"]["observed"]["target_call_count"] == 0


def test_f5_td019b_non_target_call_alone_is_unscored():
    """F5-B 口径：受检无关工具不等于覆盖目标技能。"""
    r = _f45_r4_unit(_f45_items(("write_report",
                                 {"path": "/workspace/report.json", "content": "{}"})))
    assert r["state"] == "unscored" and r["reason_code"] == "trace_invalid"
    assert r["evidence"]["observed"] == {
        "target_tools": ["fetch_orders"], "target_call_count": 0, "checked_calls": 1}


def test_f5_td019b_observed_violation_beats_coverage_gap():
    """能判的判：无关工具上已观测的违规仍判 fail，不被覆盖率不足吞掉。"""
    r = _f45_r4_unit(_f45_items(("write_report", {"content": "{}"})))
    assert r["state"] == "fail"
    assert r["error_patterns"] == ["param_missing_required"]
    assert r["evidence"]["observed"]["checked_calls"] == 1
    assert "target_call_count" not in r["evidence"]["observed"]


def test_f5_td019b_target_tools_must_be_declared():
    s = copy.deepcopy(ae.load_r4_spec())
    s["expected"].pop("target_tools")
    with pytest.raises(ae.EvaluatorError, match="target_tools"):
        ae._validate_param_schema_spec(s)
    with pytest.raises(ae.EvaluatorError, match="target_tools"):
        ae.evaluate_assertion(s, {"agent_id": "x", "final_status": "completed",
                                  "tool_calls": []})


def test_f5_td019b_target_tools_must_stay_inside_schema_ref():
    s = copy.deepcopy(ae.load_r4_spec())
    s["expected"]["target_tools"] = ["not_in_tools"]
    with pytest.raises(ae.EvaluatorError, match="不在 schema_ref"):
        ae.evaluate_assertion(s, {"agent_id": "x", "final_status": "completed",
                                  "tool_calls": []})


# ---------------------------------------------------------------------------
# F9 / TD-020：配置校验缺口 —— 落地后的正向锁（round-33 只读复现，round-36 Step1 / round-37 Step2b 落地）
#
# ① / ② 已在 rubric_layers() 加载期收口；③ 拍板 b：agent_id 降为可选元数据，
# 从 trace_requirements.minimum 删除声明，不改分数。
# 边界对照（修复后必须仍成立）：S4 执行器两路径、S6/S7 跨层与结果层重复。
# ---------------------------------------------------------------------------


def _f9_dup_task(layer: str, rid: str) -> dict:
    t = copy.deepcopy(TASK)
    t[f"rubric_{layer}"].append(
        copy.deepcopy(next(r for r in t[f"rubric_{layer}"] if r["id"] == rid)))
    return t


def _f9_typo_dep_task() -> dict:
    t = copy.deepcopy(TASK)
    next(r for r in t["rubric_credit"] if r["id"] == "R2_report_correct")["depends_on"] = ["R1_typo"]
    return t


def _f9_report(task: dict, data: dict | None = None) -> dict:
    """组装 + schema 校验；不抛 = 放行（现状锁依赖这一点）。"""
    rep = dr.build_report(task, data if data is not None else raw("raw-perfect"))
    dr.validate_report(rep)
    return rep


def test_f9_td020_duplicate_gate_id_is_accepted():
    """①【正向锁】gate 内复制 R1 → 加载期拒绝，不再产出 6 条断言。"""
    t = _f9_dup_task("gate", "R1_file_exists")
    with pytest.raises(dr.ReportError, match="gate 层断言 id 重复"):
        dr.rubric_layers(t)
    with pytest.raises(dr.ReportError, match="R1_file_exists"):
        _f9_report(t)


def test_f9_td020_duplicate_credit_id_only_trips_weight_reconciliation():
    """①【正向锁】credit 内复制 R3 → 先被 id 唯一性拒绝，不落到权重对账。"""
    t = _f9_dup_task("credit", "R3_retry_evidence")
    with pytest.raises(dr.ReportError, match="credit 层断言 id 重复") as exc:
        _f9_report(t)
    assert "R3_retry_evidence" in str(exc.value)


def test_f9_td020_duplicate_credit_id_passes_with_matched_total_weight():
    """①【正向锁】同步改 total_weight 也穿不过：id 唯一性先于权重对账。"""
    t = _f9_dup_task("credit", "R3_retry_evidence")
    t["scoring"]["score"]["credit"]["total_weight"] = 5
    with pytest.raises(dr.ReportError, match="credit 层断言 id 重复"):
        _f9_report(t)


def test_f9_td020_typo_dep_raises_bare_keyerror():
    """②【正向锁】拼错 depends_on 在加载期抛含 typo id 的 ReportError，不是 KeyError。"""
    data = copy.deepcopy(raw("raw-perfect"))
    for x in data["assertion_results"]:
        if x["id"] == "R2_report_correct":
            x["state"] = "blocked"
            x["reason_code"] = "dependency_blocked"
            x["error_patterns"] = []
    with pytest.raises(dr.ReportError, match="R1_typo"):
        dr.build_report(_f9_typo_dep_task(), data)


def test_f9_td020_typo_dep_is_silent_when_not_blocked():
    """②【正向锁】非 blocked 路径也在加载期拒绝：引用完整性不依赖运行状态。"""
    with pytest.raises(dr.ReportError, match="R1_typo"):
        _f9_report(_f9_typo_dep_task())


def test_f9_td020_depends_on_key_is_required():
    """② 新证据：key 拼错（depends_onn）等于没声明，必须在加载期拒绝而不是静默丢依赖。"""
    t = copy.deepcopy(TASK)
    r2 = next(r for r in t["rubric_credit"] if r["id"] == "R2_report_correct")
    r2["depends_onn"] = r2.pop("depends_on")
    with pytest.raises(dr.ReportError, match="depends_on"):
        dr.rubric_layers(t)


def test_f9_td020_depends_on_must_be_string_list():
    """② 新证据：depends_on 写成裸字符串 → 加载期拒绝，而不是逐字符查结果后 KeyError。"""
    t = copy.deepcopy(TASK)
    next(r for r in t["rubric_credit"] if r["id"] == "R2_report_correct")["depends_on"] = "R1_file_exists"
    with pytest.raises(dr.ReportError, match="非空字符串数组"):
        dr.rubric_layers(t)


def test_f9_td020_depends_on_on_unsupported_type_is_rejected():
    """② 新证据：只有 json_field_equals 执行依赖折叠；其他类型声明 depends_on 是死声明。"""
    t = copy.deepcopy(TASK)
    next(r for r in t["rubric_credit"] if r["id"] == "R3_retry_evidence")["depends_on"] = [
        "R4_no_param_hallucination"]
    with pytest.raises(dr.ReportError, match="不支持 depends_on"):
        dr.rubric_layers(t)


def test_f9_td020_executor_dependency_paths_are_structured():
    """②【边界锁，修复后仍须成立】执行器侧两种行为都是结构化结果，与组装器不同。"""
    s = ae.load_r2_spec()
    good = {"/workspace/report.json": json.dumps(dict(s["expected"]))}
    with pytest.raises(ae.EvaluatorError, match="dependency_results"):
        ae.evaluate_assertion(s, None, artifacts=good)
    r = ae.evaluate_assertion(s, None, artifacts=good,
                              dependency_results={"R1_file_exists": {"state": "fail"}})
    assert r["state"] == "blocked" and r["reason_code"] == "dependency_blocked"
    assert r["evidence"]["extra"]["blocked_by"] == ["R1_file_exists"]


def test_f9_td020_missing_agent_id_keeps_clean_audit():
    """③【正向锁】声明已删除：trace.agent_id 是可选元数据，缺失不改变 audit 与分数。

    身份以报告层 submission.agent_id 为准（DTO 必填）；与 P9 锁分工：那条锁 ratio，
    这条锁「YAML 不再声明 agent_id」+ audit 干净。
    """
    assert "agent_id" not in TASK["trace_requirements"]["minimum"]
    data = copy.deepcopy(raw("raw-perfect"))
    data["trace"].pop("agent_id")
    rep = _report_from_raw(data)
    assert rep["trace_audit"]["schema_valid"] is True
    assert rep["trace_audit"]["invalid_reasons"] == []
    assert rep["score"]["ratio"] == 1.0


def test_f9_td020_trace_agent_id_mismatch_is_not_a_rule():
    """③【正向锁】trace.agent_id 与 submission.agent_id 不一致也不判：它不是评分信号。"""
    data = copy.deepcopy(raw("raw-perfect"))
    data["trace"]["agent_id"] = "someone-else"
    rep = _report_from_raw(data)
    assert rep["trace_audit"]["schema_valid"] is True
    assert rep["trace_audit"]["invalid_reasons"] == []
    assert rep["score"]["ratio"] == 1.0


def test_f9_td020_cross_layer_and_result_duplicates_are_rejected():
    """【边界锁，修复后仍须成立】缺口精确在「层内配置」：跨层与结果层重复都已被拒。"""
    t = copy.deepcopy(TASK)
    t["rubric_credit"].append(copy.deepcopy(t["rubric_gate"][0]))
    with pytest.raises(dr.ReportError, match="同时出现"):
        dr.rubric_layers(t)
    data = copy.deepcopy(raw("raw-perfect"))
    data["assertion_results"].append(copy.deepcopy(data["assertion_results"][0]))
    with pytest.raises(dr.ReportError, match="重复"):
        dr.build_report(TASK, data)



# ---------------------------------------------------------------------------
# TD-035 ①：tool_calls.required 是无执行者的死声明（review-response-v1.md §43）
# ---------------------------------------------------------------------------


def _minimum_with_required(value):
    """造一份 minimum 副本：True / False / None=删 key（不落盘）。"""
    req = copy.deepcopy(TASK["trace_requirements"]["minimum"])
    if value is None:
        req["tool_calls"].pop("required", None)
    else:
        req["tool_calls"]["required"] = value
    return req


def test_td035_yaml_has_no_tool_calls_required_declaration():
    """配置锁：现役 YAML 不留无执行者的声明；final_status.required 有执行者故保留。"""
    minimum = TASK["trace_requirements"]["minimum"]
    assert "required" not in minimum["tool_calls"]
    assert minimum["final_status"]["required"] is True


def test_td035_required_key_has_no_consumer():
    """行为锁：三态（true / false / 删 key）结果必须全等，钉住「该 key 无执行者」。"""
    trace = {"agent_id": "probe", "final_status": "completed"}
    results = [tp._trace_audit_issues(trace, _minimum_with_required(v))
               for v in (True, False, None)]
    assert results == [["missing_required_field"]] * 3


def test_td035_missing_tool_calls_still_flagged_without_declaration():
    """正向锁：删声明后行为零变化——缺 tool_calls 仍报结构损坏，派生值一致。"""
    req = TASK["trace_requirements"]["minimum"]
    assert "required" not in req["tool_calls"]
    trace = {"agent_id": "probe", "final_status": "completed"}
    assert tp._trace_audit_issues(trace, req) == ["missing_required_field"]
    derived = tp._derive_trace_audit(trace, req)
    assert derived["schema_valid"] is False
    assert derived["invalid_reasons"] == ["missing_required_field"]
    assert derived["call_count"] == 0
