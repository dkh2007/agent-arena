"""B-04d「甲 + 护栏」的变异检验：争议标记 + 默认排除 + 产物留存 + v4 迁移。

判定三档（与 round-89 定的规则一致，见 `tools/mutations/README.md` 第 5 坑）：

    DETECTED  变异后 pytest 退出码 **1**（有用例失败）——真被抓住
    MISSED    变异后仍全绿——这条测试没在管这事
    INVALID   锚点没命中 / 节点不存在 / 收集失败——**不算抓住**

只有 `returncode == 1` 记 DETECTED：`no tests ran` 的退出码是 4，
按旧的"非 0 即抓住"会白跑一轮还留下假绿。

每条变异用一个**全新的** `PYTEST_DEBUG_TEMPROOT`：默认临时根攒满旧目录后，
pytest 收尾清理会撞沙箱批量删除守卫并抛 `SystemExit(1)`，让退出码失去意义。

## 这 10 组各自防的是什么

护栏最容易的失效方式不是"写错"，而是**默认值反了**——标记还在、榜单照样出，
只是含糊成绩悄悄混了进去。所以 M2/M3 专门打方向与范围，M1/M5 打"标记恒不成立"，
M4 打"裁决后标记不消失"，M6 打"参数不校验"，M7/M8 打"产物没存下来"（真实发生过的 bug），
M9 打"半旧库上迁移整批失败"，M10 打"作废的争议也被标"。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

SNAP = Path("src/arena/mastery_snapshot.py")
ST = Path("src/arena/store.py")
API = Path("src/arena/submission_api.py")
T = "tests/test_b04d_guardrail.py"
TS = "tests/test_store.py"
PYEXE = sys.executable

MUTATIONS = [
    # ── 标记判定：不许恒不成立、不许扩散 ────────────────────────────────────
    ("M1 标记恒为空集（护栏完全不生效）",
     SNAP,
     "    disputes = pending_disputes(conn, user_id=user_id)\n    if not disputes:\n        return set()",
     "    disputes = pending_disputes(conn, user_id=user_id)\n    if disputes is not None:\n        return set()",
     f"{T}::test_disputed_submission_marks_only_the_skills_it_actually_fed"),

    ("M2 标记扩散：不看这份提交有没有喂过证据",
     SNAP,
     "            if mastery.skill_evidence(report, sid).counts:",
     "            if True:",
     f"{T}::test_voided_dispute_is_not_marked"),

    ("M3 已裁决的提交仍算争议（裁决后标记不消失）",
     SNAP,
     "\"FROM arbitration_queue WHERE status = 'pending'\")",
     "\"FROM arbitration_queue WHERE status IS NOT NULL\")",
     f"{T}::test_resolving_the_queue_clears_the_marker"),

    ("M4 is_provisional 恒为 False（单点判据被架空）",
     SNAP,
     "    return (user_id, skill_id) in provisional_pairs(conn, skill_id=skill_id, user_id=user_id)",
     "    return False",
     f"{T}::test_disputed_submission_marks_only_the_skills_it_actually_fed"),

    # ── 读榜护栏：方向与参数校验 ────────────────────────────────────────────
    ("M5 读榜方向反了（默认把争议成绩放出去）",
     SNAP,
     "    if include_provisional:\n"
     "        rows = [{**r, \"provisional\": (r[\"user_id\"], r[\"skill_id\"]) in disputed} for r in rows]\n"
     "    else:\n"
     "        rows = [r for r in rows if (r[\"user_id\"], r[\"skill_id\"]) not in disputed]",
     "    if not include_provisional:\n"
     "        rows = [{**r, \"provisional\": (r[\"user_id\"], r[\"skill_id\"]) in disputed} for r in rows]\n"
     "    else:\n"
     "        rows = [r for r in rows if (r[\"user_id\"], r[\"skill_id\"]) not in disputed]",
     f"{T}::test_ranked_default_excludes_provisional_rows"),

    ("M6 不校验 include_provisional 的类型",
     SNAP,
     "    if not isinstance(include_provisional, bool):\n        raise SnapshotError(",
     "    if False:\n        raise SnapshotError(",
     f"{T}::test_ranked_rejects_non_bool_flag"),

    # ── 产物流转：那个真发生过的 bug ────────────────────────────────────────
    ("M7 落库层不写产物（列永远 NULL）",
     ST,
     "                _dump(trace),\n                _dump(artifacts),",
     "                _dump(trace),\n                None,",
     f"{T}::test_submission_api_persists_the_original_artifacts"),

    ("M8 提交入口不把产物传下去（真实 bug 的原形）",
     API,
     "            trace=prep.trace, artifacts=prep.artifacts,",
     "            trace=prep.trace,",
     f"{T}::test_submission_api_persists_the_original_artifacts"),

    # ── v4 迁移：半旧库上不许整批失败 ───────────────────────────────────────
    ("M9 加列前不判重（半旧库上 migrate 整批炸）",
     ST,
     "    if \"artifacts\" in columns:\n        return",
     "    if False:\n        return",
     f"{TS}::test_replaying_the_v4_step_on_a_half_built_database_does_not_crash"),

    # ── 甲的实质：照算不许滑回乙 ────────────────────────────────────────────
    ("M10 证据一律不计入（P 被冻住，等于滑回乙）",
     Path("src/arena/mastery.py"),
     "        if not ev.counts:\n"
     "            skipped.append(Skipped(ev.submission_id, ev.at, \"no_counted_assertion\"))\n"
     "            continue",
     "        if True:\n"
     "            skipped.append(Skipped(ev.submission_id, ev.at, \"no_counted_assertion\"))\n"
     "            continue",
     f"{T}::test_disputed_submission_still_counts_for_mastery_and_scheduling"),
]


def _run(node: str) -> tuple[int, str]:
    env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-b04d-")}
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
