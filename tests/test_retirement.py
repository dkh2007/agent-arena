"""B-04h ②：「确认不可回溯」的终止标记（round-101）。

## 它解决的具体麻烦

v4 之前落库的提交没有 `artifacts`，**永远重评不了**。而 `scope_for_task` 的规矩是
"读不到指纹 ⇒ 要重评"，于是这批人每跑一次基线就被重新试一遍、再报一次
`missing_artifacts`——跑批报告因此越长越长，读的人得从重复里挑出"这次真的变了什么"。

## 为什么不能自动终止

"试了三次就算了吧"这种规则会把**平台当年没存产物**变成**这个人不重要**。
所以出口必须是人给的：带理由、带人名、可撤销。

## 本文件锁五层

1. **标记不许说谎**：有产物的提交此刻就能重评，给它打标记是拿一句"补不回来"
   盖住一件马上能做的事 ⇒ 抛；
2. **标记只终止重试，不终止交代**：补不上的名单、T1/T2/T3 分档、曝光表、对外文案
   一样都不许少——把标记做成"从名单里消失"，它就变成了静默忘掉一个人的开关；
3. **前提会被推翻**：产物补回来之后标记不再生效，该行回到普通分类，
   并在 `ignored_marks` 里显名（"有一条标记被现实推翻了"要有人看见）；
4. **撤销不删行**：`marked_at / reason / marked_by` 都留着，只多一个撤销时间；
5. **范围按 task_id 收**：一个任务的标记不许影响另一个任务的重试范围。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import baseline as bl  # noqa: E402
from arena import diagnosis_report as dr  # noqa: E402
from arena import notices as nt  # noqa: E402
from arena import reappraisal as rap  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
OTHER_TASK_PATH = ROOT / "tasks" / "dbg-002-invoice-report.yaml"
AT = "2026-11-17T09:00:00Z"
USER = "u-ret"


def _fixture(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def _payload(*, submission_id: str = "s-1", user_id: str = USER,
             fixture: str = "raw-perfect", task_id: str | None = None) -> dict:
    raw = _fixture(fixture)
    sub = raw["submission"]
    return {
        "task_id": task_id or sub["task_id"],
        "task_version": "2026-11-13.1",
        "user_id": user_id,
        "agent_id": sub["agent_id"],
        "submission_id": submission_id,
        "task_git_hash": sub["task_git_hash"],
        "evaluated_at": AT,
        "invoke": sub.get("invoke"),
        "trace": raw.get("trace"),
        "artifacts": raw.get("artifacts"),
    }


@pytest.fixture()
def env(tmp_path):
    db = tmp_path / "arena.db"
    registry = api.TaskRegistry()
    api.bootstrap(db, registry)
    return {"db": db, "registry": registry, "conn": lambda: store.connect(db)}


def _submit(env, payload) -> dict:
    return api.submit(payload, registry=env["registry"], conn_factory=env["conn"])


def _history_blocked(env, submission_id: str, *, user_id: str = USER,
                     fixture: str = "raw-perfect", task_id: str | None = None) -> None:
    """造一份**真实的**「补不上」：v5 之前的报告（无指纹）+ 产物没存下来。

    两步都要：只去掉指纹，它会被重评并补上；只去掉产物，它仍在 `current` 桶里
    （指纹是现行的），本来就不会重试——那样测出来的"标记有效"是假的。
    """
    _submit(env, _payload(submission_id=submission_id, user_id=user_id,
                          fixture=fixture, task_id=task_id))
    c = env["conn"]()
    try:
        report = store.load_report(c, submission_id)
        assert "rubric_fingerprint" in report
        report.pop("rubric_fingerprint")
        c.execute("UPDATE submissions SET diagnosis = ?, artifacts = NULL "
                  "WHERE submission_id = ?",
                  (json.dumps(report, ensure_ascii=False, sort_keys=True), submission_id))
        c.commit()
    finally:
        c.close()


def _mark(c, submission_id: str, *, reason: str = "v4 之前产物未落盘，确认无法回溯",
          marked_by: str = "课程助教") -> dict:
    return store.mark_unrecoverable(c, submission_id, reason=reason, marked_by=marked_by,
                                    created_at=AT)


def _task() -> dict:
    return dr.load_task(TASK_PATH)


def _changed_task() -> dict:
    """模拟"人把标准答案改了"：R2 期望的总额 +1。

    曝光表要它才肯说话：任务没改时 `rule_diff` 是空差异、每份提交都 `untouched`，
    于是 `must_explain()` 天然为空——那不是"标记把人删了"，而是"这次什么都没改"。
    """
    task = copy.deepcopy(_task())
    for spec in task["rubric_credit"]:
        if spec["id"] == "R2_report_correct":
            spec["expected"]["total_amount"] += 1.0
            return task
    raise AssertionError("夹具变了：R2_report_correct 不在 rubric 里")


# ── 1. 标记不许说谎 ─────────────────────────────────────────────────────────

def test_mark_refuses_a_submission_that_can_still_be_reappraised(env):
    """有产物 ⇒ 抛。标记的意思是「输入永远回不来」，不是「这次先不跑」。"""
    _submit(env, _payload(submission_id="s-live"))
    c = env["conn"]()
    try:
        with pytest.raises(store.StoreError, match="此刻就能重评"):
            _mark(c, "s-live")
        assert store.unrecoverable_marks(c) == {}
    finally:
        c.close()


def test_mark_requires_a_reason_and_an_author(env):
    """没有理由、没有作者的"结案"与"悄悄跳过"没有区别。"""
    _history_blocked(env, "s-old")
    c = env["conn"]()
    try:
        for bad in ({"reason": "   ", "marked_by": "助教"}, {"reason": "没存", "marked_by": ""}):
            with pytest.raises(store.StoreError):
                store.mark_unrecoverable(c, "s-old", created_at=AT, **bad)
        assert store.unrecoverable_marks(c) == {}
    finally:
        c.close()


def test_marking_an_unknown_submission_raises(env):
    c = env["conn"]()
    try:
        with pytest.raises(store.StoreError, match="没有这条提交记录"):
            _mark(c, "s-ghost")
    finally:
        c.close()


def test_double_mark_is_refused_but_remarking_after_revoke_is_allowed(env):
    """重复标记会覆盖"谁在什么时候决定的"；撤销后再标是一次**新**决定，允许。"""
    _history_blocked(env, "s-old")
    c = env["conn"]()
    try:
        first = _mark(c, "s-old")
        assert first["revoked_at"] is None
        with pytest.raises(store.StoreError, match="标记过「确认不可回溯」"):
            _mark(c, "s-old", reason="再标一次")

        store.revoke_unrecoverable(c, "s-old", reason="产物从旧备份补回来了", revoked_at=AT)
        again = _mark(c, "s-old", reason="补回来的那份也丢了", marked_by="我")
        assert again["revoked_at"] is None and again["reason"] == "补回来的那份也丢了"
    finally:
        c.close()


# ── 2. 只终止重试，不终止交代 ───────────────────────────────────────────────

def test_marked_row_leaves_the_retry_scope(env):
    _history_blocked(env, "s-old")
    c = env["conn"]()
    try:
        before = rap.scope_for_task(c, _task())
        assert "s-old" in before.unfingerprinted and before.retired == ()

        _mark(c, "s-old")
        after = rap.scope_for_task(c, _task())
        assert after.retired == ("s-old",)
        assert "s-old" not in after.all_ids
        assert "s-old" not in after.unfingerprinted
    finally:
        c.close()


def test_baseline_stops_retrying_marked_rows(env):
    """连跑两次：第一次会被重试并报 `missing_artifacts`，标记之后不再出现。

    这条锁的是"报告不再线性变长"——判据不是"没有报错"，而是
    **同一批行不会在每一轮里重复出现**。
    """
    _history_blocked(env, "s-old")
    _submit(env, _payload(submission_id="s-live", user_id="u-other"))
    c = env["conn"]()
    try:
        first = bl.run_baseline(c, [_task()], reason="第一次", created_at=AT)
        assert {o.submission_id for o in first.blocked()} == {"s-old"}
        assert first.counted()["retired"] == 0

        _mark(c, "s-old")
        second = bl.run_baseline(c, [_task()], reason="标记之后再跑", created_at=AT)
        assert second.blocked() == ()
        assert second.counted()["retired"] == 1
        assert second.counted()["scanned"] == 0        # retired 不算进扫描数
        assert all(o.submission_id != "s-old" for o in second.outcomes)
    finally:
        c.close()


def test_marking_does_not_remove_anyone_from_the_explanation_path(env):
    """这一层唯一危险的误读：**标一下就不用解释了**。四张嘴都要还在。"""
    _history_blocked(env, "s-old")
    c = env["conn"]()
    try:
        _mark(c, "s-old")

        blocked = {row["submission_id"] for row in store.blocked_submissions(c)}
        assert blocked == {"s-old"}

        split = bl.tier_split(c)
        assert [item.submission_id for item in split.needs_explanation()] == ["s-old"]

        exposure = bl.exposure_split(c, _changed_task())
        assert "s-old" in {row.submission_id for row in exposure.must_explain()}

        plan = nt.plan_notices(c, _changed_task(), changed_on="2026-11-10",
                               what_changed="改了 R2 的期望值")
        notice = next(n for n in plan.notices if n.submission_id == "s-old")
        assert notice.state == "legacy_scored" and notice.disclosure == "page"
        assert notice.retired is True
        assert plan.retired() == (notice,)
    finally:
        c.close()


def test_the_student_text_is_byte_identical_whether_marked_or_not(env):
    """标记不许改对外文案：一个字都一样，只多一个内部标记位。"""
    _history_blocked(env, "s-old")
    c = env["conn"]()
    try:
        before = nt.plan_notices(c, _task(), changed_on="2026-11-10",
                                 what_changed="改了 R2 的期望值")
        _mark(c, "s-old")
        after = nt.plan_notices(c, _task(), changed_on="2026-11-10",
                                what_changed="改了 R2 的期望值")
        assert before.notices[0].text() == after.notices[0].text()
        assert before.notices[0].to_dict() != after.notices[0].to_dict()   # 只差 retired
    finally:
        c.close()


# ── 3. 前提会被推翻 ─────────────────────────────────────────────────────────

def test_a_mark_loses_effect_when_the_input_comes_back(env):
    """产物补回来 ⇒ 标记的前提没了 ⇒ 按普通行重新分类，并在 `ignored_marks` 里显名。"""
    _history_blocked(env, "s-old")
    c = env["conn"]()
    try:
        _mark(c, "s-old")
        assert rap.scope_for_task(c, _task()).retired == ("s-old",)

        c.execute("UPDATE submissions SET artifacts = ? WHERE submission_id = 's-old'",
                  (json.dumps({"report.md": "补回来的产物"}, ensure_ascii=False),))
        c.commit()

        scope = rap.scope_for_task(c, _task())
        assert scope.retired == ()
        assert scope.ignored_marks == ("s-old",)
        assert "s-old" in scope.unfingerprinted      # 没有指纹，照旧要重评
    finally:
        c.close()


def test_revoked_mark_returns_to_the_retry_scope(env):
    _history_blocked(env, "s-old")
    c = env["conn"]()
    try:
        _mark(c, "s-old")
        store.revoke_unrecoverable(c, "s-old", reason="判断错了", revoked_at=AT)
        scope = rap.scope_for_task(c, _task())
        assert scope.retired == () and "s-old" in scope.unfingerprinted
        assert store.unrecoverable_marks(c) == {}
        # 撤销不删行：那句话和那个人还在
        kept = store.load_mark(c, "s-old")
        assert kept["marked_at"] == AT and kept["marked_by"] == "课程助教"
        assert kept["revoked_reason"] == "判断错了"
    finally:
        c.close()


# ── 4. 范围与副作用 ─────────────────────────────────────────────────────────

def test_marks_are_scoped_by_task(env):
    """一个任务的标记不许影响另一个任务的重试范围。"""
    _history_blocked(env, "s-old")
    other = dr.load_task(OTHER_TASK_PATH)
    _history_blocked(env, "s-other", user_id="u-other", task_id=other["task_id"])
    c = env["conn"]()
    try:
        _mark(c, "s-old")
        assert set(store.unrecoverable_marks(c, _task()["task_id"])) == {"s-old"}
        assert store.unrecoverable_marks(c, other["task_id"]) == {}

        other_scope = rap.scope_for_task(c, other)
        assert other_scope.retired == ()
        assert "s-other" in other_scope.unfingerprinted
    finally:
        c.close()


def test_marking_touches_only_the_mark_table(env):
    """不碰历史证据：提交行一个字节不变，也不新增提交。"""
    _history_blocked(env, "s-old")
    c = env["conn"]()
    try:
        rows = [dict(r) for r in c.execute("SELECT * FROM submissions ORDER BY submission_id")]
        count = store.count_submissions(c)
        _mark(c, "s-old")
        assert [dict(r) for r in c.execute(
            "SELECT * FROM submissions ORDER BY submission_id")] == rows
        assert store.count_submissions(c) == count
    finally:
        c.close()


def test_mark_is_copied_nowhere_else(env):
    """标记只有一处真相源：`unrecoverable_marks`。

    抄到 `submissions` 加一列也能跑，但那样"撤销"要改两处，
    而漏改一处的结果是"界面上说补不回来、实际又在重试"——第二真相源的老病。
    """
    cols = {r[1] for r in env["conn"]().execute("PRAGMA table_info(submissions)")}
    assert not any("unrecoverable" in c or "retired" in c for c in cols)
