"""状态层的第一个跨提交消费者：掌握度 P（B-07）。

**这一步解决什么**：round-75 之后 `submissions` 表里已经有数据，但没有任何代码读它——
"存了但没人看"等于白存。P 是第一个真正的消费者，也是 B-08（复习调度）与 B-09（四因子排期）
的共同前置。

## 公式只抄一处

`docs/data-model.md` §5.2（round-70 P1 拍板）：

    P_k = (1 - λ)·P_{k-1} + λ·c_k

λ 是固定平滑系数，`c_k` 是第 k 次**计入**的证据给出的表现值 ∈ [0,1]。
**唯一权威定义在 data-model**；本模块只实现它，不另立口径。

## 四条设计决定

1. **`c_k` 是权重加权通过率，不是 `score.ratio`。**
   一份提交可能同时压到多个技能（R2 属 `multi_step_planning`、R3 属 `failure_retry`、
   R4 属 `tool_param_validation`）。拿整份 `ratio` 去更新每个技能，等于让 R2 答错也拉低
   `tool_param_validation` 的掌握度——那不是"聚合"，是串味。所以按断言自己的 `skill_id`
   分组，`c = Σ(通过断言权重) / Σ(计入断言权重)`。

2. **`unscored` / `blocked` 从分子分母一起剔除，不是记 0 分。**
   决策 7「数据缺失 ≠ 能力不足」。记 0 分会把"没上报 trace"惩罚成"这个技能不会"，
   而这恰好是学习者最容易踩、又最不该扣掌握度的坑。剔除后若该技能在这份提交上
   **一条计入断言都没有**，则这次提交对该技能**不产生证据**（返回 `None`），P 原封不动。

3. **时间轴用 `created_at`（落库锚点），不是列表顺序。**
   `save_submission` 已把 `created_at` 锚到 `submission.evaluated_at`。补录历史时若按插入顺序
   算，P 的递推顺序会错，而且错得看不出来（数值仍单调）。排序**按解析后的时刻**比较，
   不按字符串——`2026-11-07T12:00:00+08:00` 与 `2026-11-07T06:00:00Z` 是同一时刻，
   字符串比较却会判成"前者更晚"。

4. **初值不发明，用第一次观测播种；不实现衰减。**
   - **初值**：`p0=None`（默认）时 `P_1 = c_1`。另一条路是给一个中性先验（如 0.5），
     但那会凭空造一个没有依据的数，且让"交了一份满分"首轮只拿到 0.65。data-model 明写
     「P 的落字段与初值未定」，所以这里**两者都可配**，默认选"不发明先验"，并把它标为**假设**。
   - **衰减**：本模块**仍不内置衰减**，但不再是缺口——衰减在 `arena/decay.py`（B-08b），
     由调用方显式传 `as_of` 才生效（`decayed_at()`）。分开的理由是时间轴不同：
     `replay()` 的时间轴是"提交发生的时刻"，衰减的时间轴是"最后一次练过到 as_of 之间"，
     把两者揉进一个函数会让"我没给 as_of 时 P 是多少"变得说不清。
     **默认仍不衰减**：不传 `as_of` 的老调用行为一个字没变，所以 `test_decay_*` 的旧断言
     改成"不传 as_of ⇒ 两条序列相同"，而不是删掉。

5. **门禁状态决定证据算不算，但只按已拍板的那一格切（`voided` 排除 / `disqualified` 照记）。**
   **`voided`（版本对不上）⇒ 整份不计入 P**（2026-11-16 人拍板「甲」；与
   `diagnosis-report.schema.json` 的 `task_version` 说明、`review-packet.md` 决策 11 一致）。
   **提交行与报告照留**——它是一次练习记录，只是不参与长期状态量；`Evidence` 里仍能把
   每条断言标成 `voided:state=...`，所以"这次为什么没算"查得到，不是静默丢数据。
   所以 `build_series()` 对"只有作废提交"的用户给 `p is None`（决策 7 的同一个形状），
   `mastery_snapshots` 因此没有行——**不许写成 0.0**。

   **`disqualified`（没交产物/没跑完）仍然照记**，即 B-07b 的 **A** 口径不动
   （依据 data-model §5.1「门禁只管分数，不管观测」）。两者必须分开：作废是"这次不算数"，
   不合格是"这次算数但没做好"，把它们并成一格会让版本错位写成冤案。A 口径有个不利后果，已被实测固定：
   **一份"没交产物"的提交会让该技能的 `c = 1.0`**，而初值播种使**首次观测直接等于 1.0** ——
   即"交白卷一次"与"真做对一次"在 P 上不可区分（`test_one_white_paper_looks_like_a_perfect_run`）。
   但**改不改是产品口径**，本模块不擅自改；它做的是把切换成本降下来：
   每条证据与每一步递推都记 `gate_status`（`ok` / `disqualified` / `voided`），
   将来切到 C（降权计入）或 B（排除）**只需在计入层加一条过滤**，
   不必回头改已积累的历史，也**不必**把报告里的 credit 断言改标 `blocked`
   （那会用评分逻辑吞掉诊断数据，是多份文档明确禁止的实现方式）。

6. **缺过程（`process_fact.missing`）⇒ 整份不计入 P**（2026-11-19 人拍板「甲」，B-04i）。
   与 `voided` 同款排除，但**是另一件事**：`voided` 是版本错位（这次不算数），
   `missing` 是题要过程而这次没收到（这次也不算数）；`disqualified` 仍照记（A 口径）。
   此前 `report_attribution.counts_toward_mastery=False` 与
   `diagnosis-report-schema.md` 都白纸黑字写着「缺过程不计掌握度」，
   但 `skill_evidence()` 从未读 `process_fact`，于是答案对、过程缺的那份
   照样在 `multi_step_planning` 上记 `c=1.0`——文档承诺没有执行者。
   本次把执行者补在**计入层**：分数、断言状态、报告与提交行一个字不动。
   留痕分两路：`gate_status` 仍是三态门禁词表（排程层依赖它，不塞第四态），
   另设 `process_missing` 布尔字段记在 `Evidence` 上并进序列化；
   `ignored` 里每条断言标 `process_missing:state=...`，`skipped` 记 `process_missing`。
   缺过程那次不产生 `Step`（它没推动 P），所以 `Step` 不加恒假的字段。

## P 落在哪（round-88 / B-07c 更新）

**本模块仍然一行库都不写**：`build_series()` / `replay()` / `decayed_at()` 全是纯读，
唯一数据来源是 `submissions`。这一点有测试锁住
（`test_replay_never_writes_to_the_fsrs_card_table`）。

P 的**缓存**落在独立表 `mastery_snapshots`（v2），由 `mastery_snapshot.py` 负责写与校验；
`user_skill_states` 至今没有 P 字段，一个字段都没动——B-08b 的
「没卡 ≠ 忘光了」契约靠那张表的空/有区分，不能被"只有 P 没卡"的半行污染。
重放是唯一权威，快照不一致时以重放为准；权威口径见 `docs/data-model.md` §5.3。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

# 平滑系数默认值。**这是假设，不是标定结果**：没有答题数据之前任何具体数值都是拍的。
# 取 0.3（新证据占三成、历史占七成）落在 EMA 类掌握度估计的常用区间内，
# 但必须经 B-07b 的回测（同技能变体的达标预测）才能定值。改这个数不影响公式正确性。
LAMBDA_DEFAULT = 0.3

# 门禁状态词表。只三种，且在 `replay()` 里校验——拼错会静默躲过将来的过滤规则。
GATE_STATUSES = ("ok", "disqualified", "voided")


class MasteryError(ValueError):
    """输入不成立（越界的 λ / P / c、缺字段、时间无法解析）。不吞、不 clamp。"""


# ---------------------------------------------------------------------------
# 证据：一份报告 → 某技能吃到的表现值
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ignored:
    """被排除在掌握度之外的断言，附原因。留痕是为了"少算"这件事可审计。"""

    assertion_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"assertion_id": self.assertion_id, "reason": self.reason}


@dataclass(frozen=True)
class Evidence:
    """一次提交对**一个**技能给出的证据。

    `counts=False` 表示这份提交在该技能上没有可用证据（全 `unscored` / `blocked` /
    门禁零权重，或整份被 `voided` / 缺过程排除）。这时**不许**把它当成 0 分——
    那是决策 7 明令禁止的换算。
    """

    submission_id: str
    skill_id: str
    at: str
    passed_weight: float
    counted_weight: float
    ignored: tuple[Ignored, ...] = ()
    # 这份提交当时的门禁状态：`ok` / `disqualified` / `voided`。
    # `voided` ⇒ 整份不计入（本类的 `counts` 直接是 False，理由见 `skill_evidence`）；
    # `disqualified` ⇒ 仍照记（B-07b 的 A 口径），留它是为了将来切 C（降权）/ B（排除）
    # 只需在计入层加一条过滤，而不必回头改已积累的历史。
    gate_status: str = "ok"
    # 缺过程（B-04i 甲）：题要过程而这次没收到 ⇒ 整份不计入。
    # **不塞进 gate_status**：那是门禁三态词表，排程层按它读；缺过程不是门禁判定。
    process_missing: bool = False

    @property
    def counts(self) -> bool:
        return self.counted_weight > 0

    @property
    def c(self) -> float:
        if not self.counts:
            raise MasteryError(
                f"{self.submission_id} 在技能 {self.skill_id} 上没有计入断言"
                "（全是 unscored/blocked 或零权重），不构成证据，不能当 0 分用"
            )
        return self.passed_weight / self.counted_weight

    def ignored_reasons(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.ignored:
            key = item.reason.split(":", 1)[0]
            out[key] = out.get(key, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "skill_id": self.skill_id,
            "at": self.at,
            "counts": self.counts,
            "c": _round(self.c) if self.counts else None,
            "passed_weight": _round(self.passed_weight),
            "counted_weight": _round(self.counted_weight),
            "gate_status": self.gate_status,
            "process_missing": self.process_missing,
            "ignored": [i.to_dict() for i in self.ignored],
        }


def _round(value: float) -> float:
    """只为输出好看。**比较与递推一律用原值**，四舍五入攒到第 10 次会漂。"""
    return round(value, 6)


def skill_evidence(report: dict, skill_id: str, *, at: str | None = None) -> Evidence:
    """从一份诊断报告里取出某技能的证据。

    计入规则全部来自报告自带的 `attribution_policy`，本函数不内置任何状态白名单——
    否则口径会分叉成"代码说 pass/fail 算"与"任务 YAML 说 counts_toward_mastery"
    两套，将来改 YAML 时报告变了而 P 不变。

    **整份级例外有两个**：`score.voided`（版本错位，2026-11-16 人拍板）与
    `process_fact.missing`（题要过程却没收到，2026-11-19 人拍板甲，B-04i）。
    两者都让这份提交一条都不计入，但分别留痕，不能并成一个布尔：
    它们来自提交层而不是 `attribution_policy`，因为「这次不算数」是提交级判定，
    不是某种断言状态的性质——写成 per-state 规则就得给每个状态都复制一遍。

    `process_fact.missing` 缺失或不是布尔时**报错**，不默认 false：
    默认 false 会把「没记过程事实」当成「过程齐全」，恰好抹掉本次要执行的那个信号，
    与 `score.disqualified` 缺字段时的处理同款。
    """
    if not isinstance(report, dict):
        raise MasteryError(f"report 应是 dict，收到 {type(report).__name__}")
    submission = report.get("submission") or {}
    submission_id = submission.get("submission_id")
    if not isinstance(submission_id, str) or not submission_id:
        raise MasteryError("报告缺 submission.submission_id")
    when = at or submission.get("evaluated_at")
    if not isinstance(when, str) or not when:
        raise MasteryError(f"{submission_id} 缺 evaluated_at，且未显式给 at——时间轴不能猜")

    policy = report.get("attribution_policy")
    if not isinstance(policy, dict) or not policy:
        raise MasteryError(f"{submission_id} 缺 attribution_policy，无法判断哪些状态计入掌握度")

    # 门禁状态：`voided` 整份排除（2026-11-16 人拍板甲，见设计决定 5），
    # `disqualified` 照记（B-07b 的 A 口径）。缺字段就报错而不是默认 `ok`：
    # 默认成 `ok` 会把"整份不合格"记成"合格"，正好抹掉将来切 B/C 唯一需要的那个信号。
    score = report.get("score")
    if not isinstance(score, dict) or "disqualified" not in score or "voided" not in score:
        raise MasteryError(f"{submission_id} 缺 score.disqualified / score.voided，无法记录门禁状态")
    # **顺序有意义**：`voided` 先判。两者可以同时为真（交了一份空白的旧版提交），
    # 若先判 `disqualified` 就会把它当"不合格但算数"计入 P —— 作废被降级成不合格，
    # 版本错位照样刷高掌握度。作废是更强的判定（这次根本不算数），必须压过不合格。
    if score["voided"]:
        gate_status = "voided"
    elif score["disqualified"]:
        gate_status = "disqualified"
    else:
        gate_status = "ok"
    # 作废 = 这次不算数（版本对不上），不是"不会"，也不该抬高任何技能的状态量。
    exclude_voided = gate_status == "voided"

    # 缺过程（B-04i 甲）：报告自己已经算出 `missing`，这里只负责执行它。
    # 手写最小报告也必须带这个字段——缺了就报错，理由见本函数 docstring。
    process = report.get("process_fact")
    if not isinstance(process, dict) or not isinstance(process.get("missing"), bool):
        raise MasteryError(f"{submission_id} 缺 process_fact.missing（布尔），无法判断过程是否收到")
    process_missing = process["missing"]

    passed = counted = 0.0
    ignored: list[Ignored] = []
    for a in report.get("assertions") or []:
        if a.get("skill_id") != skill_id:
            continue
        state = a.get("state")
        rule = policy.get(state)
        if not isinstance(rule, dict) or "counts_toward_mastery" not in rule:
            raise MasteryError(
                f"{submission_id} 的断言 {a.get('id')} 状态 {state!r} 不在 attribution_policy 里"
            )
        weight = a.get("weight", 0)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise MasteryError(f"{submission_id} 的断言 {a.get('id')} 权重非数值：{weight!r}")
        if exclude_voided or process_missing:
            # 校验照做（口径/权重非法仍要炸），但一条都不计入：P 不能被"这次不算数"推动。
            # 两者同时成立（旧版提交又没交过程）时只标 `voided`，避免同一条断言
            # 在 ignored 里计两次；`Evidence.process_missing` 仍为 True，审计信息不丢。
            reason = "voided" if exclude_voided else "process_missing"
            ignored.append(Ignored(str(a.get("id")), f"{reason}:state={state}"))
            continue
        if not rule["counts_toward_mastery"]:
            ignored.append(Ignored(str(a.get("id")), f"not_counted:state={state}"))
            continue
        if weight <= 0:
            # 门禁层 weight 恒为 0（见 schema：gate 层保留 weight 字段只为结构统一）。
            # 它计入掌握度却没有任何权重 ⇒ 失败在 P 上不留痕。当前任务的门禁断言 skill_id 全为 null，
            # 所以现状无实际影响；一旦有任务给门禁断言写 skill_id，这里就会漏记失败（登记 B-07b）。
            ignored.append(Ignored(str(a.get("id")), f"zero_weight:state={state}"))
            continue
        counted += weight
        if state == "pass":
            passed += weight

    return Evidence(
        submission_id=submission_id,
        skill_id=skill_id,
        at=when,
        passed_weight=passed,
        counted_weight=counted,
        ignored=tuple(ignored),
        gate_status=gate_status,
        process_missing=process_missing,
    )


# ---------------------------------------------------------------------------
# 递推
# ---------------------------------------------------------------------------


def _check_unit(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MasteryError(f"{name} 必须是数值，收到 {type(value).__name__}")
    if not 0.0 <= float(value) <= 1.0:
        raise MasteryError(f"{name} 必须在 [0,1]，收到 {value}")
    return float(value)


def update_p(p_prev: float, c: float, lam: float = LAMBDA_DEFAULT) -> float:
    """`P_k = (1-λ)·P_{k-1} + λ·c`。越界一律报错——clamp 会把"模型/数据错"伪装成合法低分。"""
    _check_unit(p_prev, "p_prev")
    _check_unit(c, "c")
    if isinstance(lam, bool) or not isinstance(lam, (int, float)):
        raise MasteryError(f"λ 必须是数值，收到 {type(lam).__name__}")
    if not 0.0 < float(lam) <= 1.0:
        # λ=0 表示永不学习（P 恒等于初值），那不是平滑而是关掉功能；λ>1 会外推到 [0,1] 之外。
        raise MasteryError(f"λ 必须在 (0,1]，收到 {lam}")
    return (1.0 - float(lam)) * float(p_prev) + float(lam) * float(c)


@dataclass(frozen=True)
class Step:
    """一步递推的完整留痕：改公式时能一眼看出哪一步的数变了。"""

    submission_id: str
    at: str
    c: float
    p_before: float | None
    p_after: float
    counted_weight: float
    seeded: bool  # 这一步是"初值播种"（P_1 = c_1），没走递推式
    gate_status: str = "ok"  # 这份提交当时的门禁状态，只留痕

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "at": self.at,
            "c": _round(self.c),
            "p_before": None if self.p_before is None else _round(self.p_before),
            "p_after": _round(self.p_after),
            "counted_weight": _round(self.counted_weight),
            "seeded": self.seeded,
            "gate_status": self.gate_status,
        }


@dataclass(frozen=True)
class Skipped:
    submission_id: str
    at: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"submission_id": self.submission_id, "at": self.at, "reason": self.reason}


@dataclass(frozen=True)
class MasterySeries:
    user_id: str
    skill_id: str
    lam: float
    p0: float | None
    steps: tuple[Step, ...] = ()
    skipped: tuple[Skipped, ...] = ()
    ignored: dict[str, int] = field(default_factory=dict)

    @property
    def p(self) -> float | None:
        """最新掌握度。没有任何证据时是 `None`——不是 0，也不是 0.5。"""
        return self.steps[-1].p_after if self.steps else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "skill_id": self.skill_id,
            "lambda": self.lam,
            "p0": self.p0,
            "p": None if self.p is None else _round(self.p),
            "observations": len(self.steps),
            "skipped": len(self.skipped),
            "steps": [s.to_dict() for s in self.steps],
            "skipped_detail": [s.to_dict() for s in self.skipped],
            "ignored": dict(sorted(self.ignored.items())),
        }


_EPOCH_NAIVE = timezone.utc


def parse_instant(value: str) -> datetime:
    """解析 ISO 时刻。`Z` 结尾、纯日期、带偏移都要能处理——否则补录数据会整批报错。"""
    if not isinstance(value, str) or not value.strip():
        raise MasteryError(f"时刻必须是非空字符串，收到 {value!r}")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MasteryError(f"无法解析时刻 {value!r}：{exc}") from exc
    # 无时区的按 UTC 解释并**在输出里标出来**（naive 与 aware 相减会直接抛错，
    # 混用时静默当本地时间会让排序在夏令时边界上翻车）。
    return parsed.replace(tzinfo=_EPOCH_NAIVE) if parsed.tzinfo is None else parsed


def replay(
    evidence: Iterable[Evidence],
    *,
    user_id: str,
    skill_id: str,
    lam: float = LAMBDA_DEFAULT,
    p0: float | None = None,
) -> MasterySeries:
    """按时间把证据折成一条 P 序列。不计数的证据进 `skipped`，且**不推进** P。"""
    if isinstance(lam, bool) or not isinstance(lam, (int, float)) or not 0.0 < float(lam) <= 1.0:
        raise MasteryError(f"λ 必须在 (0,1]，收到 {lam!r}")
    if p0 is not None:
        _check_unit(p0, "p0")

    items = list(evidence)
    # 稳定排序：同一时刻的多份提交按 submission_id 定序，保证重跑结果一致。
    # 先把时刻解析出来再排——字符串比较在混合偏移下会给出错误的先后。
    decorated = [(parse_instant(e.at), e.submission_id, e) for e in items]
    decorated.sort(key=lambda t: (t[0], t[1]))

    steps: list[Step] = []
    skipped: list[Skipped] = []
    ignored: dict[str, int] = {}
    p: float | None = None

    for _, _, ev in decorated:
        for reason, count in ev.ignored_reasons().items():
            ignored[reason] = ignored.get(reason, 0) + count
        if ev.skill_id != skill_id:
            raise MasteryError(f"证据的技能是 {ev.skill_id}，与本次回放的 {skill_id} 不一致")
        if ev.gate_status not in GATE_STATUSES:
            raise MasteryError(
                f"{ev.submission_id} 的 gate_status={ev.gate_status!r} 不在 {GATE_STATUSES}"
                "——拼错的状态名会让将来的降权/排除过滤漏掉这一条"
            )
        if not isinstance(ev.process_missing, bool):
            raise MasteryError(f"{ev.submission_id} 的 process_missing 非布尔：{ev.process_missing!r}")
        if ev.process_missing and ev.counts:
            # 矛盾证据：标记了「整份不算数」却带着正权重进来。静默跳过哪一种都会
            # 让口径分叉（调用方以为排除了，实际 P 被推动），所以在这里炸掉。
            raise MasteryError(
                f"{ev.submission_id} 标记缺过程却有 {ev.counted_weight} 计入权重——口径冲突"
            )
        if not ev.counts:
            reason = "process_missing" if ev.process_missing else "no_counted_assertion"
            skipped.append(Skipped(ev.submission_id, ev.at, reason))
            continue
        c = ev.c
        if p is None:
            if p0 is None:
                p = c
                steps.append(Step(ev.submission_id, ev.at, c, None, p, ev.counted_weight, True,
                                  ev.gate_status))
                continue
            p = float(p0)
        before = p
        p = update_p(p, c, lam)
        steps.append(Step(ev.submission_id, ev.at, c, before, p, ev.counted_weight, False,
                          ev.gate_status))

    return MasterySeries(
        user_id=user_id,
        skill_id=skill_id,
        lam=float(lam),
        p0=p0,
        steps=tuple(steps),
        skipped=tuple(skipped),
        ignored=ignored,
    )


# ---------------------------------------------------------------------------
# 从库里读历史（本模块唯一的 I/O）
# ---------------------------------------------------------------------------


def _user_reports(conn: sqlite3.Connection, user_id: str) -> list[tuple[str, str, dict]]:
    """`(submission_id, created_at, report)`，按 `created_at` 升序。

    SQL 与 JSON 解码都留在 `store.py`（那里是表结构的唯一所有者）；本模块只消费。
    只读 `submissions` + `reappraisals`，不碰 `user_skill_states`（P 还没有落字段）。

    `report` 是**当前生效**那份（重评过就是最新 revision）。这一点很关键：
    若这里读的是落库时那份，判据改完的"批量回溯重算"就会变成"库里记了一堆重评、
    成绩一分没动"——上一轮 §91.5 ② 记的正是这个缺口。

    **只用「可信度已定」的提交**（round-111 / B-26）：双评分歧未裁决的那份不进重放。
    理由与 `voided` 同源——"这次算不算数还没定"，不能拿来改状态；区别是 `voided` 是
    判据定的，这里是人还没看。裁决完自动恢复，不需要补写任何字段。
    """
    from . import store  # 局部导入：与 submission_api 同惯例，避免模块级循环依赖

    out: list[tuple[str, str, dict]] = []
    for row in store.user_reports(conn, user_id, only_settled=True):
        out.append((row["submission_id"], row["created_at"], row["report"]))
    return out


def build_series(
    conn: sqlite3.Connection,
    user_id: str,
    skill_id: str,
    *,
    lam: float = LAMBDA_DEFAULT,
    p0: float | None = None,
) -> MasterySeries:
    """从库里读该用户全部提交 → 逐份取该技能的证据 → 折成 P 序列。

    时间轴用库里的 `created_at`（落库时已锚到 `evaluated_at`），不用报告里再读一次：
    两个来源一致时无所谓，不一致时以库为准——否则"P 的时间轴"和"复习间隔的时间轴"会分叉。
    """
    evidence = [
        skill_evidence(report, skill_id, at=created_at)
        for _, created_at, report in _user_reports(conn, user_id)
    ]
    return replay(evidence, user_id=user_id, skill_id=skill_id, lam=lam, p0=p0)


def decayed_at(
    conn: sqlite3.Connection,
    user_id: str,
    skill_id: str,
    *,
    as_of: str,
    lam: float = LAMBDA_DEFAULT,
    p0: float | None = None,
):
    """读出 P，并给出它在 `as_of` 时刻的**衰减后**读法：`(series, DecayResult)`。

    `as_of` 必填（B-08b 开工条件③）：默认墙钟会让同一份数据两次调用给出不同答案，
    复核时说不出"谁改的"。

    S 与 `last_review` 取自 `user_skill_states` 里那张卡（`decay` 只按值算，不碰库）。
    技能还没有卡时 `decay` 会返回 `applied=False, reason="no_card"` 并保留原值——
    "从没失败过所以没建卡"不等于"忘光了"。
    """
    from . import decay  # 局部导入：decay 依赖本模块的类型，模块级导入会成环
    from . import store

    series = build_series(conn, user_id, skill_id, lam=lam, p0=p0)
    row = store.load_fsrs_cards(conn, user_id).get(skill_id) or {}
    return series, decay.decay_series(
        series,
        as_of=as_of,
        stability=row.get("stability"),
        last_review=row.get("last_review"),
    )


def series_for_lambdas(
    evidence: Sequence[Evidence],
    *,
    user_id: str,
    skill_id: str,
    lambdas: Sequence[float] = (0.1, 0.2, 0.3, 0.5, 0.7),
    p0: float | None = None,
) -> dict[float, MasterySeries]:
    """同一批证据在不同 λ 下的终值。用来展示"参数没标定时结论有多不稳"。"""
    return {
        lam: replay(evidence, user_id=user_id, skill_id=skill_id, lam=lam, p0=p0)
        for lam in lambdas
    }
