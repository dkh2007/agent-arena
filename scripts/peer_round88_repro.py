"""round-88 外部审核回件的本地复核复现（只读仓库；临时库不落在仓库里）。

    python scripts/peer_round88_repro.py

为什么要留这个脚本：回件里的 `repro_b07c.py` / `repro_b07c_2.py` 在**评审方的工作区**，
维护者手里没有。§88.4 写的每个数字都必须能在自己机器上一遍跑出来，
否则「核证过」就只是自述。本脚本从仓库 `src/` 直接 import，不调用回件的脚本。

**它不是验收结论**：造数据仍走产品写路径，报告与公式口径是两条路径共用的；
三条现状锁（F2 / T6）目前是**故意保留的红灯**，脚本把它们打印出来而不是修好。
"""

from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "src" / "arena").is_dir())
sys.path.insert(0, str(ROOT / "src"))

from arena import mastery as m  # noqa: E402
from arena import mastery_snapshot as ms  # noqa: E402
from arena import store  # noqa: E402
from arena.diagnosis_report import build_report, load_task, load_vocabulary  # noqa: E402

TASK = load_task(ROOT / "tasks" / "dbg-001-order-report.yaml")
VOCAB = load_vocabulary()
FIXTURES = ROOT / "fixtures"
MSP = "multi_step_planning"
AT = "2026-11-01T00:00:00Z"


def _report(name: str, sid: str, *, version: str | None = None) -> dict:
    """真报告：voided 由 `build_report()` 比对 task_version 得出，不是手改字段。"""
    raw = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    if version is not None:
        raw["submission"]["task_version"] = version
    report = build_report(TASK, raw, VOCAB)
    report["submission"]["submission_id"] = sid
    report["submission"]["evaluated_at"] = AT
    return report


def _fresh() -> sqlite3.Connection:
    path = Path(tempfile.mkdtemp()) / "repro.db"
    conn = store.connect(path)
    store.init_db(conn)
    store.register_skills(conn)
    store.register_patterns(conn)
    store.register_task(conn, ROOT / "tasks" / "dbg-001-order-report.yaml")
    return conn


def _banner(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    # R1 —— voided 是否计入 P（回件 F1；本地核证把定性改为「两份口径互斥」）
    _banner("R1 voided 提交计入 P")
    conn = _fresh()
    v = _report("raw-perfect", "v1", version="2020-01-01.1")
    print(f"  score.voided={v['score']['voided']}  "
          f"report_attribution={v['report_attribution']}")
    store.save_submission(conn, v, user_id="alice", created_at=AT)
    print(f"  build_series().p = {m.build_series(conn, 'alice', MSP).p}")
    row = store.load_mastery_snapshot(conn, "alice", MSP)
    print(f"  快照行 = {None if row is None else {'p': row['mastery_p'], 'n': row['evidence_count'], 'last': row['last_submission_id']}}")
    conn.close()

    # R2 / R3 —— 孤儿行清理范围（本地比回件多一类「用户未注册」）
    _banner("R2 孤儿行：发现得了 ≠ 修得掉")
    conn = _fresh()
    store.ensure_user(conn, "registered_no_evidence")
    conn.execute("PRAGMA foreign_keys = OFF")
    with conn:
        for user, skill in (("alice", "phantom_skill"),
                            ("ghost_user", MSP),
                            ("registered_no_evidence", MSP)):
            conn.execute("INSERT INTO mastery_snapshots VALUES (?,?,?,?,?,?,?,?)",
                         (user, skill, 0.9, 0.3, None, 1, "gone", AT))
    conn.execute("PRAGMA foreign_keys = ON")
    print(f"  重算前不一致 = {[c.reason for c in ms.verify_all(conn) if not c.consistent]}")
    print(f"  recompute_all 写入行数 = {ms.recompute_all(conn)}")
    print(f"  重算后残留 = {[tuple(r) for r in conn.execute('SELECT user_id, skill_id FROM mastery_snapshots ORDER BY 1,2')]}")
    print(f"  重算后不一致 = {[c.reason for c in ms.verify_all(conn) if not c.consistent]}")
    conn.close()

    # R4 —— 提交与刷新不在同一事务（现状锁，未修）
    _banner("R4 刷新失败时的半状态（已知缺陷，B-07d ③）")
    conn = _fresh()
    original = ms.refresh_user

    def boom(*args, **kwargs):
        raise RuntimeError("模拟刷新失败")

    ms.refresh_user = boom
    try:
        store.save_submission(conn, _report("raw-perfect", "h1"),
                              user_id="alice", created_at=AT)
    except Exception as exc:
        print(f"  调用方收到 = {type(exc).__name__}: {exc}")
    finally:
        ms.refresh_user = original
    print(f"  submissions={conn.execute('SELECT COUNT(*) FROM submissions').fetchone()[0]}  "
          f"snapshots={conn.execute('SELECT COUNT(*) FROM mastery_snapshots').fetchone()[0]}")
    conn.close()

    # R5 —— 迁移目标版本上界（已修：现在应抛错）
    _banner("R5 migrate 超前目标（已修）")
    conn = _fresh()
    try:
        store.migrate(conn, target=store.SCHEMA_VERSION + 1)
        print("  未抛错 —— F4 修复失效！")
    except store.StoreError as exc:
        print(f"  抛错 = {exc}")
    print(f"  PRAGMA user_version = {store.schema_version(conn)}（应仍为 {store.SCHEMA_VERSION}）")
    conn.close()

    # R7 / T5 —— 并列顺序与读路径新鲜度
    _banner("R7 并列顺序 / T5 改坏值后照读")
    conn = _fresh()
    for user in ("bob", "alice"):
        store.save_submission(conn, _report("raw-perfect", f"k-{user}"),
                              user_id=user, created_at=AT)
    with conn:
        conn.execute("UPDATE mastery_snapshots SET mastery_p = 0.5 WHERE skill_id = ?", (MSP,))
    print(f"  同分读回顺序 = {[r['user_id'] for r in ms.list_ranked(conn, MSP)]}")
    with conn:
        conn.execute("UPDATE mastery_snapshots SET mastery_p = 0.01 "
                     "WHERE user_id = 'alice' AND skill_id = ?", (MSP,))
    ranked = ms.list_ranked(conn, MSP)
    print(f"  改坏后读回 = {[(r['user_id'], r['mastery_p']) for r in ranked]}")
    print(f"  verify_all 另路报 = {[c.reason for c in ms.verify_all(conn) if not c.consistent]}")
    conn.close()

    # T4 —— λ 漂移信号
    _banner("T4 λ 漂移（口径漂移 ≠ 数据损坏）")
    conn = _fresh()
    store.save_submission(conn, _report("raw-perfect", "t4"), user_id="alice", created_at=AT)
    c = ms.verify_one(conn, "alice", MSP, lam=0.5)
    print(f"  lam=0.5 → reason={c.reason} stored_p={c.stored_p} replayed_p={c.replayed_p}")
    conn.close()

    # R6 —— 词表（已修：5 种且被 verify_one 解包使用）
    _banner("R6 REASONS 词表")
    print(f"  len(REASONS) = {len(ms.REASONS)} → {ms.REASONS}")
    print(f"  verify_one 用的是解包常量 = {ms.REASON_STALE_VALUE!r}")
    print("\n本脚本只在系统临时目录建 SQLite 文件，仓库内不留库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
