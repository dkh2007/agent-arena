"""B-04d 接线：把「裁决 → 重评 → 落差异」接成一条线。

前三轮分别做完了链上的三块，但它们是断开的：

| 环节 | 已有在哪 |
|---|---|
| 争议被发现、进待办 | `judge_pipeline.record_arbitration()`（round-90） |
| 人工裁决、记理由 | `store.resolve_arbitration()`（round-90） |
| 成绩能重算 | `mastery_snapshot.recompute_all()`（round-84） |
| 重评的**输入**能拿到 | `submissions.artifacts`（round-91） |

缺的是中间那截：**拿改后的判据，把一份历史作答重新打一遍分，并把新结论存下来**。
没有它，人裁决完写一句"判据要改"，成绩一分不动（上一轮 §91.5 ② 记的就是这个）。

## 这一层刻意不做的事

1. **不重跑 judge**。重评的对象是断言的判据（rubric），不是"AI 评委两次给的分"。
   新报告里因此**没有** `judge_scores` / `judge_agreement`——不是丢了，是这次没评。
   把旧值抄进新报告才是错的：读报告的人会以为 AI 评委按新判据也评过一遍。
2. **不碰 `submissions` 一行**。不新增行（否则同一份作答在 P 的重放里算两个 step），
   也不覆盖 `diagnosis`（那是"当时按什么判的"的唯一证据）。新结论进 `reappraisals`。
3. **不自己造判据**。改后的 `task` 由调用方传进来——"标准改成什么"是人的判断，
   本模块只负责"按你给的标准重新算一遍"。
4. **不改 P 公式**。落库后调 `refresh_user()`，P 仍只有 `mastery.build_series()` 一处定义。

## 缺失产物怎么办（保守版，写清取舍）

`artifacts` 为 `NULL` 的提交**不重评**，`state="missing_artifacts"`，成绩不动。

保守的理由：用 `{}` 顶上去重评会得到"没交产物"的结论，而那份产物也许只是没存下来——
**把平台的历史缺陷算成学习者没做**，正是决策 7 要挡的事。

代价要认：v4 之前落库、而且当时确实交了产物的提交，永远重评不了。那部分只能
"让学生重做"或"承认不可回溯"，不是本模块能修的。**不做"猜产物"的启发式**。

## 复核阈值

`REVIEW_THRESHOLD = 0.02` 与争议护栏同源（B-09 排程层的平手格）：小于它的差异不足以
改变任何排序结论，所以不拦人。**未标定**，登记在 B-04c 一类问题里，不给默认值以外的解释。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:  # 作为包被 import
    from . import diagnosis_report as dr
    from . import mastery_snapshot as ms
    from . import store
    from . import trace_pipeline as tp
except ImportError:  # 作为独立脚本/被单文件测试直接 import
    import diagnosis_report as dr  # type: ignore[no-redef]
    import mastery_snapshot as ms  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]
    import trace_pipeline as tp  # type: ignore[no-redef]

# 差异超过这个值就标"需人工复核"。见模块文档末节：它是借来的数，不是标定结果。
REVIEW_THRESHOLD = 0.02

# `state` 的全部取值。用词表而不是散落的字符串，读结果的一方能一眼看全可能性。
STATES = (
    "reappraised",         # 重评成功，已落库（新结论可能与旧结论相同，那也算重评过）
    "dry_run",             # 只算不落库
    "missing_trace",       # 没有 trace ⇒ 连断言都没法重跑
    "missing_artifacts",   # 没有原始产物 ⇒ 拒绝用空对象顶替
)


class ReappraisalError(RuntimeError):
    """前置条件不成立（任务对不上、参数非法）。**一律抛，不静默降级。**"""


@dataclass(frozen=True)
class ReappraisalOutcome:
    """一份提交重评的结果。`delta` 为 `None` 表示"没算出差值"（见各 `state`）。

    `user_id`（round-95 / B-04f 补）：重评的产物最终是"要告诉谁他的成绩变了"，
    只给 `submission_id` 的话调用方还得自己回查一次库——而回查那一步没人会做，
    结果就是基线跑完了、报告很漂亮、**没有一个人知道自己被重算过**。
    """

    submission_id: str
    state: str
    user_id: str | None = None
    old_ratio: float | None = None
    new_ratio: float | None = None
    delta: float | None = None
    needs_manual_review: bool = False
    revision: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "user_id": self.user_id,
            "state": self.state,
            "old_ratio": self.old_ratio,
            "new_ratio": self.new_ratio,
            "delta": self.delta,
            "needs_manual_review": self.needs_manual_review,
            "revision": self.revision,
        }


def _check_task(task: Any) -> dict:
    if not isinstance(task, dict) or not task.get("task_id"):
        raise ReappraisalError("task 必须是已加载的任务定义（含 task_id）")
    if not task.get("rubric_credit") and not task.get("rubric_gate"):
        raise ReappraisalError(
            f"任务 {task.get('task_id')!r} 没有任何 rubric —— 拿它重评会把每份提交算成 0 分"
        )
    return task


def _check_threshold(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReappraisalError(f"review_threshold 必须是数值（收到 {value!r}）")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ReappraisalError(f"review_threshold 应在 [0,1]（收到 {value!r}）")
    return number


def rebuild_report(
    conn,
    submission_id: str,
    task: dict,
    *,
    unreplayable_as_state: bool = True,
) -> tuple[dict | None, str]:
    """按传入的 `task` 把这份作答重新打一遍分。返回 `(新报告, state)`。

    `state != "reappraised"` 时报告为 `None`——**不返回一份"看起来正常"的空报告**，
    那会让调用方拿它落库，把"不可重评"洗成"重评后 0 分"。

    输入只用**库里存的** trace + artifacts，不碰磁盘夹具：重评的语义是
    "历史证据 + 新判据"，任何来自当前工作目录的东西都不该混进来。
    """
    task = _check_task(task)
    row = store.load_submission(conn, submission_id)      # 不存在就抛

    if row["task_id"] != task["task_id"]:
        raise ReappraisalError(
            f"{submission_id} 是任务 {row['task_id']!r} 的作答，传进来的是 "
            f"{task['task_id']!r} 的判据——拿别的题的 rubric 重评会得到一个毫无意义的分数"
        )
    if row["trace"] is None:
        return None, "missing_trace"
    if row["artifacts"] is None:
        return None, "missing_artifacts"

    # 基线取**当前生效**那份：若已重评过一次，第二次重评的起点是上一次的结论，
    # 否则 delta 会一直拿"最原始"的分数比，看不出这次改了什么。
    base = store.effective_report(conn, submission_id)
    raw = tp.assemble_raw(base["submission"], task,
                          trace=row["trace"], artifacts=row["artifacts"])
    raw = tp.recompute_all(raw, task=task)
    new_report = dr.build_report(task, raw)

    # 身份与时刻以库为准，不采信任何一面重新编的值：
    # 这两样一旦变了，落库时会被拒绝（save_reappraisal 的校验），早失败早说。
    sub = new_report.setdefault("submission", {})
    sub["submission_id"] = submission_id
    sub["task_id"] = row["task_id"]
    if base["submission"].get("evaluated_at"):
        sub.setdefault("evaluated_at", base["submission"]["evaluated_at"])
    return new_report, "reappraised"


def reappraise(
    conn,
    submission_id: str,
    task: dict,
    *,
    reason: str,
    review_threshold: float = REVIEW_THRESHOLD,
    apply: bool = True,
    created_at: str | None = None,
) -> ReappraisalOutcome:
    """重评一份作答；`apply=True` 时把新报告落进 `reappraisals` 并刷新该用户快照。

    `apply=False` 是演练：只算差异、不落库、不刷 P——**用来在真正动手前看一眼
    "这一改会让多少人掉分"**。演练结果与真跑结果必须一致，否则演练没有意义
    （测试里拿同一次输入跑两遍对比）。
    """
    limit = _check_threshold(review_threshold)
    if not isinstance(reason, str) or not reason.strip():
        raise ReappraisalError(
            "reason 必须写：这是「哪一条判据改了」的唯一线索；没有它的重评事后无法复核"
        )

    old_ratio = store.effective_ratio(conn, submission_id)
    # 顺带取 user_id：两种 state（重评成功 / 补不上）都要能指名到人，
    # 所以放在分叉之前，别让"缺产物的那批"变成一群没有主人的 submission_id。
    user_id = store.load_submission(conn, submission_id)["user_id"]
    new_report, state = rebuild_report(conn, submission_id, task)

    if new_report is None:
        return ReappraisalOutcome(
            submission_id=submission_id, state=state, user_id=user_id, old_ratio=old_ratio,
        )

    new_ratio = float(new_report["score"]["ratio"])
    delta = new_ratio - old_ratio
    needs_review = abs(delta) >= limit

    revision = None
    if apply:
        # 落库与刷新同一笔（B-07d ③ 的同一条理由）：重评记录写进去了、P 没刷，
        # 就会得到"档案里是新结论、成绩还是旧的"——而且这种不一致没有任何断言看得见。
        with store.atomic(conn):
            row = store.save_reappraisal(conn, submission_id, new_report,
                                         reason=reason, created_at=created_at)
            revision = int(row["revision"])
            # 刷新该用户全部技能的快照：重评可能让某个技能从"有证据"变成"证据变弱"，
            # 也可能影响本提交没直接压到的技能（P 是全量重放的累积量）。
            ms.refresh_user(conn, user_id)

    return ReappraisalOutcome(
        submission_id=submission_id,
        state="reappraised" if apply else "dry_run",
        user_id=user_id,
        old_ratio=old_ratio,
        new_ratio=new_ratio,
        delta=delta,
        needs_manual_review=needs_review,
        revision=revision,
    )


def reappraise_user(
    conn,
    user_id: str,
    task: dict,
    *,
    reason: str,
    review_threshold: float = REVIEW_THRESHOLD,
    apply: bool = True,
    created_at: str | None = None,
) -> list[ReappraisalOutcome]:
    """把某人在**某个任务**上的全部作答按新判据重评一遍（「批量回溯重算」的入口）。

    范围只限同一任务：判据是按任务写的，拿 A 题的 rubric 去重评 B 题毫无意义
    （`rebuild_report()` 会直接拒绝，这里先筛掉不必逐份撞错）。

    返回逐份结果，**包括失败的**（`missing_trace` / `missing_artifacts`）。
    只返回成功的会让"这批里有多少根本重评不了"看不见——那正是最该报给人知的数字。
    """
    task = _check_task(task)
    if not isinstance(user_id, str) or not user_id:
        raise ReappraisalError("user_id 必须是非空字符串")

    ids = [
        row["submission_id"]
        for row in store.list_submissions(conn, user_id, limit=1_000_000)
        if row["task_id"] == task["task_id"]
    ]
    ids.sort()  # 稳定顺序：演练与真跑的遍历次序必须一致，否则两次结果不能逐条比对
    return [
        reappraise(conn, sid, task, reason=reason, review_threshold=review_threshold,
                   apply=apply, created_at=created_at)
        for sid in ids
    ]


# ── 受影响范围（B-04e ⓑ）────────────────────────────────────────────────────
# 前三步都能做，但它们仍然需要人来指范围（`reappraise_user` 要给 user_id）。
# 「标准改完后批量回溯重算」缺的最后一块是：**改完标准，谁受影响**。
# 漏掉一个人的后果不是报错，是他的成绩永久停在旧标准上、而且没有任何东西会响。


@dataclass(frozen=True)
class ReappraisalScope:
    """一个任务在**现行判据**下的重评范围。三个桶互斥且穷尽。

    - `stale`：指纹算得出、但与现行判据不同 ⇒ 确定要重评；
    - `unfingerprinted`：指纹读不到（v5 之前的报告）⇒ **也要重评**，理由见 `scope_for_task`；
    - `current`：指纹与现行判据一致 ⇒ 不动。

    分成三个桶而不是一个"要重评的列表"，是因为这三类的**可判定程度不同**：
    前一类能说"它按旧标准判的"，后一类只能说"它按什么判的无从得知"。
    合成一个列表报给人看，等于把推测和事实混成一行。
    """

    task_id: str
    rubric_fingerprint: str
    stale: tuple[str, ...] = ()
    unfingerprinted: tuple[str, ...] = ()
    current: tuple[str, ...] = ()

    @property
    def all_ids(self) -> tuple[str, ...]:
        """要重评的全部提交。**两桶都算**，顺序先 `stale` 后 `unfingerprinted`。

        顺序只要求**确定**（同输入同输出），不要求按时间交织：每份重评独立算、
        独立刷快照，谁先谁后不影响最终结果；但演练与真跑必须逐条对得上。
        """
        return self.stale + self.unfingerprinted

    @property
    def is_empty(self) -> bool:
        return not self.all_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "rubric_fingerprint": self.rubric_fingerprint,
            "stale": list(self.stale),
            "unfingerprinted": list(self.unfingerprinted),
            "current": list(self.current),
            "to_reappraise": list(self.all_ids),
        }


def scope_for_task(conn, task: dict) -> ReappraisalScope:
    """按**现行判据**算出这个任务下哪些作答需要重评。只读，不改库。

    四个决定，每个都对应一种会让机制"看着能用、实则失效"的写法：

    1. **范围必须按 `task_id` 收，不能全库扫。** 判据是按任务写的，不同任务的指纹
       天生不同；拿一个任务的指纹去全库比对，答案恒等于"其他所有任务的全部提交"。
       这种错不会报错，只会让每次改判据都触发一次全库重评。
    2. **比对对象是生效报告**（`store.task_effective_fingerprints` 已保证）。
       拿落库时那份比，重评过的提交永远是"过期"，同一批会被反复重评——
       重评链因此堆满 no-op 修订，"这轮改了谁"从此读不出来。
    3. **读不到指纹算进范围（`unfingerprinted`），不算"已符合"。** 默认"不知道 ⇒ 要重评"
       的理由是代价不对称：多算 ⇒ 多跑一次重评，且会被 `missing_artifacts` 挡住；
       少算 ⇒ 这批历史永远停在旧标准上，且没有任何信号。
       这批本来就大多重评不了（v4 之前没有 `artifacts`），但它们必须**被报出来**，
       否则"我们回溯过了"会变成一句没根据的话。
    4. **不自己造判据**。现行判据由调用方传入（与 `reappraise()` 同一约定）：
       "标准改成什么"是人的判断，本函数只回答"按你给的标准，谁对不上"。
    """
    task = _check_task(task)
    fingerprint = dr.rubric_fingerprint(task)   # 非法 rubric 在这里就抛，不给它算范围

    stale: list[str] = []
    unknown: list[str] = []
    current: list[str] = []
    for row in store.task_effective_fingerprints(conn, task["task_id"]):
        sid, fp = row["submission_id"], row["fingerprint"]
        if fp is None:
            unknown.append(sid)
        elif fp != fingerprint:
            stale.append(sid)
        else:
            current.append(sid)

    return ReappraisalScope(
        task_id=task["task_id"],
        rubric_fingerprint=fingerprint,
        stale=tuple(stale),
        unfingerprinted=tuple(unknown),
        current=tuple(current),
    )
