"""B-04f：全库基线的判据（round-95）。

`reappraisal` 管"一份提交算得对不对"，`scope_for_task` 管"这个任务里谁对不上"。
还欠一件：**历史报告没有指纹**，所以它们每次都得整批重算。本轮把那一次跑通，
本文件锁的是这个动作的四条性质 + 一处"必须能指名"：

1. **能补上**：缺指纹的历史提交重评后，`unfingerprinted` 必须变空（它进了 `current`）；
2. **可重跑**：跑完再跑一次，不产生任何新修订（幂等，靠比对生效报告）；
3. **不静默跳过**：补不上的（缺 trace / 缺产物）要单独报出来，且跑完仍留在桶里——
   "还剩谁、为什么"必须能指名，否则"我们回溯过了"就是一句没根据的话；
4. **演练不落库**：`apply=False` 后范围与之前逐字段相同（这正是"确实没写"的证据）；
5. **要能指名到人**：分数变了的那些必须带 `user_id` 与 `delta`，
   否则基线跑完、报告很漂亮、没有一个人知道自己被重算过。

每条都写成"不满足就红"，不写"看起来对"。
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
from arena import mastery as ms  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
AT = "2026-11-17T09:00:00Z"
USER = "u-base"
SKILL = "multi_step_planning"
REASON = "baseline: 全库按现行判据打平（历史报告缺 rubric_fingerprint）"


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

    def conn():
        return store.connect(db)

    return {"db": db, "registry": registry, "conn": conn}


def _submit(env, payload) -> dict:
    return api.submit(payload, registry=env["registry"], conn_factory=env["conn"])


def _strip_fingerprint(c, submission_id: str) -> None:
    """把一份已落库的报告改回 v5 之前的样子：**没有** `rubric_fingerprint`。

    这是历史数据的真实形态（当时没这个概念），而不是"故意造坏数据"：
    trace / artifacts / 分数全部原样，只少一个键。用 UPDATE 而不是重新提交，
    是因为重新提交会得到一份"现在生成的"报告，那就不再是历史了。
    """
    report = store.load_report(c, submission_id)
    assert "rubric_fingerprint" in report, "前提不成立：这份报告本来就没有指纹"
    report.pop("rubric_fingerprint")
    c.execute(
        "UPDATE submissions SET diagnosis = ? WHERE submission_id = ?",
        (json.dumps(report, ensure_ascii=False, sort_keys=True), submission_id),
    )
    c.commit()


def _changed_task() -> dict:
    """模拟"人把标准答案改了"：R2 期望的总额 +1（满分作答会掉到 0.5）。"""
    task = copy.deepcopy(dr.load_task(TASK_PATH))
    for spec in task["rubric_credit"]:
        if spec["id"] == "R2_report_correct":
            spec["expected"]["total_amount"] += 1.0
            return task
    raise AssertionError("夹具变了：R2_report_correct 不在 rubric 里")


# ── 1. 能补上 ───────────────────────────────────────────────────────────────

def test_baseline_backfills_fingerprints_for_history_that_can_be_replayed(env):
    """缺指纹的历史提交，跑完基线必须从 `unfingerprinted` 进到 `current`。

    三条同时成立才算"补上了"：
      - 指纹桶清空（否则下次还得整批重评）；
      - 重评链只有 1 条（不是每跑一次加一条）；
      - 历史证据与当时分数一点没动（补的是"按哪版判"，不是"判成什么"）。
    """
    _submit(env, _payload(submission_id="s-legacy"))
    c = env["conn"]()
    try:
        _strip_fingerprint(c, "s-legacy")
        before_score = store.load_submission(c, "s-legacy")["assert_score"]
        task = dr.load_task(TASK_PATH)

        before = bl.all_task_scopes(c, [task])
        assert before[task["task_id"]].unfingerprinted == ("s-legacy",)

        report = bl.run_baseline(c, [task], reason=REASON, created_at=AT)
        after = report.after[task["task_id"]]

        assert after.unfingerprinted == (), "还有桶里有名 ⇒ 指纹没补上，下次仍要整批重评"
        assert after.current == ("s-legacy",)
        assert len(store.list_reappraisals(c, "s-legacy")) == 1
        assert store.effective_report(c, "s-legacy")["rubric_fingerprint"]
        assert store.load_submission(c, "s-legacy")["assert_score"] == before_score
        assert store.count_submissions(c) == 1
    finally:
        c.close()


def test_baseline_is_idempotent(env):
    """全部能重评时，跑完再跑一次：范围为空、零条结果、不再新增修订。

    这条防的是"每次跑都把同一批重评一遍"——那会让重评链变成一份流水账，
    而"这一批到底改动了谁"永远读不出来。
    （有补不上的那批时，第二次**不是**空的，见下一条测试。）
    """
    _submit(env, _payload(submission_id="s-legacy"))
    c = env["conn"]()
    try:
        _strip_fingerprint(c, "s-legacy")
        task = dr.load_task(TASK_PATH)
        first = bl.run_baseline(c, [task], reason=REASON, created_at=AT)
        assert first.counted()["reappraised"] == 1

        second = bl.run_baseline(c, [task], reason=REASON, created_at=AT)
        assert second.outcomes == (), "第二次还有活干 ⇒ 范围没收敛"
        assert second.counted()["scanned"] == 0
        assert len(store.list_reappraisals(c, "s-legacy")) == 1
    finally:
        c.close()


# ── 2. 不静默跳过 ───────────────────────────────────────────────────────────

def test_baseline_reports_what_it_could_not_repair_instead_of_calling_it_done(env):
    """缺产物的历史提交补不上，必须**单独报出来**，并且跑完仍留在桶里。

    这是整层最容易被糊过去的地方：`reappraised=1` 这一个数看着像"全处理完了"，
    但实际上有 1 份永远停在旧标准上。所以三种状态各占一格，不许合并。
    """
    _submit(env, _payload(submission_id="s-ok"))
    broken = _payload(submission_id="s-noart")
    broken["artifacts"] = None
    _submit(env, broken)

    c = env["conn"]()
    try:
        _strip_fingerprint(c, "s-ok")
        _strip_fingerprint(c, "s-noart")
        task = dr.load_task(TASK_PATH)
        report = bl.run_baseline(c, [task], reason=REASON, created_at=AT)

        counted = report.counted()
        assert counted["scanned"] == 2
        assert counted["reappraised"] == 1
        assert counted["missing_artifacts"] == 1
        assert counted["still_unfingerprinted"] == 1, "补不上的必须还在桶里，能被下次看见"

        blocked = report.blocked()
        assert [out.submission_id for out in blocked] == ["s-noart"]
        assert blocked[0].state == "missing_artifacts"
        assert blocked[0].user_id == USER, "补不上的也要能指名到人"
        assert report.after[task["task_id"]].unfingerprinted == ("s-noart",)
    finally:
        c.close()


def test_blocked_rows_stay_in_scope_forever_and_are_retried_but_never_rewritten(env):
    """补不上的提交：每次跑都会被重新尝试并再报一次，但**永不落新修订**。

    这是本轮验收脚本抓出来的真相，也是这一层最容易"优化"掉的东西：
    看着"第二次还有活干"很别扭，于是有人会把它从范围里剔掉——报告立刻变漂亮，
    而那批人从此再也不会被提起。所以这里锁两件事：

      - 第二次跑**仍然**报出它（`still_unfingerprinted` 不为 0，且状态是 `missing_*`）；
      - 但重评链长度不变（没有为了"看起来干完了"而写一条什么都没改的修订）。
    """
    _submit(env, _payload(submission_id="s-ok"))
    broken = _payload(submission_id="s-noart")
    broken["artifacts"] = None
    _submit(env, broken)

    c = env["conn"]()
    try:
        _strip_fingerprint(c, "s-ok")
        _strip_fingerprint(c, "s-noart")
        task = dr.load_task(TASK_PATH)
        bl.run_baseline(c, [task], reason=REASON, created_at=AT)

        second = bl.run_baseline(c, [task], reason=REASON, created_at=AT)
        assert [out.submission_id for out in second.outcomes] == ["s-noart"]
        assert second.outcomes[0].state == "missing_artifacts"
        assert second.counted()["reappraised"] == 0, "重复改分：第二次又写了一条修订"
        assert second.counted()["still_unfingerprinted"] == 1, "把它从报告里抹掉了"
        assert len(store.list_reappraisals(c, "s-noart")) == 0
        assert len(store.list_reappraisals(c, "s-ok")) == 1
    finally:
        c.close()


# ── 3. 演练不落库 ───────────────────────────────────────────────────────────

def test_dry_run_writes_nothing_and_shows_it_by_leaving_the_scope_unchanged(env):
    """演练之后范围与之前**逐字段相同**——这正是"确实没写"的证据。

    如果演练也改了什么，那个区别就再也分不出来了；所以这里直接比 `to_dict()`。
    """
    _submit(env, _payload(submission_id="s-legacy"))
    c = env["conn"]()
    try:
        _strip_fingerprint(c, "s-legacy")
        task = dr.load_task(TASK_PATH)
        before = bl.all_task_scopes(c, [task])

        report = bl.run_baseline(c, [task], reason=REASON, apply=False, created_at=AT)
        after = bl.all_task_scopes(c, [task])

        assert {tid: s.to_dict() for tid, s in before.items()} == \
               {tid: s.to_dict() for tid, s in after.items()}
        assert [out.state for out in report.outcomes] == ["dry_run"]
        assert report.counted()["still_unfingerprinted"] == 1
        assert store.list_reappraisals(c, "s-legacy") == []
        assert store.effective_ratio(c, "s-legacy") == 1.0
    finally:
        c.close()


def test_dry_run_and_the_real_run_report_the_same_numbers(env, tmp_path):
    """同一份输入：演练报的数字必须等于真跑报的数字（差一个落不落库）。

    演练存在的意义就是"先看一眼会怎样"。两个数不一样，演练就变成了安慰剂。
    """
    db2 = tmp_path / "dry.db"
    registry = api.TaskRegistry()
    api.bootstrap(db2, registry)

    def conn2():
        return store.connect(db2)

    _submit(env, _payload(submission_id="s-legacy"))
    api.submit(_payload(submission_id="s-legacy"), registry=registry, conn_factory=conn2)

    c, d = env["conn"](), conn2()
    try:
        _strip_fingerprint(c, "s-legacy")
        _strip_fingerprint(d, "s-legacy")
        task = dr.load_task(TASK_PATH)
        dry = bl.run_baseline(c, [task], reason=REASON, apply=False, created_at=AT)
        real = bl.run_baseline(d, [task], reason=REASON, apply=True, created_at=AT)

        assert [(o.submission_id, o.old_ratio, o.new_ratio, o.delta) for o in dry.outcomes] == \
               [(o.submission_id, o.old_ratio, o.new_ratio, o.delta) for o in real.outcomes]
        assert dry.counted()["scanned"] == real.counted()["scanned"]
        assert dry.counted()["score_changed"] == real.counted()["score_changed"]
    finally:
        c.close()
        d.close()


# ── 4. 校验与自洽 ───────────────────────────────────────────────────────────

def test_duplicate_tasks_are_rejected(env):
    """同一个 task 传两次要抛，不许悄悄去重。

    去重会让"两个不同版本被当成同一个"变成"用了先出现的那个"，事后无从发现；
    而"只是想跑两遍"本来该被幂等性挡住，不该靠调用方自觉。
    """
    task = dr.load_task(TASK_PATH)
    c = env["conn"]()
    try:
        with pytest.raises(bl.BaselineError) as err:
            bl.run_baseline(c, [task, task], reason=REASON, created_at=AT)
        assert task["task_id"] in str(err.value)
    finally:
        c.close()


def test_baseline_requires_a_reason_before_doing_any_work(env):
    """没理由的整批重算＝偷偷改分。而且要在**动手之前**就挡住。

    如果只在每份提交里验，范围为空时坏参数会静默通过——那时人以为"跑过了"。
    """
    _submit(env, _payload(submission_id="s-legacy"))
    c = env["conn"]()
    try:
        _strip_fingerprint(c, "s-legacy")
        with pytest.raises(bl.BaselineError):
            bl.run_baseline(c, [dr.load_task(TASK_PATH)], reason="   ", created_at=AT)
        assert store.list_reappraisals(c, "s-legacy") == []
    finally:
        c.close()


def test_counted_agrees_with_the_detail_rows(env):
    """摘要与明细不许各说一套：`scanned` 必须等于逐条结果数。

    这类"两处口径"的漂移不会报错，只会让人在汇报时说出一个对不上的数字。
    """
    _submit(env, _payload(submission_id="s-1"))
    _submit(env, _payload(submission_id="s-2"))
    c = env["conn"]()
    try:
        _strip_fingerprint(c, "s-1")
        _strip_fingerprint(c, "s-2")
        task = dr.load_task(TASK_PATH)
        report = bl.run_baseline(c, [task], reason=REASON, created_at=AT)
        counted = report.counted()

        assert counted["scanned"] == len(report.outcomes) == 2
        assert counted["reappraised"] + counted["missing_trace"] + \
               counted["missing_artifacts"] == counted["scanned"]
        assert counted["current"] == 2 and counted["still_stale"] == 0
    finally:
        c.close()


# ── 5. 要能指名到人 ─────────────────────────────────────────────────────────

def test_baseline_names_who_lost_points(env):
    """分数变了的必须带 `user_id` 与 `delta`——这是要通知的名单。

    只有 `submission_id` 的话，调用方还得自己回查；而那一步没人会做，
    结果就是基线跑完了、报告很漂亮、没有一个人知道自己被重算过。
    """
    _submit(env, _payload(submission_id="s-legacy"))
    c = env["conn"]()
    try:
        _strip_fingerprint(c, "s-legacy")
        report = bl.run_baseline(c, [_changed_task()], reason=REASON, created_at=AT)

        changed = report.changed()
        assert [o.submission_id for o in changed] == ["s-legacy"]
        assert changed[0].user_id == USER
        assert changed[0].delta == pytest.approx(-0.5)
        assert changed[0].needs_manual_review is True
        assert report.counted()["score_changed"] == 1
        # 分数变化真的传到了 P：这是"改了档案"与"改了成绩"的分界。
        assert ms.build_series(c, USER, SKILL).p == 0.0
    finally:
        c.close()


# ── 4. 「补不上」那批的分档（B-04g 的取数层）────────────────────────────────
# 前三条性质都只说"补不上的是哪些"。但这一批里混着两种完全不同的处境：
# 当年判过给过分的（现在判不了 ⇒ 必须交代），和当年本来就没分的（没什么可解释）。
# 拿一个总数去设计政策，等于为一个还没被数过的规模拍板。

def _seed_lost_artifacts(env, fixture: str, submission_id: str, *, user_id: str = USER) -> None:
    """当年的作答**存不下产物**：报告照常判（可能有分），`artifacts` 列却是空的。

    这就是历史的真实形态——`artifacts` 列 v4 才加，之前的提交根本没有这一格，
    而它们的报告是**看着产物**判出来的，所以可以带着 1.0 分。
    """
    _submit(env, _payload(submission_id=submission_id, user_id=user_id, fixture=fixture))
    c = env["conn"]()
    try:
        c.execute("UPDATE submissions SET artifacts = NULL WHERE submission_id = ?",
                  (submission_id,))
        c.commit()
    finally:
        c.close()


def _seed_no_artifacts(env, fixture: str, submission_id: str, *, user_id: str = USER) -> None:
    """当年**学生就没交产物**：判的时候就没有，于是门禁不过、分数 0。

    与上一个的函数分开是有意的：两条路的分数档位完全不同（这条基本只能落在 0 分），
    混成一个 helper 之后，"需要交代的那一档"会永远是 0 人——
    而那个结论是被测试的构造方式制造出来的，不是库里的真实情况。
    """
    payload = _payload(submission_id=submission_id, user_id=user_id, fixture=fixture)
    payload["artifacts"] = None
    _submit(env, payload)


def test_only_the_people_who_had_a_score_need_an_explanation(env):
    """T1 单独一档，且**只有它**进交代名单。

    `raw-perfect` 当年 1.0 分（判过、给过分，只是产物没存下来）；另两份都是 0 分。
    把后两者也塞进交代名单，等于让"本来就没分"的人替我们的数据缺失背一次解释。
    """
    _seed_lost_artifacts(env, "raw-perfect", "s-scored")
    _seed_no_artifacts(env, "raw-no-file", "s-zero-ok")
    _seed_no_artifacts(env, "raw-empty", "s-zero-none")
    _submit(env, _payload(submission_id="s-fine"))      # 有产物 ⇒ 不在这批里

    c = env["conn"]()
    try:
        split = bl.tier_split(c)
        assert split.counted() == {"total": 4, "blocked": 3, "T1": 1, "T2": 1, "T3": 1}
        assert [i.submission_id for i in split.needs_explanation()] == ["s-scored"]
        assert split.needs_explanation()[0].ratio == 1.0
        assert split.users("T1") == (USER,)
    finally:
        c.close()


def test_zero_score_with_evidence_and_zero_score_without_are_different_buckets(env):
    """都是 0 分，但一个有过程证据、一个没有 —— 必须分开放。

    `raw-no-file`：门禁没过（没交文件）但 credit 层有通过项 ⇒ 分数 0，技能证据照样进了 P。
    这批人正是 B-07b（门禁口径 A/B/C）在吵的那批；`raw-empty` 什么都没留下 ⇒ 无从解释。
    合成一档的后果：要么去跟 T2 解释一件不存在的事，要么把 T3 漏掉——
    而 T3 的分数是 0、掌握度却被抬过，漏掉就等于"改标准时没人看这条线"。
    """
    _seed_no_artifacts(env, "raw-no-file", "s-zero-ok")
    _seed_no_artifacts(env, "raw-empty", "s-zero-none")

    c = env["conn"]()
    try:
        split = bl.tier_split(c)
        t3 = split.tier("T3")
        t2 = split.tier("T2")
        assert [i.submission_id for i in t3] == ["s-zero-ok"]
        assert t3[0].ratio == 0.0
        assert t3[0].positive_skills == ("failure_retry", "tool_param_validation"), \
            "T3 的判据是「某技能 passed_weight > 0」，这里锁住它确实读到了证据"
        assert [i.submission_id for i in t2] == ["s-zero-none"]
        assert t2[0].positive_skills == ()
    finally:
        c.close()


def test_tier_split_reads_the_effective_report_not_the_frozen_one(env):
    """分档看的必须是**生效报告**（学生现在看到的那个分）。

    读 `submissions.assert_score` 或落库时那份 `diagnosis` 也能跑通、也不会报错，
    但报出来的数会和接口上的分不一样——同一件事两套口径，对账时没人说得清哪个是真的。
    """
    _seed_no_artifacts(env, "raw-empty", "s-x")
    c = env["conn"]()
    try:
        assert bl.tier_split(c).blocked[0].tier == "T2"

        report = copy.deepcopy(store.load_report(c, "s-x"))
        report["score"]["ratio"] = 0.4
        store.save_reappraisal(c, "s-x", report, reason="合成的重评记录（只为验证取数口径）",
                               created_at=AT)

        item = bl.tier_split(c).blocked[0]
        assert item.ratio == 0.4, "读的是原始分 ⇒ 与接口给的数不一致"
        assert item.tier == "T1"
    finally:
        c.close()


def test_corrupt_history_is_loud_instead_of_looking_like_zero_points(env):
    """报告读不出来 ⇒ 抛，且指名是哪一条。**不许当成"当年 0 分"归进 T2。**

    这是本层最容易犯的静默降级：坏 JSON 解不出来，顺手 `or {}` 一下，
    这份提交就从"可能给过分、需要交代"掉进"本来就没分、不用管"。
    两档处置正好相反，而报告会显得一切正常。
    """
    _seed_lost_artifacts(env, "raw-perfect", "s-bad")
    c = env["conn"]()
    try:
        c.execute("UPDATE submissions SET diagnosis = ? WHERE submission_id = ?",
                  ("{不是 JSON", "s-bad"))
        c.commit()
        with pytest.raises(store.StoreError) as err:
            bl.tier_split(c)
        assert "s-bad" in str(err.value), "报错要指名是哪一条，否则等于没报"

        c.execute("UPDATE submissions SET diagnosis = ? WHERE submission_id = ?",
                  (json.dumps({"report_version": "v2"}, ensure_ascii=False), "s-bad"))
        c.commit()
        with pytest.raises(store.StoreError) as err2:
            bl.tier_split(c)
        assert "score.ratio" in str(err2.value), "缺分数不许默认成 0"
    finally:
        c.close()


def test_tier_split_is_read_only_and_its_total_covers_the_whole_library(env):
    """两件事一起锁：

    1. **只读**：分档是给人做决定用的，它自己要是一个会改库的动作，
       "先看一眼再决定"就变成了"已经动过手了"。
    2. **`total` 是全库提交数**，不是"补不上的那些"。用 `len(blocked)` 顶替，
       报出来的占比会永远接近 100%，人会以为自己面对一个巨大的历史包袱。
    """
    _seed_lost_artifacts(env, "raw-perfect", "s-a")
    _submit(env, _payload(submission_id="s-b"))
    _submit(env, _payload(submission_id="s-c"))

    c = env["conn"]()
    try:
        before = (store.count_submissions(c), len(store.list_reappraisals(c, "s-a")))
        first = bl.tier_split(c)
        second = bl.tier_split(c)

        assert first.to_dict() == second.to_dict(), "两次结果必须逐字段相同"
        assert first.counted()["total"] == 3, "total 必须是全库，不是补不上的那些"
        assert first.counted()["blocked"] == 1
        assert (store.count_submissions(c), len(store.list_reappraisals(c, "s-a"))) == before
    finally:
        c.close()
