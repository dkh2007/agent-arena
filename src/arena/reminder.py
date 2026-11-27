"""复习提醒页的数据侧（B-10「复习提醒页」，2026-11-22）。

## 这一步只回答一个问题

> 一个学生打开提醒页，今天应该看到哪几项、每项为什么在、看不完的怎么办。

**不做**：不造题（造题是 `scheduler` 的事）、不写库（读 `review_items`）、不发通知。

## 三条口径，都来自已拍板的决定

1. **一天别推太多**：`users.daily_review_cap` 是硬上限（默认 2）。看不完的项**不删**，
   明天照旧到期——所以这里必须把「被推迟几项」说出来，否则学生以为漏的项消失了。
2. **到期先看**：排序不在这里重写，直接调 `scheduler.take_daily()`。两处各写一份排序，
   迟早有一处先改（`due` 跨时区比较、同 `due` 的兜底键），然后页面上出现"昨天该看的今天还在"。
3. **不出现内部词**：`review_id` / `pending` / `skill_id` / `submission_id` 一律不进对外文案
   （与 `notices.py` 的 `_check_plain` 同一条纪律）。学生看到的是"凭证录入，逾期 2 天"。

## 与 Android 页的分工

本模块产出**页面要显示的数据**（`build_reminder` 的返回值），Compose 层只负责摆位置。
这样"今天为什么给我这三项"可以在 pytest 里锁住，不用开模拟器才能验。
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from . import mastery as m
from . import scheduler as sch

# 四种复习形态的中文说法。**不认识就报错，不糊一个默认值**——
# 糊一个"复习"会让新加的形态在页面上长得跟旧的一样，没人会发现少了一类。
#
# 2026-11-24：自动调度目前只产 `replay` / `fault_injection`（见 `scheduler.AUTO_KINDS`）。
# `variant` 这一条留着给 B-09——它要"挑题"能力才成立：标签说的是"同族换一道"，
# 而调度现在的 `task_id` 永远是刚做过那道，两者并存就是页面上的假话。
KIND_LABELS = {
    "variant": "同族换一道",
    "fault_injection": "重做上次出错的那一页",
    "timed": "限时练一遍",
    "replay": "原题重做",
}

# 对外文案里不许出现的内部词（`_` 形式与英文枚举名都算）
_INTERNAL_WORDS = ("review_id", "submission_id", "skill_id", "pending", "ratio")


class ReminderError(ValueError):
    """输入不成立（缺技能名、未知复习形态、时刻不可解析）。不吞、不兜底。"""


def _overdue_days(now: datetime, due: datetime) -> int:
    """逾期天数，向下取整；今天到期记 0。

    按整天算而不是"小时差/24"：学生脑里的单位是"天"。差 23 小时写成「逾期 0.9 天」
    只会让人去算，不如直接说「今天到期」。
    """
    seconds = (now - due).total_seconds()
    return max(0, int(seconds // 86400))


def _overdue_text(days: int) -> str:
    return "今天到期" if days == 0 else f"逾期 {days} 天"


def _check_plain(text: str) -> None:
    hit = [w for w in _INTERNAL_WORDS if w in text]
    if hit:
        raise ReminderError(
            f"对外文案里出现了内部词 {hit}：{text!r}——学生看不懂，还会以为平台出故障"
        )


def build_reminder(items: Iterable[dict], *, at: str, cap: int,
                   skill_names: dict[str, str],
                   task_titles: dict[str, str] | None = None) -> dict:
    """今日提醒页的内容。`at` 为当前时刻（带时区），`cap` 取自用户设置。

    返回结构见模块文档；`today` 的顺序与 `scheduler.take_daily` 完全一致。
    """
    if not isinstance(skill_names, dict) or not skill_names:
        raise ReminderError("skill_names 不能为空：没有技能名就只能显示英文 id")
    now = m.parse_instant(at)
    # 排序 + 截断只有这一处实现（take_daily）；这里只做展示层的加工。
    today_raw, deferred = sch.take_daily(items, at=at, cap=cap)
    due_total = len(sch.sort_queue(items, at=at))

    titles = task_titles or {}
    rows: list[dict] = []
    for row in today_raw:
        skill_id = row["skill_id"]
        if skill_id not in skill_names:
            raise ReminderError(
                f"技能 {skill_id} 在词表里没有对应的 name：不许在页面上露英文 id"
            )
        kind = row.get("kind")
        if kind not in KIND_LABELS:
            raise ReminderError(f"未知复习形态 {kind!r}，页面上没有对应的说法")
        days = _overdue_days(now, m.parse_instant(row["due"]))
        text = _overdue_text(days)
        _check_plain(text)
        task_id = row.get("task_id")
        rows.append({
            # `review_id` 只用来拼「做完了」的链接（B-08c），**不印在文案里**：
            # 页面上的字仍然只有技能名、形态、逾期天数（第 3 条口径不变）。
            "review_id": row["review_id"],
            "skill_id": skill_id,
            "skill": skill_names[skill_id],
            "kind": kind,
            "kind_label": KIND_LABELS[kind],
            "task_id": task_id,
            "task_title": titles.get(task_id, "") if task_id else "",
            "due": row["due"],
            "overdue_days": days,
            "overdue_text": text,
        })

    headline, notice = _headline(rows, due_total=due_total, deferred=deferred, cap=cap)
    return {
        "generated_at": at,
        "cap": cap,
        "due_total": due_total,
        "today": rows,
        "deferred": deferred,
        "empty": not rows,
        "headline": headline,
        "notice": notice,
    }


def _headline(rows: list[dict], *, due_total: int, deferred: int, cap: int) -> tuple[str, str | None]:
    """标题 + 一句补充说明。**两件事分开说**：今天做什么、剩下的去哪了。

    合成一句写（"今天 2 项，3 项明天"）在 cap=0 时会读成"今天 0 项"，把
    "你自己关了今天"和"确实没有要复习的"混成一件事——这正是要避免的。
    """
    if cap == 0:
        if due_total == 0:
            return "今天没有要复习的", None
        return (
            "今天你设成了不复习",
            f"还有 {due_total} 项在排队，明天会一起出现——它们没有被删掉",
        )
    if not rows:
        return "今天没有要复习的", None
    head = f"今天 {len(rows)} 项要复习"
    if deferred:
        return head, f"另有 {deferred} 项排到明天——一天不塞太多，它们不会丢"
    return head, None


def format_reminder(reminder: dict) -> str:
    """排成给人看的几行（命令行走这条；页面用 `today` 字段自己摆）。"""
    lines = [reminder["headline"]]
    for i, row in enumerate(reminder["today"], 1):
        tail = f"（{row['task_title']}）" if row["task_title"] else ""
        lines.append(f"  {i}. {row['skill']} — {row['kind_label']}，{row['overdue_text']}{tail}")
    if reminder["notice"]:
        lines.append(reminder["notice"])
    return "\n".join(lines)
