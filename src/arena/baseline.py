"""B-04f：全库基线——把「现行判据 × 全库历史」打平一次。

## 为什么还要这一步

`reappraisal` 回答的是"**一份**提交怎么按新判据重算"，`scope_for_task` 回答的是
"**这个任务**里谁对不上"。两者合起来已经能跑一条完整的回溯。但历史数据有一个
绕不过去的缺口：

**v5 之前的报告里没有 `rubric_fingerprint`**（当时没这个概念）。所以它们的指纹是
"不知道"，`scope_for_task` 只能把它们全部归进 `unfingerprinted` 桶。也就是说：
从现在起的新提交能按指纹只跑增量，而**全部历史每次都得整批重评**——那正是
"②是①的止损"这句话里的①还欠着的那一次。

本模块做的就是这一次：拿现行判据把全库跑一遍，让每份**能重评的**提交都得到一份
带指纹的生效报告。跑完之后 `unfingerprinted` 只剩"补不上"的那批，之后只靠指纹跑增量。

## 它与 `reappraisal.py` 的分工

| 层 | 关心什么 |
|---|---|
| `reappraisal` | 一份提交**算得对不对**（结论、证据、P 有没有跟着动） |
| `baseline`（本模块） | 整批**跑完能不能交代**（多少份补不上、谁的分数变了、能不能重跑） |

分开的理由是判据不同：前者要正确性，后者要**可交代性**。合成一个函数会出现
"结果对了但没人知道有多少份被跳过"的批次——那正是这一层最该防的失效。

## 四条性质（都写成判据了）

1. **不重复改分**：跑完立刻再跑一次，**不产生任何新修订**（重评链长度不变）。
   靠的是 `scope_for_task` 比对**生效报告**（`reappraisal` 那边的 M16）。

   注意它**不等于"第二次没有活干"**：补不上的那批（缺 trace / 缺产物）永远留在
   `unfingerprinted` 桶里，于是每次跑都会重新尝试一遍、再报一次 `missing_*`。
   这是刻意的——那条记录从来没被修好，把它从范围里剔掉等于让"还剩谁"消失。
   代价是 `scanned` 永远不会归零；收益是"我们回溯过了"这句话永远带着后缀。
2. **不静默跳过**：`missing_trace` / `missing_artifacts` 逐条留在 `outcomes` 里，
   并且跑完重算范围，看 `unfingerprinted` 还剩谁——"补不上的是哪些"必须能指名。
3. **演练与真跑同数**：同一段代码，只差 `apply`。演练不落库，所以它的
   `after` 与 `before` 必然相同——这不是缺陷，正是"确实什么都没写"的证据。
4. **中途抛错可安全重跑**：每份提交的重评与刷新在同一笔事务里（B-07d ③），
   失败的那份不落库；重跑时已处理过的不会重复进范围。

## 刻意不做

- **不自动触发**。"判据改动在哪登记、谁批准、跑完要不要通知学生"是流程问题，
  不是这一层能定的；本模块只回答"跑一遍会发生什么、跑完剩什么"。
- **不碰 `assert_score` / 不删旧报告 / 不新增 submissions 行**：与 `reappraisal`
  同一口径，历史证据不动。
- **不为性能牺牲可交代性**：逐份 `reappraise()`，每份各自刷一次该用户快照。
  代价是同一用户 N 份提交会刷 N 次（每次重放该用户全部历史），
  在演示规模（数十份）可接受；真到上千份要改成"按用户合并刷新一次"，
  那需要先测量再动手，现在不做。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

try:  # 作为包被 import
    from . import reappraisal as rap
except ImportError:  # 作为独立脚本/被单文件测试直接 import
    import reappraisal as rap  # type: ignore[no-redef]


class BaselineError(RuntimeError):
    """前置条件不成立（任务列表重复/为空、参数非法）。**一律抛，不静默降级。**"""


@dataclass(frozen=True)
class BaselineReport:
    """一次基线的完整交代：跑之前看到什么、跑了什么、跑完剩什么。

    `before` / `after` 都是 `task_id -> ReappraisalScope`。放两份而不是只放一份，
    是因为这一层唯一能自证"到底写进去没有"的方式就是**前后对比**：
    演练时两者必然相同；真跑时 `after.stale` 才可能变空。
    """

    reason: str
    applied: bool
    review_threshold: float
    before: dict[str, rap.ReappraisalScope]
    outcomes: tuple[rap.ReappraisalOutcome, ...]
    after: dict[str, rap.ReappraisalScope]

    # ── 派生：给"报给人看"的那些数 ──────────────────────────────────────────
    def counted(self) -> dict[str, int]:
        """按状态计数。**每种状态都有一格**，不合并——合并会让"补不上"消失。

        `scanned` 是跑之前的范围大小；`still_*` 是跑完之后的范围。
        真跑时 `scanned = reappraised + missing_*`，演练时 `scanned = dry_run`。

        **`still_unfingerprinted` 不会因为跑过就变 0**：补不上的那批仍留在桶里，
        下次跑还会被重新尝试。所以判"打平了没有"看的是它**等于补不上的那批**，
        不是"等于 0"。两者不等才说明有提交既没补上、也没被报出来。
        """
        by_state: dict[str, int] = {}
        for out in self.outcomes:
            by_state[out.state] = by_state.get(out.state, 0) + 1
        changed = sum(1 for out in self.outcomes
                      if out.delta is not None and abs(out.delta) > 1e-12)
        return {
            "scanned": sum(len(s.all_ids) for s in self.before.values()),
            "reappraised": by_state.get("reappraised", 0),
            "dry_run": by_state.get("dry_run", 0),
            "missing_trace": by_state.get("missing_trace", 0),
            "missing_artifacts": by_state.get("missing_artifacts", 0),
            "score_changed": changed,
            "needs_review": sum(1 for out in self.outcomes if out.needs_manual_review),
            "still_stale": sum(len(s.stale) for s in self.after.values()),
            "still_unfingerprinted": sum(len(s.unfingerprinted) for s in self.after.values()),
            "current": sum(len(s.current) for s in self.after.values()),
        }

    def changed(self) -> tuple[rap.ReappraisalOutcome, ...]:
        """分数真的变了的那些。**这是要通知学生的名单**，所以要能逐条指认。"""
        return tuple(out for out in self.outcomes
                     if out.delta is not None and abs(out.delta) > 1e-12)

    def blocked(self) -> tuple[rap.ReappraisalOutcome, ...]:
        """补不上的那些（缺 trace / 缺产物）。不许当成"处理完了"。"""
        return tuple(out for out in self.outcomes if out.state.startswith("missing_"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "applied": self.applied,
            "review_threshold": self.review_threshold,
            "counted": self.counted(),
            "tasks": {
                tid: scope.to_dict() for tid, scope in self.before.items()
            },
            "remaining": {
                tid: {"stale": list(s.stale), "unfingerprinted": list(s.unfingerprinted)}
                for tid, s in self.after.items()
            },
            "outcomes": [out.to_dict() for out in self.outcomes],
        }


def _check_tasks(tasks: Iterable[dict]) -> dict[str, dict]:
    """规范化任务列表。返回**按 task_id 排序**的字典，重复直接抛。

    重复要抛而不是去重的理由：同一份 rubric 出现两次，要么说明调用方把两个
    不同版本的任务当成了同一个（那重评用的到底是哪版？），要么说明它只是想
    跑两遍（那就是幂等性该挡的事，不该靠调用方自觉）。
    悄悄去重会让第一种情况变成"用了先出现的那个"，事后无从发现。
    """
    out: dict[str, dict] = {}
    for task in tasks:
        if not isinstance(task, dict) or not task.get("task_id"):
            raise BaselineError("tasks 里每一项都必须是已加载的任务定义（含 task_id）")
        tid = task["task_id"]
        if tid in out:
            raise BaselineError(
                f"任务 {tid!r} 出现了两次——同一份判据跑两遍会把重评链写成两条，"
                "而且事后看不出哪条是哪个版本判的"
            )
        out[tid] = task
    return {tid: out[tid] for tid in sorted(out)}


def all_task_scopes(conn, tasks: Iterable[dict]) -> dict[str, rap.ReappraisalScope]:
    """每个任务在现行判据下的范围（只读）。顺序按 `task_id`，便于逐次比对。"""
    return {tid: rap.scope_for_task(conn, task) for tid, task in _check_tasks(tasks).items()}


def run_baseline(
    conn,
    tasks: Iterable[dict],
    *,
    reason: str,
    apply: bool = True,
    review_threshold: float = rap.REVIEW_THRESHOLD,
    created_at: str | None = None,
) -> BaselineReport:
    """把「现行判据 × 全库」打平一次。`apply=False` 是演练（只算不写）。

    `reason` 会写进每一行 `reappraisals`，是事后复核"这批为什么被重算"的唯一线索，
    所以在这里先验一次再把活干起来：验在第一次 `reappraise()` 里也可以，
    但如果范围是空的，坏参数会静默通过——那时人以为"跑过了"，其实什么都没跑。
    """
    if not isinstance(reason, str) or not reason.strip():
        raise BaselineError("reason 必须写：这是「为什么整批重算」的唯一线索")

    by_id = _check_tasks(tasks)
    before = {tid: rap.scope_for_task(conn, task) for tid, task in by_id.items()}

    outcomes: list[rap.ReappraisalOutcome] = []
    for tid, task in by_id.items():
        # 范围在循环里用 before 里那份：它是"跑之前"的定论，跑的过程中不重算，
        # 否则前一份重评会改变后一份看到的库，批次就不可复现了。
        for sid in before[tid].all_ids:
            outcomes.append(
                rap.reappraise(conn, sid, task, reason=reason,
                               review_threshold=review_threshold,
                               apply=apply, created_at=created_at)
            )

    after = {tid: rap.scope_for_task(conn, task) for tid, task in by_id.items()}
    return BaselineReport(
        reason=reason,
        applied=apply,
        review_threshold=float(review_threshold),
        before=before,
        outcomes=tuple(outcomes),
        after=after,
    )
