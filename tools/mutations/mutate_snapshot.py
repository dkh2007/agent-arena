"""P 快照（v2 / B-07c）的变异测试。判定三档沿用既有约定：`DETECTED` / `INVALID` / `MISSED`。

**`INVALID` 不算抓住**——脚本自己写错锚点会让它看起来像"抓住了"。
每条变异必须换一个新的 `PYTEST_DEBUG_TEMPROOT`：复用同一目录时 pytest 开场清理旧编号目录、
被沙箱的 bulk-delete 守卫拦掉，整场 INTERNALERROR（输出里既没 failed 也没断言信息）。
round-84 / round-85 各栽过一次。

跑法（在仓根执行，或给绝对路径；脚本自己会站到仓根）：
    python tools/mutations/mutate_snapshot.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

S = Path("src/arena/store.py")
N = Path("src/arena/mastery_snapshot.py")
T = "tests/test_mastery_snapshot.py"
PYEXE = sys.executable

MUTATIONS = [
    # ① 无证据写 0：把"没做过"排成"完全不会"（决策 7）
    ("M1 无证据时写 0 而不是不留行",
     N, '    if series.p is None:\n        return None',
     '    if series.p is None:\n        return {"user_id": user_id, "skill_id": skill_id, '
     '"mastery_p": 0.0, "lam": float(lam), "p0": p0, "evidence_count": 0, '
     '"last_submission_id": None, "updated_at": at}',
     f"{T}::test_no_evidence_writes_no_row_instead_of_zero"),

    # ② 把衰减后的值落库 ⇒ 同一份报告会因读取时机不同产生不同历史
    ("M2 快照存的是衰减后的 P_eff 而不是 P_raw",
     N, '        "mastery_p": float(series.p),',
     '        "mastery_p": float(series.p) * 0.9,',
     f"{T}::test_snapshot_stores_p_raw_not_decayed_p_effective"),

    # ③ 一致性用容差 ⇒ 几乎看不出的偏差被放行
    ("M3 逐位比较改成 0.01 容差",
     N, '    if stored_p != replayed_p:',
     '    if abs(stored_p - replayed_p) > 0.01:',
     f"{T}::test_tiny_drift_is_still_detected_without_any_tolerance"),

    # ④ 只比 P、不比参数 ⇒ λ 标定后旧口径的快照静默混进新榜
    ("M4 跳过参数核对（lam/p0/证据数/最后提交）",
     N, '    if not same_params:',
     '    if False and not same_params:',
     f"{T}::test_replay_wins_when_only_the_parameters_drift"),

    # ⑤ 缺快照时假装一致 ⇒ 写路径漏了也看不出来
    ("M5 缺快照报 consistent=True",
     N, '        return SnapshotCheck(user_id, skill_id, None, replayed_p,\n'
        '                             REASON_MISSING, False)',
     '        return SnapshotCheck(user_id, skill_id, None, replayed_p,\n'
     '                             REASON_MISSING, True)',
     f"{T}::test_missing_snapshot_is_reported_not_silently_fixed"),

    # ⑥ 孤儿行不被识别 ⇒ 幽灵快照一直挂在榜上
    ("M6 重放无证据时把有行也说成 ok",
     N, '        reason = REASON_OK if stored is None else REASON_ORPHAN',
     '        reason = REASON_OK',
     f"{T}::test_orphan_snapshot_is_cleaned_by_refresh"),

    # ⑦ 孤儿行不删 ⇒ 修完还留着陈旧值
    ("M7 refresh_one 遇无证据不删旧行",
     N, '    if row is None:\n        store.delete_mastery_snapshot(conn, user_id, skill_id)\n'
        '        return None',
     '    if row is None:\n        return None',
     f"{T}::test_orphan_snapshot_is_cleaned_by_refresh"),

    # ⑧ verify_all 只扫 users×skills ⇒ 库外塞进来的幽灵行漏网
    ("M8 verify_all 不扫快照表里多出来的行",
     N, '    pairs.extend(\n        (r["user_id"], r["skill_id"]) for r in store.list_mastery_snapshots(conn)\n    )',
     '    pairs.extend(())',
     f"{T}::test_verify_all_covers_orphan_rows_outside_the_user_skill_product"),

    # ⑨ 核对顺手写库 ⇒ 一次查询产生写副作用
    ("M9 verify_one 顺手重算（读变成写）",
     N, '    stored = store.load_mastery_snapshot(conn, user_id, skill_id)',
     '    refresh_one(conn, user_id, skill_id, lam=lam, p0=p0)\n'
     '    stored = store.load_mastery_snapshot(conn, user_id, skill_id)',
     f"{T}::test_verify_is_pure_read"),

    # ⑩ 排序不按 P 降序 ⇒ 榜单整个反了
    ("M10 排序改成按 P 升序",
     S, '        sql += " ORDER BY mastery_p DESC, user_id ASC"',
     '        sql += " ORDER BY mastery_p ASC, user_id ASC"',
     f"{T}::test_ranked_is_sorted_stable_and_has_no_rank_field"),

    # ⑪ 平手不按 user_id 兜底 ⇒ 两次读可能给出两个榜
    ("M11 排序去掉平手兜底",
     S, '        sql += " ORDER BY mastery_p DESC, user_id ASC"',
     '        sql += " ORDER BY mastery_p DESC"',
     f"{T}::test_ranked_is_sorted_stable_and_has_no_rank_field"),

    # ⑫ limit 失效 ⇒ 展示层要前 N 个却拿到全部
    ("M12 list_ranked 忽略 limit",
     N, '        rows = rows[:limit]',
     '        rows = rows',
     f"{T}::test_ranked_is_sorted_stable_and_has_no_rank_field"),

    # ⑬ 缓存不覆盖 P ⇒ 第二次写留着旧值（表名前缀让 SQL 仍合法，才是真的语义变异；
    #    早先那版删了 Python 参数分隔逗号，靠 SyntaxWarning 变红，属 INVALID 假抓住，已换掉）
    ("M13 快照 upsert 不覆盖 P（只更新其余列）",
     S, '            "mastery_p = excluded.mastery_p, lam = excluded.lam, p0 = excluded.p0, "',
     '            "mastery_p = mastery_snapshots.mastery_p, lam = excluded.lam, p0 = excluded.p0, "',
     f"{T}::test_upsert_overwrites_because_a_cache_has_no_history"),

    # ⑭ 提交后不刷新 ⇒ 判据①「save_submission 后 P 落库」直接不成立
    ("M14 写路径不自动刷新快照",
     S, '    if refresh_mastery:\n        # 局部导入：mastery_snapshot → mastery → store，模块级导入会成环。',
     '    if False and refresh_mastery:\n        # 局部导入：mastery_snapshot → mastery → store，模块级导入会成环。',
     f"{T}::test_save_submission_refreshes_snapshots_by_default"),

    # ⑮ 允许降级 ⇒ 用旧代码开新库会静默写坏结构
    ("M15 migrate 允许从更高版本降级",
     S, '    if current > target:\n        raise StoreError(',
     '    if False and current > target:\n        raise StoreError(',
     f"{T}::test_migrate_is_idempotent_and_never_downgrades"),

    # ⑯ 外键校验被删 ⇒ 幽灵技能能进榜
    ("M16 快照写入不检查技能是否注册",
     S, '    if conn.execute("SELECT 1 FROM skills WHERE skill_id = ?", (skill_id,)).fetchone() is None:\n'
        '        raise StoreError(',
     '    if False and conn.execute("SELECT 1 FROM skills WHERE skill_id = ?", (skill_id,)).fetchone() is None:\n'
     '        raise StoreError(',
     f"{T}::test_snapshot_foreign_keys_are_enforced"),

    # ⑰ 数值越界不挡 ⇒ 排序依据可以是 1.5 或字符串
    ("M17 mastery_p 越界不校验",
     S, '    if isinstance(p, bool) or not isinstance(p, (int, float)) or not (0.0 <= float(p) <= 1.0):\n'
        '        raise StoreError(f"mastery_p 必须是 [0,1] 内的数值（收到 {p!r}）")',
     '    if False:\n        raise StoreError("unused")',
     f"{T}::test_snapshot_rejects_out_of_range_or_bad_types"),

    # ⑱ 元数据列写死 ⇒ λ 标定后无法分辨旧口径
    ("M18 元数据证据数写死为 0",
     N, '        "evidence_count": len(series.steps),',
     '        "evidence_count": 0,',
     f"{T}::test_snapshot_metadata_records_what_was_replayed"),

    # ⑲～㉑ round-88 外审回件新增的三条锁：修之前实测 MISSED / 无对应测试
    ("M19 词表取值拼错（外审 F3：改之前 REASONS 零引用，拼错无人察觉）",
     N, '    "stale_params",       # P 碰巧对，但 λ / p0 / 证据数 / 最后一条提交对不上',
     '    "stale_parmas",       # P 碰巧对，但 λ / p0 / 证据数 / 最后一条提交对不上',
     f"{T}::test_reasons_vocabulary_is_enforced"),

    ("M20 migrate 允许写出超前版本号（外审 F4）",
     S, '    if target > SCHEMA_VERSION:\n        raise StoreError(',
     '    if False and target > SCHEMA_VERSION:\n        raise StoreError(',
     f"{T}::test_migrate_rejects_a_future_target"),

    ("M21 名次字段偷偷长出来（外审 F8；修复前实测 27 条全绿 MISSED）",
     N, '        rows = rows[:limit]\n    return rows',
     '        rows = rows[:limit]\n'
     '    return [{**r, "rank_no": i + 1} for i, r in enumerate(rows)]',
     f"{T}::test_ranked_is_sorted_stable_and_has_no_rank_field"),
]


def main() -> int:
    results = []
    for name, path, old, new, node in MUTATIONS:
        env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-snap-")}
        orig = path.read_text(encoding="utf-8")
        if orig.count(old) != 1:
            results.append((name, "INVALID", f"锚点命中 {orig.count(old)} 次（应恰好 1 次）"))
            continue
        try:
            path.write_text(orig.replace(old, new, 1), encoding="utf-8")
            r = subprocess.run([PYEXE, "-m", "pytest", "-q", node, "-p", "no:cacheprovider"],
                               capture_output=True, text=True, env=env)
            if r.returncode == 0:
                status, detail = "MISSED", "测试仍绿——这条测试没在管这事"
            elif r.returncode == 5:
                status, detail = "INVALID", "测试节点不存在"
            else:
                lines = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
                if "error" in r.stdout.lower() and "failed" not in r.stdout.lower():
                    status, detail = "INVALID", "收集/语法错误，不算抓住"
                else:
                    status, detail = "DETECTED", lines[-1] if lines else ""
        finally:
            path.write_text(orig, encoding="utf-8")
        results.append((name, status, detail))

    for name, status, detail in results:
        print(f"{status:9} {name}  {detail}")
    ok = all(status == "DETECTED" for _, status, _ in results)
    print("---")
    print("判定：", f"全 DETECTED（{len(results)} 条）" if ok else "有漏网或无效变异，需处理")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
