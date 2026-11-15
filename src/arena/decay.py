"""掌握度 P 的衰减（B-08b）：借 FSRS 的 `R(t,S)`，不新造时间常数。

**这一步解决什么**：B-07 的 `P` 只会在"有新提交"时变；一个技能半年没碰，P 还是上次的数。
`tests/test_mastery.py::test_decay_is_not_implemented_yet` 把这件事锁成了现状，
本轮把它反过来（**不删**，改断言）。

## 为什么不是"再拍一条曲线"

`docs/data-model.md` §5.2 有两条硬约束：**不引入第二套时间常数**、
**不把 S 当衰减系数（要用须先定义并标定 α=f(S)）**。所以这里只有一条路：
直接调 py-fsrs 的 `Scheduler.get_card_retrievability(card, at)`——那条曲线已经存在、
已经被调度层用来算"什么时候该复习"，再写一条就等于同一件事有两个答案。

**暂时借用，仍需人拍**：这条选择等于默认"遗忘速度由 FSRS 的 S 决定"。
反证条件写在 §6：若真实数据里 S 明显低估/高估遗忘，要换形状就必须重算历史 P，
那时必须同时保留 `p_raw`（本模块的输出里两者都在，不许只留乘完的）。

## 六条设计决定

1. **`P_eff = P_raw × R`，不设下限。**
   设一个 `floor` 等于说"永远忘不掉一部分"——那需要第二个常数，且没有数据支撑。

   **实测之后要订正一句我原以为的话**：我本来写"长期不练 P 会趋近 0"，跑完发现它对
   FSRS 的曲线**不成立**。py-fsrs 6.x 的 `R(t,S)` 是重尾的，实测（`S=1` 天那张卡）：

   | 距上次复习 | 1 天 | 7 天 | 30 天 | 365 天 | 550 天 | 5 年 | 20 年 |
   |---|---|---|---|---|---|---|---|
   | R | 0.9000 | 0.7276 | 0.5906 | 0.4037 | 0.3790 | 0.3151 | 0.2544 |

   所以真实风险不是"衰减到 0"，而是**衰减太慢**：半年没碰，P 也只掉到原来的四成。
   这也解释了为什么 `R(S, t=S) = 0.9`——FSRS 把"间隔＝稳定度"定义为可达 90% 的记忆点，
   这个数不是拍的，是它的默认 `desired_retention`。
   代价因此改写：**不设下限不会造成 0，但会让"很久没练"与"刚练过"的差距只有两三倍。**
   若真使用中出现"半年没碰的技能照样排最后"，说明衰减太慢、要换形状——那时必须重算历史 P，
   所以本模块的输出里 `p_raw` 与 `p_effective` 都在。
2. **时间轴是"最后一次练过"，不是"计划什么时候到期"。**
   B-08b 原条目把这一点列为不能做的硬理由②：用到期时刻衰减＝把计划当历史。
   本模块的锚点取 `max(card.last_review, 最后一条计入证据的时刻)`：
   两者正常相等（`scheduler.review()` 就在提交时刻更卡），
   不等时取更晚的那个——"多久没新证据"问的是事实，不是计划。
   **因此不需要 ReviewLog 也能诚实实现**：衰减衡量的是证据新鲜度。
3. **`as_of` 必填，不默认 `now()`。**
   默认墙钟会让同一个函数的两次调用给出不同答案，复核时说不出"谁改的"。
   这是 B-08b 开工条件③；`as_of` 早于锚点直接报错，不许把时间倒着用。
4. **没有卡 / 卡上没有历史 ⇒ 不衰减，且写明原因。**
   py-fsrs 有个坑：给一张 `stability` 为 `None` 的卡调 `get_card_retrievability()`，
   它返回 **0**（不是 `None`）。照着乘下去，一个"从没失败过、因此没建卡"的技能
   P 会直接变 0——那正是决策 7「数据缺失 ≠ 能力不足」禁止的换算。
   所以先校验字段再用，缺就退化成"原值 + `applied=False` + reason"。
5. **衰减不写库、不推进递推。**
   P 仍然只活在内存里（`user_skill_states` 没有 P 字段，见 B-07c）。
   `P_eff` 是"给定 `as_of` 的读法"，不是新状态——所以同一个 `series` 可以按不同
   `as_of` 读出不同的 `P_eff`，而 `series.p` 一动不动。
6. **退化路径的返回值仍是完整对象，不是 `None`。**
   返回 `None` 会让调用方写 `if x is None: ...` 的分支，而"没衰减"和"算不出"是两件事
   （前者是 `applied=False` + 原因，后者是抛错）。三个原因取值：
   `no_card` / `no_history` / `not_later`（`as_of` 与锚点同一时刻，R=1）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import mastery

# 退化原因词表。只三种，且在 `decay_p()` 里校验——拼错会静默躲过将来的过滤规则。
FALLBACK_REASONS = ("no_card", "no_history", "not_later")


class DecayError(ValueError):
    """输入不成立（越界的稳定性/时刻、时间倒流）。不吞、不 clamp、不退回 1.0。"""


@dataclass(frozen=True)
class DecayResult:
    """一次衰减的结果。`p_raw` 与 `p_effective` 都在——换曲线时才能重算历史。"""

    p_raw: float
    p_effective: float
    applied: bool
    reason: str
    r: float | None
    elapsed_days: float
    stability: float | None
    anchor: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "p_raw": mastery._round(self.p_raw),
            "p_effective": mastery._round(self.p_effective),
            "applied": self.applied,
            "reason": self.reason,
            "r": None if self.r is None else mastery._round(self.r),
            "elapsed_days": round(self.elapsed_days, 3),
            "stability": None if self.stability is None else round(self.stability, 3),
            "anchor": self.anchor,
        }


def retrievability(stability: float, elapsed_days: float) -> float:
    """`R(t,S)`：造一张只有 (S, 上次复习时刻) 的卡，问 `scheduler.retrievability_at()`。

    **不在这里重写公式**：重写就会与调度层分叉，同一个 S 算出两个 R。
    """
    if isinstance(stability, bool) or not isinstance(stability, (int, float)):
        raise DecayError(f"stability 必须是数值，收到 {type(stability).__name__}")
    if not float(stability) > 0:
        raise DecayError(f"stability 必须为正，收到 {stability}")
    if isinstance(elapsed_days, bool) or not isinstance(elapsed_days, (int, float)):
        raise DecayError(f"elapsed_days 必须是数值，收到 {type(elapsed_days).__name__}")
    if float(elapsed_days) < 0:
        raise DecayError(f"elapsed_days 不能为负，收到 {elapsed_days}")

    from datetime import timedelta, timezone

    from fsrs import Card

    from . import scheduler as sch

    anchor = datetime(2026, 1, 1, tzinfo=timezone.utc)
    card = Card(due=anchor, stability=float(stability), difficulty=5.0, last_review=anchor)
    got = sch.retrievability_at(
        card, (anchor + timedelta(days=float(elapsed_days))).isoformat()
    )
    # 上面这张卡字段齐全，返回值不该是 None；真为 None 说明守卫或者 py-fsrs 变了，
    # 那必须炸出来——静默当成 0 或 1 都会让 P 错得看不出来。
    if got is None:
        raise DecayError("字段齐全的卡返回了 None，语义变了，先别信这个数")
    return got


def _elapsed(anchor: str, as_of: str) -> float:
    start = mastery.parse_instant(anchor)
    end = mastery.parse_instant(as_of)
    seconds = (end - start).total_seconds()
    if seconds < 0:
        raise DecayError(f"as_of={as_of} 早于锚点 {anchor}：时间不许倒着用")
    return seconds / 86400.0


def decay_p(
    p_raw: float,
    *,
    as_of: str,
    last_evidence_at: str | None,
    last_review: str | None = None,
    stability: float | None = None,
) -> DecayResult:
    """把 `p_raw` 按 `as_of` 衰减一次。

    `last_evidence_at` 来自 `MasterySeries.steps[-1].at`，`last_review` / `stability`
    来自 FSRS 卡片（`store.load_fsrs_cards`）。三者任一缺失都走退化路径，不猜。
    """
    p = mastery._check_unit(p_raw, "p_raw")
    candidates = [t for t in (last_evidence_at, last_review) if t]
    if not candidates:
        return DecayResult(p, p, False, "no_history", None, 0.0, None, None)

    anchor = max(candidates, key=mastery.parse_instant)
    elapsed = _elapsed(anchor, as_of)

    if stability is None:
        return DecayResult(p, p, False, "no_card", None, elapsed, None, anchor)
    if isinstance(stability, bool) or not isinstance(stability, (int, float)) or not float(
        stability
    ) > 0:
        # 卡存在但 S 非正（还没进 Review 态）⇒ 同样是"没有可用的历史"，不是"遗忘到 0"。
        return DecayResult(p, p, False, "no_history", None, elapsed, None, anchor)

    if elapsed == 0.0:
        # 同一时刻 ⇒ R=1，P 不该变。单独记一个原因，免得"没变"被误当成"没算"。
        return DecayResult(p, p, False, "not_later", 1.0, 0.0, float(stability), anchor)

    r = retrievability(float(stability), elapsed)
    return DecayResult(
        p_raw=p, p_effective=p * r, applied=True, reason="applied", r=r,
        elapsed_days=elapsed, stability=float(stability), anchor=anchor,
    )


def decay_series(
    series: mastery.MasterySeries,
    *,
    as_of: str,
    stability: float | None = None,
    last_review: str | None = None,
) -> DecayResult:
    """对一条 P 序列在 `as_of` 时刻的读法。**不修改** `series`（它仍是不可变对象）。"""
    if not isinstance(series, mastery.MasterySeries):
        raise DecayError(f"series 应是 MasterySeries，收到 {type(series).__name__}")
    last = series.steps[-1].at if series.steps else None
    p_raw = series.p
    if p_raw is None:
        # 一条证据都没有：P 本来就是 None（不是 0），衰减对 None 无意义。
        return DecayResult(0.0, 0.0, False, "no_history", None, 0.0, None, None)
    return decay_p(
        p_raw, as_of=as_of, last_evidence_at=last, last_review=last_review,
        stability=stability,
    )
