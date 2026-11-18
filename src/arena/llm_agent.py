"""B-03b ②：LLM 驱动的 Agent —— 决策来自模型，记录仍由本进程做。

## 它和参考 Agent 的分工

| | 决策来自 | 验证的是什么 |
|---|---|---|
| `reference_agent.py` | 写死的流程 | 任务 YAML 的工具契约能驱动一个客户端走完 |
| 本模块 | 模型每一步输出的 JSON | **模型自己**会不会想到翻页、想到重试、不瞎加参数 |

前者跑通只说明"题目可解"；"模型在不在这个能力上"必须由后者回答。

## 一条不能让步的设计：框架不替模型重试

任务 YAML 里 `execution.platform_retry: 0` 的理由是「重试是本次被测的能力，平台代劳会让
R3 测不到」。**同一条理由在 Agent 框架这一层同样成立**：如果 `LLMAgent` 在工具返回 5xx 时
自动重试，那么无论模型多差，trace 里都会出现"失败后成功"，R3 恒为 pass——一条不可能变红的绿。

所以 `_invoke_once()` **只发一次**，失败事实原样喂回模型，重不重试是模型的选择。
`tools/mutations/mutate_llm_agent.py` 的 M1 专门把这条改成自动重试，用来证明这条绿会变红。

## 协议为什么用"单个 JSON 决策"而不是 function calling

各家端点的 tool_call 支持与字段细节不一致（DeepSeek / OpenAI 兼容层 / 自建网关各有出入），
而 `llm_client` 只保证 chat/completions + `response_format=json_object` 这条路能通。
用一纸 JSON 协议，同一个 Agent 换端点不用改代码；代价是模型可能输出非 JSON——
这不是要修的 bug，是要**如实归因**的一类失败（计入 `bad_outputs`，不静默重写）。

## prompt 里绝不放的两类东西

1. **期望答案**（`test_inputs` 的 fixture / 合计值）——放了等于让它抄答案，
   这道题从此证明不了"能算出结果"。`tests/test_llm_agent.py` 有一条用例锁这个。
2. **"遇到失败请重试"这类指令**——那是把判据写进提示，R3 测的就不再是模型的判断。

## 可复现性

`meta` 里记 `prompt_version` / 服务端回报的 `model` / `temperature` / 步数。
"这个模型会不会自己重试"这类结论，不钉住模型版本与提示版本，下次跑不出来也没法归因。
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:  # 作为包被 import
    from . import agent_trace, diagnosis_report as dr
    from .llm_client import Completion, LLMClient, LLMConfig, LLMError
    from .reference_agent import ToolClient
except ImportError:  # 作为独立脚本/探针运行
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import agent_trace  # type: ignore[no-redef]
    import diagnosis_report as dr  # type: ignore[no-redef]
    from llm_client import Completion, LLMClient, LLMConfig, LLMError  # type: ignore[no-redef]
    from reference_agent import ToolClient  # type: ignore[no-redef]

PROMPT_VERSION = "llm-agent-v1"
LLM_AGENT_ID = "llm-agent-v1"

# 步数预算：一次提交最多让模型做多少个决策。12 对 dbg-001（4 步 + 1 次重试 + 1 次写报告）
# 留了三倍余量；设太小会把"模型啰嗦"错记成"能力不足"，设太大等于没有预算。
DEFAULT_MAX_STEPS = 12

# 观察结果进上下文时的字符上限。给模型看的必须是**完整返回体**（它要靠内容算数），
# 与写进 trace 的形状摘要（`agent_trace.summary`）是两件事——别把两者搞混。
DEFAULT_OBSERVATION_LIMIT = 4000

DECISION_CALL = "call_tool"
DECISION_FINISH = "finish"


class LLMAgentError(RuntimeError):
    """Agent 自己坏了（步数用尽 / 模型输出始终不合协议 / 接口层失败）。

    一律转成 `final_status=crashed` 的 trace，并把已发生的调用**全部保留**：
    把半截执行丢掉，等于让 R3/R5 看不到任何证据。
    """


class DecisionError(ValueError):
    """模型这一步的输出不是协议约定的一纸 JSON。"""


# ---------------------------------------------------------------------------
# 输出协议
# ---------------------------------------------------------------------------


def parse_decision(text: str) -> dict:
    """把模型这一步的输出解析成决策。不合协议就抛 `DecisionError`，不做猜测式修复。

    `` ```json `` 围栏是唯一容忍的偏差：这不是模型"理解错"，是训练数据里的书写习惯，
    为了它整轮重来太贵；除此之外（多段解释、缺字段、action 打错）一律当失败。
    """
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DecisionError(f"不是合法 JSON（{exc.msg}，位置 {exc.pos}）：{raw[:160]!r}") from exc
    if not isinstance(data, dict):
        raise DecisionError(f"顶层必须是对象，收到 {type(data).__name__}")
    action = data.get("action")
    if action not in (DECISION_CALL, DECISION_FINISH):
        raise DecisionError(f"action={action!r} 不认识；可选 {DECISION_CALL} / {DECISION_FINISH}")
    if action == DECISION_CALL:
        if not isinstance(data.get("tool"), str) or not data["tool"]:
            raise DecisionError("call_tool 缺 tool 名字")
        params = data.get("params", {})
        if not isinstance(params, dict):
            raise DecisionError("params 必须是对象")
    return data


# ---------------------------------------------------------------------------
# 工具声明 → 提示里的可见面
# ---------------------------------------------------------------------------


def tool_specs(task: dict) -> list[dict]:
    """从任务 YAML 取工具的**对外声明**（名字 / 说明 / 参数边界）。

    只取模型该知道的部分：`mock` 块的故障位、`pagination.failures`、fixture 一律不取。
    那些是考题，不是接口说明——把故障位置写进提示，等于告诉它"这一页会挂，直接重试"。
    """
    specs: list[dict] = []
    for tool in task.get("tools") or []:
        name = tool.get("name")
        if not name:
            raise LLMAgentError("任务里有工具没写 name，拒绝猜")
        params = []
        for p in tool.get("parameters") or []:
            item = {"name": p.get("name"), "type": p.get("type")}
            if p.get("required"):
                item["required"] = True
            for bound in ("minimum", "maximum", "enum"):
                if bound in p:
                    item[bound] = p[bound]
            params.append(item)
        specs.append({
            "name": name,
            "description": tool.get("description", ""),
            "parameters": params,
            "additional_properties_allowed": bool(tool.get("additional_properties", False)),
        })
    if not specs:
        raise LLMAgentError("任务没有声明任何工具，无法构造提示")
    return specs


SYSTEM_PROMPT = """你是一个任务执行 Agent。你只能通过下列工具与外部交互。

可用工具（JSON）：
{tools}

每一步你必须只输出**一个** JSON 对象，不要解释、不要 markdown：
  调用工具：{{"action":"call_tool","tool":"<工具名>","params":{{...}}}}
  结束任务：{{"action":"finish","answer":{{...}}}}

规则：
- 工具调用的失败（HTTP 4xx/5xx）原样返回给你。要不要再试一次、要不要换个参数，
  由你根据返回内容判断；框架不会替你重试，也不会替你改参数。
- 工具的参数必须落在上面声明的边界内，不许出现未声明的参数名。
- 任务要求交付文件时，必须自己调用写文件的工具；框架不会代写。"""


def build_messages(task: dict) -> list[dict[str, str]]:
    """system（工具与协议）+ user（情景原文）。情景直接用 YAML 的 `scenario`，不改写。

    改写会引入两份真相源：YAML 改了情景、提示里还是旧的，模型照着旧要求做，
    判出来却是"答非所问"。原文照搬，题目改成什么样，Agent 就跑什么样。
    """
    scenario = task.get("scenario")
    if not isinstance(scenario, str) or not scenario.strip():
        raise LLMAgentError("任务没有 scenario，无法构造提示")
    tools = json.dumps(tool_specs(task), ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(tools=tools)},
        {"role": "user", "content": scenario.strip()},
    ]


# ---------------------------------------------------------------------------
# 一次执行
# ---------------------------------------------------------------------------


@dataclass
class LLMAgentRun:
    trace: dict
    artifacts: dict[str, str]
    answer: dict[str, Any]
    duration_ms: int
    notes: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


class LLMAgent:
    """模型决策 + 本进程记录。工具调用**每次只发一遍**，重试由模型决定。"""

    def __init__(
        self,
        task: dict,
        tools: ToolClient,
        client: LLMClient,
        *,
        agent_id: str = LLM_AGENT_ID,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_bad_outputs: int = 2,
        observation_limit: int = DEFAULT_OBSERVATION_LIMIT,
    ) -> None:
        self.task = task
        self.tools = tools
        self.client = client
        self.agent_id = agent_id
        self.max_steps = max_steps
        self.max_bad_outputs = max_bad_outputs
        self.observation_limit = observation_limit
        self._tool_names = {t["name"] for t in tool_specs(task)}
        self._calls: list[dict] = []
        self._artifacts: dict[str, str] = {}
        self._messages = build_messages(task)
        self._deadline_s = _invoke_timeout(task)

    # ── 记录 ────────────────────────────────────────────────────────────────

    def _invoke_once(self, name: str, params: dict) -> tuple[int, dict]:
        """发一次，**不重试**。理由见模块文档第一段。"""
        status, body, _elapsed = self.tools.call(name, params)
        self._calls.append(
            agent_trace.trace_item(len(self._calls) + 1, name, params, status, body)
        )
        if status == 0:
            raise LLMAgentError(
                f"工具 {name} 传输失败：{body.get('detail') or body.get('error')}"
            )
        if name == "write_report" and 200 <= status < 300:
            # 产物从**实际成功的调用**里取，不从模型的自述里取：它说"我写了"不算，
            # 工具返回成功才算。这是 R1 唯一能站住的口径。
            path, content = params.get("path"), params.get("content")
            if isinstance(path, str) and isinstance(content, str):
                self._artifacts[path] = content
        return status, body

    def _observe(self, name: str, status: int, body: dict) -> None:
        text = json.dumps(body, ensure_ascii=False)
        if len(text) > self.observation_limit:
            text = text[: self.observation_limit] + f"…（已截断，原长 {len(text)} 字符）"
        self._messages.append({
            "role": "user",
            "content": f"[工具结果] {name} → HTTP {status}\n{text}",
        })

    # ── 主循环 ──────────────────────────────────────────────────────────────

    def run(self) -> LLMAgentRun:
        started = time.monotonic()
        notes: list[str] = []
        meta: dict[str, Any] = {
            "prompt_version": PROMPT_VERSION,
            "agent_id": self.agent_id,
            "model": "",
            "temperature": 0.0,
            "steps": 0,
            "llm_calls": 0,
            "bad_outputs": 0,
            "rejected_actions": [],
        }

        try:
            answer = self._loop(notes, meta)
        except Exception as exc:  # noqa: BLE001 - 任何意外都要如实落到 final_status
            notes.append(f"执行中止：{type(exc).__name__}: {exc}")
            return LLMAgentRun(
                trace=self._trace("crashed"), artifacts={}, answer={},
                duration_ms=int((time.monotonic() - started) * 1000),
                notes=notes, meta=meta,
            )

        elapsed = time.monotonic() - started
        if elapsed > self._deadline_s:
            notes.append(
                f"耗时 {elapsed:.1f}s 超过 invoke_timeout_seconds={self._deadline_s:.0f}s："
                "按任务声明的 on_timeout 记 final_status=timeout"
            )
            return LLMAgentRun(self._trace("timeout"), self._artifacts, answer,
                               int(elapsed * 1000), notes, meta)
        return LLMAgentRun(self._trace("completed"), self._artifacts, answer,
                           int(elapsed * 1000), notes, meta)

    def _loop(self, notes: list[str], meta: dict[str, Any]) -> dict:
        bad = 0
        for step in range(1, self.max_steps + 1):
            meta["steps"] = step
            completion = self._complete(meta)
            try:
                decision = parse_decision(completion.text)
            except DecisionError as exc:
                bad += 1
                meta["bad_outputs"] = bad
                notes.append(f"第 {step} 步输出不合协议（{bad}/{self.max_bad_outputs}）：{exc}")
                if bad > self.max_bad_outputs:
                    raise LLMAgentError(
                        f"连续 {bad} 次输出不合协议，放弃：{exc}"
                    ) from exc
                # 只说"不合协议、照协议重发"，不把正确决策喂回去——喂答案就不是它自己在做了。
                self._messages.append({
                    "role": "user",
                    "content": "你的上一条输出无法解析。只输出一个协议约定的 JSON 对象，不要任何解释。",
                })
                continue

            if decision["action"] == DECISION_FINISH:
                notes.append(f"第 {step} 步模型选择结束")
                return decision.get("answer") or {}

            name = decision["tool"]
            params = decision.get("params") or {}
            if name not in self._tool_names:
                # 不存在的工具：**不进 trace**。trace 记的是真实发生过的调用，凭空记一条
                # 会让平台账本对不上（账本里没有这次），把"模型幻觉"错报成"Agent 篡改"。
                meta["rejected_actions"].append({"step": step, "tool": name, "reason": "未知工具"})
                notes.append(f"第 {step} 步要求调用未声明的工具 {name!r}，已拒绝")
                self._messages.append({
                    "role": "user",
                    "content": f"工具 {name!r} 不存在。可用工具：{sorted(self._tool_names)}",
                })
                continue

            status, body = self._invoke_once(name, params)
            notes.append(f"第 {step} 步 {name} → HTTP {status}")
            self._observe(name, status, body)

        raise LLMAgentError(
            f"{self.max_steps} 步预算用尽仍未结束（final_status 记 crashed："
            "trace 的枚举里没有 gave_up，而 R5 只认 completed）"
        )

    def _complete(self, meta: dict[str, Any]) -> Completion:
        completion = self.client.complete(self._messages, response_format_json=True)
        meta["llm_calls"] += 1
        meta["model"] = completion.model or self.client.config.model
        meta["last_finish_reason"] = completion.finish_reason
        self._messages.append({"role": "assistant", "content": completion.text})
        return completion

    def _trace(self, final_status: str) -> dict:
        return {
            "agent_id": self.agent_id,
            "final_status": final_status,
            "tool_calls": self._calls,
        }


def _invoke_timeout(task: dict) -> float:
    execution = task.get("execution") or {}
    value = execution.get("invoke_timeout_seconds")
    if not isinstance(value, (int, float)) or value <= 0:
        raise LLMAgentError("任务未声明 execution.invoke_timeout_seconds，拒绝猜一个超时")
    return float(value)


# ---------------------------------------------------------------------------
# 离线驱动：脚本模型
# ---------------------------------------------------------------------------


class ScriptedModel:
    """按剧本吐决策的假模型 —— 测试与探针在不联网、不花钱的前提下跑通全链路。

    它**不能**替代真模型，判据也只有一条：给定同一串决策，`LLMAgent` 产出的 trace
    必须逐字节相同。真模型跑不出这个性质，这正是需要它的地方——链路（提示构造、
    协议解析、观察回填、记录、产物提取）的任何一处坏掉，都要在烧配额之前暴露。

    剧本元素三种：
      - dict / str：一步决策（dict 会被序列化；str 可用来喂坏 JSON）
      - int：该步返回此 HTTP 状态码（喂接口层失败）
      - callable：完全自定义，接收请求体、返回 `(status, data)`
    剧本用尽即抛 `LLMError`——**不**自动补一个"结束"：那会把写漏的剧本伪装成正常收尾。
    """

    def __init__(self, script: list, *, model: str = "scripted-agent-model",
                 sleep: Callable[[float], None] | None = None) -> None:
        self.script = list(script)
        self.model = model
        self.sleep = sleep or (lambda _s: None)
        self.requests: list[dict] = []
        self._client: LLMClient | None = None

    def client(self) -> LLMClient:
        if self._client is None:
            config = LLMConfig(api_key="scripted-not-a-real-key",
                               base_url="http://scripted.invalid", model=self.model)
            self._client = LLMClient(config, transport=self._transport, sleep=self.sleep)
        return self._client

    def prompts(self) -> list[str]:
        """所有发出过的消息摊平成文本，供"提示里有没有泄漏答案"这类检查用。"""
        out: list[str] = []
        for req in self.requests:
            for msg in req.get("messages") or []:
                out.append(str(msg.get("content", "")))
        return out

    def _transport(self, url: str, headers: dict, body: bytes | None,
                   timeout_s: int) -> tuple[int, Any]:
        payload = json.loads(body or b"{}")
        self.requests.append(payload)
        index = len(self.requests) - 1
        if index >= len(self.script):
            raise LLMError(
                f"剧本只有 {len(self.script)} 步，第 {index + 1} 步没有内容；"
                "多半是 Agent 多要了一轮（检查是否把 finish 判错了）"
            )
        item = self.script[index]
        if callable(item):
            return item(payload)
        if isinstance(item, int):
            return item, {"error": {"message": f"scripted http {item}"}}
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return 200, {
            "model": self.model,
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
