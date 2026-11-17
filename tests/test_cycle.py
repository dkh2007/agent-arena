"""B-04h ③：一次跑批「停在哪一步」的判据（round-101）。

前面各层都只回答一个问题，`cycle` 把它们串起来并回答**流程**问题：
什么时候跑、谁批、页面那行字谁发。流程层的失效方式与算法层不同——
它不报错，只是**悄悄做了不该做的那一步**：

1. 没署名就把分改了（事后查不到谁批的）；
2. 演练与执行对不上还继续写（拿一份过期清单当依据）；
3. 把"发文案"也顺手做了（发出去收不回来）；
4. 归档覆盖上一次（复盘时只剩最后一次）。

本文件每一条都是这四种之一的正面锁。
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import baseline as bl  # noqa: E402
from arena import cycle as cy  # noqa: E402
from arena import diagnosis_report as dr  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
AT = "2026-11-17T09:00:00Z"
USER = "u-cy"


def _fixture(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def _payload(*, submission_id: str = "s-1", user_id: str = USER,
             fixture: str = "raw-perfect") -> dict:
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


def _submit(env, **kwargs) -> None:
    api.submit(_payload(**kwargs), registry=env["registry"], conn_factory=env["conn"])


def _stale_task() -> dict:
    """把 R2 的期望值改掉 ⇒ 满分作答从 1.0 掉到 0.5，一定会产生"分数变了"。"""
    task = copy.deepcopy(dr.load_task(TASK_PATH))
    for spec in task["rubric_credit"]:
        if spec["id"] == "R2_report_correct":
            spec["expected"]["total_amount"] += 1.0
            return task
    raise AssertionError("夹具变了：R2_report_correct 不在 rubric 里")


def _revisions(c, submission_id: str) -> int:
    return len(store.list_reappraisals(c, submission_id))


def _run(c, task, **kwargs):
    params = dict(reason="判据修订后批量回溯", changed_on="2026-11-10",
                  what_changed="改了 R2 的期望总额")
    params.update(kwargs)
    return cy.run_cycle(c, task, **params)


# ── 1. 没署名就不许写库 ─────────────────────────────────────────────────────

def test_dry_run_writes_nothing(env):
    _submit(env)
    c = env["conn"]()
    try:
        before = store.load_submission(c, "s-1")
        report = _run(c, _stale_task())
        after = store.load_submission(c, "s-1")
        assert report.applied is False and report.approved_by is None
        assert _revisions(c, "s-1") == 0
        assert before["diagnosis"] == after["diagnosis"]
        assert before["assert_score"] == after["assert_score"]
    finally:
        c.close()


def test_dry_run_still_says_what_would_change(env):
    """演练不等于"什么都不给"：它就是给人看"这一改会让多少人掉分"的那份清单。"""
    _submit(env)
    c = env["conn"]()
    try:
        report = _run(c, _stale_task())
        assert report.counted["score_changed"] == 1
        assert any("演练" in item for item in report.needs_human)
        assert [s["submission_id"] for s in report.pending_sends] == ["s-1"]
    finally:
        c.close()


def test_dry_run_shows_the_very_same_words_the_execution_will_send(env, tmp_path):
    """演练看到的话 = 真跑发出的话，逐字相同。

    若两套文案各写一份，差别不会报错，只会让 "先看清单再批准" 变成批准一份没人兑现的东西。
    """
    _submit(env)
    c = env["conn"]()
    try:
        dry = _run(c, _stale_task())
        real = _run(c, _stale_task(), approved_by="课程负责人")
        assert dry.pending_sends == real.pending_sends
        assert dry.pending_sends[0]["state"] == "recalculated"
        assert dry.pending_sends[0]["elements"] == real.pending_sends[0]["elements"]
    finally:
        c.close()


def test_execution_requires_a_name_and_records_it(env):
    _submit(env)
    c = env["conn"]()
    try:
        report = _run(c, _stale_task(), approved_by="课程负责人")
        assert report.applied is True and report.approved_by == "课程负责人"
        assert _revisions(c, "s-1") == 1
        reason = store.list_reappraisals(c, "s-1")[0]["reason"]
        assert "经 课程负责人 确认" in reason
    finally:
        c.close()


def test_blank_name_is_not_a_name(env):
    _submit(env)
    c = env["conn"]()
    try:
        with pytest.raises(cy.CycleError, match="空字符串"):
            _run(c, _stale_task(), approved_by="   ")
        assert _revisions(c, "s-1") == 0
    finally:
        c.close()


# ── 2. 演练与执行必须逐份对齐 ───────────────────────────────────────────────

def test_mismatch_between_dry_and_real_stops_the_batch(env, monkeypatch):
    """两次之间库被改过 ⇒ 抛。继续写等于拿一份对不上的清单当依据。"""
    _submit(env)
    original = bl.run_baseline
    calls = {"n": 0}

    def tampering(conn, tasks, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:            # 演练已经算完、真跑还没开始的那一刻
            row = store.load_report(conn, "s-1")
            row["score"]["ratio"] = 0.9     # 有人手工动过旧分
            conn.execute("UPDATE submissions SET diagnosis = ? WHERE submission_id = 's-1'",
                         (json.dumps(row, ensure_ascii=False, sort_keys=True),))
            conn.commit()
        return original(conn, tasks, **kwargs)

    monkeypatch.setattr(cy.bl, "run_baseline", tampering)
    c = env["conn"]()
    try:
        with pytest.raises(cy.CycleError, match="演练与执行不一致"):
            _run(c, _stale_task(), approved_by="课程负责人")
    finally:
        c.close()


def test_second_apply_does_not_add_a_revision(env):
    """同一判据跑第二遍不许再追加重评（"不重复改分"在流程层的体现）。"""
    _submit(env)
    c = env["conn"]()
    try:
        _run(c, _stale_task(), approved_by="课程负责人")
        assert _revisions(c, "s-1") == 1
        again = _run(c, _stale_task(), approved_by="课程负责人")
        assert _revisions(c, "s-1") == 1
        assert again.counted["scanned"] == 0      # 范围已收敛
        assert again.counted["score_changed"] == 0
    finally:
        c.close()


# ── 3. 发送这一步不许代码替人做 ─────────────────────────────────────────────

def test_pending_sends_only_contains_page_disclosures(env):
    """只在档案里的、不告知的，都不许混进"要发出去的"清单。"""
    _submit(env, submission_id="s-1")
    _submit(env, submission_id="s-2", user_id="u-other", fixture="raw-empty")
    c = env["conn"]()
    try:
        report = _run(c, _stale_task(), approved_by="课程负责人")
        states = {item["state"] for item in report.pending_sends}
        assert "unchanged" not in states
        assert all(item["state"] in ("recalculated", "legacy_scored")
                   for item in report.pending_sends)
        assert any("本工具不发送" in item for item in report.needs_human)
    finally:
        c.close()


def test_there_is_no_send_function():
    """锁一个**不存在**的东西：这一层刻意没有发送能力。

    有人"顺手"加一个 `send_notices()`，就等于让一次代码调用做了不可撤回的事；
    真需要发的时候，应该由界面/通知层做，并且那一步有人看着。
    """
    for name in dir(cy):
        assert not name.lower().startswith(("send", "push", "notify")), name


# ── 4. 归档：不覆盖，且能独立看懂 ────────────────────────────────────────────

def test_archive_is_readable_on_its_own(env, tmp_path):
    _submit(env)
    c = env["conn"]()
    try:
        report = _run(c, _stale_task(), approved_by="课程负责人")
        paths = report.archive(tmp_path / "run-1")
        text = paths["markdown"].read_text(encoding="utf-8")
        assert "跑批记录" in text and "改了 R2 的期望总额" in text
        assert "课程负责人" in text
        assert "本工具不发送" in text
        data = json.loads(paths["json"].read_text(encoding="utf-8"))
        assert data["applied"] is True and data["counted"]["reappraised"] == 1
    finally:
        c.close()


def test_archive_refuses_to_overwrite(env, tmp_path):
    _submit(env)
    c = env["conn"]()
    try:
        report = _run(c, _stale_task())
        report.archive(tmp_path / "run-1")
        with pytest.raises(cy.CycleError, match="不许覆盖"):
            report.archive(tmp_path / "run-1")
    finally:
        c.close()


def test_report_flags_the_undecided_ones(env):
    """T3 那批（0 分但证据进过 P）在流程层必须显式出现，不能只在代码里待着。"""
    _submit(env, submission_id="s-1", fixture="raw-no-file")
    c = env["conn"]()
    try:
        # 造出"补不上"：报告留着，产物抹掉
        c.execute("UPDATE submissions SET artifacts = NULL WHERE submission_id = 's-1'")
        c.commit()
        report = _run(c, _stale_task())
        assert any("掌握度待定" in item for item in report.needs_human)
    finally:
        c.close()


# ── 5. 手册与命令不许各说一套 ───────────────────────────────────────────────

def test_runbook_only_mentions_flags_the_cli_actually_has(capsys):
    """手册里的参数名与 CLI 对齐。

    这一条防的是"文档漂移"：有人改了参数名、忘了改手册，手册就会教人跑一条
    跑不通的命令——而它不会报错，只是照着做的人以为跑过了。
    """
    with pytest.raises(SystemExit):
        cy.main(["--help"])
    helptext = capsys.readouterr().out
    text = (ROOT / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")
    flags = set(re.findall(r"--[a-z][a-z-]+", text))
    assert flags, "手册里一个参数都没提到？先确认读的是对的文件"
    missing = sorted(flag for flag in flags if flag not in helptext)
    assert not missing, f"手册提到了不存在的参数：{missing}"
