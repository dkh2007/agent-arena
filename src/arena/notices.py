"""B-04g 落地：把「这条记录算什么」翻成「跟谁、说什么」。**只读，不改分。**

前面三层的分工已经清楚：`reappraisal` 管一份算得对不对，`baseline` 管整批能不能交代，
`exposure_split` 管这次改的规则谁的记录真的用到了。但它们都还停在"数"上。
人拍完口径之后，缺的是最后一段：**每条记录落在哪个状态、要不要说、说什么**。

## 人已拍的三条（2026-11-17，原话见 `review-response-v1.md` §101.6）

1. **当年有分、现在判不了的**：原分不动 + 与分数同屏的一句标注 + 可选的不计分重做，
   并且**追新不追旧**——他后来做过同类练习的话，用那份代替重做；
2. **分数真的变了**：页面上与分数同屏的一行（不推送、不群发），必须含四件；
3. **判据没碰到的那批**：不推送，只在档案里留一句"已复核，结论不变"。

## 五个记录状态（每条记录落在且只落在一格）

| 状态 | 什么时候 | 对外 | 谁看得到 |
|---|---|---|---|
| `recalculated` | 重评后生效分与当时不同 | 新分 + 一行说明 | 本人（页面） |
| `unchanged` | 重评后分数没变 / 本来就按现行判据判的 / 判据没碰到它 | 原分 | 只有档案 |
| `legacy_scored` | 当年有分，现在补不上（判据真的参与过） | 原分 + 可见标注 | 本人（页面） |
| `legacy_unscored` | 当年没分，补不上 | 无分，**诊断保留** | 不告知 |
| `pending_mastery` | 0 分但过程证据进过 P（补不上） | 分数侧不动 | **待拍**：口径未定，先标出来 |

**`pending_mastery` 是刻意留白的**：`T3` 那批人的掌握度贡献冻在旧判据上，
"要不要重算、要不要在界面上说"是 B-07b 的口径，不是这里能定的。本模块只负责
**把它标成待定**，不许悄悄并进 `legacy_unscored`——那等于替人拍了"不用管"。

## 对外文案的硬约束

- **四件必备**：按哪版标准判的 / 能和什么比不能和什么比 / 要不要做什么 / **这不是你的问题**。
  四件缺一件，学生就会自己补一个更坏的版本（"我退步了""老师改了我的分"）。
- **不许出现内部术语**：`submission_id` / `ratio` / `rubric` / 断言 / 重评 / 基线 一律不许进
  对外文案（`_check_plain` 会拦）。这些词对写代码的人有意义，对学生只是噪音，
  而且会让人以为"平台出了故障"。
- **不许承诺将来**：没有任何一句"以后不会再变"。标准还会修，给不了这个保证。

## 刻意不做

- 不发送、不落库、不改 `submissions` / `reappraisals`：本模块产出的是一份**计划**，
  真正发出去是界面/通知层的事，那一步要人盯着。
- 不判断"该不该告知"以外的任何事：例如"要不要重做"只写成"可选"，不排期。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # 作为包被 import
    from . import baseline as bl
    from . import diagnosis_report as dr
    from . import store
except ImportError:  # 作为脚本直接跑
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import baseline as bl  # type: ignore[no-redef]
    import diagnosis_report as dr  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[2]

RECORD_STATES = (
    "recalculated",
    "unchanged",
    "legacy_scored",
    "legacy_unscored",
    "pending_mastery",
)
DISCLOSURES = ("page", "archive", "none")
# 对外文案里不许出现的内部词。列在这里而不是写在测试里：检查与用法在一处，
# 改口径的人一定看得见。
FORBIDDEN_IN_TEXT = (
    "submission_id", "ratio", "rubric", "断言", "重评", "基线", "rubric_gate",
    "rubric_credit", "payload", "JSON", "schema",
)


class NoticeError(RuntimeError):
    """前置条件不成立（文案参数缺失/不合法、任务没加载）。**一律抛，不静默填空。**"""


def _check_plain(text: str) -> None:
    lowered = text.lower()
    for word in FORBIDDEN_IN_TEXT:
        if word.lower() in lowered:
            raise NoticeError(f"对外文案里出现了内部术语 {word!r}：{text!r}")


@dataclass(frozen=True)
class Notice:
    """一条记录的处置计划：状态 + 谁看得到 + 要说的那几句（按"件"拆开）。"""

    submission_id: str
    user_id: str
    task_id: str
    state: str
    disclosure: str
    elements: tuple[tuple[str, str], ...]
    needs_decision: bool = False

    def text(self) -> str:
        return "\n".join(text for _, text in self.elements)

    def keys(self) -> tuple[str, ...]:
        return tuple(key for key, _ in self.elements)

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "user_id": self.user_id,
            "task_id": self.task_id,
            "state": self.state,
            "disclosure": self.disclosure,
            "needs_decision": self.needs_decision,
            "elements": [{"key": k, "text": t} for k, t in self.elements],
        }


@dataclass(frozen=True)
class NoticePlan:
    """一个任务的全部记录怎么处置。`counted()` 五态各一格，不合并。"""

    task_id: str
    changed_rules: tuple[str, ...]
    notices: tuple[Notice, ...]

    def counted(self) -> dict[str, int]:
        out = {name: 0 for name in RECORD_STATES}
        for notice in self.notices:
            out[notice.state] += 1
        return {"records": len(self.notices), **out}

    def by_state(self, state: str) -> tuple[Notice, ...]:
        if state not in RECORD_STATES:
            raise NoticeError(f"未知记录状态 {state!r}，只有 {RECORD_STATES}")
        return tuple(n for n in self.notices if n.state == state)

    def disclosed(self, how: str) -> tuple[Notice, ...]:
        """`page`（本人页面上看得到）/ `archive`（只有档案）/ `none`（不告知）。"""
        if how not in DISCLOSURES:
            raise NoticeError(f"未知告知形态 {how!r}，只有 {DISCLOSURES}")
        return tuple(n for n in self.notices if n.disclosure == how)

    def needs_decision(self) -> tuple[Notice, ...]:
        return tuple(n for n in self.notices if n.needs_decision)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "changed_rules": list(self.changed_rules),
            "counted": self.counted(),
            "notices": [n.to_dict() for n in self.notices],
        }


def _fill(template: str, **values: Any) -> str:
    try:
        text = template.format(**values)
    except KeyError as exc:  # 模板少给参数 ⇒ 抛，不许留一个 {xxx} 给学生看
        raise NoticeError(f"文案模板缺参数：{exc}") from exc
    _check_plain(text)
    return text


def _pct(value: float) -> str:
    return f"{value * 100:.0f} 分" if value <= 1 else f"{value:.0f}"


def _recalculated(ratio_before: float, ratio_after: float, *, changed_on: str,
                  what_changed: str, scope: str) -> tuple[tuple[str, str], ...]:
    """乙那一行：四件，一件都不许省。"""
    return (
        ("what_changed", _fill(
            "这次记录的分数是 {after}，之前是 {before}。变的原因不是你：判据在 {on} 改过——{what}。",
            before=_pct(ratio_before), after=_pct(ratio_after),
            on=changed_on, what=what_changed)),
        ("why_me", _fill("{scope}", scope=scope)),
        ("comparable",
         "新分只和同一天之后、按新判据判的记录比；不能和更早的作业比（那些也是旧判据），"
         "也不能和同学比。"),
        ("next_step", "你不需要做什么，也没有任何扣分后果；复习计划照常。"),
    )


def _legacy_scored(ratio: float, *, changed_on: str) -> tuple[tuple[str, str], ...]:
    """当年有分、现在判不了：四件同样一件不少，但"做了什么"变成"什么都不用做"。"""
    return (
        ("which_rubric", _fill(
            "这条记录的分数（{score}）是按 {on} 之前的判据给的。判据后来改过，"
            "而我们没有当时那份作答内容可以按新判据重算。",
            score=_pct(ratio), on=changed_on)),
        ("comparable",
         "这个分只说明当时的情况：它不能和新判据下的成绩比较，也不能和同学比。"),
        ("next_step",
         "你不需要重做，也没有任何扣分后果。想再练一次可以，那次不计分；"
         "如果你后来已经做过同类练习，那份就可以直接算你现在的情况。"),
        ("not_your_fault",
         "这不是你的问题：是判据改了、而当时的作答内容没有留到今天。"),
    )


def _unchanged_recalculated() -> tuple[tuple[str, str], ...]:
    return (("reviewed", "已复核，结论不变。"),)


def _unchanged_current() -> tuple[tuple[str, str], ...]:
    return (("reviewed", "这条记录本来就按现行判据判的，已复核，无需改动。"),)


def plan_notices(conn, task: dict, *, changed_on: str, what_changed: str,
                 scope: str = "这次判据修订后重算的一批记录里有这一条。") -> NoticePlan:
    """一个任务下每条记录的处置计划。只读。

    `changed_on` / `what_changed` 是**人给的事实**（标准哪天改的、改了什么），
    本函数不猜、不编版本号——编一个日期进学生的页面，比不说更糟。

    状态判定顺序（先命中先落格）：分数变了 → 补得上的 → 补不上的（按档位与曝光）。
    """
    if not isinstance(task, dict) or not task.get("task_id"):
        raise NoticeError("task 必须是已加载的任务定义（含 task_id）")
    for name, value in (("changed_on", changed_on), ("what_changed", what_changed),
                        ("scope", scope)):
        if not isinstance(value, str) or not value.strip():
            raise NoticeError(f"{name} 必须写：学生的页面上要靠它解释「为什么变了」")

    tid = task["task_id"]
    exposure = bl.exposure_split(conn, task)
    tier_by_id = {row.submission_id: row.tier for row in exposure.rows}
    status_by_id = {row.submission_id: row.status for row in exposure.rows}
    blocked_ids = set(tier_by_id)

    rows = [s for s in store.list_submissions(conn, limit=10_000) if s["task_id"] == tid]
    notices: list[Notice] = []
    for row in rows:
        sid = row["submission_id"]
        original = float(store.load_report(conn, sid)["score"]["ratio"])
        effective = store.effective_ratio(conn, sid)
        changed = abs(effective - original) > 1e-12

        if changed:
            notice = Notice(sid, row["user_id"], tid, "recalculated", "page",
                            _recalculated(original, effective, changed_on=changed_on,
                                          what_changed=what_changed, scope=scope))
        elif sid in blocked_ids:
            tier, status = tier_by_id[sid], status_by_id[sid]
            if tier == "T1" and status in ("touched", "added_only", "unknown"):
                notice = Notice(sid, row["user_id"], tid, "legacy_scored", "page",
                                _legacy_scored(original, changed_on=changed_on))
            elif tier == "T3":
                # 分数侧没有可解释的东西，但过程证据进过 P 且永远停在旧判据上。
                notice = Notice(sid, row["user_id"], tid, "pending_mastery", "none",
                                (), needs_decision=True)
            elif tier == "T1":
                notice = Notice(sid, row["user_id"], tid, "unchanged", "archive",
                                _unchanged_recalculated())
            else:
                notice = Notice(sid, row["user_id"], tid, "legacy_unscored", "none", ())
        else:
            notice = Notice(sid, row["user_id"], tid, "unchanged", "archive",
                            _unchanged_current())
        notices.append(notice)

    order = {name: i for i, name in enumerate(RECORD_STATES)}
    notices.sort(key=lambda n: (order[n.state], n.submission_id))
    return NoticePlan(task_id=tid, changed_rules=exposure.changed_rules,
                      notices=tuple(notices))


def _fmt(plan: NoticePlan) -> str:
    lines = [f"任务 {plan.task_id}：{plan.counted()}",
             f"本次改到的规则：{', '.join(plan.changed_rules) or '（无）'}"]
    for notice in plan.notices:
        head = notice.elements[0][1] if notice.elements else "（不告知）"
        lines.append(f"  [{notice.state:<16}] {notice.submission_id:<22} "
                     f"{notice.disclosure:<8} {head}")
        for key, text in notice.elements[1:]:
            lines.append(f"      · {key}: {text}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="列出每条历史记录的处置计划（只读）")
    ap.add_argument("--db", required=True, help="sqlite 库路径")
    ap.add_argument("--task", default=str(ROOT / "tasks" / "dbg-001-order-report.yaml"))
    ap.add_argument("--changed-on", required=True, help="判据修订日期（人给的事实）")
    ap.add_argument("--what-changed", required=True, help="改了哪一条、为什么（一句人话）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    conn = store.connect(Path(args.db))
    try:
        plan = plan_notices(conn, dr.load_task(args.task),
                            changed_on=args.changed_on,
                            what_changed=args.what_changed)
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) if args.json
              else _fmt(plan))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
