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

   **唯一的出口是显式标记**（v6 / B-04h ②）：人确认过"这份产物不可能再回来"之后，
   用 `store.mark_unrecoverable()` 打一个带理由、带人名、可撤销的标记，该行从此
   进 `retired` 而不再被逐次重试。**出口必须是人给的、留痕的、可撤回的**——
   自动判定"试了三次就算了"会把"平台当年没存产物"变成"这个人不重要"。
   标记只影响重试，不影响交代：那批人仍在 `tier_split()` 与 `notices` 里。
2. **不静默跳过**：`missing_trace` / `missing_artifacts` 逐条留在 `outcomes` 里，
   并且跑完重算范围，看 `unfingerprinted` 还剩谁——"补不上的是哪些"必须能指名。
3. **演练与真跑同数**：同一段代码，只差 `apply`。演练不落库，所以它的
   `after` 与 `before` 必然相同——这不是缺陷，正是"确实什么都没写"的证据。
4. **中途抛错可安全重跑**：每份提交的重评与刷新在同一笔事务里（B-07d ③），
   失败的那份不落库；重跑时已处理过的不会重复进范围。

## 「补不上」那批的分档（B-04g 的取数层）

`blocked()` 把"补不上"报成一个数，但那一类里混着两种处境：当年判过给过分的
（现在判不了，必须解释），和当年本来就没分的（没什么可解释）。拿一个总数去设计
交代方案，等于为一个还没被数过的规模拍板。`tier_split()` 把它切成 T1/T2/T3，
**只有 T1 进交代名单**。

分档是**事实**（当年有没有分、有没有可计入的通过证据），不是政策：
拿到名单之后是保留原分还是让学生重做，是 B-04g 要人拍的，本模块不做选择。

一个和测试有关的坑（写在这里，免得下一个人重踩）：**"补不上"有两种来源**——
产物没存下来（`artifacts` 列 v4 才有，当年的报告是看着产物判的，可以带 1.0 分）、
和当年学生就没交产物（判出来就是 0 分）。两者在库里都是 `artifacts IS NULL`，
但只按后者造样本，T1 会恒为 0，于是结论是"没有人需要交代"——那个答案是构造方式给的。

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
    from . import diagnosis_report as dr
    from . import mastery as ms
    from . import reappraisal as rap
    from . import store
except ImportError:  # 作为独立脚本/被单文件测试直接 import
    import diagnosis_report as dr  # type: ignore[no-redef]
    import mastery as ms  # type: ignore[no-redef]
    import reappraisal as rap  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]


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

        `retired`（v6）单独一格，**不算进 `scanned`**：这些行已标「确认不可回溯」，
        本次没有重试它们。把它并进 `scanned` 会让"这次扫了多少"虚高，
        并进 `missing_artifacts` 会让"还剩谁要重试"虚高；两处都会让人误判规模。
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
            "retired": sum(len(s.retired) for s in self.before.values()),
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
                tid: {"stale": list(s.stale), "unfingerprinted": list(s.unfingerprinted),
                      "retired": list(s.retired), "ignored_marks": list(s.ignored_marks)}
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


# ── 「补不上」那批的分档（B-04g 的取数层）──────────────────────────────────────
# 为什么要有这一步：`blocked()` 把"补不上"当成一类报，但那一类里混着两种完全不同的处境——
# 当年判过、给过分的人（现在判不了，必须交代），和当年本来就没分的人（没什么可解释）。
# 把一个总数摆到人面前让他拍"怎么交代"，等于为一个还没被数过的规模设计政策。
#
# 分档是**事实**，不是政策：它只回答"这份提交当年有没有分、有没有可计入的通过证据"。
# 至于拿到名单之后是保留原分、还是让学生重做，是 B-04g 要人拍的，本函数不做选择。

TIERS = ("T1", "T2", "T3")

TIER_TITLES = {
    "T1": "当年有分（判过、给过分，现在判不了 —— 要交代的是这一档）",
    "T2": "当年 0 分且无可计入证据（本来就没有分数可解释）",
    "T3": "当年 0 分但有可计入的通过证据（分数 0，技能证据照样进了 P）",
}


@dataclass(frozen=True)
class BlockedItem:
    """一份补不上的提交，连同它落在哪一档。

    `positive_skills` 是"这份提交给哪些技能留下过正向证据"（`passed_weight > 0`）。
    它是 T3 的判据，也是把 T3 与 B-07b（门禁口径）对上号的依据——
    那批人的分数是 0，但掌握度被这份提交抬过。
    """

    submission_id: str
    user_id: str
    task_id: str
    ratio: float
    tier: str
    positive_skills: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "user_id": self.user_id,
            "task_id": self.task_id,
            "ratio": self.ratio,
            "tier": self.tier,
            "positive_skills": list(self.positive_skills),
        }


@dataclass(frozen=True)
class TierSplit:
    """全库"补不上"那批的分档结果。`total` 是全库提交数，用于算占比。"""

    total: int
    blocked: tuple[BlockedItem, ...]

    def tier(self, name: str) -> tuple[BlockedItem, ...]:
        if name not in TIERS:
            raise BaselineError(f"未知档位 {name!r}，只有 {TIERS}")
        return tuple(item for item in self.blocked if item.tier == name)

    def needs_explanation(self) -> tuple[BlockedItem, ...]:
        """真正需要向学生交代的那批（T1）。**其余两档不许混进来充数。**"""
        return self.tier("T1")

    def users(self, name: str) -> tuple[str, ...]:
        return tuple(sorted({item.user_id for item in self.tier(name)}))

    def counted(self) -> dict[str, int]:
        return {
            "total": self.total,
            "blocked": len(self.blocked),
            "T1": len(self.tier("T1")),
            "T2": len(self.tier("T2")),
            "T3": len(self.tier("T3")),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "counted": self.counted(),
            "tiers": {
                name: [item.to_dict() for item in self.tier(name)] for name in TIERS
            },
        }


def classify_blocked(rows: Iterable[dict], skill_ids: Iterable[str], *, total: int) -> TierSplit:
    """把 `store.blocked_submissions()` 的行分三档。纯函数：不读库、不写库。

    判据（按顺序，先命中先落档）：

    | 档 | 条件 | 为什么这么切 |
    |---|---|---|
    | T1 | `ratio > 0` | 判过、给过分，现在判不了 ⇒ 唯一必须解释的一档 |
    | T3 | `ratio == 0` 且某技能 `passed_weight > 0` | 分数 0，但过程证据进了 P；与 B-07b 同批人 |
    | T2 | 其余 | 既没分、也没留下正向证据 ⇒ 没有可解释的东西 |

    **作废（`score.voided`）的天然落 T2**：`skill_evidence()` 对作废提交一律不计入，
    所以它的 `positive_skills` 为空；这是刻意的，不作废单列一档——作废的含义就是
    "这次不算数"，它既没有分要解释，也没有证据需要撤回。

    反过来说清一件事：**T2 不等于"完全没影响"**。它没有*分数*可解释，但它的失败证据
    （`state=fail` 且权重 > 0）照样在 P 里，而且因为补不上，那份证据**永远停在旧判据上**。
    这一点写在这里是为了不要有人把 T2 读成"这些人跟我们改标准无关"。
    `total` 要显式传：占比是给人看的第一句话，而"全库多少份"不可能从"补不上的那些"
    推出来——偷偷用 `len(rows)` 顶替，报出来的占比会永远接近 100%。
    """
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise BaselineError(f"total 必须是非负整数（全库提交数），收到 {total!r}")
    skills = tuple(skill_ids)
    items: list[BlockedItem] = []
    for row in rows:
        sid = row["submission_id"]
        report = row["report"]
        try:
            positive = tuple(
                skill for skill in skills
                if ms.skill_evidence(report, skill, at=row["created_at"]).passed_weight > 0
            )
        except ms.MasteryError as exc:
            raise BaselineError(
                f"{sid} 的报告无法判断它有没有计入掌握度的证据：{exc}"
            ) from exc
        ratio = float(row["ratio"])
        if ratio > 0:
            tier = "T1"
        elif positive:
            tier = "T3"
        else:
            tier = "T2"
        items.append(BlockedItem(
            submission_id=sid,
            user_id=row["user_id"],
            task_id=row["task_id"],
            ratio=ratio,
            tier=tier,
            positive_skills=positive,
        ))
    return TierSplit(total=total, blocked=tuple(items))


def tier_split(conn) -> TierSplit:
    """全库分档：`store.blocked_submissions()` + `classify_blocked()`。只读。"""
    return classify_blocked(
        store.blocked_submissions(conn),
        store.list_skill_ids(conn),
        total=store.count_submissions(conn),
    )


# ── 「这条改过的规则，谁的提交真的用到了」（B-04g 取数层的第二段）─────────────
# 分档回答"这批人里谁有分数需要解释"，本段回答"其中谁真的受这次改动影响"。
# 两段的差别是**这个问题是不是凭空问的**：整份指纹只能说出"这个任务全体都该重评"，
# 于是"要交代的人"被默认成了"补不上的全部"。而实际需要解释的，只是当年那次判定
# **真的用到了被改的那条规则**的人——先把这个数跑出来，再谈怎么交代。

EXPOSURE_STATUSES = ("touched", "added_only", "not_applied", "unknown", "untouched")


@dataclass(frozen=True)
class RuleTouch:
    """一份提交里，某条被改过的规则当年判成了什么。`state` 直接来自报告断言。"""

    rule_id: str
    state: str

    def to_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "state": self.state}


@dataclass(frozen=True)
class RuleDiff:
    """现行判据相对某份历史报告，在**规则级别**上的差异。

    `changed` / `added` / `removed` 三种要分开，因为它们对"影响"的结论不同：
      - `changed`：这条规则当时判过，现在判据文字变了 ⇒ 拿它当年的状态就能判断"要不要解释"；
      - `added`：现在多了这条规则，**当年那份提交根本没被它判过** ⇒ 从报告里读不出
        "它在新判据下会不会不通过"，所以不能算"没受影响"；
      - `removed`：当年判过、现在不判了 ⇒ 与 `changed` 同样按"当年状态"看。
    """

    changed: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]

    def all(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.changed) | set(self.added) | set(self.removed)))

    def is_empty(self) -> bool:
        return not (self.changed or self.added or self.removed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": list(self.changed),
            "added": list(self.added),
            "removed": list(self.removed),
        }


def rule_diff(before: Any, task: dict) -> RuleDiff | None:
    """把一份报告里的单条指纹与现行判据比一遍。**读不到就返回 `None`，不返回"没变"。**

    `None` 是"不知道"（历史报告没有 `rubric_rule_fingerprints` 这个键），
    与 `RuleDiff()`（确实一条都没变）是两件事。混成一个，就是"不认识当合格"——
    B-04e 里"读不到指纹当已符合"让全部历史静默漏掉，同一个坑。
    """
    if before is None:
        return None
    if not isinstance(before, dict):
        raise BaselineError(
            f"rubric_rule_fingerprints 应是对象，收到 {type(before).__name__}——"
            "坏数据不该被当成「规则没变」"
        )
    for rid, value in before.items():
        if not isinstance(rid, str) or not isinstance(value, str) or not value:
            raise BaselineError(f"rubric_rule_fingerprints 里有非法项：{rid!r} -> {value!r}")
    if not before:
        raise BaselineError(
            "rubric_rule_fingerprints 是空对象——合法任务至少有一条规则，空对象等于说"
            "「当时没有任何规则」，那是坏数据，不是「没有改动」"
        )
    now = dr.rubric_rule_fingerprints(task)
    return RuleDiff(
        changed=tuple(sorted(r for r in now if r in before and now[r] != before[r])),
        added=tuple(sorted(r for r in now if r not in before)),
        removed=tuple(sorted(r for r in before if r not in now)),
    )


# "这条规则当年真的按其判据判过"的状态集合。`blocked` / `unscored` 都不是判过：
# 前者是**前置断言没过**（本断言未进入判据）、后者是**前置材料缺失**（没上报 trace 等）。
# 两者对"改这条规则的判据会不会改这份分"的回答都是"不会"——因为它的判据根本没参与。
# 这条区分就是 B-04g 外部意见里那个"这次练习里到底有没有触发那条写错的规则"，
# 在库里唯一能被机器回答的形式。
JUDGED_STATES = ("pass", "fail")


@dataclass(frozen=True)
class RuleExposure:
    """一份补不上的提交，与"这次改过的那几条规则"的关系。

    `status` 五态，**不许合并**（顺序即判定优先级）：

    | status | 判据 | 为什么不能并进别的格 |
    |---|---|---|
    | `unknown` | 报告没有单条指纹 | "不知道"不能当"没变"，也不能当"没碰" |
    | `touched` | 变更/删除的规则里至少一条判成了 `pass`/`fail` | 判据真的参与了 ⇒ 分数可能因这次改动而不准 |
    | `added_only` | 没判过，但现行判据**新增**了规则 | 当年没被这条判过 ⇒ 读不出它在新判据下会不会不过 |
    | `not_applied` | 变更/删除的规则全部是 `blocked`/`unscored` | 判据没参与 ⇒ 改它改不动这份分（唯一可核对的"没触发"） |
    | `untouched` | 逐条比完一条差异都没有 | 这次改动跟这个任务无关，压根不用谈影响 |

    **`not_applied` 与 `untouched` 的区别要守住**：前者是"改了它、但它当年没参与判定"，
    后者是"根本没改到它"。合并会让人以为"这个任务这次没动"，从而漏掉真正该做的事——
    确认那条规则当时确实没执行（比如学生当年没交 trace，断言全 `unscored`）。
    """

    submission_id: str
    user_id: str
    tier: str
    ratio: float
    status: str
    touches: tuple[RuleTouch, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "user_id": self.user_id,
            "tier": self.tier,
            "ratio": self.ratio,
            "status": self.status,
            "touches": [t.to_dict() for t in self.touches],
        }


@dataclass(frozen=True)
class ExposureReport:
    """一个任务下，"补不上"的提交 × 这次改过哪些规则 的交叉表。只读。

    `changed_rules` 是**各份提交各自差异的并集**，不是"一个全局新旧版本差"：
    同一个任务的补不上那批可能来自好几个历史版本（判据改过多次），
    拿最后一次的差异去套全部人，会把早先版本改过的规则说成"当时没碰"。
    逐份比、再取并集，代价是多算几次 sha256，换来"每份的结论都针对它自己那版"。
    """

    task_id: str
    changed_rules: tuple[str, ...]
    rows: tuple[RuleExposure, ...]

    def counted(self) -> dict[str, int]:
        """按状态计数。**五态各一格**（`unknown` 就是"没指纹"那一格，不并进 untouched）。"""
        by_state: dict[str, int] = {name: 0 for name in EXPOSURE_STATUSES}
        for row in self.rows:
            by_state[row.status] += 1
        return {"rows": len(self.rows), **by_state}

    def by_tier(self, name: str) -> tuple[RuleExposure, ...]:
        if name not in TIERS:
            raise BaselineError(f"未知档位 {name!r}，只有 {TIERS}")
        return tuple(row for row in self.rows if row.tier == name)

    def must_explain(self) -> tuple[RuleExposure, ...]:
        """**缩窄之后**真正需要交代的名单：T1 且判据真的参与过判定（或读不出来）。

        剔掉的两格都有可核对的事实依据，不是"看起来没事"：
          - `not_applied`：那几条改过的规则当年判成 `blocked`/`unscored`，判据没参与，
            改它不可能改这份分；
          - `untouched`：这个任务这次一条规则都没改（差异为空）。
        `unknown` 与 `added_only` **必须留在名单里**：留着的代价是多说一句；
        剔掉的代价是有人被永久静默跳过（B-04e 那个"读不到指纹当已符合"的同一个坑）。
        """
        return tuple(row for row in self.rows
                     if row.tier == "T1" and row.status in ("touched", "added_only", "unknown"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "changed_rules": list(self.changed_rules),
            "counted": self.counted(),
            "rows": [row.to_dict() for row in self.rows],
        }


def exposure_split(conn, task: dict) -> ExposureReport:
    """一个任务下，"补不上"的提交各自碰没碰到这次改过的规则。只读。

    **必须按 task_id 收范围**：别的任务的规则 id 可能同名（R1…R5 是骨架共用的），
    不收范围会把"另一个任务改了 R3"算成"这个任务的人也受影响"，
    与 B-04e 里"丢了 task_id 过滤 ⇒ 改一次判据触发全库重评"同一类错误。

    **不许只报 T1**：T3（分数 0 但证据进了 P）同样被冻在旧判据上，
    只报 T1 会让它从视野里消失；名单收窄是 `must_explain()` 的事，不是取数的事。
    """
    tid = task.get("task_id") if isinstance(task, dict) else None
    if not isinstance(tid, str) or not tid:
        raise BaselineError("task 必须是已加载的任务定义（含 task_id）")

    rows = [row for row in store.blocked_submissions(conn) if row["task_id"] == tid]
    split = classify_blocked(rows, store.list_skill_ids(conn),
                             total=store.count_submissions(conn))
    tier_by_id = {item.submission_id: item.tier for item in split.blocked}

    exposures: list[RuleExposure] = []
    union: set[str] = set()
    for row in rows:
        report = row["report"]
        diff = rule_diff(report.get("rubric_rule_fingerprints"), task)
        if diff is None:
            status, touches = "unknown", ()
        else:
            union |= set(diff.all())
            seen: dict[str, str] = {}
            for assertion in report.get("assertions") or []:
                rid = assertion.get("id") if isinstance(assertion, dict) else None
                if rid in diff.all():
                    seen[rid] = str(assertion.get("state"))
            touches = tuple(RuleTouch(rid, seen[rid]) for rid in sorted(seen))
            judged = any(t.state in JUDGED_STATES for t in touches)
            if judged:
                status = "touched"
            elif diff.added:
                status = "added_only"
            elif touches:
                status = "not_applied"
            else:
                status = "untouched"
        exposures.append(RuleExposure(
            submission_id=row["submission_id"],
            user_id=row["user_id"],
            tier=tier_by_id[row["submission_id"]],
            ratio=row["ratio"],
            status=status,
            touches=touches,
        ))
    order = {name: i for i, name in enumerate(TIERS)}
    exposures.sort(key=lambda r: (order[r.tier], r.submission_id))
    return ExposureReport(task_id=tid, changed_rules=tuple(sorted(union)),
                          rows=tuple(exposures))
