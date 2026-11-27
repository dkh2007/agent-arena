"""复习提醒页（B-10）的契约测试。

判据分三组：

**算得对** — 到期才进队列、上限截断、被推迟的项不丢、逾期按整天算。
**说得清** — cap=0 与"确实没有要复习的"必须说成两件事；被推迟的项要在页面上被点名。
**不露内部词** — `review_id` / `pending` / `skill_id` 这些词对写代码的人有意义，
对学生只是噪音，还会让人以为平台出故障（与 `notices.py` 同一条纪律）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import reminder, scheduler  # noqa: E402

TZ = timezone(timedelta(hours=8))
NOW = "2026-11-22T21:00:00+08:00"
NAMES = {"tool_param_validation": "工具参数校验",
         "multi_step_planning": "多步任务分解",
         "failure_retry": "失败重试策略"}


def _item(i: int, *, days: float, skill: str = "tool_param_validation",
          kind: str = "variant", status: str = "pending", task_id: str | None = None) -> dict:
    base = datetime.fromisoformat(NOW)
    return {
        "review_id": f"r-{i}",
        "user_id": "u1",
        "skill_id": skill,
        "kind": kind,
        "task_id": task_id,
        "due": (base + timedelta(days=days)).isoformat(timespec="seconds"),
        "status": status,
    }


def _build(items, cap=2, task_titles=None) -> dict:
    return reminder.build_reminder(items, at=NOW, cap=cap, skill_names=NAMES,
                                   task_titles=task_titles)


# ---------------------------------------------------------------------------
# 算得对
# ---------------------------------------------------------------------------

def test_only_due_pending_items_enter_today():
    data = _build([
        _item(1, days=-1),
        _item(2, days=+3),                 # 还没到
        _item(3, days=-1, status="done"),  # 做过了
    ])
    assert [r["skill_id"] for r in data["today"]] == ["tool_param_validation"]
    assert len(data["today"]) == 1
    assert data["due_total"] == 1, "未到期与已完成都不该算进『排队中』"


def test_cap_truncates_and_says_how_many_were_pushed():
    data = _build([_item(i, days=-i) for i in range(1, 6)], cap=2)
    assert len(data["today"]) == 2
    assert data["deferred"] == 3
    assert data["due_total"] == 5
    assert "3" in data["notice"] and "不会丢" in data["notice"], (
        "被推迟的项必须在页面上被点名，否则学生以为漏的项消失了"
    )


def test_order_is_delegated_to_take_daily():
    items = [_item(1, days=-1), _item(2, days=-5), _item(3, days=-2)]
    data = _build(items, cap=3)
    expected = [r["review_id"] for r in scheduler.sort_queue(items, at=NOW)]
    assert [r["due"] for r in data["today"]] == [
        next(r["due"] for r in items if r["review_id"] == rid) for rid in expected
    ], "排序只能有一处实现（scheduler），页面不许自己再排一遍"


def test_overdue_days_are_whole_days_and_zero_means_today():
    data = _build([_item(1, days=-2.2), _item(2, days=-0.02)], cap=2)
    days = [r["overdue_days"] for r in data["today"]]
    assert days == [2, 0], "23 小时写成『逾期 0.9 天』只会让学生去算"
    assert data["today"][1]["overdue_text"] == "今天到期"


def test_cap_zero_is_not_the_same_as_nothing_due():
    """这两件事在页面上必须读成两句不同的话——合成一句会让学生以为平台把题吞了。"""
    with_items = _build([_item(1, days=-1), _item(2, days=-2)], cap=0)
    assert with_items["today"] == [] and with_items["empty"] is True
    assert with_items["headline"] == "今天你设成了不复习"
    assert "2" in with_items["notice"]

    without = _build([], cap=0)
    assert without["headline"] == "今天没有要复习的"
    assert without["notice"] is None


def test_task_title_is_shown_only_when_known():
    data = _build([_item(1, days=-1, task_id="dbg-001-order-report")],
                  task_titles={"dbg-001-order-report": "订单数据核对报告"})
    assert data["today"][0]["task_title"] == "订单数据核对报告"
    bare = _build([_item(1, days=-1, task_id="nope")], task_titles={})
    assert bare["today"][0]["task_title"] == "", "查不到标题就留空，不许编一个"


# ---------------------------------------------------------------------------
# 说不清就报错，不许兜底
# ---------------------------------------------------------------------------

def test_unknown_skill_raises_instead_of_leaking_the_id():
    with pytest.raises(reminder.ReminderError, match="没有对应的 name"):
        _build([_item(1, days=-1, skill="skill_that_does_not_exist")])


def test_unknown_kind_raises_instead_of_using_a_catch_all_label():
    with pytest.raises(reminder.ReminderError, match="未知复习形态"):
        _build([_item(1, days=-1, kind="mystery")])


def test_empty_skill_names_is_rejected():
    with pytest.raises(reminder.ReminderError, match="不能为空"):
        reminder.build_reminder([_item(1, days=-1)], at=NOW, cap=2, skill_names={})


# ---------------------------------------------------------------------------
# 不露内部词
# ---------------------------------------------------------------------------

def test_user_facing_text_has_no_internal_words():
    data = _build([_item(i, days=-i) for i in range(1, 4)], cap=1)
    # `review_id` 是「做完了」链接的唯一标识（B-08c）：它必须传出去，但只进 `href`，
    # 不进任何一句给人看的话。所以这里查的是**文案字段**——键名本身不是文案。
    # 真正"页面上看得到什么"由 `tests/test_hosting.py` 那条去标签后检查的用例守。
    rows = [{k: v for k, v in row.items() if k != "review_id"} for row in data["today"]]
    blob = json.dumps(
        {k: data[k] for k in ("headline", "notice")} | {"today": rows},
        ensure_ascii=False,
    )
    for word in ("review_id", "submission_id", "pending", "ratio"):
        assert word not in blob, f"对外文案里出现了内部词 {word}"
    for row in data["today"]:
        assert row["skill"] in NAMES.values(), "技能一栏必须是中文名，不是英文 id"


def test_format_reminder_renders_numbered_rows():
    data = _build([_item(1, days=-2, task_id="dbg-001-order-report")],
                  task_titles={"dbg-001-order-report": "订单数据核对报告"})
    text = reminder.format_reminder(data)
    lines = text.splitlines()
    assert lines[0] == "今天 1 项要复习"
    assert lines[1].strip().startswith("1. 工具参数校验")
    assert "逾期 2 天" in lines[1] and "订单数据核对报告" in lines[1]


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def test_cli_demo_runs_and_prints_a_headline():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "review_page.py"),
         "--at", NOW, "--tasks", str(ROOT / "tasks")],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0] == "今天 2 项要复习"
    assert "重做上次出错的那一页" in result.stdout


def test_cli_cap_zero_explains_the_queue():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "review_page.py"),
         "--at", NOW, "--cap", "0", "--tasks", str(ROOT / "tasks")],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
    )
    assert result.returncode == 0
    assert "设成了不复习" in result.stdout


def test_cli_reads_a_real_database(tmp_path):
    """接真实库的那条分支也要走通：页面读的是落库结果，不是只有演示数据能跑。

    顺带锁住 cap 的来源——`--cap` 不给时取用户自己的设置（默认 2），不是写死。
    """
    from arena import store

    db = tmp_path / "arena.db"
    conn = store.connect(db)
    store.migrate(conn)
    store.register_skills(conn)      # review_items.skill_id 有外键，得先有词表
    store.ensure_user(conn, "u1")
    store.save_review_items(conn, [
        {"review_id": "r-1", "user_id": "u1", "skill_id": "multi_step_planning",
         "kind": "variant", "task_id": None,
         "due": "2026-11-21T00:00:00+08:00", "status": "pending"},
        {"review_id": "r-2", "user_id": "u1", "skill_id": "failure_retry",
         "kind": "fault_injection", "task_id": None,
         "due": "2026-11-22T00:00:00+08:00", "status": "pending"},
    ])
    conn.close()

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "review_page.py"),
         "--db", str(db), "--user", "u1", "--at", NOW, "--tasks", str(ROOT / "tasks")],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "今天 2 项要复习" in result.stdout
    assert "多步任务分解" in result.stdout and "失败重试策略" in result.stdout
