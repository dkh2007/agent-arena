"""材料构造 + LLMJudge 的变异检验（临时脚本，不提交）。判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）。"""

from __future__ import annotations

from pathlib import Path

# 站到仓根再干活：脚本里全是相对路径（src/… / tests/…），在别的目录直接跑会
# FileNotFoundError。round-86 收尾时实测踩到（用户要求给完整路径，才发现"完整路径"
# 反而跑不通）。这里按"往上找 src/arena"定位仓根，从 .git/ 或 tools/mutations/ 调用都成立。
import os as _os

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
_os.chdir(_ROOT)

from _runner import run_mutations  # 同目录；判定只有一处实现

SRC = Path("src/arena/llm_judge.py")
A = "tests/test_llm_judge.py"
B = "tests/test_llm_judge_llm.py"

MUTATIONS = [
    ("M1 材料里塞进标准答案", '        "answer": answer,',
     '        "answer": answer,\n        "expected": (task.get("test_inputs") or {}).get("expected"),',
     f"{B}::test_context_never_contains_expected_values"),
    ("M2 清单带上 detection_rule", '            "description": str(p.get("description") or ""),',
     '            "description": str(p.get("description") or ""),\n            "detection_rule": p.get("detection_rule"),',
     f"{B}::test_catalog_omits_detection_rule"),
    ("M3 不截断过程", "    for call in list(calls)[:max_calls]:", "    for call in list(calls):",
     f"{B}::test_context_truncates_long_traces"),
    ("M4 不标截断", '        "process_truncated": len(calls) > max_calls,', '        "process_truncated": False,',
     f"{B}::test_context_truncates_long_traces"),
    ("M5 清单外模式静默放过", "        if unknown:", "        if False:",
     f"{B}::test_judge_rejects_pattern_outside_catalog"),
    ("M6 第二次评价复用第一次结果", "            verdict = judge(context)",
     "            verdict = judge(context) if not verdicts else verdicts[0]",
     f"{B}::test_double_judge_actually_calls_twice_without_cache"),
    ("M7 解析器兼容旧字段名", '    missing = [k for k in ("rating", "suspected_patterns") if k not in text]',
     '    if "suspected_patterns" not in text and "patterns" in text:\n        text = {**text, "suspected_patterns": text["patterns"]}\n    missing = [k for k in ("rating", "suspected_patterns") if k not in text]',
     f"{A}::test_parse_verdict_rejects_legacy_patterns_key"),
    ("M8 不截断单次返回值", "    return text if len(text) <= limit else text[:limit] + \"…（截断）\"",
     "    return text",
     f"{B}::test_context_clips_long_result_summary"),
    ("M9 不要求 JSON 模式", "            response_format_json=True,", "            response_format_json=False,",
     f"{B}::test_judge_passes_json_mode_and_temperature_zero"),
    ("M10 不记录 prompt（审计缺失）", '        self.prompts.append(messages[1]["content"])', "        pass",
     f"{B}::test_judge_records_every_prompt_it_sent"),
]

def main() -> int:
    """判定与还原全交给 `_runner`：本文件只负责"变异长什么样"。

    以前这里有一份自己的判定代码，窄看只是重复；实际是两套规则会分叉——旧那套
    把 rc=2/3/4 也当 `DETECTED`，于是节点名写错时会报"抓住了"（round-89 M14 实测）。
    """
    return run_mutations(MUTATIONS, default_path=SRC, temp_prefix="pt-judge-")

if __name__ == "__main__":
    raise SystemExit(main())
