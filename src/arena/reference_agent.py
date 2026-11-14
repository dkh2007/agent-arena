"""B-03：参考 Agent —— 一个真的跑起来、真的调工具、真的产出 trace 的进程。

## 它解决什么问题

在此之前，`trace.tool_calls` 全部是**手写**的：R3（失败重试证据）能满分，
只是因为有人往 JSON 里抄了一行 `status: error`。没有任何东西真的失败过，
也没有任何东西真的重试过。于是"评测器能判出重试"这件事，从未被真实验证。

本模块把 scenario 的四步写成固定流程，让它真的：
  分页拉取 → 遇 500 真的重试 → 剔除无效行 → 真的调 write_report。
产出的 trace 因此来自**执行**，而不是抄写。

## 它不是 LLM（必须说清）

这是**确定性执行体**：步骤写死，不做规划，不调模型。所以它**不能**验证
"模型会不会自己想到要重试"——那是别的题目要测的东西。它能验证的是：

- 任务 YAML 声明的工具契约与故障注入，真的能驱动一个客户端走完；
- 这样产生的 trace 能不能直接喂进 `recompute_all()` 而不触发 `trace_invalid`；
- 手写夹具的结论与真实执行的结论是否一致（这是第一次有机会对撞）。

## 为什么 trace 的 `call_index` 由 Agent 编号

任务 YAML 写的是「call_index 平台侧编号（1 起）」，但当前架构是**Agent 自报 trace**
（平台不观测调用）。所以本模块按调用到达顺序 1..n 编号，平台侧只校验
「连续、从 1 起」（`trace_pipeline._trace_audit_issues`）。

**这是一处已知弱点**：Agent 可以改写编号把重试藏起来。彻底解决要平台侧抓包
（下方"未做"第 2 条），在那之前不能声称"trace 不可伪造"。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # 作为包被 import
    from . import diagnosis_report as dr
except ImportError:  # 作为独立脚本运行
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import diagnosis_report as dr  # type: ignore[no-redef]

REPORT_PATH = "/workspace/report.json"
AGENT_ID = "reference-agent-v1"

# 缺陷形态：用来证明「评测器区分得开」，不是产品功能。
# 每种形态都对应一个**能单独把某条断言打到 fail** 的真实行为：
#   correct       正确执行（对照组）
#   wrong_filter  不剔除无效行（amount 缺失/null/负数照算）⇒ 应只打掉 R2
#   extra_param   多传一个 schema 外参数 debug=True      ⇒ 应只打掉 R4
#   no_retry      遇 500 不重试                          ⇒ 应打掉 R3（并连带 R1/R5）
FLAWS = ("correct", "wrong_filter", "extra_param", "no_retry")


class AgentError(RuntimeError):
    """Agent 自己坏了（不是任务失败）：宁可崩，也不交一份看起来正常的假 trace。"""


@dataclass
class AgentRun:
    trace: dict
    artifacts: dict[str, str]
    answer: dict[str, Any]
    duration_ms: int
    notes: list[str] = field(default_factory=list)


class ToolClient:
    """对 mock 工具服务的薄客户端。每次调用都记耗时与结果，供 trace 使用。"""

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def call(self, name: str, params: dict) -> tuple[int, dict, int]:
        """返回 (http_status, body, elapsed_ms)。**不抛**：失败也是结果。"""
        payload = json.dumps(params, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/tools/{name}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
                return resp.status, body, int((time.monotonic() - started) * 1000)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {"error": "unparsable_error_body", "detail": raw[:200]}
            return exc.code, body, int((time.monotonic() - started) * 1000)
        except (urllib.error.URLError, TimeoutError) as exc:
            # 连不上/超时：交给上层决定算 timeout 还是 crashed，这里只如实上报
            return 0, {"error": "transport", "detail": str(exc)}, int(
                (time.monotonic() - started) * 1000
            )


class ReferenceAgent:
    """按 dbg-001 的四步走完，产出 (trace, artifacts, answer)。"""

    def __init__(self, task: dict, tools: ToolClient, *,
                 agent_id: str = AGENT_ID, retries: int = 1, flaw: str = "correct"):
        if flaw not in FLAWS:
            raise AgentError(f"flaw={flaw!r} 未实现；可选 {FLAWS}")
        self.task = task
        self.tools = tools
        self.agent_id = agent_id
        self.flaw = flaw
        # no_retry 是"缺陷形态"，必须压到 0，否则它就不是那件事了
        self.retries = 0 if flaw == "no_retry" else retries
        self._calls: list[dict] = []
        self._artifacts: dict[str, str] = {}
        self._deadline_s = self._invoke_timeout(task)
        # 配置错误在构造期就暴露：带着缺声明的任务跑到一半崩，会产出一份
        # "crashed" 的 trace，看起来像 Agent 的问题，实际是任务 YAML 的问题。
        self._fetch_tool()
        self._page_size()

    # ── 任务派生的参数（不硬编码） ──────────────────────────────────────────

    @staticmethod
    def _invoke_timeout(task: dict) -> float:
        execution = task.get("execution") or {}
        value = execution.get("invoke_timeout_seconds")
        if not isinstance(value, (int, float)) or value <= 0:
            raise AgentError("任务未声明 execution.invoke_timeout_seconds，拒绝猜一个超时")
        return float(value)

    def _page_size(self) -> int:
        """从 `tools[*].mock.pagination.page_size_max` 读，不写死 10。

        写死会让「改任务 = 改 YAML」这条约定失效：YAML 里降到 5，Agent 仍按 10 拉，
        于是每页都被服务端拒，看起来像"Agent 不会用工具"。
        """
        for tool in self.task.get("tools") or []:
            pag = (tool.get("mock") or {}).get("pagination")
            if pag and tool.get("name") == self._fetch_tool():
                return int(pag["page_size_max"])
        raise AgentError("任务未声明分页工具的 mock.pagination.page_size_max")

    def _fetch_tool(self) -> str:
        """被 `fault_injection.target_tool` 指到的那个工具就是分页工具。"""
        target = (self.task.get("fault_injection") or {}).get("target_tool")
        if not target:
            raise AgentError("任务未声明 fault_injection.target_tool，无法确定拉取工具")
        return target

    # ── 单个调用（含重试） ──────────────────────────────────────────────────

    def _invoke(self, name: str, params: dict) -> tuple[int, dict]:
        """调一次，5xx/传输失败按 retries 重试；每次尝试都进 trace。"""
        attempts = 0
        while True:
            status, body, elapsed = self.tools.call(name, params)
            self._record(name, params, status, body)

            if status == 0:
                # 传输层失败：是 timeout 还是 crashed 由上层按截止时间判，这里不重试——
                # 平台不替 Agent 重试（execution.platform_retry=0 的同一条理由：
                # 重试是本次被测的能力，代劳会让 R3 测不到）。
                raise AgentError(f"工具 {name} 传输失败：{body.get('detail')}")

            if 500 <= status < 600 and attempts < self.retries:
                attempts += 1
                continue
            return status, body

    def _record(self, name: str, params: dict, status: int, body: dict) -> None:
        """把一次尝试写进 trace。字段按 trace_requirements.minimum.item_fields 给满。"""
        item: dict[str, Any] = {
            "call_index": len(self._calls) + 1,
            "name": name,
            "params": params,
        }
        if status == 0:
            item.update(status="timeout", error=str(body.get("error")))
        elif 200 <= status < 300:
            item.update(status="success", result_summary=_summary(body))
        else:
            item.update(status="error", http_status=status,
                        error=str(body.get("error") or f"http_{status}"))
        self._calls.append(item)

    # ── 主流程 ──────────────────────────────────────────────────────────────

    def run(self) -> AgentRun:
        started = time.monotonic()
        notes: list[str] = []

        try:
            orders = self._fetch_all_orders(notes)
            answer = _summarize_orders(orders, apply_filter=self.flaw != "wrong_filter")
            self._write_report(answer)
        except Exception as exc:  # noqa: BLE001 - 任何意外都要如实落到 final_status
            # 不往上抛：平台要的是"这次跑挂了"这个事实，而不是一份缺失的提交。
            # trace 里已有的调用记录全部保留，R3/R5 据此判失败或 inconclusive。
            notes.append(f"执行中止：{type(exc).__name__}: {exc}")
            return AgentRun(
                trace=self._trace("crashed"),
                artifacts={},
                answer={},
                duration_ms=int((time.monotonic() - started) * 1000),
                notes=notes,
            )

        elapsed = time.monotonic() - started
        if elapsed > self._deadline_s:
            notes.append(
                f"耗时 {elapsed:.1f}s 超过 invoke_timeout_seconds={self._deadline_s:.0f}s："
                "按任务声明的 on_timeout 记 final_status=timeout"
            )
            return AgentRun(self._trace("timeout"), self._artifacts, answer,
                            int(elapsed * 1000), notes)

        return AgentRun(self._trace("completed"), self._artifacts, answer,
                        int(elapsed * 1000), notes)

    def _fetch_all_orders(self, notes: list[str]) -> list[dict]:
        tool = self._fetch_tool()
        page = self._page_size()
        offset = 0
        rows: list[dict] = []
        total: int | None = None

        while True:
            params = {"offset": offset, "limit": page}
            if self.flaw == "extra_param":
                # schema 外参数。mock 会**忽略**它（服务端不该替评测器判分），
                # 是否算幻觉由 R4 从 trace 判——这条形态正好验证那个分工。
                params["debug"] = True
            status, body = self._invoke(tool, params)
            if status != 200:
                raise AgentError(
                    f"{tool}(offset={offset}, limit={page}) 最终失败：{status} {body}"
                )
            page_rows = body.get("orders")
            if not isinstance(page_rows, list):
                raise AgentError(f"{tool} 返回的 orders 不是数组：{body!r}")
            total = body.get("total", total)
            rows.extend(page_rows)
            if not page_rows:
                break
            offset += page
            if total is not None and len(rows) >= int(total):
                break

        if total is not None and len(rows) != int(total):
            notes.append(
                f"拉取到 {len(rows)} 条，但服务端声明 total={total}："
                "数据不完整，后续统计基于已拉到的部分"
            )
        return rows

    def _write_report(self, answer: dict) -> None:
        content = json.dumps(answer, ensure_ascii=False, sort_keys=True)
        status, body = self._invoke("write_report", {"path": REPORT_PATH, "content": content})
        if status != 200:
            raise AgentError(f"write_report 失败：{status} {body}")
        self._artifacts = {REPORT_PATH: content}

    def _trace(self, final_status: str) -> dict:
        return {
            "agent_id": self.agent_id,
            "final_status": final_status,
            "tool_calls": self._calls,
        }


def _summary(body: dict) -> dict:
    """把工具返回压成可核对的小摘要：**只记形状与计数，不记原始行**。

    为什么不记 `orders` 全量：trace 会长到不可读，且金额出现在 trace 里会让
    「答案是否来自过程」变成看运气（凑巧有个数等于合计）。真正的出处核查
    属平台侧抓包（B-03b），不靠 Agent 自觉多写。
    """
    if "orders" in body and isinstance(body["orders"], list):
        return {"orders": len(body["orders"]), "total": body.get("total")}
    return {k: v for k, v in body.items() if k in ("written", "bytes")} or {"ok": True}


def _summarize_orders(rows: list[dict], *, apply_filter: bool = True) -> dict[str, Any]:
    """剔除无效行（amount 缺失 / null / 负数），统计条数与合计。

    判定规则来自任务 scenario 第 2 条，不是从代码里"推断"出来的：
    「剔除无效订单：amount 字段缺失、为 null 或为负数」。

    `apply_filter=False` 是**故意做错**（缺陷形态 wrong_filter）：把每一行都算有效、
    缺失/非数值当 0。它必须在 R2 上失败——否则说明 R2 判不出"没剔除"。
    """
    if not apply_filter:
        total = 0.0
        for r in rows:
            amount = r.get("amount") if isinstance(r, dict) else None
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                total += float(amount)
        return {"valid_count": len(rows), "total_amount": round(total, 2)}

    valid = [r for r in rows
             if isinstance(r, dict) and isinstance(r.get("amount"), (int, float))
             and not isinstance(r.get("amount"), bool) and r["amount"] >= 0]
    total = round(sum(float(r["amount"]) for r in valid), 2)
    return {"valid_count": len(valid), "total_amount": total}


def build_payload(run: AgentRun, task: dict, *, user_id: str,
                  submission_id: str | None = None,
                  evaluated_at: str | None = None) -> dict:
    """把一次执行翻成 `POST /submissions` 的请求体（外部视角：只交自己知道的）。"""
    payload: dict[str, Any] = {
        "task_id": task["task_id"],
        "task_version": task.get("task_version"),
        "user_id": user_id,
        "agent_id": AGENT_ID,
        "trace": run.trace,
        "artifacts": run.artifacts,
        "invoke": {
            "duration_ms": run.duration_ms,
            "timeout_seconds": int((task.get("execution") or {})
                                   .get("invoke_timeout_seconds", 0)),
            "platform_retries": int((task.get("execution") or {})
                                    .get("platform_retry", 0)),
        },
    }
    if submission_id:
        payload["submission_id"] = submission_id
    if evaluated_at:
        payload["evaluated_at"] = evaluated_at
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跑一次参考 Agent，打印产出的 trace")
    parser.add_argument("--task", default=str(
        Path(__file__).resolve().parents[2] / "tasks" / "dbg-001-order-report.yaml"))
    parser.add_argument("--tools", required=True, help="mock 工具服务基址，如 http://127.0.0.1:9001")
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--flaw", default="correct", choices=FLAWS,
                        help="故意做错的形态，用于验证评测器区分得开")
    args = parser.parse_args(argv)

    task = dr.load_task(args.task)
    agent = ReferenceAgent(task, ToolClient(args.tools), retries=args.retries,
                           flaw=args.flaw)
    run = agent.run()
    print(json.dumps({"trace": run.trace, "artifacts": run.artifacts,
                      "answer": run.answer, "notes": run.notes},
                     ensure_ascii=False, indent=2))
    return 0 if run.trace["final_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
