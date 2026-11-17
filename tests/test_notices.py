"""B-04g 落地：「每条记录算什么、要不要说、说什么」（round-100）。

前四层都只产出"数"：谁受影响、谁有分、哪条规则改了、谁的记录用到了它。
人把口径拍完之后，最后一段是**把数翻成句子**，而这一段最容易出的错不是算错，
是**说错**——少一件必说的事，学生就会自己补一个更坏的版本。

本文件锁五条，每条都对应一个真实的失效方式：

1. **四件必须齐全**：当年有分那批与分数真变了那批，各有四件必说的事（缺一件就有人
   把系统的改动读成自己的退步）；四件按"件"拆开存，不靠字符串包含来断言；
2. **`pending_mastery` 不许并进 `legacy_unscored`**：把"掌握度被冻住"那批悄悄归到
   "没什么可解释"，等于替人拍了"不用管"；
3. **不告知不等于没记录**：0 分那批不推送，但诊断必须还在（本项目已拍"数据缺失 ≠
   能力不足"，删诊断就是把这句反过来说）；
4. **内部术语不许进对外文案**：`ratio` / 断言 / 重评 这些词对学生只是噪音，
   还会让人以为平台出了故障；
5. **不给将来承诺**：任何一句"以后不会再变"都不许出现——标准还会修，给不了这个保证。
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
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
AT = "2026-11-17T09:00:00Z"
CHANGED_ON = "2026-11-10"
WHAT = "改了一条判据的期望值"
REASON = "B-04g: 判据修订后按现行判据重算"


def _fixture(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def _payload(*, submission_id: str, fixture: str = "raw-perfect",
             user_id: str = "u-notice") -> dict:
    raw = _fixture(fixture)
    sub = raw["submission"]
    return {
        "task_id": sub["task_id"],
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


def _seed_lost_artifacts(env, submission_id: str, fixture: str = "raw-perfect") -> None:
    _submit(env, _payload(submission_id=submission_id, fixture=fixture))
    c = env["conn"]()
    try:
        c.execute("UPDATE submissions SET artifacts = NULL WHERE submission_id = ?",
                  (submission_id,))
        c.commit()
    finally:
        c.close()


def _changed_task() -> dict:
    """"人把标准改了"：R2 期望的总额 +1（满分作答会掉一半）。"""
    task = copy.deepcopy(dr.load_task(TASK_PATH))
    for spec in task["rubric_credit"]:
        if spec["id"] == "R2_report_correct":
            spec["expected"]["total_amount"] += 1.0
            return task
    raise AssertionError("夹具变了：R2_report_correct 不在 rubric 里")


def _plan(env, task: dict | None = None) -> nt.NoticePlan:
    c = env["conn"]()
    try:
        return nt.plan_notices(c, task or dr.load_task(TASK_PATH),
                               changed_on=CHANGED_ON, what_changed=WHAT)
    finally:
        c.close()


# ── 1. 四件必须齐全 ─────────────────────────────────────────────────────────

def test_scored_but_unrepairable_gets_all_four_elements_on_the_page(env):
    """当年有分、现在判不了：原分不动，但页面上必须有四件。

    四件按"件"断言（不是字符串包含）：少一件，学生会自己补一个更坏的版本。
    """
    _seed_lost_artifacts(env, "s-scored")
    notice = _plan(env, _changed_task()).notices[0]

    assert (notice.state, notice.disclosure) == ("legacy_scored", "page")
    assert notice.keys() == ("which_rubric", "comparable", "next_step", "not_your_fault")
    text = notice.text()
    assert "100 分" in text, "必须写出这个分是多少（不是「你有个分」）"
    assert CHANGED_ON in text, "必须说清是按哪天之前的判据给的"
    assert "不能和同学比" in text
    assert "不需要重做" in text
    assert "这不是你的问题" in text


def test_recalculated_record_names_what_changed_and_the_new_score(env):
    """分数真的变了：变前变后都要写出来，并且说清原因不在学生。

    这条走完整链路：改判据 → 基线重算 → 生效分变化 → 处置计划里落 `recalculated`。
    """
    _submit(env, _payload(submission_id="s-fixed"))
    c = env["conn"]()
    try:
        report = store.load_report(c, "s-fixed")
        report.pop("rubric_fingerprint")          # 历史形态：没有指纹
        c.execute("UPDATE submissions SET diagnosis = ? WHERE submission_id = ?",
                  (json.dumps(report, ensure_ascii=False, sort_keys=True), "s-fixed"))
        c.commit()
        task = _changed_task()
        bl.run_baseline(c, [task], reason=REASON, created_at=AT)

        plan = nt.plan_notices(c, task, changed_on=CHANGED_ON, what_changed=WHAT)
        notice = plan.notices[0]
        assert (notice.state, notice.disclosure) == ("recalculated", "page")
        assert notice.keys() == ("what_changed", "why_me", "comparable", "next_step")
        text = notice.text()
        assert "50 分" in text and "100 分" in text, "变前、变后各是多少都要写出来"
        assert "不是你" in text
    finally:
        c.close()


# ── 2. 待定不许被并进"没什么可解释" ────────────────────────────────────────

def test_evidence_only_record_is_pending_not_folded_into_unscored(env):
    """0 分但过程证据进过 P（T3）：标成待定，**不许**并进"当年没分、不用管"。

    并进去等于替人拍了"掌握度那侧不用管"，而那是 B-07b 的口径。
    """
    _seed_lost_artifacts(env, "s-evidence", fixture="raw-no-trace")
    plan = _plan(env)
    notice = plan.notices[0]

    assert notice.state == "pending_mastery"
    assert notice.needs_decision is True
    assert notice.disclosure == "none"
    assert plan.needs_decision() == (notice,)
    assert plan.by_state("legacy_unscored") == ()


def test_zero_score_unrepairable_keeps_its_diagnosis_and_is_not_announced(env):
    """当年没分那批：不推送，但诊断一个字都不许少。

    "数据缺失 ≠ 能力不足" 已经拍过，所以"不给分"和"把当时为什么卡住的记录删掉"
    是两件事；删掉后者就是把这句口径反过来说。
    """
    _seed_lost_artifacts(env, "s-zero", fixture="raw-empty")
    plan = _plan(env)
    notice = plan.notices[0]

    assert (notice.state, notice.disclosure, notice.elements) == ("legacy_unscored", "none", ())

    c = env["conn"]()
    try:
        report = store.load_report(c, "s-zero")
        assert report["assertions"], "诊断还在（断言逐条都留着）"
        assert report["skill_gaps"] is not None
    finally:
        c.close()


# ── 3. 没被碰到的那批：只留档案一句 ────────────────────────────────────────

def test_untouched_records_only_get_an_archive_line(env):
    """判据没改到的记录：不进页面，只在档案里留一句"已复核，结论不变"。"""
    _submit(env, _payload(submission_id="s-plain"))
    _seed_lost_artifacts(env, "s-scored-lost")     # 补不上，但这次判据没碰它
    plan = _plan(env)
    by_id = {n.submission_id: n for n in plan.notices}

    assert by_id["s-plain"].state == "unchanged"
    assert by_id["s-plain"].disclosure == "archive"
    assert by_id["s-scored-lost"].state == "unchanged"
    assert by_id["s-scored-lost"].disclosure == "archive"
    assert plan.disclosed("page") == ()


# ── 4. 对外文案的硬约束 ────────────────────────────────────────────────────

def test_internal_jargon_is_rejected_before_it_reaches_a_student():
    """内部术语一旦混进文案，学生读到的是"平台出故障"，不是"标准改了"。"""
    with pytest.raises(nt.NoticeError, match="内部术语"):
        nt._check_plain("你的 ratio 变了")
    with pytest.raises(nt.NoticeError, match="内部术语"):
        nt._check_plain("重评已完成")


def _join(elements: tuple[tuple[str, str], ...]) -> str:
    return "\n".join(text for _, text in elements)


def _template_texts() -> list[tuple[str, str]]:
    """四段模板的产出（不经过库）。

    为什么不只用库里的记录来扫：库只能摆出**它当前有**的那几种状态，
    新写的模板要是这次恰好没有对应记录，就永远扫不到。这里把四段全叫一遍。
    """
    out = [("recalculated", _join(nt._recalculated(
        1.0, 0.5, changed_on=CHANGED_ON, what_changed=WHAT, scope="范围一句话")))]
    out.append(("legacy_scored", _join(nt._legacy_scored(0.75, changed_on=CHANGED_ON))))
    out.append(("unchanged", _join(nt._unchanged_recalculated())))
    out.append(("unchanged_current", _join(nt._unchanged_current())))
    return out


def test_no_notice_text_contains_jargon_or_promises(env):
    """所有对外文案（含库里还没有对应记录的那几段）都不许出现内部术语、不许承诺将来。"""
    _seed_lost_artifacts(env, "s-scored")
    _submit(env, _payload(submission_id="s-plain"))
    corpus = _template_texts() + [(n.state, n.text()) for n in _plan(env).notices]
    for label, text in corpus:
        for word in nt.FORBIDDEN_IN_TEXT:
            assert word.lower() not in text.lower(), f"{label} 文案里出现了 {word}"
        assert "以后不会" not in text, f"{label} 文案里给了将来承诺"
        assert "不会再变" not in text, f"{label} 文案里给了将来承诺"


def test_missing_facts_are_rejected_instead_of_invented(env):
    """日期/原因/范围缺一个就抛：编一个日期进学生页面，比不说更糟。"""
    _submit(env, _payload(submission_id="s-plain"))
    c = env["conn"]()
    try:
        task = dr.load_task(TASK_PATH)
        for kwargs in ({"changed_on": "", "what_changed": WHAT},
                       {"changed_on": CHANGED_ON, "what_changed": "   "}):
            with pytest.raises(nt.NoticeError, match="必须写"):
                nt.plan_notices(c, task, **kwargs)
    finally:
        c.close()


# ── 5. 一条记录只能落一格 + 只读 ───────────────────────────────────────────

def test_every_record_falls_in_exactly_one_state(env):
    _seed_lost_artifacts(env, "s-scored")
    _seed_lost_artifacts(env, "s-zero", fixture="raw-empty")
    _seed_lost_artifacts(env, "s-evidence", fixture="raw-no-trace")
    _submit(env, _payload(submission_id="s-plain"))
    plan = _plan(env)

    assert plan.counted()["records"] == 4
    assert sum(v for k, v in plan.counted().items() if k != "records") == 4
    ids = [n.submission_id for n in plan.notices]
    assert len(ids) == len(set(ids)) == 4


def test_planning_notices_changes_nothing(env):
    """只读：跑两遍结果逐字段相同，提交数、重评链长度、落库报告一个字节都不动。"""
    _seed_lost_artifacts(env, "s-scored")
    _submit(env, _payload(submission_id="s-plain"))
    c = env["conn"]()
    try:
        before = (store.count_submissions(c),
                  len(store.list_reappraisals(c, "s-scored")),
                  json.dumps(store.load_report(c, "s-plain"), sort_keys=True))
        first = nt.plan_notices(c, dr.load_task(TASK_PATH),
                                changed_on=CHANGED_ON, what_changed=WHAT)
        second = nt.plan_notices(c, dr.load_task(TASK_PATH),
                                 changed_on=CHANGED_ON, what_changed=WHAT)
        after = (store.count_submissions(c),
                 len(store.list_reappraisals(c, "s-scored")),
                 json.dumps(store.load_report(c, "s-plain"), sort_keys=True))
        assert first.to_dict() == second.to_dict()
        assert before == after
    finally:
        c.close()
