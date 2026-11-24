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

import contextlib
import hashlib
import itertools
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

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
# v5（B-04d 接线第一步）：新增 `reappraisals`（重评记录）。
# 这张表回答的是"同一份作答被按改后的判据重新打过分，新结论是什么"，
# **不是**新提交：`submissions` 一行不动、不新增行。这样 P 的重放仍旧一份作答只贡献
# 一个 step（翻新了算两次会把一个人的掌握度凭空拉高）。
# v6（B-04h ②）：新增 `unrecoverable_marks`（「确认不可回溯」的终止标记）。
# 它终止的是**重试**，不是**告知**：标记过的行不再每次重评一遍又报一遍，
# 但它照样留在「补不上」名单里、照样要按 B-04g 的口径向学生交代。
# 把标记做成"从名单里消失"是这一层唯一危险的误读——那会变成静默忘掉一个人。
# 标记带撤销字段（`revoked_at` / `revoked_reason`）而不是靠删行：
# 将来若产物被补回来，要能说清"谁在什么时候说过它补不回来"。
SCHEMA_VERSION = 7

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


SCHEMA_SQL_V5 = """
-- v5 / B-04d 接线：重评记录（append-only）。
--
-- 它存在的唯一理由：「标准改完后批量回溯重算」需要一个地方放**新结论**，
-- 而 `submissions.diagnosis` 是历史证据，覆盖它等于把"当时按什么判的"抹掉，
-- 与"诊断记录不覆盖"是同一条铁律。
--
-- 为什么不是"插一条新 submission"：那样同一份作答会在 P 的重放里贡献两个 step，
-- 一个人的掌握度会因为系统改了一次判据而凭空变化。**一份作答永远只算一个 step**，
-- 换的是那一步用的报告，不是步数。
--
-- 主键 (submission_id, revision) 同时是三道约束：
--   ① 同一份提交的重评必须编号（谁先谁后不能靠 created_at 猜，同秒会撞）；
--   ② 已有 revision 不能被覆盖（append-only，改结论只能再起一条）；
--   ③ 外键保证重评指向的提交真实存在。
--
-- 表里**不存** ratio / delta / P：它们都是 `report` 的派生量，落一列就多一处漂移点。
-- 要查"改了多少"就现场算（见 `effective_ratio()` 与 `reappraisal_delta()`）。
CREATE TABLE IF NOT EXISTS reappraisals (
    submission_id TEXT NOT NULL REFERENCES submissions(submission_id),
    revision      INTEGER NOT NULL CHECK (revision >= 1),
    report        TEXT NOT NULL,
    reason        TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    created_at    TEXT NOT NULL,
    PRIMARY KEY (submission_id, revision)
);
"""

SCHEMA_SQL_V6 = """
-- v6 / B-04h ②：「确认不可回溯」的终止标记。
--
-- 它存在的原因是一个重复劳动，不是新功能：v4 之前的提交没有 `artifacts`，
-- 那些行**永远重评不了**，而 `scope_for_task` 每跑一次就把它们重新试一遍、
-- 再报一次 `missing_artifacts`。跑批报告因此随时间线性变长，读的人要从
-- 第 N 次重复里找出"这次真的变了什么"。
--
-- 标记的**唯一**作用是：范围计算时不再把这一行算进"待重评"。
-- 它**不改变**任何一件事的可见性：这些行仍然在 `blocked_submissions()` 里、
-- 仍然进 T1/T2/T3 分档、仍然是 B-04g 要交代的那批人。标记不是"结案"，是"别再试了"。
--
-- 为什么带撤销字段而不是 DELETE：这个标记是一个**判断**（"这份产物不可能再出现"），
-- 判断会被推翻（例如后来做了平台侧抓包、把产物补了回来）。推翻时必须留下
-- "谁在什么时候说过它补不回来"——删行会让那句话消失，而它正是事后复盘唯一能追的线。
--
-- 为什么 `reason` / `marked_by` 都 NOT NULL 且非空：没有作者、没有理由的结案
-- 与"悄悄跳过"没有区别，而这一层的失效方式恰好就是"有人被悄悄跳过"。
CREATE TABLE IF NOT EXISTS unrecoverable_marks (
    submission_id  TEXT PRIMARY KEY REFERENCES submissions(submission_id),
    reason         TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    marked_by      TEXT NOT NULL CHECK (length(trim(marked_by)) > 0),
    marked_at      TEXT NOT NULL,
    revoked_at     TEXT,
    revoked_reason TEXT,
    CHECK ((revoked_at IS NULL) = (revoked_reason IS NULL))
);
"""

SCHEMA_SQL_V7 = """
-- v7 / 学习者申诉（2026-11-24）。
--
-- **为什么不复用 `arbitration_queue`**（我先试过，不行）：
--   ① 那张表的 `submission_id` 是主键——一份提交只能挂一条争议；而学习者申诉
--      完全可能与「judge 双评不一致」并存，两条都得留；
--   ② `judge_agreement` / `threshold` 是 NOT NULL——学习者申诉没有这两个值。
--      为了塞进那张表而编一个 0.5，等于往证据里掺假。
-- 两张表通向同一个出口（人工裁决 → `reappraisal.reappraise()`），但入口与字段不同，
-- 合表只会让两边都别扭。
--
-- **`accepted` 只表示"承认判错了"，不自动重评**。新判据是人的判断（`reappraise()`
-- 的 `task` 参数由人给），本表不替人做这个决定。接受后进"待重评清单"，谁重评、按什么
-- 判据重评，都留痕在 `reappraisals` 里。
CREATE TABLE IF NOT EXISTS appeals (
    appeal_id        TEXT PRIMARY KEY,
    submission_id    TEXT NOT NULL REFERENCES submissions(submission_id),
    user_id          TEXT NOT NULL REFERENCES users(user_id),
    -- 指向哪一条：NULL = 整份报告；否则是断言 id（R2 / R4 …）。前端从报告里取，不许自造。
    target_assertion TEXT,
    reason_text      TEXT NOT NULL CHECK (length(trim(reason_text)) > 0),
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'accepted', 'rejected', 'withdrawn')),
    created_at       TEXT NOT NULL,
    resolved_at      TEXT,
    resolution_note  TEXT,
    -- 与 arbitration_queue 同款：pending 不许带裁决痕迹，非 pending 必须同时有时间和理由。
    -- 这条 CHECK 把"裁决要写理由"变成机械约束，而不是靠代码自觉。
    CHECK (
        (status = 'pending'  AND resolved_at IS NULL     AND resolution_note IS NULL)
     OR (status <> 'pending' AND resolved_at IS NOT NULL AND resolution_note IS NOT NULL)
    )
);

-- 同一份提交、同一个人，**同时只能有一条待处理申诉**：
-- 否则点两下就产生两条，裁决的人会以为有两件事，`reappraise()` 也跟着跑两遍。
-- 用 partial index 而不是把 status 放进主键：已处理的历史申诉可以有多条（那是留痕）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_appeals_one_pending
    ON appeals(submission_id, user_id) WHERE status = 'pending';

-- 待办按"最早开的先看"排，与仲裁队列同一个约定。
CREATE INDEX IF NOT EXISTS idx_appeals_pending ON appeals(status, created_at);
"""

SCHEMA_SQL = (SCHEMA_SQL_V1 + SCHEMA_SQL_V2 + SCHEMA_SQL_V3 + SCHEMA_SQL_V5
              + SCHEMA_SQL_V6 + SCHEMA_SQL_V7)


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
    5: (SCHEMA_SQL_V5, ("reappraisals",)),
    6: (SCHEMA_SQL_V6, ("unrecoverable_marks",)),
    7: (SCHEMA_SQL_V7, ("appeals",)),
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
    "reappraisals": ("report",),
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


_SAVEPOINT_SEQ = itertools.count(1)


@contextlib.contextmanager
def atomic(conn: sqlite3.Connection) -> Iterator[None]:
    """一次原子写，**允许嵌套**。

    ## 为什么不能用 `with conn:`

    两层问题，都会让"原子"变成假原子：

    1. `with conn:` 是 `commit()` / `rollback()` 的包装，**不是 SAVEPOINT**：
       内层退出时 `commit()`，把外层已写的东西一起提交，外层再抛异常也回不去。
    2. 更隐蔽的是**它进入时不 BEGIN**。Python sqlite3 只在遇到 DML 时才隐式开启
       事务，`SELECT` 不会。所以"外层 `with conn:` → 内层先查一下再写"这段里，
       内层看到的是"没有事务"，于是它自己 BEGIN + COMMIT——外层的保护当场失效。

    ## 因此统一用 SAVEPOINT（不再区分有没有活动事务）

    SQLite 的 `SAVEPOINT` 在事务外执行时会**自己开启**一个事务，
    `RELEASE` 最外层 savepoint 即提交；嵌套时只释放自己那一层，不提交外层。
    这一条性质同时解决上面两个问题，也让本函数不依赖驱动的隐式 BEGIN 行为
    （将来换连接模式也不会静默退化）。

    ## 唯一的边界

    **不处理 `executescript`**：它会隐式 `COMMIT`，把外层事务连同 savepoint 一起结束。
    所以建表/迁移那两处（`init_db()` / `migrate()`）仍用 `with conn:`，它们不被嵌套。
    """
    # 名字由模块内计数器生成，不接受外部输入，故 f-string 拼 SQL 无注入面。
    name = f"sp_{next(_SAVEPOINT_SEQ)}"
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        conn.execute(f"ROLLBACK TO {name}")
        conn.execute(f"RELEASE {name}")
        raise
    else:
        conn.execute(f"RELEASE {name}")


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

    with atomic(conn):
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
    with atomic(conn):
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
    with atomic(conn):
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
    with atomic(conn):
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
    with atomic(conn):
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
    with atomic(conn):
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

    **刷新与 INSERT 在同一事务里（round-93 / B-07d ③）**：以前两者分开提交，
    刷新抛异常会留下"有提交、没成绩"的半状态。现在整块包在 `atomic()` 内，
    刷新失败 ⇒ INSERT 也回滚 ⇒ 客户端拿到错误且库里什么都没变（可以安全重试）。
    这依赖 `refresh_*` 内部改用 `atomic()`——它们原本各自 `with conn:` 会提前提交，
    只在外层再套一个 `with conn:` 是**假原子**。

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
    with atomic(conn):
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

            # **必须在同一个事务里**（B-07d ③ / 外审 T6）：刷新抛异常时提交也要撤销，
            # 否则留下"有提交、没成绩"的半状态——成绩层看不到它、报告层看得见它，
            # 而它自己既不会被重算纠正（重放只认提交），也不会被任何断言发现。
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


def user_reports(conn: sqlite3.Connection, user_id: str, *,
                only_settled: bool = False) -> list[dict]:
    """该用户**全部**作答的**当前生效**报告，按 `created_at` 升序（round-80，B-07 的第一个读者）。

    与 `list_submissions` 的分工：那个是"看列表"（倒序、限条数、不带正文）；
    这个是"按时间重放"（正序、不限条数、带整份报告）。

    六点刻意为之：
    - **正序**：状态层要的是"从头往后折"，倒序交给调用方自己翻会诱导出"按列表位置当时间"。
    - **不限条数**：掌握度是累计量，截断等于悄悄少算历史；要限流该在调用方显式做。
    - **`created_at` 排序而不是 `evaluated_at`**：落库时前者已锚到后者，而补录时显式传入的值
      才是"这条记录代表哪一刻"的权威。同一时刻按 `submission_id` 兜底，保证重跑顺序稳定。
    - **给的是 `report`（生效版）不是 `diagnosis`（当时版）**（round-92 / v5）：
      判据改过之后重放必须按新结论算，否则「批量回溯重算」这件事在库里做了、在成绩上没发生。
      键名刻意不叫 `diagnosis`——两个名字相近的报告放在一起，一定有人拿错。
      要读当时那份用 `load_report()`。
    - **一份作答仍只有一行**：重评是在 `reappraisals` 链上追加，不是插新 submission，
      所以这里的行数 = 提交数，P 的 step 数不会因重评而变化。
    - **`only_settled=True` 时排除仲裁未出结果的提交**（round-111 / B-26）：
      双评不一致就说明"这份报告的可信度还没定"，让它推动掌握度等于用未定论据改状态。
      状态**属于队列自己**（v3 刻意没给 `submissions` 加"待仲裁"列，避免第二真相源），
      所以这里用 `NOT EXISTS` 现查队列，而不是抄一个布尔到提交行上。
      裁决完（`resolve_arbitration`）之后同一份提交会自动重新进入重放——不需要补写任何东西。
    """
    settled_filter = ""
    if only_settled:
        settled_filter = (
            " AND NOT EXISTS (SELECT 1 FROM arbitration_queue q "
            "                 WHERE q.submission_id = s.submission_id "
            "                   AND q.status = 'pending')"
        )
    rows = conn.execute(
        "SELECT s.submission_id, s.user_id, s.task_id, s.created_at, s.diagnosis, "
        "       r.report AS latest_report "
        "FROM submissions s "
        "LEFT JOIN reappraisals r ON r.submission_id = s.submission_id "
        "     AND r.revision = (SELECT MAX(revision) FROM reappraisals "
        "                      WHERE submission_id = s.submission_id) "
        "WHERE s.user_id = ?" + settled_filter + " ORDER BY s.created_at ASC, s.submission_id ASC",
        (user_id,),
    ).fetchall()
    return [
        {
            "submission_id": r["submission_id"],
            "user_id": r["user_id"],
            "task_id": r["task_id"],
            "created_at": r["created_at"],
            "report": report,
            # 派生列也按**生效报告**重算：重评可能让某个错误消失或新增，
            # 照读 `submissions.detected_patterns`（落库时那份）会让热度统计停在旧结论上。
            "detected_patterns": _failed_patterns(report),
        }
        for r in rows
        for report in (_load(r["latest_report"]) if r["latest_report"] is not None
                       else _load(r["diagnosis"]),)
    ]


def task_effective_fingerprints(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    """该任务下每份作答**当前生效报告**里的判据指纹（B-04e ⓑ 的取数口）。

    返回每行 `{submission_id, user_id, created_at, fingerprint, source, has_artifacts}`，
    `source` 是 `"reappraisal"` 或 `"original"`（这份指纹从哪份报告读来的），
    `has_artifacts` 是"这份提交有没有留下可重评的输入"（终止标记要拿它复核前提）。
    按 `created_at`、`submission_id` 升序——重评范围的遍历顺序必须可复现，
    否则"演练看到 7 条、真跑动了 6 条"这种差异没法逐条对齐。

    四个刻意的选择：

    1. **取生效报告，不取 `diagnosis`**。落库时那份永远不变，重评过之后它照样是"旧"的，
       于是同一批提交每扫一次就重新进一次范围——重评链被同一批 no-op 修订灌满，
       而"这一轮究竟改动了谁"再也读不出来。生效报告随重评往前滚，范围才会自己收敛。
    2. **读不到指纹就是 `None`，不猜也不补**。v5 之前的报告没有这个键（当时没这个概念），
       唯一诚实的表示是"不知道"。编一个假值回去，"旧的"与"新的"就混成一回事了。
       `None` 算不算进重评范围是**策略**，交给调用方（见 `reappraisal.scope_for_task`）。
    3. **指纹存在但不是字符串 ⇒ 抛**。这只会是坏数据；batch 操作悄悄跳过一行，
       意味着有人的成绩静默停在旧标准上，正是这一层要挡的错。
    4. **不在这里判范围**。本函数只回答"库里每条记录按什么判据判的"，
       不回答"哪些该重评"。混在一起会让"范围"变成一段没法单独测的 SQL。

    成本如实记：一行的 `diagnosis` 整份要反序列化，量级是"该任务的作答数 × 报告大小"。
    这是**冷路径**（一次判据改动跑一次），所以没为它加列或加表达式索引；
    真变成热路径时再加 `submissions(rubric_fingerprint)`，那时才有标定依据。
    """
    rows = conn.execute(
        "SELECT s.submission_id, s.user_id, s.created_at, s.diagnosis, "
        "       (s.artifacts IS NOT NULL) AS has_artifacts, "
        "       r.report AS latest_report "
        "FROM submissions s "
        "LEFT JOIN reappraisals r ON r.submission_id = s.submission_id "
        "     AND r.revision = (SELECT MAX(revision) FROM reappraisals "
        "                      WHERE submission_id = s.submission_id) "
        "WHERE s.task_id = ? ORDER BY s.created_at ASC, s.submission_id ASC",
        (task_id,),
    ).fetchall()

    out = []
    for row in rows:
        from_reappraisal = row["latest_report"] is not None
        report = _load(row["latest_report"] if from_reappraisal else row["diagnosis"])
        fp = report.get("rubric_fingerprint")
        if fp is not None and not (isinstance(fp, str) and fp):
            raise StoreError(
                f"{row['submission_id']} 的 rubric_fingerprint 存在但不是非空字符串"
                f"（{type(fp).__name__}）——坏数据不该被当成「没指纹」跳过"
            )
        out.append({
            "submission_id": row["submission_id"],
            "user_id": row["user_id"],
            "created_at": row["created_at"],
            "fingerprint": fp,
            "source": "reappraisal" if from_reappraisal else "original",
            # 有没有产物，决定"补不补得上"；范围计算要靠它复核终止标记的前提，
            # 所以一起带出来，不额外再查一次（多一次查询就多一个口径分叉的机会）。
            "has_artifacts": bool(row["has_artifacts"]),
        })
    return out


# ── 「确认不可回溯」的终止标记（v6 / B-04h ②）─────────────────────────────────
# 分工：本模块只负责"照写、照读、按前提挡一次"；至于"谁有资格说它补不回来"、
# "标记之后还要不要向学生交代"，是 B-04g 的口径（已拍：照样交代），不在这一层。

def mark_unrecoverable(conn: sqlite3.Connection, submission_id: str, *,
                       reason: str, marked_by: str,
                       created_at: str | None = None) -> dict:
    """给一份提交打「确认不可回溯」。返回落库后的那一行。

    三条前置，**每条都是"标记本身可能是假话"的一种来源**：

    1. **提交必须存在**（外键也管，但这里报得更清楚）；
    2. **它必须真的没有产物**（`artifacts IS NULL`）。有产物的提交此刻就能重评，
       给它打终止标记等于用一句"补不回来"盖住一件马上能做的事；
    3. **不许重复标记**（已标记且未撤销 ⇒ 抛）。重复标记不会带来新信息，
       只会让"谁在什么时候决定的"变成最后一次覆盖——那正好毁掉这张表存在的理由。

    已撤销的标记**可以再打**：撤销意味着前提变过（产物补回来了 / 又丢了），
    那时起一条新记录不覆盖旧的，撤销字段照旧指着上一次的推翻过程。
    换言之，本函数只重置 `marked_at/reason/marked_by` 并清空撤销字段，
    **不删行**——行在，历史就在。
    """
    if not isinstance(reason, str) or not reason.strip():
        raise StoreError("reason 必须写：没有理由的结案与「悄悄跳过」没有区别")
    if not isinstance(marked_by, str) or not marked_by.strip():
        raise StoreError("marked_by 必须写：谁说的这句话，事后必须能追")

    row = conn.execute(
        "SELECT (artifacts IS NOT NULL) AS has_artifacts FROM submissions WHERE submission_id = ?",
        (submission_id,),
    ).fetchone()
    if row is None:
        raise StoreError(f"没有这条提交记录：{submission_id}")
    if row["has_artifacts"]:
        raise StoreError(
            f"{submission_id} 有产物、此刻就能重评——不能标记「确认不可回溯」。"
            "标记的意思是「输入永远回不来了」，不是「这次先不跑」"
        )

    existing = conn.execute(
        "SELECT marked_at, revoked_at FROM unrecoverable_marks WHERE submission_id = ?",
        (submission_id,),
    ).fetchone()
    if existing is not None and existing["revoked_at"] is None:
        raise StoreError(
            f"{submission_id} 已于 {existing['marked_at']} 标记过「确认不可回溯」；"
            "重复标记会覆盖掉「谁在什么时候决定的」——要改理由先撤销"
        )

    stamp = created_at or _now()
    with conn:
        if existing is None:
            conn.execute(
                "INSERT INTO unrecoverable_marks "
                "(submission_id, reason, marked_by, marked_at) VALUES (?, ?, ?, ?)",
                (submission_id, reason.strip(), marked_by.strip(), stamp),
            )
        else:
            conn.execute(
                "UPDATE unrecoverable_marks SET reason = ?, marked_by = ?, marked_at = ?, "
                "revoked_at = NULL, revoked_reason = NULL WHERE submission_id = ?",
                (reason.strip(), marked_by.strip(), stamp, submission_id),
            )
    return load_mark(conn, submission_id)


def revoke_unrecoverable(conn: sqlite3.Connection, submission_id: str, *,
                         reason: str, revoked_at: str | None = None) -> dict:
    """撤销一条终止标记（前提变了：产物补回来了，或当初判断错了）。

    **不删行**：撤销之后这一行仍留着 `marked_at / reason / marked_by`，
    再加一个 `revoked_at`。删掉它等于把那句判断从历史里抹去，
    而"当初是谁说它补不回来"恰恰是复盘时唯一能追的线。
    """
    if not isinstance(reason, str) or not reason.strip():
        raise StoreError("撤销也要理由：这次为什么推翻上一次的判断")
    row = conn.execute(
        "SELECT revoked_at FROM unrecoverable_marks WHERE submission_id = ?",
        (submission_id,),
    ).fetchone()
    if row is None:
        raise StoreError(f"{submission_id} 没有终止标记，无从撤销")
    if row["revoked_at"] is not None:
        raise StoreError(f"{submission_id} 的终止标记已于 {row['revoked_at']} 撤销过")

    with conn:
        conn.execute(
            "UPDATE unrecoverable_marks SET revoked_at = ?, revoked_reason = ? "
            "WHERE submission_id = ?",
            (revoked_at or _now(), reason.strip(), submission_id),
        )
    return load_mark(conn, submission_id)


def load_mark(conn: sqlite3.Connection, submission_id: str) -> dict:
    """读一条标记（含已撤销的）。没有则抛——它只被上面两个函数在"刚写过"之后调。"""
    row = conn.execute(
        "SELECT * FROM unrecoverable_marks WHERE submission_id = ?", (submission_id,)
    ).fetchone()
    if row is None:
        raise StoreError(f"{submission_id} 没有终止标记")
    return dict(row)


def unrecoverable_marks(conn: sqlite3.Connection, task_id: str | None = None) -> dict[str, dict]:
    """**未撤销**的终止标记，`submission_id -> 行`。

    只返回未撤销的：撤销的含义就是"前提不再成立、要重新考虑它"，
    把它继续当成"别试了"是撤销功能的失效方式。历史行仍可通过 `load_mark()` 查到。

    `task_id` 过滤与 `scope_for_task` 同一约定：判据按任务写，跨任务收范围
    会让一个任务的标记影响另一个任务的重试范围。
    """
    sql = ("SELECT m.* FROM unrecoverable_marks m "
           "JOIN submissions s ON s.submission_id = m.submission_id "
           "WHERE m.revoked_at IS NULL")
    params: tuple = ()
    if task_id is not None:
        sql += " AND s.task_id = ?"
        params = (task_id,)
    sql += " ORDER BY m.submission_id"
    return {row["submission_id"]: dict(row) for row in conn.execute(sql, params)}


def blocked_submissions(conn: sqlite3.Connection) -> list[dict]:
    """全库 `artifacts IS NULL` 的提交（"补不上"那一批）及其**生效报告**。

    返回每行 `{submission_id, user_id, task_id, created_at, ratio, report, source}`，
    按 `created_at`、`submission_id` 升序（要能逐条对照，顺序必须可复现）。

    **它不排除已打终止标记的行**（v6）。标记终止的是"重试"，不是"这批人的存在"：
    补不上的名单、T1/T2/T3 分档、要交代的名单，都仍以本函数为取数口。
    若要"还剩谁要试"，看 `unrecoverable_marks()` 的差集，不要在这里加 WHERE——
    在这里加一个条件，等于让标记把一个人从交代名单里静默删掉。

    三个刻意的选择：

    1. **取生效报告与生效分**。学生看到的是生效分；报告里写"当年多少分"而接口给另一个数，
       就是同一件事两套口径。补不上的那份按定义没有重评记录（`rebuild_report` 直接拒），
       所以两者通常相同——**但"通常"不能当保证**：手工插过 `reappraisals` 行的库照样是合法库。
    2. **读不出来就抛，不降级成"0 分"**。`diagnosis` 坏了、`score.ratio` 缺了，都直接报错并指名
       `submission_id`。降级成 0 会让这份提交从"当年给过分、需要交代"掉进"本来就没分"，
       而两档的处置完全相反——静默降级等于替当事人把问题抹掉。
    3. **只回答"哪批补不上、它们各是什么样"**，不回答"该怎么向学生交代"。
       分档与处置在 `baseline.tier_split()`，因为那是"怎么解释"的问题。
    """
    rows = conn.execute(
        "SELECT s.submission_id, s.user_id, s.task_id, s.created_at, s.diagnosis, "
        "       r.report AS latest_report "
        "FROM submissions s "
        "LEFT JOIN reappraisals r ON r.submission_id = s.submission_id "
        "     AND r.revision = (SELECT MAX(revision) FROM reappraisals "
        "                      WHERE submission_id = s.submission_id) "
        "WHERE s.artifacts IS NULL ORDER BY s.created_at ASC, s.submission_id ASC"
    ).fetchall()

    out = []
    for row in rows:
        from_reappraisal = row["latest_report"] is not None
        raw = row["latest_report"] if from_reappraisal else row["diagnosis"]
        try:
            report = _load(raw)
        except ValueError as exc:
            raise StoreError(
                f"{row['submission_id']} 的{'重评' if from_reappraisal else ''}报告不是合法 JSON"
                f"（{exc}）——坏数据不该被当成「当年没分」算进「不需要交代」那一档"
            ) from exc
        if not isinstance(report, dict):
            raise StoreError(
                f"{row['submission_id']} 的报告不是对象（{type(report).__name__}）——无法判断它当年有没有分"
            )
        out.append({
            "submission_id": row["submission_id"],
            "user_id": row["user_id"],
            "task_id": row["task_id"],
            "created_at": row["created_at"],
            "ratio": float(_require(report, "score", "ratio")),
            "report": report,
            "source": "reappraisal" if from_reappraisal else "original",
        })
    return out


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

# 申诉的四种状态。`accepted` 与 `rejected` 的区别是**判分要不要重算**，
# `withdrawn` 是学习者自己撤的（既不承认也不否认判分，只是不追究了）。
APPEAL_STATUSES = ("pending", "accepted", "rejected", "withdrawn")


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

    with atomic(conn):
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
    with atomic(conn):
        conn.execute(
            "UPDATE arbitration_queue SET status = ?, resolved_at = ?, resolution_note = ? "
            "WHERE submission_id = ? AND status = 'pending'",
            (status, at or _now(), note.strip(), submission_id),
        )
    return load_arbitration(conn, submission_id)  # type: ignore[return-value]


# ── 学习者申诉（v7 / 2026-11-24）─────────────────────────────────────────────
# 「判错了」是学习者主动发起的入口，与 `arbitration_queue`（系统发现双评不一致）
# 是两张表、两个入口、同一个出口（人工裁决 → `reappraisal.reappraise()`）。
# 不合表的技术理由写在 `SCHEMA_SQL_V7` 的注释里。

def _appeal_id(conn: sqlite3.Connection, submission_id: str, user_id: str) -> str:
    """由 (提交, 人, 该提交已有几条申诉) 派生 id，不取随机数、也不含时刻。

    **为什么不把 `created_at` 掺进来**（第一版就是这么写的，撞了）：`_now()` 只到秒，
    同一秒内提两条（用户连点、测试循环）会派生同一个 id，撞主键报出来的话是
    "这条申诉已经在队列里了"——而实际情况可能是两条不同理由。用序号就没有这个问题：
    它是该提交下的第几条，确定、单调、与时间轴无关，重放也好对账。
    """
    n = conn.execute(
        "SELECT COUNT(*) FROM appeals WHERE submission_id = ?", (submission_id,)
    ).fetchone()[0]
    digest = hashlib.sha1(f"{submission_id}|{user_id}|{n}".encode("utf-8")).hexdigest()
    return f"ap-{digest[:12]}"


def create_appeal(
    conn: sqlite3.Connection,
    submission_id: str,
    *,
    user_id: str,
    reason_text: str,
    target_assertion: str | None = None,
    created_at: str | None = None,
) -> dict:
    """提一条申诉。返回落库后的行。

    **`user_id` 必须与提交的 `user_id` 一致**——不是安全问题（本地单机没有鉴权），
    是数据完整性问题：允许 A 替 B 申诉，会让"这个人对判分的信任程度"变成无法解释的数。
    """
    if not isinstance(submission_id, str) or not submission_id.strip():
        raise StoreError("申诉缺 submission_id")
    if not isinstance(user_id, str) or not user_id.strip():
        raise StoreError("申诉缺 user_id")
    if not isinstance(reason_text, str) or not reason_text.strip():
        raise StoreError("申诉必须写理由——没有理由的申诉等于要求无条件重判")
    if len(reason_text) > 2000:
        raise StoreError(f"申诉理由过长（{len(reason_text)} 字 > 2000）")

    row = conn.execute(
        "SELECT user_id, diagnosis FROM submissions WHERE submission_id = ?",
        (submission_id.strip(),),
    ).fetchone()
    if row is None:
        raise StoreError(f"提交 {submission_id} 还没落库——申诉必须先有被判的那份报告")
    if row["user_id"] != user_id.strip():
        raise StoreError(
            f"提交 {submission_id} 属于 {row['user_id']}，不是 {user_id}——"
            "只能对自己的判分提申诉"
        )

    target = None if target_assertion is None else str(target_assertion).strip()
    if target == "":
        target = None
    if target is not None:
        # 给了断言 id 就得真的存在：申诉一条不存在的断言，裁决的人会去找一条假证据。
        diagnosis = _load(row["diagnosis"]) if row["diagnosis"] else None
        if not isinstance(diagnosis, dict):
            raise StoreError(
                f"提交 {submission_id} 没有诊断报告，无法指向断言 {target!r}"
                "——整份报告级的申诉请留空 target_assertion"
            )
        known = {item.get("id") for item in (diagnosis.get("assertions") or [])}
        if target not in known:
            raise StoreError(
                f"断言 {target!r} 不在这份报告里（有 {sorted(k for k in known if k)}）"
                "——前端应从报告的 `assertions[].id` 里取，不许自造"
            )

    stamp = created_at or _now()
    with atomic(conn):
        appeal_id = _appeal_id(conn, submission_id.strip(), user_id.strip())
        try:
            conn.execute(
                "INSERT INTO appeals (appeal_id, submission_id, user_id, target_assertion, "
                "reason_text, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (appeal_id, submission_id.strip(), user_id.strip(), target,
                 reason_text.strip(), stamp),
            )
        except sqlite3.IntegrityError as exc:
            # 两种可能：id 重复（同一秒同内容），或"已有待处理申诉"（partial unique index）。
            # 后者更常见，也更有用——告诉人"你已经提过一条了，先等结果"。
            raise StoreError(
                f"这条申诉已经在队列里了（{exc}）——同一份提交同时只能有一条待处理申诉；"
                "要补充理由请等上一条裁决完再提"
            ) from exc
    return load_appeal(conn, appeal_id)  # type: ignore[return-value]


def load_appeal(conn: sqlite3.Connection, appeal_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM appeals WHERE appeal_id = ?", (appeal_id,)).fetchone()
    return None if row is None else _row_to_dict(row, "appeals")


def load_appeals(conn: sqlite3.Connection, submission_id: str) -> list[dict]:
    """一份提交的全部申诉（含历史），按提交先后。空列表 = 从没申诉过，不是错误。"""
    rows = conn.execute(
        "SELECT * FROM appeals WHERE submission_id = ? ORDER BY created_at ASC, appeal_id ASC",
        (submission_id,),
    )
    return [_row_to_dict(r, "appeals") for r in rows]


def list_appeals(conn: sqlite3.Connection, *, status: str | None = "pending",
                 limit: int = 50) -> list[dict]:
    """列申诉。默认只列 `pending`——`status=None` 才是全量，避免顺手把历史都拉出来。"""
    if status is not None and status not in APPEAL_STATUSES:
        raise StoreError(f"未知的申诉状态 {status!r}，合法值 {APPEAL_STATUSES}")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise StoreError(f"limit 必须是正整数（收到 {limit!r}）")
    sql = "SELECT * FROM appeals"
    params: tuple = ()
    if status is not None:
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY created_at ASC, appeal_id ASC LIMIT ?"
    return [_row_to_dict(r, "appeals") for r in conn.execute(sql, (*params, limit))]


def resolve_appeal(conn: sqlite3.Connection, appeal_id: str, *,
                   status: str, note: str, at: str | None = None) -> dict:
    """裁决一条申诉。**必须写理由**，且**不许改判已裁决的**。

    `withdrawn` 是学习者撤回：仍要写 note（表上的 CHECK 要求），调用方传"用户撤回"即可。
    """
    if status not in ("accepted", "rejected", "withdrawn"):
        raise StoreError(
            f"裁决状态只能是 'accepted' / 'rejected' / 'withdrawn'（收到 {status!r}）；"
            "'pending' 不是裁决结果"
        )
    if not isinstance(note, str) or not note.strip():
        raise StoreError("裁决必须写 note：没有理由的收尾等于把申诉悄悄删掉")
    current = load_appeal(conn, appeal_id)
    if current is None:
        raise StoreError(f"没有这条申诉 {appeal_id}")
    if current["status"] != "pending":
        raise StoreError(
            f"{appeal_id} 已经裁决过（{current['status']}）——不覆盖；"
            "要改结论请另存一条说明，或让学习者重新提"
        )
    with atomic(conn):
        conn.execute(
            "UPDATE appeals SET status = ?, resolved_at = ?, resolution_note = ? "
            "WHERE appeal_id = ? AND status = 'pending'",
            (status, at or _now(), note.strip(), appeal_id),
        )
    return load_appeal(conn, appeal_id)  # type: ignore[return-value]


def list_accepted_appeals(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict]:
    """"该重评的清单"：已接受但还没跑过重评的申诉。

    判据是 `reappraisals` 里没有对应记录——**不看申诉自己的时间戳**：
    重评可能晚于裁决好几天，用时间比会漏掉"先接受、后批量重算"的那批。
    """
    sql = (
        "SELECT a.* FROM appeals a "
        "WHERE a.status = 'accepted' "
        "  AND NOT EXISTS (SELECT 1 FROM reappraisals r WHERE r.submission_id = a.submission_id) "
        "ORDER BY a.resolved_at ASC, a.appeal_id ASC LIMIT ?"
    )
    return [_row_to_dict(r, "appeals") for r in conn.execute(sql, (limit,))]



# 「裁决 → 重评 → 落差异」的最后一步。判定与执行在 `reappraisal.py`，这里只存取。
#
# 三条设计决定：
# 1. **一份作答只算一个 step**。重评不新增 `submissions` 行，只在该提交的
#    `reappraisals` 链上追加一条；重放读到的是"最新结论"，步数不变。
# 2. **append-only**。已有 revision 不许改、不许删。要改结论就再追加一条——
#    覆盖会让"谁在什么时候按什么改成了什么"消失，与 `submissions` 不覆盖同理。
# 3. **身份字段必须与原件一致**。重评可以把分数改掉，但不许把 `submission_id` /
#    `task_id` 换成别的：那已经不是"重评这份作答"，而是"拿重评的名义换一份证据"。

def save_reappraisal(
    conn: sqlite3.Connection,
    submission_id: str,
    report: dict,
    *,
    reason: str,
    created_at: str | None = None,
) -> dict:
    """追加一条重评记录，revision 自动取 `max+1`。返回新记录。

    校验四件事，任一不成立就抛（不写半条）：
      - 报告的身份字段与原件一致（`submission_id`、`task_id`）；
      - 报告带 `user_id` 时（有些报告不含）必须与提交人一致；
      - `score.ratio` 是 [0,1] 内的数；
      - `reason` 非空——没有理由的重评和"偷偷改了分数"没有区别。
    """
    if not isinstance(submission_id, str) or not submission_id.strip():
        raise StoreError("重评缺 submission_id")
    if not isinstance(report, dict):
        raise StoreError(f"重评的 report 必须是 dict（收到 {type(report).__name__}）")
    if not isinstance(reason, str) or not reason.strip():
        raise StoreError(
            "重评必须写 reason：这是「改判据之后哪一条变了」的唯一线索，"
            "没有它的重评事后无法复核"
        )

    original = load_submission(conn, submission_id)     # 不存在就抛
    new_sub = _require(report, "submission")
    if new_sub.get("submission_id") != submission_id:
        raise StoreError(
            f"重评报告的 submission_id 是 {new_sub.get('submission_id')!r}，"
            f"与要重评的 {submission_id!r} 不一致——重评不许换证据"
        )
    if new_sub.get("task_id") != original["task_id"]:
        raise StoreError(
            f"重评报告的 task_id 是 {new_sub.get('task_id')!r}，原件是 {original['task_id']!r}"
            "——换任务等于换了一份作答，不是重评"
        )
    claimed_user = new_sub.get("user_id")
    if claimed_user is not None and claimed_user != original["user_id"]:
        raise StoreError(
            f"重评报告声明的 user_id={claimed_user!r} 与提交人 {original['user_id']!r} 不一致"
            "——重评不能把成绩记到别人头上"
        )

    ratio = _require(report, "score", "ratio")
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0.0 <= float(ratio) <= 1.0:
        raise StoreError(f"重评报告的 score.ratio 必须是 [0,1] 内的数值（收到 {ratio!r}）")

    last = conn.execute(
        "SELECT COALESCE(MAX(revision), 0) FROM reappraisals WHERE submission_id = ?",
        (submission_id,),
    ).fetchone()[0]
    revision = int(last) + 1

    with atomic(conn):
        conn.execute(
            "INSERT INTO reappraisals (submission_id, revision, report, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (submission_id, revision, _dump(report), reason.strip(), created_at or _now()),
        )
    return load_reappraisal(conn, submission_id)  # type: ignore[return-value]


def load_reappraisal(
    conn: sqlite3.Connection, submission_id: str, *, revision: int | None = None
) -> dict | None:
    """读一条重评记录。`revision=None` 取**最新**一条；没有重评过返回 `None`。

    "没重评过"是正常状态（绝大多数提交都是），所以返回 `None` 而不是抛错。
    """
    if revision is None:
        row = conn.execute(
            "SELECT * FROM reappraisals WHERE submission_id = ? ORDER BY revision DESC LIMIT 1",
            (submission_id,),
        ).fetchone()
    else:
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise StoreError(f"revision 必须是正整数（收到 {revision!r}）")
        row = conn.execute(
            "SELECT * FROM reappraisals WHERE submission_id = ? AND revision = ?",
            (submission_id, revision),
        ).fetchone()
    return None if row is None else _row_to_dict(row, "reappraisals")


def list_reappraisals(conn: sqlite3.Connection, submission_id: str | None = None) -> list[dict]:
    """列重评记录：给定提交则按 revision 升序列它的链，否则列全库（按时间）。"""
    if submission_id is None:
        rows = conn.execute(
            "SELECT * FROM reappraisals ORDER BY created_at ASC, submission_id ASC, revision ASC"
        )
    else:
        if not isinstance(submission_id, str) or not submission_id.strip():
            raise StoreError("submission_id 必须是非空字符串")
        rows = conn.execute(
            "SELECT * FROM reappraisals WHERE submission_id = ? ORDER BY revision ASC",
            (submission_id,),
        )
    return [_row_to_dict(r, "reappraisals") for r in rows]


def effective_report(conn: sqlite3.Connection, submission_id: str) -> dict:
    """这份作答**当前生效**的报告：有重评取最新一条，否则是落库时那份。

    这是"重评到底有没有改变成绩"的唯一入口。要读**当时**那份请用 `load_report()`——
    两个函数分开而不是加个 `effective=True` 开关：叫 `load_report` 却可能返回另一份报告，
    迟早有人拿它去对账历史。
    """
    latest = load_reappraisal(conn, submission_id)
    if latest is not None:
        return latest["report"]
    return load_report(conn, submission_id)


def effective_ratio(conn: sqlite3.Connection, submission_id: str) -> float:
    """这份作答当前生效的分数（`score.ratio`）。原始分数仍看 `submissions.assert_score`。"""
    return float(_require(effective_report(conn, submission_id), "score", "ratio"))



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
    with atomic(conn):
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
    with atomic(conn):
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
    with atomic(conn):
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
    with atomic(conn):
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
