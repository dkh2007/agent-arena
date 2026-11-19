"""掌握度模块的变异测试（round-80，B-07）。

判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）：`DETECTED` / `INVALID`（锚点未命中、变异自身崩、语法错）/ `MISSED`。
**`INVALID` 不算抓住**——变异脚本自己的 bug 会让它看起来像"抓住了"。

跑法： python tools/mutations/mutate_mastery.py
"""

from __future__ import annotations

from pathlib import Path

# 站到仓根再干活：脚本里全是相对路径（src/… / tests/…），在别的目录直接跑会
# FileNotFoundError。round-86 收尾时实测踩到（用户要求给完整路径，才发现"完整路径"
# 反而跑不通）。这里按"往上找 src/arena"定位仓根，从 .git/ 或 tools/mutations/ 调用都成立。
import os as _os

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
_os.chdir(_ROOT)

from _runner import run_mutations  # 同目录；判定只有一处实现

M = Path("src/arena/mastery.py")
S = Path("src/arena/store.py")
T = "tests/test_mastery.py"

MUTATIONS = [
    # (说明, 文件, 旧片段, 新片段, 期望撞红的用例)
    ("M1 分母数断言条数而不是权重",
     M, "        counted += weight", "        counted += 1",
     f"{T}::test_partial_pass_within_one_skill_is_fractional"),

    ("M2 unscored/blocked 也计入（决策 7 被破）",
     M, '        if not rule["counts_toward_mastery"]:', "        if False:",
     # B-04i 后不能撞 raw-no-trace 那条：它整份排除，会挡在规则判断前面
     # （实测 MISSED）。改撞过程齐全的独立样本，决策 7 与 B-04i 各有守卫。
     f"{T}::test_unscored_and_blocked_are_dropped_even_with_process_present"),

    ("M3 无证据当成 0 分（决策 7 被破，另一路）",
     M,
     "        if not ev.counts:\n"
     "            reason = \"process_missing\" if ev.process_missing else \"no_counted_assertion\"\n"
     "            skipped.append(Skipped(ev.submission_id, ev.at, reason))\n"
     "            continue\n"
     "        c = ev.c",
     "        if not ev.counts:\n"
     "            reason = \"process_missing\" if ev.process_missing else \"no_counted_assertion\"\n"
     "            skipped.append(Skipped(ev.submission_id, ev.at, reason))\n"
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
     S, '        "WHERE s.user_id = ? ORDER BY s.created_at ASC, s.submission_id ASC",',
     '        "WHERE (? IS NOT NULL) ORDER BY s.created_at ASC, s.submission_id ASC",',
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
     f"{T}::test_disqualified_still_counts_under_policy_a_and_is_recorded"),

    ("M15 缺 score 时默认 ok（静默吞掉信号）",
     M,
     "    if not isinstance(score, dict) or \"disqualified\" not in score or \"voided\" not in score:\n"
     "        raise MasteryError(f\"{submission_id} 缺 score.disqualified / score.voided，无法记录门禁状态\")",
     "    if not isinstance(score, dict):\n        score = {}",
     f"{T}::test_missing_score_is_loud_not_defaulted_to_ok"),

    ("M16 作废也计入（人拍板甲被推翻）",
     M,
     '    exclude_voided = gate_status == "voided"',
     "    exclude_voided = False",
     f"{T}::test_voided_evidence_is_excluded_not_scored_as_zero"),

    ("M17 作废被不合格压过（顺序回退，空白旧版照样刷高 P）",
     M,
     '    if score["voided"]:\n'
     '        gate_status = "voided"\n'
     '    elif score["disqualified"]:\n'
     '        gate_status = "disqualified"',
     '    if score["disqualified"]:\n'
     '        gate_status = "disqualified"\n'
     '    elif score["voided"]:\n'
     '        gate_status = "voided"',
     "tests/test_diagnosis_report.py::test_td038_voided_never_counts_toward_mastery"),

    # --- B-04i：2026-11-19 人拍「甲」缺过程整份排除 -------------------------------
    ("M18 缺过程也计入（人拍甲被推翻，回到 c=1.0）",
     M, "        if exclude_voided or process_missing:", "        if exclude_voided:",
     f"{T}::test_unscored_and_blocked_are_dropped_from_both_sides"),

    ("M19 缺 process_fact 默认 false（没记当成过程齐全）",
     M,
     '    process = report.get("process_fact")\n'
     '    if not isinstance(process, dict) or not isinstance(process.get("missing"), bool):\n'
     '        raise MasteryError(f"{submission_id} 缺 process_fact.missing（布尔），无法判断过程是否收到")\n'
     '    process_missing = process["missing"]',
     '    process = report.get("process_fact") or {}\n'
     '    process_missing = bool(process.get("missing"))',
     f"{T}::test_missing_process_fact_is_loud_not_defaulted_to_false"),

    ("M20 skipped 不区分缺过程与无证据（留痕失效）",
     M,
     '            reason = "process_missing" if ev.process_missing else "no_counted_assertion"',
     '            reason = "no_counted_assertion"',
     f"{T}::test_missing_process_evidence_is_excluded_not_scored_as_zero"),

    ("M21 缺过程与门禁不合格混成一格（把 A 口径顺手改了）",
     M, '    process_missing = process["missing"]',
     '    process_missing = bool(process["missing"] or score["disqualified"])',
     f"{T}::test_missing_process_and_disqualified_are_different_dimensions"),

    ("M22 标了缺过程却带权重的矛盾证据不报错",
     M, "        if ev.process_missing and ev.counts:", "        if False:",
     f"{T}::test_contradictory_process_missing_evidence_is_loud"),
]

def main() -> int:
    """判定与还原全交给 `_runner`：本文件只负责"变异长什么样"。

    以前这里有一份自己的判定代码，窄看只是重复；实际是两套规则会分叉——旧那套
    把 rc=2/3/4 也当 `DETECTED`，于是节点名写错时会报"抓住了"（round-89 M14 实测）。
    """
    return run_mutations(MUTATIONS, temp_prefix="pt-mastery-", repeats=2)

if __name__ == "__main__":
    raise SystemExit(main())
