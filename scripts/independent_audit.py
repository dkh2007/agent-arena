"""B-07c 独立对拍器：用第二条实现路径重算 P，与库里的快照逐位对撞。

    python scripts/independent_audit.py

## 它解决什么

单元测试是「同一套代码 + 同一套测试」写出来的：测试锁的是实现的行为，
锁不住「实现与测试一起理解错」。本脚本换一条路径：

- 造数据仍走产品写路径（故意的，被测对象就是"提交后快照对不对"）；
- **独立读侧只用 sqlite3 + json + datetime**，自己从 `submissions.diagnosis` 里
  取断言、自己加权、自己排时刻、自己递推，不调用
  `build_series()` / `compute_row()` / `verify_one()` / `repair_one()` 中的任何一个；
- λ 在本文件**独立硬编码为 0.3**，不读 `LAMBDA_DEFAULT`。产品代码若改了 λ
  而这里没改，对撞会报红 —— 这正是要抓的口径漂移。
- 机械锁：进入独立读侧前把被测计算函数换成会抛异常的哨兵，
  一旦偷调就炸（不是靠注释声明"我没调"）。

## 它证明不了什么（别拿它当验收结论）

1. 报告里的 `attribution_policy` 是两条路径共用的输入；报告本身错了，两边一起错。
2. 公式口径 `P_k=(1-λ)P+λc` 也是共用的；口径该不该这样定，本脚本不判。
3. 造数据用了产品的写函数；「写侧有没有在错误时机刷新」不在独立覆盖范围内。
4. 它仍是**我写的**。要第二真相源，得让外部 AI 审，见 `docs/peer-brief-b07c.md`。

## 受控反例

光打印一堆 MATCH 是恒真断言（本项目踩过）。所以脚本故意把库改坏三次，
每一次都必须报红，再回滚恢复、确认全表重新 MATCH。
"""

from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# 造数据必须用的两个入口。下面 import 的模块**只在造数据阶段**使用。
from arena import store  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402

TASK_YAML = ROOT / "tasks" / "dbg-001-order-report.yaml"

# 独立硬编码：与产品代码刻意不共享常量。改这里等于换一条公式，对撞会报红。
LAM = 0.3


def _report(name: str, sid: str, at: str) -> dict:
    r = copy.deepcopy(run_fixture(name))
    r["submission"]["submission_id"] = sid
    r["submission"]["evaluated_at"] = at
    return r


def _build_demo_db(path: Path) -> None:
    conn = store.connect(path)
    store.init_db(conn)
    store.register_skills(conn)
    store.register_patterns(conn)
    store.register_task(conn, TASK_YAML)
    for name, sid, at, user in (
        ("raw-perfect", "s1", "2026-11-01T00:00:00Z", "alice"),
        ("raw-fake-retry", "s2", "2026-11-02T08:00:00+08:00", "alice"),
        ("raw-perfect", "s3", "2026-11-03T00:00:00Z", "alice"),
        ("raw-fake-retry", "b1", "2026-11-01T00:00:00Z", "bob"),
        ("raw-perfect", "b2", "2026-11-02T00:00:00Z", "bob"),
    ):
        store.save_submission(conn, _report(name, sid, at), user_id=user, created_at=at)
    conn.close()


# ---------------------------------------------------------------------------
# 独立读侧：以下所有函数不得 import 或调用 arena.mastery / arena.mastery_snapshot
# ---------------------------------------------------------------------------


def _instant(text: str) -> datetime:
    t = text.strip()
    if t.endswith(("Z", "z")):
        t = t[:-1] + "+00:00"
    d = datetime.fromisoformat(t)
    return d if d.tzinfo is not None else d.replace(tzinfo=timezone.utc)


def _raw_reports(conn: sqlite3.Connection, user: str) -> list[tuple[str, str, dict]]:
    rows = conn.execute(
        "SELECT submission_id, created_at, diagnosis FROM submissions WHERE user_id = ?",
        (user,),
    ).fetchall()
    return [(sid, at, json.loads(blob)) for sid, at, blob in rows]


def _independent_p(reports: list[tuple[str, str, dict]], skill: str) -> tuple[float | None, int]:
    """从报告原文独立重算 c 与 P 序列，返回 (终值, 计入的提交数)。"""
    items: list[tuple[datetime, str, float]] = []
    for sid, at, rep in reports:
        policy = rep.get("attribution_policy") or {}
        passed = counted = 0.0
        for a in rep.get("assertions") or []:
            if a.get("skill_id") != skill:
                continue
            rule = policy.get(a.get("state")) or {}
            if not rule.get("counts_toward_mastery"):
                continue
            w = a.get("weight", 0)
            if isinstance(w, bool) or not isinstance(w, (int, float)) or w <= 0:
                continue
            counted += w
            if a.get("state") == "pass":
                passed += w
        if counted <= 0:
            continue
        items.append((_instant(at), sid, passed / counted))
    items.sort(key=lambda x: (x[0], x[1]))
    p: float | None = None
    for _, _, c in items:
        p = c if p is None else (1.0 - LAM) * p + LAM * c
    return p, len(items)


def _audit(conn: sqlite3.Connection) -> list[dict]:
    users = [r[0] for r in conn.execute(
        "SELECT DISTINCT user_id FROM submissions ORDER BY user_id")]
    skills = [r[0] for r in conn.execute("SELECT skill_id FROM skills ORDER BY skill_id")]
    out: list[dict] = []
    for user in users:
        reports = _raw_reports(conn, user)
        for skill in skills:
            p, n = _independent_p(reports, skill)
            row = conn.execute(
                "SELECT mastery_p, lam, evidence_count FROM mastery_snapshots "
                "WHERE user_id = ? AND skill_id = ?", (user, skill),
            ).fetchone()
            stored = row[0] if row else None
            if p is None and stored is None:
                verdict = "OK_NONE"
            elif p is None:
                verdict = "ORPHAN"
            elif stored is None:
                verdict = "MISSING"
            elif stored == p and row[1] == LAM and row[2] == n:
                verdict = "MATCH"
            else:
                verdict = "MISMATCH"
            out.append({"user": user, "skill": skill, "independent": p,
                        "stored": stored, "lam": row[1] if row else None,
                        "evidence": n, "verdict": verdict})
    return out


def _print(rows: list[dict], title: str) -> None:
    print(f"\n=== {title} ===")
    print(f"{'用户 / 技能':<44} {'独立重算':>20} {'库里存的':>20} {'证据':>4}  判定")
    for r in rows:
        key = f"{r['user']} / {r['skill']}"
        ind = "—" if r["independent"] is None else f"{r['independent']:.16f}"
        sto = "—" if r["stored"] is None else f"{r['stored']:.16f}"
        print(f"{key:<44} {ind:>20} {sto:>20} {r['evidence']:>4}  {r['verdict']}")


def _install_sentinels() -> list[tuple[object, str, object]]:
    """把被测计算函数换成会抛异常的哨兵；返回原值供 finally 恢复。"""
    import arena.mastery as m
    import arena.mastery_snapshot as ms

    def boom(*_a, **_kw):
        raise AssertionError("独立读侧偷调了被测计算函数 —— 两条路径不再独立")

    saved = []
    for mod, name in ((m, "build_series"), (m, "replay"), (m, "skill_evidence"),
                      (ms, "compute_row"), (ms, "verify_one"), (ms, "verify_all"),
                      (ms, "repair_one"), (ms, "recompute_all")):
        saved.append((mod, name, getattr(mod, name)))
        setattr(mod, name, boom)
    return saved


def main() -> int:
    path = Path(tempfile.mkdtemp()) / "independent-audit.db"
    _build_demo_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    saved = _install_sentinels()
    try:
        base = _audit(conn)
        _print(base, "① 独立重算 vs 库里快照（哨兵已装：偷调即炸）")
        bad = [r for r in base if r["verdict"] not in ("MATCH", "OK_NONE")]
        print(f"   不一致 {len(bad)} 行 / 共 {len(base)} 行")

        target = next(r for r in base if r["verdict"] == "MATCH")
        where = "WHERE user_id = ? AND skill_id = ?"
        args = (target["user"], target["skill"])

        controls = []
        for label, sql, params, expect in (
            ("把 P 改错", "UPDATE mastery_snapshots SET mastery_p = 0.123 " + where,
             args, "MISMATCH"),
            ("删掉一行", "DELETE FROM mastery_snapshots " + where, args, "MISSING"),
            ("只改 λ（P 不变）", "UPDATE mastery_snapshots SET lam = 0.5 " + where,
             args, "MISMATCH"),
        ):
            conn.execute("SAVEPOINT negative_control")
            conn.execute(sql, params)
            rows = _audit(conn)
            hit = any(r["verdict"] == expect for r in rows)
            conn.execute("ROLLBACK TO negative_control")
            conn.execute("RELEASE negative_control")
            controls.append((label, expect, hit))
            print(f"   反例「{label}」→ 报 {expect}: {hit}")

        after = _audit(conn)
        clean = all(r["verdict"] in ("MATCH", "OK_NONE") for r in after)
        all_hit = all(hit for _, _, hit in controls)
        print(f"   回滚后全表恢复: {clean}")

        print("\n=== 结论 ===")
        print(f"   INDEPENDENT_MATCH={not bad}  NEGATIVE_CONTROLS_DETECTED={all_hit}  "
              f"RESTORED={clean}")
        print("   覆盖边界见本文件头部：共用报告与公式口径，不等于口径正确。")
        ok = not bad and all_hit and clean
    finally:
        for mod, name, original in saved:
            setattr(mod, name, original)
        conn.close()

    print("\n本脚本只在系统临时目录建文件，仓库内不留库。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
