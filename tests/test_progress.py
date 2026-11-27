"""同一道题第二次作答 vs 上一次（B-56）的判据锁。

锁的是 `progress.py` 文档里那四条口径，每条对应一种具体的自欺：

1. 题目改过 / 检查项对不上 → **只说不比，不给分差**；
2. `blocked` / `unscored` 不算改好、也不算退步 —— 它们是"这次没判成"；
3. 同一份提交不能自己跟自己比（重复交卷去重后的漏网之鱼要炸出来）；
4. 只报事实，不判"进步"；页面上不许出现机器码。

最后两条是真报告跑的端到端：两个夹具各跑一遍，确认这套口径在真数据上不会
把"这次没交过程"算成"上次没做到"。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import hosting  # noqa: E402
from arena import progress  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402

TASK_ID = "dbg-001-order-report"
LABELS = {"p_fabricate": "编造数据", "p_no_retry": "失败后没有重试", "p_skip_check": "跳过核对"}
GID = "abc1234"


def _report(states: dict[str, str], *, ratio: float,
            patterns: dict[str, list[str]] | None = None) -> dict:
    """最小报告：只带 `compare` 会读的两个键（`score.ratio` 与 `assertions`）。"""
    patterns = patterns or {}
    return {
        "score": {"ratio": ratio},
        "assertions": [
            {"id": key, "title": f"第 {index} 条要求", "state": state,
             "error_patterns": patterns.get(key, [])}
            for index, (key, state) in enumerate(states.items(), 1)
        ],
    }


def _meta(submission_id: str, *, gid: str | None = GID, at: str = "2026-11-01T00:00:00Z") -> dict:
    return {"submission_id": submission_id, "task_git_hash": gid, "created_at": at}


# ── 1. 逐条迁移的分类 ────────────────────────────────────────────────────────


def test_fail_to_pass_is_the_only_thing_counted_as_fixed():
    """`fail→pass` 才叫改好；`blocked→pass` 是"上次根本没比"。

    混在一起的话，一个上次没交过程的人会被自己的重做报告夸一遍。
    """
    prev = _report({"R1": "fail", "R2": "blocked", "R3": "pass"}, ratio=0.4)
    cur = _report({"R1": "pass", "R2": "pass", "R3": "pass"}, ratio=0.9)

    out = progress.compare(prev, cur, prev_meta=_meta("s1"), cur_meta=_meta("s2"), labels=LABELS)

    assert [row["id"] for row in out["fixed"]] == ["R1"]
    assert [row["id"] for row in out["unjudged"]] == ["R2"]
    assert out["unjudged"][0]["was"] == "blocked" and out["unjudged"][0]["now"] == "pass"
    assert out["score"] == {"before": 0.4, "after": 0.9, "delta": 0.5}


def test_pass_to_fail_is_reported_as_a_fact_not_a_verdict():
    """这次没过就是这次没过，不写成"退步"——两次样本判不了趋势。"""
    prev = _report({"R1": "pass", "R2": "pass"}, ratio=1.0)
    cur = _report({"R1": "fail", "R2": "pass"}, ratio=0.5)

    out = progress.compare(prev, cur, prev_meta=_meta("s1"), cur_meta=_meta("s2"), labels=LABELS)

    assert [row["id"] for row in out["broke"]] == ["R1"]
    assert out["counts"]["passed_both"] == 1
    assert "退步" not in out["headline"]
    assert "低 0.50" in out["headline"]


def test_blocked_costs_nothing_in_either_direction():
    """`fail→blocked` 既不算改好也不算没改：它不在 `still_failing` 里。

    这条防的是"这次忘交过程"被记成"上次那个错还在"——两者对学习者的建议完全相反。
    """
    prev = _report({"R1": "fail"}, ratio=0.0)
    cur = _report({"R1": "blocked"}, ratio=0.0)

    out = progress.compare(prev, cur, prev_meta=_meta("s1"), cur_meta=_meta("s2"), labels=LABELS)

    assert out["still_failing"] == [] and out["broke"] == [] and out["fixed"] == []
    assert out["unjudged"] == [{"id": "R1", "title": "第 1 条要求", "was": "fail", "now": "blocked"}]
    assert "没有判成" in out["headline"]


def test_both_times_blocked_is_counted_but_not_listed():
    """两次都没判成：计一个数就够，逐条列出来只会在页面上堆一串"没法判定"。"""
    prev = _report({"R1": "blocked", "R2": "fail"}, ratio=0.5)
    cur = _report({"R1": "unscored", "R2": "fail"}, ratio=0.5)

    out = progress.compare(prev, cur, prev_meta=_meta("s1"), cur_meta=_meta("s2"), labels=LABELS)

    assert out["counts"]["unresolved_both"] == 1
    assert out["unjudged"] == []
    assert [row["id"] for row in out["still_failing"]] == ["R2"]


# ── 2. 错法：走了、还在、新来的 ──────────────────────────────────────────────


def test_patterns_are_split_into_cleared_persisted_new():
    """错法用中文名，且三种都分得开：上次犯的这次没犯、两次都犯、这次新犯的。

    只测"都还在"不够——那可能是把三个集合都写成了同一个表达式。
    """
    prev = _report({"R1": "fail", "R2": "fail", "R3": "pass"}, ratio=0.3,
                   patterns={"R1": ["p_fabricate"], "R2": ["p_no_retry"]})
    cur = _report({"R1": "pass", "R2": "fail", "R3": "fail"}, ratio=0.4,
                  patterns={"R2": ["p_no_retry"], "R3": ["p_skip_check"]})

    out = progress.compare(prev, cur, prev_meta=_meta("s1"), cur_meta=_meta("s2"), labels=LABELS)

    assert out["patterns"]["cleared"] == ["编造数据"]
    assert out["patterns"]["persisted"] == ["失败后没有重试"]
    assert out["patterns"]["new"] == ["跳过核对"]


def test_a_blocked_assertion_cannot_contribute_a_pattern():
    prev = _report({"R1": "pass"}, ratio=0.5)
    cur = _report({"R1": "blocked"}, ratio=0.5, patterns={"R1": ["p_fabricate"]})

    out = progress.compare(prev, cur, prev_meta=_meta("s1"), cur_meta=_meta("s2"), labels=LABELS)

    assert out["patterns"]["new"] == [], "没判成的检查项不该产出错法"


# ── 3. 不可比：只说不比，不给分差 ────────────────────────────────────────────


def test_different_task_version_makes_them_incomparable():
    """题目文件改过：编号可能还是 R2，期望金额已经变了。这时候给 `+0.36` 是骗人。"""
    prev = _report({"R1": "fail"}, ratio=0.2)
    cur = _report({"R1": "pass"}, ratio=0.95)

    out = progress.compare(prev, cur, prev_meta=_meta("s1", gid="aaa1111"),
                           cur_meta=_meta("s2", gid="bbb2222"), labels=LABELS)

    assert out["comparable"] is False
    assert "题目改过" in out["reason"]
    assert "delta" not in out["score"]
    assert out["fixed"] == [] and out["still_failing"] == []
    assert out["headline"] == out["reason"]


def test_missing_git_hash_does_not_block_comparison():
    """v9 之前的历史行没有版本号。缺值不能反推"题目动过"，也不能反推"没动过"——
    这种情况只看检查项集合，比"直接拒绝"更有用，也不会骗人。"""
    prev = _report({"R1": "fail"}, ratio=0.2)
    cur = _report({"R1": "pass"}, ratio=0.9)

    out = progress.compare(prev, cur, prev_meta=_meta("s1", gid=None),
                           cur_meta=_meta("s2", gid=None), labels=LABELS)

    assert out["comparable"] is True
    assert out["score"]["delta"] == 0.7


def test_different_assertion_sets_make_them_incomparable():
    prev = _report({"R1": "fail"}, ratio=0.2)
    cur = _report({"R1": "pass", "R2": "pass"}, ratio=0.9)

    out = progress.compare(prev, cur, prev_meta=_meta("s1"), cur_meta=_meta("s2"), labels=LABELS)

    assert out["comparable"] is False
    assert "检查项对不上" in out["reason"]


def test_same_submission_cannot_compare_itself():
    """重复交卷返回同一份报告。比出来满屏"无变化"，看着像"他很稳定"。"""
    report = _report({"R1": "pass"}, ratio=1.0)
    with pytest.raises(ValueError):
        progress.compare(report, report, prev_meta=_meta("s1"), cur_meta=_meta("s1"))


# ── 4. 给页面看的那一句话里没有机器码 ────────────────────────────────────────


def test_headline_contains_no_machine_codes():
    prev = _report({"R1_report_correct": "fail", "R2": "blocked"}, ratio=0.3)
    cur = _report({"R1_report_correct": "pass", "R2": "pass"}, ratio=0.8)

    out = progress.compare(prev, cur, prev_meta=_meta("s1"), cur_meta=_meta("s2"), labels=LABELS)

    for token in ("R1_", "R2", "fail", "pass", "blocked", "unscored", "_"):
        assert token not in out["headline"], f"页面上不该出现 {token!r}：{out['headline']}"


def test_rows_carry_the_chinese_title_not_just_the_id():
    prev = _report({"R1": "fail"}, ratio=0.0)
    cur = _report({"R1": "pass"}, ratio=1.0)

    out = progress.compare(prev, cur, prev_meta=_meta("s1"), cur_meta=_meta("s2"), labels=LABELS)

    assert out["fixed"][0]["title"] == "第 1 条要求"


# ── 5. 真报告：两个夹具各跑一遍 ──────────────────────────────────────────────


def test_real_reports_from_two_fixtures_compare_cleanly():
    """`raw-empty`（什么都没做）→ `raw-perfect`：分差为正、至少一条进入"改好了"。

    这两个夹具的题目版本都是当前 HEAD 的同一个值，所以这里顺带验证了"可比"的分支
    在真报告上成立（真报告里 `assertions` 的 id 是 `R1_...` 这种长名字）。
    """
    prev = run_fixture("raw-empty")
    cur = run_fixture("raw-perfect")
    gid = prev["submission"].get("task_git_hash")

    out = progress.compare(prev, cur, prev_meta=_meta("s1", gid=gid),
                           cur_meta=_meta("s2", gid=gid), labels=LABELS)

    assert out["comparable"] is True
    assert out["score"]["delta"] > 0
    assert out["score"]["after"] == 1.0
    assert out["fixed"], "什么都没做 → 全对，至少有一条从没过变成过了"
    for row in out["fixed"]:
        assert row["title"] and not row["title"].startswith("R"), "标题必须是中文，不是断言 id"


# ── 6. 取数：最近两次 ────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "progress.db"
    registry = api.TaskRegistry(ROOT / "tasks")
    api.bootstrap(path, registry)
    conn = store.connect(path)
    try:
        yield conn, registry
    finally:
        conn.close()


def _save(conn, report: dict, *, user_id: str, at: str) -> str:
    return store.save_submission(conn, report, user_id=user_id, created_at=at, refresh_mastery=False)


def _fixture_report(name: str, submission_id: str) -> dict:
    import copy

    report = copy.deepcopy(run_fixture(name))
    report["submission"]["submission_id"] = submission_id
    return report


def test_pair_takes_the_last_two_submissions(db):
    """三次提交只比后两次：页面上要的是"这次和上次"，五次摊开没人看。"""
    conn, _registry = db
    for index, name in enumerate(["raw-empty", "raw-fake-retry", "raw-perfect"], 1):
        _save(conn, _fixture_report(name, f"sub-{index}"), user_id="u-a",
              at=f"2026-11-0{index}T00:00:00Z")

    found = progress.pair(conn, "u-a", TASK_ID)

    assert found is not None
    prev, cur = found
    assert prev["submission_id"] == "sub-2" and cur["submission_id"] == "sub-3"


def test_pair_needs_two_submissions(db):
    conn, _registry = db
    _save(conn, _fixture_report("raw-empty", "sub-1"), user_id="u-a", at="2026-11-01T00:00:00Z")

    assert progress.pair(conn, "u-a", TASK_ID) is None
    assert progress.of(conn, "u-a", TASK_ID) is None


def test_pair_does_not_mix_two_people(db):
    """同题不同人不能配成一对：A 的第一次不能被拿去当 B 的"上次"。"""
    conn, _registry = db
    _save(conn, _fixture_report("raw-empty", "sub-a"), user_id="u-a", at="2026-11-01T00:00:00Z")
    _save(conn, _fixture_report("raw-perfect", "sub-b"), user_id="u-b", at="2026-11-02T00:00:00Z")

    assert progress.pair(conn, "u-b", TASK_ID) is None


def test_of_returns_both_timestamps_for_the_page(db):
    conn, _registry = db
    _save(conn, _fixture_report("raw-empty", "sub-1"), user_id="u-a", at="2026-11-01T00:00:00Z")
    _save(conn, _fixture_report("raw-perfect", "sub-2"), user_id="u-a", at="2026-11-03T00:00:00Z")

    out = progress.of(conn, "u-a", TASK_ID, labels=LABELS)

    assert out["prev_at"] == "2026-11-01T00:00:00Z" and out["cur_at"] == "2026-11-03T00:00:00Z"
    assert out["comparable"] is True and out["score"]["delta"] > 0


def test_previous_of_anchors_on_the_given_submission_not_the_latest(db):
    """回看第二次那一页，比的必须是**第一次**，不是"最近两次"。

    拿 `pair` 顶替会算出第三次 vs 第四次——页面上的分数变化和这条链接对不上，
    而且看起来完全正常（都是同一道题），这类错最难被发现。
    """
    conn, _registry = db
    names = ["raw-empty", "raw-fake-retry", "raw-no-file", "raw-perfect"]
    for index, name in enumerate(names, 1):
        _save(conn, _fixture_report(name, f"sub-{index}"), user_id="u-a",
              at=f"2026-11-0{index}T00:00:00Z")

    out = progress.of_submission(conn, "sub-2", labels=LABELS)

    assert out is not None
    assert out["prev_at"] == "2026-11-01T00:00:00Z" and out["cur_at"] == "2026-11-02T00:00:00Z"
    assert out["score"]["before"] == 0.0, "第二次的上一份是 raw-empty，不是 raw-no-file"


def test_previous_of_the_first_submission_is_none(db):
    conn, _registry = db
    _save(conn, _fixture_report("raw-empty", "sub-1"), user_id="u-a", at="2026-11-01T00:00:00Z")
    _save(conn, _fixture_report("raw-perfect", "sub-2"), user_id="u-a", at="2026-11-02T00:00:00Z")

    assert progress.of_submission(conn, "sub-1", labels=LABELS) is None


def test_previous_of_unknown_submission_is_none(db):
    conn, _registry = db
    assert progress.previous_of(conn, "no-such-id") is None


def test_same_second_submissions_still_order_by_insertion(db):
    """两次提交落在同一秒时，顺序只能靠插入先后 —— 不能靠 UUID 的字典序。

    id 特意取成"字典序与插入序相反"：先插 `zzz`、后插 `aaa`。按 id 排序，
    第二次会被当成第一次，分差符号直接反了，而页面看起来完全正常。
    """
    conn, _registry = db
    at = "2026-11-04T00:00:00Z"
    _save(conn, _fixture_report("raw-empty", "zzz"), user_id="u-a", at=at)
    _save(conn, _fixture_report("raw-perfect", "aaa"), user_id="u-a", at=at)

    later = progress.of_submission(conn, "aaa", labels=LABELS)

    assert later is not None, "后插入的那份必须能找到前一份"
    assert later["score"]["before"] == 0.0 and later["score"]["after"] == 1.0
    assert later["score"]["delta"] > 0, "分差符号反了：把后来的那份当成了上一次"
    assert progress.of_submission(conn, "zzz", labels=LABELS) is None


# ── 7. 命令行：助教侧看到的是同一句话 ────────────────────────────────────────


def test_cli_progress_prints_the_page_headline_without_assertion_ids(db, capsys):
    """助教在终端看到的，必须和学生页上那句一模一样——两处各写一套文案迟早会分叉。"""
    conn, _registry = db
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    _save(conn, _fixture_report("raw-empty", "sub-1"), user_id="u-a", at="2026-11-01T00:00:00Z")
    _save(conn, _fixture_report("raw-perfect", "sub-2"), user_id="u-a", at="2026-11-02T00:00:00Z")

    rc = hosting.main(["progress", "--db", path, "--user", "u-a", "--task", TASK_ID])

    out = capsys.readouterr().out
    assert rc == 0
    assert "比上次高 1.00" in out
    assert "R1_" not in out and "R2_" not in out
    assert "这次改好了" in out


def test_cli_progress_says_so_when_there_is_only_one_submission(db, capsys):
    conn, _registry = db
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    _save(conn, _fixture_report("raw-empty", "sub-1"), user_id="u-a", at="2026-11-01T00:00:00Z")

    rc = hosting.main(["progress", "--db", path, "--user", "u-a", "--task", TASK_ID])

    assert rc == 0
    assert "还没有两次提交" in capsys.readouterr().out
