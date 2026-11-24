"""费曼复述页数据包的契约测试。

守四件事（都是「页面会不会说谎」的问题）：
1. 材料必须能追到来源：要求清单逐字等于任务 YAML，期望/实际逐字等于报告里的 diffs；
2. 报错优先于凑数：任务对不上、词表缺条目时必须抛错，不许糊一个默认值；
3. 不许有空壳提示：hint 的 lines 为空就不该出现，页面点开一片空白比没有按钮更糟；
4. 数字不装精度：17 条的 17 不能渲染成 17.00，读者一眼就知道那种数字是编的。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena.diagnosis_report import load_pattern_labels, load_skill_labels  # noqa: E402
from arena.feynman import (  # noqa: E402
    FeynmanError,
    QUESTIONS,
    _fmt,
    build_prompt,
    load_pattern_plains,
)
from arena.error_notes import FIELD_LABELS  # noqa: E402
from arena.training import asks_of  # noqa: E402

SAMPLE_REPORT = ROOT / "android" / "app" / "src" / "main" / "assets" / "sample-report.json"
TASK_FILE = ROOT / "tasks" / "dbg-001-order-report.yaml"


@pytest.fixture(scope="module")
def report():
    return json.loads(SAMPLE_REPORT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def task():
    return yaml.safe_load(TASK_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def prompt(report, task):
    return build_prompt(report, task)


# ---------------------------------------------------------------- 逐字追源


def test_prompt_carries_the_same_task_as_the_report(prompt, report):
    assert prompt["task_id"] == report["submission"]["task_id"]
    assert prompt["submission_id"] == report["submission"]["submission_id"]
    assert prompt["task_title"]


def test_asks_are_verbatim_from_task_scenario(prompt, task):
    """要求清单必须与训练单同一份实现产出——两处各摘一次，迟早一句不一样。"""
    assert prompt["asks"] == asks_of(task)
    assert len(prompt["asks"]) == 4
    assert "write_report" in prompt["asks"][-1]


def test_questions_are_the_three_fixed_prompts(prompt):
    assert [q["key"] for q in prompt["questions"]] == [q["key"] for q in QUESTIONS] == [
        "task",
        "gap",
        "fix",
    ]
    assert all(q["text"].strip() for q in prompt["questions"])


def test_gap_labels_come_from_vocabulary(prompt):
    labels = [g["skill_label"] for g in prompt["gaps"]]
    assert labels == ["多步任务分解", "失败重试策略"]
    vocab = load_skill_labels()
    assert all(label in vocab.values() for label in labels)


def test_pattern_explanations_are_verbatim_from_vocabulary(prompt):
    """「这个错法叫什么」逐字取自词表的 plain，不是 description。

    description 是 judge 判定时读的清单（带 `valid_count`、`fixture` 这类内部词），
    页面上用的是 plain。两者分开是刻意的：一个要精确到能判分，一个要人能读懂。
    """
    plains = load_pattern_plains()
    labels = load_pattern_labels()
    seen = 0
    for gap in prompt["gaps"]:
        for pattern in gap["patterns"]:
            assert pattern["label"] == labels[pattern["id"]]
            assert pattern["plain"] == plains[pattern["id"]]
            seen += 1
    assert seen == 3


def test_checks_only_contain_failed_assertions(prompt, report):
    failed_ids = [a["id"] for a in report["assertions"] if a["state"] != "pass"]
    assert [c["assertion_id"] for c in prompt["checks"]] == failed_ids


def test_check_diffs_are_verbatim_from_report(prompt, report):
    """期望/实际是学生要去核对的事实，多一个字、换一种数值写法都算改判据。"""
    raw = {
        a["id"]: a["evidence"]["extra"]["diffs"]
        for a in report["assertions"]
        if "diffs" in (a.get("evidence", {}).get("extra") or {})
    }
    for check in prompt["checks"]:
        expected_rows = raw.get(check["assertion_id"], [])
        assert len(check["diffs"]) == len(expected_rows)
        for got, want in zip(check["diffs"], expected_rows):
            # 字段名在这一层就翻成中文（数字与写法不变）：词的唯一来源是
            # error_notes.FIELD_LABELS，页面侧不再留映射表。
            assert got["field"] == FIELD_LABELS.get(want["field"], want["field"])
            assert got["expected"] == str(want["expected"])
            assert got["actual"] == str(want["actual"])


def test_scene_lines_are_verbatim_from_report(prompt, report):
    for check in prompt["checks"]:
        source = next(a for a in report["assertions"] if a["id"] == check["assertion_id"])
        raw_note = source.get("note")
        raw_why = (source.get("evidence", {}).get("extra") or {}).get("why")
        if raw_note:
            assert raw_note in check["scene"]
        if raw_why:
            assert raw_why in check["scene"]


# ---------------------------------------------------------------- 提示不许空壳


def test_every_hint_has_content(prompt):
    assert prompt["hints"]
    for hint in prompt["hints"]:
        assert hint["key"] and hint["label"]
        assert hint["lines"], f"提示 {hint['label']} 没有任何内容，不该出现在页面里"


def test_hint_keys_are_the_four_source_backed_ones(prompt):
    """本样例报告只有三条来源：题目步骤、期望/实际、错法解释。

    「当时现场」第四条依赖报告里的 `note`/`why`——按当前链路（执行器现算）重生成的
    报告一个都不带，所以这里**不该**出现空壳。有 note 时它必须出现，见下一条。
    """
    assert [h["key"] for h in prompt["hints"]] == ["asks", "diffs", "patterns"]


def test_scene_hint_appears_only_when_report_gives_note_or_why(report, task):
    """「当时现场」是数据派生的：报告写了 note 才出现，没写就不许留空壳。"""
    with_note = json.loads(json.dumps(report))
    failed = next(a for a in with_note["assertions"] if a["state"] == "fail")
    failed["note"] = "只统计了第一页 10 条，第二页从未成功拉取"
    out = build_prompt(with_note, task)
    scene = next(h for h in out["hints"] if h["key"] == "scene")
    assert scene["lines"] == ["只统计了第一页 10 条，第二页从未成功拉取"]
    blank = json.loads(json.dumps(report))
    for assertion in blank["assertions"]:
        assertion.pop("note", None)
        (assertion.get("evidence") or {}).get("extra", {}).pop("why", None)
    assert "scene" not in [h["key"] for h in build_prompt(blank, task)["hints"]]


def test_pattern_hint_disappears_when_report_has_no_patterns(report, task):
    """没有错误模式时不许留一条空壳提示——页面会点开一片空白。"""
    stripped = json.loads(json.dumps(report))
    for assertion in stripped["assertions"]:
        assertion["error_patterns"] = []
    for gap in stripped["skill_gaps"]:
        gap["patterns"] = []
    out = build_prompt(stripped, task)
    assert "patterns" not in [h["key"] for h in out["hints"]]


def test_clean_report_keeps_only_source_backed_hints(report, task):
    """全通过的复述材料仍要能出：此时只剩「题目到底要几步」，不编激励语。"""
    clean = json.loads(json.dumps(report))
    for assertion in clean["assertions"]:
        assertion["state"] = "pass"
        assertion["error_patterns"] = []   # 真实的干净报告不会给通过项留错法标签
    clean["skill_gaps"] = []
    out = build_prompt(clean, task)
    assert out["checks"] == []
    assert [h["key"] for h in out["hints"]] == ["asks"]


# ---------------------------------------------------------------- 出错优先


def test_mismatched_task_id_is_rejected(report):
    """讲错题的复述材料比没有更糟：必须抛错，而不是打一行警告继续。"""
    other = {"task_id": "dbg-002-invoice-report", "title": "别的题"}
    with pytest.raises(FeynmanError) as exc:
        build_prompt(report, other)
    assert "对不上" in str(exc.value)


def test_missing_pattern_plain_is_an_error(report, task):
    labels = load_pattern_labels()
    plains = load_pattern_plains()
    broken = dict(plains)
    broken.pop("wrong_total_amount")
    with pytest.raises(FeynmanError):
        build_prompt(report, task, pattern_labels=labels, pattern_plains=broken)


# 这几个词只要出现在页面上，学习者第一眼读到的就不是"你哪里错了"，而是内部代号。
# 清单刻意短而硬：都是本仓真实用过的标识符，不写"看起来像技术的词"那种模糊判据。
INTERNAL_IDENTIFIERS = (
    "valid_count", "total_amount", "root_cause", "offending_line",
    "report.json", "diagnosis.json", "test_inputs", "ask_policy",
    "additional_properties", "final_status", "fixture", "offset",
    "minimum", "maximum", "required", "crashed", "timeout",
)


def test_plain_has_no_internal_identifiers():
    """给学习者看的那句话里不许出现字段名 / 文件名 / 枚举值。

    这是「换成人话」这件事唯一的可机器判定的部分。没有它，"人话"就只是自己觉得像。
    """
    plains = load_pattern_plains()
    offenders = {
        pid: [w for w in INTERNAL_IDENTIFIERS if w in text]
        for pid, text in plains.items()
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, f"这些 plain 里漏了内部词：{offenders}"


def test_plain_is_not_just_the_judge_text():
    """plain 与 description 必须是两句话。

    description 是 judge 判定时读的清单，精确到能判分；把它原样搬到页面上，
    学习者读到的就是"valid_count 与依据 fixture 剔除规则现算的条数不符"。
    两条相同 ⇒ 这次拆分没有发生，只是换了个字段名。
    """
    from arena.error_notes import load_pattern_descriptions

    descs = load_pattern_descriptions()
    plains = load_pattern_plains()
    same = [pid for pid, text in plains.items() if text == descs.get(pid, "").strip()]
    assert not same, f"这些模式的 plain 与 description 一字不差：{same}"


def test_unknown_skill_in_gap_is_an_error(report, task):
    broken = json.loads(json.dumps(report))
    broken["skill_gaps"][0]["skill_id"] = "not_a_skill"
    with pytest.raises(Exception):
        build_prompt(broken, task)


# ---------------------------------------------------------------- 数值不装精度


@pytest.mark.parametrize(
    "value,text",
    [
        (17, "17"),
        (1837.99, "1837.99"),
        (0.5, "0.5"),
        (True, "是"),
        (False, "否"),
        (None, "（空）"),
        ("completed", "completed"),
    ],
)
def test_fmt_does_not_invent_precision(value, text):
    assert _fmt(value) == text


def test_diff_lines_keep_integer_as_integer(prompt):
    """反例守门：曾想统一 `%.2f`，那样「17 条」会变成「17.00 条」。"""
    rows = [row for check in prompt["checks"] for row in check["diffs"]]
    assert {"field": "有效条数", "expected": "17", "actual": "9"} in rows
    assert not any("17.00" in row["expected"] for row in rows)


def test_serialized_prompt_has_no_stray_markdown(prompt):
    """页面只渲染加粗；其它 markdown 符号会原样露出来。"""
    text = json.dumps(prompt, ensure_ascii=False)
    for token in ("```", "## ", "]("):
        assert token not in text


# --- 提交后解锁的自查清单（2026-11-21 人拍板：先交后给答案）-----------------------

def test_checklist_steps_are_verbatim_from_task(prompt, task):
    """「该说哪几步」必须逐字等于任务自述的编号条目，不许在这一层改写。"""
    steps = [item for item in prompt["checklist"] if item["kind"] == "step"]
    assert [item["text"] for item in steps] == asks_of(task)
    assert steps, "没有步骤就不该有清单主干——空清单等于页面假装自查过了"


def test_checklist_missed_text_is_verbatim_from_report(prompt, report):
    """「哪里说漏了」用报告里那句人话原文；报告没写就只留断言 id，不代写解释。

    2026-11-24 起人话的主来源是 `pattern_notes`（按错误模式拼、带本次数字），
    其次才是管线偶尔写下的 `note`。这条锁的是「逐字搬」，不是「搬哪一处」：
    两种来源都必须原样出现，不许在这一层润色。
    """
    misses = [item for item in prompt["checklist"] if item["kind"] == "missed"]
    by_key = {item["key"]: item for item in misses}

    def expected_text(assertion):
        texts = [
            str(n.get("text")).strip()
            for n in (assertion.get("pattern_notes") or ())
            if str(n.get("text") or "").strip()
        ]
        if texts:
            return "；".join(texts)
        note = assertion.get("note")
        return note.strip() if isinstance(note, str) and note.strip() else assertion["id"]

    explained = [
        a for a in report["assertions"]
        if a.get("state") != "pass" and expected_text(a) != a["id"]
    ]
    assert explained, "这份样例报告至少要有一条能说出人话的失败项，否则这条测试测不到东西"
    for assertion in report["assertions"]:
        if assertion.get("state") == "pass":
            continue
        assert by_key[assertion["id"]]["text"] == expected_text(assertion), assertion["id"]


def test_checklist_missed_diffs_match_the_outer_checks(prompt):
    """清单里的期望/实际与外层 checks 是同一份数据，不许各存一份。"""
    outer = {check["assertion_id"]: check["diffs"] for check in prompt["checks"]}
    for item in prompt["checklist"]:
        if item["kind"] == "missed":
            assert item["diffs"] == outer[item["key"]]


def test_checklist_puts_steps_before_misses(prompt):
    """先「该说什么」再「哪里说漏了」：顺序反了会变成一上来先看答案。"""
    kinds = [item["kind"] for item in prompt["checklist"]]
    assert kinds == sorted(kinds, key=lambda k: 0 if k == "step" else 1)


def test_clean_report_has_no_missed_items(report, task):
    """全过的时候清单只剩步骤：没有漏项就别造出「你漏了」这一栏。"""
    clean = json.loads(json.dumps(report))
    for assertion in clean["assertions"]:
        assertion["state"] = "pass"
    clean["skill_gaps"] = []
    for assertion in clean["assertions"]:
        assertion.pop("note", None)
    built = build_prompt(clean, task)
    assert [item["kind"] for item in built["checklist"]] == ["step"] * len(built["asks"])
