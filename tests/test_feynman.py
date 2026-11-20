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
    load_pattern_descriptions,
)
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
    descriptions = load_pattern_descriptions()
    labels = load_pattern_labels()
    seen = 0
    for gap in prompt["gaps"]:
        for pattern in gap["patterns"]:
            assert pattern["label"] == labels[pattern["id"]]
            assert pattern["description"] == descriptions[pattern["id"]]
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
            assert got["field"] == want["field"]
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
    assert [h["key"] for h in prompt["hints"]] == ["asks", "diffs", "patterns", "scene"]


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


def test_missing_pattern_description_is_an_error(report, task):
    labels = load_pattern_labels()
    descriptions = load_pattern_descriptions()
    broken = dict(descriptions)
    broken.pop("wrong_total_amount")
    with pytest.raises(FeynmanError):
        build_prompt(report, task, pattern_labels=labels, pattern_descriptions=broken)


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
    assert {"field": "valid_count", "expected": "17", "actual": "9"} in rows
    assert not any("17.00" in row["expected"] for row in rows)


def test_serialized_prompt_has_no_stray_markdown(prompt):
    """页面只渲染加粗；其它 markdown 符号会原样露出来。"""
    text = json.dumps(prompt, ensure_ascii=False)
    for token in ("```", "## ", "]("):
        assert token not in text
