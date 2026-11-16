"""B-04b：把双评接进提交管线，并把「不一致」变成一张人工待办。

`llm_judge.py` 已经有双评、有一致率、有 `needs_arbitration(threshold)`，
但三样东西都没人用：报告里没有字段、没有队列、`submission_api` 也不调它。
本模块补的是**接线**，不是算法——一致率怎么算仍在 `llm_judge` 里。

## 这一层唯一新增的权力：把结果写进报告

写进报告的是 `judge_scores` / `judge_agreement` 两个**只读字段**（`docs/data-model.md` §5.1
早留好的位置）。除此之外一个字都不改：`score`、`assertions`、`report_attribution`、
`detected_patterns` 全部原样——`attach_judgement()` 会当场逐项核对，改了就抛错。

## 为什么「不一致」只排队、不冻结这次提交

两种读法都能自圆其说，写清取舍，免得以后有人以为是漏了：

- 读法 A（本模块采用）：`judge_agreement` 低说明**判据表述有歧义**，那是系统侧缺陷。
  这份提交仍然是学习者真实作答的结果，按 A 口径照记 P、照排复习。
  队列是给「改判据」的人看的待办，不是给这份成绩打的问号。
- 读法 B：判据不明 ⇒ 这份提交先不算数，等人工裁决后再放行。

选 A 的理由是决策 7（**数据缺失 ≠ 能力不足**）的同一条推论：把系统的口径问题冻结成
学习者的成绩缺失，等于把自己的缺陷记在对方账上。这也与 `llm_judge` 模块文档
「它不是『模型不好』，也不是『考生答错了』」一致。
**代价要认**：队列里的提交已经计入 P，将来若人工判定"判据要改"，历史成绩需要重算
（`mastery_snapshot.recompute_all()` 现成）——也就是说 A 比 B 多一步返工。
⇒ 这条是**口径判断，已登记待人拍**；要切 B 只需让 `schedule_submission` / 计入层
读队列状态，不必改本模块的数据结构。

## 阈值从哪来

`threshold` **必填、无默认值**，与 `llm_judge.needs_arbitration()` 同一个理由：
任何默认值都会变成事实上的拍板。标定是 B-04c（需 ≥2 个任务 × 每任务 ≥5 个提交），
在那之前调用方必须自己给出数字，并知道自己在用一个未标定的值。

## 默认不跑

`submission_api.submit()` 只有显式传入 `judge=` 才会跑双评——它花钱、要网络、
且模型不可用时会挡住提交。默认关闭意味着：**不接 judge 的部署行为与 round-89 完全一致**。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Iterable

try:  # 作为包被 import
    from . import diagnosis_report as dr
    from . import llm_judge as lj
    from . import store
except ImportError:  # 作为独立脚本/被单文件测试直接 import
    import diagnosis_report as dr  # type: ignore[no-redef]
    import llm_judge as lj  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]

JUDGE_FIELDS = ("judge_scores", "judge_agreement")
# 允许被打上 judge 字段的分数键一个都不许动——列出来是为了让"改了就报错"有据可依。
FROZEN_KEYS = ("score", "assertions", "report_attribution", "attribution_policy",
               "skill_gaps", "unattributed_patterns", "trace_audit", "process_fact")


class JudgePipelineError(RuntimeError):
    """接线层的前置条件不成立。**一律抛错，不静默补默认值。**"""


@dataclass(frozen=True)
class JudgeOutcome:
    """一次「双评 + 归档」的完整结果。"""

    report: dict
    result: lj.DoubleJudgeResult
    threshold: float

    @property
    def needs_arbitration(self) -> bool:
        return self.result.needs_arbitration(self.threshold)

    @property
    def submission_id(self) -> str:
        return self.report["submission"]["submission_id"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "judge_agreement": self.result.agreement,
            "threshold": self.threshold,
            "needs_arbitration": self.needs_arbitration,
            "runs": self.result.runs,
        }


def _check_threshold(threshold: Any) -> float:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise JudgePipelineError(f"threshold 必须是数值（收到 {threshold!r}）")
    value = float(threshold)
    if not 0.0 < value <= 1.0:
        raise JudgePipelineError(
            f"threshold 应在 (0,1]（收到 {threshold!r}）。"
            "0 等于永不仲裁、>1 等于全部仲裁，两者都不该被写成一个阈值。"
        )
    return value


def attach_judgement(report: dict, result: lj.DoubleJudgeResult) -> dict:
    """把双评结果写进报告的**副本**。原报告不动，且除两个字段外逐项不变。

    为什么返回副本而不是就地改：调用方可能还持有"未经 judge 的报告"（落库、对拍、
    对比实验都要它）。就地改会让同一对象在不同时刻是两份不同的证据。
    """
    if not isinstance(report, dict):
        raise JudgePipelineError(f"report 必须是 dict（收到 {type(report).__name__}）")
    if not isinstance(result, lj.DoubleJudgeResult):
        raise JudgePipelineError(
            f"result 必须是 DoubleJudgeResult（收到 {type(result).__name__}）"
        )
    existing = [k for k in JUDGE_FIELDS if k in report]
    if existing:
        raise JudgePipelineError(
            f"报告已带 {existing}——不覆盖。"
            "judge 结果是这一次评价的证据，重评要另起一份提交或显式清空后再接"
        )

    for key in FROZEN_KEYS:
        if key not in report:
            raise JudgePipelineError(f"报告缺 {key}——不是一份完整报告，拒绝往上挂 judge 字段")

    fields = result.to_report_fields()
    new = copy.deepcopy(report)
    new.update(copy.deepcopy(fields))

    # 逐项核对：除了 JUDGE_FIELDS，其余字段必须与原件相等。
    # 这不是"再算一遍"，是防将来有人在这里顺手改分（比如把 agreement 掺进 ratio）。
    extra = set(new) - set(report) - set(JUDGE_FIELDS)
    if extra:
        raise JudgePipelineError(f"attach 多写了字段 {sorted(extra)}——只允许 {JUDGE_FIELDS}")
    for key in report:
        if new[key] != report[key]:
            raise JudgePipelineError(
                f"attach 改动了 {key}：judge 不参与判定，任何分数/断言变化都是缺陷"
            )
    return new


def run_judge(
    report: dict,
    raw: dict,
    task: dict,
    judge: lj.JudgeFn,
    *,
    threshold: float,
    runs: int = lj.DEFAULT_RUNS,
    vocab: Iterable[str] | None = None,
) -> JudgeOutcome:
    """双评一份提交并挂到报告上。**不落库、不入队**（那两件事各有专门的函数）。

    `raw` 是 `trace_pipeline.assemble_raw()` 的产物：judge 需要看 trace 与产物，
    而报告里已经把断言结果（含 `expected`）算完了——拿报告喂 judge 等于把答案递过去。
    """
    limit = _check_threshold(threshold)
    if not isinstance(raw, dict):
        raise JudgePipelineError(f"raw 必须是 dict（收到 {type(raw).__name__}）")
    if not isinstance(task, dict) or not task.get("task_id"):
        raise JudgePipelineError("task 必须是已加载的任务定义（含 task_id）")

    context = lj.build_judge_context(task, raw)
    known = set(vocab) if vocab is not None else dr.load_vocabulary()
    result = lj.double_judge(judge, context, runs=runs, vocab=known)
    return JudgeOutcome(report=attach_judgement(report, result), result=result, threshold=limit)


def record_arbitration(conn, outcome: JudgeOutcome, *, opened_at: str | None = None) -> dict | None:
    """一致率低于阈值时入队；够一致就返回 `None`（不入队不是错误）。

    **必须在 `save_submission()` 之后调用**：队列的外键指向 `submissions`，
    先入队会指向一条还不存在的证据。
    """
    if not isinstance(outcome, JudgeOutcome):
        raise JudgePipelineError(f"outcome 必须是 JudgeOutcome（收到 {type(outcome).__name__}）")
    if not outcome.needs_arbitration:
        return None
    return store.save_arbitration_entry(
        conn,
        outcome.submission_id,
        judge_agreement=outcome.result.agreement,
        threshold=outcome.threshold,
        reason="judge_disagreement",
        opened_at=opened_at,
    )
