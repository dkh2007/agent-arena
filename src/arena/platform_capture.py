"""B-03b ①：平台侧采集——把「Agent 自报 trace」换成「平台观测 + 逐条对账」。

## 它解决什么问题

在它之前，trace 完全由 Agent 自己写：`call_index` 由 Agent 从 1 起编号，失败调用可以由
Agent 直接不写。于是「我重试了」这句话是**无法证伪**的——R3（失败重试证据）能满分，
可能只是因为 Agent 想拿满分。`reference_agent.py` 的模块文档把这条列为已知弱点第 1 条。

本模块做两件事：

1. **代理**：工具服务前面站一层，Agent 只能拿到平台转发回来的结果。平台按调用到达顺序
   自己编 `call_index`、自己记 `params / status / http_status`。
2. **对账**：把平台记录与 Agent 自报的 trace 按序号逐条比，任一处不符即 `mismatch`，
   并在文案里点名是第几条、哪个字段、两边各是什么。

## 两条必须守住的顺序（都是"错了就白干"的那种）

- **先记录，再返回**。反过来（先返回给 Agent 再记录）就会出现「Agent 已经拿到结果、
  记录还没落」的窗口，进程此刻被杀 ⇒ 平台账本缺一条，而这次调用真实发生过。
- **记录失败就不返回**（fail closed）。记账抛异常时请求也随之失败，宁可让这次提交报废，
  也不能出现「平台没记到、Agent 当没发生」的空白。代价说清楚：`write_report` 之类的
  副作用可能已经发生——所以记账只做纯内存追加，不做 IO。

## 它**不**做的事

- 不判断对错。命中失败是业务结果；R3 判不判满分是断言层的事（与 mock 同一条边界：
  一旦这一层开始判分，它就成了第二个真相源）。
- 不替 Agent 重试（`execution.platform_retry=0`）。
- 不解决"Agent 干脆不给你它的 trace"——那是提交接口要不要强制的口径问题，
  不在采集层。

## 与任务 YAML 的关系

`trace_requirements.minimum.tool_calls.note` 写着「`call_index` 平台侧编号（1 起）」。本模块
就是那句话的执行者：此前它是空声明（编号其实是 Agent 编的），现在平台真的在编号。
`http_status` 只在失败调用上比（与 B-22 结论一致：它仅对 `status=error` 必填）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Iterable

# 对账要比的字段。加字段要同时改这条与 `_compare_one()`，否则"看起来在比"。
COMPARED_FIELDS = ("name", "params", "status", "http_status")

# 平台记录里不该被 Agent 看见的东西（它对账时才会被读到）。
PRIVATE_ENDPOINT = "/__captured"


def _status_of(http_status: int) -> str:
    """与 `reference_agent._record()` 的分支保持一致：2xx 成功，其余一律 error。"""
    return "success" if 200 <= int(http_status) < 300 else "error"


@dataclass(frozen=True)
class CapturedCall:
    """平台亲眼看到的一次调用。字段刻意与 trace 条目同名，方便直接比。"""

    call_index: int
    name: str
    params: dict
    status: str
    http_status: int | None
    error: str | None

    def as_trace_item(self) -> dict:
        """转成与 `trace.tool_calls[]` 同形的条目（只含平台能证实的字段）。"""
        item: dict[str, Any] = {
            "call_index": self.call_index,
            "name": self.name,
            "params": dict(self.params),
            "status": self.status,
        }
        if self.http_status is not None:
            item["http_status"] = self.http_status
        if self.error is not None:
            item["error"] = self.error
        return item


class CaptureRecorder:
    """线程安全的调用账本。`reset()` 的语义是「换一次提交」，与 mock 的 `per_submission` 对齐。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: list[CapturedCall] = []

    def reset(self) -> None:
        with self._lock:
            self._calls = []

    def record(self, name: str, params: dict, http_status: int,
               body: dict | None = None) -> CapturedCall:
        """记一次调用。**纯内存追加**：这里抛异常等于让请求失败（故意 fail closed）。"""
        body = body or {}
        err = body.get("error") if isinstance(body, dict) else None
        with self._lock:
            call = CapturedCall(
                call_index=len(self._calls) + 1,
                name=name,
                params=dict(params) if isinstance(params, dict) else {},
                status=_status_of(http_status),
                http_status=int(http_status),
                error=str(err) if err is not None else None,
            )
            self._calls.append(call)
            return call

    def calls(self) -> list[CapturedCall]:
        with self._lock:
            return list(self._calls)

    def trace_items(self) -> list[dict]:
        return [c.as_trace_item() for c in self.calls()]

    def __len__(self) -> int:
        with self._lock:
            return len(self._calls)


@dataclass(frozen=True)
class TraceComparison:
    """对账结果。`mismatches` 为空才算一致（不设"差不多一致"这种档）。"""

    mismatches: tuple[str, ...]
    compared: int

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def verdict(self) -> str:
        return "match" if self.ok else "mismatch"


def _fmt_call(call: dict) -> str:
    name = call.get("name")
    status = call.get("status")
    hs = call.get("http_status")
    tail = f" http_status={hs}" if hs is not None else ""
    return f"{name} status={status}{tail}"


def _params_diff(captured: dict, reported: dict) -> str:
    """把参数差异说成"缺哪个 / 多哪个 / 哪个值不同"，而不是整段 JSON 对照。"""
    parts: list[str] = []
    c_keys, r_keys = set(captured or {}), set(reported or {})
    missing = sorted(c_keys - r_keys)
    extra = sorted(r_keys - c_keys)
    changed = sorted(k for k in c_keys & r_keys if captured[k] != reported[k])
    if missing:
        parts.append(f"自报缺 {missing}")
    if extra:
        parts.append(f"自报多 {extra}")
    if changed:
        parts.append("值不同 " + "；".join(
            f"{k}: 平台 {captured[k]!r} / 自报 {reported[k]!r}" for k in changed
        ))
    return "，".join(parts)


def _compare_one(idx: int, cap: dict, rep: dict) -> list[str]:
    """比一条。返回值全部是可直接贴进结论的句子（点名第几条 + 两边各是什么）。"""
    out: list[str] = []
    for field in ("name", "status"):
        if cap.get(field) != rep.get(field):
            out.append(
                f"call_index={idx} {field} 不符：平台 {cap.get(field)!r} / 自报 {rep.get(field)!r}"
            )
    if cap.get("params") != rep.get("params"):
        diff = _params_diff(cap.get("params") or {}, rep.get("params") or {})
        out.append(f"call_index={idx} 参数不符：{diff or '形状不同（不是对象）'}")
    # http_status 只对失败调用必填（B-22：`error/result_summary` 不做条件必填，http_status 是例外）。
    cap_hs, rep_hs = cap.get("http_status"), rep.get("http_status")
    if rep_hs is None:
        if cap.get("status") == "error":
            out.append(
                f"call_index={idx} 自报缺 http_status（平台记录 status=error，"
                f"http_status={cap_hs}）"
            )
    elif cap_hs is not None and rep_hs != cap_hs:
        out.append(
            f"call_index={idx} http_status 不符：平台 {cap_hs} / 自报 {rep_hs}"
        )
    return out


def compare_traces(reported: Iterable[dict], captured: Iterable[dict]) -> TraceComparison:
    """平台记录 vs Agent 自报。**按 `call_index` 对齐，不是按数组次序**。

    按次序对齐会掩盖最要紧的那种篡改：Agent 删掉失败的调用后，后面每条都往前挪一位，
    按次序比全都是"参数不符"，看着像记错；按编号比才说得清"第 2 条整个没了"。
    """
    cap_list = [c for c in captured if isinstance(c, dict)]
    rep_list = [r for r in reported if isinstance(r, dict)]

    def index_of(items: list[dict]) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for item in items:
            out[int(item.get("call_index") or 0)] = item
        return out

    cap, rep = index_of(cap_list), index_of(rep_list)
    problems: list[str] = []

    dup_cap = len(cap) != len(cap_list)
    dup_rep = len(rep) != len(rep_list)
    if dup_cap:
        problems.append(f"平台记录里 call_index 有重复（{len(cap_list)} 条 / {len(cap)} 个编号）")
    if dup_rep:
        problems.append(f"自报 trace 里 call_index 有重复（{len(rep_list)} 条 / {len(rep)} 个编号）")

    if len(cap_list) != len(rep_list):
        problems.append(
            f"条数不符：平台记录 {len(cap_list)} 条，自报 {len(rep_list)} 条"
            "（差一条通常意味着有一次调用被漏写或多写）"
        )

    for idx in sorted(set(cap) | set(rep)):
        if idx not in rep:
            problems.append(
                f"call_index={idx} 平台有、自报缺（平台：{_fmt_call(cap[idx])}）"
                "——这次调用真实发生过，不能从 trace 里消失"
            )
            continue
        if idx not in cap:
            problems.append(
                f"call_index={idx} 自报有、平台没记录（自报：{_fmt_call(rep[idx])}）"
                "——平台没收到过的调用不算证据"
            )
            continue
        problems.extend(_compare_one(idx, cap[idx], rep[idx]))

    return TraceComparison(mismatches=tuple(problems), compared=len(cap))


def capture_proxy(task: dict, recorder: CaptureRecorder, **server_kwargs):
    """起一个"记录 + 转发"的工具服务。

    复用 `mock_tools.make_server` 的 HTTP 层与语义（只有一份实现），这里只挂一个
    `on_call` 钩子把观测写进账本。返回 `(server, service)`，与 `make_server` 同形。

    部署提醒：`PRIVATE_ENDPOINT` 是给对账方读的，真实环境里不能暴露给被测 Agent
    ——它要是能读到账本，就等于把答案也给了它。本仓的 `make_server` 不挂这个端点，
    所以这里也**不挂**：账本由进程内的 `recorder` 直接读，少一条越权路径。
    """
    from . import mock_tools

    return mock_tools.make_server(task, on_call=recorder.record, **server_kwargs)
