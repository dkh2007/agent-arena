"""掌握度 P 的**快照**（v2 / B-07c）：把 `build_series()` 的结果抄进库。

## 这一步到底解决什么

`mastery.build_series()` 能把 P 从 `submissions` 里重放出来，但它要读该用户的
**全部**报告。一个人看自己的进步曲线没问题；一旦要「按 P 排序很多用户」
（排名 / 班级榜 / 教师视图），每刷新一次榜就把所有人的历史全重放一遍，
成本随人数 × 历史长度增长。所以才需要一张表把当前 P 抄下来。

## 五条设计决定（每条都有测试否得掉）

1. **重放是唯一权威，快照只是缓存。**
   `verify_*()` 永远现场重放一次再比对；不一致时**信重放**，不信库里的数。
   `repair_*()` / `recompute_all()` 是重算入口，把缓存改回权威值。
   不做"读的时候顺手改库"——那会让一次查询产生写副作用，谁在什么时候改的说不清。

2. **存的是 `P_raw`，不是衰减后的 `P_eff`。**
   B-08b 定过：衰减是「读法」（`P_eff = P_raw × R(t,S)`，由调用方给 `as_of` 现算），
   不是新状态。把 `as_of` 钉进快照等于给同一份数据造两个真相源，
   而且"这个 P 是哪一刻的"会永远说不清。见 `review-response-v1.md` §86.8 ④。

3. **没有证据就没有行，不写 0。**
   `build_series().p is None` 表示该技能一条计入证据都没有（决策 7：
   数据缺失 ≠ 能力不足）。这时若写 `mastery_p=0`，排序会把"没做过"
   排成"完全不会"。所以 `refresh_one()` 遇到 None 会**删掉**旧快照。

4. **快照自带"按什么算的"**：`lam` / `p0` / `evidence_count` /
   `last_submission_id` 一起落库。只比 P 值不够——λ 从 0.3 改成 0.5
   （B-07b 标定）时 P 可能碰巧不变，但每个数都是旧口径下算的，
   混进新榜就是静默错账。`verify_one()` 四项全比。

5. **写路径自动刷新，读路径只读取**。`store.save_submission()` 落完报告后
   刷新该用户的快照，所以正常情况下缓存与报告同步；`list_ranked()` 不重放，
   这是落字段的意义。新鲜度由 `verify_all()` 单独核——**要不要在展示前核一遍，
   归展示层（B-25，未开）决定，本模块只提供裁判**。

## 与 B-08b / B-07b 的边界

- 不改 `build_series()` 一行：P 的算法仍只有一处。
- 不改 `decay.py` 一行：衰减仍只在显式 `as_of` 时发生。
- 不标定 λ：这里照抄 `mastery.LAMBDA_DEFAULT`，它仍是**假设**。
- 不做排名页、不产出名次、不引入跨用户比较口径：那些是 B-25 的事。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from . import mastery
from . import store

# 不一致的原因词表。**只这五种**；`verify_one()` 全部从本元组解包取值，
# 词表与实现之间有强制关系，拼错会被 `test_reasons_vocabulary_is_enforced` 抓住。
REASONS = (
    "ok",                 # 一致
    "missing_snapshot",   # 重放有值、库里没行（写路径没刷新 / 行被删了）
    "stale_value",        # P 值对不上
    "stale_params",       # P 碰巧对，但 λ / p0 / 证据数 / 最后一条提交对不上
    "orphan_snapshot",    # 库里有行、重放却无证据（不该有快照）
)

# 解包而不是散写字面量：词表一旦被改错，下面所有 reason 跟着变，
# `tests/test_mastery_snapshot.py::test_reasons_vocabulary_is_enforced` 立刻报红。
# 对照 round-88 外审 F3：改之前 REASONS 在 src/ 与 tests/ 里除定义处零引用。
(REASON_OK, REASON_MISSING, REASON_STALE_VALUE, REASON_STALE_PARAMS,
 REASON_ORPHAN) = REASONS


class SnapshotError(ValueError):
    """输入不成立。不吞、不 clamp、不猜。"""


@dataclass(frozen=True)
class SnapshotCheck:
    """一次「重放 vs 快照」的核对结果。`consistent` 是唯一裁决位。"""

    user_id: str
    skill_id: str
    stored_p: float | None
    replayed_p: float | None
    reason: str
    consistent: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "skill_id": self.skill_id,
            "stored_p": self.stored_p,
            "replayed_p": self.replayed_p,
            "reason": self.reason,
            "consistent": self.consistent,
        }


def _check_ids(user_id: str, skill_id: str) -> None:
    if not isinstance(user_id, str) or not user_id:
        raise SnapshotError(f"user_id 必须是非空字符串（收到 {user_id!r}）")
    if not isinstance(skill_id, str) or not skill_id:
        raise SnapshotError(f"skill_id 必须是非空字符串（收到 {skill_id!r}）")


def compute_row(
    conn: sqlite3.Connection,
    user_id: str,
    skill_id: str,
    *,
    lam: float = mastery.LAMBDA_DEFAULT,
    p0: float | None = None,
    at: str | None = None,
) -> dict | None:
    """纯读：从 `submissions` 重放出该 (user, skill) 当前应落的快照行。

    返回 `None` 表示"没有证据 ⇒ 不该有快照"（决定 3）。本函数**不写库**。
    """
    _check_ids(user_id, skill_id)
    series = mastery.build_series(conn, user_id, skill_id, lam=lam, p0=p0)
    if series.p is None:
        return None
    last = series.steps[-1].submission_id if series.steps else None
    return {
        "user_id": user_id,
        "skill_id": skill_id,
        "mastery_p": float(series.p),
        "lam": float(lam),
        "p0": p0,
        "evidence_count": len(series.steps),
        "last_submission_id": last,
        "updated_at": at,
    }


def refresh_one(
    conn: sqlite3.Connection,
    user_id: str,
    skill_id: str,
    *,
    lam: float = mastery.LAMBDA_DEFAULT,
    p0: float | None = None,
    at: str | None = None,
) -> dict | None:
    """重算并写一条。返回写入的行；无证据时删旧行并返回 `None`。"""
    row = compute_row(conn, user_id, skill_id, lam=lam, p0=p0, at=at)
    if row is None:
        store.delete_mastery_snapshot(conn, user_id, skill_id)
        return None
    store.save_mastery_snapshot(conn, row)
    return row


def refresh_user(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    lam: float = mastery.LAMBDA_DEFAULT,
    p0: float | None = None,
    at: str | None = None,
    skill_ids: list[str] | None = None,
) -> list[dict]:
    """刷新一个用户的全部已注册技能。返回实际写入的行（无证据的不算）。

    以 `skills` 表为准而不是"报告里出现过的技能"：一份提交可能把某个技能的
    证据从有变无（例如历史被作废），只扫报告会漏掉该删的旧快照。
    """
    skills = store.list_skill_ids(conn) if skill_ids is None else skill_ids
    rows = []
    for skill_id in skills:
        row = refresh_one(conn, user_id, skill_id, lam=lam, p0=p0, at=at)
        if row is not None:
            rows.append(row)
    return rows


def recompute_all(
    conn: sqlite3.Connection,
    *,
    lam: float = mastery.LAMBDA_DEFAULT,
    p0: float | None = None,
    at: str | None = None,
) -> int:
    """全库重算入口（迁移回填、"故意改错后修回来"都走这里）。返回写入行数。

    扫描范围 = 已注册用户 × 已注册技能。对每一对调 `refresh_one()`，
    重放无证据就删行，所以**范围内的**孤儿行会被清掉。

    **已知边界（round-88 外审 F2，本地实测复核）**：范围之外的孤儿行——
    「技能未注册」或「用户未注册」的快照行——`verify_all()` 报得出 `orphan_snapshot`，
    但本函数碰不到、也修不掉。要不要扩成全表清理属产品口径
    （技能被删时，快照可能是它唯一的历史痕迹），登记 BACKLOG B-07d，拍板前不改。
    """
    written = 0
    skills = store.list_skill_ids(conn)
    for user_id in store.list_user_ids(conn):
        written += len(refresh_user(conn, user_id, lam=lam, p0=p0, at=at,
                                    skill_ids=skills))
    return written


def verify_one(
    conn: sqlite3.Connection,
    user_id: str,
    skill_id: str,
    *,
    lam: float = mastery.LAMBDA_DEFAULT,
    p0: float | None = None,
) -> SnapshotCheck:
    """**以重放为准**核一条。纯读，绝不写库。

    P 用 `==` 逐位比：重放是确定性的，同一个函数对同一批报告算两次
    必须给出同一个 float；SQLite REAL 是 IEEE-754 双精度，写入读出不丢位。
    这里若需要容差，说明重放本身不确定——那是 bug，不该靠容差糊过去。
    """
    _check_ids(user_id, skill_id)
    row = compute_row(conn, user_id, skill_id, lam=lam, p0=p0)
    stored = store.load_mastery_snapshot(conn, user_id, skill_id)
    stored_p = None if stored is None else stored["mastery_p"]
    replayed_p = None if row is None else row["mastery_p"]

    if row is None:
        reason = REASON_OK if stored is None else REASON_ORPHAN
        return SnapshotCheck(user_id, skill_id, stored_p, None, reason,
                             stored is None)
    if stored is None:
        return SnapshotCheck(user_id, skill_id, None, replayed_p,
                             REASON_MISSING, False)

    if stored_p != replayed_p:
        return SnapshotCheck(user_id, skill_id, stored_p, replayed_p,
                             REASON_STALE_VALUE, False)

    same_params = (
        stored["lam"] == row["lam"]
        and stored["p0"] == row["p0"]
        and stored["evidence_count"] == row["evidence_count"]
        and stored["last_submission_id"] == row["last_submission_id"]
    )
    if not same_params:
        return SnapshotCheck(user_id, skill_id, stored_p, replayed_p,
                             REASON_STALE_PARAMS, False)
    return SnapshotCheck(user_id, skill_id, stored_p, replayed_p, REASON_OK, True)


def verify_all(
    conn: sqlite3.Connection,
    *,
    lam: float = mastery.LAMBDA_DEFAULT,
    p0: float | None = None,
) -> list[SnapshotCheck]:
    """全库新鲜度裁判。返回所有 (user, skill) 的核对结果，顺序稳定。

    覆盖范围 = 已注册技能 × 已有用户，再加上**库里多出来的孤儿行**：
    只按前两者的笛卡尔积扫，会漏掉"技能被删了但快照还在"这种行。
    """
    seen: set[tuple[str, str]] = set()
    checks: list[SnapshotCheck] = []
    pairs: list[tuple[str, str]] = [
        (u, s) for u in store.list_user_ids(conn) for s in store.list_skill_ids(conn)
    ]
    pairs.extend(
        (r["user_id"], r["skill_id"]) for r in store.list_mastery_snapshots(conn)
    )
    for user_id, skill_id in pairs:
        key = (user_id, skill_id)
        if key in seen:
            continue
        seen.add(key)
        checks.append(verify_one(conn, user_id, skill_id, lam=lam, p0=p0))
    return checks


def repair_one(
    conn: sqlite3.Connection,
    user_id: str,
    skill_id: str,
    *,
    lam: float = mastery.LAMBDA_DEFAULT,
    p0: float | None = None,
    at: str | None = None,
) -> tuple[SnapshotCheck, bool]:
    """核一条；不一致就重算覆盖。返回 `(核对结果, 是否动过库)`。

    这是"故意把快照改错 → 重算能纠正"那条判据的直接入口。
    """
    before = verify_one(conn, user_id, skill_id, lam=lam, p0=p0)
    if before.consistent:
        return before, False
    refresh_one(conn, user_id, skill_id, lam=lam, p0=p0, at=at)
    return before, True


def list_ranked(
    conn: sqlite3.Connection,
    skill_id: str,
    *,
    limit: int | None = None,
) -> list[dict]:
    """按 P 从高到低读快照（B-25 的查询地基）。**只读缓存、不重放、不给名次**。

    刻意不返回 `rank` 字段：名次是产品口径（并列怎么算、是否展示），
    排名没开就不许先在接口里长出来。调用方拿到的是有序行列表。
    """
    if not isinstance(skill_id, str) or not skill_id:
        raise SnapshotError(f"skill_id 必须是非空字符串（收到 {skill_id!r}）")
    rows = store.list_mastery_snapshots(conn, skill_id=skill_id, order_by_p=True)
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise SnapshotError(f"limit 必须是非负整数（收到 {limit!r}）")
        rows = rows[:limit]
    return rows
