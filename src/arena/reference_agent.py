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

任务 YAML 写的是「call_index 平台侧编号（1 起）」，但本模块**自己**按调用到达顺序 1..n
编号，平台侧只校验「连续、从 1 起」（`trace_pipeline._trace_audit_issues`）。

**这是一处已知弱点**：Agent 可以改写编号、也可以把一次失败的调用整个不写。
平台侧抓包已经落地在 `platform_capture.py`（B-03b ①）：把 Agent 指向那个代理，
账本由平台自己编，自报与账本逐条对账，不一致即 `mismatch`。
但要清楚边界——**平台采集是可选代理，默认路径仍是自报**：只有部署时强制 Agent 走代理，
"trace 不可伪造"这句话才成立。人已拍板的部署口径（2026-11-19）：**正式比赛与对外演示
必须强制走代理；日常练习保持可选**（自报照常，方便低成本 dogfooding）。
`scripts/capture_probe.py` 的第 4 段给出了反例：一份凭空编出来的"失败+重试"在评测器里
`schema_valid=True` 且 R3 `pass`，唯一能说破它的是账本。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # 作为包被 import
    from . import agent_trace, diagnosis_report as dr
except ImportError:  # 作为独立脚本运行
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import agent_trace  # type: ignore[no-redef]
    import diagnosis_report as dr  # type: ignore[no-redef]

REPORT_PATH = "/workspace/report.json"
AGENT_ID = "reference-agent-v1"

# 骨架（`variant_template_id`）→ 流程。round-86（B-05b）加了第二套：定位题。
# 2026-11-23 加第三套：交接题（先问人再动手）。
# 三套骨架共用同一个 Agent 进程与同一份 trace 契约，差别只在"要做什么"。
TEMPLATE_DEBUG = "tpl-tool-debug"
TEMPLATE_LOCATE = "tpl-bug-locate"
TEMPLATE_ASK = "tpl-ask-first"
KNOWN_TEMPLATES = (TEMPLATE_DEBUG, TEMPLATE_LOCATE, TEMPLATE_ASK)

# 根因取值域与判定规则来自任务 scenario 的**明文约定**，不是从代码里推断的：
#   异常类 TypeError                     ⇒ type_mismatch
#   异常类 IndexError                    ⇒ off_by_one
#   异常类 AttributeError 且消息含 NoneType ⇒ null_deref
#   其余                                 ⇒ other
# 参考 Agent 按这条规则算，不去读 `test_inputs.expected`（读了就等于抄答案，
# 那份夹具也就再也证明不了题目可解）。
ROOT_CAUSE_BY_EXCEPTION = {
    "TypeError": "type_mismatch",
    "IndexError": "off_by_one",
}
ROOT_CAUSE_OTHER = "other"

# 缺陷形态：用来证明「评测器区分得开」，不是产品功能。
# 每种形态都对应一个**能单独把某条断言打到 fail** 的真实行为：
#   correct       正确执行（对照组）
#   wrong_filter  不剔除无效行（汇报题）/ 把行号算错一位（定位题）⇒ 应只打掉 R2
#   extra_param   多传一个 schema 外参数 debug=True      ⇒ 应只打掉 R4
#   no_retry      遇 5xx 不重试                          ⇒ 应打掉 R3（并连带 R1/R5）
#   wrong_line    定位题专属：行号算错，别的都对          ⇒ 应只打掉 R2
#   no_ask        交接题专属：不问口径直接猜着干          ⇒ 应打掉 R6（口径错则连带 R2）
#   ask_late      交接题专属：先干完再补问一句             ⇒ 应只打掉 R6
# 各骨架各实现自己那几条；拿到不认识的形态一律 AgentError，不静默当 correct。
FLAWS = ("correct", "wrong_filter", "extra_param", "no_retry", "wrong_line",
         "no_ask", "ask_late")

# 骨架 → 它实现了哪些缺陷形态。一套骨架不能"假装实现了"另一套的形态。
_TEMPLATE_FLAWS = {
    TEMPLATE_DEBUG: ("correct", "wrong_filter", "extra_param", "no_retry"),
    TEMPLATE_LOCATE: ("correct", "extra_param", "no_retry", "wrong_line"),
    TEMPLATE_ASK: ("correct", "extra_param", "no_ask", "ask_late"),
}



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
        self._template = self._template_of(task)
        self._check_flaw_supported()
        if self._template == TEMPLATE_LOCATE:
            self._locate = self._read_locate_paths(task)
        else:
            self._fetch_tool()
            self._page_size()
            if self._template == TEMPLATE_ASK:
                self._ask_tool()

    def _check_flaw_supported(self) -> None:
        """缺陷形态与骨架必须配套，在构造期就拒。

        放到 `run()` 里再抛会被那条 `except Exception` 收走，变成一份 crashed 的 trace——
        看起来像"任务失败"，实际是我把形态接错了。构造期抛，测试才会红在正确的地方。
        """
        allowed = _TEMPLATE_FLAWS[self._template]
        if self.flaw not in allowed:
            raise AgentError(
                f"缺陷形态 flaw={self.flaw!r} 不属于骨架 {self._template}；可选 {allowed}"
            )

    # ── 骨架分派 ────────────────────────────────────────────────────────────

    @staticmethod
    def _template_of(task: dict) -> str:
        """按 `variant_template_id` 选流程。不认识的骨架直接拒跑。

        不默认走汇报流：那不是"兼容旧任务"，是让一道定位题被当成汇报题跑完，
        产出一份看着正常、其实答非所问的 trace。
        """
        tpl = task.get("variant_template_id")
        if tpl not in KNOWN_TEMPLATES:
            raise AgentError(
                f"任务的 variant_template_id={tpl!r} 未实现；已实现 {KNOWN_TEMPLATES}"
            )
        return tpl

    @staticmethod
    def _read_locate_paths(task: dict) -> dict[str, str]:
        """定位题的三个路径全部来自 YAML 的 `locate` 块，代码里不写死。

        写死会让「改任务 = 改 YAML」失效：变体换了日志路径，Agent 还去读母本那份，
        于是拿到的是旧日志——判出来"根因没错但行号不对"，看起来像学习者的问题。
        """
        locate = task.get("locate") or {}
        fields = ("log_path", "source_path", "diagnosis_path")
        missing = [f for f in fields
                   if not isinstance(locate.get(f), str) or not locate.get(f)]
        if missing:
            raise AgentError(f"定位任务缺 locate.{missing}，拒绝猜路径")
        files = ((task.get("test_inputs") or {}).get("files")) or {}
        unreadable = [f for f in ("log_path", "source_path") if locate[f] not in files]
        if unreadable:
            raise AgentError(
                f"locate 里的 {unreadable} 不在 test_inputs.files 里，读不到内容"
            )
        return {f: locate[f] for f in fields}


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
        """分页工具＝声明了 `mock.pagination` 的那个，**不依赖故障声明**。

        原来靠 `fault_injection.target_tool` 认，理由是"故障一定打在分页工具上"。
        交接题刻意不注入故障（只考"先问再动手"），那条前提就不成立了——
        一道没有故障的题会让 Agent 在构造期崩掉，看起来像"任务坏了"。
        按分页声明认更本质：谁会被分页调用，谁就是拉数工具。
        """
        for tool in self.task.get("tools") or []:
            if (tool.get("mock") or {}).get("pagination"):
                return str(tool["name"])
        raise AgentError("任务未声明任何带 mock.pagination 的分页工具，无法确定拉取工具")

    def _ask_tool(self) -> str:
        """`mock.source=static` 的那个工具就是"问人"的通道。

        按声明形态认，不按名字猜：变体把 `ask_policy` 改名成 `ask_ops` 也照跑。
        认不出来就拒跑——否则 Agent 会静默跳过"问"这一步，产出一份
        R6 必挂的 trace，看起来像"这个 Agent 不会澄清"。
        """
        for tool in self.task.get("tools") or []:
            if (tool.get("mock") or {}).get("source") == "static":
                return str(tool["name"])
        raise AgentError(
            "tpl-ask-first 的任务必须声明一个 mock.source=static 的工具（问人的通道）"
        )

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
        """把一次尝试写进 trace。

        字段拼装不在本模块：`agent_trace.trace_item()` 是唯一实现，LLM Agent 用的是同一个。
        两处各写一份的话，改一处漏一处会让 trace 静默地不符合契约，而后果是断言 fail——
        看起来像"这个 Agent 能力不行"，归因直接指错方向。
        """
        self._calls.append(
            agent_trace.trace_item(len(self._calls) + 1, name, params, status, body)
        )

    # ── 主流程 ──────────────────────────────────────────────────────────────

    def run(self) -> AgentRun:
        started = time.monotonic()
        notes: list[str] = []

        try:
            if self._template == TEMPLATE_LOCATE:
                answer = self._locate_root_cause(notes)
                self._write_report(answer, self._locate["diagnosis_path"])
            elif self._template == TEMPLATE_ASK:
                answer = self._ask_then_report(notes)
                self._write_report(answer, REPORT_PATH)
            else:
                orders = self._fetch_all_orders(notes)
                answer = _summarize_orders(orders, apply_filter=self.flaw != "wrong_filter")
                self._write_report(answer, REPORT_PATH)
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

    def _fetch_all_orders(self, notes: list[str], page_size: int | None = None) -> list[dict]:
        tool = self._fetch_tool()
        # page_size 由调用方覆盖时以调用方为准（交接题的大小是**问出来的**，
        # 与 `mock.pagination.page_size_max` 恰好相等只是题目的设定，不是规则）。
        page = page_size if page_size is not None else self._page_size()
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

    def _locate_root_cause(self, notes: list[str]) -> dict:
        """定位题：先读日志（会撞上注入的 5xx 并重试），再读源码，最后按规则给结论。

        **顺序不参与判定**：R3 是子序列匹配，先读源码再读日志同样能判出重试。
        先读日志的理由只有一条：故障落在日志上，先读它就能在"没重试"时立刻失败，
        不必先花一次成功调用。两处都必须读，那是 R2 的要求（日志给函数名、源码给行号）。
        """
        log_path, src_path = self._locate["log_path"], self._locate["source_path"]

        log_params: dict[str, Any] = {"path": log_path}
        if self.flaw == "extra_param":
            # schema 外参数：mock 会忽略它（服务端不替评测器判分），
            # 算不算幻觉由 R4 从 trace 判——这条形态正好验证那个分工。
            log_params["debug"] = True
        status, body = self._invoke("read_file", log_params)
        if status != 200:
            raise AgentError(f"read_file({log_path}) 最终失败：{status} {body}")

        src_status, src_body = self._invoke("read_file", {"path": src_path})
        if src_status != 200:
            raise AgentError(f"read_file({src_path}) 最终失败：{src_status} {src_body}")

        answer = _diagnose(str(body.get("content") or ""), str(src_body.get("content") or ""))
        if self.flaw == "wrong_line":
            answer["offending_line"] += 1
            notes.append("缺陷形态 wrong_line：行号多算一行，只该打掉 R2")
        notes.append(
            f"定位结论：{answer['root_cause']} @ 第 {answer['offending_line']} 行"
            f"（依据 {log_path} 最后一条 ERROR + {src_path} 函数体）"
        )
        return answer

    def _ask_then_report(self, notes: list[str]) -> dict:
        """交接题：先问口径，再按口径拉数与统计。

        三条形态各自打在不同断言上，这是本骨架的区分度所在：
          correct   问 → 拉 → 统计                ⇒ R6 pass、R2 pass
          no_ask    不问，按"只剔金额无效"猜着干    ⇒ R6 fail；把两条 cancelled 算进去，R2 也 fail
          ask_late  先按标准口径干完，最后补问一句   ⇒ 只 R6 fail（R2 pass）
        `ask_late` 用标准口径是**故意的**：它要证明"结果对但顺序错"能被单独扣分，
        否则就分不清"R6 是独立维度"还是"R6 只是 R2 的影子"。
        """
        ask_tool = self._ask_tool()
        question = {"question": "上周交接的三条口径是什么？"}

        if self.flaw == "ask_late":
            rows = self._fetch_all_orders(notes)
            answer = _summarize_orders(rows, drop_cancelled=True)
            status, _ = self._invoke(ask_tool, question)
            if status != 200:
                raise AgentError(f"{ask_tool} 最终失败：{status}")
            notes.append("缺陷形态 ask_late：已经干完了才问口径——只该打掉 R6")
            return answer

        if self.flaw == "no_ask":
            rows = self._fetch_all_orders(notes)
            answer = _summarize_orders(rows, drop_cancelled=False)
            notes.append(
                "缺陷形态 no_ask：不问口径，按『只剔金额无效』猜着干——R6 必挂，"
                "若把 cancelled 算进去 R2 也挂"
            )
            return answer

        status, body = self._invoke(ask_tool, question)
        if status != 200:
            raise AgentError(f"{ask_tool} 最终失败：{status} {body}")
        policy = str(body.get("answer") or "")
        page = _page_size_from_answer(policy)
        drop_cancelled = "cancelled" in policy
        notes.append(
            f"口径来自 {ask_tool}：一页 {page} 条、剔除 cancelled={drop_cancelled}"
            "（**不是**从 mock.pagination 读的——那等于不问题也知道答案）"
        )
        rows = self._fetch_all_orders(notes, page_size=page)
        return _summarize_orders(rows, drop_cancelled=drop_cancelled)

    def _write_report(self, answer: dict, path: str) -> None:
        content = json.dumps(answer, ensure_ascii=False, sort_keys=True)
        status, body = self._invoke("write_report", {"path": path, "content": content})
        if status != 200:
            raise AgentError(f"write_report 失败：{status} {body}")
        self._artifacts = {path: content}

    def _trace(self, final_status: str) -> dict:
        return {
            "agent_id": self.agent_id,
            "final_status": final_status,
            "tool_calls": self._calls,
        }


_ERROR_LINE = re.compile(r"\b(\w+Error)\s+in\s+(\w+):\s*(.+?)\s*$")
# 「一页最多 3 条」——中间允许空格，数字必须有。题目文案可以换说法，
# 但"页大小"这个数必须能被读出来，否则 Agent 只能猜。
_PAGE_FROM_POLICY = re.compile(r"一页最多\s*(\d+)\s*条")
_NOT_SUPPORTED = re.compile(r"'(<=|>=|==|!=|<|>)'?\s+not supported")
_NO_ATTRIBUTE = re.compile(r"has no attribute '(\w+)'")


def _diagnose(log_text: str, source_text: str) -> dict:
    """从「服务日志 + 源码」推出根因与出错行号。

    规则与任务 scenario 的明文约定一致，不是从代码里猜的：
      1. 取**最后一条** ERROR —— 服务用全局异常处理器把每次异常压成一行，最后一条对应本次失败；
         （更早的 ERROR 是别的已知问题，是干扰项，读日志的意义就在于分辨它们）
      2. 异常类 → 根因，取值域见 `ROOT_CAUSE_BY_EXCEPTION`；
      3. 从消息里取出"定位特征"：比较类看哪个运算符不被支持，空值类看少了哪个属性；
      4. 在该函数体内找**第一处**含该特征的行 —— 日志只给了函数名，行号只能从源码里找。

    日志里给了行号就没必要读源码了；正因为它只给函数名，两处证据缺一不可。
    """
    lines = [ln for ln in log_text.splitlines() if " ERROR " in ln]
    if not lines:
        raise AgentError("日志里没有任何 ERROR 行，无法定位")
    match = _ERROR_LINE.search(lines[-1])
    if not match:
        raise AgentError(f"最后一条 ERROR 行不符合约定格式：{lines[-1]!r}")
    error_class, func, message = match.group(1), match.group(2), match.group(3)

    if error_class == "AttributeError" and "NoneType" in message:
        root_cause = "null_deref"
    else:
        root_cause = ROOT_CAUSE_BY_EXCEPTION.get(error_class, ROOT_CAUSE_OTHER)

    op = _NOT_SUPPORTED.search(message)
    attr = _NO_ATTRIBUTE.search(message)
    if op:
        needle = op.group(1)
    elif attr:
        needle = "." + attr.group(1)
    else:
        raise AgentError(f"异常 {error_class} 的消息里没有可定位的特征：{message!r}")

    return {"root_cause": root_cause,
            "offending_line": _line_in_function(source_text, func, needle)}


def _line_in_function(source_text: str, func: str, needle: str) -> int:
    """在 `def <func>` 的函数体内找第一处含 needle 的行，返回 1 基行号。"""
    lines = source_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"def {func}("):
            start = i
            break
    if start is None:
        raise AgentError(f"源码里找不到函数 {func}()")
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line and not line[0].isspace():   # 回到列首 = 函数体结束
            break
        if needle in line:
            return i + 1
    raise AgentError(f"函数 {func}() 体内找不到含 {needle!r} 的行")


def _page_size_from_answer(policy: str) -> int:
    """从同事的答复里读"一页最多 N 条"。

    正则宽松匹配（中间可以有空格），但**匹配不到就拒跑**——猜一个默认值会让
    "问了但没读懂"看起来像"没问对"，而它其实是一次读不懂。
    """
    m = _PAGE_FROM_POLICY.search(policy)
    if not m:
        raise AgentError(f"同事的答复里没有『一页最多 N 条』这样的一句：{policy[:80]!r}")
    return int(m.group(1))


def _summarize_orders(rows: list[dict], *, apply_filter: bool = True,
                      drop_cancelled: bool = False) -> dict[str, Any]:
    """剔除无效行（amount 缺失 / null / 负数，可选再剔 status=cancelled），统计条数与合计。

    判定规则来自任务 scenario 第 2 条，不是从代码里"推断"出来的：
    「剔除无效订单：amount 字段缺失、为 null 或为负数」。

    `apply_filter=False` 是**故意做错**（缺陷形态 wrong_filter）：把每一行都算有效、
    缺失/非数值当 0。它必须在 R2 上失败——否则说明 R2 判不出"没剔除"。

    `drop_cancelled` 由交接题按**问到的口径**决定，默认 False 保持另两套骨架的语义不变。
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
    if drop_cancelled:
        valid = [r for r in valid if r.get("status") != "cancelled"]
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
