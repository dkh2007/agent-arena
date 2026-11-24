"""`android/app/src/main/assets/reminder.json` 的契约测试（B-10，2026-11-24）。

**为什么必须有这一条**：Compose 页不排序、不截断、不改写文案——它把"今天该显示什么"
整个托付给这份 JSON。而 asset 是**生成物**，会随演示数据、词表、上限口径变化而过期。
过期之后页面照样渲染，错误就变成"看起来正常"：条数多了、顺序乱了、文案还是旧的那句。
这类错误没有任何运行时症状，只能靠对账发现。

判据分四组：

1. **等于现算结果** — 拿固定 `at` 重跑一遍 CLI，逐字段比对；不一致就报出重新生成的命令。
2. **字段集合固定** — 顶层与每条 row 的键集合都钉死，页面解析器（ReminderModel.kt）才敢按名取。
3. **页面能显示的东西是干净的** — 技能是中文名、形态有中文说法、文案里没有内部词。
4. **顺序与上限来自 scheduler** — 页面不重排，所以这份 JSON 的顺序必须是 `take_daily` 的顺序。
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import scheduler  # noqa: E402

ASSET = ROOT / "android" / "app" / "src" / "main" / "assets" / "reminder.json"
SCRIPT = ROOT / "scripts" / "review_page.py"

# asset 里写死的"现在"。改成别的时刻就要重新生成 asset——这正是下面第一条要抓的。
ASSET_AT = "2026-11-24T21:00:00+08:00"
REGEN = (f"python scripts/review_page.py --at {ASSET_AT} "
         f"--out android/app/src/main/assets/reminder.json")

TOP_KEYS = {"generated_at", "cap", "due_total", "today", "deferred",
            "empty", "headline", "notice"}
ROW_KEYS = {"skill_id", "skill", "kind", "kind_label", "task_id",
            "task_title", "due", "overdue_days", "overdue_text"}

# 页面上不许出现的内部词（与 reminder._check_plain 同一张单子）
INTERNAL = ("review_id", "submission_id", "skill_id", "pending", "ratio")


def _cli_json() -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--at", ASSET_AT, "--json",
         "--tasks", str(ROOT / "tasks")],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _asset() -> dict:
    assert ASSET.exists(), f"缺少 {ASSET.relative_to(ROOT)}；生成命令：{REGEN}"
    return json.loads(ASSET.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. 等于现算结果
# ---------------------------------------------------------------------------

def test_asset_equals_freshly_built_reminder():
    """asset 过期是静默错误：页面照旧渲染旧的条数和旧的话。"""
    assert _asset() == _cli_json(), (
        "assets/reminder.json 与 build_reminder() 的现算结果不一致（演示数据、词表或上限改过了）。"
        f"重新生成：{REGEN}"
    )


def test_cli_out_writes_exactly_the_committed_content(tmp_path):
    """连格式一起锁：换行、缩进、字段顺序变了，版本库里会冒出一堆假 diff。

    比的是**文本**不是字节：本机 `core.autocrlf=true`，别人的工作区拿到的是 CRLF，
    按字节比会在干净克隆上假红。真正归生成器管的是下面那条——它自己必须写 LF。
    """
    out = tmp_path / "reminder.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--at", ASSET_AT, "--out", str(out),
         "--tasks", str(ROOT / "tasks")],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.read_text(encoding="utf-8") == ASSET.read_text(encoding="utf-8"), (
        "写出的内容与版本库里的 asset 不同（手工改过，或生成逻辑变了）。"
        f"重新生成：{REGEN}"
    )
    assert b"\r\n" not in out.read_bytes(), (
        "生成器自己写出了 CRLF：跨机器 diff 会多出一堆假改动（与 git 的 autocrlf 无关）"
    )


# ---------------------------------------------------------------------------
# 2. 字段集合固定（Kotlin 解析器按名取字段）
# ---------------------------------------------------------------------------

def test_top_level_keys_are_exactly_the_contract():
    assert set(_asset()) == TOP_KEYS, (
        "顶层字段变了。Kotlin 侧 ReminderModel.parseReminder 按名取字段："
        "少一个会静默变空，多一个没人显示——两边都要改。"
    )


def test_every_row_has_the_contract_keys():
    data = _asset()
    assert data["today"], "演示数据至少要有一项，否则这页的常态看着像坏掉了"
    for row in data["today"]:
        assert set(row) == ROW_KEYS, f"row 字段与契约不符：{sorted(row)}"


def test_field_types_match_the_kotlin_readers():
    """键对还不够：`optInt("cap")` 读到字符串会静默给 0，页面就变成"每天最多 0 项"。

    没有模拟器也能验这一层——类型错了在这里红，不必等装机。
    """
    data = _asset()
    assert isinstance(data["generated_at"], str) and data["generated_at"]
    for key in ("cap", "due_total", "deferred"):
        assert isinstance(data[key], int) and not isinstance(data[key], bool), (
            f"{key} 必须是整数（Kotlin 用 optInt 读）"
        )
    assert isinstance(data["empty"], bool), "empty 必须是布尔（Kotlin 用 optBoolean 读）"
    assert isinstance(data["headline"], str)
    assert data["notice"] is None or isinstance(data["notice"], str)
    for row in data["today"]:
        for key in ("skill", "kind_label", "task_title", "overdue_text"):
            assert isinstance(row[key], str), f"{key} 必须是字符串（Kotlin 用 optString 读）"
        assert isinstance(row["overdue_days"], int) and not isinstance(row["overdue_days"], bool)


# ---------------------------------------------------------------------------
# 3. 页面能显示的东西是干净的
# ---------------------------------------------------------------------------

def test_displayed_text_has_no_internal_words():
    data = _asset()
    shown = {"headline": data["headline"], "notice": data["notice"] or ""}
    for row in data["today"]:
        shown[row["skill_id"]] = " ".join(
            str(row[k]) for k in ("skill", "kind_label", "task_title", "overdue_text")
        )
    blob = json.dumps(shown, ensure_ascii=False)
    for word in INTERNAL:
        assert word not in blob, f"页面会显示内部词 {word}：{blob}"


def test_skill_is_a_chinese_label_not_an_id():
    """学生看到的是"凭证录入，逾期 2 天"，不是 `tool_param_validation`。"""
    from arena.diagnosis_report import load_skill_labels

    labels = load_skill_labels()
    for row in _asset()["today"]:
        assert row["skill"] != row["skill_id"], f"技能一栏回落成了 id：{row['skill']!r}"
        assert row["skill"] in labels.values(), (
            f"技能名 {row['skill']!r} 不在词表里——它可能是 id 或页面自己编的别名"
        )


def test_overdue_text_agrees_with_days():
    for row in _asset()["today"]:
        if row["overdue_days"] == 0:
            assert row["overdue_text"] == "今天到期", "今天到期不许写成『逾期 0 天』"
        else:
            assert row["overdue_text"] == f"逾期 {row['overdue_days']} 天"


def test_headline_is_never_empty_and_notice_is_null_or_text():
    data = _asset()
    assert data["headline"].strip(), "标题不能是空串——页面顶部会留一块空白"
    assert data["notice"] is None or data["notice"].strip()


# ---------------------------------------------------------------------------
# 4. 顺序与上限来自 scheduler（页面不重排、不截断）
# ---------------------------------------------------------------------------

def test_today_order_is_the_scheduler_order():
    """拿演示数据真跑一遍排序入口，逐项比 due。

    页面不重排，所以这份 JSON 的顺序**必须**就是 `take_daily` 的顺序。
    自测"按 due 升序"不够：兜底键（同 due 时的次序）与截断位置都在后端，
    页面一旦拿到别的顺序，"昨天该看的今天还在"就是从这一行开始的。
    """
    import importlib.util

    data = _asset()
    spec = importlib.util.spec_from_file_location("review_page_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    items = mod._demo_items(datetime.fromisoformat(ASSET_AT))
    expected, deferred = scheduler.take_daily(items, at=ASSET_AT, cap=data["cap"])

    assert [r["due"] for r in data["today"]] == [r["due"] for r in expected], (
        "今天这几项的顺序与 take_daily 不一致——页面照单全收，会显示另一套优先级"
    )
    assert data["deferred"] == deferred
    assert data["due_total"] == len(data["today"]) + data["deferred"], (
        "排队数 ≠ 今日项 + 推迟项：这个等式一破，页面上的『另有 N 项』就是编的"
    )
    assert data["generated_at"] == ASSET_AT


def test_empty_flag_matches_the_list():
    data = _asset()
    assert data["empty"] is (len(data["today"]) == 0)


def test_take_daily_agrees_with_the_asset_cap():
    """再走一遍真正的排序入口：条数不能超过上限。"""
    data = _asset()
    items = [{"review_id": f"r{i}", "user_id": "demo", "skill_id": "tool_param_validation",
              "kind": "variant", "task_id": None, "due": row["due"], "status": "pending"}
             for i, row in enumerate(data["today"])]
    today, deferred = scheduler.take_daily(items, at=data["generated_at"], cap=data["cap"])
    assert len(today) == min(len(items), data["cap"]), "今日条数超过上限"
    assert len(today) + deferred == len(items), "被挤掉的项必须仍然存在（只是排到明天）"
