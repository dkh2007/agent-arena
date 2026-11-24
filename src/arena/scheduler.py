"""复习层的调度器（B-08）：从一份提交推出"哪个技能该复习、什么时候"。

**这一步解决什么**：`review_items` 表建好之后一直没人写——DDL 有了、实体有了、
`kind` 四值也定了，但零行为（`docs/ROADMAP.md` 第 4 层那行写的"纸面"）。
本模块是第一个**写**它的人：读一份诊断报告 → 按技能算表现 c → 映射成 FSRS 评分 →
用 py-fsrs 更新该技能的卡片 → 卡片的 `due` 变成一条 `pending` 的 ReviewItem。

## 七条设计决定

1. **关掉 fuzz（`enable_fuzzing=False`）。** py-fsrs 默认给 due 加随机偏移（模拟"同一天
   复习时间也分散"）。对我们这是硬伤：同一份提交跑两次得到两个 due，"复核一遍"就对不上账，
   测试也会随机飘。可复现优先——代价是所有人的复习点会落在同一天同一时刻，
   这在单机/小班规模无所谓，**上量后再考虑按用户加固定偏移**（那也是有确定性的）。

2. **rating 映射是假设，不是结论。** `docs/data-model.md` §4 决策 2 的草案：
   `c ≥ 0.9 → 4(Easy)` / `≥ 0.7 → 3(Good)` / `≥ 0.4 → 2(Hard)` / `< 0.4 → 1(Again)`。
   三个阈值是拍的，要 dogfooding 后校准。代码里用**降序元组**并在导入时校验降序，
   否则 `0.7` 会先被 `0.4` 那条匹配走——这类错会让整张映射表静默返回错档。

3. **只给"有证据且这次没过"的技能建复习项。** 三条排除，都对应已有口径：
   - `counted_weight == 0`（全 `blocked` / `unscored`）⇒ **没有证据**，不推复习。
     决策 7：数据缺失不等于能力不足，凭空推题等于把"没交产物"当成"不会"。
   - `c == 1.0` ⇒ 这次全过，不建复习项（但**卡片照样更新**，见第 4 条）。
   - 技能 id 为 `None` 的断言（只计分不归因）不产生复习项，也不动卡片。

4. **每份提交、每个有证据的技能都更新卡片——包括全过的那些。**
   FSRS 的语义里"复习一次"就该推进间隔；做题本身就是复习。只在失败时更新会让
   已掌握的技能永远停在旧 due，于是"天天到期"——那不是调度，是噪声。

5. **`review_id` 由 (user, skill, due) 派生，不是随机 UUID。**
   三个好处：同一份提交重跑得到同一个 id（**幂等**，不产生重复队列项）；
   同一技能的下一次复习因为 due 变了而有新 id（**历史保留**，不会被覆盖）；
   不需要额外查询就能判断"这条已经建过了"。代价：id 不可读——排查靠
   `source_submission_id` 与 `skill_id` 两列，它们才是给人看的。

6. **卡的 `card_id` 也确定性派生**（`sha256(user|skill)` 前 12 位十六进制）。
   py-fsrs 的 `Card()` 默认用毫秒时间戳当 id，而我们根本不用 card_id 做主键
   （主键是 `(user_id, skill_id)`）——它只是序列化内容的一部分。让它随机，
   等于每次重放都得到不同的 `fsrs_card` JSON，比对"重启前后是否一致"就失去意义。

7. **每日上限只截断"今天看什么"，不删数据。**
   `users.daily_review_cap`（默认 2）是产品口径"一天别推太多"。超出的条目
   **仍然留在库里、仍然是 pending**，明天自然还在队列里。若在落库时丢弃，
   就等于"因为今天看不下，所以永远不用看了"——那是数据丢失，不是限流。

## 不做什么（写清边界，避免下轮重猜）

- **只填当前题，不挑题**。`task_id` 取 `sub.task_id`——刚提交的那道，所以复习形态只能是
  "再做同一道"（`replay` / `fault_injection`）。真正的"同族换一道"（`variant`）要按家族、
  难度、错误热度、截止时间四个因子权衡，而其中三个还没有数据，交给排程层（B-09）决定。
  现在硬塞一个"挑同族下一个变体"的规则，会被 B-09 整个推翻。
  2026-11-24 前这里写"`task_id` 默认留空"，与代码不符（代码一直填 `sub.task_id`），已更正。
- **不产出 `variant` / `timed`**。`variant` 要挑题能力（同上），`timed` 要人设时限。
  目前只产出 `replay`（原题重做）与 `fault_injection`（后者由 `tasks.fault_injection` 列决定：
  题本身带故障注入，复习它就等于重练"出错后怎么办"）。
  2026-11-24 前这里写"只产出 `variant` 与 `fault_injection`"——而 `variant` 的词表标签是
  "同族换一道"，与实际的同一道题冲突，已更正。
- **不衰减 P**。见 `mastery.py` 的说明：唯一时间轴是复习间隔，而"实际复习完成时刻"
  要等复习层真的开始跑才有数据。本模块只把间隔算出来、把 due 存下去。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from fsrs import Card, Rating, Scheduler

from arena import diagnosis_report as dr
from arena import mastery  # 绝对导入：本仓无 `arena/__init__.py`，与 store.py / review_packet.py 一致

# FSRS 评分映射（假设，见文件头第 2 条）。**必须降序**：从上往下第一条命中即返回。
RATING_THRESHOLDS: tuple[tuple[float, int], ...] = ((0.9, 4), (0.7, 3), (0.4, 2))

# 低于所有阈值时的评分（Again：下次 ASAP 再来一次）。
RATING_FLOOR = 1

# 现在能自动产出的两种复习形态。`variant` / `timed` 要挑题能力（B-09 未做），见文件头"不做什么"。
#
# 2026-11-24 更正：这里原来是 ("variant", "fault_injection")，错在把 `variant` 当成了
# "同族换一道"。实际上本模块给 `task_id` 填的是 `sub.task_id`——**刚提交的那道题**。
# 于是页面上出现"同族换一道（订单数据核对报告）"却指回同一道题，用户点进去会发现
# 还是刚才那道，判定系统坏了。演示数据当时手工把 task_id 写成族内另一道题（dbg-003），
# 正好把这个矛盾遮住——假绿。改成 `replay`（词表里本来就有"原题重做"）后，
# kind 与 task_id 自洽；`variant` 留待 B-09 真能挑题时再启用。
AUTO_KINDS = ("replay", "fault_injection")

REVIEW_STATUSES = ("pending", "done", "skipped")


class SchedulerError(ValueError):
    """输入不成立（越界的 c、倒序的时刻、未知的 kind）。不吞、不兜底。"""


def _check_thresholds() -> None:
    """导入即校验：阈值必须严格降序且落在 (0,1)，否则映射会静默错档。"""
    values = [t for t, _ in RATING_THRESHOLDS]
    if values != sorted(values, reverse=True) or len(set(values)) != len(values):
        raise SchedulerError(f"RATING_THRESHOLDS 必须严格降序：{values}")
    for threshold in values:
        if not 0 < threshold < 1:
            raise SchedulerError(f"阈值必须落在开区间 (0,1)：{threshold}")
    ratings = [r for _, r in RATING_THRESHOLDS]
    if ratings != sorted(ratings, reverse=True) or RATING_FLOOR >= min(ratings):
        raise SchedulerError(f"评分必须随阈值单调、且 FLOOR 最小：{ratings}")


_check_thresholds()


def rating_for(c: float) -> int:
    """表现 c（0–1 的加权通过率）→ FSRS 1–4。越界报错，不 clamp。

    `c == 1.0` 落 4（`>=` 而非 `>`）；`c == 0.9` 也落 4——阈值取闭区间右侧，
    这样"刚好达标"不会被判成差一档。这个选择写进测试，改它要显式改测试。
    """
    if isinstance(c, bool) or not isinstance(c, (int, float)):
        raise SchedulerError(f"c 必须是数值（收到 {type(c).__name__}）")
    if not 0.0 <= float(c) <= 1.0:
        raise SchedulerError(f"c 必须落在 [0,1]（收到 {c}）——越界说明上游算错了，不 clamp")
    for threshold, rating in RATING_THRESHOLDS:
        if c >= threshold:
            return rating
    return RATING_FLOOR


def make_scheduler(*, desired_retention: float | None = None) -> Scheduler:
    """构造调度器。**一律关 fuzz**（文件头第 1 条）——可复现优先于均匀分布。"""
    kwargs: dict[str, Any] = {"enable_fuzzing": False}
    if desired_retention is not None:
        if not 0.0 < desired_retention <= 1.0:
            raise SchedulerError(f"desired_retention 必须在 (0,1]：{desired_retention}")
        kwargs["desired_retention"] = desired_retention
    return Scheduler(**kwargs)


def _stable_int(text: str, *, digits: int = 12) -> int:
    """由字符串派生稳定整数。用 sha256 而不是内置 hash：后者按进程随机化（PYTHONHASHSEED），
    重启后同一个用户会得到不同的 card_id。"""
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:digits], 16)


def new_card(user_id: str, skill_id: str, *, at: str) -> Card:
    """该 (用户, 技能) 的第一张卡：Learning 状态、`due = at`（现在就该学）。

    `due` 必须显式给——`Card()` 的默认 due 是"调用瞬间"，会让测试与重放都不可复现。
    """
    instant = mastery.parse_instant(at)
    return Card(card_id=_stable_int(f"{user_id}|{skill_id}"), due=instant)


def review(card: Card, c: float, *, at: str, scheduler: Scheduler | None = None) -> tuple[Card, int]:
    """用一次表现 c 更新卡片，返回 (新卡, 本次评分)。

    时间**倒序**直接报错：补录更早的提交时，卡片已经推进到更晚的时刻，
    此时套用 FSRS 会算出负间隔——而 py-fsrs 不报错，只是给出一个看着合理的 due。
    正确做法是按 `created_at` 升序重放（与 `mastery.replay` 同一约定）。
    """
    instant = mastery.parse_instant(at)
    if card.last_review is not None:
        last = card.last_review
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if instant < last:
            raise SchedulerError(
                f"review 时刻 {instant.isoformat()} 早于卡片上次复习 {last.isoformat()}——"
                "请按 created_at 升序重放，不要在乱序输入上套 FSRS（它会给出一个看着合理的错 due）"
            )
    sched = scheduler or make_scheduler()
    rating = rating_for(c)
    updated, _log = sched.review_card(card, Rating(rating), review_datetime=instant)
    return updated, rating


def _skills_in(report: dict) -> list[str]:
    """报告里出现过的技能 id（去重保序，`None` 不算）。"""
    out: list[str] = []
    for assertion in report.get("assertions") or []:
        sid = assertion.get("skill_id")
        if isinstance(sid, str) and sid and sid not in out:
            out.append(sid)
    return out


def _iso(instant: datetime) -> str:
    """统一按 UTC、秒级精度输出。py-fsrs 返回的都是 aware UTC 时刻；
    统一格式是为了让 `review_id` 的派生稳定（同一 due 必须得到同一串）。"""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def review_id_for(user_id: str, skill_id: str, due: str) -> str:
    """`rv-` + 16 位十六进制。由 (user, skill, due) 派生 ⇒ 同提交重跑幂等、下次 due 变了才有新 id。"""
    return "rv-" + hashlib.sha256(f"{user_id}|{skill_id}|{due}".encode("utf-8")).hexdigest()[:16]


def retrievability_at(card: Card, at: str, *, scheduler: Scheduler | None = None) -> float | None:
    """卡在 `at` 时刻的可提取性 R。**全项目只此一处算 R**。

    还没复习过的卡（`stability is None`）返回 **None**，不是 0：py-fsrs 对这种卡返回 0.0，
    而 0.0 在"遗忘风险 = 1-R"里是"已经忘光"、在掌握度衰减里会把 P 直接乘成 0
    ——那是把"还没开始"说成"已经忘完"（决策 7 的同一个错误方向）。
    两处调用方（排程层、衰减层）都按 None 走"弃权/不衰减"，所以这条语义必须在这里统一。
    """
    if card.stability is None:
        return None
    instant = mastery.parse_instant(at)
    if card.last_review is not None:
        last = card.last_review
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if instant < last:
            raise SchedulerError(
                f"R 的时刻 {instant.isoformat()} 早于卡片上次复习 {last.isoformat()}——"
                "时间不许倒着用"
            )
    sched = scheduler or make_scheduler()
    got = sched.get_card_retrievability(card, instant)
    if got is None:  # 字段齐全时不至于；真为 None 说明 py-fsrs 改了语义，别静默当成 0
        raise SchedulerError("py-fsrs 对完整字段的卡返回 None，语义变了，先别信这个数")
    return float(got)


@dataclass(frozen=True)
class ReviewDraft:
    """一条待落库的复习项。字段与 `review_items` 表一一对应，不多不少。"""

    review_id: str
    user_id: str
    skill_id: str
    kind: str
    source_submission_id: str
    due: str
    task_id: str | None = None
    status: str = "pending"

    def to_row(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "user_id": self.user_id,
            "skill_id": self.skill_id,
            "kind": self.kind,
            "task_id": self.task_id,
            "source_submission_id": self.source_submission_id,
            "due": self.due,
            "status": self.status,
        }


@dataclass(frozen=True)
class SkillDecision:
    """一个技能在这份提交上被怎么处理——留痕，便于"为什么没给我推复习"能被回答。"""

    skill_id: str
    c: float | None
    rating: int | None
    counted_weight: float
    gate_status: str
    action: str  # "review"（建复习项）/ "passed"（全过，只更新卡）/ "no_evidence" / "suppressed"
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "c": None if self.c is None else round(self.c, 6),
            "rating": self.rating,
            "counted_weight": round(self.counted_weight, 6),
            "gate_status": self.gate_status,
            "action": self.action,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ScheduleResult:
    """一次调度后的完整结果：新卡片（内存里，由调用方落库）+ 待建复习项 + 每技能决策。"""

    user_id: str
    submission_id: str
    at: str
    cards: dict[str, Card] = field(default_factory=dict)
    items: tuple[ReviewDraft, ...] = ()
    decisions: tuple[SkillDecision, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "submission_id": self.submission_id,
            "at": self.at,
            "cards": {k: v.to_dict() for k, v in sorted(self.cards.items())},
            "items": [i.to_row() for i in self.items],
            "decisions": [d.to_dict() for d in self.decisions],
        }


def schedule_submission(
    report: dict,
    *,
    user_id: str,
    at: str | None = None,
    cards: Mapping[str, Card] | None = None,
    task_id: str | None = None,
    task_has_fault: bool = False,
    scheduler: Scheduler | None = None,
) -> ScheduleResult:
    """一份报告 → (该更新的卡, 该建的复习项)。

    `cards` 是**该用户已有的卡**（skill_id → Card）。缺的按新卡初始化（`due = at`）。
    纯函数：不读库、不写库，落库由 `store.save_schedule()` 做。
    """
    sub = report.get("submission") or {}
    submission_id = sub.get("submission_id")
    if not isinstance(submission_id, str) or not submission_id:
        raise SchedulerError("报告缺 submission.submission_id——没有它无法追溯复习项的来源")
    if not isinstance(user_id, str) or not user_id:
        raise SchedulerError("user_id 必须显式给（不从 agent_id 推）")

    stamp = at or sub.get("evaluated_at")
    if not isinstance(stamp, str) or not stamp.strip():
        raise SchedulerError("报告缺 submission.evaluated_at，且调用方未显式给 at")
    instant = mastery.parse_instant(stamp)

    task = task_id if task_id is not None else sub.get("task_id")
    # `fault_injection`：重做上次出错的那一页（同一道题，坑还在）。
    # `replay`：原题重做——因为 `task_id` 取的就是当前题，这是唯一自洽的说法。
    kind = "fault_injection" if task_has_fault else "replay"
    if kind not in AUTO_KINDS:  # 防御：将来改 kind 分支时不要漏
        raise SchedulerError(f"未知的复习形态 {kind}，自动调度只产出 {AUTO_KINDS}")

    sched = scheduler or make_scheduler()
    known = dict(cards or {})
    out_cards: dict[str, Card] = {}
    items: list[ReviewDraft] = []
    decisions: list[SkillDecision] = []

    # 「本次不算数」的提交**不动卡、不建项**（门禁不合格 / 版本不一致 / 缺过程）。
    # 这是本轮的判断，不是既有口径的推论，所以写清依据与代价：
    #   - 依据：这三类在报告里的语义就是"本次不算数"（`ratio` 被压成 0 的那三种原因），
    #     而卡片是**复习排期**，不是能力估计。把一次"不算数"的提交当成一次复习，
    #     会用 FSRS 的 Easy 把该技能的间隔推到 8 天后——真实需求被推远，且看不出来。
    #   - 与 P 的区别：P 仍按现行 A 口径照记（要不要给门禁开例外是 B-07b，待人拍）。
    #     两者分开是因为它们回答不同问题：P 问"能力估计"，卡问"下次什么时候练"。
    #   - 代价：一直交缺过程/不合格提交的人，队列会一直是空的。这是对的（没有有效证据
    #     就不该有排期），但如果将来发现"缺过程的提交其实有诊断价值"，可以在
    #     `reasons` 上分流（例如只对 `process_missing` 降权而非跳过），留痕已够。
    suppression = dr.suppression_reasons(report)
    for skill_id in _skills_in(report):
        evidence = mastery.skill_evidence(report, skill_id, at=stamp)
        base = {
            "skill_id": skill_id,
            "counted_weight": evidence.counted_weight,
            "gate_status": evidence.gate_status,
        }
        if suppression:
            decisions.append(
                SkillDecision(c=None, rating=None, action="suppressed",
                              reasons=tuple(suppression), **base)
            )
            continue
        if not evidence.counts:
            decisions.append(SkillDecision(c=None, rating=None, action="no_evidence", **base))
            continue

        c = evidence.c
        card = known.get(skill_id) or new_card(user_id, skill_id, at=stamp)
        card, rating = review(card, c, at=stamp, scheduler=sched)
        out_cards[skill_id] = card

        if c >= 1.0:
            decisions.append(
                SkillDecision(c=c, rating=rating, action="passed", **base)
            )
            continue

        due = _iso(card.due)
        items.append(
            ReviewDraft(
                review_id=review_id_for(user_id, skill_id, due),
                user_id=user_id,
                skill_id=skill_id,
                kind=kind,
                source_submission_id=submission_id,
                due=due,
                task_id=task if isinstance(task, str) else None,
            )
        )
        decisions.append(
            SkillDecision(c=c, rating=rating, action="review", **base)
        )

    return ScheduleResult(
        user_id=user_id,
        submission_id=submission_id,
        at=_iso(instant),
        cards=out_cards,
        items=tuple(items),
        decisions=tuple(decisions),
    )


def cards_from_rows(rows: Mapping[str, dict]) -> dict[str, Card]:
    """把 `store.load_fsrs_cards()` 读到的 JSON 还原成 `Card`。

    反序列化只在这里做一次：`store` 不认识 py-fsrs 的类型（它只存 JSON），
    `scheduler` 不认识 sqlite。两边各守一半，卡片的 `due` 只有一个解释处。
    """
    return {skill_id: Card.from_dict(payload) for skill_id, payload in rows.items()}


def sort_queue(items: Iterable[dict], *, at: str) -> list[dict]:
    """按"到期先看"排序：due 升序，同 due 按 review_id 兜底（保证重跑顺序稳定）。

    只保留 `status == "pending"` 且 `due <= at` 的项——未到期的不进今日队列。
    `at` 必须是同一时区的可比时刻：不同偏移的 ISO 串直接比字符串会判反
    （`+08:00` 的 12:00 早于 `Z` 的 06:00，字符串比较却相反）。
    """
    now = mastery.parse_instant(at)
    live: list[tuple[datetime, str, dict]] = []
    for row in items:
        if row.get("status", "pending") != "pending":
            continue
        due = mastery.parse_instant(row["due"])
        if due <= now:
            live.append((due, row["review_id"], row))
    live.sort(key=lambda x: (x[0], x[1]))
    return [row for _, _, row in live]


def take_daily(items: Iterable[dict], *, at: str, cap: int) -> tuple[list[dict], int]:
    """今日队列 = 到期项按 due 排序后取前 `cap` 条；返回 (今日要看的, 被推迟的条数)。

    `cap == 0` 合法（"今天不复习"）；负数报错。**被推迟的项不删**——它们仍是 pending，
    明天照旧到期（文件头第 7 条）。
    """
    if isinstance(cap, bool) or not isinstance(cap, int):
        raise SchedulerError(f"cap 必须是整数（收到 {type(cap).__name__}）")
    if cap < 0:
        raise SchedulerError(f"cap 不能为负（收到 {cap}）——'今天不看'写 0，不是 -1")
    due = sort_queue(items, at=at)
    return due[:cap], max(0, len(due) - cap)
