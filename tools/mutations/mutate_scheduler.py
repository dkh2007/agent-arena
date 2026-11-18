"""复习调度模块的变异测试（round-84，B-08）。

判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）：`DETECTED` / `INVALID`（锚点未命中、变异自身崩、语法错）/ `MISSED`。
**`INVALID` 不算抓住**——变异脚本自己的 bug 会让它看起来像"抓住了"。

跑法： python tools/mutations/mutate_scheduler.py
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

C = Path("src/arena/scheduler.py")
S = Path("src/arena/store.py")
D = Path("src/arena/diagnosis_report.py")
T = "tests/test_scheduler.py"

MUTATIONS = [
    # (说明, 文件, 旧片段, 新片段, 期望撞红的用例)
    ("M1 阈值判定从闭区间改成开区间（0.9/0.7/0.4 掉档）",
     C, "        if c >= threshold:", "        if c > threshold:",
     f"{T}::test_rating_map_boundaries"),

    ("M2 不做「本次不算数」判断（白卷照动卡）",
     C, "    if suppression:", "    if False:",
     f"{T}::test_disqualified_submission_updates_nothing"),

    ("M3 抑制条件只认门禁，漏掉「缺过程」",
     D, '    if (report.get("process_fact") or {}).get("missing"):', "    if False:",
     f"{T}::test_missing_process_submission_updates_nothing"),

    ("M4 review_id 不含 due（下次复习覆盖上一条，历史丢失）",
     C, '    return "rv-" + hashlib.sha256(f"{user_id}|{skill_id}|{due}".encode("utf-8")).hexdigest()[:16]',
     '    return "rv-" + hashlib.sha256(f"{user_id}|{skill_id}".encode("utf-8")).hexdigest()[:16]',
     f"{T}::test_review_id_depends_on_due_so_rerun_is_idempotent"),

    ("M5 关旧项不看是否到期（提前关掉未来的计划）",
     S, '"WHERE user_id = ? AND status = \'pending\' AND due <= ? AND skill_id IN ({placeholders})",',
     '"WHERE user_id = ? AND status = \'pending\' AND ? IS NOT NULL AND skill_id IN ({placeholders})",',
     f"{T}::test_future_items_are_not_closed_by_an_early_submission"),

    ("M6 用全部决策去关旧项（白卷也关）",
     S, '    effective = [d.skill_id for d in decisions if getattr(d, "action", None) in ("review", "passed")]',
     "    effective = [d.skill_id for d in decisions]",
     f"{T}::test_suppressed_run_does_not_close_pending_items"),

    ("M7 每日上限不生效（返回全部到期项）",
     C, "    return due[:cap], max(0, len(due) - cap)", "    return due, 0",
     f"{T}::test_take_daily_caps_without_dropping"),

    ("M8 队列按字符串排 due（跨偏移判反）",
     C, "    live.sort(key=lambda x: (x[0], x[1]))", '    live.sort(key=lambda x: (x[2]["due"], x[1]))',
     f"{T}::test_queue_order_parses_instants_instead_of_comparing_strings"),

    ("M9 register_task 回退成直接读 YAML（变体元数据全错）",
     S,
     "    from arena.diagnosis_report import load_task\n\n    task = load_task(path)",
     "    import yaml\n\n    task = yaml.safe_load(path.read_text(encoding=\"utf-8\"))",
     f"{T}::test_register_task_reads_variant_inheritance"),

    ("M10 写卡不检查技能是否注册（外键变装饰）",
     S,
     '            if conn.execute("SELECT 1 FROM skills WHERE skill_id = ?", (skill_id,)).fetchone() is None:\n'
     '                raise StoreError(\n'
     '                    f"技能 {skill_id} 未注册——先 register_skills()；"\n'
     '                    "允许未注册技能写卡片等于让 skills 外键变成装饰"\n'
     "                )",
     "            _ = skill_id",
     f"{T}::test_card_for_unregistered_skill_is_loud"),

    ("M11 全过也算缺口（c==1 也建复习项）",
     C, "        if c >= 1.0:", "        if c > 1.0:",
     f"{T}::test_perfect_run_creates_no_item_but_updates_cards"),

    ("M12 新卡不给 due（默认取调用瞬间，重放不可复现）",
     C, '    return Card(card_id=_stable_int(f"{user_id}|{skill_id}"), due=instant)',
     '    return Card(card_id=_stable_int(f"{user_id}|{skill_id}"))',
     f"{T}::test_card_id_is_stable_across_processes"),

    ("M13 打开 fuzz（due 随机偏移）",
     C, '    kwargs: dict[str, Any] = {"enable_fuzzing": False}',
     '    kwargs: dict[str, Any] = {"enable_fuzzing": True}',
     f"{T}::test_scheduler_is_deterministic_because_fuzz_is_off"),

    ("M14 due 不归到秒（同一时刻两种写法 ⇒ 两条队列项）",
     C, "    return instant.astimezone(timezone.utc).replace(microsecond=0).isoformat()",
     "    return instant.astimezone(timezone.utc).isoformat()",
     f"{T}::test_due_is_normalised_to_seconds_even_if_at_has_microseconds"),
]

def main() -> int:
    """判定与还原全交给 `_runner`：本文件只负责"变异长什么样"。

    以前这里有一份自己的判定代码，窄看只是重复；实际是两套规则会分叉——旧那套
    把 rc=2/3/4 也当 `DETECTED`，于是节点名写错时会报"抓住了"（round-89 M14 实测）。
    """
    return run_mutations(MUTATIONS, temp_prefix="pt-scheduler-")

if __name__ == "__main__":
    raise SystemExit(main())
