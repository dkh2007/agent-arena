"""费曼复述原文的落库（B-58）的判据锁。

这件事的动因值得写在最前面：**满分不等于会**。2026-11-26 第 1 份真实试用里，
学弟的提交拿了 1.00，但那份日志只说明"某个程序做对了"——分不清是他做的还是 AI 写的。
唯一能分辨的是让他用自己的话讲一遍，而报告页那一格以前写着"写完把这段文字发回给我"，
却没有任何地方能收：文字只活在浏览器里，关掉就没了。

所以这里锁的不是"存了个字符串"，而是四条边界，每条对应一种具体的坏结果：

1. **别人的提交不能替他写** —— 复述是"这个人怎么想"的证据，串一条就全废；
2. **原文不改写** —— 分段与标点是他表达的一部分，替他规整就是把他的话说成我的话；
3. **超长拒绝而不是截断** —— 截断会让他以为讲完了，而库里只有前半段；
4. **不判分、不改 P、不动复习队列** —— 复述没有第二真相源，任何分数都是编的。

第 4 条是本文件最重要的一条：如果哪天有人"顺手"让它推进掌握度，这个文件会红。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402

TASK_ID = "dbg-001-order-report"


@pytest.fixture()
def env(tmp_path):
    """真库 + 真任务注册表；提交用夹具跑一遍真管线，不是手搓的假报告。"""
    path = tmp_path / "recall.db"
    registry = api.TaskRegistry(ROOT / "tasks")
    api.bootstrap(path, registry)
    conn = store.connect(path)
    try:
        yield conn, path
    finally:
        conn.close()


def _save_submission(conn, name: str, *, submission_id: str, user_id: str,
                     at: str = "2026-11-26T10:00:00Z") -> str:
    report = copy.deepcopy(run_fixture(name))
    report["submission"]["submission_id"] = submission_id
    return store.save_submission(conn, report, user_id=user_id, created_at=at,
                                 refresh_mastery=False)


# ── 1. 收得下，而且原样 ──────────────────────────────────────────────────────


def test_the_words_come_back_exactly_as_typed(env):
    """中间的换行、缩进、标点一字不动——分段是他的表达，不是待清洗的数据。

    只允许去掉两侧空白：那是输入法/粘贴带进来的，不属于他的句子。
    """
    conn, _path = env
    _save_submission(conn, "raw-perfect", submission_id="sub-1", user_id="u-a")

    typed = "\n  第一页我只拉了 10 条，漏了第二页。\n  证据是返回体里 total=20。\n"
    saved = store.save_recall(conn, "sub-1", user_id="u-a", text=typed,
                              at="2026-11-26T10:05:00Z")

    assert saved["text"] == typed.strip()
    assert "\n  证据是" in saved["text"], "内部换行与缩进必须保留"
    assert saved["chars"] == len(typed.strip())
    assert store.load_recall(conn, "sub-1") == saved


def test_nobody_spoke_is_none_not_an_empty_string(env):
    """没讲过 ≠ 讲了空话。前者是 `None`，后者的入口根本不该存在（空文本被拒）。

    合成一个空串，会让"他没讲"和"他讲了但库里是空的"长得一样。
    """
    conn, _path = env
    _save_submission(conn, "raw-perfect", submission_id="sub-1", user_id="u-a")
    assert store.load_recall(conn, "sub-1") is None


def test_a_second_retell_replaces_the_first_instead_of_stacking(env):
    """改一遍就是改这条：`created_at` 不动、抬 `updated_at`，行数还是 1。

    新增行会让"这个人讲了几次"变成一个可疑的计数，而页面只该显示他最后那版。
    """
    conn, _path = env
    _save_submission(conn, "raw-perfect", submission_id="sub-1", user_id="u-a")
    first = store.save_recall(conn, "sub-1", user_id="u-a", text="第一版",
                              at="2026-11-26T10:05:00Z")
    second = store.save_recall(conn, "sub-1", user_id="u-a", text="第二版，说清楚了",
                               at="2026-11-26T10:09:00Z")

    assert second["created_at"] == first["created_at"] == "2026-11-26T10:05:00Z"
    assert second["updated_at"] == "2026-11-26T10:09:00Z"
    assert second["text"] == "第二版，说清楚了"
    rows = conn.execute("SELECT COUNT(*) FROM recalls").fetchone()[0]
    assert rows == 1


# ── 2. 四条边界 ─────────────────────────────────────────────────────────────


def test_someone_elses_submission_is_refused(env):
    """拿别人的 submission_id 写复述必须失败，而且库里一行都不多。

    这条路以前是通的（`/submissions/{id}` 凭 id 就能看），复述不能重犯：
    一旦串了，库里存的就不是"这个人怎么想"。
    """
    conn, _path = env
    _save_submission(conn, "raw-perfect", submission_id="sub-1", user_id="u-a")

    with pytest.raises(store.StoreError, match="不属于"):
        store.save_recall(conn, "sub-1", user_id="u-b", text="我来替他讲讲")
    assert store.load_recall(conn, "sub-1") is None


def test_unknown_submission_is_refused(env):
    conn, _path = env
    with pytest.raises(store.StoreError, match="没有这份提交"):
        store.save_recall(conn, "no-such-id", user_id="u-a", text="随便讲讲")


def test_blank_text_is_refused(env):
    """空白不算讲过：存一条空记录，报告页会显示"他讲过了"，点开却是空的。"""
    conn, _path = env
    _save_submission(conn, "raw-perfect", submission_id="sub-1", user_id="u-a")
    for bad in ("", "   ", "\n\t\n"):
        with pytest.raises(store.StoreError, match="空的"):
            store.save_recall(conn, "sub-1", user_id="u-a", text=bad)
    assert store.load_recall(conn, "sub-1") is None


def test_too_long_is_refused_with_the_actual_count(env):
    """超长直接拒绝，不截断——并且报出**他实际写了多少字**，否则他不知道要删多少。"""
    conn, _path = env
    _save_submission(conn, "raw-perfect", submission_id="sub-1", user_id="u-a")
    long_text = "啊" * (store.RECALL_MAX_CHARS + 1)

    with pytest.raises(store.StoreError) as exc:
        store.save_recall(conn, "sub-1", user_id="u-a", text=long_text)
    assert str(store.RECALL_MAX_CHARS + 1) in str(exc.value)
    assert store.load_recall(conn, "sub-1") is None

    ok = "啊" * store.RECALL_MAX_CHARS
    assert store.save_recall(conn, "sub-1", user_id="u-a", text=ok)["chars"] == store.RECALL_MAX_CHARS


# ── 3. 最要紧的一条：它不产生任何"成绩" ─────────────────────────────────────


def test_a_retell_changes_no_score_no_mastery_and_no_review_item(env):
    """复述不改 P、不写快照、不排复习项——它只是"他说的话"，不是证据。

    这条是本文件的核心：一旦有人给复述打分并喂掌握度，就会出现
    "页面说他掌握了、成绩单上没有"的两套账（同样的理由已经在 `mark_review_done` 里用过）。
    复述的价值是给人看（自己对照、助教判断），而不是喂模型。
    """
    conn, _path = env
    _save_submission(conn, "raw-empty", submission_id="sub-1", user_id="u-a")

    def snapshot() -> tuple:
        return (
            conn.execute("SELECT COUNT(*) FROM user_skill_states").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM mastery_snapshots").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM review_items").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0],
        )

    before = snapshot()
    store.save_recall(conn, "sub-1", user_id="u-a", text="我没重试那次 500，直接跳过了")
    assert snapshot() == before, "复述不该改动任何一张成绩/状态表"

    # 报告本身也没被改写：复述是另一张表，不是往 diagnosis 里塞一段。
    report = store.effective_report(conn, "sub-1")
    assert "recall" not in report and "复述" not in str(report.get("score"))


# ── 4. 助教侧按人查 ─────────────────────────────────────────────────────────


def test_list_recalls_is_newest_first_and_only_this_person(env):
    """助教问"这个人讲过什么"：只给他自己的，按最近讲的排在前面。"""
    conn, _path = env
    _save_submission(conn, "raw-empty", submission_id="s-1", user_id="u-a", at="2026-11-26T09:00:00Z")
    _save_submission(conn, "raw-perfect", submission_id="s-2", user_id="u-a", at="2026-11-26T11:00:00Z")
    _save_submission(conn, "raw-perfect", submission_id="s-3", user_id="u-b", at="2026-11-26T12:00:00Z")

    store.save_recall(conn, "s-1", user_id="u-a", text="第一次讲", at="2026-11-26T09:10:00Z")
    store.save_recall(conn, "s-2", user_id="u-a", text="第二次讲", at="2026-11-26T11:10:00Z")
    store.save_recall(conn, "s-3", user_id="u-b", text="别人的", at="2026-11-26T12:10:00Z")

    mine = store.list_recalls(conn, "u-a")
    assert [row["submission_id"] for row in mine] == ["s-2", "s-1"]
    assert all(row["user_id"] == "u-a" for row in mine)


# ── 5. 助教侧那一栏：满分却没讲的，要单独点出来 ──────────────────────────────


def test_the_recall_view_points_at_the_full_marks_nobody_explained(env, capsys):
    """这个视图的价值全在最后那句汇总：**满分却没讲过的那几份，才是要先问的**。

    只列"讲过的"会让人以为大家都讲了；把"满分 + 没讲"混在一堆"没讲"里，
    助教就得自己逐行比分数——那这张表就白做了。
    """
    from arena import hosting

    conn, path = env
    _save_submission(conn, "raw-perfect", submission_id="s-1", user_id="u-a",
                     at="2026-11-24T09:00:00Z")
    _save_submission(conn, "raw-empty", submission_id="s-2", user_id="u-a",
                     at="2026-11-25T09:00:00Z")
    store.save_recall(conn, "s-1", user_id="u-a",
                      text="第二页 500，我拿同一个 offset 又试了一次", at="2026-11-24T09:20:00Z")

    assert hosting.main(["recall", "--db", str(path), "--user", "u-a"]) == 0
    out = capsys.readouterr().out

    assert "第二页 500" in out, "讲过的要能看到原文"
    assert "满分，但还不知道他会不会" not in out, "s-1 讲过了，不该被点名"
    assert "共 2 份提交，1 份讲过" in out


def test_the_recall_view_flags_a_perfect_submission_with_no_retell(env, capsys):
    """满分的两份、只讲了一份：汇总里必须精确点出 1 份没讲，而不是含糊的"部分未讲"。"""
    from arena import hosting

    conn, path = env
    _save_submission(conn, "raw-perfect", submission_id="s-1", user_id="u-a",
                     at="2026-11-24T09:00:00Z")
    _save_submission(conn, "raw-perfect", submission_id="s-2", user_id="u-a",
                     at="2026-11-25T09:00:00Z")
    store.save_recall(conn, "s-1", user_id="u-a", text="讲过了", at="2026-11-24T09:20:00Z")

    assert hosting.main(["recall", "--db", str(path), "--user", "u-a"]) == 0
    out = capsys.readouterr().out
    assert out.count("满分，但还不知道他会不会") == 1
    assert "其中 1 份是满分却没讲——先问这几份。" in out


def test_the_recall_view_says_nothing_happened_yet(env, capsys):
    """一份提交都没有时讲一句实话，而不是印一张空表。"""
    from arena import hosting

    _conn, path = env
    assert hosting.main(["recall", "--db", str(path), "--user", "nobody"]) == 0
    assert "还没有任何提交" in capsys.readouterr().out
