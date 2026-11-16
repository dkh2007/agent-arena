"""P 快照（v2 / B-07c）的约束测试。

锁的是 `src/arena/mastery_snapshot.py` 文档里的五条设计决定，
以及 `docs/BACKLOG.md` B-07c 的四条做完判据。每条都要能被一份红测试否掉：

  **判据**
  ① `save_submission` 后 P 落库，写 → 关连接 → 重开 → 读回与 `build_series()` 终值逐位相同；
  ② `PRAGMA user_version` 升到 2 且有迁移函数（真 v1 库 from=1、旧数据不丢、幂等）；
  ③ 故意把快照改错 → 重算能纠正；
  ④ 快照是缓存，与报告不一致时以重放为准。

  **边界**
  ⑤ 存的是 `P_raw` 不是 `P_eff`；⑥ 没证据不写 0（决策 7）；
  ⑦ 参数漂移要报 `stale_params`；⑧ 排序不给名次字段、平手稳定；
  ⑨ 快照表独立，不碰 `user_skill_states`（B-08b 的"没卡≠忘光了"契约）。
"""

from __future__ import annotations

import copy
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import decay  # noqa: E402
from arena import mastery as m  # noqa: E402
from arena import mastery_snapshot as ms  # noqa: E402
from arena import scheduler as sch  # noqa: E402
from arena import store  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402

TASK_YAML = ROOT / "tasks" / "dbg-001-order-report.yaml"
MSP = "multi_step_planning"
TPV = "tool_param_validation"
AT1 = "2026-11-01T00:00:00Z"
AT2 = "2026-11-02T00:00:00Z"
AT3 = "2026-11-03T00:00:00Z"


def _report(name: str, *, submission_id: str, at: str) -> dict:
    report = copy.deepcopy(run_fixture(name))
    report["submission"]["submission_id"] = submission_id
    report["submission"]["evaluated_at"] = at
    return report


def _setup(conn) -> None:
    store.init_db(conn)
    store.register_skills(conn)
    store.register_patterns(conn)
    store.register_task(conn, TASK_YAML)


@pytest.fixture()
def db(tmp_path):
    conn = store.connect(tmp_path / "snapshot.db")
    _setup(conn)
    yield conn
    conn.close()


def _seed(conn, user: str = "u", *, pairs=(("raw-perfect", "s1", AT1),)) -> None:
    for name, sid, at in pairs:
        store.save_submission(conn, _report(name, submission_id=sid, at=at),
                              user_id=user, created_at=at)


# --- 判据 ①：提交后落库 + 重启读回逐位相同 -----------------------------------

def test_criterion_save_then_reconnect_reads_bitwise_equal_p(tmp_path):
    """B-07c 的第一判据：写 → 关连接 → 重开 → 读回 == `build_series()` 终值。

    用 `==` 不用 `approx`：两边都来自同一个确定性重放，浮点该逐位相同。
    若哪天需要容差，说明重放本身不确定，那是 bug，不该靠容差糊过去。
    """
    path = tmp_path / "persist.db"
    conn = store.connect(path)
    _setup(conn)
    _seed(conn, pairs=(
        ("raw-perfect", "s1", AT1),
        ("raw-fake-retry", "s2", AT2),
    ))
    assert len(store.list_mastery_snapshots(conn)) == 3  # 三个技能各一行
    conn.close()

    again = store.connect(path)
    try:
        for skill in (MSP, "failure_retry", TPV):
            snap = store.load_mastery_snapshot(again, "u", skill)
            assert snap is not None, f"{skill} 的快照没落库"
            assert snap["mastery_p"] == m.build_series(again, "u", skill).p
        assert all(c.consistent for c in ms.verify_all(again))
    finally:
        again.close()


def test_save_submission_refreshes_snapshots_by_default(db):
    """提交一次就刷新一次：第二份报告之后 P 必须变，不是第一份的旧值。"""
    _seed(db, pairs=(("raw-perfect", "s1", AT1),))
    first = store.load_mastery_snapshot(db, "u", MSP)["mastery_p"]
    _seed(db, pairs=(("raw-fake-retry", "s2", AT2),))
    second = store.load_mastery_snapshot(db, "u", MSP)["mastery_p"]
    assert first == 1.0
    assert second == pytest.approx(0.7)
    assert second == m.build_series(db, "u", MSP).p


def test_refresh_mastery_false_leaves_the_cache_untouched(db):
    """关掉刷新能造出"过期快照"——`verify_*` 的负例要靠它，不能靠手改库。"""
    _seed(db, pairs=(("raw-perfect", "s1", AT1),))
    before = store.load_mastery_snapshot(db, "u", MSP)
    _seed_without_refresh = _report("raw-fake-retry", submission_id="s2", at=AT2)
    store.save_submission(db, _seed_without_refresh, user_id="u",
                          created_at=AT2, refresh_mastery=False)
    assert store.load_mastery_snapshot(db, "u", MSP) == before
    check = ms.verify_one(db, "u", MSP)
    assert check.consistent is False and check.reason == "stale_value"


# --- 判据 ②：v2 迁移 ---------------------------------------------------------

def _v1_db(path: Path):
    """造一个**真的** v1 老库：只执行 v1 的 DDL、版本写 1、落一份旧提交。"""
    conn = store.connect(path)
    conn.executescript(store.SCHEMA_SQL_V1)
    conn.execute("PRAGMA user_version = 1")
    store.register_skills(conn)
    store.register_patterns(conn)
    store.register_task(conn, TASK_YAML)
    return conn


def _insert_legacy_submission(conn, report: dict, *, user_id: str, at: str) -> None:
    """按 **v1 的列集**直插一条提交——模拟"上个版本的服务写下的历史数据"。

    v4 之后 `save_submission()` 会写 `artifacts` 列，而 v1 老库没有这一列，
    所以这里不能借它：迁移测试要证明的是"迁移前就有的数据一个字节不动"，
    构造那段数据就必须用那个版本真实存在的列。
    """
    sub = report["submission"]
    store.ensure_user(conn, user_id)  # save_submission 平时会做这一步，直插要自己补
    with conn:
        conn.execute(
            "INSERT INTO submissions (submission_id, user_id, task_id, assert_score, "
            "detected_patterns, diagnosis, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sub["submission_id"], user_id, sub["task_id"], float(report["score"]["ratio"]),
             "[]", store._dump(report), at),
        )


def test_criterion_migrate_from_real_v1_keeps_data_and_backfills(tmp_path):
    """迁移必须从一个"没有新表"的 v1 库起跑，不是拿新库改个 PRAGMA 假装。"""
    path = tmp_path / "v1.db"
    conn = _v1_db(path)
    report = _report("raw-perfect", submission_id="old-1", at=AT1)
    _insert_legacy_submission(conn, report, user_id="old-user", at=AT1)
    before = store.count_submissions(conn)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "mastery_snapshots" not in names
    assert "arbitration_queue" not in names
    assert store.schema_version(conn) == 1

    result = store.migrate(conn)
    # v1 → 最新版本要**逐级**跑：v2 建快照表 + 回填，v3 建仲裁队列表，v4 加产物列，
    # v5 建重评表。一次跳到最新那段是错的写法——这里锁住"每一级的动作都在"。
    assert result["from"] == 1 and result["to"] == store.SCHEMA_VERSION
    assert result["added_tables"] == ["mastery_snapshots", "arbitration_queue",
                                      "submissions.artifacts", "reappraisals"]
    assert result["backfilled"] == 3
    assert store.schema_version(conn) == store.SCHEMA_VERSION == 5
    assert store.count_submissions(conn) == before
    assert store.load_report(conn, "old-1") == report          # 旧报告一个字节没动
    # 旧提交的产物补不回来 —— 迁移只加列，不给历史数据编造产物。
    assert store.load_submission(conn, "old-1")["artifacts"] is None
    assert len(store.list_mastery_snapshots(conn)) == 3        # 回填了三个技能
    assert all(c.consistent for c in ms.verify_all(conn))
    conn.close()


def test_migrate_is_idempotent_and_never_downgrades(db):
    # db 夹具建的是当前版本的库，所以这里就是"到了目标就什么都不做"。
    assert store.migrate(db) == {"from": store.SCHEMA_VERSION, "to": store.SCHEMA_VERSION,
                                 "added_tables": [], "backfilled": 0}
    assert store.schema_version(db) == store.SCHEMA_VERSION
    with db:
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(store.StoreError, match="拒绝降级"):
        store.migrate(db)
    assert store.schema_version(db) == 99      # 抛错时没偷偷改库


def test_migrate_rejects_a_bogus_target(db):
    for bad in (0, -1, True, 1.5, "2"):
        with pytest.raises(store.StoreError, match="正整数"):
            store.migrate(db, target=bad)


def test_migrate_rejects_a_future_target(db):
    """round-88 外审 F4：以前 `target=5` 会写出 `user_version=5`，
    而库里其实只有 v1+v2 的 8 张表。版本号不能比本代码认识的结构还新。
    """
    with pytest.raises(store.StoreError, match="看不懂"):
        store.migrate(db, target=store.SCHEMA_VERSION + 1)
    assert store.schema_version(db) == store.SCHEMA_VERSION


def test_init_db_on_a_v1_file_does_not_leave_a_half_migrated_state(tmp_path):
    """老库被 `init_db()` 重新打开时，不能出现"表有了、PRAGMA 还是 1"。"""
    path = tmp_path / "reopen.db"
    conn = _v1_db(path)
    conn.close()
    again = store.connect(path)
    _setup(again)
    assert store.schema_version(again) == store.SCHEMA_VERSION
    names = {r[0] for r in again.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"mastery_snapshots", "arbitration_queue"} <= names
    again.close()


# --- 判据 ③④：改错 → 重算纠正；以重放为准 -----------------------------------

def test_criterion_corrupted_snapshot_is_detected_and_repaired(db):
    """把库里的 P 故意改错，`verify` 必须报不一致，`repair` 必须改回权威值。"""
    _seed(db, pairs=(("raw-perfect", "s1", AT1), ("raw-fake-retry", "s2", AT2)))
    expected = m.build_series(db, "u", MSP).p
    with db:
        db.execute("UPDATE mastery_snapshots SET mastery_p = 0.123 "
                   "WHERE user_id = 'u' AND skill_id = ?", (MSP,))

    check = ms.verify_one(db, "u", MSP)
    assert check.consistent is False
    assert check.reason == "stale_value"
    assert check.stored_p == 0.123 and check.replayed_p == expected

    before, changed = ms.repair_one(db, "u", MSP, at=AT3)
    assert changed is True and before.reason == "stale_value"
    assert store.load_mastery_snapshot(db, "u", MSP)["mastery_p"] == expected
    assert ms.verify_one(db, "u", MSP).consistent is True


def test_recompute_all_repairs_every_corrupted_row(db):
    """全库重算入口：一次把多行错值都改回来，且幂等。"""
    _seed(db, pairs=(("raw-perfect", "s1", AT1), ("raw-fake-retry", "s2", AT2)))
    with db:
        db.execute("UPDATE mastery_snapshots SET mastery_p = 0.0")
    assert not any(c.consistent for c in ms.verify_all(db))
    assert ms.recompute_all(db, at=AT3) == 3
    assert all(c.consistent for c in ms.verify_all(db))
    assert ms.recompute_all(db, at=AT3) == 3


def test_tiny_drift_is_still_detected_without_any_tolerance(db):
    """故意只差 0.0001：`verify` 用逐位比较，不许拿"误差可忽略"当借口放行。

    快照是排序依据，一个几乎看不出的偏差足以让两个并列的人换个位置；
    而且重放是确定性的，本就不该有偏差——有偏差就是有人动过库或算法。
    """
    _seed(db, pairs=(("raw-perfect", "s1", AT1), ("raw-fake-retry", "s2", AT2)))
    expected = m.build_series(db, "u", MSP).p
    with db:
        db.execute("UPDATE mastery_snapshots SET mastery_p = ? "
                   "WHERE user_id = 'u' AND skill_id = ?", (expected + 0.0001, MSP))
    check = ms.verify_one(db, "u", MSP)
    assert check.consistent is False and check.reason == "stale_value"


def test_snapshot_metadata_records_what_was_replayed(db):
    """`lam` / `p0` / `evidence_count` / `last_submission_id` 不是装饰列。

    它们让"按哪批报告、哪套参数算的"可复核；B-07b 标定 λ 时，
    全靠这几列才能发现旧口径的快照混进了新榜。
    """
    _seed(db, pairs=(("raw-perfect", "s1", AT1), ("raw-fake-retry", "s2", AT2)))
    row = store.load_mastery_snapshot(db, "u", MSP)
    series = m.build_series(db, "u", MSP)
    assert row["lam"] == m.LAMBDA_DEFAULT == 0.3
    assert row["p0"] is None
    assert row["evidence_count"] == len(series.steps) == 2
    assert row["last_submission_id"] == series.steps[-1].submission_id == "s2"


def test_verify_is_pure_read(db):
    """核对绝不能顺手改库——否则一次查询就产生写副作用，谁改的说不清。"""
    _seed(db, pairs=(("raw-perfect", "s1", AT1),))
    with db:
        db.execute("UPDATE mastery_snapshots SET mastery_p = 0.1 "
                   "WHERE user_id = 'u' AND skill_id = ?", (MSP,))
    rows = store.list_mastery_snapshots(db)
    ms.verify_one(db, "u", MSP)
    ms.verify_all(db)
    assert store.list_mastery_snapshots(db) == rows


def test_replay_wins_when_only_the_parameters_drift(db):
    """λ 改成 0.5 后 P 可能碰巧不变，但快照仍是旧口径算的 ⇒ 必须报 `stale_params`。

    只比 P 值会漏掉这一整类静默错账（B-07b 标定 λ 时正是这种情形）。
    """
    _seed(db, pairs=(("raw-perfect", "s1", AT1),))
    assert ms.verify_one(db, "u", MSP, lam=0.3).consistent is True
    check = ms.verify_one(db, "u", MSP, lam=0.5)
    assert check.consistent is False and check.reason == "stale_params"


def test_missing_snapshot_is_reported_not_silently_fixed(db):
    """有报告却没快照 = 写路径漏了，报 `missing_snapshot`，不伪造 0。"""
    _seed(db, pairs=(("raw-perfect", "s1", AT1),))
    store.delete_mastery_snapshot(db, "u", MSP)
    check = ms.verify_one(db, "u", MSP)
    assert check.reason == "missing_snapshot" and check.stored_p is None
    assert check.replayed_p == 1.0
    assert ms.repair_one(db, "u", MSP)[1] is True
    assert ms.verify_one(db, "u", MSP).consistent is True


def test_orphan_snapshot_is_cleaned_by_refresh(db):
    """库里有行、重放却无证据 ⇒ `orphan_snapshot`；重算会把它删掉。"""
    store.save_mastery_snapshot(db, {
        "user_id": "ghost", "skill_id": MSP, "mastery_p": 0.9,
        "lam": 0.3, "p0": None, "evidence_count": 1,
        "last_submission_id": "gone", "updated_at": AT1,
    })
    check = ms.verify_one(db, "ghost", MSP)
    assert check.reason == "orphan_snapshot" and check.replayed_p is None
    assert ms.refresh_one(db, "ghost", MSP) is None
    assert store.load_mastery_snapshot(db, "ghost", MSP) is None
    assert ms.verify_one(db, "ghost", MSP).consistent is True


def test_verify_all_covers_orphan_rows_outside_the_user_skill_product(db):
    """只按 users×skills 扫会漏掉"库外被人塞进来的"幽灵行。

    **一处实测纠正**：我原想写"技能被删了但快照还在"，但这个库的外键
    （`mastery_snapshots.skill_id → skills`）让这种行根本插不进来。所以改成
    临时关掉外键、直接 INSERT 一行幽灵快照——这恰好也是"缓存被人在库外改过"
    的真实形态。把 `verify_all()` 里那段 `pairs.extend(...)` 删掉，本条会红。
    """
    db.execute("PRAGMA foreign_keys = OFF")
    with db:
        db.execute(
            "INSERT INTO mastery_snapshots (user_id, skill_id, mastery_p, lam, p0, "
            "evidence_count, last_submission_id, updated_at) "
            "VALUES ('ghost', 'phantom_skill', 0.9, 0.3, NULL, 1, 'gone', ?)",
            (AT1,),
        )
    db.execute("PRAGMA foreign_keys = ON")
    reasons = [c.reason for c in ms.verify_all(db)]
    assert "orphan_snapshot" in reasons
    assert ("ghost", "phantom_skill") in {
        (c.user_id, c.skill_id) for c in ms.verify_all(db)
    }


# --- 边界 ⑤⑥：P_raw、没证据不写 0 -------------------------------------------

def test_snapshot_stores_p_raw_not_decayed_p_effective(db):
    """**B-08b 契约**：衰减是读法，不是新状态。落库的必须是 P_raw。

    若把 P_eff 写进快照，同一份报告会因为"谁在什么时候读的"产生不同历史，
    正是 review-response §86.8 ④ 列的反证条件。
    """
    _seed(db, pairs=(("raw-perfect", "s1", AT1),))
    card = sch.new_card("u", MSP, at=AT1)
    card, _ = sch.review(card, 0.4, at=AT1)
    store.save_fsrs_cards(db, "u", {MSP: card.to_dict()})

    series, dec = m.decayed_at(db, "u", MSP, as_of="2026-12-01T00:00:00Z")
    assert dec.applied is True and dec.p_effective < series.p
    stored = store.load_mastery_snapshot(db, "u", MSP)["mastery_p"]
    assert stored == series.p == dec.p_raw
    assert stored != dec.p_effective
    assert decay.retrievability(1.0, 30) < 1.0  # 曲线确实会掉，不是这一条没测到


def test_no_evidence_writes_no_row_instead_of_zero(db):
    """决策 7：没有计入证据 ⇒ 没这行，不写 0（0 会把"没做过"排成"完全不会"）。"""
    assert ms.refresh_one(db, "empty-user", TPV) is None
    assert store.load_mastery_snapshot(db, "empty-user", TPV) is None
    assert ms.verify_one(db, "empty-user", TPV).consistent is True
    assert store.list_mastery_snapshots(db, skill_id=TPV) == []


def test_compute_row_is_read_only(db):
    """重算的"算"这一步不许写库，写是 `refresh_*` 的专属动作。"""
    _seed(db, pairs=(("raw-perfect", "s1", AT1),))
    rows = store.list_mastery_snapshots(db)
    assert ms.compute_row(db, "u", MSP) is not None
    assert store.list_mastery_snapshots(db) == rows


# --- 边界 ⑦⑧：排序、参数、输入校验 -------------------------------------------

def test_ranked_is_sorted_stable_and_has_no_rank_field(db):
    """排序是查询地基，不是榜单：不给名次字段，平手按 user_id 稳定。"""
    _seed(db, user="b", pairs=(("raw-perfect", "b1", AT1),))
    _seed(db, user="a", pairs=(("raw-perfect", "a1", AT1),))
    _seed(db, user="c", pairs=(("raw-fake-retry", "c1", AT1),))

    rows = ms.list_ranked(db, MSP)
    assert [r["user_id"] for r in rows] == ["a", "b", "c"]
    assert rows[0]["mastery_p"] == rows[1]["mastery_p"] == 1.0
    assert rows[2]["mastery_p"] == m.build_series(db, "c", MSP).p
    assert rows[2]["mastery_p"] < 1.0
    # round-88 外审 F8：原来只锁 "rank" 这个字面键名，返回里塞个 "rank_no"
    # 照样全绿（本地实跑变异：27 passed）。改成锁整个键集合，悄悄加列也会红。
    expected_cols = {
        "user_id", "skill_id", "mastery_p", "lam", "p0",
        "evidence_count", "last_submission_id", "updated_at",
    }
    assert all(set(r) == expected_cols for r in rows)
    assert all("rank" not in r for r in rows)
    assert [r["user_id"] for r in ms.list_ranked(db, MSP, limit=2)] == ["a", "b"]
    assert ms.list_ranked(db, MSP, limit=0) == []


def test_list_ranked_rejects_bad_limit(db):
    for bad in (-1, True, 1.5, "2"):
        with pytest.raises(ms.SnapshotError, match="非负整数"):
            ms.list_ranked(db, MSP, limit=bad)


def test_snapshot_rejects_out_of_range_or_bad_types(db):
    base = {"user_id": "u", "skill_id": MSP, "mastery_p": 0.5, "lam": 0.3,
            "p0": None, "evidence_count": 1, "last_submission_id": "s1",
            "updated_at": AT1}
    for field, bad in (("mastery_p", 1.5), ("mastery_p", -0.1),
                       ("mastery_p", True), ("mastery_p", "0.5"),
                       ("lam", 0.0), ("lam", 1.0), ("evidence_count", -1),
                       ("evidence_count", 1.5)):
        row = dict(base, **{field: bad})
        with pytest.raises(store.StoreError):
            store.save_mastery_snapshot(db, row)


def test_snapshot_foreign_keys_are_enforced(db):
    """没注册的技能不许写快照——否则外键成装饰，榜上会出现幽灵技能。"""
    row = {"user_id": "u", "skill_id": "not_a_skill", "mastery_p": 1.0,
           "lam": 0.3, "p0": None, "evidence_count": 1,
           "last_submission_id": "s1", "updated_at": AT1}
    with pytest.raises(store.StoreError, match="未注册"):
        store.save_mastery_snapshot(db, row)


def test_upsert_overwrites_because_a_cache_has_no_history(db):
    """缓存覆盖是正确语义：同一 (user, skill) 第二次写不该留两条。"""
    row = {"user_id": "u", "skill_id": MSP, "mastery_p": 0.4, "lam": 0.3,
           "p0": None, "evidence_count": 1, "last_submission_id": "s1",
           "updated_at": AT1}
    store.save_mastery_snapshot(db, row)
    store.save_mastery_snapshot(db, dict(row, mastery_p=0.8, evidence_count=2))
    assert len(store.list_mastery_snapshots(db, skill_id=MSP)) == 1
    assert store.load_mastery_snapshot(db, "u", MSP)["mastery_p"] == 0.8


def test_bad_ids_are_rejected(db):
    with pytest.raises(ms.SnapshotError, match="user_id"):
        ms.compute_row(db, "", MSP)
    with pytest.raises(ms.SnapshotError, match="skill_id"):
        ms.list_ranked(db, "")


# --- 边界 ⑨：与 FSRS 卡片表隔离 ----------------------------------------------

def test_snapshot_never_touches_the_fsrs_card_table(db):
    """B-08b 的 `stability is None ⇒ None` 契约靠 `user_skill_states` 的空/有区分。

    往那张表塞 P 会把"空卡"和"没卡"混成一件事，所以快照必须独立成表。
    """
    _seed(db, pairs=(("raw-perfect", "s1", AT1),))
    cols_before = [r[1] for r in db.execute("PRAGMA table_info(user_skill_states)")]
    rows_before = db.execute("SELECT * FROM user_skill_states").fetchall()
    ms.recompute_all(db)
    assert [r[1] for r in db.execute("PRAGMA table_info(user_skill_states)")] == cols_before
    assert db.execute("SELECT * FROM user_skill_states").fetchall() == rows_before


def test_v1_ddl_really_lacks_the_snapshot_table():
    """防止有人把新版本的表偷偷加回旧的分段，让迁移测试变成假测试。

    每加一张表就要在这里多守一条：只守第一张的话，下一次加表时
    "v1 库里居然已经有了"这种错会悄悄过关。
    """
    assert "mastery_snapshots" not in store.SCHEMA_SQL_V1
    assert "mastery_snapshots" in store.SCHEMA_SQL_V2
    assert "arbitration_queue" not in store.SCHEMA_SQL_V1
    assert "arbitration_queue" not in store.SCHEMA_SQL_V2
    assert "arbitration_queue" in store.SCHEMA_SQL_V3
    assert "reappraisals" not in store.SCHEMA_SQL_V1
    assert "reappraisals" not in store.SCHEMA_SQL_V2
    assert "reappraisals" not in store.SCHEMA_SQL_V3
    assert "reappraisals" in store.SCHEMA_SQL_V5
    assert store.SCHEMA_SQL == (store.SCHEMA_SQL_V1 + store.SCHEMA_SQL_V2
                                + store.SCHEMA_SQL_V3 + store.SCHEMA_SQL_V5)


# --- round-88 外部回件：三条新锁 ----------------------------------------------

def test_reasons_vocabulary_is_enforced(db):
    """外审 F3：改之前 `REASONS` 在 src/ 与 tests/ 里除定义处零引用，
    注释还写「六种」而实际 5 种。现在 `verify_one()` 全部从词表解包取值：
    把任一取值拼错，下面第一条集合断言就会红，词表不再是摆设。
    """
    assert len(ms.REASONS) == 5
    assert set(ms.REASONS) == {
        "ok", "missing_snapshot", "stale_value", "stale_params", "orphan_snapshot"
    }
    _seed(db, pairs=(("raw-perfect", "s1", AT1),))
    assert {c.reason for c in ms.verify_all(db)} <= set(ms.REASONS)
    with db:
        db.execute("UPDATE mastery_snapshots SET mastery_p = 0.0")
    assert ms.verify_one(db, "u", MSP).reason == ms.REASON_STALE_VALUE
    assert ms.verify_one(db, "u", MSP).reason in ms.REASONS


def test_orphan_rows_outside_registered_scope_are_detected_but_not_repaired(db):
    """外审 F2 的**现状锁**（已知缺陷，B-07d 修复时把最后一句反转）。

    `verify_all()` 报得出范围外孤儿行，`recompute_all()` 却只扫
    已注册用户 × 已注册技能。本地复现比回件多造了一类：
    「用户未注册」与「技能未注册」两类都修不掉（回件只写了后者）。
    """
    db.execute("PRAGMA foreign_keys = OFF")
    with db:
        db.execute("INSERT INTO mastery_snapshots VALUES (?,?,?,?,?,?,?,?)",
                   ("u", "phantom_skill", 0.9, 0.3, None, 1, "gone", AT1))
        db.execute("INSERT INTO mastery_snapshots VALUES (?,?,?,?,?,?,?,?)",
                   ("ghost_user", MSP, 0.9, 0.3, None, 1, "gone", AT1))
    db.execute("PRAGMA foreign_keys = ON")
    assert "orphan_snapshot" in {c.reason for c in ms.verify_all(db)}
    before = {tuple(r) for r in db.execute("SELECT user_id, skill_id FROM mastery_snapshots")}
    ms.recompute_all(db)
    after = {tuple(r) for r in db.execute("SELECT user_id, skill_id FROM mastery_snapshots")}
    assert before == after, "现状：范围外孤儿行修不掉；B-07d 修复后把本断言反转"


def test_refresh_failure_leaves_the_submission_committed_without_snapshot(db, monkeypatch):
    """外审 T6 的**现状锁**（已知缺陷，B-07d 修复时反转本测试）。

    `save_submission()` 的 INSERT 与快照刷新不在同一事务：刷新抛异常时，
    提交已落库、快照为空。真修要把两者放进同一事务，注意 `refresh_*`
    内部各自还有 `with conn:`，得先拆掉嵌套提交，不能只在外层再套一个。
    """
    def boom(*args, **kwargs):
        raise RuntimeError("模拟刷新失败")

    monkeypatch.setattr(ms, "refresh_user", boom)
    with pytest.raises(RuntimeError, match="模拟刷新失败"):
        store.save_submission(db, _report("raw-perfect", submission_id="half", at=AT1),
                              user_id="u", created_at=AT1)
    assert db.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM mastery_snapshots").fetchone()[0] == 0
