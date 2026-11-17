"""B-04h ③：一次「判据改动 → 跑批 → 交代」的完整动作，以及它**停在哪一步**。

## 为什么还要这一层

前面几层各自都能用，但它们连起来还差三件只有人能回答的事：

1. **什么时候跑**：判据改一次就跑一次，还是攒到学期末？
2. **谁跑**：谁有权把新结论写进历史（写进去就改了别人的分数与掌握度）；
3. **页面那行字谁发**：`notices.plan_notices()` 产出的是文案，不是发送动作。

第 3 件尤其不能交给代码：发出去收不回来。所以本模块**只做到"清单 + 归档"**，
`pending_sends()` 是把"该发什么"摊开，没有 send 函数，也不打算有。

## 两阶段，靠一个参数分开

```
run_cycle(..., approved_by=None)   # 演练：只算不写，产出清单给人看
run_cycle(..., approved_by="张三")  # 执行：写库，并把"经谁确认"记进重评理由
```

**没有 `approved_by` 就绝不写库**，不是风格问题：写库会追加重评、刷新掌握度，
而这两样都改学生看到的东西。一次没有署名的批量改分，事后无从追责——
B-04d 那条"`reason` 是唯一线索"的口径，在这里补上"谁批的"。

## 演练与执行必须逐条对齐

执行前先算一遍演练，执行后拿真跑的结果**逐份对比** `changed()`：
份数、id、delta 全要一样。不一样就抛——那说明两次之间库被改过（并发或手工），
此时继续写等于把一份对不上的清单当成依据。这不是防御性编程，是这一层唯一的正确性判据。

## 刻意不做

- **不发送**、不生成学生可见页面：`pending_sends()` 只是清单。
- **不排期**：什么时候跑是人定的（`docs/RUNBOOK.md` 写了默认节奏）。
- **不删除/不覆盖历史**：与前面各层同一口径。
- **不因为"没有变化"就跳过通知环节**：`changed()` 为空也要走一遍，
  因为"这次没人受影响"本身就是要向人汇报的结论。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # 作为包被 import
    from . import baseline as bl
    from . import diagnosis_report as dr
    from . import notices as nt
    from . import store
except ImportError:  # 作为脚本直接跑
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import baseline as bl  # type: ignore[no-redef]
    import diagnosis_report as dr  # type: ignore[no-redef]
    import notices as nt  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[2]


class CycleError(RuntimeError):
    """前置条件不成立（缺署名、演练与执行对不上）。**一律抛，不静默继续。**"""


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class CycleReport:
    """一次跑批的完整交代：算了什么、写了什么、还差谁做什么。"""

    task_id: str
    reason: str
    changed_on: str
    what_changed: str
    applied: bool
    approved_by: str | None
    counted: dict[str, int]
    notices: tuple[dict, ...]
    pending_sends: tuple[dict, ...]
    needs_human: tuple[str, ...]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "reason": self.reason,
            "changed_on": self.changed_on,
            "what_changed": self.what_changed,
            "applied": self.applied,
            "approved_by": self.approved_by,
            "counted": self.counted,
            "notices": list(self.notices),
            "pending_sends": list(self.pending_sends),
            "needs_human": list(self.needs_human),
            "created_at": self.created_at,
        }

    def markdown(self) -> str:
        head = "执行" if self.applied else "演练（未写库）"
        lines = [
            f"# 跑批记录：{self.task_id}（{head}）",
            "",
            f"- 判据修订日期：{self.changed_on}",
            f"- 改了什么：{self.what_changed}",
            f"- 为什么跑：{self.reason}",
            f"- 署名：{self.approved_by or '（演练，无需署名）'}",
            f"- 时间：{self.created_at}",
            "",
            "## 数",
            "",
            "| 项 | 数 |",
            "|---|---|",
        ]
        for key, value in self.counted.items():
            lines.append(f"| {key} | {value} |")
        lines += ["", "## 要发出去的那几句（本工具不发送）", ""]
        if not self.pending_sends:
            lines.append("（本次没有需要发到本人页面的文案）")
        for item in self.pending_sends:
            lines.append(f"### {item['submission_id']} · {item['state']} · 用户 {item['user_id']}")
            lines.append("")
            for element in item["elements"]:
                lines.append(f"- **{element['key']}**：{element['text']}")
            lines.append("")
        lines += ["## 还要人做的", ""]
        lines += [f"- [ ] {item}" for item in self.needs_human] or ["- [ ] （无）"]
        return "\n".join(lines).rstrip() + "\n"

    def archive(self, directory: Path) -> dict[str, Path]:
        """把这次跑批写成三份文件。**不覆盖已有目录**：覆盖等于抹掉上一次的证据。"""
        directory = Path(directory)
        if directory.exists() and any(directory.iterdir()):
            raise CycleError(f"{directory} 不是空目录——归档不许覆盖上一次的记录")
        directory.mkdir(parents=True, exist_ok=True)
        paths = {
            "json": directory / "cycle.json",
            "markdown": directory / "cycle.md",
        }
        paths["json"].write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        paths["markdown"].write_text(self.markdown(), encoding="utf-8")
        return paths


def _check_args(task: Any, reason: str, changed_on: str, what_changed: str,
                approved_by: str | None) -> str:
    if not isinstance(task, dict) or not task.get("task_id"):
        raise CycleError("task 必须是已加载的任务定义（含 task_id）")
    for name, value in (("reason", reason), ("changed_on", changed_on),
                        ("what_changed", what_changed)):
        if not isinstance(value, str) or not value.strip():
            raise CycleError(f"{name} 必须写：跑批记录里它是唯一能追的说明")
    if approved_by is not None and (not isinstance(approved_by, str) or not approved_by.strip()):
        raise CycleError("approved_by 给了就得是名字；空字符串与「没人批」没有区别")
    return approved_by.strip() if approved_by else None


def _alignment_error(dry: bl.BaselineReport, real: bl.BaselineReport) -> str | None:
    """演练与执行的差异。返回 `None` 表示逐份对齐。

    比的是**会改变学生看到的东西**那部分：分数变了的名单与每份 delta。
    不比 `missing_*`——补不上的那批在两次里都一样，比它只会掩盖真正该查的差。
    """
    before = {o.submission_id: round(o.delta or 0.0, 9) for o in dry.changed()}
    after = {o.submission_id: round(o.delta or 0.0, 9) for o in real.changed()}
    if before == after:
        return None
    only_dry = sorted(set(before) - set(after))
    only_real = sorted(set(after) - set(before))
    differs = sorted(sid for sid in set(before) & set(after) if before[sid] != after[sid])
    return (f"演练与执行不一致：仅演练有 {only_dry}，仅执行有 {only_real}，"
            f"差值不同 {differs}——两次之间库被改过（并发或手工），"
            "先查清再重跑，别把一份对不上的清单当依据")


def run_cycle(conn, task: dict, *, reason: str, changed_on: str, what_changed: str,
              scope: str = "这次判据修订后重算的一批记录里有这一条。",
              approved_by: str | None = None,
              created_at: str | None = None) -> CycleReport:
    """跑一次完整动作。`approved_by=None` ⇒ 只演练，绝不写库。"""
    who = _check_args(task, reason, changed_on, what_changed, approved_by)
    stamp = created_at or _stamp()

    dry = bl.run_baseline(conn, [task], reason=f"[演练] {reason}",
                          apply=False, created_at=stamp)
    real = None
    if who is not None:
        real = bl.run_baseline(conn, [task], reason=f"{reason}（经 {who} 确认）",
                              apply=True, created_at=stamp)
        problem = _alignment_error(dry, real)
        if problem is not None:
            raise CycleError(problem)

    plan = nt.plan_notices(conn, task, changed_on=changed_on, what_changed=what_changed,
                           scope=scope)
    report = real or dry

    sends = [
        {"submission_id": n.submission_id, "user_id": n.user_id, "state": n.state,
         "elements": [{"key": k, "text": t} for k, t in n.elements]}
        for n in plan.disclosed("page")
    ]
    if real is None:
        # 演练时库里一个字没写，`plan_notices()` 读生效分读不到变化，
        # 于是"先看清单再批准"会看到一份空清单。这里用演练算出的差值补上那一批，
        # 文案复用同一组模板（`notices.recalculated_elements`）——
        # 演练看到的话必须与真跑发出的话逐字相同，否则这份清单只是摆设。
        known = {item["submission_id"] for item in sends}
        for out in dry.changed():
            if out.submission_id in known or out.user_id is None:
                continue
            sends.append({
                "submission_id": out.submission_id, "user_id": out.user_id,
                "state": "recalculated",
                "elements": [{"key": k, "text": t} for k, t in
                             nt.recalculated_elements(out.old_ratio, out.new_ratio,
                                                      changed_on=changed_on,
                                                      what_changed=what_changed,
                                                      scope=scope)],
            })
    sends.sort(key=lambda item: item["submission_id"])
    sends = tuple(sends)
    needs: list[str] = []
    if who is None:
        needs.append("本次是演练，一个字都没写进库。确认上面那份「分数变化清单」无误后，"
                     "带 `--approved-by <确认人>` 重跑一次才会真写。")
    else:
        needs.append(f"已写库（经 {who} 确认）。下一步：把下面 {len(sends)} 条页面文案"
                     "交给发布的人，在后台发出去——本工具不发送。")
    if plan.needs_decision():
        needs.append(f"还有 {len(plan.needs_decision())} 条落在「掌握度待定」那格"
                     "（0 分但过程证据进过 P），口径未定，先别对它们做任何结论。")
    if report.counted().get("needs_review"):
        needs.append(f"{report.counted()['needs_review']} 份差值超过复核阈值，"
                     "需要人逐份看一下再发文案。")
    if report.counted().get("retired"):
        needs.append(f"{report.counted()['retired']} 份已标「确认不可回溯」，本次没有重试；"
                     "它们仍在交代名单里，文案照发。")

    return CycleReport(
        task_id=task["task_id"],
        reason=reason,
        changed_on=changed_on,
        what_changed=what_changed,
        applied=real is not None,
        approved_by=who,
        counted=report.counted(),
        notices=tuple(n.to_dict() for n in plan.notices),
        pending_sends=sends,
        needs_human=tuple(needs),
        created_at=stamp,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跑一次「判据改动 → 跑批 → 交代」（默认演练）")
    ap.add_argument("--db", required=True, help="sqlite 库路径")
    ap.add_argument("--task", default=str(ROOT / "tasks" / "dbg-001-order-report.yaml"))
    ap.add_argument("--reason", required=True, help="为什么跑这一批")
    ap.add_argument("--changed-on", required=True, help="判据修订日期（人给的事实）")
    ap.add_argument("--what-changed", required=True, help="改了哪一条、为什么（一句人话）")
    ap.add_argument("--approved-by", default=None,
                    help="确认人；**不给就只演练**（不写库）")
    ap.add_argument("--archive", default=None, help="归档目录（必须不存在或为空）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    conn = store.connect(Path(args.db))
    try:
        report = run_cycle(conn, dr.load_task(args.task), reason=args.reason,
                           changed_on=args.changed_on, what_changed=args.what_changed,
                           approved_by=args.approved_by)
        if args.archive:
            paths = report.archive(Path(args.archive))
            print(f"归档：{paths['markdown']}")
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
              if args.json else report.markdown())
        return 0
    except (CycleError, bl.BaselineError, nt.NoticeError, dr.ReportError) as exc:
        print(f"停下：{exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
