"""同一个人在同一道题上再做一次，和上一次比（B-56）。

## 它补的是哪一格

到现在为止，一个人交两次卷，系统里只有两行互不相干的记录：

- 学生页（`hosting.render_landing`）会说"你交过了"，却答不了"这次比上次好在哪"；
- 助教侧（`hosting.summary`）把所有人的提交按**要求**聚成一列，答的是"这题怎么样"，
  不是"这个人第二次有没有改掉第一次那个错"。

对学习者来说，第二句才是他重做的理由。所以这里只做一件事：**两份报告 → 一张迁移表**。

## 四条口径，每条都在防一种具体的自欺

1. **不可比就直说不可比**。两次之间题目文件改过（`task_git_hash` 不同），或者检查项
   对不上（断言 id 集合不同），逐条迁移就不成立——R2 可能还是那个编号、但期望金额变了。
   这时**只报"不能比"，不报分差**：给一个 `+0.36` 反而更误导。
2. **`blocked` / `unscored` 不算"改好了"，也不算"退步"**。它们的意思是"这次没判成"
   （没交过程、没交产物）或"这条不适用"。`fail → pass` 才叫改好；`blocked → pass`
   是"上次根本没比"。
3. **同一份提交不能自己跟自己比**。重复交卷返回的是同一份报告，比出来满屏"无变化"，
   看着像"他很稳定"。这由调用方按 `submission_id` 去重，本模块另外加一道断言兜住。
4. **只报事实，不判"进步"**。分数变了是事实；"你进步了"是评价，评价要人来下，
   而且样本只有两次。所以输出里没有 `improved` 这种布尔，只有哪几条改了、哪几条没改。

## 谁调用

- `pair()`：从库里取这个人这道题**最近两次**（`created_at` 升序里最后两行）。
- 学生页把 `render_block()` 的结果贴进报告入口下面。
- 助教侧走 `host.py progress --user --task`。
"""

from __future__ import annotations

import sqlite3

from . import store

#: 状态对 → 迁移类别。**只有明确的一对一映射**，别的组合一律进 `unjudged`。
_FIXED = ("fail", "pass")
_BROKE = ("pass", "fail")
_STILL = ("fail", "fail")

#: 这两类状态表示"这条这次没有结论"，涉及它们的任何变化都不褒不贬。
_NO_VERDICT = ("blocked", "unscored")


def _assertions(report: dict) -> dict[str, dict]:
    """断言 id → 断言本体。id 缺失的条目直接跳过：没有 id 就没法对齐两次。"""
    out: dict[str, dict] = {}
    for item in report.get("assertions") or []:
        key = str(item.get("id") or "")
        if key:
            out[key] = item
    return out


def _patterns(report: dict) -> set[str]:
    """这份报告里"因失败而被记下的错法"。

    口径与 `hosting.summary` 一致：**只收 `fail` 项的 `error_patterns`**。
    把 `blocked` 项上的错法也算进来，会让"上次没判成"变成"上次犯了这个错"。
    """
    out: set[str] = set()
    for item in report.get("assertions") or []:
        if str(item.get("state") or "") != "fail":
            continue
        for pattern in item.get("error_patterns") or []:
            out.add(str(pattern))
    return out


def _ratio(report: dict) -> float:
    return float((report.get("score") or {}).get("ratio") or 0.0)


def _why_incomparable(prev: dict, cur: dict, prev_meta: dict, cur_meta: dict) -> str:
    """不可比的原因；可比时返回空串。

    两条判据都必须过：题目版本一致（两边都有值时才比）、检查项集合一致。
    `task_git_hash` 可能是 NULL（v9 之前的历史行），缺值不当成"不一样"——
    那时它没有记录，不能反推题目没动过，也不能反推动过。这种情况只看检查项。
    """
    prev_hash = str(prev_meta.get("task_git_hash") or "")
    cur_hash = str(cur_meta.get("task_git_hash") or "")
    if prev_hash and cur_hash and prev_hash != cur_hash:
        return "这两次之间题目改过，检查项的含义可能变了，不能直接比。"
    if set(_assertions(prev)) != set(_assertions(cur)):
        return "这两次的检查项对不上（判分规则或题目改过），不能逐条比。"
    return ""


def compare(prev: dict, cur: dict, *, prev_meta: dict | None = None,
            cur_meta: dict | None = None, labels: dict[str, str] | None = None) -> dict:
    """两份生效报告 → 迁移表。`prev` 是较早那份。

    返回的键固定（页面按这些键排版，不猜）：

    | 键 | 含义 |
    |---|---|
    | `comparable` / `reason` | 能不能逐条比；不能时 `reason` 是一句人话 |
    | `score` | `{before, after, delta}`，不可比时只有前两项、没有 `delta` |
    | `fixed` / `still_failing` / `broke` | `fail→pass` / `fail→fail` / `pass→fail` |
    | `unjudged` | 涉及 `blocked`/`unscored` 的变化，带前后状态 |
    | `patterns` | `{cleared, persisted, new}`，元素是中文名 |
    | `counts` | 两次都过的条数、两次都没过的条数等，给"稳"这个事实一个数 |
    | `headline` | 一句能直接印在页面上的话，**不含 id、不含字段名** |

    `labels` 不给就现场读词表：`pattern_id` 印在页面上是机器码，这条在
    `docs/android-ui-handoff.md` §4.6 已经定死了。
    """
    if prev_meta and cur_meta and prev_meta.get("submission_id") == cur_meta.get("submission_id"):
        raise ValueError("同一份提交不能自己跟自己比：调用方应跳过重复提交")
    prev_meta = prev_meta or {}
    cur_meta = cur_meta or {}
    if labels is None:
        from .diagnosis_report import load_pattern_labels

        labels = load_pattern_labels()

    reason = _why_incomparable(prev, cur, prev_meta, cur_meta)
    before, after = _ratio(prev), _ratio(cur)
    score: dict = {"before": round(before, 3), "after": round(after, 3)}
    if not reason:
        score["delta"] = round(after - before, 3)

    fixed: list[dict] = []
    still: list[dict] = []
    broke: list[dict] = []
    unjudged: list[dict] = []
    counts = {"passed_both": 0, "unresolved_both": 0}

    if not reason:
        prev_items, cur_items = _assertions(prev), _assertions(cur)
        for key in sorted(prev_items):
            was = str(prev_items[key].get("state") or "")
            now = str(cur_items[key].get("state") or "")
            title = str(cur_items[key].get("title") or prev_items[key].get("title") or "这项检查")
            row = {"id": key, "title": title}
            if (was, now) == _FIXED:
                fixed.append(row)
            elif (was, now) == _BROKE:
                broke.append(row)
            elif (was, now) == _STILL:
                still.append(row)
            elif (was, now) == ("pass", "pass"):
                counts["passed_both"] += 1
            elif was in _NO_VERDICT and now in _NO_VERDICT:
                # 两次都没结论：不是"稳"，但也没变化，单独计一个数就够了。
                counts["unresolved_both"] += 1
            else:
                # 剩下的是"至少一侧没有结论、且两侧不同"：上次没判成这次过了、
                # 这次没交过程所以没判成……都归到这里，带前后状态，不做褒贬。
                unjudged.append({**row, "was": was, "now": now})

    prev_patterns, cur_patterns = _patterns(prev), _patterns(cur)
    patterns = {
        "cleared": [labels.get(p, p) for p in sorted(prev_patterns - cur_patterns)],
        "persisted": [labels.get(p, p) for p in sorted(prev_patterns & cur_patterns)],
        "new": [labels.get(p, p) for p in sorted(cur_patterns - prev_patterns)],
    }

    result = {
        "comparable": not reason,
        "reason": reason,
        "score": score,
        "fixed": fixed,
        "still_failing": still,
        "broke": broke,
        "unjudged": unjudged,
        "patterns": patterns,
        "counts": counts,
    }
    result["headline"] = _headline(result)
    return result


def _headline(data: dict) -> str:
    """一句人话。**不可比时先说不可比**，因为后面所有数字都失去参照。"""
    if not data["comparable"]:
        return data["reason"]
    score = data["score"]
    delta = score.get("delta") or 0.0
    fixed, still, broke = len(data["fixed"]), len(data["still_failing"]), len(data["broke"])
    unjudged = len(data["unjudged"])

    if fixed == 0 and broke == 0 and still == 0:
        head = "检查项和上次一样，没有可对比的变化。"
    elif delta > 0:
        head = f"比上次高 {delta:.2f}"
        parts = []
        if fixed:
            parts.append(f"上次没过的 {fixed + still} 条里过了 {fixed} 条")
        if still:
            parts.append(f"还剩 {still} 条没过")
        head += "：" + "，".join(parts) if parts else "。"
    elif delta < 0:
        head = f"比上次低 {abs(delta):.2f}"
        head += f"：上次过的 {broke} 条这次没过。" if broke else "。"
    else:
        head = "分数和上次一样"
        head += f"：还是那 {still} 条没过。" if still else "。"

    if not head.endswith("。"):
        head += "。"
    if unjudged:
        head += f"另有 {unjudged} 条这次没有判成，不计入比较。"
    return head


def _bundle(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """一行提交 → 元信息 + **当前生效**报告。重评过的行按新结论比，否则页面自相矛盾。"""
    return {
        "submission_id": row["submission_id"],
        "task_git_hash": row["task_git_hash"],
        "created_at": row["created_at"],
        "report": store.effective_report(conn, row["submission_id"]),
    }


def _recent(conn: sqlite3.Connection, user_id: str, task_id: str, limit: int) -> list[sqlite3.Row]:
    """按时间倒序取最近几行。**同一时刻用 `rowid` 兜底**。

    为什么不能拿 `submission_id` 当第二排序键：它是 UUID，字典序和提交先后无关。
    两次提交落在同一秒（学生连续交、或测试里连跑两轮）时，按 UUID 排会让"上一次"
    变成"下一次"——算出来的分差符号是反的，而页面看起来完全正常。
    `rowid` 是 SQLite 的插入序，只在这一秒内当兜底，不越过 `created_at`
    （补录历史数据时显式传入的 `created_at` 仍然是权威）。
    """
    return conn.execute(
        "SELECT rowid AS rid, submission_id, task_git_hash, created_at FROM submissions "
        "WHERE user_id = ? AND task_id = ? "
        "ORDER BY created_at DESC, rid DESC LIMIT ?",
        (user_id, task_id, limit),
    ).fetchall()


def pair(conn: sqlite3.Connection, user_id: str, task_id: str) -> tuple[dict, dict] | None:
    """这个人在这道题上**最近两次**提交（较早、较晚）。不足两次返回 `None`。

    **只取两行，不取全部**：页面上要的是"这次和上次"，把五次摊开会变成一张没人看的表。
    整条时间线留给 `store.user_reports`（它是给状态层重放用的，正序、不限条数）。
    """
    rows = _recent(conn, user_id, task_id, 2)
    if len(rows) < 2 or rows[0]["submission_id"] == rows[1]["submission_id"]:
        return None
    cur_row, prev_row = rows[0], rows[1]
    return _bundle(conn, prev_row), _bundle(conn, cur_row)


def previous_of(conn: sqlite3.Connection, submission_id: str) -> tuple[dict, dict] | None:
    """**指定这一份**提交与它的上一次。不够两次（或找不到）返回 `None`。

    与 `pair()` 的分工：`pair` 是"他最近一次比上一次"（命令行用）；
    这个是"**这一份**比它之前那份"（学生页用）。

    为什么不能拿 `pair` 顶替：一个人交了四次，回看第二次那一页时，`pair` 给的是
    第三次和第四次——页面上会出现"比上次高 0.2"，而这条链接对应的根本是另一份卷子。
    """
    cur = conn.execute(
        "SELECT rowid AS rid, submission_id, user_id, task_id, task_git_hash, created_at "
        "FROM submissions WHERE submission_id = ?",
        (submission_id,),
    ).fetchone()
    if cur is None:
        return None
    prev = conn.execute(
        "SELECT rowid AS rid, submission_id, task_git_hash, created_at FROM submissions "
        "WHERE user_id = ? AND task_id = ? "
        "  AND (created_at < ? OR (created_at = ? AND rowid < ?)) "
        "ORDER BY created_at DESC, rid DESC LIMIT 1",
        (cur["user_id"], cur["task_id"], cur["created_at"], cur["created_at"], cur["rid"]),
    ).fetchone()
    if prev is None:
        return None
    return _bundle(conn, prev), _bundle(conn, cur)


def _compare_bundles(prev: dict, cur: dict, labels: dict[str, str] | None) -> dict:
    result = compare(prev["report"], cur["report"], prev_meta=prev, cur_meta=cur, labels=labels)
    result["prev_at"] = prev["created_at"]
    result["cur_at"] = cur["created_at"]
    return result


def of(conn: sqlite3.Connection, user_id: str, task_id: str, *,
       labels: dict[str, str] | None = None) -> dict | None:
    """`pair()` + `compare()`，给命令行用（"他最近一次比上次怎样"）。"""
    found = pair(conn, user_id, task_id)
    return None if found is None else _compare_bundles(found[0], found[1], labels)


def of_submission(conn: sqlite3.Connection, submission_id: str, *,
                  labels: dict[str, str] | None = None) -> dict | None:
    """`previous_of()` + `compare()`，给学生页用（"你这一份比上一份怎样"）。"""
    found = previous_of(conn, submission_id)
    return None if found is None else _compare_bundles(found[0], found[1], labels)
