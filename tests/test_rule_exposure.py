"""B-04g：判据改动之后，"到底谁的提交真的用到了那条改过的规则"（round-99）。

分档（`tier_split`）回答"补不上的这批里谁当年有分、要解释"。但那还是一个**全体**的数字：
整份指纹只知道"这个任务所有提交都该重评"，于是"要交代的人"被默认成了"补不上的全部"。
外部意见（`b04g-external-ai-answer.md`）的第一步就是把这个人群切小，问题只有一句：
**这次练习里，到底有没有触发那条写错的规则？**

在库里这句话只能落成一件可核对的事：**那条规则当年是不是真的被判了**
（`pass` / `fail`）。如果它当年是 `blocked`（前置断言没过）或 `unscored`（trace 缺失），
它的判据根本没参与判定，改它改不动这份分。

本文件锁四层，每层都在"改坏了会静默"的地方下判据：

1. **单条指纹只认自己那条规则**：改 R2 不许动 R3 的指纹；换位置只动整份指纹，
   所以单条指纹**不能**拿来决定要不要重评（那是有语义的改动）；
2. **"不知道"不是"没变"**：历史报告没有这个键 ⇒ `rule_diff()` 返回 `None`，
   `exposure_split()` 把它归 `unknown`（留在交代名单里），不许当成 `untouched`；
3. **判过没判过要分开**：`touched` / `not_applied` 的差别是"这条规则当年有没有参与判定"，
   合并会让"当年被门禁拦住、连 R2 都没走到"的提交被说成"受 R2 改动影响"；
4. **范围必须按 task_id 收**：规则 id 是全库骨架共用的（R1…R5），
   不收范围会把另一个任务的改动算到这个任务的人头上。
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
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
OTHER_TASK_PATH = ROOT / "tasks" / "dbg-002-invoice-report.yaml"
AT = "2026-11-17T09:00:00Z"
USER = "u-exp"


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
    return {"db": db, "registry": registry,
            "conn": lambda: store.connect(db)}


def _submit(env, payload) -> dict:
    return api.submit(payload, registry=env["registry"], conn_factory=env["conn"])


def _seed_lost_artifacts(env, submission_id: str, fixture: str = "raw-perfect",
                         task_id: str | None = None) -> None:
    """当年判过、给过分，但产物没存下来（`artifacts` 列 v4 才加）。

    与 test_baseline 的同名 helper 同一手法：先正常提交，再把这一列置空。
    直接构造 `artifacts=None` 的 payload 是另一件事（当年学生就没交），
    两者在库里都是 NULL，但分数档位完全不同。
    """
    _submit(env, _payload(submission_id=submission_id, fixture=fixture, task_id=task_id))
    c = env["conn"]()
    try:
        c.execute("UPDATE submissions SET artifacts = NULL WHERE submission_id = ?",
                  (submission_id,))
        c.commit()
    finally:
        c.close()


def _edit_task(mutate) -> dict:
    task = copy.deepcopy(dr.load_task(TASK_PATH))
    mutate(task)
    return task


def _bump(task: dict, rule_id: str, key: str = "expected") -> None:
    """把某条规则的判据改一个数——模拟"人改了标准答案"。"""
    for spec in task["rubric_gate"] + task["rubric_credit"]:
        if spec["id"] == rule_id:
            target = spec[key]
            first = next(k for k, v in target.items() if isinstance(v, (int, float)))
            target[first] = target[first] + 1
            return
    raise AssertionError(f"{rule_id} 不在 rubric 里")


# ── 1. 单条指纹：只认自己那条规则 ────────────────────────────────────────────

def test_editing_one_rule_moves_only_that_rules_fingerprint():
    """改 R2 的判据，R3/R4/R1/R5 的指纹必须一字不动，整份指纹必须变。

    这是"缩窄人群"能成立的物理基础：没有它，"哪条规则变了"只能靠人回忆。
    """
    base = dr.load_task(TASK_PATH)
    edited = _edit_task(lambda t: _bump(t, "R2_report_correct"))

    before = dr.rubric_rule_fingerprints(base)
    after = dr.rubric_rule_fingerprints(edited)

    assert set(before) == set(after)
    assert [r for r in before if before[r] != after[r]] == ["R2_report_correct"]
    assert dr.rubric_fingerprint(base) != dr.rubric_fingerprint(edited)


def test_reordering_rules_moves_the_whole_fingerprint_but_no_single_rule_one():
    """调换两条规则的位置：整份指纹变、单条指纹全不变。

    这条不是在测实现细节，是在**锁住一个不许被误用的分工**：
    换位置会改执行顺序（`depends_on` 语义），必须触发重评；
    但单条指纹看不出来。所以单条指纹只能用来解释"哪条规则改了"，
    绝不能替代整份指纹去决定"要不要重评"。
    """
    base = dr.load_task(TASK_PATH)
    swapped = copy.deepcopy(base)
    credit = swapped["rubric_credit"]
    credit[1], credit[2] = credit[2], credit[1]

    assert dr.rubric_rule_fingerprints(base) == dr.rubric_rule_fingerprints(swapped)
    assert dr.rubric_fingerprint(base) != dr.rubric_fingerprint(swapped)


def test_duplicate_rule_ids_are_rejected_before_a_fingerprint_exists():
    """同一个 id 出现在 gate 和 credit 里 ⇒ 抛。

    "哪条规则变了"没有确定答案，所以不许给它起指纹。校验在 `rubric_layers()`
    （本函数的必经前置）里做，本文件只锁"这条门确实挡在前面"——
    将来若有人绕过分层校验直接调指纹，这里会先红。
    """
    task = _edit_task(lambda t: t["rubric_credit"][0].__setitem__("id", "R1_file_exists"))
    with pytest.raises(dr.ReportError, match="只能属于一层"):
        dr.rubric_rule_fingerprints(task)


def test_every_report_carries_rule_fingerprints():
    """报告无条件带单条指纹，且整份报告仍过 schema（新键是可选的，历史报告不受影响）。"""
    task = dr.load_task(TASK_PATH)
    report = dr.build_report(task, _fixture("raw-perfect"))

    assert report["rubric_rule_fingerprints"] == dr.rubric_rule_fingerprints(task)
    dr.validate_report(report)


# ── 2. 差异层：「不知道」不许被当成「没变」 ──────────────────────────────────

def test_legacy_report_without_rule_fingerprints_is_unknown_not_unchanged():
    """缺这个键 ⇒ `None`。返回空差异会把"不认识"伪装成"没改过"。"""
    task = dr.load_task(TASK_PATH)
    assert bl.rule_diff(None, task) is None


def test_rule_diff_separates_changed_added_and_removed():
    """三种差异分开报：改了一条、加了一条、删了一条，三者不能合并成一个"变了"。"""
    task = _edit_task(lambda t: _bump(t, "R2_report_correct"))
    task["rubric_credit"].append({
        "id": "R9_new_check", "type": "json_field_equals",
        "target": "artifacts.report", "expected": {"page_one_rows": 10},
        "depends_on": ["R1_file_exists"], "skill_id": "multi_step_planning",
        "error_patterns": [], "weight": 1,
    })
    task["rubric_credit"] = [s for s in task["rubric_credit"]
                             if s["id"] != "R4_no_param_hallucination"]

    old = dr.rubric_rule_fingerprints(dr.load_task(TASK_PATH))
    old.pop("R4_no_param_hallucination")      # 当年有、现在删了

    diff = bl.rule_diff(old, task)
    assert diff.changed == ("R2_report_correct",)
    assert diff.added == ("R9_new_check",)
    # R4 被删但 old 里也被我摘掉了 ⇒ 不算 removed；这里锁的是"只看确实消失的那些"
    assert diff.removed == ()


def test_empty_rule_fingerprint_map_is_bad_data_not_no_change():
    """空对象 ⇒ 抛。合法任务至少一条规则，空对象等于"当时没有任何规则"。"""
    with pytest.raises(bl.BaselineError, match="空对象"):
        bl.rule_diff({}, dr.load_task(TASK_PATH))


# ── 3. 曝光层：判过没判过要分开 ────────────────────────────────────────────

def test_edited_rule_that_was_really_judged_lands_in_touched_with_its_state(env):
    """当年判成 `pass` 的规则被改 ⇒ `touched`，并带出当年状态，进交代名单。

    这是外部意见要的那个数：不是"补不上 3 份"，而是"其中 1 份的 R2 当年过了、
    这次标准改了，所以他的分可能偏"。
    """
    _seed_lost_artifacts(env, "s-scored")
    task = _edit_task(lambda t: _bump(t, "R2_report_correct"))
    c = env["conn"]()
    try:
        report = bl.exposure_split(c, task)
        assert report.counted()["rows"] == 1
        row = report.rows[0]
        assert (row.tier, row.status) == ("T1", "touched")
        assert [(t.rule_id, t.state) for t in row.touches] == [("R2_report_correct", "pass")]
        assert [r.submission_id for r in report.must_explain()] == ["s-scored"]
        assert report.changed_rules == ("R2_report_correct",)
    finally:
        c.close()


def test_rule_that_never_ran_is_not_applied_not_touched(env):
    """当年被门禁拦住、R2 只是 `blocked` 的提交：改 R2 的判据改不动它的分。

    它就是外部意见里"这次练习到底有没有触发那条写错的规则"的**否定**答案。
    这不等于"它没受影响"（它是 0 分那批，另有 B-07b 的口径问题），
    而是"这次改动跟它这份分无关"。
    """
    _submit(env, _payload(submission_id="s-gate-failed", fixture="raw-empty"))
    c = env["conn"]()
    try:
        c.execute("UPDATE submissions SET artifacts = NULL WHERE submission_id = ?",
                  ("s-gate-failed",))
        c.commit()
        task = _edit_task(lambda t: _bump(t, "R2_report_correct"))
        report = bl.exposure_split(c, task)
        row = report.rows[0]
        assert row.status == "not_applied"
        assert [(t.rule_id, t.state) for t in row.touches] == [("R2_report_correct", "blocked")]
        assert report.counted()["not_applied"] == 1
    finally:
        c.close()


def test_new_rule_and_missing_fingerprints_stay_on_the_list(env):
    """两种"读不出结论"的情况都必须留在交代名单里，不许被当成"没受影响"。

    - `added_only`：新加了规则，当年没被它判过 ⇒ 读不出新判据下会不会不过；
    - `unknown`：历史报告没有单条指纹 ⇒ 压根不知道。
    剔除它们能让名单变短，代价是有人被永久静默跳过（B-04e 同一个坑）。
    """
    _seed_lost_artifacts(env, "s-added")
    _seed_lost_artifacts(env, "s-legacy")
    task = _edit_task(lambda t: t["rubric_credit"].append({
        "id": "R9_new_check", "type": "json_field_equals",
        "target": "artifacts.report", "expected": {"page_one_rows": 10},
        "depends_on": ["R1_file_exists"], "skill_id": "multi_step_planning",
        "error_patterns": [], "weight": 1,
    }))
    c = env["conn"]()
    try:
        report = store.load_report(c, "s-legacy")
        report.pop("rubric_rule_fingerprints")
        c.execute("UPDATE submissions SET diagnosis = ? WHERE submission_id = ?",
                  (json.dumps(report, ensure_ascii=False, sort_keys=True), "s-legacy"))
        c.commit()

        split = bl.exposure_split(c, task)
        by_id = {row.submission_id: row for row in split.rows}
        assert by_id["s-added"].status == "added_only"
        assert by_id["s-legacy"].status == "unknown"
        assert {r.submission_id for r in split.must_explain()} == {"s-added", "s-legacy"}
        assert split.counted()["unknown"] == 1
    finally:
        c.close()


def test_unchanged_task_marks_everyone_untouched(env):
    """判据没改 ⇒ 一条差异都没有 ⇒ `untouched`，不进交代名单。

    这一格必须能出现：否则"缩窄人群"就没有任何效果，等于没做。
    """
    _seed_lost_artifacts(env, "s-scored")
    c = env["conn"]()
    try:
        report = bl.exposure_split(c, dr.load_task(TASK_PATH))
        assert report.changed_rules == ()
        assert [row.status for row in report.rows] == ["untouched"]
        assert report.must_explain() == ()
    finally:
        c.close()


# ── 4. 范围与可见性 ─────────────────────────────────────────────────────────

def test_exposure_is_scoped_to_one_task(env):
    """别的任务的提交不许进来：规则 id 是全库骨架共用的，混进来就是凭空多一批人。

    两份提交都"补不上"（`artifacts` 为空），区别只在属于哪个任务——
    要是丢了 `task_id` 过滤，`s-elsewhere` 会顶进这张表，而它的规则根本不是这套。
    """
    _seed_lost_artifacts(env, "s-here")
    other = dr.load_task(OTHER_TASK_PATH)
    _seed_lost_artifacts(env, "s-elsewhere", task_id=other["task_id"])

    c = env["conn"]()
    try:
        report = bl.exposure_split(c, _edit_task(lambda t: _bump(t, "R2_report_correct")))
        assert [row.submission_id for row in report.rows] == ["s-here"]
        assert report.task_id == dr.load_task(TASK_PATH)["task_id"]
    finally:
        c.close()


def test_zero_score_submissions_are_reported_too_not_only_the_scored_ones(env):
    """T3（0 分但证据进了 P）也要出现在这张表里，不许只报 T1。

    名单收窄是 `must_explain()` 的事，不是取数的事——只取 T1 会让
    "分数 0、掌握度被抬过"的那批从视野里彻底消失。
    """
    _seed_lost_artifacts(env, "s-scored")
    # B-04i（甲）后 raw-no-trace 缺过程、passed_weight=0 ⇒ 按定义落 T2，
    # 不再是 T3 的样本；raw-no-file 是门禁不合格但过程证据照记，仍落 T3。
    _submit(env, _payload(submission_id="s-t3", fixture="raw-no-file"))
    c = env["conn"]()
    try:
        c.execute("UPDATE submissions SET artifacts = NULL WHERE submission_id = ?",
                  ("s-t3",))
        c.commit()
        split = bl.exposure_split(c, _edit_task(lambda t: _bump(t, "R2_report_correct")))
        tiers = {row.submission_id: row.tier for row in split.rows}
        assert tiers["s-t3"] == "T3", "raw-no-file 当年 0 分但过程证据照记 ⇒ 应在 T3"
        assert "T3" in {row.tier for row in split.rows}
    finally:
        c.close()


def test_missing_process_submission_falls_into_t2_not_t3(env):
    """B-04i 甲的下游推论：缺过程提交落 T2，不再挂「掌握度待拍」。

    不是新口径：T3 的定义就是 `ratio==0` 且某技能 `passed_weight>0`，
    而甲口径让缺过程那份 `passed_weight=0`。若有人日后把缺过程证据放回 P，
    这条会红，正好提醒 T3 与 notices 的 `pending_mastery` 要一起改。
    """
    _submit(env, _payload(submission_id="s-t2", fixture="raw-no-trace"))
    c = env["conn"]()
    try:
        c.execute("UPDATE submissions SET artifacts = NULL WHERE submission_id = ?", ("s-t2",))
        c.commit()
        split = bl.exposure_split(c, _edit_task(lambda t: _bump(t, "R2_report_correct")))
        tiers = {row.submission_id: row.tier for row in split.rows}
        assert tiers["s-t2"] == "T2"
    finally:
        c.close()
