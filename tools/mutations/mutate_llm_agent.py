"""B-03b ②「LLM 驱动 Agent」的变异检验（round-107）。

判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）：
DETECTED / MISSED / INVALID。

## 这一层的失效方式，为什么比别处更危险

这一层坏掉的时候，**系统看起来更聪明**：模型不重试也有重试证据、不会写文件也交了产物、
乱答也能正常收尾。没有人会来查一个"表现不错"的 Agent，所以只能靠变异把它逼红。

- M1 框架替模型重试 ⇒ R3 对任何模型恒为 pass（与 `platform_retry: 0` 同一件事往上挪一层）；
- M2 提示里带上 fixture ⇒ 这道题从此证明不了"能算出结果"；
- M3 未声明的工具也记进 trace ⇒ 平台账本里没有这次调用，对账会把"幻觉"错报成"篡改"，
  而且受冤的是提交方；
- M4 模型说写了就算写了 ⇒ R1「交了产物」变成自述，产物与过程脱钩；
- M5 接口/协议失败记成 completed ⇒ R5「正常退出」不再可信，失败全部伪装成完成；
- M6 纠正消息里递一个正确决策 ⇒ `bad_outputs` 那层测的就不再是模型，而是提示里的答案。

跑法：  python tools/mutations/mutate_llm_agent.py
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

from _runner import run_mutations  # 同目录；判定只有一处实现

A = Path("src/arena/llm_agent.py")
T = "tests/test_llm_agent.py"

MUTATIONS = [
    ("M1 框架替模型重试（R3 从此恒为 pass）",
     A,
     '''        status, body, _elapsed = self.tools.call(name, params)
        self._calls.append(
            agent_trace.trace_item(len(self._calls) + 1, name, params, status, body)
        )''',
     '''        for _ in range(3):  # 变异：框架替模型重试
            status, body, _elapsed = self.tools.call(name, params)
            self._calls.append(
                agent_trace.trace_item(len(self._calls) + 1, name, params, status, body)
            )
            if not 500 <= status < 600:
                break''',
     f"{T}::test_harness_issues_exactly_one_call_per_decision"),

    ("M2 提示里带上 test_inputs（答案可抄）",
     A,
     "    tools = json.dumps(tool_specs(task), ensure_ascii=False, indent=2)",
     "    tools = (json.dumps(tool_specs(task), ensure_ascii=False, indent=2)\n"
     "             + json.dumps(task.get('test_inputs') or {}, ensure_ascii=False))",
     f"{T}::test_prompt_does_not_carry_the_expected_answer"),

    ("M3 未声明的工具也照发并记进 trace",
     A,
     "            if name not in self._tool_names:",
     "            if False:  # 变异：不校验工具名",
     f"{T}::test_unknown_tool_is_refused_and_never_enters_the_trace"),

    ("M4 finish 时把 answer 直接当产物（模型说写了就算）",
     A,
     '''                notes.append(f"第 {step} 步模型选择结束")
                return decision.get("answer") or {}''',
     '''                notes.append(f"第 {step} 步模型选择结束")
                self._artifacts["/workspace/report.json"] = json.dumps(
                    decision.get("answer") or {}, ensure_ascii=False)
                return decision.get("answer") or {}''',
     f"{T}::test_finishing_without_writing_leaves_no_artifact"),

    ("M5 执行中止记成 completed（失败伪装成正常收尾）",
     A,
     '''            return LLMAgentRun(
                trace=self._trace("crashed"), artifacts={}, answer={},''',
     '''            return LLMAgentRun(
                trace=self._trace("completed"), artifacts={}, answer={},''',
     f"{T}::test_interface_failure_becomes_crashed_not_completed"),

    ("M6 纠正消息里递一个正确决策（不是在测模型了）",
     A,
     '''                    "content": "你的上一条输出无法解析。只输出一个协议约定的 JSON 对象，不要任何解释。",''',
     '''                    "content": "上一条无法解析。照这个重发：{action: call_tool, tool: fetch_orders}",''',
     f"{T}::test_garbage_output_gets_one_corrective_turn_then_gives_up"),
]

def main() -> int:
    return run_mutations(MUTATIONS)


if __name__ == "__main__":
    raise SystemExit(main())
