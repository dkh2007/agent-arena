"""排程层（B-09）：把"今天到期的这几条，先看哪一条"算出来，并说清为什么。

**这一步解决什么**：`scheduler.sort_queue()` 只有一个维度——`due`。同一时刻到期的
五条复习项在它眼里完全等价，先看谁靠 `review_id` 的字典序（那是给机器稳定的，不是给人用的）。
计划书第 5 层承诺的是「掌握度 + 遗忘风险 + 错误热度 + 项目截止」四因子联合排程，
本模块就是这四个因子的第一版实现，**而且它不改库**：只读 `review_items` / `user_skill_states` /
`submissions` / `error_patterns`，产出一份建议 + 理由。

## 一、四个因子里，三个有数据，一个没有

这是本模块最该先讲清的事，因为它决定了对外该怎么措辞：

| 因子 | 数据来源 | 现状 |
|---|---|---|
| 掌握度缺口 `1-P` | `mastery.replay()`（`submissions.diagnosis`） | **有**。P 只活在内存，但排程只需要"现在这一刻的 P"，不需要历史快照 |
| 遗忘风险 `1-R` | py-fsrs `get_card_retrievability(card, at)` | **有**。卡片在 `user_skill_states`，R 由 (stability, 距上次复习天数) 给出 |
| 错误热度 | `submissions.detected_patterns` × `error_patterns.mapped_skill_ids` | **有**。窗口内"这次提交有没有该技能相关错误"计数 |
| 项目截止 | —— | **没有**。`SCHEMA_SQL` 里没有任何截止字段，也没有项目/日程实体 |

所以**截止因子在本模块里是"弃权"状态**：它会出现在每条理由里，并明写「没有这项数据」，
但**不参与打分**（权重整批退出）。**不编一个假的截止日期**，也不用"没截止=不紧急"去顶
——前者是造假数据，后者是把"未知"当成"已知的不急"（决策 7 的同一个错误方向）。

> 与既有记载的关系：`docs/review-response-v1.md` §1 ⑤-3 采纳了外部评审的「MVP 只做两因子
> （遗忘风险 + 掌握度）」并把"截止/错误热度"写成规划迭代；但计划书 §五 第 3 条仍写四因子，
> `docs/BACKLOG.md` B-09 标题也是四因子。**本轮的做法是第三条路**：错误热度既然已有数据源
> （`detected_patterns` + `mapped_skill_ids` 都是既有一等公民），就做成真代码，少写一句"规划"；
> 截止因子做成**留好接口的弃权**，等真有数据再填。口径措辞需要人拍（登记 B-09b）。

## 二、六条设计决定

1. **总序是加权和，不是逐级字典序。** 理由：字典序在连续值上会退化——截止压力 0.71
   与 0.70 之间会压过遗忘风险的 0.10 与 0.99 之差，等于用浮点噪声决定顺序。加权和给出
   一个可复算的数，理由里把算式摊开，人能自己验。
   **代价**：加权和允许"补偿"（一个因子很差可以被另一个因子很好抵消），这与"截止优先"的
   直觉不完全一致。权重按优先级单调递减（8/4/2/1），让补偿需要更大的差值才能翻盘。

2. **权重 8/4/2/1 是假设，不是结论。** 它们只是把计划书 §八 开放问题 4 的暂定优先级
   「截止 > 遗忘风险 > 错误热度 > 掌握度」翻译成一串单调递减的数。取几何级数（每级 2 倍）
   是为了让"下一个优先级要压过上一个"需要接近翻倍的差距，而不是 0.05 的噪声。
   没有 dogfooding 数据之前任何具体数值都是拍的（与 `scheduler.RATING_THRESHOLDS` 同性质）。
   `scripts/advice_walk.py --flip` 会把权重反过来再排一遍，用来实测"哪些名次是权重敏感"的。

3. **缺数据的因子用"同批均值"顶，不重归一化。** 这是本轮最容易被误选的一处：
   - 重归一化（把缺的权重从分母里去掉）会让**缺数据反而占便宜**：一条只有"错误热度"
     有数据的项，会拿 1.0 除以 2，轻松压过三条因子齐备、每条 0.6 的项。
   - 记 0 分等于把"没数据"说成"不紧急"，是决策 7 明令禁止的换算。
   - 取同批均值：既不奖励也不惩罚，且**必须写进理由**（"按同批平均 x.xx 顶"），不许静默。
   若某因子在这一批里**一条数据都没有**（今天的截止因子就是），整个因子退出、权重归一化
   ——此时"归一化"是安全的，因为它对所有项一视同仁，不会改变相对顺序。
   **一处必须知道的推论**：个别项缺数据被均值顶之后，它**不会**排到最后，而是落在"平均紧急度"上。
   真正的"没有分数、只能按到期先后"只在**整批都没数据**时出现（那时所有项一起按 due 排）。
   这个取舍是对的：把"没数据"一律踢到最后，等于用"我们不知道"去惩罚一条可能最该做的项。

4. **0.02 的平手格，是为了不装精确。** 综合分差 0.003 就说"A 比 B 急"是假精度。
   先按 `round(score / 0.02)` 落格，同格视作平手，再按 `due` 升序、最后 `review_id` 兜底。
   格子（而不是两两比较）是因为后者不满足传递性，排序会随输入顺序变。
   0.02 = 综合分满量程的 1/50，同样是拍的。

5. **改序必须留痕，且必须给理由。** `apply_overrides()` 不接受空理由、不接受同一条改两次、
   不接受把项挪到不存在的名次，也不允许"改序顺便少一条"（集合不变）。
   产出 `AuditLine`（系统建议第几 → 实际第几、差几、理由、时刻）。
   **边界**：落盘是本地 JSONL，可被编辑，**不是防篡改的**。要真防篡改需要服务端 append-only
   + 身份认证（B-10 有前端之后才有意义），现在只能做到"改了会被自己看见、也能被别人对账"。

6. **本模块不做的事**：不改 `review_items.status`、不推进卡片、不删项、不写任何表。
   顺延的项仍是 `pending`（与 `scheduler` 文件头第 7 条一致：上限只截断视图）。
   "今天没时间"不等于"以后不用看"。

## 三、可被否掉的判据

- 给定 5 条同一批到期的复习项，排序结果与手推一致（`tests/test_schedule_advisor.py` 里
  把 5 条的因子值、算式、期望名次逐条写出来）；
- 同一输入跑两次，名次、理由、审计行逐字相同；
- 截止数据缺失时，因子**弃权**而不是记 0（`abstained` 里出现 `deadline`，且去掉它
  对顺序零影响）；
- 改序后集合不变、审计行数等于改序条数、`deviations()` 只说真正挪动过的那几条。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from arena import mastery
from arena import scheduler as sch
from arena import store

# 因子优先级顺序（计划书 §八 开放问题 4 的暂定序）。**顺序即优先级**，权重必须沿它递减。
FACTOR_ORDER: tuple[str, ...] = ("deadline", "forgetting", "error_heat", "mastery_gap")

FACTOR_LABELS: dict[str, str] = {
    "deadline": "项目截止",
    "forgetting": "遗忘风险",
    "error_heat": "错误热度",
    "mastery_gap": "掌握度缺口",
}

# 权重（假设，见文件头第 2 条）。几何级数：8 : 4 : 2 : 1。
FACTOR_WEIGHTS: dict[str, float] = {
    "deadline": 8.0,
    "forgetting": 4.0,
    "error_heat": 2.0,
    "mastery_gap": 1.0,
}

# 错误热度的统计窗口（天）。取 7 是因为"这周在这上面栽过几次"是可解释的问法；
# 换成 3 天会让周一犯的错周二就消失，换成 30 天会让一个月前的错一直压着。
ERROR_WINDOW_DAYS = 7

# 截止压力的视界（天）：还有 7 天及以上 ⇒ 压力 0；已经到期 ⇒ 压力 1。
DEADLINE_HORIZON_DAYS = 7.0

# 平手格（见文件头第 4 条）。
TIE_GRID = 0.02


def _iso(instant: datetime) -> str:
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc).replace(microsecond=0).isoformat()


class AdvisorError(ValueError):
    """输入不成立（越界的因子值、坏权重、改序越界）。不吞、不兜底。"""


def _check_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """权重必须覆盖四个因子、全正、且沿优先级严格递减。错了直接报错，不排序。

    为什么要校验单调：权重一旦不单调，"截止优先"这句话就是假的，
    而排序结果看着仍然正常——这种错只能靠导入期检查抓。
    """
    missing = [name for name in FACTOR_ORDER if name not in weights]
    if missing:
        raise AdvisorError(f"权重缺因子 {missing}；四个因子 {FACTOR_ORDER} 一个都不能少")
    out: dict[str, float] = {}
    for name in FACTOR_ORDER:
        value = weights[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AdvisorError(f"{name} 的权重必须是数值（收到 {type(value).__name__}）")
        if value <= 0:
            raise AdvisorError(f"{name} 的权重必须为正（收到 {value}）——权重为 0 等于删因子，"
                               "请显式改 FACTOR_ORDER 的语义，不要用 0 偷偷关掉")
        out[name] = float(value)
    values = [out[name] for name in FACTOR_ORDER]
    if any(a <= b for a, b in zip(values, values[1:])):
        raise AdvisorError(
            f"权重必须沿优先级 {FACTOR_ORDER} 严格递减（收到 {values}）——"
            "不递减就等于改了优先级，那要显式改 FACTOR_ORDER 并留痕"
        )
    return out


_check_weights(FACTOR_WEIGHTS)


def _unit(value: float | None, name: str, item: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdvisorError(f"{item} 的 {name} 必须是数值或 None（收到 {type(value).__name__}）")
    if not 0.0 <= float(value) <= 1.0:
        raise AdvisorError(f"{item} 的 {name} 必须落在 [0,1]（收到 {value}）——越界说明上游算错了，不 clamp")
    return float(value)


# ---------------------------------------------------------------------------
# 一个因子的取值
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Factor:
    """某条复习项在某个因子上的取值。

    `value is None` 有两种来路，且都在 `detail` 里写明：整批都没数据（因子退出）、
    或这条自己没数据又无均值可借。**绝不把 None 当 0 用**。
    """

    name: str
    weight: float
    value: float | None
    detail: str
    imputed: bool = False  # True ⇒ 这条的数是用同批均值顶出来的

    @property
    def label(self) -> str:
        return FACTOR_LABELS[self.name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "weight": self.weight,
            "value": None if self.value is None else round(self.value, 6),
            "imputed": self.imputed,
            "detail": self.detail,
        }

    def text(self) -> str:
        """人话片段，直接进建议正文。"""
        if self.value is None:
            return f"{self.label} 弃权（{self.detail}）"
        tag = "·均值顶" if self.imputed else ""
        return f"{self.label} {self.value:.2f}{tag}（{self.detail}）"


@dataclass(frozen=True)
class Judgment:
    """一条复习项的建议判决。"""

    review_id: str
    skill_id: str
    kind: str
    due: str
    rank: int
    score: float | None  # None ⇒ 四个因子全弃权，只能按到期先后
    factors: tuple[Factor, ...]
    confidence: float  # 实测覆盖（权重占比，均值顶出来的不算）
    summary: str
    task_id: str | None = None

    @property
    def scored(self) -> bool:
        return self.score is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "skill_id": self.skill_id,
            "kind": self.kind,
            "due": self.due,
            "task_id": self.task_id,
            "rank": self.rank,
            "score": None if self.score is None else round(self.score, 6),
            "confidence": round(self.confidence, 6),
            "summary": self.summary,
            "factors": [f.to_dict() for f in self.factors],
        }


@dataclass(frozen=True)
class Advice:
    """一次建议排期。`judged` 已按建议顺序排好（rank 从 1 起）。"""

    user_id: str
    at: str
    judged: tuple[Judgment, ...] = ()
    cap: int = 0
    abstained: tuple[str, ...] = ()
    weights: tuple[tuple[str, float], ...] = ()

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(j.review_id for j in self.judged)

    def today(self, cap: int | None = None) -> tuple[str, ...]:
        """今日要做的（前 cap 条）。只截断视图，不删数据。"""
        limit = self.cap if cap is None else cap
        return self.order[:limit]

    def deferred(self, cap: int | None = None) -> tuple[str, ...]:
        limit = self.cap if cap is None else cap
        return self.order[limit:]

    def by_id(self, review_id: str) -> Judgment:
        for j in self.judged:
            if j.review_id == review_id:
                return j
        raise AdvisorError(f"这批建议里没有 {review_id}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "at": self.at,
            "cap": self.cap,
            "abstained": list(self.abstained),
            "weights": [{"name": n, "weight": w} for n, w in self.weights],
            "judged": [j.to_dict() for j in self.judged],
        }


# ---------------------------------------------------------------------------
# 因子求值（纯函数，不碰库）
# ---------------------------------------------------------------------------


def _deadline_factor(deadline: str | None, at: datetime) -> tuple[float | None, str]:
    if deadline is None:
        return None, "系统里没有这项截止日期（数据模型无对应字段）"
    when = mastery.parse_instant(deadline)
    days = (when - at).total_seconds() / 86400.0
    if days <= 0:
        return 1.0, f"已到截止（{_iso(when)}）"
    if days >= DEADLINE_HORIZON_DAYS:
        return 0.0, f"{days:.1f} 天后截止，视界 {DEADLINE_HORIZON_DAYS:g} 天外 ⇒ 不急"
    return 1.0 - days / DEADLINE_HORIZON_DAYS, f"{days:.1f} 天后截止"


def _forgetting_factor(r: float | None, at: datetime, last_review: str | None) -> tuple[float | None, str]:
    if r is None:
        return None, "这张卡还没复习过，谈不上遗忘曲线"
    elapsed = ""
    if last_review:
        days = (at - mastery.parse_instant(last_review)).total_seconds() / 86400.0
        elapsed = f"，距上次复习 {days:.1f} 天"
    value = 1.0 - r
    return value, f"FSRS 估计现在还能记得 {r * 100:.0f}%{elapsed}"


def _heat_factor(count: int, peak: int) -> tuple[float | None, str]:
    if peak <= 0:
        return None, f"近 {ERROR_WINDOW_DAYS} 天整批都没有该技能相关错误记录"
    return count / peak, f"近 {ERROR_WINDOW_DAYS} 天 {count} 次（本批最高 {peak} 次）"


def _gap_factor(p: float | None) -> tuple[float | None, str]:
    if p is None:
        return None, "该技能还没有任何一次计入证据（P 为空，不记 0）"
    return 1.0 - p, f"P={p:.2f}"


def _summarise(factors: Sequence[Factor]) -> str:
    """把四个因子拼成一句能读的话：先列参与打分的（按优先级），再单列弃权的。"""
    live = [f.text() for f in factors if f.value is not None]
    dead = [f"{f.label}弃权" for f in factors if f.value is None]
    if not live:
        return "整批四个因子都没有数据 ⇒ 只能按到期先后排（这是缺口，不是结论）"
    text = "；".join(live)
    if dead:
        text += "｜" + "、".join(dead)
    return text


def _score(factors: Sequence[Factor]) -> tuple[float | None, float]:
    """加权和 + 实测覆盖。返回 (综合分 or None, 覆盖度)。"""
    total = 0.0
    numerator = 0.0
    measured = 0.0
    for f in factors:
        if f.value is None:
            continue
        total += f.weight
        numerator += f.weight * f.value
        if not f.imputed:
            measured += f.weight
    if total <= 0:
        return None, 0.0
    return numerator / total, measured / sum(f.weight for f in factors)


def _bucket(score: float) -> int:
    """平手格：同一格视作平手，交给 due / review_id 决定先后。"""
    return int(round(score / TIE_GRID))


def advise(
    items: Iterable[Mapping[str, Any]],
    *,
    user_id: str,
    at: str,
    p_by_skill: Mapping[str, float | None] | None = None,
    r_by_skill: Mapping[str, float | None] | None = None,
    last_review_by_skill: Mapping[str, str | None] | None = None,
    error_counts: Mapping[str, int] | None = None,
    deadlines: Mapping[str, str] | None = None,
    cap: int = 2,
    weights: Mapping[str, float] | None = None,
) -> Advice:
    """一批复习项 → 建议排程。纯函数：不读库、不写库。

    只排"今天到期且仍 pending"的项（口径复用 `scheduler.sort_queue`，不另立一份
    "什么算到期"的判定）。未到期的、已完成的项不出现，也不在返回里被删掉——
    它们本来就还在库里 `pending`。

    `p_by_skill` / `r_by_skill` / `error_counts` / `deadlines` 缺省为"没有这项数据"，
    不是"值为 0"：缺了就是弃权或均值顶，见文件头第 3 条。
    """
    if isinstance(cap, bool) or not isinstance(cap, int):
        raise AdvisorError(f"cap 必须是整数（收到 {type(cap).__name__}）")
    if cap < 0:
        raise AdvisorError(f"cap 不能为负（收到 {cap}）")
    w = _check_weights(weights or FACTOR_WEIGHTS)
    instant = mastery.parse_instant(at)

    rows = sch.sort_queue(items, at=at)  # 已按 due 升序、只留 pending 且到期
    for row in rows:
        owner = row.get("user_id")
        if owner is not None and owner != user_id:
            raise AdvisorError(f"{row.get('review_id')} 属于 {owner}，不属于 {user_id}——不要混批排程")

    p_map = dict(p_by_skill or {})
    r_map = dict(r_by_skill or {})
    last_map = dict(last_review_by_skill or {})
    heat = dict(error_counts or {})
    dl = dict(deadlines or {})

    peak = max((heat.get(str(r["skill_id"]), 0) for r in rows), default=0)

    # 第一遍：逐项取原始值（None = 这条没有数据）
    raw: list[dict[str, tuple[float | None, str]]] = []
    for row in rows:
        skill = str(row["skill_id"])
        p = _unit(p_map.get(skill), "P", skill)
        r = _unit(r_map.get(skill), "R", skill)
        raw.append({
            "deadline": _deadline_factor(dl.get(skill), instant),
            "forgetting": _forgetting_factor(r, instant, last_map.get(skill)),
            "error_heat": _heat_factor(heat.get(skill, 0), peak),
            "mastery_gap": _gap_factor(p),
        })

    # 第二遍：给缺数据的项补"同批均值"；整批都缺的因子退出
    means: dict[str, float | None] = {}
    for name in FACTOR_ORDER:
        known = [raw[i][name][0] for i in range(len(rows)) if raw[i][name][0] is not None]
        means[name] = sum(known) / len(known) if known else None

    judged: list[tuple[tuple, Judgment]] = []
    for i, row in enumerate(rows):
        skill = str(row["skill_id"])
        factors: list[Factor] = []
        for name in FACTOR_ORDER:
            value, detail = raw[i][name]
            imputed = False
            if value is None and means[name] is not None:
                value = means[name]
                imputed = True
                detail = f"{detail}；按同批平均 {value:.2f} 顶"
            factors.append(Factor(name=name, weight=w[name], value=value, detail=detail, imputed=imputed))
        score, confidence = _score(factors)
        due_at = mastery.parse_instant(str(row["due"]))
        # 有分的排前面；同分格按 due 升序，再按 review_id（重跑稳定）
        key = (0 if score is not None else 1,
               -_bucket(score) if score is not None else 0,
               due_at, str(row["review_id"]))
        judged.append((key, Judgment(
            review_id=str(row["review_id"]),
            skill_id=skill,
            kind=str(row.get("kind") or ""),
            due=str(row["due"]),
            rank=0,
            score=score,
            factors=tuple(factors),
            confidence=confidence,
            summary=_summarise(factors),
            task_id=row.get("task_id"),
        )))

    judged.sort(key=lambda t: t[0])
    ordered = tuple(replace(j, rank=i) for i, (_, j) in enumerate(judged, start=1))

    return Advice(
        user_id=user_id,
        at=_iso(instant),
        judged=ordered,
        cap=cap,
        abstained=tuple(name for name in FACTOR_ORDER if means[name] is None),
        weights=tuple((name, w[name]) for name in FACTOR_ORDER),
    )


# ---------------------------------------------------------------------------
# 用户改序（留痕）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Override:
    """用户把某条复习项挪到第 `to_rank` 位。**理由必填**——不接受无理由改序。"""

    review_id: str
    to_rank: int
    reason: str
    at: str

    def to_dict(self) -> dict[str, Any]:
        return {"review_id": self.review_id, "to_rank": self.to_rank,
                "reason": self.reason, "at": self.at}


@dataclass(frozen=True)
class AuditLine:
    """一条改序留痕：系统建议第几 → 实际第几。"""

    review_id: str
    skill_id: str
    suggested_rank: int
    final_rank: int
    reason: str
    at: str

    @property
    def delta(self) -> int:
        return self.suggested_rank - self.final_rank  # 正数 = 往前挪了

    @property
    def moved(self) -> bool:
        return self.final_rank != self.suggested_rank

    def to_dict(self) -> dict[str, Any]:
        return {"review_id": self.review_id, "skill_id": self.skill_id,
                "suggested_rank": self.suggested_rank, "final_rank": self.final_rank,
                "delta": self.delta, "moved": self.moved,
                "reason": self.reason, "at": self.at}

    def text(self) -> str:
        if not self.moved:
            return f"{self.review_id} 保持第 {self.final_rank} 位（理由：{self.reason}）"
        arrow = "提前" if self.delta > 0 else "推后"
        return (f"{self.review_id} 从第 {self.suggested_rank} 位挪到第 {self.final_rank} 位"
                f"（{arrow} {abs(self.delta)} 位；理由：{self.reason}）")


@dataclass(frozen=True)
class Plan:
    """改序后的最终排程：顺序 + 审计行 + 今日/顺延视图。"""

    advice: Advice
    order: tuple[str, ...]
    audits: tuple[AuditLine, ...] = ()
    cap: int = 0

    def today(self) -> tuple[str, ...]:
        return self.order[:self.cap]

    def deferred(self) -> tuple[str, ...]:
        return self.order[self.cap:]

    def deviations(self) -> tuple[AuditLine, ...]:
        """真正挪动过名次的那几条（按了但没动的也留痕，只是不算 deviation）。"""
        return tuple(a for a in self.audits if a.moved)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.advice.user_id,
            "at": self.advice.at,
            "cap": self.cap,
            "order": list(self.order),
            "today": list(self.today()),
            "deferred": list(self.deferred()),
            "audits": [a.to_dict() for a in self.audits],
            "deviations": [a.to_dict() for a in self.deviations()],
            "advice": self.advice.to_dict(),
        }


def apply_overrides(advice: Advice, overrides: Iterable[Override]) -> Plan:
    """按用户改序生成最终排程。**改序不是删项**：集合必须与建议一致，一条不多一条不少。

    五条拒绝：不存在这条 / 同一条改两次（谁说了算？）/ 理由为空 / 名次越界 / 同名次
    互相挤压导致的重复 id。全部报错，不"尽力而为"——静默忽略一条改序，用户会以为生效了。
    """
    order = list(advice.order)
    suggested = {j.review_id: j.rank for j in advice.judged}
    seen: set[str] = set()
    audits: list[AuditLine] = []

    for ov in overrides:
        if not isinstance(ov, Override):
            raise AdvisorError(f"改序必须是 Override（收到 {type(ov).__name__}）")
        if ov.review_id not in suggested:
            raise AdvisorError(f"这批建议里没有 {ov.review_id}——改序不能凭空加项")
        if ov.review_id in seen:
            raise AdvisorError(f"{ov.review_id} 被改序两次——两条指令冲突时不做取舍，请合成一条")
        if not isinstance(ov.reason, str) or not ov.reason.strip():
            raise AdvisorError(f"{ov.review_id} 的改序没写理由——为什么要改是这一步唯一要留的东西")
        if isinstance(ov.to_rank, bool) or not isinstance(ov.to_rank, int):
            raise AdvisorError(f"{ov.review_id} 的目标名次必须是整数（收到 {type(ov.to_rank).__name__}）")
        if not 1 <= ov.to_rank <= len(order):
            raise AdvisorError(f"{ov.review_id} 的目标名次 {ov.to_rank} 越界（有效 1..{len(order)}）")

        seen.add(ov.review_id)
        order.remove(ov.review_id)
        order.insert(ov.to_rank - 1, ov.review_id)
        audits.append(AuditLine(
            review_id=ov.review_id,
            skill_id=advice.by_id(ov.review_id).skill_id,
            suggested_rank=suggested[ov.review_id],
            final_rank=order.index(ov.review_id) + 1,
            reason=ov.reason.strip(),
            at=ov.at,
        ))

    if sorted(order) != sorted(suggested):
        raise AdvisorError("改序后集合变了——这是实现 bug，不是用户输入问题")

    # 全部改完再回填真实名次：改到一半时的名次不是最终名次
    final = tuple(
        AuditLine(a.review_id, a.skill_id, a.suggested_rank, order.index(a.review_id) + 1,
                  a.reason, a.at)
        for a in audits
    )
    return Plan(advice=advice, order=tuple(order), audits=final, cap=advice.cap)


def append_audit(path: str | Path, plan: Plan, *, user_id: str | None = None) -> int:
    """把审计行追加成 JSONL（一行一条，便于 `jq` 与人工对账）。返回写入条数。

    **不是防篡改的**：本地文件谁都能改。它的作用是"改过就会被看见"，
    要实现"改不了"需要服务端 append-only + 认证（B-10 之后）。
    """
    if not plan.audits:
        return 0
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    uid = user_id or plan.advice.user_id
    written = 0
    with target.open("a", encoding="utf-8") as fh:
        for audit in plan.audits:
            fh.write(json.dumps(
                {"kind": "override", "user_id": uid, "plan_at": plan.advice.at,
                 **audit.to_dict()},
                ensure_ascii=False, sort_keys=True,
            ) + "\n")
            written += 1
    return written


# ---------------------------------------------------------------------------
# 从库里取因子（唯一 I/O）
# ---------------------------------------------------------------------------


def _skill_mastery(conn: sqlite3.Connection, user_id: str, skill_ids: Sequence[str],
                   reports: Sequence[Mapping[str, Any]]) -> dict[str, float | None]:
    """一次读库、逐技能折 P。复用 `mastery.skill_evidence` + `mastery.replay`，
    不在这里重写公式（P 的权威定义只有一处）。"""
    out: dict[str, float | None] = {}
    for skill in skill_ids:
        evidence = [mastery.skill_evidence(r["diagnosis"], skill, at=r["created_at"])
                    for r in reports]
        out[skill] = mastery.replay(evidence, user_id=user_id, skill_id=skill).p
    return out


def _error_counts(reports: Sequence[Mapping[str, Any]], pattern_skills: Mapping[str, Sequence[str]],
                  *, at: datetime, window_days: int) -> dict[str, int]:
    """窗口内"该技能相关错误出现了几次"。

    计数单位是**提交**而不是模式：一份提交里同时踩了"未重试"和"重试参数错"，
    对 `failure_retry` 只算 1 次——同一份作业踩两个坑不等于练了两次。
    窗口是闭区间 `[at - window_days, at]`；晚于 `at` 的提交不算（回看历史时不能用未来）。
    """
    start = at - timedelta(days=window_days)
    counts: dict[str, int] = {}
    for row in reports:
        when = mastery.parse_instant(row["created_at"])
        if when < start or when > at:
            continue
        hit: set[str] = set()
        for pattern in row.get("detected_patterns") or []:
            for skill in pattern_skills.get(str(pattern), ()):
                hit.add(str(skill))
        for skill in hit:
            counts[skill] = counts.get(skill, 0) + 1
    return counts


def _retrievability(conn: sqlite3.Connection, user_id: str, skill_ids: Sequence[str],
                    at: datetime, *, scheduler: sch.Scheduler | None = None,
                    ) -> tuple[dict[str, float | None], dict[str, str | None]]:
    """该用户各技能的 R（可提取性）与上次复习时刻。

    R 本身由 `scheduler.retrievability_at()` 算（全项目只此一处）——这里只负责
    "取卡 + 把 None 传下去"。`None` 表示"还没开始"，不是"忘光了"。
    """
    cards = sch.cards_from_rows(store.load_fsrs_cards(conn, user_id))
    r: dict[str, float | None] = {}
    last: dict[str, str | None] = {}
    for skill in skill_ids:
        card = cards.get(skill)
        if card is None or card.stability is None or card.last_review is None:
            r[skill] = None
            last[skill] = None
            continue
        r[skill] = sch.retrievability_at(card, at.isoformat(), scheduler=scheduler)
        last[skill] = _iso(card.last_review)
    return r, last


def advise_from_store(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    at: str,
    cap: int | None = None,
    deadlines: Mapping[str, str] | None = None,
    window_days: int = ERROR_WINDOW_DAYS,
    weights: Mapping[str, float] | None = None,
) -> Advice:
    """从库里读齐四个因子的输入，跑一次 `advise()`。**只读库**。

    `deadlines` 目前只能由调用方显式传（库里没有截止字段，见文件头第一节）。
    不传就是"没有这项数据"，因子弃权——**不会**去编一个。
    """
    instant = mastery.parse_instant(at)
    limit = store.daily_review_cap(conn, user_id) if cap is None else cap
    rows = store.list_review_items(conn, user_id)
    skills = sorted({str(r["skill_id"]) for r in rows})
    if not skills:  # 队列空是正常状态（还没失败过/都做完了），不是错误
        return Advice(user_id=user_id, at=_iso(instant), cap=limit,
                      weights=tuple((n, FACTOR_WEIGHTS[n]) for n in FACTOR_ORDER))

    reports = store.user_reports(conn, user_id)
    p = _skill_mastery(conn, user_id, skills, reports)
    r, last = _retrievability(conn, user_id, skills, instant)
    counts = _error_counts(reports, store.pattern_skill_map(conn), at=instant,
                           window_days=window_days)

    return advise(rows, user_id=user_id, at=at, p_by_skill=p, r_by_skill=r,
                  last_review_by_skill=last, error_counts=counts, deadlines=deadlines,
                  cap=limit, weights=weights)
