"""P 快照（B-07c）的可复核演示：五段，全部用真 SQLite 文件，不留库。

    python scripts/snapshot_walk.py

刻意不写"看起来对"的结论：每段都打印能与 `mastery.build_series()` 对账的数，
读者可以自己改数再跑一遍。**本脚本不产出名次**——排名（B-25）尚未开，
这里只有"按 P 排序的有序行"，没有 rank 字段。
"""

from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import mastery as m  # noqa: E402
from arena import mastery_snapshot as ms  # noqa: E402
from arena import store  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402

TASK_YAML = ROOT / "tasks" / "dbg-001-order-report.yaml"
MSP = "multi_step_planning"


def _report(name: str, sid: str, at: str) -> dict:
    r = copy.deepcopy(run_fixture(name))
    r["submission"]["submission_id"] = sid
    r["submission"]["evaluated_at"] = at
    return r


def _setup(conn) -> None:
    store.init_db(conn)
    store.register_skills(conn)
    store.register_patterns(conn)
    store.register_task(conn, TASK_YAML)


def _banner(n: int, title: str) -> None:
    print(f"\n=== {n}. {title} ===")


def main() -> int:
    path = Path(tempfile.mkdtemp()) / "snapshot-walk.db"
    conn = store.connect(path)
    _setup(conn)

    # ① 提交即落库：三次真提交，看 P 怎么动
    _banner(1, "提交即刷新（P 随报告变化，不是第一份的旧值）")
    for name, sid, at in (
        ("raw-perfect", "s1", "2026-11-01T00:00:00Z"),
        ("raw-fake-retry", "s2", "2026-11-02T00:00:00Z"),
        ("raw-perfect", "s3", "2026-11-03T00:00:00Z"),
    ):
        store.save_submission(conn, _report(name, sid, at), user_id="alice", created_at=at)
        row = store.load_mastery_snapshot(conn, "alice", MSP)
        print(f"  {sid} {name:16s} → P={row['mastery_p']:.4f} "
              f"证据数={row['evidence_count']} 最后提交={row['last_submission_id']}")

    for name, sid, at in (
        ("raw-fake-retry", "b1", "2026-11-01T00:00:00Z"),
        ("raw-perfect", "b2", "2026-11-02T00:00:00Z"),
    ):
        store.save_submission(conn, _report(name, sid, at), user_id="bob", created_at=at)
    conn.close()

    # ② 关连接重开：读回与重放逐位相同（B-07c 第一判据）
    _banner(2, "写 → 关连接 → 重开 → 读回 == build_series() 终值")
    again = store.connect(path)
    print(f"  PRAGMA user_version = {store.schema_version(again)}")
    for user in ("alice", "bob"):
        for skill in ("multi_step_planning", "failure_retry", "tool_param_validation"):
            snap = store.load_mastery_snapshot(again, user, skill)
            replayed = m.build_series(again, user, skill).p
            ok = snap is not None and snap["mastery_p"] == replayed
            print(f"  {user:6s} / {skill:22s} 库里={snap['mastery_p'] if snap else None} "
                  f"重放={replayed} 逐位相同={ok}")

    # ③ 故意改错 → 检测 → 重算纠正（B-07c 第三判据）
    _banner(3, "故意把快照改错 → verify 报不一致 → repair 改回权威值")
    expected = m.build_series(again, "alice", MSP).p
    with again:
        again.execute("UPDATE mastery_snapshots SET mastery_p = 0.123 "
                      "WHERE user_id = 'alice' AND skill_id = ?", (MSP,))
    check = ms.verify_one(again, "alice", MSP)
    print(f"  改错后：{check.reason} 库里={check.stored_p} 重放={check.replayed_p} "
          f"一致={check.consistent}")
    before, changed = ms.repair_one(again, "alice", MSP, at="2026-11-04T00:00:00Z")
    print(f"  重算：动过库={changed} 修后={store.load_mastery_snapshot(again, 'alice', MSP)['mastery_p']} "
          f"（重放={expected}）")
    print(f"  全库核对：{[c.reason for c in ms.verify_all(again)]}")

    # ④ 按 P 排序多个用户（地基，不是榜单）
    _banner(4, "按 P 排序多个用户：只有有序行，没有名次字段")
    for row in ms.list_ranked(again, MSP):
        print(f"  {row['user_id']:8s} P={row['mastery_p']:.4f} "
              f"字段={sorted(row.keys())}")
    print("  ↑ 这是 B-25 的查询地基；本轮不产 rank、不开榜。")

    # ⑤ v1 老库迁移：从真的 v1 结构升上来
    # 不写具体版本号：每加一版就要改一次文案，而漏改的后果是“演示输出与代码不一致”。
    _banner(5, "v1 老库迁移（逐级升到最新版本，旧报告一个字节不动）")
    again.close()
    v1path = Path(tempfile.mkdtemp()) / "legacy-v1.db"
    old = store.connect(v1path)
    old.executescript(store.SCHEMA_SQL_V1)
    old.execute("PRAGMA user_version = 1")
    store.register_skills(old)
    store.register_patterns(old)
    store.register_task(old, TASK_YAML)
    rep = _report("raw-perfect", "legacy-1", "2026-09-01T00:00:00Z")
    # 按 **v1 的列集**直插：v4 之后 save_submission() 会写 `artifacts`，而 v1 老库没有这一列。
    # 这里要构造的是"迁移前就有的历史数据"，就必须用那个版本真实存在的列。
    store.ensure_user(old, "legacy-user")
    with old:
        old.execute(
            "INSERT INTO submissions (submission_id, user_id, task_id, assert_score, "
            "detected_patterns, diagnosis, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("legacy-1", "legacy-user", rep["submission"]["task_id"],
             float(rep["score"]["ratio"]), "[]", store._dump(rep), "2026-09-01T00:00:00Z"),
        )
    names = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    print(f"  迁移前：version={store.schema_version(old)} 有快照表={'mastery_snapshots' in names} "
          f"旧报告数={store.count_submissions(old)}")
    print(f"  migrate() → {store.migrate(old)}")
    print(f"  迁移后：version={store.schema_version(old)} "
          f"旧报告逐字相同={store.load_report(old, 'legacy-1') == rep} "
          f"全库核对={[c.reason for c in ms.verify_all(old)]}")
    old.close()

    print("\n本脚本只在系统临时目录建文件，仓库内不留库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
