"""重评链的变异检验（B-04d 接线，round-92）。

判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）：

    DETECTED  变异后 pytest 退出码 **1**
    MISSED    变异后仍全绿——这条测试没在管这事
    INVALID   锚点没命中 / 收集失败——**不算抓住**

## 这 19 组各自防的是什么

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
- M11 打"批量不筛任务"（顺手把别的任务也重评了）；
- M12/M13/M14 打"读数口径退回旧件"（round-93：默认视图仍给冻结报告、
  `view` 写错静默回落、子资源把"不存在"和"没重评过"混成一样）。
- M15–M19 打"受影响范围算错"（B-04e ⓑ）。这一组的失效方式和上面都不同：
  **范围错了不会报错，只会多算或少算**——
  少算（M15 读不到指纹当已符合 / M17 丢 task 过滤）⇒ 有人永久停在旧标准上；
  多算（M16 拿落库时那份比）⇒ 每次扫都把同一批重评一遍，范围永不收敛；
  M18 让坏数据混进"不知道怎么判的"那桶，M19 让空判据算出"全库过期"。

每条变异用一个**全新的** `PYTEST_DEBUG_TEMPROOT`：默认临时根攒满旧目录后，
pytest 收尾清理会撞沙箱批量删除守卫并抛 `SystemExit(1)`，让退出码失去意义。
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

from _runner import run_mutations  # 同目录；判定只有一处实现

RAP = Path("src/arena/reappraisal.py")
ST = Path("src/arena/store.py")
API = Path("src/arena/submission_api.py")
T = "tests/test_reappraisal.py"
TA = "tests/test_submission_api.py"

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

    # 锚点要带到 WHERE：这段 SELECT 在 store 里有两处（`user_reports` 与
    # `blocked_submissions`），只写前两行会命中 2 次而判 INVALID。
    # 重放读的是 `user_reports`（mastery._user_reports 调它），所以锚在 `WHERE s.user_id` 那条上。
    ("M2 重放读 diagnosis 而非生效报告（P 停在旧结论）",
     ST,
     '        "       r.report AS latest_report "\n'
     '        "FROM submissions s "\n'
     '        "LEFT JOIN reappraisals r ON r.submission_id = s.submission_id "\n'
     '        "     AND r.revision = (SELECT MAX(revision) FROM reappraisals "\n'
     '        "                      WHERE submission_id = s.submission_id) "\n'
     '        "WHERE s.user_id = ? ORDER BY s.created_at ASC, s.submission_id ASC",',
     '        "       NULL AS latest_report "\n'
     '        "FROM submissions s "\n'
     '        "LEFT JOIN reappraisals r ON r.submission_id = s.submission_id "\n'
     '        "     AND r.revision = (SELECT MAX(revision) FROM reappraisals "\n'
     '        "                      WHERE submission_id = s.submission_id) "\n'
     '        "WHERE s.user_id = ? ORDER BY s.created_at ASC, s.submission_id ASC",',
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

    # ── 读数口径（round-93）──────────────────────────────────────────────────
    ("M12 读接口退回旧件（默认视图给冻结报告，学习者看不到新结论）",
     API,
     "            report = (store.load_report(conn, submission_id) if view == \"original\"\n"
     "                      else store.effective_report(conn, submission_id))",
     "            report = store.load_report(conn, submission_id)  # 变异：无视 view",
     f"{TA}::test_get_by_default_returns_the_effective_report_not_the_frozen_one"),

    ("M13 view 写错静默回落成默认值（看错了也没有任何信号）",
     API,
     "    if got[0] not in _VIEWS:\n"
     "        raise SubmissionError(\"bad_request\",\n"
     "                              f\"view 只接受 {'/'.join(_VIEWS)}（收到 {got[0]!r}）\")",
     "    if got[0] not in _VIEWS:\n"
     "        return \"effective\"  # 变异：回落",
     f"{TA}::test_a_typo_in_view_is_a_400_not_a_silent_fallback"),

    ("M14 重评链把「提交不存在」当成「没重评过」（返回空数组）",
     API,
     "        conn = self.ctx[\"conn_factory\"]()\n"
     "        try:\n"
     "            store.load_report(conn, submission_id)          # 兼做存在性检查",
     "        self._send(200, {\"submission_id\": submission_id, \"reappraisals\": [],\n"
     "                         \"effective_ratio\": 0.0, \"original_ratio\": 0.0})\n"
     "        return  # 变异：跳过存在性检查\n"
     "        conn = self.ctx[\"conn_factory\"]()\n"
     "        try:\n"
     "            store.load_report(conn, submission_id)",
     f"{TA}::test_reappraisal_chain_distinguishes_missing_from_never_reappraised"),

    # ── 受影响范围（B-04e ⓑ）────────────────────────────────────────────────
    ("M15 读不到指纹就当「已符合」（全部历史静默漏掉，没有任何信号）",
     RAP,
     "        if fp is None:\n"
     "            unknown.append(sid)",
     "        if fp is None:\n"
     "            current.append(sid)  # 变异：不知道 ⇒ 当作已经符合",
     f"{T}::test_unfingerprinted_history_is_in_scope_and_reported_as_its_own_bucket"),

    ("M16 指纹取落库时那份（重评过的提交永远「过期」，范围永不收敛）",
     ST,
     '        report = _load(row["latest_report"] if from_reappraisal else row["diagnosis"])',
     '        report = _load(row["diagnosis"])  # 变异：无视重评链',
     f"{T}::test_scope_shrinks_after_reappraisal_instead_of_flagging_it_forever"),

    ("M17 取数不按 task_id 过滤（改一次判据＝触发一次全库重评）",
     ST,
     '"WHERE s.task_id = ? ORDER BY s.created_at ASC, s.submission_id ASC",',
     '"WHERE s.task_id IS NOT NULL AND ? IS NOT NULL "\n'
     '        "ORDER BY s.created_at ASC, s.submission_id ASC",  # 变异：丢了过滤',
     f"{T}::test_scope_never_lends_one_task_its_neighbours_submissions"),

    ("M18 坏指纹静默当「没指纹」（坏数据永远查不出来）",
     ST,
     "        if fp is not None and not (isinstance(fp, str) and fp):",
     "        if False:  # 变异：不校验，坏数据混进「不知道怎么判的」那桶",
     f"{T}::test_a_corrupt_fingerprint_is_loud_instead_of_looking_unfingerprinted"),

    ("M19 不校验 rubric 就给范围（空判据会算出「全库过期」）",
     RAP,
     '    task = _check_task(task)\n'
     '    fingerprint = dr.rubric_fingerprint(task)',
     '    fingerprint = dr.rubric_fingerprint(task)  # 变异：跳过 _check_task',
     f"{T}::test_scope_refuses_an_invalid_rubric_and_writes_nothing"),
]

def _run(node: str) -> tuple[int, str]:
    env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-reap-")}
    r = subprocess.run([PYEXE, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                       capture_output=True, text=True, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()

def main() -> int:
    """判定与还原全交给 `_runner`：本文件只负责"变异长什么样"。

    以前这里有一份自己的判定代码，窄看只是重复；实际是两套规则会分叉——旧那套
    把 rc=2/3/4 也当 `DETECTED`，于是节点名写错时会报"抓住了"（round-89 M14 实测）。
    """
    return run_mutations(MUTATIONS, temp_prefix="pt-reappraisal-")

if __name__ == "__main__":
    raise SystemExit(main())
