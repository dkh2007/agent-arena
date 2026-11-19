"""把 P3 对账结论写进诊断报告（D 口径，2026-11-19 人拍）。

## 为什么新增这个模块，而不是改 `reconciliation.py`

`reconcile()` 是对账**判定器**（返回三态 + 明细 + 诊断字段），本来就跑在评测侧；
它返回的字典带 `tolerance` / `derived_fields` / `trace_number_count` 这些**诊断字段**，
不是报告 DTO。把它整个塞进报告会撞 `additionalProperties:false`。这里只做两件事：
① 投影成 schema 认可的三态字段；② 生成**学生可见**的人话 note。

## 两条硬约束（人拍时已定，这里不重新拍）

1. **不进分数**：对账结论不写 `score`、不写断言、不计掌握度。credit 分母恒为权重和，
   一条"有代理/有重放才过得了"的断言等于给每份提交收 20% 的普遍税，上界从 1.0 掉到 0.8。
2. **不复用 `process_fact.missing`**：那个字段一为真 `ratio` 直接归零，语义完全不同。
   对账的 `unverifiable` 是"数据不足，不是答错"，跟"没交过程"不是一回事。

## `notes` 措辞：不写指控性字眼

`submission_api` 的 `view=original/effective` 都把 `notes` 原样给学生（已核证）。
所以对账 note 只陈述**中性事实 + 处置**，不写"疑似编造""欺诈"这类词——
`mismatch` 是"有值对不上，交复核"，不是对学生的定性。判定细节留在 JSON 字段里给评审看。
"""
from __future__ import annotations

from typing import Any

VALID_STATUSES = ("verified", "unverifiable", "mismatch")

# 学生可见的人话：三态各一句，只说事实与下一步，不做指控。
_STUDENT_NOTES = {
    "verified": "过程对账：报告数值均可在操作记录中找到出处。",
    "unverifiable": "过程对账：部分数值属汇总推导，操作记录里不会逐字出现；数据不足，不构成答错。",
    "mismatch": "过程对账：有数值暂未在操作记录中找到对应出处，已交人工复核。",
}


def project_verdict(result: dict) -> dict[str, Any]:
    """把 `reconcile()` 的结果投影成报告 `reconciliation` 字段（schema 认可的形状）。"""
    status = result.get("status")
    if status not in VALID_STATUSES:
        raise ValueError(f"对账状态非法：{status!r}，只能是 {VALID_STATUSES}")
    out: dict[str, Any] = {
        "status": status,
        "checked": [dict(i) for i in result.get("checked", [])],
    }
    if "counts_as_evidence" in result:
        out["counts_as_evidence"] = bool(result["counts_as_evidence"])
    if "reason" in result:
        out["reason"] = str(result["reason"])
    return out


def student_note(status: str) -> str:
    """学生可见的一句人话；不写指控性字眼。"""
    if status not in _STUDENT_NOTES:
        raise ValueError(f"未知对账状态：{status!r}")
    return _STUDENT_NOTES[status]


def attach(report: dict, result: dict) -> dict:
    """把对账结论挂到报告上（**原地修改并返回**，与 `build_report` 的收尾风格一致）。

    不碰 `score` / `assertions` / `process_fact` / `report_attribution`。
    调用方负责在 `validate_report()` 之前调它。
    **当前状态：落点已定、还没有生产调用方**——`reconcile()` 至今只在 demo/探针里跑，
    "要不要对每份提交做对账"是 B-20b 的另一半，不在本模块的拍板范围内。这里只保证
    "一旦接，结论有地方放、且放进去不会污染分数"。
    """
    verdict = project_verdict(result)
    report["reconciliation"] = verdict
    notes = report.setdefault("notes", [])
    note = student_note(verdict["status"])
    if note not in notes:
        notes.append(note)
    return report
