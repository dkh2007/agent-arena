"""材料构造 + LLMJudge 的变异检验（临时脚本，不提交）。判定三档同 round-75。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# 站到仓根再干活：脚本里全是相对路径（src/… / tests/…），在别的目录直接跑会
# FileNotFoundError。round-86 收尾时实测踩到（用户要求给完整路径，才发现"完整路径"
# 反而跑不通）。这里按"往上找 src/arena"定位仓根，从 .git/ 或 tools/mutations/ 调用都成立。
import os as _os

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
_os.chdir(_ROOT)

SRC = Path("src/arena/llm_judge.py")
PYEXE = sys.executable
A = "tests/test_llm_judge.py"
B = "tests/test_llm_judge_llm.py"

PT = Path(".git/pt").resolve()
PT.mkdir(parents=True, exist_ok=True)
CHILD_ENV = {**os.environ, "PYTEST_DEBUG_TEMPROOT": str(PT)}

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
    orig = SRC.read_text(encoding="utf-8")
    results = []
    try:
        for name, old, new, node in MUTATIONS:
            hits = orig.count(old)
            if hits != 1:
                results.append((name, "INVALID", f"锚点命中 {hits} 次（应为 1）"))
                continue
            SRC.write_text(orig.replace(old, new, 1), encoding="utf-8")
            r = subprocess.run(
                [PYEXE, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                capture_output=True,
                text=True,
                env=CHILD_ENV,
            )
            SRC.write_text(orig, encoding="utf-8")
            out = (r.stdout + r.stderr).lower()
            if r.returncode == 5:
                results.append((name, "INVALID", "测试节点不存在"))
            elif r.returncode == 0:
                results.append((name, "MISSED", "测试仍绿 ⇒ 没在管这事"))
            elif "error" in out and "failed" not in out:
                results.append((name, "INVALID", "收集/语法错误，不算抓住"))
            else:
                last = [l for l in r.stdout.strip().splitlines() if l.strip()][-1:]
                results.append((name, "DETECTED", last[0] if last else ""))
    finally:
        SRC.write_text(orig, encoding="utf-8")

    for n, v, d in results:
        print(f"{v:9} {n}  {d}")
    ok = all(v == "DETECTED" for _, v, _ in results)
    print("---")
    print("判定：", "全 DETECTED" if ok else "有漏网或无效变异")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
