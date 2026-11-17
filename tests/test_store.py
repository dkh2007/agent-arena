"""状态层落库的约束测试（round-75，M1 第一步）。

锁的是 `src/arena/store.py` 文档里那四条设计决定，每条都要能被一份红测试否掉，
否则它们只是注释里的愿望：

  ① `user_id` 必须显式给，不从 `agent_id` 推——缺了就报错，不猜；
  ② `detected_patterns` 是派生列，不进报告、只认 `state=fail`（决策 7：缺数据不是能力不足）；
  ③ 重复 `submission_id` 抛错，不覆盖旧诊断；
  ④ 分数只落 `score.ratio`，反事实分只在 `diagnosis` 里。

另外三条地基判据：建表幂等、外键真的生效、**关掉连接重开后数据还在**——
最后一条是"持久化"这个词唯一的实证，内存里跑通不算。
"""

from __future__ import annotations

import copy
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import store  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402

TASK_YAML = ROOT / "tasks" / "dbg-001-order-report.yaml"
ALL_FIXTURES = ["raw-perfect", "raw-empty", "raw-no-file", "raw-no-trace", "raw-fake-retry"]
USER = "user-test"


@pytest.fixture()
def db(tmp_path):
    """建好一套可直接落库的库：表 + 3 技能 + 14 模式 + 1 任务。"""
    conn = store.connect(tmp_path / "arena.db")
    store.init_db(conn)
    store.register_skills(conn)
    store.register_patterns(conn)
    store.register_task(conn, TASK_YAML)
    yield conn
    conn.close()


# --- 地基：建表、外键、真的落了盘 -------------------------------------------------

def test_init_db_is_idempotent_and_creates_eleven_tables(tmp_path):
    """建表两次不报错、表齐十一张、结构版本可读。

    **现状锁，改一次反转一次**：round-88（B-07c）7 → 8 张，round-90（B-04b）→ 9 张，
    round-91（B-04d）只加列不加表（v4），round-92 加 `reappraisals` → 10 张（v5），
    round-101（B-04h ②）加 `unrecoverable_marks` → 11 张（v6）。
    锁的**不是数字本身**，而是「`init_db()` 会顺带把版本抬到 `SCHEMA_VERSION`」——
    表建了而版本没抬，才是真故障（半迁移）。
    """
    conn = store.connect(tmp_path / "a.db")
    store.init_db(conn)
    store.init_db(conn)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"users", "skills", "tasks", "error_patterns", "submissions",
            "user_skill_states", "review_items", "mastery_snapshots",
            "arbitration_queue", "reappraisals", "unrecoverable_marks"} <= names
    assert len({n for n in names if not n.startswith("sqlite_")}) == 11
    assert store.schema_version(conn) == store.SCHEMA_VERSION == 6
    cols = {r[1] for r in conn.execute("PRAGMA table_info(submissions)")}
    assert "artifacts" in cols
    conn.close()


def test_foreign_keys_are_actually_enforced(db):
    """外键必须真生效：SQLite 默认关闭，只写 REFERENCES 不写 PRAGMA 等于没有约束。"""
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        with db:
            db.execute(
                "INSERT INTO submissions (submission_id, user_id, task_id, assert_score, "
                "diagnosis, created_at) VALUES ('ghost', 'nobody', 'dbg-001-order-report', "
                "0.0, '{}', '2026-11-13T00:00:00Z')"
            )


def test_data_survives_reconnect(tmp_path):
    """**持久化唯一的实证**：写 → 关连接 → 重开 → 数据还在。

    同一连接里读回来只证明"还在内存里"；`sqlite3` 即使 `commit` 了，
    只要文件没落盘或被写在临时目录以外的位置，这个测试同样会红。
    """
    path = tmp_path / "persist.db"
    conn = store.connect(path)
    store.init_db(conn)
    store.register_skills(conn)
    store.register_patterns(conn)
    store.register_task(conn, TASK_YAML)
    sid = store.save_submission(conn, run_fixture("raw-perfect"), user_id=USER)
    conn.close()

    again = store.connect(path)
    try:
        assert store.count_submissions(again) == 1
        assert store.load_report(again, sid) == run_fixture("raw-perfect")
    finally:
        again.close()


# --- ④ 分数：只落 ratio，反事实分不进这一列 --------------------------------------

def test_assert_score_is_ratio_not_counterfactual(db):
    """`raw-no-file` 门禁未过：ratio=0 但反事实分不为 0。

    落错一列（把反事实分当分数）会让"没交产物"在库里看起来像"交了但做得差"，
    两者在诊断上是完全不同的结论——这正是决策 5 要分开存的原因。
    """
    report = run_fixture("raw-no-file")
    assert report["score"]["ratio"] == 0.0
    assert report["score"]["counterfactual_credit"] > 0.0

    sid = store.save_submission(db, report, user_id=USER)
    row = store.load_submission(db, sid)
    assert row["assert_score"] == 0.0
    assert row["diagnosis"]["score"]["counterfactual_credit"] > 0.0


# --- ② 派生列：只认 fail，且绝不回写报告 ------------------------------------------

def test_detected_patterns_only_counts_fail(db):
    """决策 7 的可执行版本：`unscored` 不算检出错误。

    `raw-no-trace` 的轨迹断言全被改写成 unscored（没上报 trace），
    若把它们的 error_patterns 也算进来，"没给数据"就被记成"做错了"。
    """
    expect = {
        "raw-perfect": [],
        "raw-no-trace": [],
        "raw-no-file": ["no_output_file"],
        "raw-empty": ["no_output_file", "no_retry"],
    }
    for name, want in expect.items():
        sid = store.save_submission(db, run_fixture(name), user_id=USER)
        assert store.load_submission(db, sid)["detected_patterns"] == want, name


def test_derived_column_never_leaks_into_report(db):
    """派生列只在表里，不许混进报告——否则报告多出一个 DTO 没有的字段。"""
    sid = store.save_submission(db, run_fixture("raw-empty"), user_id=USER)
    back = store.load_report(db, sid)
    assert "detected_patterns" not in back
    assert back == run_fixture("raw-empty")


def test_non_fail_patterns_are_ignored_even_if_present(db):
    """**这条锁不能只靠夹具**：`unscored` 断言的 `error_patterns` 已被上游清空，
    于是"过滤"与"不过滤"在五份夹具上结果相同——变异测试会漏过去（round-75 实证）。
    只有上游真的留下非 fail 的模式时两者才分得开，所以这里显式构造：
    一条 `pass` + 一条 `blocked`，都带模式，断言一条都不进库。
    `blocked` 是真实会保留原值的那类（前置未过 ≠ 做错，决策 7）。
    """
    report = copy.deepcopy(run_fixture("raw-perfect"))
    report["assertions"][0]["error_patterns"] = ["no_output_file"]  # pass 带模式
    report["assertions"][1]["state"] = "blocked"
    report["assertions"][1]["error_patterns"] = ["no_retry"]        # blocked 带模式
    sid = store.save_submission(db, report, user_id=USER)
    assert store.load_submission(db, sid)["detected_patterns"] == []


# --- ③ 不覆盖：诊断记录要能回溯到"当时那份" --------------------------------------

def test_duplicate_submission_id_is_rejected_and_old_row_survives(db):
    """重复 id 抛错，且旧记录一个字节都不变。"""
    report = run_fixture("raw-perfect")
    sid = store.save_submission(db, report, user_id=USER)

    tampered = copy.deepcopy(report)
    tampered["score"]["ratio"] = 0.5
    with pytest.raises(store.StoreError, match="已存在"):
        store.save_submission(db, tampered, user_id=USER)

    assert store.count_submissions(db) == 1
    assert store.load_submission(db, sid)["assert_score"] == 1.0


# --- ① 前置条件不成立就停，不写半条 ----------------------------------------------

def test_missing_user_id_is_rejected(db):
    """`user_id` 不给就报错——不从 `agent_id` 顶替（编一个会污染后面所有按人聚合的统计）。"""
    report = run_fixture("raw-perfect")
    for bad in ("", "   ", None):
        with pytest.raises(store.StoreError, match="user_id"):
            store.save_submission(db, report, user_id=bad)  # type: ignore[arg-type]
    assert store.count_submissions(db) == 0


def test_unregistered_task_is_rejected(db):
    """任务没注册就落库，等于让 tasks 外键变装饰。"""
    report = copy.deepcopy(run_fixture("raw-perfect"))
    report["submission"]["task_id"] = "ghost-task"
    with pytest.raises(store.StoreError, match="未注册"):
        store.save_submission(db, report, user_id=USER)


def test_broken_report_writes_nothing(db):
    """缺 `score.ratio` 直接停，且库里不留半条——失败要么全成要么全不成。"""
    report = copy.deepcopy(run_fixture("raw-perfect"))
    del report["score"]["ratio"]
    with pytest.raises(store.StoreError, match="score.ratio"):
        store.save_submission(db, report, user_id=USER)
    assert store.count_submissions(db) == 0


def test_register_task_requires_skills_present(tmp_path):
    """缺技能时注册任务要报出**缺哪个**，不能等外键抛出看不懂的约束错误。"""
    conn = store.connect(tmp_path / "no-skills.db")
    store.init_db(conn)
    with pytest.raises(store.StoreError, match="multi_step_planning"):
        store.register_task(conn, TASK_YAML)
    conn.close()


# --- 时间轴：锚在评测时刻，不锚在落库时刻 ----------------------------------------

def test_created_at_prefers_evaluated_at(db):
    """补录历史数据时按"今天"落库，会把复习间隔整段算错。"""
    report = run_fixture("raw-perfect")
    sid = store.save_submission(db, report, user_id=USER)
    assert store.load_submission(db, sid)["created_at"] == report["submission"]["evaluated_at"]


def test_created_at_can_be_overridden(db):
    """显式传时间优先于报告值（重算历史、批量导入时需要）。"""
    sid = store.save_submission(db, run_fixture("raw-perfect"), user_id=USER,
                                created_at="2020-01-01T00:00:00Z")
    assert store.load_submission(db, sid)["created_at"] == "2020-01-01T00:00:00Z"


# --- 查询：给状态层（M3）用的那两个口 --------------------------------------------

def test_list_is_newest_first_and_limit_honoured(db):
    """列表按评测时刻倒序 + limit 生效：掌握度要的是"最近几次"。"""
    for name in ALL_FIXTURES:
        store.save_submission(db, run_fixture(name), user_id=USER)

    rows = store.list_submissions(db, USER)
    assert [r["submission_id"] for r in rows][0] == "sub-demo-perfect"
    assert len(store.list_submissions(db, USER, limit=2)) == 2
    assert store.count_submissions(db, USER) == 5
    assert store.count_submissions(db, "someone-else") == 0


# --- 约束层：范围检查写在表上，不只写在函数里 ------------------------------------

def test_range_constraints_are_in_the_schema(db):
    """`fsrs_rating` 与 `judge_agreement` 的合法范围由表约束兜住，不靠调用方自觉。"""
    report = run_fixture("raw-perfect")
    sid = store.save_submission(db, report, user_id=USER)
    for column, value in (("fsrs_rating", 5), ("fsrs_rating", 0), ("judge_agreement", 1.5)):
        with pytest.raises(sqlite3.IntegrityError):
            with db:
                db.execute(f"UPDATE submissions SET {column} = ? WHERE submission_id = ?",
                           (value, sid))


def test_replaying_the_v4_step_on_a_half_built_database_does_not_crash(tmp_path):
    """「表建好了、PRAGMA 还是 0」的半旧库，重放 v4 步骤不许炸。

    这种库真的会出现：手工 `executescript(SCHEMA_SQL)` 建库、迁移中途被打断、
    或者上一版服务跑过建表但没跑 `migrate()`。那时 v4 的 `ALTER TABLE` 会以
    `duplicate column name` 抛错，而**整个迁移会连同后面所有步骤一起失败**。

    所以 v4 的加列必须自己判重（SQLite 没有 `ADD COLUMN IF NOT EXISTS`）。
    这条测试就构造那个状态：列先加上，版本仍写 0，然后走正常 `init_db()`。
    """
    conn = store.connect(tmp_path / "half.db")
    conn.executescript(store.SCHEMA_SQL)      # 表全在（v1+v2+v3 的 DDL）
    store._ensure_artifacts_column(conn)      # 列也在了
    assert store.schema_version(conn) == 0    # 但版本还是 0 —— 这就是"半旧库"

    store.init_db(conn)                       # 重放 step 2 / 3 / 4

    assert store.schema_version(conn) == store.SCHEMA_VERSION
    cols = [r[1] for r in conn.execute("PRAGMA table_info(submissions)")]
    assert cols.count("artifacts") == 1       # 没被加成两列
    conn.close()
