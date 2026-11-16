"""重评链的变异检验（B-04d 接线，round-92）。

判定三档与 `mutate_b04d.py` 完全一致（见 `tools/mutations/README.md` 第 5 坑）：

    DETECTED  变异后 pytest 退出码 **1**
    MISSED    变异后仍全绿——这条测试没在管这事
    INVALID   锚点没命中 / 收集失败——**不算抓住**

## 这 11 组各自防的是什么

重评链最容易的失效方式不是崩，而是**"做了但没生效"**：新报告落了库、分数没变、
或者变了但历史被覆盖。所以：

- M1/M2 打"重评不生效"（读的仍是旧报告）；
- M3 打"delta 起点错"（第二次重评拿最原始的分数比）；
- M4 打"缺产物用空对象顶替"（凭空扣分，决策 7 的反面）；
- M5 打"拿别的题的判据重评"；
- M6/M7 打"重评换了证据的身份证"；
- M8 打"没有理由的重评"；
- M9 打"演练也落库"（预判变成既成事实）；
- M10 打"只报成功的"（把不会数的人蒙过去）；
- M11 打"批量不筛任务"（顺手把别的任务也重评了）。

每条变异用一个**全新的** `PYTEST_DEBUG_TEMPROOT`：默认临时根攒满旧目录后，
pytest 收尾清理会撞沙箱批量删除守卫并抛 `SystemExit(1)`，让退出码失去意义。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

RAP = Path("src/arena/reappraisal.py")
ST = Path("src/arena/store.py")
T = "tests/test_reappraisal.py"
PYEXE = sys.executable

MUTATIONS = [
    # ── 重评必须真的生效 ────────────────────────────────────────────────────
    ("M1 生效报告永远取原始那份（重评白做）",
     ST,
     '''    latest = load_reappraisal(conn, submission_id)
    if latest is not None:
        return latest["report"]''',
     '''    latest = load_reappraisal(conn, submission_id)
    if False:
        return latest["report"]''',
     f"{T}::test_reappraisal_changes_the_score_but_not_the_evidence"),

    ("M2 重放读 diagnosis 而非生效报告（P 停在旧结论）",
     ST,
     '        "       r.report AS latest_report "',
     '        "       NULL AS latest_report "',
     f"{T}::test_one_submission_still_contributes_exactly_one_step"),

    # ── delta 的起点 ────────────────────────────────────────────────────────
    ("M3 delta 永远从最原始分数起算（第二次重评的差异失真）",
     RAP,
     "    old_ratio = store.effective_ratio(conn, submission_id)",
     '    old_ratio = store.load_submission(conn, submission_id)["assert_score"]',
     f"{T}::test_second_reappraisal_starts_from_the_previous_conclusion"),

    # ── 缺产物不许顶替（决策 7）────────────────────────────────────────────
    ("M4 缺产物拿空对象顶替（把平台的缺陷算成学习者没做）",
     RAP,
     '''    if row["artifacts"] is None:
        return None, "missing_artifacts"''',
     '''    if row["artifacts"] is None:
        row = dict(row)
        row["artifacts"] = {}''',
     f"{T}::test_missing_artifacts_blocks_reappraisal_instead_of_scoring_zero"),

    # ── 判据与证据不许被换 ──────────────────────────────────────────────────
    ("M5 不校验任务（拿别的题的 rubric 重评）",
     RAP,
     '    if row["task_id"] != task["task_id"]:',
     "    if False:",
     f"{T}::test_reappraisal_refuses_a_rubric_from_another_task"),

    ("M6 落库不校验 submission_id（重评可以换证据）",
     ST,
     '    if new_sub.get("submission_id") != submission_id:',
     "    if False:",
     f"{T}::test_stored_reappraisal_cannot_swap_the_identity_of_the_evidence"),

    ("M7 落库不校验 task_id（同一份作答被记到别的题下）",
     ST,
     '    if new_sub.get("task_id") != original["task_id"]:',
     "    if False:",
     f"{T}::test_stored_reappraisal_cannot_swap_the_identity_of_the_evidence"),

    # ── 理由与演练 ──────────────────────────────────────────────────────────
    ("M8 空理由也照收（重评变成偷偷改分）",
     RAP,
     "    if not isinstance(reason, str) or not reason.strip():",
     "    if not isinstance(reason, str):",
     f"{T}::test_reappraisal_requires_a_reason"),

    ("M9 演练也落库（预判变成既成事实）",
     RAP,
     "    if apply:",
     "    if True:",
     f"{T}::test_dry_run_gives_the_same_numbers_and_writes_nothing"),

    # ── 批量的两个方向 ──────────────────────────────────────────────────────
    ("M10 批量只返回成功的（把重评不了的藏起来）",
     RAP,
     '''    return [
        reappraise(conn, sid, task, reason=reason, review_threshold=review_threshold,
                   apply=apply, created_at=created_at)
        for sid in ids
    ]''',
     '''    outs = [
        reappraise(conn, sid, task, reason=reason, review_threshold=review_threshold,
                   apply=apply, created_at=created_at)
        for sid in ids
    ]
    return [o for o in outs if o.state == "reappraised"]''',
     f"{T}::test_batch_reappraisal_reports_the_ones_it_could_not_redo"),

    ("M11 批量不筛任务（顺手把别的任务也重评了）",
     RAP,
     '        if row["task_id"] == task["task_id"]',
     "        if True",
     f"{T}::test_batch_reappraisal_only_touches_the_given_task"),
]


def _run(node: str) -> tuple[int, str]:
    env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-reap-")}
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
