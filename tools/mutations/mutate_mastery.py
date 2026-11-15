"""掌握度模块的变异测试（round-80，B-07）。

判定三档沿用 round-75：`DETECTED` / `INVALID`（锚点未命中、变异自身崩、语法错）/ `MISSED`。
**`INVALID` 不算抓住**——变异脚本自己的 bug 会让它看起来像"抓住了"。

跑法： python tools/mutations/mutate_mastery.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# 站到仓根再干活：脚本里全是相对路径（src/… / tests/…），在别的目录直接跑会
# FileNotFoundError。round-86 收尾时实测踩到（用户要求给完整路径，才发现"完整路径"
# 反而跑不通）。这里按"往上找 src/arena"定位仓根，从 .git/ 或 tools/mutations/ 调用都成立。
import os as _os

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
_os.chdir(_ROOT)

M = Path("src/arena/mastery.py")
S = Path("src/arena/store.py")
T = "tests/test_mastery.py"
PYEXE = sys.executable

MUTATIONS = [
    # (说明, 文件, 旧片段, 新片段, 期望撞红的用例)
    ("M1 分母数断言条数而不是权重",
     M, "        counted += weight", "        counted += 1",
     f"{T}::test_partial_pass_within_one_skill_is_fractional"),

    ("M2 unscored/blocked 也计入（决策 7 被破）",
     M, '        if not rule["counts_toward_mastery"]:', "        if False:",
     f"{T}::test_unscored_and_blocked_are_dropped_from_both_sides"),

    ("M3 无证据当成 0 分（决策 7 被破，另一路）",
     M,
     "        if not ev.counts:\n"
     "            skipped.append(Skipped(ev.submission_id, ev.at, \"no_counted_assertion\"))\n"
     "            continue\n"
     "        c = ev.c",
     "        if not ev.counts:\n"
     "            skipped.append(Skipped(ev.submission_id, ev.at, \"no_counted_assertion\"))\n"
     "            c = 0.0\n"
     "        else:\n"
     "            c = ev.c",
     f"{T}::test_skipped_evidence_does_not_advance_p"),

    ("M4 按字符串排时间（不解析时刻）",
     M, "    decorated = [(parse_instant(e.at), e.submission_id, e) for e in items]",
     "    decorated = [(e.at, e.submission_id, e) for e in items]",
     f"{T}::test_order_is_by_instant_not_by_string_or_input_order"),

    ("M5 同刻平局按输入顺序（不稳定）",
     M, "    decorated.sort(key=lambda t: (t[0], t[1]))", "    decorated.sort(key=lambda t: t[0])",
     f"{T}::test_same_instant_is_ordered_by_submission_id_for_determinism"),

    ("M6 λ 越界不报错",
     M, "    if not 0.0 < float(lam) <= 1.0:", "    if False:",
     f"{T}::test_out_of_range_inputs_raise_instead_of_clamping"),

    ("M7 P/c 越界改成 clamp 而不是报错",
     M,
     "    if not 0.0 <= float(value) <= 1.0:\n        raise MasteryError(f\"{name} 必须在 [0,1]，收到 {value}\")",
     "    if not 0.0 <= float(value) <= 1.0:\n        return min(1.0, max(0.0, float(value)))",
     f"{T}::test_out_of_range_inputs_raise_instead_of_clamping"),

    ("M8 初值改成 0.5 先验（发明一个没有依据的数）",
     M, "                p = c\n", "                p = 0.5\n",
     f"{T}::test_seeded_initialisation_vs_explicit_p0"),

    ("M9 零权重断言静默跳过（不留痕）",
     M, '            ignored.append(Ignored(str(a.get("id")), f"zero_weight:state={state}"))',
     "            _ = state",
     f"{T}::test_zero_weight_counted_assertion_is_loud_in_ignored"),

    ("M10 时间轴改读报告 evaluated_at（不读库锚点）",
     M, "        skill_evidence(report, skill_id, at=created_at)",
     "        skill_evidence(report, skill_id)",
     f"{T}::test_build_series_uses_db_created_at_as_time_axis"),

    ("M11 读历史不过滤用户（跨用户串味）",
     S, "        \"SELECT * FROM submissions WHERE user_id = ? ORDER BY created_at ASC, submission_id ASC\",\n        (user_id,),",
     "        \"SELECT * FROM submissions ORDER BY created_at ASC, submission_id ASC\",\n        (),",
     f"{T}::test_build_series_ignores_other_users"),

    ("M12 缺 attribution_policy 时静默按 pass/fail 计",
     M,
     "    if not isinstance(policy, dict) or not policy:\n"
     "        raise MasteryError(f\"{submission_id} 缺 attribution_policy，无法判断哪些状态计入掌握度\")",
     "    if not isinstance(policy, dict) or not policy:\n"
     "        policy = {'pass': {'counts_toward_mastery': True},"
     " 'fail': {'counts_toward_mastery': True}, 'blocked': {'counts_toward_mastery': False},"
     " 'unscored': {'counts_toward_mastery': False}}",
     f"{T}::test_missing_attribution_policy_is_loud"),

    ("M13 门禁失败时把 credit 证据一并作废（擅自改口径）",
     M, '        if not rule["counts_toward_mastery"]:',
     "        if not rule[\"counts_toward_mastery\"] or (report.get(\"score\") or {}).get(\"disqualified\"):",
     f"{T}::test_gate_failure_still_lifts_credit_skills"),

    ("M14 门禁状态一律记 ok（留痕失效）",
     M, '        gate_status = "disqualified"', '        gate_status = "ok"',
     f"{T}::test_gate_status_is_recorded_but_does_not_change_p"),

    ("M15 缺 score 时默认 ok（静默吞掉信号）",
     M,
     "    if not isinstance(score, dict) or \"disqualified\" not in score or \"voided\" not in score:\n"
     "        raise MasteryError(f\"{submission_id} 缺 score.disqualified / score.voided，无法记录门禁状态\")",
     "    if not isinstance(score, dict):\n        score = {}",
     f"{T}::test_missing_score_is_loud_not_defaulted_to_ok"),
]


def main() -> int:
    results = []
    for name, path, old, new, node in MUTATIONS:
        orig = path.read_text(encoding="utf-8")
        backup = orig
        if orig.count(old) != 1:
            results.append((name, "INVALID", f"锚点命中 {orig.count(old)} 次（应恰好 1 次）"))
            continue
        try:
            path.write_text(orig.replace(old, new, 1), encoding="utf-8")
            status, detail = "MISSED", ""
            for i in range(2):  # 每个变异跑两遍：测试若自己抖动，两次结果必须一致
                r = subprocess.run(
                    [PYEXE, "-m", "pytest", "-q", node, "-p", "no:cacheprovider"],
                    capture_output=True, text=True,
                    env={**__import__("os").environ, "PYTEST_DEBUG_TEMPROOT": str(Path(".git/pt").resolve())},
                )
                if r.returncode == 0:
                    status, detail = "MISSED", "测试仍绿——这条测试没在管这事"
                    break
                if r.returncode == 5:
                    status, detail = "INVALID", "测试节点不存在"
                    break
                lines = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
                tail = lines[-1] if lines else ""
                if "error" in r.stdout.lower() and "failed" not in r.stdout.lower():
                    status, detail = "INVALID", "收集/语法错误，不算抓住"
                    break
                status, detail = "DETECTED", tail
        finally:
            path.write_text(backup, encoding="utf-8")
        results.append((name, status, detail))

    for name, status, detail in results:
        print(f"{status:9} {name}  {detail}")
    ok = all(status == "DETECTED" for _, status, _ in results)
    print("---")
    print("判定：", f"全 DETECTED（{len(results)} 条）" if ok else "有漏网或无效变异，需处理")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
