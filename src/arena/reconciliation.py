"""P3 / R6：产物与过程记录的对账（答案里的数值必须能从 trace 的工具返回值推出）。

round-70 定案（评审包决策 10）：防作弊**保对账**，随机化只作补充。理由是威胁模型里
最要命的是「不干活却报出正确答案」——它不仅骗分数，还骗走诊断（系统据此排错复习项）。
随机化挡的是「抄上次答案」，挡不住这一条。

本模块是 P3 的**最小可运行实现**，故意不接进 `tasks/dbg-001-order-report.yaml` 的 rubric。
round-108 实测后，这句"接入只是下一批的事"要改成更准确的版本——**它有两个前置，缺一条都接不进去**：

1. **没有判定器类型**。`assertion_evaluator.evaluate_assertion()` 的类型分派里没有
   `reconciliation`，它的 `SUPPORTED_TYPES` 也不含；直接写进 `rubric_credit` 会在判定时抛
   `NotImplementedError`。要接先得写 `evaluate_reconciliation` + 登记类型 + 结果形状。
2. **就算写好了，也没有任何提交能通过**。实测（round-108，五份夹具逐个跑）：

   | 夹具 | 状态 | 原因 |
   |---|---|---|
   | raw-empty / raw-no-file | — | 没有产物，无从对账 |
   | raw-no-trace | unverifiable | trace 里没有数值 |
   | raw-fake-retry | unverifiable | 推导值（9 / 861.24）逐字找不到出处 |
   | **raw-perfect** | **unverifiable** | 推导值（17 / 1837.99）逐字找不到出处 |

   `raw-perfect` 过不了是致命的：credit 分母**恒定**为权重之和（见 YAML 的 `denominator`），
   一条谁都过不了的断言不是检查，是给每份提交收 20% 的普遍税——满分从 1.0 掉到 0.8，
   上界直接没了。`tests/test_task_executability.py` 现在把"每个任务满分可达"钉死了，
   这类接法会在那里变红。

**为什么推导值找不到出处**：`_numbers_in()` 只能找**逐字出现**的数，而条数与合计是从多行
数据算出来的——工具返回值里根本没有 `1837.99` 这个数。要判它，只能**重放**
（从原始行重算 17 与 1837.99）。

**重放缺什么（round-109 修正，之前这一句写错了一半）**：原文写「缺 trace 里的原始行、缺推导规则」
——后半句对，前半句错。原始行**不必进 trace**：任务数据在 `tasks/*.yaml` 里，mock 是确定性的，
平台账本又记下了有序的 `(call_index, name, params)`，所以「当时返回了什么」可以事后重算。
`tests/test_replay_premise.py` 把这条钉住了（真跑一遍 → 用参数序列重放 → 逐条一致；改一条金额
返回体必须变；同一参数连调两次结果可以不同 ⇒ 要冻的是**有序序列**而不是单次请求）。

所以真正缺的是两样东西里的**一样**：

1. ~~原始行~~ 已有 —— 账本参数序列 + 任务数据（后者由提交里的 `task_git_hash` 锚定）；
2. **声明式推导规则** —— 真的没有：哪些行算「有效」、怎么聚合，现在只写在任务 `scenario`
   的自然语言里，代码里没有可执行版本。没有它，重放只能重算出「返回了什么」，算不出「答案该是什么」。

```text
trace 只记形状（result_summary: {"orders": 10, "total": 20}）
  ⇒ 逐字对账够用，聚合值不够用
  ⇒ 补的不是"把原始行塞进 trace"，是"一条能执行的推导规则"
```

trace 只记形状这件事本身是 round-70 定的（不让答案与过程同时出现在 Agent 可控的内容里），
**不是遗漏**，也不该为了对账而改：
把原始行放进 Agent 自报的记录里，「答案是否来自过程」就退化成「能不能凑出一个相等的数」，
诚实的和伪造的都能凑出来——那不是弱证据，是无效证据。

所以本模块的输出在接入之前只有三种用途：诊断线索、人工复核、以及给"重放"当参照物。
`counts_as_evidence` 只有 `verified` 才为真——这是它现在唯一能给出的确定结论。

三种结果（对应决策 10 的「验证不通过 ≠ 答错」）：

  verified     待查数值都能在过程记录里找到出处
  unverifiable 记录里根本没有可用数值（**数据不足，不是答错**：不计掌握度、不记短板）
  mismatch     记录里有数值，但某个待查值找不到出处（疑似编造，交人复核）

**聚合推导值（`derived_fields`，round-77 补）**：条数、合计这类值由多行数据算出来，
工具返回值里**逐字不可能出现**（没有哪个响应会回一个 `1837.99`）。
把它们当 `mismatch` 就是把诚实提交判成编造——round-77 实测：唯一任务的完美提交
就落在这个坑里（`valid_count=17` / `total_amount=1837.99` 都找不到出处）。
所以调用方用 `derived_fields` 声明哪些字段是推导值，这些字段缺出处时归 `unverifiable`
（`reason=derived_not_replayable`），**只有非推导字段缺出处才叫 mismatch**。
不传 `derived_fields` 时行为与旧版一致（默认不替调用方猜）。

调用关系：状态层在更新掌握度之前调用一次；它不是断言，不产生 error_patterns。
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

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
              tolerance: float = DEFAULT_TOLERANCE,
              derived_fields: Iterable[str] = ()) -> dict:
    """把报告里的数值逐个拿去过程记录里找出处。

    `report_values` 形如 `{"valid_count": 17, "total_amount": 1837.99}`——
    只传**答案性**数值，不要把中间量混进来（中间量通常没有返回值可对）。
    """
    if not isinstance(report_values, dict) or not report_values:
        raise ValueError("report_values 必须是非空映射")

    derived = set(derived_fields or ())
    stray = derived - set(report_values)
    if stray:
        raise ValueError(f"derived_fields 里有 report_values 未覆盖的字段：{sorted(stray)}")

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

    unfound = [i for i in checked if not i["found"]]
    if not pool:
        status, reason = "unverifiable", "no_numbers_in_trace"
    elif not unfound:
        status, reason = "verified", "all_traceable"
    elif all(i["field"] in derived for i in unfound):
        # 缺出处的**全是聚合推导值**（条数、合计）：工具返回值里逐字不可能出现，
        # 最小实现不做重放计算 ⇒ 属"数据不足"，**不是**编造。
        # 不这样分，诚实提交会被判成「疑似编造」（round-77 实测：完美夹具即此情形）。
        status, reason = "unverifiable", "derived_not_replayable"
    else:
        status, reason = "mismatch", "value_not_traceable"

    return {
        "status": status,
        "reason": reason,
        "tolerance": tolerance,
        "derived_fields": sorted(derived),
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
        if result.get("reason") == "derived_not_replayable":
            names = "、".join(i["field"] for i in result["checked"] if not i["found"])
            return (f"无法对账：{names} 属聚合推导值，过程记录里不会逐字出现 "
                    "（最小实现不做重放计算；数据不足，不是答错）")
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
