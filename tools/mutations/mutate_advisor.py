"""排程层（B-09）的变异测试。判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）：`DETECTED` / `INVALID` / `MISSED`。

**`INVALID` 不算抓住**——变异脚本自己写错锚点会让它看起来像"抓住了"（round-84 就踩过两次：
一次把锚点写进了 `scheduler.py` 而实际在 `diagnosis_report.py`，一次是测试数据全整秒导致变异无行为）。

跑法： python tools/mutations/mutate_advisor.py
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

A = Path("src/arena/schedule_advisor.py")
T = "tests/test_schedule_advisor.py"

MUTATIONS = [
    # (说明, 文件, 旧片段, 新片段, 期望撞红的用例)
    ("M1 缺数据的因子记 0 分（决策 7 的反面）",
     A, "        if f.value is None:\n            continue",
     "        if f.value is None:\n            total += f.weight\n            continue",
     f"{T}::test_factor_with_no_data_at_all_drops_out_of_the_denominator"),

    ("M2 缺数据不做均值顶（重归一化，让数据少的项占便宜）",
     A, "            if value is None and means[name] is not None:",
     "            if False:",
     f"{T}::test_missing_factor_for_one_item_is_filled_with_batch_mean"),

    ("M3 不校验权重沿优先级递减（'截止优先'变成空话）",
     A, "    if any(a <= b for a, b in zip(values, values[1:])):",
     "    if False:",
     f"{T}::test_bad_weights_rejected"),

    ("M4 平手格失效（0.005 的分差决定顺序 = 假精度）",
     A, "    return int(round(score / TIE_GRID))",
     "    return int(round(score / 1e-6))",
     f"{T}::test_score_difference_within_grid_is_a_tie_and_due_wins"),

    ("M5 排序只看到期先后（四因子白算）",
     A, "        key = (0 if score is not None else 1,\n               -_bucket(score) if score is not None else 0,",
     "        key = (0 if score is not None else 1,\n               0,",
     f"{T}::test_five_items_match_hand_calculation"),

    ("M6 改序允许不写理由（留痕变成空壳）",
     A, "        if not isinstance(ov.reason, str) or not ov.reason.strip():",
     "        if False:",
     f"{T}::test_bad_override_rejected"),

    ("M7 同一条允许被改两次（静默取后者）",
     A, "        if ov.review_id in seen:",
     "        if False:",
     f"{T}::test_same_item_overridden_twice_rejected"),

    # 注意：`if sorted(order) != sorted(suggested)` 这条守卫**按构造不可达**
    # （`remove` + `insert` 不改集合），直接删守卫的变异测不出来（首跑就是 MISSED）。
    # 所以这里改的是"只 insert 不 remove"——它会让集合真的多出一条，守卫与测试都该拦住。
    ("M8 改序只 insert 不 remove（集合多出一条，等于凭空加项）",
     A, "        order.remove(ov.review_id)\n        order.insert(ov.to_rank - 1, ov.review_id)",
     "        order.insert(ov.to_rank - 1, ov.review_id)",
     f"{T}::test_override_keeps_the_set_intact"),

    ("M9 错误热度按模式计数（同一份作业踩两个坑算两次）",
     A, '        hit: set[str] = set()\n        for pattern in row.get("detected_patterns") or []:\n'
        '            for skill in pattern_skills.get(str(pattern), ()):\n'
        '                hit.add(str(skill))',
     '        hit: list[str] = []\n        for pattern in row.get("detected_patterns") or []:\n'
     '            for skill in pattern_skills.get(str(pattern), ()):\n'
     '                hit.append(str(skill))',
     f"{T}::test_error_heat_counts_submissions_not_patterns"),

    ("M10 热度窗口按 at 上界失效（回看历史时用了未来数据）",
     A, "        if when < start or when > at:",
     "        if when < start:",
     f"{T}::test_error_heat_window_is_inclusive_and_ignores_future"),

    ("M11 未复习的卡把 R 当 0.0（'还没开始'说成'已经忘完'）",
     A, "        if card is None or card.stability is None or card.last_review is None:",
     "        if card is None:",
     f"{T}::test_never_reviewed_card_produces_no_retrievability"),

    ("M12 不筛到期/未完成（未到期的项也排进今天）",
     A, "    rows = sch.sort_queue(items, at=at)",
     "    rows = [dict(r) for r in items if r.get('status', 'pending') == 'pending']",
     f"{T}::test_unfinished_and_future_items_are_not_in_the_plan"),

    ("M13 混批不检查归属（别人的项排进我的队列）",
     A, "        if owner is not None and owner != user_id:",
     "        if False:",
     f"{T}::test_item_from_another_user_is_rejected"),
]

def main() -> int:
    """判定与还原全交给 `_runner`：本文件只负责“变异长什么样”。

    以前这里有一份自己的判定代码，窄看只是重复；实际是两套规则会分叉——旧那套
    把 rc=2/3/4 也当 `DETECTED`，于是节点名写错时会报“抓住了”（round-89 M14 实测）。
    """
    return run_mutations(MUTATIONS, temp_prefix="pt-advisor-")

if __name__ == "__main__":
    raise SystemExit(main())
