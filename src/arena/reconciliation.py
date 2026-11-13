"""P3 / R6：产物与过程记录的对账（答案里的数值必须能从 trace 的工具返回值推出）。

round-70 定案（评审包决策 10）：防作弊**保对账**，随机化只作补充。理由是威胁模型里
最要命的是「不干活却报出正确答案」——它不仅骗分数，还骗走诊断（系统据此排错复习项）。
随机化挡的是「抄上次答案」，挡不住这一条。

本模块是 P3 的**最小可运行实现**，故意不接进 `tasks/dbg-001-order-report.yaml` 的 rubric：
一旦成为 credit 断言，分母就不再是 4，五夹具分数序与已发布评审包都要跟着动。
按人拍板口径，先做成独立判定器 + 测试，接入属下一批（登记见 §60）。

三种结果（对应决策 10 的「验证不通过 ≠ 答错」）：

  verified     待查数值都能在过程记录里找到出处
  unverifiable 记录里根本没有可用数值（**数据不足，不是答错**：不计掌握度、不记短板）
  mismatch     记录里有数值，但某个待查值找不到出处（疑似编造，交人复核）

调用关系：状态层在更新掌握度之前调用一次；它不是断言，不产生 error_patterns。
"""

from __future__ import annotations

import json
import re
from typing import Any

# 与 R2 的浮点容差一致（tasks/dbg-001-order-report.yaml 的 tolerance.total_amount = 0.01）。
DEFAULT_TOLERANCE = 0.01

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

SOURCE_FIELDS = ("result_summary", "response", "result", "output")


def _numbers_in(value: Any, out: list[float]) -> None:
    """递归收集一个结构里的全部数值；字符串按数字字面量拆。

    这一步是「对账」的底座：只要工具返回值里带着这个数，就算有出处。
    不做启发式匹配（例如"17 等于 20 减 3"），因为那需要重放计算，
    属 P3 的进阶形态，不在最小实现里冒充已实现。
    """
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.append(float(value))
        return
    if isinstance(value, str):
        for m in _NUM_RE.findall(value):
            try:
                out.append(float(m))
            except ValueError:  # pragma: no cover - 正则已保证可解析
                continue
        return
    if isinstance(value, dict):
        for v in value.values():
            _numbers_in(v, out)
        return
    if isinstance(value, (list, tuple)):
        for v in value:
            _numbers_in(v, out)


def trace_numbers(trace: dict | None) -> list[float]:
    """从 trace 的工具调用里收集所有可见数值（只认返回值类字段）。"""
    out: list[float] = []
    if not isinstance(trace, dict):
        return out
    calls = trace.get("tool_calls")
    if not isinstance(calls, list):
        return out
    for call in calls:
        if not isinstance(call, dict):
            continue
        for field in SOURCE_FIELDS:
            if field in call:
                _numbers_in(call[field], out)
    return out


def _matches(value: float, pool: list[float], tolerance: float) -> bool:
    return any(abs(value - candidate) <= tolerance for candidate in pool)


def reconcile(report_values: dict[str, float], trace: dict | None,
              tolerance: float = DEFAULT_TOLERANCE) -> dict:
    """把报告里的数值逐个拿去过程记录里找出处。

    `report_values` 形如 `{"valid_count": 17, "total_amount": 1837.99}`——
    只传**答案性**数值，不要把中间量混进来（中间量通常没有返回值可对）。
    """
    if not isinstance(report_values, dict) or not report_values:
        raise ValueError("report_values 必须是非空映射")

    pool = trace_numbers(trace)
    checked = []
    for name, raw in report_values.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{name}: 待对账值必须是数值（收到 {type(raw).__name__}）")
        value = float(raw)
        checked.append({
            "field": name,
            "value": value,
            "found": _matches(value, pool, tolerance),
        })

    if not pool:
        status = "unverifiable"
    elif all(item["found"] for item in checked):
        status = "verified"
    else:
        status = "mismatch"

    return {
        "status": status,
        "tolerance": tolerance,
        "checked": checked,
        "trace_number_count": len(pool),
        # 只有 verified 才有资格谈"答案来自过程"；unverifiable 与 mismatch 都要由人复核，
        # 前者是记录缺失，后者是疑似编造——两者都不写进 error_patterns（决策 10）。
        "counts_as_evidence": status == "verified",
    }


def summarize(result: dict) -> str:
    """人类可读的一行摘要，供报告 notes / 日志使用。"""
    missing = [f"{i['field']}={i['value']}" for i in result["checked"] if not i["found"]]
    if result["status"] == "verified":
        return f"对账通过：{len(result['checked'])} 个数值均可在过程记录中找到出处"
    if result["status"] == "unverifiable":
        return "无法对账：过程记录里没有可用数值（数据不足，不是答错）"
    return "对账失败：找不到出处的数值 " + "、".join(missing) + "（疑似编造，交人复核）"


def _dump(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)


if __name__ == "__main__":  # pragma: no cover - 手工探查入口
    demo_trace = {
        "tool_calls": [
            {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10},
             "result_summary": {"orders": 10, "total": 20}},
            {"call_index": 2, "name": "write_report",
             "params": {"path": "/workspace/report.json"},
             "result_summary": "wrote 17 valid rows, amount 1837.99"},
        ]
    }
    print(_dump(reconcile({"valid_count": 17, "total_amount": 1837.99}, demo_trace)))
