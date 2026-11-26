"""报告页（B-48）的约束测试：给人看的那一页，不能夹带机器码。

两条硬规则（都在 `docs/android-ui-handoff.md` §4.6 定过，这里在 HTML 上再锁一次）：

1. **不出现断言 id 与引擎词**（`R2_report_correct` / `observed` / `expected`）。
   学习者要看到的是「结论是否与判据一致」，不是"哪条断言挂了"。
2. **自包含**：没有外链、没有 `<script src>`、没有 CSS 框架。
   这一页要靠微信/邮件发出去，对方双击就能看，网络条件不可控。

第三件事是转义：页面里会插入 learner 自己产出的文本（报告摘要、参数值），
一个 `<script>` 就能把整页变成注入点。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import report_html  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402


@pytest.fixture(scope="module")
def real_report() -> dict:
    """真报告：走 夹具 → 五条断言现算 → build_report → 过 schema。"""
    return run_fixture("raw-fake-retry")


@pytest.fixture(scope="module")
def page(real_report) -> str:
    return report_html.render_html(real_report, task_title="订单数据核对报告")


def test_page_has_no_assertion_ids_or_engine_words(page):
    # 只看正文：CSS 里的 `font-weight:` 不是引擎词，但它会误命中
    body = page.split("</style>", 1)[-1]
    assert not re.search(r"\bR\d+_[a-z_]+", body), "页面上出现了断言 id"
    for word in ("observed", "expected", "pattern_notes", "trace_audit",
                 "error_patterns", "weight_lost", "rubric"):
        assert word not in body, f"页面上出现了引擎词 {word!r}"


def test_page_uses_the_human_sentences_from_the_report(real_report, page):
    """报告里每条人话都得出现在页面上——否则等于把写好的大白话丢掉了。"""
    texts = [
        note["text"]
        for assertion in real_report["assertions"]
        for note in (assertion.get("pattern_notes") or [])
        if note.get("text")
    ]
    assert texts, "这夹具本来就该带人话，没有说明上游退化"
    for text in texts:
        assert report_html.html.escape(text, quote=True) in page, f"漏了这句：{text[:20]}"


def test_page_shows_a_next_step_for_each_problem(real_report, page):
    hints = [
        note["hint"]
        for assertion in real_report["assertions"]
        for note in (assertion.get("pattern_notes") or [])
        if note.get("hint")
    ]
    assert hints, "这夹具本来就该带下一步建议"
    for hint in hints:
        assert report_html.html.escape(hint, quote=True) in page


def test_page_is_self_contained(page):
    assert "<script src" not in page.lower()
    assert "<link" not in page.lower()
    assert "http://" not in page and "https://" not in page, "有外链就不是自包含"
    assert 'name="viewport"' in page, "手机上不加 viewport 会缩成一团"


def test_page_escapes_learner_text():
    """学习者自己的文本进页面，必须转义——一个 <script> 就能毁掉这一页。"""
    report = {
        "score": {"ratio": 0.5, "by_state": {"fail": 1}},
        "assertions": [{
            "id": "R9_injected",
            "state": "fail",
            "title": "<script>alert(1)</script>",
            "pattern_notes": [{"text": "<b>加粗</b>", "hint": '<img src=x onerror="x">'}],
        }],
        "trace_view": [{"call_index": 1, "name": "fetch_orders",
                        "params": {"offset": "<script>x</script>"}, "status": "success"}],
    }
    page = report_html.render_html(report)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert "<b>加粗</b>" not in page
    assert "&lt;img" in page


def test_missing_title_falls_back_without_leaking_the_id():
    """缺标题时退回一句普通话，**不能**拿 id 顶上——那正是要藏的东西。"""
    page = report_html.render_html({
        "score": {"ratio": 0.0, "by_state": {"fail": 1}},
        "assertions": [{"id": "R2_report_correct", "state": "fail"}],
    })
    assert "这项检查" in page
    assert "R2_report_correct" not in page


def test_score_and_states_are_readable(page, real_report):
    ratio = real_report["score"]["ratio"]
    assert f"{ratio * 100:.0f}%" in page
    assert "没过" in page and "通过" in page


def test_long_content_is_summarized_not_dumped():
    """`write_report` 的 content 是整份产物；原样铺进表格会把页面撑坏。"""
    page = report_html.render_html({
        "score": {"ratio": 1.0, "by_state": {"pass": 1}},
        "assertions": [],
        "trace_view": [{"call_index": 3, "name": "write_report",
                        "params": {"path": "/workspace/report.json",
                                   "content": "{\"k\": 1}" * 40}, "status": "success"}],
    })
    assert "内容（" in page
    assert "{\"k\": 1}" * 5 not in page


def test_cli_writes_the_file_and_refuses_bad_input(tmp_path, real_report, capsys):
    good = tmp_path / "report.json"
    good.write_text(json.dumps(real_report, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "report.html"
    assert report_html.main(["--report", str(good), "--out", str(out),
                             "--task", "dbg-001-order-report"]) == 0
    assert "订单数据核对报告" in out.read_text(encoding="utf-8"), "给了 --task 就该用题目的中文标题"

    assert report_html.main(["--report", str(tmp_path / "nope.json"),
                             "--out", str(out)]) == 2
    assert "找不到报告" in capsys.readouterr().out

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert report_html.main(["--report", str(bad), "--out", str(out)]) == 2


def test_all_pass_page_says_what_went_right():
    """全绿也要有内容。

    第一份真实提交就是满分（学弟让 AI 写的脚本），当时页面只有五张"通过"卡片，
    等于什么都没说。这里要求它把**通过的技能**点出来，并给一句下一步。
    """
    from arena.trace_pipeline import run_fixture

    report = run_fixture("raw-perfect")
    page = report_html.render_html(report, task_title="订单数据核对报告")
    body = page.split("</style>", 1)[-1]
    assert "全过了" in body
    for label in ("多步任务分解", "失败重试策略", "工具参数校验"):
        assert label in body, f"没点出通过的技能：{label}"
    assert "下一步" in body
    assert not re.search(r"\bR\d+_[a-z_]+", body)


def test_failed_report_does_not_claim_everything_passed():
    """只要有一条没过，"全过了"就不能出现——这句话比分数更容易被当真。"""
    from arena.trace_pipeline import run_fixture

    page = report_html.render_html(run_fixture("raw-fake-retry"))
    assert "全过了" not in page.split("</style>", 1)[-1]
