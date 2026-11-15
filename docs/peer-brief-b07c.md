<!-- status=ACTIVE unpublished generated-by=tools/gen_peer_brief_b07c.py -->
# 外部审核委托包 —— B-07c 掌握度快照（round-88）

> 本包**尚未发送给任何外部 AI**。发起权在维护者手里；AI 只备包、不外发。

## 0 任务书（请按这里交付）

1. 你只拿到这一份文件，**看不到仓库，也不需要仓库**。所有需要的源码在 §3 逐字内联。
2. 目标不是给总体评价，而是找出**可能存在的 BUG 与契约漏洞**。请按 §4 的 T1–T9 逐条判定。
3. 每条结论必须标 **【事实】**（能指到 §3 的文件名、函数名或具体表达式）或
   **【推测】**（需要更多信息才能定），两者不许混写。
4. 每条判定都要给一个**十分钟内能做的验证**：输入 → 期望 → 实际 → 什么算通过。
   给不出的写入【假设】，不要写成结论。
5. **不要求读代码库、不要求联网、不要引用本包里没出现的版本号 / 条款号 / API 名。**
   本包不提供任何真实姓名、学校、机器路径或凭据，也请不要索要。
6. 已拍板的产品口径不翻案；但「按此口径落地会留下什么洞」属于审核范围。
7. 交付格式（硬性）：① 核证总表（编号 / 判定 / 依据 / 事实或推测）；② 每条的最小复现；
   ③ 待拍板项写成两个选项各自代价，**不替人拍板**；④ 不写「这样应该没问题」。

## 1 现状（中性陈述，全部可在 §3 复核）

- 项目是「Agent 开发学习领域的自适应诊断与训练闭环」：对学习者的 Agent 执行轨迹做诊断，
  把错误模式映射到技能缺口，按遗忘规律调度任务变体，并生成训练建议排期。
- 本轮（B-07c）新增第八张表 `mastery_snapshots`，每个 (用户, 技能) 一行，
  存 `mastery_p` / `lam` / `p0` / `evidence_count` / `last_submission_id` / `updated_at`；
  `PRAGMA user_version` 升到 2，配一个增量迁移函数。
- 代码自述的契约：`build_series()` 从提交历史重放出的 P 是**唯一权威**，
  表里只是缓存；校核时现场重放、不一致以重放为准；只存未衰减的 `P_raw`；
  没有证据的技能不写行（不写 0）；写路径在提交落库后自动刷新，读路径只读缓存。
- 边界（代码自述）：排名功能未开、平滑系数与初值未经数据标定、衰减曲线借自 FSRS 且未标定。

## 2 请优先审这四个「我自己判过」的地方

上一轮全部代码、测试、变异、文档由**同一个作者（一个 AI）**写出，本地绿灯只能证明
「实现符合它自己写的测试」。下面四处是作者对自己的判断，**本地裁判覆盖不到**：

| # | 作者自己的判断 | 请你核什么 |
|---|---|---|
| A | 「新建第八张表虽与数据模型文档里『不另设掌握度表』的原则字面冲突，但没违背精神」 | 这个自我豁免成立吗？有没有更便宜的写法？有没有被绕开的约束？ |
| B | 「开工条件被提前解除，但排名功能仍未开、开榜前必须先改口径」 | 本轮代码有没有悄悄产出名次、榜单或跨用户比较口径？ |
| C | 「27 条测试 + 18 组变异全绿，所以实现可信」 | 有没有测试覆盖不到的真实故障形态？哪条测试可能是恒真断言？ |
| D | 「快照只存 P_raw，衰减是读法不是状态」 | 有没有任何路径把某个时间点钉进库里？ |

## 3 逐字内联源码

> 每块与磁盘文件的 sha256 见 §6；`python tools/gen_peer_brief_b07c.py --check`
> 会把包内块抽出来与磁盘重新比对。**块内不改动一个字节**，包括注释。

### 3.1 `src/arena/mastery_snapshot.py` — 本轮新增：快照的算、写、校验、排序

<!-- BEGIN_FILE src/arena/mastery_snapshot.py -->
```python
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

# 不一致的原因词表。只这六种，写错拼法会在测试里被单独抓住。
REASONS = (
    "ok",                 # 一致
    "missing_snapshot",   # 重放有值、库里没行（写路径没刷新 / 行被删了）
    "stale_value",        # P 值对不上
    "stale_params",       # P 碰巧对，但 λ / p0 / 证据数 / 最后一条提交对不上
    "orphan_snapshot",    # 库里有行、重放却无证据（不该有快照）
)


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

    注意它**不删**"库里有、重放无证据"的行——那是 `refresh_one()` 逐条删的，
    而 `recompute_all()` 正是靠逐条调它实现的，所以孤儿行照样会被清掉。
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
        reason = "ok" if stored is None else "orphan_snapshot"
        return SnapshotCheck(user_id, skill_id, stored_p, None, reason,
                             stored is None)
    if stored is None:
        return SnapshotCheck(user_id, skill_id, None, replayed_p,
                             "missing_snapshot", False)

    if stored_p != replayed_p:
        return SnapshotCheck(user_id, skill_id, stored_p, replayed_p,
                             "stale_value", False)

    same_params = (
        stored["lam"] == row["lam"]
        and stored["p0"] == row["p0"]
        and stored["evidence_count"] == row["evidence_count"]
        and stored["last_submission_id"] == row["last_submission_id"]
    )
    if not same_params:
        return SnapshotCheck(user_id, skill_id, stored_p, replayed_p,
                             "stale_params", False)
    return SnapshotCheck(user_id, skill_id, stored_p, replayed_p, "ok", True)


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
```
<!-- END_FILE src/arena/mastery_snapshot.py -->

### 3.2 `src/arena/store.py` — v2 表结构与迁移、提交写路径

<!-- BEGIN_FILE src/arena/store.py -->
```python
"""状态层的落库入口（M1 第一步）：把一份诊断报告写进 SQLite，并能重启后读回。

**为什么这一步现在才做**：round-74 的骨架盘点给出一条明确的分界线——
「一次提交内」的能力基本齐全（评分 / 诊断 / 缺口聚合 / 作废 / 对账），
「跨提交」的能力一件都没有。而跨提交的一切（掌握度 P、错误热度、FSRS 调度、
四因子排期）都卡在同一件事上：没有地方存上一次的结果。本模块补的就是这块地基。

四条设计决定，都不是"顺手这么写"：

1. **不用 ORM**。`requirements.txt` 已 pin 死，`tests/test_dependencies.py` 会离线比对已安装版本；
   引 sqlalchemy 是为一件事加一整层依赖。8 张表（v1 七张 + v2 的 `mastery_snapshots`）
   的 DDL 手写比 ORM 映射更短，
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
SCHEMA_VERSION = 2

# 必须按依赖顺序建（被引用的先建）：users / skills → tasks → submissions → user_skill_states / review_items。
# **拆成 v1/v2 两段**：迁移测试需要一个"真的 v1 老库"来证明 from=1，
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

SCHEMA_SQL = SCHEMA_SQL_V1 + SCHEMA_SQL_V2

# 需要 JSON 编解码的列：写库前 dumps，读库后 loads（NULL 保持 NULL，不转成空数组）。
JSON_COLUMNS = {
    "skills": ("prerequisites",),
    "tasks": ("secondary_skill_ids",),
    "error_patterns": ("detection_rule", "mapped_skill_ids"),
    "submissions": ("trace", "judge_scores", "detected_patterns", "diagnosis"),
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

    三条硬规矩：
    1. **只做增量**：v1→v2 只补 `mastery_snapshots` 表与索引，`ALTER` 一个字段都不改。
       所以任何一步失败，旧库仍是可用的一堆旧表，不会变砖。
    2. **回填是"重算"，不是"猜"**：v1 库里已经有提交时，按 `mastery.build_series()`
       逐用户逐技能重放，把结果写进快照表；原 `submissions` 一个字节不动。
    3. **不许降级**：`PRAGMA user_version > target` 直接抛错。看见看不懂的新版本还继续写，
       比停下来更危险。

    `target` 只接受正整数；它是内部常量，不来自用户输入。
    """
    if isinstance(target, bool) or not isinstance(target, int) or target < 1:
        raise StoreError("migrate 的 target 必须是正整数")
    current = schema_version(conn)
    if current > target:
        raise StoreError(
            f"库版本 {current} 高于目标 {target}——拒绝降级，先确认是不是用旧代码开了新库"
        )
    if current == target:
        return {"from": current, "to": target, "added_tables": [], "backfilled": 0}

    with conn:
        if current == 0:
            # 全新库：一次建齐（v2 的表有外键指向 users/skills，不能只建它）。
            conn.executescript(SCHEMA_SQL)
        else:
            # 已有 v1 库：只补 v2 新增的部分，旧七张表一个字段都不碰。
            conn.executescript(SCHEMA_SQL_V2)

    backfilled = 0
    if current < 2 and target >= 2:
        from . import mastery_snapshot  # 局部导入：mastery 会反向引用 store

        backfilled = mastery_snapshot.recompute_all(conn)

    with conn:
        conn.execute(f"PRAGMA user_version = {target}")
    return {
        "from": current,
        "to": target,
        "added_tables": ["mastery_snapshots"] if current < 2 <= target else [],
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
            "trace, assert_score, judge_scores, judge_agreement, detected_patterns, diagnosis, "
            "fsrs_rating, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                submission_id,
                user_id,
                task_id,
                sub.get("task_git_hash"),
                agent_endpoint_url,
                _dump(trace),
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
```
<!-- END_FILE src/arena/store.py -->

### 3.3 `src/arena/mastery.py` — P 的权威公式与重放（本轮未改算法）

<!-- BEGIN_FILE src/arena/mastery.py -->
```python
"""状态层的第一个跨提交消费者：掌握度 P（B-07）。

**这一步解决什么**：round-75 之后 `submissions` 表里已经有数据，但没有任何代码读它——
"存了但没人看"等于白存。P 是第一个真正的消费者，也是 B-08（复习调度）与 B-09（四因子排期）
的共同前置。

## 公式只抄一处

`docs/data-model.md` §5.2（round-70 P1 拍板）：

    P_k = (1 - λ)·P_{k-1} + λ·c_k

λ 是固定平滑系数，`c_k` 是第 k 次**计入**的证据给出的表现值 ∈ [0,1]。
**唯一权威定义在 data-model**；本模块只实现它，不另立口径。

## 四条设计决定

1. **`c_k` 是权重加权通过率，不是 `score.ratio`。**
   一份提交可能同时压到多个技能（R2 属 `multi_step_planning`、R3 属 `failure_retry`、
   R4 属 `tool_param_validation`）。拿整份 `ratio` 去更新每个技能，等于让 R2 答错也拉低
   `tool_param_validation` 的掌握度——那不是"聚合"，是串味。所以按断言自己的 `skill_id`
   分组，`c = Σ(通过断言权重) / Σ(计入断言权重)`。

2. **`unscored` / `blocked` 从分子分母一起剔除，不是记 0 分。**
   决策 7「数据缺失 ≠ 能力不足」。记 0 分会把"没上报 trace"惩罚成"这个技能不会"，
   而这恰好是学习者最容易踩、又最不该扣掌握度的坑。剔除后若该技能在这份提交上
   **一条计入断言都没有**，则这次提交对该技能**不产生证据**（返回 `None`），P 原封不动。

3. **时间轴用 `created_at`（落库锚点），不是列表顺序。**
   `save_submission` 已把 `created_at` 锚到 `submission.evaluated_at`。补录历史时若按插入顺序
   算，P 的递推顺序会错，而且错得看不出来（数值仍单调）。排序**按解析后的时刻**比较，
   不按字符串——`2026-11-07T12:00:00+08:00` 与 `2026-11-07T06:00:00Z` 是同一时刻，
   字符串比较却会判成"前者更晚"。

4. **初值不发明，用第一次观测播种；不实现衰减。**
   - **初值**：`p0=None`（默认）时 `P_1 = c_1`。另一条路是给一个中性先验（如 0.5），
     但那会凭空造一个没有依据的数，且让"交了一份满分"首轮只拿到 0.65。data-model 明写
     「P 的落字段与初值未定」，所以这里**两者都可配**，默认选"不发明先验"，并把它标为**假设**。
   - **衰减**：本模块**仍不内置衰减**，但不再是缺口——衰减在 `arena/decay.py`（B-08b），
     由调用方显式传 `as_of` 才生效（`decayed_at()`）。分开的理由是时间轴不同：
     `replay()` 的时间轴是"提交发生的时刻"，衰减的时间轴是"最后一次练过到 as_of 之间"，
     把两者揉进一个函数会让"我没给 as_of 时 P 是多少"变得说不清。
     **默认仍不衰减**：不传 `as_of` 的老调用行为一个字没变，所以 `test_decay_*` 的旧断言
     改成"不传 as_of ⇒ 两条序列相同"，而不是删掉。

5. **门禁状态只留痕，不参与计算（为 A→C→B 留切换口）。**
   现行口径是 **A**：整份不合格（`disqualified`）时，credit 层照常判定的证据**照样计入** P
   （依据 data-model §5.1「门禁只管分数，不管观测」）。这条口径有个不利后果，已被实测固定：
   **一份"没交产物"的提交会让该技能的 `c = 1.0`**，而初值播种使**首次观测直接等于 1.0** ——
   即"交白卷一次"与"真做对一次"在 P 上不可区分（`test_one_white_paper_looks_like_a_perfect_run`）。
   但**改不改是产品口径**，本模块不擅自改；它做的是把切换成本降下来：
   每条证据与每一步递推都记 `gate_status`（`ok` / `disqualified` / `voided`），
   将来切到 C（降权计入）或 B（排除）**只需在计入层加一条过滤**，
   不必回头改已积累的历史，也**不必**把报告里的 credit 断言改标 `blocked`
   （那会用评分逻辑吞掉诊断数据，是多份文档明确禁止的实现方式）。

## P 落在哪（round-88 / B-07c 更新）

**本模块仍然一行库都不写**：`build_series()` / `replay()` / `decayed_at()` 全是纯读，
唯一数据来源是 `submissions`。这一点有测试锁住
（`test_replay_never_writes_to_the_fsrs_card_table`）。

P 的**缓存**落在独立表 `mastery_snapshots`（v2），由 `mastery_snapshot.py` 负责写与校验；
`user_skill_states` 至今没有 P 字段，一个字段都没动——B-08b 的
「没卡 ≠ 忘光了」契约靠那张表的空/有区分，不能被"只有 P 没卡"的半行污染。
重放是唯一权威，快照不一致时以重放为准；权威口径见 `docs/data-model.md` §5.3。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

# 平滑系数默认值。**这是假设，不是标定结果**：没有答题数据之前任何具体数值都是拍的。
# 取 0.3（新证据占三成、历史占七成）落在 EMA 类掌握度估计的常用区间内，
# 但必须经 B-07b 的回测（同技能变体的达标预测）才能定值。改这个数不影响公式正确性。
LAMBDA_DEFAULT = 0.3

# 门禁状态词表。只三种，且在 `replay()` 里校验——拼错会静默躲过将来的过滤规则。
GATE_STATUSES = ("ok", "disqualified", "voided")


class MasteryError(ValueError):
    """输入不成立（越界的 λ / P / c、缺字段、时间无法解析）。不吞、不 clamp。"""


# ---------------------------------------------------------------------------
# 证据：一份报告 → 某技能吃到的表现值
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ignored:
    """被排除在掌握度之外的断言，附原因。留痕是为了"少算"这件事可审计。"""

    assertion_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"assertion_id": self.assertion_id, "reason": self.reason}


@dataclass(frozen=True)
class Evidence:
    """一次提交对**一个**技能给出的证据。

    `counts=False` 表示这份提交在该技能上没有可用证据（全 `unscored` / `blocked` /
    门禁零权重）。这时**不许**把它当成 0 分——那是决策 7 明令禁止的换算。
    """

    submission_id: str
    skill_id: str
    at: str
    passed_weight: float
    counted_weight: float
    ignored: tuple[Ignored, ...] = ()
    # 这份提交当时的门禁状态：`ok` / `disqualified` / `voided`。
    # **只留痕、不参与计算**（现行口径仍是 A：证据照记）。留它的唯一目的是让
    # 「A → C（降权）→ B（排除）」成为计入层的一条过滤，而不是回头改已积累的历史。
    gate_status: str = "ok"

    @property
    def counts(self) -> bool:
        return self.counted_weight > 0

    @property
    def c(self) -> float:
        if not self.counts:
            raise MasteryError(
                f"{self.submission_id} 在技能 {self.skill_id} 上没有计入断言"
                "（全是 unscored/blocked 或零权重），不构成证据，不能当 0 分用"
            )
        return self.passed_weight / self.counted_weight

    def ignored_reasons(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.ignored:
            key = item.reason.split(":", 1)[0]
            out[key] = out.get(key, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "skill_id": self.skill_id,
            "at": self.at,
            "counts": self.counts,
            "c": _round(self.c) if self.counts else None,
            "passed_weight": _round(self.passed_weight),
            "counted_weight": _round(self.counted_weight),
            "gate_status": self.gate_status,
            "ignored": [i.to_dict() for i in self.ignored],
        }


def _round(value: float) -> float:
    """只为输出好看。**比较与递推一律用原值**，四舍五入攒到第 10 次会漂。"""
    return round(value, 6)


def skill_evidence(report: dict, skill_id: str, *, at: str | None = None) -> Evidence:
    """从一份诊断报告里取出某技能的证据。

    计入规则全部来自报告自带的 `attribution_policy`，本函数不内置任何状态白名单——
    否则口径会分叉成"代码说 pass/fail 算"与"任务 YAML 说 counts_toward_mastery"
    两套，将来改 YAML 时报告变了而 P 不变。
    """
    if not isinstance(report, dict):
        raise MasteryError(f"report 应是 dict，收到 {type(report).__name__}")
    submission = report.get("submission") or {}
    submission_id = submission.get("submission_id")
    if not isinstance(submission_id, str) or not submission_id:
        raise MasteryError("报告缺 submission.submission_id")
    when = at or submission.get("evaluated_at")
    if not isinstance(when, str) or not when:
        raise MasteryError(f"{submission_id} 缺 evaluated_at，且未显式给 at——时间轴不能猜")

    policy = report.get("attribution_policy")
    if not isinstance(policy, dict) or not policy:
        raise MasteryError(f"{submission_id} 缺 attribution_policy，无法判断哪些状态计入掌握度")

    # 门禁状态只如实记录，不改任何判定（现行口径＝A）。缺字段就报错而不是默认 `ok`：
    # 默认成 `ok` 会把"整份不合格"记成"合格"，正好抹掉将来切 B/C 唯一需要的那个信号。
    score = report.get("score")
    if not isinstance(score, dict) or "disqualified" not in score or "voided" not in score:
        raise MasteryError(f"{submission_id} 缺 score.disqualified / score.voided，无法记录门禁状态")
    if score["disqualified"]:
        gate_status = "disqualified"
    elif score["voided"]:
        gate_status = "voided"
    else:
        gate_status = "ok"

    passed = counted = 0.0
    ignored: list[Ignored] = []
    for a in report.get("assertions") or []:
        if a.get("skill_id") != skill_id:
            continue
        state = a.get("state")
        rule = policy.get(state)
        if not isinstance(rule, dict) or "counts_toward_mastery" not in rule:
            raise MasteryError(
                f"{submission_id} 的断言 {a.get('id')} 状态 {state!r} 不在 attribution_policy 里"
            )
        weight = a.get("weight", 0)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise MasteryError(f"{submission_id} 的断言 {a.get('id')} 权重非数值：{weight!r}")
        if not rule["counts_toward_mastery"]:
            ignored.append(Ignored(str(a.get("id")), f"not_counted:state={state}"))
            continue
        if weight <= 0:
            # 门禁层 weight 恒为 0（见 schema：gate 层保留 weight 字段只为结构统一）。
            # 它计入掌握度却没有任何权重 ⇒ 失败在 P 上不留痕。当前任务的门禁断言 skill_id 全为 null，
            # 所以现状无实际影响；一旦有任务给门禁断言写 skill_id，这里就会漏记失败（登记 B-07b）。
            ignored.append(Ignored(str(a.get("id")), f"zero_weight:state={state}"))
            continue
        counted += weight
        if state == "pass":
            passed += weight

    return Evidence(
        submission_id=submission_id,
        skill_id=skill_id,
        at=when,
        passed_weight=passed,
        counted_weight=counted,
        ignored=tuple(ignored),
        gate_status=gate_status,
    )


# ---------------------------------------------------------------------------
# 递推
# ---------------------------------------------------------------------------


def _check_unit(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MasteryError(f"{name} 必须是数值，收到 {type(value).__name__}")
    if not 0.0 <= float(value) <= 1.0:
        raise MasteryError(f"{name} 必须在 [0,1]，收到 {value}")
    return float(value)


def update_p(p_prev: float, c: float, lam: float = LAMBDA_DEFAULT) -> float:
    """`P_k = (1-λ)·P_{k-1} + λ·c`。越界一律报错——clamp 会把"模型/数据错"伪装成合法低分。"""
    _check_unit(p_prev, "p_prev")
    _check_unit(c, "c")
    if isinstance(lam, bool) or not isinstance(lam, (int, float)):
        raise MasteryError(f"λ 必须是数值，收到 {type(lam).__name__}")
    if not 0.0 < float(lam) <= 1.0:
        # λ=0 表示永不学习（P 恒等于初值），那不是平滑而是关掉功能；λ>1 会外推到 [0,1] 之外。
        raise MasteryError(f"λ 必须在 (0,1]，收到 {lam}")
    return (1.0 - float(lam)) * float(p_prev) + float(lam) * float(c)


@dataclass(frozen=True)
class Step:
    """一步递推的完整留痕：改公式时能一眼看出哪一步的数变了。"""

    submission_id: str
    at: str
    c: float
    p_before: float | None
    p_after: float
    counted_weight: float
    seeded: bool  # 这一步是"初值播种"（P_1 = c_1），没走递推式
    gate_status: str = "ok"  # 这份提交当时的门禁状态，只留痕

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "at": self.at,
            "c": _round(self.c),
            "p_before": None if self.p_before is None else _round(self.p_before),
            "p_after": _round(self.p_after),
            "counted_weight": _round(self.counted_weight),
            "seeded": self.seeded,
            "gate_status": self.gate_status,
        }


@dataclass(frozen=True)
class Skipped:
    submission_id: str
    at: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"submission_id": self.submission_id, "at": self.at, "reason": self.reason}


@dataclass(frozen=True)
class MasterySeries:
    user_id: str
    skill_id: str
    lam: float
    p0: float | None
    steps: tuple[Step, ...] = ()
    skipped: tuple[Skipped, ...] = ()
    ignored: dict[str, int] = field(default_factory=dict)

    @property
    def p(self) -> float | None:
        """最新掌握度。没有任何证据时是 `None`——不是 0，也不是 0.5。"""
        return self.steps[-1].p_after if self.steps else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "skill_id": self.skill_id,
            "lambda": self.lam,
            "p0": self.p0,
            "p": None if self.p is None else _round(self.p),
            "observations": len(self.steps),
            "skipped": len(self.skipped),
            "steps": [s.to_dict() for s in self.steps],
            "skipped_detail": [s.to_dict() for s in self.skipped],
            "ignored": dict(sorted(self.ignored.items())),
        }


_EPOCH_NAIVE = timezone.utc


def parse_instant(value: str) -> datetime:
    """解析 ISO 时刻。`Z` 结尾、纯日期、带偏移都要能处理——否则补录数据会整批报错。"""
    if not isinstance(value, str) or not value.strip():
        raise MasteryError(f"时刻必须是非空字符串，收到 {value!r}")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MasteryError(f"无法解析时刻 {value!r}：{exc}") from exc
    # 无时区的按 UTC 解释并**在输出里标出来**（naive 与 aware 相减会直接抛错，
    # 混用时静默当本地时间会让排序在夏令时边界上翻车）。
    return parsed.replace(tzinfo=_EPOCH_NAIVE) if parsed.tzinfo is None else parsed


def replay(
    evidence: Iterable[Evidence],
    *,
    user_id: str,
    skill_id: str,
    lam: float = LAMBDA_DEFAULT,
    p0: float | None = None,
) -> MasterySeries:
    """按时间把证据折成一条 P 序列。不计数的证据进 `skipped`，且**不推进** P。"""
    if isinstance(lam, bool) or not isinstance(lam, (int, float)) or not 0.0 < float(lam) <= 1.0:
        raise MasteryError(f"λ 必须在 (0,1]，收到 {lam!r}")
    if p0 is not None:
        _check_unit(p0, "p0")

    items = list(evidence)
    # 稳定排序：同一时刻的多份提交按 submission_id 定序，保证重跑结果一致。
    # 先把时刻解析出来再排——字符串比较在混合偏移下会给出错误的先后。
    decorated = [(parse_instant(e.at), e.submission_id, e) for e in items]
    decorated.sort(key=lambda t: (t[0], t[1]))

    steps: list[Step] = []
    skipped: list[Skipped] = []
    ignored: dict[str, int] = {}
    p: float | None = None

    for _, _, ev in decorated:
        for reason, count in ev.ignored_reasons().items():
            ignored[reason] = ignored.get(reason, 0) + count
        if ev.skill_id != skill_id:
            raise MasteryError(f"证据的技能是 {ev.skill_id}，与本次回放的 {skill_id} 不一致")
        if ev.gate_status not in GATE_STATUSES:
            raise MasteryError(
                f"{ev.submission_id} 的 gate_status={ev.gate_status!r} 不在 {GATE_STATUSES}"
                "——拼错的状态名会让将来的降权/排除过滤漏掉这一条"
            )
        if not ev.counts:
            skipped.append(Skipped(ev.submission_id, ev.at, "no_counted_assertion"))
            continue
        c = ev.c
        if p is None:
            if p0 is None:
                p = c
                steps.append(Step(ev.submission_id, ev.at, c, None, p, ev.counted_weight, True,
                                  ev.gate_status))
                continue
            p = float(p0)
        before = p
        p = update_p(p, c, lam)
        steps.append(Step(ev.submission_id, ev.at, c, before, p, ev.counted_weight, False,
                          ev.gate_status))

    return MasterySeries(
        user_id=user_id,
        skill_id=skill_id,
        lam=float(lam),
        p0=p0,
        steps=tuple(steps),
        skipped=tuple(skipped),
        ignored=ignored,
    )


# ---------------------------------------------------------------------------
# 从库里读历史（本模块唯一的 I/O）
# ---------------------------------------------------------------------------


def _user_reports(conn: sqlite3.Connection, user_id: str) -> list[tuple[str, str, dict]]:
    """`(submission_id, created_at, report)`，按 `created_at` 升序。

    SQL 与 JSON 解码都留在 `store.py`（那里是表结构的唯一所有者）；本模块只消费。
    只读 `submissions`，不碰 `user_skill_states`（P 还没有落字段，见模块文档末节）。
    """
    from . import store  # 局部导入：与 submission_api 同惯例，避免模块级循环依赖

    out: list[tuple[str, str, dict]] = []
    for row in store.user_reports(conn, user_id):
        out.append((row["submission_id"], row["created_at"], row["diagnosis"]))
    return out


def build_series(
    conn: sqlite3.Connection,
    user_id: str,
    skill_id: str,
    *,
    lam: float = LAMBDA_DEFAULT,
    p0: float | None = None,
) -> MasterySeries:
    """从库里读该用户全部提交 → 逐份取该技能的证据 → 折成 P 序列。

    时间轴用库里的 `created_at`（落库时已锚到 `evaluated_at`），不用报告里再读一次：
    两个来源一致时无所谓，不一致时以库为准——否则"P 的时间轴"和"复习间隔的时间轴"会分叉。
    """
    evidence = [
        skill_evidence(report, skill_id, at=created_at)
        for _, created_at, report in _user_reports(conn, user_id)
    ]
    return replay(evidence, user_id=user_id, skill_id=skill_id, lam=lam, p0=p0)


def decayed_at(
    conn: sqlite3.Connection,
    user_id: str,
    skill_id: str,
    *,
    as_of: str,
    lam: float = LAMBDA_DEFAULT,
    p0: float | None = None,
):
    """读出 P，并给出它在 `as_of` 时刻的**衰减后**读法：`(series, DecayResult)`。

    `as_of` 必填（B-08b 开工条件③）：默认墙钟会让同一份数据两次调用给出不同答案，
    复核时说不出"谁改的"。

    S 与 `last_review` 取自 `user_skill_states` 里那张卡（`decay` 只按值算，不碰库）。
    技能还没有卡时 `decay` 会返回 `applied=False, reason="no_card"` 并保留原值——
    "从没失败过所以没建卡"不等于"忘光了"。
    """
    from . import decay  # 局部导入：decay 依赖本模块的类型，模块级导入会成环
    from . import store

    series = build_series(conn, user_id, skill_id, lam=lam, p0=p0)
    row = store.load_fsrs_cards(conn, user_id).get(skill_id) or {}
    return series, decay.decay_series(
        series,
        as_of=as_of,
        stability=row.get("stability"),
        last_review=row.get("last_review"),
    )


def series_for_lambdas(
    evidence: Sequence[Evidence],
    *,
    user_id: str,
    skill_id: str,
    lambdas: Sequence[float] = (0.1, 0.2, 0.3, 0.5, 0.7),
    p0: float | None = None,
) -> dict[float, MasterySeries]:
    """同一批证据在不同 λ 下的终值。用来展示"参数没标定时结论有多不稳"。"""
    return {
        lam: replay(evidence, user_id=user_id, skill_id=skill_id, lam=lam, p0=p0)
        for lam in lambdas
    }
```
<!-- END_FILE src/arena/mastery.py -->

### 3.4 `tests/test_mastery_snapshot.py` — 本轮新增的 27 条测试

<!-- BEGIN_FILE tests/test_mastery_snapshot.py -->
```python
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


def test_criterion_migrate_from_real_v1_keeps_data_and_backfills(tmp_path):
    """迁移必须从一个"没有新表"的 v1 库起跑，不是拿新库改个 PRAGMA 假装。"""
    path = tmp_path / "v1.db"
    conn = _v1_db(path)
    report = _report("raw-perfect", submission_id="old-1", at=AT1)
    store.save_submission(conn, report, user_id="old-user", created_at=AT1,
                          refresh_mastery=False)
    before = store.count_submissions(conn)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "mastery_snapshots" not in names
    assert store.schema_version(conn) == 1

    result = store.migrate(conn)
    assert result == {"from": 1, "to": 2, "added_tables": ["mastery_snapshots"],
                      "backfilled": 3}
    assert store.schema_version(conn) == store.SCHEMA_VERSION == 2
    assert store.count_submissions(conn) == before
    assert store.load_report(conn, "old-1") == report          # 旧报告一个字节没动
    assert len(store.list_mastery_snapshots(conn)) == 3        # 回填了三个技能
    assert all(c.consistent for c in ms.verify_all(conn))
    conn.close()


def test_migrate_is_idempotent_and_never_downgrades(db):
    assert store.migrate(db) == {"from": 2, "to": 2, "added_tables": [], "backfilled": 0}
    assert store.schema_version(db) == 2
    with db:
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(store.StoreError, match="拒绝降级"):
        store.migrate(db)
    assert store.schema_version(db) == 99      # 抛错时没偷偷改库


def test_migrate_rejects_a_bogus_target(db):
    for bad in (0, -1, True, 1.5, "2"):
        with pytest.raises(store.StoreError, match="正整数"):
            store.migrate(db, target=bad)


def test_init_db_on_a_v1_file_does_not_leave_a_half_migrated_state(tmp_path):
    """老库被 `init_db()` 重新打开时，不能出现"表有了、PRAGMA 还是 1"。"""
    path = tmp_path / "reopen.db"
    conn = _v1_db(path)
    conn.close()
    again = store.connect(path)
    _setup(again)
    assert store.schema_version(again) == 2
    assert "mastery_snapshots" in {
        r[0] for r in again.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
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
    """防止有人把 v2 的表偷偷加回 `SCHEMA_SQL_V1`，让迁移测试变成假测试。"""
    assert "mastery_snapshots" not in store.SCHEMA_SQL_V1
    assert "mastery_snapshots" in store.SCHEMA_SQL_V2
    assert store.SCHEMA_SQL == store.SCHEMA_SQL_V1 + store.SCHEMA_SQL_V2
```
<!-- END_FILE tests/test_mastery_snapshot.py -->

### 3.5 `scripts/independent_audit.py` — 独立对拍器（裁判本身也要被审）

<!-- BEGIN_FILE scripts/independent_audit.py -->
```python
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
```
<!-- END_FILE scripts/independent_audit.py -->

## 4 可判定命题（逐条给判定 + 最小复现）

| # | 命题 | 什么算通过 |
|---|---|---|
| T1 | `verify_all()` 能发现「库里有行、重放却无证据」的孤儿行 | 说出它扫的用户×技能集合从哪来；举一个能绕过该集合的构造，或证明绕不过 |
| T2 | `refresh_one()` 在重放无证据时删旧行 | 找出一个「该删」与「不该删」的边界输入；判断是否误删合法数据 |
| T3 | `migrate()` 从真 v1 库升级是增量、幂等、不降级、旧报告不变 | 指出任一失败步骤后库是否仍可用；指出 `init_db` 与 `migrate` 的调用顺序有无半迁移窗口 |
| T4 | `evidence_count` / `last_submission_id` / `lam` 与重放序列末步一致 | 构造「P 值碰巧相同但参数漂移」的输入；看校核是否报不一致 |
| T5 | `list_ranked()` 只给有序行、不含名次字段、并列时顺序稳定 | 从代码指出稳定的依据；举一个会让它不稳定的输入 |
| T6 | 提交落库与快照刷新之间没有「报告落了、缓存没刷」的半状态 | 指出两者是否在同一个事务里；举一个中途失败的具体位置 |
| T7 | `verify_*` / `compute_row` 纯读，不写库 | 找出任何写副作用；找不到就给出你据以判定的代码位置 |
| T8 | 27 条测试里没有恒真断言 | 至少指出 1 条最可疑的测试名，给出「删掉哪个行为它仍会绿」的变异方式；或证明全部有效 |
| T9 | 文档自述与代码一致 | 在 §3 里找出一处与 §1 现状描述不符的代码，或确认无不符 |

**T8 是本次审核的重点**：作者自己写的测试可能只测了它实现过的分支。

## 5 交付格式（硬性）

```
核证总表：T1 … T9 各一行（判定 / 依据（文件:函数名或表达式）/ 事实 or 推测）
最小复现：每条一段，写清输入与期望；不能实跑的写「需维护者提供什么」
待拍板项：若有，写两个选项各自的代价，不替人拍板
```

不要写「整体质量不错」这类总体评价；不要用本包未提供的信息补全结论。

## 6 指纹表与自检

| 文件 | 行数 | sha256（前 16） |
|---|---|---|
| `src/arena/mastery_snapshot.py` | 298 | `ade8d6f3fa6ed917` |
| `src/arena/store.py` | 855 | `dd43c7c804eecc07` |
| `src/arena/mastery.py` | 488 | `78587c88e9951eb6` |
| `tests/test_mastery_snapshot.py` | 445 | `927a253320ab2c02` |
| `scripts/independent_audit.py` | 239 | `a1648f7d8c9438c1` |

自检命令（相对仓库根，**本包不写任何机器绝对路径**）：

```
python tools/gen_peer_brief_b07c.py --check
```

输出应含 `BLOCKS_MATCH=True` 与 `ANON=CLEAN`。

## 7 边界

- 本包**不外发**：是否发给外部 AI、发给谁、何时发，由维护者决定。
- 若维护者愿意另外开放仓库只读权限，路径清单为 §6 表中五个文件加
  `docs/data-model.md` / `docs/BACKLOG.md` / `docs/review-response-v1.md` §87；
  均为仓库相对路径。不给整个仓库目录、不给内部处置记录、不给已过期评审包。
- 回件按「核证 → 登记 → 处置 → 落仓 → 人拍板」处理：**外部 AI 的建议不当证据**，
  每条仍由本地实测复核。
