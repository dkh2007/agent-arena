"""B-04f 全库基线的变异检验：证明那 9 条判据在管"批次能不能交代"，不是在管注释。

判定三档与 `README.md` 第 5 坑一致：

    DETECTED  变异后 pytest 退出码 **1** —— 真被抓住
    MISSED    变异后仍全绿 —— 这条测试没在管这事
    INVALID   锚点没命中 / 收集失败 —— **不算抓住**

## 这 8 组各自防的是什么

基线的失效方式和单份重评不同：单份错了会算错分，批次错了**看起来全对**——
数报得漂亮、报告齐全，只是漏了一批人或把补不上的算成了补上了。所以：

- M1 打"跑完不重算范围"（用跑之前的范围冒充跑之后，等于永远不承认自己干过活）；
- M2 打"只跑 stale 不跑 unfingerprinted"——那正好是基线要治的那批人；
- M3 打"补不上的算成补上了"（缺产物被计进 reappraised）；
- M4 打"演练也落库"（预判变成既成事实，且再也不可回退）；
- M5 打"同一个任务传两次悄悄去重"（两个版本被当成一个，事后无从发现）；
- M6 打"没理由也照跑"（整批改分没有任何线索）；
- M7 打"结果不带 user_id"（跑完了没人知道自己被重算过）；
- M8 打"报表说已经全部打平"（把补不上的从剩余里抹掉，报告立刻变漂亮）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

BL = Path("src/arena/baseline.py")
RAP = Path("src/arena/reappraisal.py")
T = "tests/test_baseline.py"
PYEXE = sys.executable

MUTATIONS = [
    ("M1 跑完不重算范围（用跑之前的范围冒充跑之后）",
     BL,
     "    after = {tid: rap.scope_for_task(conn, task) for tid, task in by_id.items()}",
     "    after = before  # 变异：不重算，等于永远不承认自己干过活",
     f"{T}::test_baseline_backfills_fingerprints_for_history_that_can_be_replayed"),

    ("M2 只跑 stale、不跑 unfingerprinted（正好漏掉基线要治的那批人）",
     BL,
     "        for sid in before[tid].all_ids:",
     "        for sid in before[tid].stale:  # 变异：历史那批不处理",
     f"{T}::test_baseline_backfills_fingerprints_for_history_that_can_be_replayed"),

    ("M3 补不上的算成补上了（缺产物被计进 reappraised）",
     BL,
     '            "missing_artifacts": by_state.get("missing_artifacts", 0),',
     '            "missing_artifacts": 0,  # 变异：把它并进 reappraised',
     f"{T}::test_baseline_reports_what_it_could_not_repair_instead_of_calling_it_done"),

    ("M4 演练也落库（预判变成既成事实）",
     BL,
     "                               apply=apply, created_at=created_at)",
     "                               created_at=created_at)  # 变异：无视 apply",
     f"{T}::test_dry_run_writes_nothing_and_shows_it_by_leaving_the_scope_unchanged"),

    ("M5 同一个任务传两次悄悄去重（两个版本被当成一个）",
     BL,
     "        if tid in out:",
     "        if False:  # 变异：不查重，后一个覆盖前一个",
     f"{T}::test_duplicate_tasks_are_rejected"),

    ("M6 没理由也照跑（整批改分没有任何线索）",
     BL,
     '    if not isinstance(reason, str) or not reason.strip():\n'
     '        raise BaselineError("reason 必须写：这是「为什么整批重算」的唯一线索")',
     "    if False:  # 变异：理由交给每一份提交去验（范围为空时等于没验）\n"
     "        pass",
     f"{T}::test_baseline_requires_a_reason_before_doing_any_work"),

    ("M7 结果不带 user_id（跑完了没人知道自己被重算过）",
     RAP,
     '    user_id = store.load_submission(conn, submission_id)["user_id"]',
     "    user_id = None  # 变异：只给 submission_id",
     f"{T}::test_baseline_names_who_lost_points"),

    ("M8 报表说「已全部打平」（把补不上的从剩余里抹掉）",
     BL,
     '            "still_unfingerprinted": sum(len(s.unfingerprinted) for s in self.after.values()),',
     '            "still_unfingerprinted": 0,  # 变异：报告变漂亮了，那批人再也没人提',
     f"{T}::test_blocked_rows_stay_in_scope_forever_and_are_retried_but_never_rewritten"),
]


def _run(node: str) -> tuple[int, str]:
    env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-base-")}
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
