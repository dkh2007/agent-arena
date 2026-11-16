"""状态层的落库入口（M1 第一步）：把一份诊断报告写进 SQLite，并能重启后读回。

**为什么这一步现在才做**：round-74 的骨架盘点给出一条明确的分界线——
「一次提交内」的能力基本齐全（评分 / 诊断 / 缺口聚合 / 作废 / 对账），
「跨提交」的能力一件都没有。而跨提交的一切（掌握度 P、错误热度、FSRS 调度、
四因子排期）都卡在同一件事上：没有地方存上一次的结果。本模块补的就是这块地基。

四条设计决定，都不是"顺手这么写"：

1. **不用 ORM**。`requirements.txt` 已 pin 死，`tests/test_dependencies.py` 会离线比对已安装版本；
   引 sqlalchemy 是为一件事加一整层依赖。9 张表（v1 七张 + v2 的 `mastery_snapshots`
   + v3 的 `arbitration_queue`）的 DDL 手写比 ORM 映射更短，
   将来换 PostgreSQL 时重写一遍 DDL，比为抽象层打补丁便宜。
2. **`user_id` 必须由调用方显式给**，不从 `agent_id` 推。fixture 里只有 `agent_id`（学习者部署的服务名），
   而「谁交的」和「哪个 agent 交的」是两个概念——一个学习者可以有多个 agent。
   在这里编一个 user_id，会污染后面所有按用户聚合的统计（掌握度、热度、排期全部错人）。
   缺了就抛 `StoreError`，不猜。
3. **`detected_patterns` 是派生列，不是第二套口径**。它的值是"报告里 `state=fail` 的断言所带的
   `error_patterns` 去重保序汇总"，唯一真相源是 `diagnosis` 列（存整份报告）。这一列只为查询方便，
   报告本身一个字都不改。
4. **重复 `submission_id` 抛错，不覆盖**。诊断记录要能回溯到"当时那份"——分数争议时，
   静默 UPSERT 会把证据换成最后一份，等于自己制造不可复核。

表名用复数小写（`submissions`），与 `docs/data-model.md` 的实体名（`Submission`）一一对应。
字段与顺序按该文件逐项对齐；**字段变更视为破坏性变更**，须同步改该文件并升 `SCHEMA_VERSION`。

调用关系：`build_report()` 产出报告 → `save_submission()` 落库 → 状态层（M3）读历史算掌握度。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SKILL_TAXONOMY = ROOT / "docs" / "skill-taxonomy-v0.yaml"
VOCAB_PATH = ROOT / "docs" / "error-patterns-v0.yaml"

# 结构版本：任何字段增删改都要 +1，并在 docs/data-model.md 留痕。
# v2（B-07c）：新增 `mastery_snapshots`（P 的**缓存**）。旧表一个字段没动——
# 迁移因此是纯增量（加一张表 + 回填），不需要重建 `user_skill_states`。
# v3（B-04b）：新增 `arbitration_queue`（judge 双评不一致时的人工待办）。
# 同样纯增量：**旧表一个字段不动**，尤其没有给 `submissions` 加"是否待仲裁"的列——
# 状态属于队列自己，抄一份到 submissions 就变成第二真相源。
# v4（B-04d 甲+护栏）：`submissions` 补 `artifacts` 列（原始产物）。
# **这不是为查询方便，是"能不能回溯"的前提**：报告里的 credit 断言（交没交产物、
# 汇总数对不对）都是拿产物判的；只存 trace 与报告，等于把"判定结果"当成了唯一证据，
# 一旦判据改动想按新标准重评历史提交，会发现**根本没有输入可评**。
# 反证条件：若将来发现更完整的行为记录（如平台侧全量抓包）能替代它，可回滚这一列。
SCHEMA_VERSION = 4

# 迁移步骤表：按版本号逐级升。写成数据而不是一串 if，是为了加 v4 时只加一行，
# 以及让"从这个版本升到那个版本要跑哪几步"能被测试逐一覆盖。
# 必须按依赖顺序建（被引用的先建）：users / skills → tasks → submissions → 其余。
# **拆成 v1/v2/v3 三段**：迁移测试需要一个"真的 v1 老库"来证明 from=1，
# 而不是拿当前建好的库改个 PRAGMA 假装——那测不出"旧表缺新表"的情形。
SCHEMA_SQL_V1 = """
CREATE TABLE IF NOT EXISTS users (
    user_id            TEXT PRIMARY KEY,
    display_name       TEXT NOT NULL DEFAULT '',
    agent_endpoint_url TEXT,
    daily_review_cap   INTEGER NOT NULL DEFAULT 2 CHECK (daily_review_cap >= 0),
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    skill_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    prerequisites TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id             TEXT PRIMARY KEY,
    yaml_path           TEXT NOT NULL,
    title               TEXT NOT NULL,
    primary_skill_id    TEXT NOT NULL REFERENCES skills(skill_id),
    secondary_skill_ids TEXT NOT NULL DEFAULT '[]',
    variant_template_id TEXT,
    fault_injection     INTEGER NOT NULL DEFAULT 0 CHECK (fault_injection IN (0, 1)),
    git_hash            TEXT,
    rubric_count        INTEGER NOT NULL DEFAULT 0 CHECK (rubric_count >= 0),
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS error_patterns (
    pattern_id       TEXT PRIMARY KEY,
    label            TEXT NOT NULL UNIQUE,
    description      TEXT NOT NULL DEFAULT '',
    detection_rule   TEXT NOT NULL,
    mapped_skill_ids TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    submission_id      TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL REFERENCES users(user_id),
    task_id            TEXT NOT NULL REFERENCES tasks(task_id),
    task_git_hash      TEXT,
    agent_endpoint_url TEXT,
    trace              TEXT,
    assert_score       REAL NOT NULL,
    judge_scores       TEXT,
    judge_agreement    REAL CHECK (judge_agreement IS NULL OR (judge_agreement >= 0 AND judge_agreement <= 1)),
    detected_patterns  TEXT NOT NULL DEFAULT '[]',
    diagnosis          TEXT NOT NULL,
    fsrs_rating        INTEGER CHECK (fsrs_rating IS NULL OR fsrs_rating BETWEEN 1 AND 4),
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_skill_states (
    user_id    TEXT NOT NULL REFERENCES users(user_id),
    skill_id   TEXT NOT NULL REFERENCES skills(skill_id),
    fsrs_card  TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, skill_id)
);

CREATE TABLE IF NOT EXISTS review_items (
    review_id            TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL REFERENCES users(user_id),
    skill_id             TEXT NOT NULL REFERENCES skills(skill_id),
    kind                 TEXT NOT NULL CHECK (kind IN ('variant', 'fault_injection', 'timed', 'replay')),
    task_id              TEXT REFERENCES tasks(task_id),
    source_submission_id TEXT REFERENCES submissions(submission_id),
    due                  TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'skipped')),
    created_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_submissions_user_time ON submissions(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_review_items_queue     ON review_items(user_id, status, due);
"""

SCHEMA_SQL_V2 = """
-- v2 / B-07c：掌握度 P 的**快照**。这是缓存，不是第二真相源：
--   `mastery.build_series()` 从 submissions 重放出来的值永远是权威，
--   本表只是把那个值抄一份，省掉"按 P 排序很多用户"时的全量重放。
-- `lam` / `p0` / `evidence_count` / `last_submission_id` 记的是"按哪批报告、哪套参数算的"，
--   让"快照过期了没有"可以不用重放就先筛一遍；真要判定仍以重放为准。
CREATE TABLE IF NOT EXISTS mastery_snapshots (
    user_id            TEXT NOT NULL REFERENCES users(user_id),
    skill_id           TEXT NOT NULL REFERENCES skills(skill_id),
    mastery_p          REAL NOT NULL CHECK (mastery_p >= 0 AND mastery_p <= 1),
    lam                REAL NOT NULL CHECK (lam > 0 AND lam < 1),
    p0                 REAL,
    evidence_count     INTEGER NOT NULL CHECK (evidence_count >= 0),
    last_submission_id TEXT,
    updated_at         TEXT NOT NULL,
    PRIMARY KEY (user_id, skill_id)
);

-- 排序需求（B-25，尚未开）要的就是"某个技能下按 P 从高到低"，索引按这个顺序建。
-- 本轮只建索引、不做排名页：有索引不等于有榜单。
CREATE INDEX IF NOT EXISTS idx_mastery_snapshots_rank ON mastery_snapshots(skill_id, mastery_p DESC);
"""

SCHEMA_SQL_V3 = """
-- v3 / B-04b：judge 双评不一致时的人工待办。**它不是闸门，也不是分数的一部分**：
--   一条记录只回答"这份提交的两次评价给了不同档位，需要人看一眼判据表述"。
--   为什么是独立表而不是 submissions 上的一列：同一份提交可能被重新评价（改 prompt、
--   换模型），队列状态有它自己的时间轴（opened_at / resolved_at）；抄一列进 submissions
--   会让"提交"与"待仲裁"两件事互相覆盖。
CREATE TABLE IF NOT EXISTS arbitration_queue (
    submission_id   TEXT PRIMARY KEY REFERENCES submissions(submission_id),
    user_id         TEXT NOT NULL REFERENCES users(user_id),
    task_id         TEXT NOT NULL REFERENCES tasks(task_id),
    judge_agreement REAL NOT NULL CHECK (judge_agreement >= 0 AND judge_agreement <= 1),
    threshold       REAL NOT NULL CHECK (threshold > 0 AND threshold <= 1),
    reason          TEXT NOT NULL DEFAULT 'judge_disagreement',
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'resolved', 'dismissed')),
    opened_at       TEXT NOT NULL,
    resolved_at     TEXT,
    resolution_note TEXT,
    -- 收尾与留痕绑死：pending 不许带裁决痕迹，非 pending 必须同时有时间和理由。
    -- 这条 CHECK 是把"裁决要写理由"变成机械约束，而不是靠代码自觉。
    CHECK (
        (status = 'pending'  AND resolved_at IS NULL     AND resolution_note IS NULL)
     OR (status <> 'pending' AND resolved_at IS NOT NULL AND resolution_note IS NOT NULL)
    )
);

-- 待办按"最早开的先看"排；status 在前是因为查询永远先筛 pending。
CREATE INDEX IF NOT EXISTS idx_arbitration_pending ON arbitration_queue(status, opened_at);
"""

SCHEMA_SQL = SCHEMA_SQL_V1 + SCHEMA_SQL_V2 + SCHEMA_SQL_V3


def _ensure_artifacts_column(conn: sqlite3.Connection) -> None:
    """v4：给 `submissions` 补 `artifacts`（原始产物）。

    为什么这一步是函数而不是一段 DDL：SQLite **没有** `ADD COLUMN IF NOT EXISTS`，
    而 `migrate()` 在一个"表建好了但 PRAGMA 还是 0"的半旧库上会重放本步——
    那时 ALTER 会以 `duplicate column name` 炸掉整个迁移。先查列再加，是本步幂等的唯一做法。

    不做重建表（rename → create → copy）：`submissions` 是历史证据表，
    重建过程中任何一次中断都会让"证据"和"索引"处于两套结构之间，代价远大于省一个函数。
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(submissions)")}
    if "artifacts" in columns:
        return
    conn.execute("ALTER TABLE submissions ADD COLUMN artifacts TEXT")


# 版本号 → 该版本要补的 DDL / 新建的表名。`migrate()` 只读这张表，不再写 if 分支。
# 值可以是 SQL 文本，也可以是"接收连接的函数"——后者用于没法写成幂等 DDL 的步骤
# （v4 的加列：SQLite 缺 `ADD COLUMN IF NOT EXISTS`）。两种形态都只跑一次，由版本号保证。
#
# 表名元组的用途有两个：`migrate()` 的返回值（告诉调用方加了什么），以及
# `tests/test_store.py` 里"表数 = 9 + 列 = 1"的现状锁。
MIGRATION_STEPS: dict[int, tuple[Any, tuple[str, ...]]] = {
    2: (SCHEMA_SQL_V2, ("mastery_snapshots",)),
    3: (SCHEMA_SQL_V3, ("arbitration_queue",)),
    4: (_ensure_artifacts_column, ("submissions.artifacts",)),
}


def _apply_step(conn: sqlite3.Connection, step: int) -> None:
    """执行一步迁移。文本走 `executescript`（DDL 自带 IF NOT EXISTS），函数直接调。"""
    action = MIGRATION_STEPS[step][0]
    if callable(action):
        action(conn)
    else:
        conn.executescript(action)

# 需要 JSON 编解码的列：写库前 dumps，读库后 loads（NULL 保持 NULL，不转成空数组）。
JSON_COLUMNS = {
    "skills": ("prerequisites",),
    "tasks": ("secondary_skill_ids",),
    "error_patterns": ("detection_rule", "mapped_skill_ids"),
    "submissions": ("trace", "judge_scores", "detected_patterns", "diagnosis", "artifacts"),
    "user_skill_states": ("fsrs_card",),
}


class StoreError(ValueError):
    """落库前置条件不成立——必须停下来补数据，不许写半条记录。"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开连接。**每个连接都要显式开外键**——SQLite 默认关闭，忘了就等于没有约束。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """建表（幂等）+ 把结构版本升到当前值。

    `executescript` 只负责"表都在"，**版本与迁移一律交给 `migrate()`**——
    否则一个 v1 老库被 `init_db` 重新打开时，会只补了表、没回填、也没升版本，
    从此"表里有列、PRAGMA 还是 1"，是最难查的那种半迁移状态。
    """
    with conn:
        conn.executescript(SCHEMA_SQL)
    migrate(conn)


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection, *, target: int = SCHEMA_VERSION) -> dict:
    """把库升到 `target`，返回本次动作（幂等、不降级、不删旧数据）。

    四条硬规矩：
    1. **只做增量**：v1→v2 只补 `mastery_snapshots`，v2→v3 只补 `arbitration_queue`，
       v3→v4 只给 `submissions` 加 `artifacts` 一列。**没有一步会重建表或改旧列**。
       所以任何一步失败，旧库仍是可用的一堆旧表，不会变砖。
    2. **回填是"重算"，不是"猜"**：v1 库里已经有提交时，按 `mastery.build_series()`
       逐用户逐技能重放，把结果写进快照表；原 `submissions` 一个字节不动。
    3. **不许降级**：`PRAGMA user_version > target` 直接抛错。看见看不懂的新版本还继续写，
       比停下来更危险。
    4. **不许超前**（round-88 外审 F4）：`target > SCHEMA_VERSION` 同样抛错。
       以前只拦下界，允许写出 `user_version=5` 而库里其实只有 v1+v2 的结构——
       「不许降级」的同一条理由，反方向也成立。

    **逐级升而不是一步到位**：v1 库升到 v4 会依次执行 v2、v3、v4 三段动作。
    一步到位的写法（"只跑最新那段"）在加 v4 时必然写错——那时 v1 库会缺 v3 的表。

    **v4 的历史提交补不回来（务必照实说）**：`artifacts` 列对**新增**提交生效，
    已经落库的旧提交这一列是 NULL。没有产物就无法按新判据重评，那部分历史
    只能靠"让学生重做"或"承认不可回溯"，不是本函数能修的。

    `target` 只接受 `1..SCHEMA_VERSION` 的整数；它是内部常量，不来自用户输入。
    """
    if isinstance(target, bool) or not isinstance(target, int) or target < 1:
        raise StoreError("migrate 的 target 必须是正整数")
    if target > SCHEMA_VERSION:
        raise StoreError(
            f"migrate 的 target={target} 高于本代码认识的 SCHEMA_VERSION={SCHEMA_VERSION}"
            "——拒绝写出自己看不懂的版本号"
        )
    current = schema_version(conn)
    if current > target:
        raise StoreError(
            f"库版本 {current} 高于目标 {target}——拒绝降级，先确认是不是用旧代码开了新库"
        )
    if current == target:
        return {"from": current, "to": target, "added_tables": [], "backfilled": 0}

    applied: list[int] = []
    with conn:
        if current == 0:
            # 全新库：先一次建齐（各版本的表之间有外键，不能只建最新那段），
            # 再把从 v2 起的**步骤动作**跑一遍：文本步骤是幂等的（DDL 自带 IF NOT EXISTS），
            # 函数步骤自己判重（v4 的加列）。这样全新库与老库走的是同一串动作，
            # 不会出现"新建库有某列、老库升级后没有"这种最难查的差异。
            conn.executescript(SCHEMA_SQL)
            for step in sorted(MIGRATION_STEPS):
                if step <= target:
                    _apply_step(conn, step)
                    applied.append(step)
        else:
            for step in sorted(MIGRATION_STEPS):
                if current < step <= target:
                    _apply_step(conn, step)
                    applied.append(step)

    backfilled = 0
    if current < 2 <= target:
        from . import mastery_snapshot  # 局部导入：mastery 会反向引用 store

        backfilled = mastery_snapshot.recompute_all(conn)

    with conn:
        conn.execute(f"PRAGMA user_version = {target}")
    return {
        "from": current,
        "to": target,
        "added_tables": [t for step in applied for t in MIGRATION_STEPS[step][1]],
        "backfilled": backfilled,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── P 快照的落库（v2 / B-07c）────────────────────────────────────────────────
# 分工与 FSRS 卡那段相同：重放与判定都在 `mastery_snapshot.py`，本模块只"照写、照读"。
# **这张表里存的是 P_raw，不是衰减后的 P_eff**（B-08b 定：衰减是读法，不是新状态）。
# 把 as_of 钉进快照会制造第二真相源，见 review-response §86.8 ④。

def list_user_ids(conn: sqlite3.Connection) -> list[str]:
    """全部用户 id（重算入口用，按 id 排序保证顺序稳定）。"""
    return [r[0] for r in conn.execute("SELECT user_id FROM users ORDER BY user_id")]


def list_skill_ids(conn: sqlite3.Connection) -> list[str]:
    """全部已注册技能 id。快照的外键以这里为准：没注册的技能不写快照。"""
    return [r[0] for r in conn.execute("SELECT skill_id FROM skills ORDER BY skill_id")]


def save_mastery_snapshot(conn: sqlite3.Connection, row: dict) -> None:
    """写/覆盖一条 (user, skill) 的 P 快照。返回 None。

    **覆盖是正确语义**：这是缓存，不是历史证据；缓存没有"保留旧值"这回事。
    与 `submissions` 的"不覆盖"相反——那边是证据，这边是当前派生值。

    所有数值字段在这里再挡一层：表约束只在库里生效，报不清是哪一列，
    而快照是排序依据，一个越界的值会静默把人排错。
    """
    user_id, skill_id = row.get("user_id"), row.get("skill_id")
    if not isinstance(user_id, str) or not user_id:
        raise StoreError(f"快照缺 user_id（收到 {user_id!r}）")
    if not isinstance(skill_id, str) or not skill_id:
        raise StoreError(f"快照缺 skill_id（收到 {skill_id!r}）")

    p = row.get("mastery_p")
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not (0.0 <= float(p) <= 1.0):
        raise StoreError(f"mastery_p 必须是 [0,1] 内的数值（收到 {p!r}）")
    lam = row.get("lam")
    if isinstance(lam, bool) or not isinstance(lam, (int, float)) or not (0.0 < float(lam) < 1.0):
        raise StoreError(f"lam 必须是 (0,1) 内的数值（收到 {lam!r}）")
    count = row.get("evidence_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise StoreError(f"evidence_count 必须是非负整数（收到 {count!r}）")

    ensure_user(conn, user_id)
    if conn.execute("SELECT 1 FROM skills WHERE skill_id = ?", (skill_id,)).fetchone() is None:
        raise StoreError(
            f"技能 {skill_id} 未注册——先 register_skills()；"
            "允许未注册技能写快照等于让外键变成装饰"
        )

    with conn:
        conn.execute(
            "INSERT INTO mastery_snapshots (user_id, skill_id, mastery_p, lam, p0, "
            "evidence_count, last_submission_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, skill_id) DO UPDATE SET "
            "mastery_p = excluded.mastery_p, lam = excluded.lam, p0 = excluded.p0, "
            "evidence_count = excluded.evidence_count, "
            "last_submission_id = excluded.last_submission_id, "
            "updated_at = excluded.updated_at",
            (
                user_id, skill_id, float(p), float(lam), row.get("p0"), int(count),
                row.get("last_submission_id"), row.get("updated_at") or _now(),
            ),
        )


def load_mastery_snapshot(conn: sqlite3.Connection, user_id: str, skill_id: str) -> dict | None:
    """读回一条快照；没有就是 `None`（没证据 ≠ P=0，不许造 0 出来）。"""
    r = conn.execute(
        "SELECT * FROM mastery_snapshots WHERE user_id = ? AND skill_id = ?",
        (user_id, skill_id),
    ).fetchone()
    return dict(r) if r is not None else None


def list_mastery_snapshots(conn: sqlite3.Connection, *, skill_id: str | None = None,
                           order_by_p: bool = False) -> list[dict]:
    """列出快照。`order_by_p=True` 就是"按 P 排序多个用户"的读法（B-25 的地基）。

    平手按 `user_id` 兜底：否则 SQLite 同分行的顺序不稳定，两次读可能给出两个榜。
    本函数**只读快照、不重放**——这正是落字段的意义；新鲜度由
    `mastery_snapshot.verify_*()` 单独核，两者不混在一起。
    """
    sql = "SELECT * FROM mastery_snapshots"
    params: tuple = ()
    if skill_id is not None:
        sql += " WHERE skill_id = ?"
        params = (skill_id,)
    if order_by_p:
        sql += " ORDER BY mastery_p DESC, user_id ASC"
    else:
        sql += " ORDER BY skill_id ASC, user_id ASC"
    return [dict(r) for r in conn.execute(sql, params)]


def delete_mastery_snapshot(conn: sqlite3.Connection, user_id: str, skill_id: str) -> int:
    """删一条快照。返回删除行数。用于"该技能已无证据"时避免留下陈旧值。"""
    with conn:
        cur = conn.execute(
            "DELETE FROM mastery_snapshots WHERE user_id = ? AND skill_id = ?",
            (user_id, skill_id),
        )
    return int(cur.rowcount or 0)


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def _row_to_dict(row: sqlite3.Row, table: str) -> dict:
    item = dict(row)
    for col in JSON_COLUMNS.get(table, ()):
        if col in item:
            item[col] = _load(item[col])
    return item


def ensure_user(conn: sqlite3.Connection, user_id: str, display_name: str = "") -> str:
    """首次提交自动建用户（注册流程属 M1 后半段，落库侧不依赖它）。

    已存在则**不动** `display_name` 与 `daily_review_cap`——这两个是用户设置，
    被一次提交悄悄覆盖等于丢配置。
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise StoreError("user_id 必须是非空字符串（不从 agent_id 推断，见模块文档第 2 条）")
    with conn:
        conn.execute(
            "INSERT INTO users (user_id, display_name, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO NOTHING",
            (user_id, display_name, _now()),
        )
    return user_id


def register_skills(conn: sqlite3.Connection, taxonomy_path: str | Path = SKILL_TAXONOMY) -> int:
    """从技能分类学导入（幂等 upsert）。返回处理条数。"""
    import yaml

    data = yaml.safe_load(Path(taxonomy_path).read_text(encoding="utf-8"))
    skills = data.get("skills") or []
    now = _now()
    with conn:
        for s in skills:
            conn.execute(
                "INSERT INTO skills (skill_id, name, prerequisites, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(skill_id) DO UPDATE SET name = excluded.name, prerequisites = excluded.prerequisites",
                (s["skill_id"], s.get("name", ""), _dump(s.get("prerequisites") or []), now),
            )
    return len(skills)


def register_patterns(conn: sqlite3.Connection, vocab_path: str | Path = VOCAB_PATH) -> int:
    """从错误模式词表导入（幂等 upsert）。`label` 全局唯一由表约束兜住。"""
    import yaml

    data = yaml.safe_load(Path(vocab_path).read_text(encoding="utf-8"))
    patterns = data.get("patterns") or []
    now = _now()
    with conn:
        for p in patterns:
            conn.execute(
                "INSERT INTO error_patterns "
                "(pattern_id, label, description, detection_rule, mapped_skill_ids, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(pattern_id) DO UPDATE SET label = excluded.label, "
                "description = excluded.description, detection_rule = excluded.detection_rule, "
                "mapped_skill_ids = excluded.mapped_skill_ids",
                (
                    p["pattern_id"],
                    p.get("label", ""),
                    p.get("description", ""),
                    _dump(p.get("detection_rule") or {}),
                    _dump(p.get("mapped_skill_ids") or []),
                    now,
                ),
            )
    return len(patterns)


def pattern_skill_map(conn: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
    """错误模式 → 技能缺口（B-09 的读取口，round-85）。

    `mapped_skill_ids` 一直是"有写入、没读出"——`register_patterns` 写进去，
    `diagnosis_report` 另有 YAML 直读的兜底路径，而排程层要从**已落库**的模式反查技能，
    不读这张表就只能再解析一遍 YAML，那会造出第二个真相源。

    返回空元组的模式表示「只诊断不归因」（见 `docs/error-patterns-v0.yaml` 文件头），
    它与"表里查不到这个模式"是两件事：前者是口径，后者是数据缺失。这里不合并二者，
    查不到就是不在字典里，由调用方决定怎么处理。
    """
    rows = conn.execute("SELECT pattern_id, mapped_skill_ids FROM error_patterns").fetchall()
    return {str(r["pattern_id"]): tuple(str(s) for s in (_load(r["mapped_skill_ids"]) or ()))
            for r in rows}


def register_task(conn: sqlite3.Connection, yaml_path: str | Path,
                  git_hash: str | None = None) -> str:
    """从任务 YAML 注册一条 Task 元数据（幂等 upsert）。只存元数据，评测配置仍只在 YAML 里。

    **必须走 `diagnosis_report.load_task()`，不能 `yaml.safe_load()`。** round-83 起变体用
    `base_task` 继承母本骨架，直接读文件只看得到补丁里写的那几条断言——
    `rubric_count` 会记成 2（母本是 5）、`fault_injection` 会记成 0，
    而后者正是复习层选 `kind` 的依据（见 `scheduler.schedule_submission`）。
    这个坑是 round-84 接复习层时才暴露的：元数据错了，`kind` 分支就永远是死代码。
    """
    path = Path(yaml_path)
    # 局部导入：`store` 是地基模块，不在导入期拉起整条评测链（与 llm_judge 同一取舍）。
    from arena.diagnosis_report import load_task

    task = load_task(path)
    task_id = task.get("task_id")
    primary = task.get("primary_skill_id")
    if not task_id or not primary:
        raise StoreError(f"{path}: 缺 task_id 或 primary_skill_id，无法注册")

    known = {r[0] for r in conn.execute("SELECT skill_id FROM skills")}
    missing = {primary, *(task.get("secondary_skill_ids") or [])} - known
    if missing:
        raise StoreError(
            f"{task_id}: 引用了未注册的技能 {sorted(missing)}——先 register_skills()，"
            "否则 tasks.primary_skill_id 的外键等于没有约束"
        )

    rubric_count = len(task.get("rubric_gate") or []) + len(task.get("rubric_credit") or [])
    # 取 `enabled` 而不是"配置块非空"：块存在但 `enabled: false` 表示这一版没开故障注入，
    # 记成 True 会让排程层把正常任务当调试题推。
    fault_injection = 1 if (task.get("fault_injection") or {}).get("enabled") else 0
    with conn:
        conn.execute(
            "INSERT INTO tasks (task_id, yaml_path, title, primary_skill_id, secondary_skill_ids, "
            "variant_template_id, fault_injection, git_hash, rubric_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET yaml_path = excluded.yaml_path, title = excluded.title, "
            "primary_skill_id = excluded.primary_skill_id, secondary_skill_ids = excluded.secondary_skill_ids, "
            "variant_template_id = excluded.variant_template_id, fault_injection = excluded.fault_injection, "
            "git_hash = excluded.git_hash, rubric_count = excluded.rubric_count",
            (
                task_id,
                str(path).replace("\\", "/"),
                task.get("title", ""),
                primary,
                _dump(task.get("secondary_skill_ids") or []),
                task.get("variant_template_id"),
                fault_injection,
                git_hash,
                rubric_count,
                _now(),
            ),
        )
    return task_id


def _failed_patterns(report: dict) -> list[str]:
    """派生 `detected_patterns`：`state=fail` 的断言所带的模式，去重保序。

    **不改变报告**（报告本身仍只在 `diagnosis` 列）。`blocked` / `unscored` 一律不计——
    决策 7：数据缺失不等于能力不足，把它们算成"检出错误"就是把缺数据当错。
    """
    out: list[str] = []
    for a in report.get("assertions") or []:
        if a.get("state") != "fail":
            continue
        for code in a.get("error_patterns") or []:
            if code not in out:
                out.append(code)
    return out


def _require(report: dict, *path: str) -> Any:
    cur: Any = report
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            raise StoreError(f"报告缺字段 {'.'.join(path)}——拒绝落一条无法复核的记录")
        cur = cur[key]
    return cur


def save_submission(
    conn: sqlite3.Connection,
    report: dict,
    *,
    user_id: str,
    trace: dict | None = None,
    artifacts: dict | None = None,
    judge_scores: Any = None,
    judge_agreement: float | None = None,
    fsrs_rating: int | None = None,
    agent_endpoint_url: str | None = None,
    created_at: str | None = None,
    refresh_mastery: bool = True,
) -> str:
    """把一份诊断报告写进 `submissions`。返回 `submission_id`。

    **只落 `score.ratio` 作为分数**：`counterfactual_credit` 是反事实参考（决策 5），
    它跟着 `diagnosis` 列一起存，绝不写进 `assert_score`——否则两套分数会被下游混读。

    `created_at` 的取值顺序：显式参数 → 报告里的 `submission.evaluated_at` → 当前时刻。
    优先用**评测时刻**而不是落库时刻：复习间隔（M4）以它为时间轴，
    补录历史数据若记成"今天"，间隔会全部算错。

    **v2 / B-07c**：`refresh_mastery=True`（默认）时，报告落库后立刻刷新该用户的
    P 快照，所以"提交完再重开库读回"能拿到最新 P。设 `False` 可关掉——
    迁移回填、故意构造"过期快照"的测试需要这个开关。
    刷新失败会抛（不吞）：缓存写坏了还让提交报成功，是最难查的那种不一致。

    **v4 / B-04d**：`artifacts`（原始产物）与 `trace` 一起落库。报告里的 credit 断言
    是拿产物判的，只留报告等于只留结论；判据改动后想按新标准重评历史提交，
    必须有这份产物作为输入。不传就是 NULL——那是"这份提交不可回溯"的诚实记法，
    **不要**拿空字典冒充"没有产物"（`{}` 会被当成"交了但为空"）。
    """
    sub = _require(report, "submission")
    submission_id = sub.get("submission_id")
    task_id = sub.get("task_id")
    if not isinstance(submission_id, str) or not submission_id:
        raise StoreError("报告缺 submission.submission_id")
    if not isinstance(task_id, str) or not task_id:
        raise StoreError("报告缺 submission.task_id")

    ratio = _require(report, "score", "ratio")
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
        raise StoreError(f"score.ratio 必须是数值（收到 {type(ratio).__name__}）")

    ensure_user(conn, user_id)

    if conn.execute("SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)).fetchone() is None:
        raise StoreError(
            f"任务 {task_id} 未注册——先 register_task()；"
            "允许未注册任务落库等于让 tasks 外键变成装饰"
        )

    if conn.execute("SELECT 1 FROM submissions WHERE submission_id = ?", (submission_id,)).fetchone():
        raise StoreError(
            f"submission_id 已存在：{submission_id}——诊断记录不覆盖，"
            "要重算请生成新的 submission_id 并保留旧记录"
        )

    stamp = created_at or sub.get("evaluated_at") or _now()
    with conn:
        conn.execute(
            "INSERT INTO submissions (submission_id, user_id, task_id, task_git_hash, agent_endpoint_url, "
            "trace, artifacts, assert_score, judge_scores, judge_agreement, detected_patterns, diagnosis, "
            "fsrs_rating, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                submission_id,
                user_id,
                task_id,
                sub.get("task_git_hash"),
                agent_endpoint_url,
                _dump(trace),
                _dump(artifacts),
                float(ratio),
                _dump(judge_scores),
                judge_agreement,
                _dump(_failed_patterns(report)),
                _dump(report),
                fsrs_rating,
                stamp,
            ),
        )
    if refresh_mastery:
        # 局部导入：mastery_snapshot → mastery → store，模块级导入会成环。
        from . import mastery_snapshot

        mastery_snapshot.refresh_user(conn, user_id, at=stamp)
    return submission_id


def load_submission(conn: sqlite3.Connection, submission_id: str) -> dict:
    """读回一条记录。`diagnosis` 列还原成完整报告，与落库前逐字段相等。"""
    row = conn.execute("SELECT * FROM submissions WHERE submission_id = ?", (submission_id,)).fetchone()
    if row is None:
        raise StoreError(f"没有这条提交记录：{submission_id}")
    return _row_to_dict(row, "submissions")


def load_report(conn: sqlite3.Connection, submission_id: str) -> dict:
    """只要报告本体（落库侧派生列一律不带，避免与报告字段同名混淆）。"""
    return load_submission(conn, submission_id)["diagnosis"]


def list_submissions(conn: sqlite3.Connection, user_id: str | None = None,
                     limit: int = 50) -> list[dict]:
    """按时间倒序列出（不含 diagnosis 正文，避免一次拉爆内存）。"""
    sql = ("SELECT submission_id, user_id, task_id, assert_score, detected_patterns, created_at "
           "FROM submissions")
    params: tuple = ()
    if user_id is not None:
        sql += " WHERE user_id = ?"
        params = (user_id,)
    sql += " ORDER BY created_at DESC, submission_id DESC LIMIT ?"
    return [_row_to_dict(r, "submissions") for r in conn.execute(sql, (*params, limit))]


def count_submissions(conn: sqlite3.Connection, user_id: str | None = None) -> int:
    if user_id is None:
        return int(conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0])
    return int(conn.execute("SELECT COUNT(*) FROM submissions WHERE user_id = ?", (user_id,)).fetchone()[0])


def user_reports(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    """该用户**全部**提交的完整报告，按 `created_at` 升序（round-80，B-07 的第一个读者）。

    与 `list_submissions` 的分工：那个是"看列表"（倒序、限条数、不带正文）；
    这个是"按时间重放"（正序、不限条数、带整份 `diagnosis`）。

    三点刻意为之：
    - **正序**：状态层要的是"从头往后折"，倒序交给调用方自己翻会诱导出"按列表位置当时间"。
    - **不限条数**：掌握度是累计量，截断等于悄悄少算历史；要限流该在调用方显式做。
    - **`created_at` 排序而不是 `evaluated_at`**：落库时前者已锚到后者，而补录时显式传入的值
      才是"这条记录代表哪一刻"的权威。同一时刻按 `submission_id` 兜底，保证重跑顺序稳定。
    """
    rows = conn.execute(
        "SELECT * FROM submissions WHERE user_id = ? ORDER BY created_at ASC, submission_id ASC",
        (user_id,),
    ).fetchall()
    return [_row_to_dict(r, "submissions") for r in rows]


# ── 仲裁队列的落库（v3 / B-04b）───────────────────────────────────────────────
# judge 双评不一致时的**人工待办**。判定与入队条件在 `judge_pipeline.py`，这里只存取。
#
# 三条设计决定：
# 1. **主键就是 `submission_id`**：一份提交最多一条待办。重复入队幂等——已经在了就原样
#    返回，**不改它记下的 threshold**。改掉等于把"当时按多少判的"抹了，事后无法复核。
# 2. **本表任何函数都不碰 `submissions.fsrs_rating`**（也不碰 `user_skill_states`）。
#    队列回答的是"判据表述要不要人看一眼"，不是"这份提交不算数"。混起来会把系统的
#    口径问题算成学习者的能力问题（决策 7），那正是这一层最该避免的错。
# 3. **收尾必须带时间与理由**：表级 CHECK 强制，代码层再挡一次只为给出可读错误。

ARBITRATION_STATUSES = ("pending", "resolved", "dismissed")


def save_arbitration_entry(
    conn: sqlite3.Connection,
    submission_id: str,
    *,
    judge_agreement: float,
    threshold: float,
    reason: str = "judge_disagreement",
    opened_at: str | None = None,
) -> dict:
    """把一份提交排进仲裁队列（幂等）。返回该行现状。

    `user_id` / `task_id` **从 `submissions` 现读**，不接受调用方传入：让队列跟着提交走，
    少一个可能与提交对不上的副本。
    """
    if not isinstance(submission_id, str) or not submission_id.strip():
        raise StoreError("仲裁入队缺 submission_id")
    for name, value, ok in (
        ("judge_agreement", judge_agreement, 0.0),
        ("threshold", threshold, 0.0),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not (ok <= float(value) <= 1.0):
            raise StoreError(f"{name} 必须是 [0,1] 内的数值（收到 {value!r}）")
    if float(threshold) <= 0.0:
        raise StoreError(
            "threshold 必须 > 0：阈值 0 等于『永不仲裁』，那不该靠入队函数来表达"
        )
    if not isinstance(reason, str) or not reason.strip():
        raise StoreError("reason 必须是非空字符串——队列条目要能说明为什么进来")

    row = conn.execute(
        "SELECT user_id, task_id FROM submissions WHERE submission_id = ?", (submission_id,)
    ).fetchone()
    if row is None:
        raise StoreError(
            f"提交 {submission_id} 还没落库——先 save_submission() 再入队；"
            "先入队会让外键指向一条不存在的证据"
        )

    existing = load_arbitration(conn, submission_id)
    if existing is not None:
        return existing

    with conn:
        conn.execute(
            "INSERT INTO arbitration_queue (submission_id, user_id, task_id, judge_agreement, "
            "threshold, reason, status, opened_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                submission_id, row["user_id"], row["task_id"], float(judge_agreement),
                float(threshold), reason.strip(), opened_at or _now(),
            ),
        )
    return load_arbitration(conn, submission_id)  # type: ignore[return-value]


def load_arbitration(conn: sqlite3.Connection, submission_id: str) -> dict | None:
    """读一条队列记录；没有就返回 `None`（"没进队"是正常状态，不是错误）。"""
    row = conn.execute(
        "SELECT * FROM arbitration_queue WHERE submission_id = ?", (submission_id,)
    ).fetchone()
    return None if row is None else _row_to_dict(row, "arbitration_queue")


def list_arbitration(conn: sqlite3.Connection, *, status: str | None = "pending",
                     limit: int = 50) -> list[dict]:
    """列队列。默认只列 `pending`——`status=None` 才是全量，避免"顺手把所有历史拉出来"。"""
    if status is not None and status not in ARBITRATION_STATUSES:
        raise StoreError(f"未知的队列状态 {status!r}，合法值 {ARBITRATION_STATUSES}")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise StoreError(f"limit 必须是正整数（收到 {limit!r}）")
    sql = "SELECT * FROM arbitration_queue"
    params: tuple = ()
    if status is not None:
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY opened_at ASC, submission_id ASC LIMIT ?"
    return [_row_to_dict(r, "arbitration_queue") for r in conn.execute(sql, (*params, limit))]


def resolve_arbitration(conn: sqlite3.Connection, submission_id: str, *,
                        status: str = "resolved", note: str, at: str | None = None) -> dict:
    """人工裁决一条待办。**必须写理由**，且**不许改判已裁决的条目**。

    改判要留痕就得另起一条记录；覆盖会让"谁在什么时候为什么这么判"消失，
    这与 `submissions` 不覆盖是同一个理由。
    """
    if status not in ("resolved", "dismissed"):
        raise StoreError(
            f"裁决状态只能是 'resolved' 或 'dismissed'（收到 {status!r}）；"
            "'pending' 不是裁决结果"
        )
    if not isinstance(note, str) or not note.strip():
        raise StoreError("裁决必须写 note：没有理由的收尾等于把待办悄悄删掉")
    current = load_arbitration(conn, submission_id)
    if current is None:
        raise StoreError(f"队列里没有 {submission_id}")
    if current["status"] != "pending":
        raise StoreError(
            f"{submission_id} 已经裁决过（{current['status']}）——不覆盖；"
            "要改结论请另存一条说明或走新的提交"
        )
    with conn:
        conn.execute(
            "UPDATE arbitration_queue SET status = ?, resolved_at = ?, resolution_note = ? "
            "WHERE submission_id = ? AND status = 'pending'",
            (status, at or _now(), note.strip(), submission_id),
        )
    return load_arbitration(conn, submission_id)  # type: ignore[return-value]


# ── 复习层的落库（B-08）──────────────────────────────────────────────────────
# 分工：调度逻辑（rating 映射、due 计算）在 `scheduler.py`，这里只负责"照写、照读"。
# 因此本模块**不 import fsrs**：卡在库里是 JSON（`fsrs_card` 列），反序列化由调度层做。
# 这样两张表都不会长出第二套业务口径。

def save_fsrs_cards(conn: sqlite3.Connection, user_id: str, cards: dict[str, dict],
                    *, at: str | None = None) -> int:
    """写 `user_skill_states`（按 (user, skill) upsert）。返回写入条数。

    **覆盖是正确语义**：一张卡的新状态就是它的全部内容（`due` / `stability` /
    `last_review` 都在里面），没有"保留旧字段"这回事。与 `submissions` 的"不覆盖"
    相反——那边是历史证据，这边是当前状态。

    `cards` 的值必须是 `Card.to_dict()` 的形态（dict）；写成别的会在读回时炸，
    而那时已经落库了——所以这里**先校验类型**，不靠读回发现。
    """
    if not isinstance(cards, dict):
        raise StoreError(f"cards 必须是 skill_id → dict 的映射（收到 {type(cards).__name__}）")
    if not cards:
        return 0
    ensure_user(conn, user_id)
    stamp = at or _now()
    written = 0
    with conn:
        for skill_id, card in cards.items():
            if not isinstance(skill_id, str) or not skill_id:
                raise StoreError(f"skill_id 必须是非空字符串（收到 {skill_id!r}）")
            if not isinstance(card, dict):
                raise StoreError(
                    f"{skill_id} 的卡片必须是 dict（来自 Card.to_dict()），收到 {type(card).__name__}"
                )
            if conn.execute("SELECT 1 FROM skills WHERE skill_id = ?", (skill_id,)).fetchone() is None:
                raise StoreError(
                    f"技能 {skill_id} 未注册——先 register_skills()；"
                    "允许未注册技能写卡片等于让 skills 外键变成装饰"
                )
            conn.execute(
                "INSERT INTO user_skill_states (user_id, skill_id, fsrs_card, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(user_id, skill_id) DO UPDATE SET "
                "fsrs_card = excluded.fsrs_card, updated_at = excluded.updated_at",
                (user_id, skill_id, _dump(card), stamp),
            )
            written += 1
    return written


def load_fsrs_cards(conn: sqlite3.Connection, user_id: str) -> dict[str, dict]:
    """读回该用户所有卡片（`skill_id → Card.to_dict()` 形态）。

    返回原始 dict 而不是 `Card` 对象：本模块不认识 py-fsrs 的类型，
    反序列化交给调度层（`scheduler.cards_from_rows`）——否则两处都能构造卡片，
    一张卡片的 `due` 就有两个解释处。
    """
    rows = conn.execute(
        "SELECT skill_id, fsrs_card FROM user_skill_states WHERE user_id = ? ORDER BY skill_id",
        (user_id,),
    ).fetchall()
    return {r["skill_id"]: _load(r["fsrs_card"]) for r in rows}


def save_review_items(conn: sqlite3.Connection, items) -> tuple[int, int]:
    """写 `review_items`（**幂等**：`review_id` 已存在就跳过）。返回 (新增数, 跳过数)。

    幂等是有意的：同一份提交重跑调度必须不产生第二条队列项。`review_id` 由
    (user, skill, due) 派生（见 `scheduler.review_id_for`），所以"重跑"与"已建过"
    天然撞同一个 id——这里只要不覆盖即可。

    **不覆盖已有项**：一条 `done` 的历史项不该被后来的调度改回 `pending`；
    新的 due 会派生新的 id，历史因此完整保留。
    """
    added = skipped = 0
    with conn:
        for item in items:
            row = item.to_row() if hasattr(item, "to_row") else dict(item)
            cur = conn.execute(
                "INSERT INTO review_items (review_id, user_id, skill_id, kind, task_id, "
                "source_submission_id, due, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(review_id) DO NOTHING",
                (
                    row["review_id"],
                    row["user_id"],
                    row["skill_id"],
                    row["kind"],
                    row.get("task_id"),
                    row.get("source_submission_id"),
                    row["due"],
                    row.get("status", "pending"),
                    _now(),
                ),
            )
            if cur.rowcount:
                added += 1
            else:
                skipped += 1
    return added, skipped


def list_review_items(conn: sqlite3.Connection, user_id: str, *,
                      status: str | None = None) -> list[dict]:
    """该用户的复习项（不带 SQL 排序，顺序由 `scheduler.take_daily` 决定）。

    为什么不在 SQL 里排序：`due` 是 ISO 字符串，跨偏移（`+08:00` 与 `Z`）直接比会判反。
    库里统一存 UTC（`scheduler._iso` 的约定），但读侧不该依赖"写侧一定守规矩"——
    排一次序、只有一处，比两处各自保证一致更可靠。
    """
    sql = "SELECT * FROM review_items WHERE user_id = ?"
    params: tuple = (user_id,)
    if status is not None:
        sql += " AND status = ?"
        params = (user_id, status)
    return [dict(r) for r in conn.execute(sql, params)]


def daily_review_cap(conn: sqlite3.Connection, user_id: str) -> int:
    """读 `users.daily_review_cap`（产品口径"一天别推太多"，默认 2）。"""
    row = conn.execute("SELECT daily_review_cap FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        raise StoreError(f"没有这个用户：{user_id}")
    return int(row["daily_review_cap"])


def set_daily_review_cap(conn: sqlite3.Connection, user_id: str, cap: int) -> int:
    """改每日上限。0 合法（今天不复习）；负数由表约束拒（`CHECK (cap >= 0)`），这里先挡一层。"""
    if isinstance(cap, bool) or not isinstance(cap, int):
        raise StoreError(f"daily_review_cap 必须是整数（收到 {type(cap).__name__}）")
    if cap < 0:
        raise StoreError(f"daily_review_cap 不能为负（收到 {cap}）")
    ensure_user(conn, user_id)
    with conn:
        conn.execute("UPDATE users SET daily_review_cap = ? WHERE user_id = ?", (cap, user_id))
    return cap


def close_due_reviews(conn: sqlite3.Connection, user_id: str, skill_ids, *,
                      at: str) -> int:
    """把 `skill_ids` 下「`at` 之前已到期且仍 pending」的复习项标 `done`。返回关闭条数。

    **为什么必须有这一步**：`review_id` 由 (user, skill, due) 派生，所以下一次复习一定是
    新的一条，旧的那条不会自动消失。若不关闭，队列就只增不减——那不是调度，是堆积。
    「在 `at` 时刻对某技能做了一份有效提交」在语义上就等于"这轮的复习做掉了"，
    不论做对做错（做错会产生新的 due，见 `scheduler.schedule_submission`）。

    **只关已到期的**（`due <= at`）：还没到期的那条是"未来的计划"，被一次提前的提交
    关掉会让后续排期凭空消失——那不是完成，是抹掉。

    只接受 UTC 且秒级精度的 `at`（`scheduler` 的 `_iso` 就是这形态）。跨偏移的 ISO 串
    直接比字符串会判反，所以这里**不做解析**：解析归 `scheduler`，本模块只负责一次比较。
    """
    ids = [s for s in skill_ids if isinstance(s, str) and s]
    if not ids:
        return 0
    placeholders = ", ".join("?" for _ in ids)
    with conn:
        cur = conn.execute(
            f"UPDATE review_items SET status = 'done' "
            f"WHERE user_id = ? AND status = 'pending' AND due <= ? AND skill_id IN ({placeholders})",
            (user_id, at, *ids),
        )
    return int(cur.rowcount or 0)


def save_schedule(conn: sqlite3.Connection, result, *, user_id: str | None = None) -> dict:
    """把一次调度的结果整体落库：先关已完成、再更新卡片、最后加新复习项。

    **顺序三条理由**：
    1. 卡片先于复习项：复习项的 `due` 是卡片算出来的。若卡片写失败而复习项写成功，
       库里会出现"卡片说 8 天后、队列说 1 分钟后"的分裂。
    2. 关旧项先于加新项：新项的 `due` 一定晚于本次提交时刻（FSRS 从 `at` 往后推），
       理论上不会被自己关掉——但先关再加让这个不变量不依赖"理论上"。
    3. 只关「本次有效复习过的技能」：`suppressed` / `no_evidence` 的技能不算复习，
       关掉它们等于用一次不算数的提交抹掉排期。

    幂等：卡片是 upsert、复习项按确定性 id 去重、关旧项是幂等 UPDATE。同一份结果重复落库，
    库里状态不变（第二次 `items_added` 为 0）。
    """
    uid = user_id or getattr(result, "user_id", None)
    if not isinstance(uid, str) or not uid:
        raise StoreError("save_schedule 需要 user_id（结果对象上没有时可显式传）")
    at = getattr(result, "at", None)
    if not isinstance(at, str) or not at:
        raise StoreError("结果对象缺 at（本次提交的评测时刻）——关闭已完成项要用它做分界")

    decisions = list(getattr(result, "decisions", ()))
    effective = [d.skill_id for d in decisions if getattr(d, "action", None) in ("review", "passed")]
    cards = {k: v.to_dict() for k, v in getattr(result, "cards", {}).items()}
    items = list(getattr(result, "items", ()))

    closed = close_due_reviews(conn, uid, effective, at=at)
    written = save_fsrs_cards(conn, uid, cards, at=at)
    added, skipped = save_review_items(conn, items)
    return {
        "user_id": uid,
        "submission_id": getattr(result, "submission_id", None),
        "cards_written": written,
        "items_closed": closed,
        "items_added": added,
        "items_skipped": skipped,
    }
