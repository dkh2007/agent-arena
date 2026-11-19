"""D 口径：三态进 JSON 字段，notes 只写中性人话，不进分数。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from arena import reconciliation as rc  # noqa: E402
from arena import reconciliation_report as rr  # noqa: E402
from arena import diagnosis_report as dr  # noqa: E402


def _result(status):
    return {
        "status": status,
        "reason": "x",
        "tolerance": 0.01,
        "derived_fields": [],
        "checked": [{"field": "n", "value": 1.0, "found": status == "verified"}],
        "trace_number_count": 3,
        "counts_as_evidence": status == "verified",
    }


def _report():
    return {
        "score": {"ratio": 1.0},
        "assertions": [],
        "process_fact": {"required": True, "submitted": True, "missing": False},
        "report_attribution": {"counts_toward_mastery": True},
    }


def test_projection_drops_diagnostic_fields():
    out = rr.project_verdict(_result("verified"))
    assert set(out) <= {"status", "checked", "counts_as_evidence", "reason"}
    assert "tolerance" not in out and "trace_number_count" not in out


@pytest.mark.parametrize("status", rr.VALID_STATUSES)
def test_student_notes_never_accuse(status):
    note = rr.student_note(status)
    for banned in ("编造", "欺诈", "作弊", "造假", "撒谎"):
        assert banned not in note, (status, note)


def test_mismatch_note_says_only_neutral_fact():
    note = rr.student_note("mismatch")
    assert "复核" in note and "对应出处" in note


def test_unverifiable_is_not_a_wrong_answer():
    note = rr.student_note("unverifiable")
    assert "不构成答错" in note


def test_attach_does_not_touch_score_or_process_fact():
    import copy
    report = _report()
    # 深拷贝快照：save 同一对象引用会让"往 score 里加键"的变异自证通过。
    before = copy.deepcopy((report["score"], report["process_fact"], report["report_attribution"]))
    rr.attach(report, _result("mismatch"))
    assert report["score"] == before[0]
    assert report["process_fact"] == before[1]
    assert report["report_attribution"] == before[2]
    assert report["reconciliation"]["status"] == "mismatch"
    assert any("复核" in n for n in report["notes"])


def test_attach_is_idempotent_on_notes():
    report = _report()
    rr.attach(report, _result("verified"))
    rr.attach(report, _result("verified"))
    assert sum("过程对账" in n for n in report["notes"]) == 1


def test_illegal_status_rejected():
    with pytest.raises(ValueError):
        rr.project_verdict({"status": "maybe"})


def test_projected_verdict_passes_schema_fragment():
    """投影出的字段形状能过 schema 里 reconciliation 那一段（三态 + 不许多字段）。"""
    import jsonschema
    schema = dr.load_schema()
    frag = schema["properties"]["reconciliation"]
    for status in rr.VALID_STATUSES:
        jsonschema.Draft202012Validator(frag).validate(rr.project_verdict(_result(status)))


def test_real_reconcile_result_can_be_attached():
    """真跑一次 reconcile，三态进字段不报错、不污染分数。"""
    trace = {"tool_calls": [{"name": "f", "params": {"n": 1}, "status": 200,
                             "result_summary": {"total": 5.0}}]}
    r = rc.reconcile({"total": 5.0}, trace)
    report = _report()
    rr.attach(report, r)
    assert report["reconciliation"]["status"] == "verified"
    assert report["score"]["ratio"] == 1.0


def test_absent_field_means_not_run():
    """字段缺失 = 未做对账，不是通过。这个语义由 schema description 固定。"""
    report = _report()
    assert "reconciliation" not in report
