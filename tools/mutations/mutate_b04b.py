"""B-04b 的变异检验：judge 接线层 + 仲裁队列。

判定三档（与 round-89 定的规则一致，见 `tools/mutations/README.md` 第 5 坑）：

    DETECTED  变异后 pytest 退出码 **1**（有用例失败）——真被抓住
    MISSED    变异后仍全绿——这条测试没在管这事
    INVALID   锚点没命中 / 节点不存在 / 收集失败——**不算抓住**

只有 `returncode == 1` 记 DETECTED：round-89 实测过 `no tests ran` 的退出码是 4，
旧判定把它当"抓住"，等于白跑一轮还留下假绿。

每条变异用一个**全新的** `PYTEST_DEBUG_TEMPROOT`：默认临时根攒满旧目录后，
pytest 收尾清理会撞沙箱的批量删除守卫并抛 `SystemExit(1)`，让退出码失去意义。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

# 站到仓根再干活：下面全是相对路径。按"往上找 src/arena"定位，从任何目录调用都成立。
_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

JP = Path("src/arena/judge_pipeline.py")
ST = Path("src/arena/store.py")
API = Path("src/arena/submission_api.py")
T = "tests/test_judge_pipeline.py"
PYEXE = sys.executable

MUTATIONS = [
    # ── judge_pipeline：入队判据与 attach 的边界 ────────────────────────────
    ("M1 阈值比较写成 <=（边界入队）",
     JP,
     "        return self.result.needs_arbitration(self.threshold)",
     "        return self.result.agreement <= self.threshold",
     f"{T}::test_threshold_boundary_is_strictly_less_than"),
    ("M2 永不入队",
     JP,
     "    if not outcome.needs_arbitration:\n        return None",
     "    if True:\n        return None",
     f"{T}::test_disagreement_lands_in_the_queue"),
    ("M3 允许覆盖已有 judge 字段",
     JP,
     "    existing = [k for k in JUDGE_FIELDS if k in report]\n    if existing:",
     "    existing = [k for k in JUDGE_FIELDS if k in report]\n    if False:",
     f"{T}::test_attach_refuses_to_overwrite_and_to_attach_to_a_fragment"),
    ("M4 attach 顺手改分（judge 碰分数）",
     JP,
     "    new.update(copy.deepcopy(fields))",
     "    new.update(copy.deepcopy(fields))\n    new[\"score\"][\"ratio\"] = 0.0",
     f"{T}::test_judge_never_moves_score_assertions_or_attribution"),
    ("M5 attach 返回原对象（就地改）",
     JP,
     "    new = copy.deepcopy(report)",
     "    new = report",
     f"{T}::test_attach_returns_a_copy_and_leaves_the_original_alone"),
    ("M6 不检查报告完整性",
     JP,
     "    for key in FROZEN_KEYS:\n        if key not in report:",
     "    for key in FROZEN_KEYS:\n        if False:",
     f"{T}::test_attach_refuses_to_overwrite_and_to_attach_to_a_fragment"),

    # ── submission_api：默认关闭 / 故障不冒充判决 / 派生列 ──────────────────
    ("M7 配置检查被删（只给 judge 也放行）",
     API,
     "    if (judge is None) != (arbitration_threshold is None):",
     "    if False:",
     f"{T}::test_judge_without_threshold_is_a_config_error"),
    ("M8 judge 崩了也不报错（吞成正常提交）",
     API,
     "            raise SubmissionError(\"judge_unavailable\", str(exc), status=503) from exc",
     "            pass",
     f"{T}::test_judge_crash_is_503_and_writes_nothing"),
    ("M9 派生列不填（报告有、库里 NULL）",
     API,
     "            judge_scores=report.get(\"judge_scores\"),",
     "            judge_scores=None,",
     f"{T}::test_disagreement_lands_in_the_queue"),

    # ── store：队列自身 ────────────────────────────────────────────────────
    ("M10 重复入队时覆盖原阈值",
     ST,
     "    existing = load_arbitration(conn, submission_id)\n    if existing is not None:\n        return existing",
     "    existing = load_arbitration(conn, submission_id)\n"
     "    if existing is not None:\n"
     "        with conn:\n"
     "            conn.execute(\"UPDATE arbitration_queue SET threshold = ? WHERE submission_id = ?\",\n"
     "                         (float(threshold), submission_id))\n"
     "        return load_arbitration(conn, submission_id)",
     f"{T}::test_queue_is_idempotent_and_keeps_the_original_threshold"),
    ("M11 不校验提交是否已落库",
     ST,
     "    if row is None:\n        raise StoreError(\n"
     "            f\"提交 {submission_id} 还没落库",
     "    if False:\n        raise StoreError(\n"
     "            f\"提交 {submission_id} 还没落库",
     f"{T}::test_queue_requires_a_saved_submission"),
    ("M12 裁决不要求写理由",
     ST,
     "    if not isinstance(note, str) or not note.strip():\n        raise StoreError(",
     "    if False:\n        raise StoreError(",
     f"{T}::test_resolution_requires_a_note_and_is_not_overwritable"),
    ("M13 允许改判已裁决的条目",
     ST,
     "    if current[\"status\"] != \"pending\":",
     "    if False:",
     f"{T}::test_resolution_requires_a_note_and_is_not_overwritable"),
]


def _run(node: str) -> tuple[int, str]:
    env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-b04b-")}
    r = subprocess.run([PYEXE, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                       capture_output=True, text=True, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()


def main() -> int:
    results: list[tuple[str, str, str]] = []
    originals = {p: p.read_text(encoding="utf-8") for p in (JP, ST, API)}
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
