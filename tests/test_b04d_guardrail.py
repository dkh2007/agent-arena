"""B-04d「甲 + 护栏」的判据锁（v4，2026-11-16 人拍板）。

人拍板的口径不是裸甲，而是四句话：

1. **临时成绩照出**——争议提交照算 P，学习者看得到自己的分（决策 7 的反面就是乙）；
2. **复习照排**——争议不能让"下次什么时候练"变成空白；
3. **争议答卷打「标准待复核」标记**——这份成绩的判据表述可能有歧义，是系统侧的账；
4. **正式档案/排名默认不含它**——含糊成绩可以展示，但不能无声地进对外档案。

本文件锁的是 3 与 4，以及"标记不会自己烂掉"（裁决后必须自动消失）。

为什么 1、2 也在这里各留一条：甲/乙之争的真实分歧点就是这两句。只锁"标记"不锁
"照算"，等于允许有人日后把实现滑回乙——那时标记测试仍然全绿，而学生已经被冻住了。

判据的可判定性：每条测试都给出一个**不满足时会红**的断言，不写"看起来对"。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import llm_judge as lj  # noqa: E402
from arena import mastery as ms  # noqa: E402
from arena import mastery_snapshot as snap  # noqa: E402
from arena import scheduler as sch  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
AT = "2026-11-16T09:00:00Z"
CLEAN = "u-clean"      # 交了一份正常提交，从不进队列
DISPUTED = "u-disputed"  # 交了一份 judge 两次给不同档的提交


# ── 夹具 ─────────────────────────────────────────────────────────────────────

def _fixture(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def _payload(*, user_id: str, submission_id: str, fixture: str = "raw-perfect",
             task_version: str = "2026-11-13.1") -> dict:
    raw = _fixture(fixture)
    sub = raw["submission"]
    return {
        "task_id": sub["task_id"],
        "task_version": task_version,
        "user_id": user_id,
        "agent_id": sub["agent_id"],
        "submission_id": submission_id,
        "task_git_hash": sub["task_git_hash"],
        "evaluated_at": AT,
        "invoke": sub.get("invoke"),
        "trace": raw.get("trace"),
        "artifacts": raw.get("artifacts"),
    }


def _verdict(rating: int) -> lj.JudgeVerdict:
    return lj.JudgeVerdict(rating=rating, patterns=())


# 两次给不同档 ⇒ 一致率低于阈值。判据在 `llm_judge.DoubleJudgeResult.needs_arbitration`。
# **必须是工厂不是常量**：StubJudge 的预设结果是 `pop(0)` 消耗的，
# 模块级共享会被上一个测试吃掉，后面的测试拿到的是"结果已用尽"或错档的判决。
def DISAGREE() -> lj.StubJudge:
    return lj.StubJudge([_verdict(4), _verdict(1)])


def AGREE() -> lj.StubJudge:
    return lj.StubJudge([_verdict(3), _verdict(3)])


@pytest.fixture()
def env(tmp_path):
    db = tmp_path / "arena.db"
    registry = api.TaskRegistry()
    api.bootstrap(db, registry)

    def conn():
        return store.connect(db)

    return {"db": db, "registry": registry, "conn": conn}


def _submit(env, payload, **kw) -> dict:
    return api.submit(payload, registry=env["registry"], conn_factory=env["conn"], **kw)


# ── 1. 标记的判定：靠证据，不靠"这人有争议提交" ─────────────────────────────

def test_disputed_submission_marks_only_the_skills_it_actually_fed(env):
    """争议提交只会把**它真给了计入证据**的技能标成待复核。

    `raw-perfect` 五条断言全过，压到三个技能，所以三个都该被标。
    反面对照在下一个测试里：一个没吃到证据的技能不许被顺手标上。
    """
    _submit(env, _payload(user_id=DISPUTED, submission_id="s-disputed"),
            judge=DISAGREE(), arbitration_threshold=0.8)

    c = env["conn"]()
    try:
        pairs = snap.provisional_pairs(c)
        skills = set(store.list_skill_ids(c))
        assert {s for u, s in pairs if u == DISPUTED} == skills
        assert all(u == DISPUTED for u, _ in pairs)
        for skill_id in skills:
            assert snap.is_provisional(c, DISPUTED, skill_id) is True
    finally:
        c.close()


def test_a_skill_with_no_evidence_from_the_disputed_submission_is_not_marked(env, tmp_path):
    """没吃到证据的技能不许被标——否则"待复核"会扩散成"这个人整体可疑"。

    构造：用 `raw-no-file`（没交产物）。它的 credit 断言一条都没通过、`counts` 仍为真，
    所以三个技能其实都会被计入（c=0 也是一次观测）。真正"不计入"的路径是作废，
    那一条单独测（下面 `test_voided_dispute_is_not_marked`）。
    这里锁的是另一半：**新用户、新技能没有任何提交**时，`provisional_pairs` 里不许有他。
    """
    _submit(env, _payload(user_id=DISPUTED, submission_id="s-disputed"),
            judge=DISAGREE(), arbitration_threshold=0.8)

    c = env["conn"]()
    try:
        assert snap.provisional_pairs(c, user_id="nobody-ever-submitted") == set()
        assert snap.is_provisional(c, "nobody-ever-submitted", "multi_step_planning") is False
    finally:
        c.close()


def test_voided_dispute_is_not_marked(env):
    """作废提交整份不计入 P（B-07d ① 的甲）⇒ 它的争议也不该把成绩标成待复核。

    两者若是同一个判据，会出现"成绩里根本没有这份提交，却挂着它的争议"——
    那时人工去看队列会发现无从下手的谜题。
    """
    _submit(env, _payload(user_id=DISPUTED, submission_id="s-voided", task_version="2020-01-01.0"),
            judge=DISAGREE(), arbitration_threshold=0.8)

    c = env["conn"]()
    try:
        # 队列里确实有它（judge 确实不同意），但成绩里没有它。
        assert [r["submission_id"] for r in store.list_arbitration(c)] == ["s-voided"]
        assert snap.provisional_pairs(c) == set()
    finally:
        c.close()


def test_agreed_submission_is_not_marked(env):
    """反面对照：一致率够的提交不许被标。没有这一条，"全都标"也能过上面的测试。"""
    _submit(env, _payload(user_id=CLEAN, submission_id="s-agree"),
            judge=AGREE(), arbitration_threshold=0.8)

    c = env["conn"]()
    try:
        assert store.list_arbitration(c, status=None) == []
        assert snap.provisional_pairs(c) == set()
    finally:
        c.close()


# ── 2. 护栏：正式榜默认不含它 ───────────────────────────────────────────────

def _two_users(env):
    """一个干净用户 + 一个争议用户，都交同一份满分夹具 ⇒ P 相同，但档案资格不同。"""
    _submit(env, _payload(user_id=CLEAN, submission_id="s-clean"))
    _submit(env, _payload(user_id=DISPUTED, submission_id="s-disputed"),
            judge=DISAGREE(), arbitration_threshold=0.8)


def test_ranked_default_excludes_provisional_rows(env):
    """默认榜里只有干净用户。

    这是护栏的核心：**方向不能反**。如果默认含、要显式排掉，任何新写的读榜接口
    在"忘了传参数"时都会把含糊成绩放出去。
    """
    _two_users(env)
    c = env["conn"]()
    try:
        for skill_id in store.list_skill_ids(c):
            rows = snap.list_ranked(c, skill_id)
            assert [r["user_id"] for r in rows] == [CLEAN]
            assert "provisional" not in rows[0]
    finally:
        c.close()


def test_ranked_can_include_provisional_with_an_explicit_flag(env):
    """显式打开时两人都在，且争议那条带标记 —— 「进档案但带争议标记」那条路要能走。"""
    _two_users(env)
    c = env["conn"]()
    try:
        rows = snap.list_ranked(c, "multi_step_planning", include_provisional=True)
        by_user = {r["user_id"]: r for r in rows}
        assert set(by_user) == {CLEAN, DISPUTED}
        assert by_user[CLEAN]["provisional"] is False
        assert by_user[DISPUTED]["provisional"] is True
        # 两人的 P 相同（同一份夹具）——差异只在"能不能进正式档案"，不在分数。
        assert by_user[CLEAN]["mastery_p"] == by_user[DISPUTED]["mastery_p"]
    finally:
        c.close()


def test_ranked_rejects_non_bool_flag(env):
    """`include_provisional=None/"yes"` 必须报错。

    Python 里 `None` 是假值，会让 `if include_provisional:` 静默按 False 走——
    调用方以为自己开了，实际没开（或者反过来）。护栏参数不允许"看起来能用"。
    """
    _two_users(env)
    c = env["conn"]()
    try:
        for bad in (None, "yes", 1, 0):
            if isinstance(bad, bool):
                continue
            with pytest.raises(snap.SnapshotError, match="布尔"):
                snap.list_ranked(c, "multi_step_planning", include_provisional=bad)
    finally:
        c.close()


# ── 3. 标记不会自己烂掉：裁决后自动消失 ─────────────────────────────────────

def test_resolving_the_queue_clears_the_marker(env):
    """裁决之后不需要任何额外操作，标记就该消失。

    标记是**从队列状态派生的**，不是另存一份在快照里。存一份就得在每个改状态的
    地方记得刷新，漏一处就会永久留下一条"标准待复核"——而那时队列里什么都没有，
    谁也不知道该复核什么。
    """
    _two_users(env)
    c = env["conn"]()
    try:
        assert snap.is_provisional(c, DISPUTED, "multi_step_planning") is True
        store.resolve_arbitration(c, "s-disputed", note="判据第 3 条表述已改，本份按新判据重评")
        assert snap.is_provisional(c, DISPUTED, "multi_step_planning") is False
        rows = snap.list_ranked(c, "multi_step_planning")
        assert [r["user_id"] for r in rows] == [CLEAN, DISPUTED]
    finally:
        c.close()


def test_dismissed_queue_also_clears_the_marker(env):
    """`dismissed`（人工看过后认为判据没问题）同样清标记——两种收尾都算"有结论"。"""
    _two_users(env)
    c = env["conn"]()
    try:
        store.resolve_arbitration(c, "s-disputed", status="dismissed", note="判据边界清楚，是模型抖动")
        assert snap.is_provisional(c, DISPUTED, "multi_step_planning") is False
    finally:
        c.close()


# ── 4. 甲那一半：成绩照算、复习照排（不许滑回乙）────────────────────────────

def test_disputed_submission_does_not_lift_p_but_still_schedules(env):
    """待裁决的争议提交**不推动 P**，但**照常排复习**（round-111 / B-26 反转本条）。

    反转历史（照抄本仓规矩：反转而不是删除，理由留在原处）：
    本条原先锁的是「争议提交照算 P、照排复习」，判据是"判据歧义不是学生的问题，
    记账成本由平台承担"。2026-11-21 人拍板改口径——**仲裁没出结果的证据先不进掌握度**：
    可信度还没定的报告不该拿去改状态。因为裁决完会自动恢复（重放是纯函数），
    这是**延迟**而不是**丢失**，所以与原判据"不惩罚学生"并不冲突。

    反转后仍保留两个判据，第二个特意留着防止"顺手一起改掉"：
      ① `judged_p is None`——争议期间 P 不被这份证据推动；
      ② 复习卡与复习项**与不带 judge 时逐字段相等**——排程不因争议而停。
         少排一次复习才是真惩罚（学生什么也没做错，却少了一次练习机会）。
    """
    db = env["db"]
    registry = env["registry"]

    def run(tag: str) -> tuple[float | None, list[dict]]:
        d = db.parent / f"{tag}.db"
        reg = api.TaskRegistry()
        api.bootstrap(d, reg)
        kw = ({"judge": DISAGREE(), "arbitration_threshold": 0.8} if tag == "judged" else {})
        api.submit(_payload(user_id=DISPUTED, submission_id="s-same"),
                   registry=reg, conn_factory=lambda: store.connect(d), **kw)
        c = store.connect(d)
        try:
            series = ms.build_series(c, DISPUTED, "multi_step_planning")
            cards = store.load_fsrs_cards(c, DISPUTED)
            items = store.list_review_items(c, DISPUTED, status=None)
            return series.p, [{"cards": {k: v for k, v in sorted(cards.items())},
                               "items": items}]
        finally:
            c.close()

    plain_p, plain_rest = run("plain")
    judged_p, judged_rest = run("judged")

    assert plain_p is not None, "不带 judge 时必须正常算出 P，否则下面的对照没有意义"
    assert judged_p is None, "待裁决的证据不许推动 P（B-26，2026-11-21 人拍板）"
    assert judged_rest == plain_rest, "复习卡与复习项照常生成：争议不导致停排"


# ── 5. 「能回溯」的前提：原始产物真的落库了 ─────────────────────────────────

def test_submission_api_persists_the_original_artifacts(env):
    """落库必须带原始产物——这是「标准改完后批量回溯重算」唯一的输入来源。

    **这条是真的抓过 bug**：v4 加列时 `store.save_submission()` 接了参数，
    但 `submission_api.submit()` 忘了把 `prep.artifacts` 传下去 ⇒ 列永远是 NULL。
    报告、分数、队列、掌握度全都正常，只有"将来想按新判据重评"时才会发现
    **根本没有输入可评**。这种缺陷不会被任何上游断言抓到，只能靠这一条锁。
    """
    _submit(env, _payload(user_id=CLEAN, submission_id="s-artifacts"))
    expected = _fixture("raw-perfect").get("artifacts")

    c = env["conn"]()
    try:
        row = store.load_submission(c, "s-artifacts")
        assert row["artifacts"] == expected
        assert row["artifacts"] is not None
    finally:
        c.close()


def test_missing_artifacts_stay_null_and_are_not_invented(env):
    """没给产物就落 NULL，不写 `{}`。

    `{}` 会被下游当成"交了但内容为空"，从而把"平台没收到"算成"学生交错了东西"。
    决策 7 的同一条理由：缺数据不许伪装成一个取值。
    """
    payload = _payload(user_id=CLEAN, submission_id="s-noart")
    payload["artifacts"] = None
    _submit(env, payload)

    c = env["conn"]()
    try:
        assert store.load_submission(c, "s-noart")["artifacts"] is None
    finally:
        c.close()


def test_a_stored_submission_can_be_replayed_under_a_changed_rubric(env):
    """判据改了之后，能从库里把这份提交按新标准重评，并且差异传得到 P。

    三步都必须在**库里**完成（只读 trace + artifacts，不碰磁盘夹具）：
      ① 用改写过的 rubric 重跑断言执行器 ⇒ ratio 变化；
      ② 用新报告重放 ⇒ P 变化；
      ③ 变化量能被人工复核阈值筛出来。

    任何一步做不到，「批量回溯重算」就是空话——所以这条用"必须变"来判，
    不用"没报错"来判。
    """
    import copy

    from arena import diagnosis_report as dr
    from arena import trace_pipeline as tp

    _submit(env, _payload(user_id=CLEAN, submission_id="s-replay"))

    c = env["conn"]()
    try:
        row = store.load_submission(c, "s-replay")
        old_report = row["diagnosis"]
        old_p = ms.build_series(c, CLEAN, "multi_step_planning").p

        task = dr.load_task(TASK_PATH)
        changed = copy.deepcopy(task)
        for spec in changed["rubric_credit"]:
            if spec["id"] == "R2_report_correct":
                spec["expected"]["total_amount"] += 1.0   # 模拟「标准答案改了」

        raw = tp.assemble_raw(old_report["submission"], changed,
                              trace=row["trace"], artifacts=row["artifacts"])
        raw = tp.recompute_all(raw, task=changed)
        new_report = dr.build_report(changed, raw)

        assert new_report["score"]["ratio"] < old_report["score"]["ratio"], \
            "改了标准答案却没影响分数 ⇒ 重评链路没通"

        evidence = [ms.skill_evidence(new_report, "multi_step_planning")]
        new_p = ms.replay(evidence, user_id=CLEAN, skill_id="multi_step_planning").p

        assert old_p is not None and new_p is not None
        assert new_p < old_p, "差异没有传到 P ⇒ 重评只改了报告，没改成绩"
        assert abs(new_p - old_p) >= 0.02, "差异小于人工复核阈值，这个用例证明不了筛查机制"
    finally:
        c.close()


# ── 6. 一笔原子写（B-07d ③ / 外审 T6，round-93）─────────────────────────────

def test_queue_failure_rolls_back_the_submission_and_the_snapshot(env, monkeypatch):
    """入队失败 ⇒ 提交、快照、队列**一律不留**。

    以前三步各写各的，"提交落了、入队炸了"会留下：报告层看得见这份提交、
    成绩层按它算过了 P、但待办里没有它——于是这份争议**永远不会被人工看见**，
    而所有上游断言都是绿的（它们各自只查自己那一层）。

    判据必须是"异常之后查询结果为 0"，不是"抛了异常"：假原子（外层再套
    `with conn:`）同样会抛异常，但库里会留下那条提交。
    """
    from arena import judge_pipeline as jp

    def boom(*args, **kwargs):
        raise store.StoreError("队列写坏了")

    monkeypatch.setattr(jp, "record_arbitration", boom)
    with pytest.raises(store.StoreError, match="队列写坏了"):
        _submit(env, _payload(user_id=DISPUTED, submission_id="s-qfail"),
                judge=DISAGREE(), arbitration_threshold=0.8)

    c = env["conn"]()
    try:
        assert store.count_submissions(c) == 0
        assert store.list_arbitration(c, status=None) == []
        assert c.execute("SELECT COUNT(*) FROM mastery_snapshots").fetchone()[0] == 0
    finally:
        c.close()


def test_a_duplicate_id_still_reports_409_and_leaves_the_first_untouched(env):
    """把 StoreError 就地转成 409 之后，重复 id 的对外表现不许跟着变。

    改这一处最容易顺手改坏：`submit()` 原来是外层 `except store.StoreError`
    统一转 409，现在必须在 `save_submission()` 周围就地转——否则入队失败
    （也是 StoreError）会被报成 409 "你的提交有问题"，让调用方去改一个
    本来没问题的请求体。这条锁的是"转换之后，重复 id 仍然 409、首份仍完好"。
    """
    from arena.submission_api import SubmissionError

    _submit(env, _payload(user_id=CLEAN, submission_id="s-dup"))
    with pytest.raises(SubmissionError) as ei:
        _submit(env, _payload(user_id=CLEAN, submission_id="s-dup"))
    assert ei.value.status == 409
    assert ei.value.code == "store_rejected"

    c = env["conn"]()
    try:
        assert store.count_submissions(c) == 1
        # 首份仍是第一次那份：重复提交不许改掉已有记录的任何字段。
        assert store.load_submission(c, "s-dup")["assert_score"] == 1.0
    finally:
        c.close()
