"""B-04h ③「跑批流程停在哪一步」的变异检验（round-101）。

判定三档与 `README.md` 第 5 坑一致：

    DETECTED  变异后 pytest 退出码 **1** —— 真被抓住
    MISSED    变异后仍全绿 —— 这条测试没在管这事
    INVALID   锚点没命中 / 收集失败 —— **不算抓住**

## 这一层的失效方式

流程层的错都不报错，只是"悄悄多做了不该做的那一步"：

- 没署名就写库（事后查不到谁批的）；
- 演练与执行对不上还继续写（拿一份过期清单当依据）；
- 演练给的是空清单（"先看再批"变成看不看都一样）；
- 顺手把"发文案"也做了（发出去收不回来）；
- 归档覆盖上一次（复盘时只剩最后一份）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

CY = Path("src/arena/cycle.py")
T = "tests/test_cycle.py"
PYEXE = sys.executable

MUTATIONS = [
    ("M1 演练也写库（没署名就把分改了）",
     CY,
     "    if who is not None:",
     "    if True:",
     f"{T}::test_dry_run_writes_nothing"),

    ("M2 演练与执行对不上也继续写（拿过期清单当依据）",
     CY,
     "        problem = _alignment_error(dry, real)",
     "        problem = None",
     f"{T}::test_mismatch_between_dry_and_real_stops_the_batch"),

    ("M3 只比份数不比差值（同一批人、每个都少改一点就漏过去）",
     CY,
     "    if before == after:",
     "    if set(before) == set(after):",
     f"{T}::test_mismatch_between_dry_and_real_stops_the_batch"),

    ("M4 重评理由里不写谁批的（事后无从追责）",
     CY,
     'f"{reason}（经 {who} 确认）"',
     "reason",
     f"{T}::test_execution_requires_a_name_and_records_it"),

    ("M5 空名字当成没批（空格也算署名）",
     CY,
     "    if approved_by is not None and (not isinstance(approved_by, str) or not approved_by.strip()):",
     "    if False:",
     f"{T}::test_blank_name_is_not_a_name"),

    ("M6 待发清单不过滤（只在档案里的、不告知的也混进「要发」）",
     CY,
     '        for n in plan.disclosed("page")',
     "        for n in plan.notices",
     f"{T}::test_pending_sends_only_contains_page_disclosures"),

    ("M7 演练给空清单（「先看再批」变成看不看都一样）",
     CY,
     "        for out in dry.changed():",
     "        for out in []:",
     f"{T}::test_dry_run_still_says_what_would_change"),

    ("M8 报告不署名（记录里看不出是谁批的）",
     CY,
     "        approved_by=who,",
     "        approved_by=None,",
     f"{T}::test_execution_requires_a_name_and_records_it"),

    ("M9 归档覆盖上一次（复盘时只剩最后一份）",
     CY,
     "        if directory.exists() and any(directory.iterdir()):",
     "        if False:",
     f"{T}::test_archive_refuses_to_overwrite"),

    ("M10 不报「掌握度待定」那批（口径未定的人从流程里消失）",
     CY,
     "    if plan.needs_decision():",
     "    if False:",
     f"{T}::test_report_flags_the_undecided_ones"),
]


def _run(node: str) -> tuple[int, str]:
    env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-cyc-")}
    r = subprocess.run([PYEXE, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                       capture_output=True, text=True, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()


def main() -> int:
    results: list[tuple[str, str, str]] = []
    originals = {p: p.read_text(encoding="utf-8") for p in {m[1] for m in MUTATIONS}}
    try:
        for name, path, old, new, node in MUTATIONS:
            orig = originals[path]
            hits = orig.count(old)
            if hits != 1:
                results.append((name, "INVALID", f"锚点命中 {hits} 次（应为 1）"))
                continue
            path.write_text(orig.replace(old, new, 1), encoding="utf-8")
            try:
                rc, out = _run(node)
            finally:
                path.write_text(orig, encoding="utf-8")
            if rc == 1:
                tail = [line for line in out.splitlines() if line.strip()][-1:]
                results.append((name, "DETECTED", tail[0] if tail else ""))
            elif rc == 0:
                results.append((name, "MISSED", "测试仍绿——没在管这事"))
            else:
                results.append((name, "INVALID", f"收集/环境错误 rc={rc}"))
    finally:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")

    for name, verdict, detail in results:
        print(f"{verdict:9} {name}  {detail}")
    ok = all(v == "DETECTED" for _, v, _ in results)
    print("---")
    print(f"共 {len(results)} 组；判定：", "全 DETECTED" if ok else "有漏网或无效变异")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
