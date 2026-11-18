"""B-04h ②「确认不可回溯」终止标记的变异检验（round-101）。

判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）：

    DETECTED  变异后 pytest 退出码 **1** —— 真被抓住
    MISSED    变异后仍全绿 —— 这条测试没在管这事
    INVALID   锚点没命中 / 收集失败 —— **不算抓住**

## 这一层的失效方式

标记的两种错法都不报错：

1. **标记得太容易**（有产物也能标、不写理由也能标、标两次也不管）——
   于是"确认不可回溯"变成一句谁都能盖的章；
2. **标记管得太宽**（把人从交代名单里删掉、把撤销做成删行、把前提复核省掉）——
   于是"标一下就不用解释了"，或者"产物补回来了还在跳过"。

两种都不会让任何测试变红，除非有人专门锁住它们——本文件逐个把锁拧松一遍。
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

from _runner import run_mutations  # 同目录；判定只有一处实现

ST = Path("src/arena/store.py")
RAP = Path("src/arena/reappraisal.py")
BL = Path("src/arena/baseline.py")
NT = Path("src/arena/notices.py")
T = "tests/test_retirement.py"

MUTATIONS = [
    ("M1 有产物也能标「不可回溯」（拿一句补不回来盖住一件马上能做的事）",
     ST,
     '    if row["has_artifacts"]:\n'
     '        raise StoreError(\n'
     '            f"{submission_id} 有产物、此刻就能重评——不能标记「确认不可回溯」。"',
     '    if False:\n'
     '        raise StoreError(\n'
     '            f"{submission_id} 有产物、此刻就能重评——不能标记「确认不可回溯」。"',
     f"{T}::test_mark_refuses_a_submission_that_can_still_be_reappraised"),

    ("M2 不写理由也能标（没有理由的结案与「悄悄跳过」没有区别）",
     ST,
     '    if not isinstance(reason, str) or not reason.strip():\n'
     '        raise StoreError("reason 必须写：没有理由的结案与「悄悄跳过」没有区别")',
     '    if False:\n'
     '        raise StoreError("reason 必须写：没有理由的结案与「悄悄跳过」没有区别")',
     f"{T}::test_mark_requires_a_reason_and_an_author"),

    ("M3 重复标记静默覆盖（「谁在什么时候决定的」被最后一个人吃掉）",
     ST,
     '    if existing is not None and existing["revoked_at"] is None:',
     '    if False:',
     f"{T}::test_double_mark_is_refused_but_remarking_after_revoke_is_allowed"),

    ("M4 撤销改成删行（那句判断和签字的人一起消失）",
     ST,
     '        conn.execute(\n'
     '            "UPDATE unrecoverable_marks SET revoked_at = ?, revoked_reason = ? "\n'
     '            "WHERE submission_id = ?",',
     '        conn.execute(\n'
     '            "DELETE FROM unrecoverable_marks WHERE submission_id = ?",',
     f"{T}::test_revoked_mark_returns_to_the_retry_scope"),

    ("M5 撤销过的标记继续生效（撤销功能等于没有）",
     ST,
     '           "WHERE m.revoked_at IS NULL")',
     '           "WHERE 1 = 1")',
     f"{T}::test_revoked_mark_returns_to_the_retry_scope"),

    ("M6 标记跨任务生效（一个任务的结案影响另一个任务的重试范围）",
     ST,
     '        sql += " AND s.task_id = ?"',
     '        sql += " AND (s.task_id = ? OR 1 = 1)"',
     f"{T}::test_marks_are_scoped_by_task"),

    ("M7 范围计算完全不看标记（每一轮照样重试、报告照样变长）",
     RAP,
     "        if sid in marks:",
     "        if False:",
     f"{T}::test_marked_row_leaves_the_retry_scope"),

    ("M8 标记了也照样进待重评列表（retired 又被算回 all_ids）",
     RAP,
     "        return self.stale + self.unfingerprinted",
     "        return self.stale + self.unfingerprinted + self.retired",
     f"{T}::test_marked_row_leaves_the_retry_scope"),

    ("M9 不复核标记的前提（产物补回来了，还在跳过它）",
     RAP,
     '            if row["has_artifacts"]:',
     "            if False:",
     f"{T}::test_a_mark_loses_effect_when_the_input_comes_back"),

    ("M10 retired 混进 current（变成「按现行判据判的」，事实是判不了）",
     RAP,
     "                retired.append(sid)      # 前提仍成立：不再重试",
     "                current.append(sid)      # 变异：当成已按现行判据判过",
     f"{T}::test_marked_row_leaves_the_retry_scope"),

    ("M11 标记的人从交代名单里消失（补不上的行不再解释）",
     NT,
     "        elif sid in blocked_ids:",
     "        elif sid in blocked_ids and sid not in marks:",
     f"{T}::test_marking_does_not_remove_anyone_from_the_explanation_path"),

    ("M12 retired 算进 scanned（「这次扫了多少」虚高，看不出还剩谁）",
     BL,
     '            "scanned": sum(len(s.all_ids) for s in self.before.values()),',
     '            "scanned": sum(len(s.all_ids) + len(s.retired) for s in self.before.values()),',
     f"{T}::test_baseline_stops_retrying_marked_rows"),
]

def _run(node: str) -> tuple[int, str]:
    env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-ret-")}
    r = subprocess.run([PYEXE, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                       capture_output=True, text=True, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()

def main() -> int:
    """判定与还原全交给 `_runner`：本文件只负责"变异长什么样"。

    以前这里有一份自己的判定代码，窄看只是重复；实际是两套规则会分叉——旧那套
    把 rc=2/3/4 也当 `DETECTED`，于是节点名写错时会报"抓住了"（round-89 M14 实测）。
    """
    return run_mutations(MUTATIONS, temp_prefix="pt-retirement-")

if __name__ == "__main__":
    raise SystemExit(main())
