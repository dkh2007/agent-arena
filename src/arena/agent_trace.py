"""trace 记录的唯一实现 —— 参考 Agent（确定性）与 LLM Agent（模型驱动）共用。

## 为什么值得单独一个模块

`trace.tool_calls[*]` 的字段名是**跨模块契约**，至少四处消费它：

| 消费方 | 依赖 |
|---|---|
| `trace_pipeline._trace_audit_issues` | `call_index` 连续 1..n、`status` 在枚举内 |
| R3（失败重试证据） | `status=error` 那一项 + `http_status` |
| R4（参数幻觉） | `params` 的键集 |
| `platform_capture.compare_traces` | `name / params / status / http_status` 逐项对账 |

两个 Agent 各写一份记录函数，某天只改一处（比如给错误项加个字段、把 `timeout` 的
写法改掉），另一处产出的 trace 就会**静默地**不符合契约。而"不符合契约"在评测器里
表现为断言 fail，看起来像"这个 Agent 能力不行"——归因直接指错方向。

所以记录只有这一处，两边都调它。

## 一条刻意不做的检查

这里**不校验** status 与 http_status 的条件关系（B-22 / TD-026 的结论）：判定链不读
`error` / `result_summary`，把"没写说明"记成结构缺失会让 `unscored` 语义被稀释。
记录层只做一件事——把这一次尝试**如实**写下来。
"""

from __future__ import annotations

from typing import Any


def summary(body: dict) -> dict:
    """把工具返回压成可核对的小摘要：**只记形状与计数，不记原始行**。

    为什么不记 `orders` 全量：trace 会长到不可读，且金额出现在 trace 里会让
    「答案是否来自过程」变成看运气（凑巧有个数等于合计）。真正的出处核查
    属平台侧抓包（`platform_capture.py`），不靠 Agent 自觉多写。

    注意这个压缩只作用于 **trace**。给模型看的观察结果必须是完整返回体——
    两者是不同的东西：trace 记形状，模型上下文要内容，否则它没法算数。
    """
    if "orders" in body and isinstance(body["orders"], list):
        return {"orders": len(body["orders"]), "total": body.get("total")}
    # 定位骨架的 read_file：记路径与规模即可，内容不进 trace（同上：不能让"答案"
    # 与"过程"在 trace 里同时出现，否则出处核查就退化成看运气）。
    keep = ("path", "written", "lines", "bytes")
    return {k: v for k, v in body.items() if k in keep} or {"ok": True}


def trace_item(index: int, name: str, params: dict, status: int, body: dict) -> dict:
    """一次工具尝试 → 一条 trace 记录。字段按 `item_fields` 给满。

    `status` 是 HTTP 状态码（0 表示传输层失败）。映射到 trace 的三值枚举：

    | HTTP | trace.status | 附带字段 |
    |---|---|---|
    | 0（连不上/超时） | `timeout` | `error` |
    | 2xx | `success` | `result_summary` |
    | 其余 | `error` | `http_status` + `error` |

    编号由调用方给（`index`），不在这里数数组长度：LLM Agent 的编号必须与
    平台账本对齐，让记录函数自己数会让"重排编号"这种篡改变得容易。
    """
    item: dict[str, Any] = {
        "call_index": index,
        "name": name,
        "params": params,
    }
    if status == 0:
        item.update(status="timeout", error=str(body.get("error")))
    elif 200 <= status < 300:
        item.update(status="success", result_summary=summary(body))
    else:
        item.update(status="error", http_status=status,
                    error=str(body.get("error") or f"http_{status}"))
    return item
