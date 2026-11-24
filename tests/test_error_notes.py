"""19 条错误模式的人话模板：结构、覆盖、与真实执行器的一致性。

这套测试的全部意义是**别让模板变成第二真相源**：

1. 每条模式都要有模板，且必须有一条无条件兜底——没有兜底的分支在真实证据上会
   突然无话可说，而页面只剩一个四字标签。
2. 渲染数据来自**真实执行器**（`assertion_evaluator` + 真实任务 YAML），不是手写的
   evidence 字面量。手写夹具曾让「假重试」看起来没问题，同样也会让模板看起来没问题。
3. 填不满槽必须抛错（`NoteError`），由报告层降级并标 `degraded`；测试要求 19 条零降级。
4. 句子里不许出现内部词：断言 id、字段名、分类来源、未替换的占位符大括号。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

import pytest
import yaml

from arena import assertion_evaluator as ae
from arena import diagnosis_report as dr
from arena import error_notes as en
from arena import trace_pipeline as tp

ROOT = Path(__file__).resolve().parents[1]
DBG = ROOT / "tasks" / "dbg-001-order-report.yaml"
FIX = ROOT / "tasks" / "fix-001-token-expiry.yaml"
ASK = ROOT / "tasks" / "ask-001-report-handover.yaml"


# ---------------------------------------------------------------------------
# 用真实执行器造出 19 条模式各一份证据
# ---------------------------------------------------------------------------


def _call(i: int, name: str, params: Mapping[str, Any],
          status: str = "success", http: int | None = None) -> dict:
    call = {"call_index": i, "name": name, "params": dict(params), "status": status}
    if http is not None:
        call["http_status"] = http
    return call


def _trace(*calls: dict) -> dict:
    return {"final_status": "completed", "tool_calls": list(calls)}


@pytest.fixture(scope="module")
def cases() -> dict[str, dict]:
    """pattern_id -> 触发该模式的那条断言结果（真实执行器产出）。

    同一模式可能被多条用例命中，这里只留第一条；`test_every_pattern_is_reachable`
    反过来保证 19 条全都被造到。
    """
    vocab = dr.load_vocabulary()
    task = dr.load_task(DBG)
    r1 = ae.load_r1_spec(DBG)
    r2 = ae.load_r2_spec(DBG)
    r3 = next(s for s in task["rubric_credit"] if s["id"] == "R3_retry_evidence")
    r4 = ae.load_r4_spec(DBG)
    r5 = ae.load_r5_spec(DBG)
    fr2 = next(s for s in dr.load_task(FIX)["rubric_credit"] if s["id"] == "R2_diagnosis_correct")
    ar6 = next(s for s in dr.load_task(ASK)["rubric_credit"] if s["id"] == "R6_ask_before_act")
    dep = {"R1_file_exists": {"state": "pass"}}

    results = [
        ae.evaluate_file_exists(r1, {}, vocab),
        ae.evaluate_file_exists(r1, {"/workspace/report.json": ""}, vocab),
        ae.evaluate_json_field_equals(
            r2, {"/workspace/report.json": '{"valid_count": 9, "total_amount": 1837.99}'}, vocab, dep),
        ae.evaluate_json_field_equals(
            r2, {"/workspace/report.json": '{"valid_count": 17, "total_amount": 861.24}'}, vocab, dep),
        ae.evaluate_json_field_equals(
            r2, {"/workspace/report.json": '{"total_amount": 1837.99}'}, vocab, dep),
        ae.evaluate_json_field_equals(
            r2, {"/workspace/report.json": "{bad"}, vocab, dep),
        ae.evaluate_json_field_equals(
            fr2, {"/workspace/diagnosis.json": '{"root_cause": "null_deref", "offending_line": 20}'},
            vocab, dep),
        ae.evaluate_json_field_equals(
            fr2, {"/workspace/diagnosis.json": '{"root_cause": "type_mismatch", "offending_line": 12}'},
            vocab, dep),
        ae.evaluate_json_field_equals(
            fr2, {"/workspace/diagnosis.json": '{"root_cause": "type_mismatch"}'}, vocab, dep),
        ae.evaluate(r3, _trace(
            _call(1, "fetch_orders", {"offset": 0, "limit": 10}),
            _call(2, "fetch_orders", {"offset": 10, "limit": 10}, "error", 500)), vocab),
        ae.evaluate(r3, _trace(), vocab),
        ae.evaluate(r3, _trace(
            _call(1, "fetch_orders", {"offset": 0, "limit": 10}),
            _call(2, "fetch_orders", {"offset": 10, "limit": 10}, "error", 500),
            _call(3, "fetch_orders", {"offset": 0, "limit": 10})), vocab),
        ae.evaluate(r3, _trace(
            _call(1, "fetch_orders", {"offset": 10, "limit": 10}, "error", 500),
            _call(2, "fetch_orders", {"offset": 10, "limit": 10}, "error", 500),
            _call(3, "fetch_orders", {"offset": 10, "limit": 10}, "error", 500)), vocab),
        ae.evaluate(ar6, _trace(_call(1, "fetch_orders", {"offset": 0, "limit": 10})), vocab),
        ae.evaluate(ar6, _trace(
            _call(1, "fetch_orders", {"offset": 0, "limit": 10}),
            _call(2, "ask_policy", {"question": "口径?"})), vocab),
        ae.evaluate_param_schema(r4, {"tool_calls": [
            _call(1, "fetch_orders", {"offset": 0, "limit": 3, "page": 2})]}, vocab),
        ae.evaluate_param_schema(r4, {"tool_calls": [
            _call(1, "fetch_orders", {"offset": 0})]}, vocab),
        ae.evaluate_param_schema(r4, {"tool_calls": [
            _call(1, "fetch_orders", {"offset": 0, "limit": "10"})]}, vocab),
        ae.evaluate_param_schema(r4, {"tool_calls": [
            _call(1, "fetch_orders", {"offset": 0, "limit": 20})]}, vocab),
        ae.evaluate_final_status(r5, {"final_status": "crashed"}, vocab),
        ae.evaluate_final_status(r5, {"final_status": "timeout"}, vocab),
    ]

    out: dict[str, dict] = {}
    for result in results:
        assert result["state"] == "fail", result.get("id")
        for pid in result["error_patterns"]:
            out.setdefault(pid, result)
    return out


# ---------------------------------------------------------------------------
# 词表结构
# ---------------------------------------------------------------------------


def test_every_pattern_has_a_template():
    """19 条一条都不能少。少一条 = 那个错误类型的学生只会看到四个字。"""
    templates = en.load_templates()
    vocab = yaml.safe_load(en.VOCAB_PATH.read_text(encoding="utf-8"))["patterns"]
    assert set(templates) == {p["pattern_id"] for p in vocab}
    assert len(templates) == 19


def test_every_pattern_is_reachable_by_the_evaluator(cases):
    """模板引用的 19 条模式，真实执行器都能判出来——判据改了而模板没跟，这里会红。"""
    assert set(cases) == set(en._FACTS)


def test_last_branch_is_the_fallback(tmp_path):
    """兜底必须在最后一条：放在中间会把后面的分支全挡住。"""
    vocab = yaml.safe_load(en.VOCAB_PATH.read_text(encoding="utf-8"))
    for p in vocab["patterns"]:
        blocks = p["human"]
        assert blocks[-1].get("when") is None, p["pattern_id"]
        assert all(b.get("when") is not None for b in blocks[:-1]), p["pattern_id"]
        assert all(str(b.get("text", "")).strip() for b in blocks), p["pattern_id"]


def test_template_without_fallback_is_rejected(tmp_path):
    vocab = yaml.safe_load(en.VOCAB_PATH.read_text(encoding="utf-8"))
    for p in vocab["patterns"]:
        if len(p["human"]) < 2:
            continue
        broken = json.loads(json.dumps(vocab, ensure_ascii=False))
        target = next(x for x in broken["patterns"] if x["pattern_id"] == p["pattern_id"])
        target["human"] = target["human"][:-1]  # 砍掉兜底
        path = tmp_path / "vocab.yaml"
        path.write_text(yaml.safe_dump(broken, allow_unicode=True), encoding="utf-8")
        with pytest.raises(en.NoteError, match="兜底"):
            en.load_templates(path)
        return


def test_missing_template_is_rejected(tmp_path):
    vocab = yaml.safe_load(en.VOCAB_PATH.read_text(encoding="utf-8"))
    broken = json.loads(json.dumps(vocab, ensure_ascii=False))
    broken["patterns"][0].pop("human")
    path = tmp_path / "vocab.yaml"
    path.write_text(yaml.safe_dump(broken, allow_unicode=True), encoding="utf-8")
    with pytest.raises(en.NoteError, match="human"):
        en.load_templates(path)


# ---------------------------------------------------------------------------
# 渲染：19 条逐条
# ---------------------------------------------------------------------------

_INTERNAL = ("R1_", "R2_", "R3_", "R4_", "R5_", "R6_", "valid_count", "total_amount",
             "root_cause", "offending_line", "matched_steps", "case:", "offset=",
             "{", "}", "None", "True", "False")


@pytest.mark.parametrize("pattern_id", sorted(en._FACTS))
def test_renders_without_degrading(pattern_id, cases):
    """每一条都能只靠本次证据说出一句完整的话，且不夹带内部词。"""
    result = cases[pattern_id]
    note = en.render(pattern_id, result)
    assert note["pattern_id"] == pattern_id
    text = note["text"]
    assert 8 <= len(text) <= 80, text
    for token in _INTERNAL:
        assert token not in text, (pattern_id, token, text)
    assert text.endswith("。"), text
    hint = note.get("hint")
    assert hint and hint.endswith("。"), (pattern_id, hint)


def test_texts_are_distinct(cases):
    """19 条不许共用一句话——共用就说明模板没按模式分开写。"""
    texts = [en.render(pid, cases[pid])["text"] for pid in sorted(cases)]
    assert len(set(texts)) == len(texts)


def test_parse_error_branch_replaces_missing_field_branch():
    """同一模式的两条分支：整份解析失败与只缺一项，说的不是同一句话。"""
    templates = en.load_templates()
    vocab = dr.load_vocabulary()
    r2 = ae.load_r2_spec(DBG)
    dep = {"R1_file_exists": {"state": "pass"}}
    parse = ae.evaluate_json_field_equals(
        r2, {"/workspace/report.json": "{bad"}, vocab, dep)
    assert parse["error_patterns"] == ["report_missing_field"]
    parse_text = en.render("report_missing_field", parse, templates)["text"]
    assert "JSON" in parse_text

    partial = ae.evaluate_json_field_equals(
        r2, {"/workspace/report.json": '{"valid_count": 17}'}, vocab, dep)
    text = en.render("report_missing_field", partial, templates)["text"]
    assert "JSON" not in text
    assert "合计金额" in text
    assert text != parse_text


def test_missing_evidence_raises_instead_of_inventing(cases):
    """证据缺字段就抛错：宁可降级成词表原话，也不许给槽补一个默认值。"""
    broken = json.loads(json.dumps(cases["retry_wrong_params"]))
    broken["evidence"]["extra"].pop("matched_steps")
    with pytest.raises(en.NoteError, match="matched_steps"):
        en.render("retry_wrong_params", broken)


def test_unknown_field_label_raises(cases):
    """字段名没有中文叫法就抛错——回退成英文 id 等于把技术口径漏回页面。"""
    broken = json.loads(json.dumps(cases["report_missing_field"]))
    for diff in broken["evidence"]["extra"]["diffs"]:
        diff["field"] = "some_new_field"
    with pytest.raises(en.NoteError, match="中文叫法"):
        en.render("report_missing_field", broken)


def test_notes_for_degrades_loudly(cases):
    """填不满时退回词表 description 并打 degraded 标记：不静默丢，也不现编。"""
    broken = json.loads(json.dumps(cases["retry_wrong_params"]))
    broken["evidence"]["extra"].pop("matched_steps")
    notes = en.notes_for(broken)
    assert len(notes) == 1
    assert notes[0]["degraded"] is True
    assert notes[0]["text"] == en.load_pattern_descriptions()["retry_wrong_params"]


# ---------------------------------------------------------------------------
# 报告层：五份夹具零降级
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_fixture_reports_carry_no_degraded_notes(name):
    """真跑五份夹具：任何一条降级都说明模板与执行器证据形状脱节。"""
    report = tp.run_fixture(name)
    degraded = [
        (a["id"], n["pattern_id"])
        for a in report["assertions"]
        for n in (a.get("pattern_notes") or ())
        if n.get("degraded")
    ]
    assert degraded == [], f"{name}: {degraded}"


def test_report_assertions_carry_title_and_notes():
    """报告要带标题与人话；这是页面不再显示机器码的前提。"""
    report = tp.run_fixture("raw-fake-retry")
    by_id = {a["id"]: a for a in report["assertions"]}
    assert by_id["R2_report_correct"]["title"] == "结论是否与判据一致"
    notes = by_id["R2_report_correct"]["pattern_notes"]
    assert [n["pattern_id"] for n in notes] == ["wrong_valid_count", "wrong_total_amount"]
    assert notes[0]["text"].startswith("有效条数你填了 9")
    assert notes[1]["hint"]
    # 通过项不产出人话：没有失败就没有「错在哪」
    assert "pattern_notes" not in by_id["R1_file_exists"]
    assert by_id["R1_file_exists"]["title"] == "交付物是否存在"


def test_pass_assertions_never_get_pattern_notes():
    report = tp.run_fixture("raw-perfect")
    assert all("pattern_notes" not in a for a in report["assertions"])


def test_display_labels_carry_field_names_for_diffs():
    """差异行的字段中文名随报告下发，页面侧不再自带映射表。"""
    report = tp.run_fixture("raw-fake-retry")
    assert report["display_labels"]["fields"] == {
        "total_amount": "合计金额",
        "valid_count": "有效条数",
    }


def test_every_type_has_a_chinese_title():
    """每种断言类型都要有中文标题，否则页面上又会出现英文 type。"""
    task = dr.load_task(DBG)
    types = {s["type"] for s in sum(dr.rubric_layers(task), [])}
    missing = sorted(types - set(en.TYPE_LABELS))
    assert missing == []


def test_llm_judge_catalog_does_not_leak_templates():
    """人话模板不进给模型的清单：给了它，模型就把模板抄成「独立观察」。"""
    from arena import llm_judge

    catalog = llm_judge.load_pattern_catalog()
    assert catalog, "清单为空会让判分模型没有任何合法取值"
    assert set(catalog[0]) == {"pattern_id", "label", "description"}
    text = llm_judge.render_catalog(catalog)
    assert "human" not in text
    assert "有效条数你填了" not in text
