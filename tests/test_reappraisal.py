"""B-04d 接线：重评链的判据锁（v5，round-92）。

人拍板的第三句是「标准改完后批量回溯重算」。上一轮证明了三块拼图（产物能存、
报告能重算、差异能传到 P），但它们是断开的——**裁决完写一句"判据要改"，成绩一分不动**。
本轮把中间那截接上，本文件锁的就是这截接线的五个关键性质：

1. 重评**换结论不换证据**：`submissions` 一行不动、不新增，`diagnosis` 逐字节不变；
2. 一份作答在 P 的重放里**永远只算一个 step**（否则重评会让掌握度凭空翻倍）；
3. 缺产物的提交**不重评**，而不是"用空产物算成 0 分"（决策 7）；
4. 第二次重评从**上一次的结论**起算 delta，不从最原始那个比；
5. 演练与真跑给出**同一组数字**，区别只在落不落库。

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

from arena import diagnosis_report as dr  # noqa: E402
from arena import mastery as ms  # noqa: E402
from arena import reappraisal as rap  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
AT = "2026-11-16T09:00:00Z"
USER = "u-reap"
SKILL = "multi_step_planning"
REASON = "rubric: R2.expected.total_amount 1837.99 -> 1838.99"


def _fixture(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def _payload(*, user_id: str = USER, submission_id: str = "s-1",
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


def _changed_task() -> dict:
    """模拟"人把标准答案改了"：R2 期望的总额 +1。

    选这一条是因为 `raw-perfect` 恰好把它算对（1837.99）——标准一改，这份满分作答
    就该掉分，那正是重评要看见的东西。
    """
    task = copy.deepcopy(dr.load_task(TASK_PATH))
    hit = False
    for spec in task["rubric_credit"]:
        if spec["id"] == "R2_report_correct":
            spec["expected"]["total_amount"] += 1.0
            hit = True
    assert hit, "夹具变了：R2_report_correct 不在 rubric 里，本测试失去意义"
    return task


# ── 1. 换结论不换证据 ────────────────────────────────────────────────────────

def test_reappraisal_changes_the_score_but_not_the_evidence(env):
    """重评后分数与 P 都变了，而 `submissions` 与 `reappraisals` 各守本分。

    四处必须同时成立，少一处"重评"就退化成"改了个字段"：
      - 分数确实变了（不是只新存了一份报告）；
      - 原始 `diagnosis` 逐字节相同（历史证据不许被覆盖）；
      - `assert_score` 不动（它是"当时算出的分数"，不是"当前生效的分数"）；
      - `submissions` 行数不变（重评不是新提交）。
    """
    _submit(env, _payload())
    c = env["conn"]()
    try:
        before = store.load_submission(c, "s-1")
        before_p = ms.build_series(c, USER, SKILL).p
        assert before["assert_score"] == 1.0 and before_p == 1.0

        out = rap.reappraise(c, "s-1", _changed_task(), reason=REASON, created_at=AT)

        assert out.state == "reappraised"
        assert out.old_ratio == 1.0 and out.new_ratio == 0.5
        assert out.delta == pytest.approx(-0.5)
        assert out.needs_manual_review is True
        assert out.revision == 1

        after = store.load_submission(c, "s-1")
        assert after["diagnosis"] == before["diagnosis"]      # 原始报告逐字段不变
        assert after["assert_score"] == 1.0                   # 历史分数不动
        assert store.count_submissions(c) == 1                # 没有新增行
        assert store.effective_ratio(c, "s-1") == 0.5         # 生效分数变了

        after_p = ms.build_series(c, USER, SKILL).p
        assert after_p == 0.0, "差异没传到 P ⇒ 重评只改了报告，没改成绩"
    finally:
        c.close()


def test_one_submission_still_contributes_exactly_one_step(env):
    """重评后 P 的序列**步数不变**——一份作答永远只算一次练习。

    这条防的是最隐蔽的一种实现错法：重评时插一条新 `submissions` 行。
    那样报告、分数、队列全对，只有一件事错了——这个人的掌握度会因为
    "系统改了一次判据"而凭空变化（练一次变成练两次）。
    """
    _submit(env, _payload())
    c = env["conn"]()
    try:
        before = ms.build_series(c, USER, SKILL)
        rap.reappraise(c, "s-1", _changed_task(), reason=REASON, created_at=AT)
        after = ms.build_series(c, USER, SKILL)

        assert len(before.steps) == len(after.steps) == 1
        assert before.steps[0].c == 1.0
        assert after.steps[0].submission_id == "s-1"
        # credit 层从 1.0 掉到 0.0：R2 改错后依赖它的后续断言跟着折叠。
        # 这个数若还是 1.0，说明重放读的是落库时那份报告（重评白做了）。
        assert after.steps[0].c == 0.0, "步数对了但用的是旧报告 ⇒ 重放没读生效版"
    finally:
        c.close()


# ── 2. delta 的起点 ─────────────────────────────────────────────────────────

def test_second_reappraisal_starts_from_the_previous_conclusion(env):
    """第二次重评的 `old_ratio` 是第一次的结论，不是最初那份。

    从最原始那个数起算会让 delta 永远显示"自始至终改了多少"，
    而人真正要知道的是"**这一次**改了多少"——两者在第二次之后就不再相等。
    """
    _submit(env, _payload())
    c = env["conn"]()
    try:
        first = rap.reappraise(c, "s-1", _changed_task(), reason=REASON, created_at=AT)
        assert first.revision == 1 and first.new_ratio == 0.5

        # 第二次改成"把标准答案改回来一半"，得到 0.75 之类与上次不同的结论。
        task = copy.deepcopy(dr.load_task(TASK_PATH))
        for spec in task["rubric_credit"]:
            if spec["id"] == "R2_report_correct":
                spec["expected"]["total_amount"] += 0.5
        second = rap.reappraise(c, "s-1", task, reason="rubric: 折中值", created_at=AT)

        assert second.revision == 2
        assert second.old_ratio == first.new_ratio, "delta 又拿原始分数当起点了"
        assert second.delta == pytest.approx(second.new_ratio - first.new_ratio)
        assert len(store.list_reappraisals(c, "s-1")) == 2
        assert store.effective_ratio(c, "s-1") == second.new_ratio
    finally:
        c.close()


# ── 3. 演练与真跑同数 ───────────────────────────────────────────────────────

def test_dry_run_gives_the_same_numbers_and_writes_nothing(env):
    """演练必须与真跑逐项相同，且一行不写。

    演练存在的理由是"改判据之前先看会影响多少人"。它一旦与真跑不一致，
    它给出的预判就是假的——那比没有演练更糟（人会照着它做决定）。
    """
    _submit(env, _payload())
    c = env["conn"]()
    try:
        dry = rap.reappraise(c, "s-1", _changed_task(), reason=REASON, apply=False)
        assert dry.state == "dry_run"
        assert dry.revision is None
        assert store.list_reappraisals(c, "s-1") == []
        assert store.effective_ratio(c, "s-1") == 1.0     # 库里的结论一点没动

        real = rap.reappraise(c, "s-1", _changed_task(), reason=REASON, created_at=AT)
        assert (dry.old_ratio, dry.new_ratio, dry.delta, dry.needs_manual_review) == \
               (real.old_ratio, real.new_ratio, real.delta, real.needs_manual_review)
    finally:
        c.close()


# ── 4. 缺产物不许顶成 0 分 ──────────────────────────────────────────────────

def test_missing_artifacts_blocks_reappraisal_instead_of_scoring_zero(env):
    """产物没存下来的提交**不重评**，成绩原地不动。

    用 `{}` 顶上去会算出"没交产物"的结论 ⇒ 一份满分作业被判 0。
    而真实原因是"我们当时没存"——把平台的缺陷算成学习者没做（决策 7）。
    """
    payload = _payload()
    payload["artifacts"] = None
    _submit(env, payload)

    c = env["conn"]()
    try:
        # 没交产物 ⇒ 门禁 fail ⇒ 本来就 0 分。这里要证明的是"重评没有把它变成别的数"，
        # 以及"没有拿空产物算出一条凭空出现的结论"。
        before_ratio = store.effective_ratio(c, "s-1")
        before_p = ms.build_series(c, USER, SKILL).p
        assert before_ratio == 0.0

        out = rap.reappraise(c, "s-1", _changed_task(), reason=REASON, created_at=AT)

        assert out.state == "missing_artifacts"
        assert out.new_ratio is None and out.delta is None and out.revision is None
        assert store.list_reappraisals(c, "s-1") == []
        assert store.effective_ratio(c, "s-1") == before_ratio
        assert ms.build_series(c, USER, SKILL).p == before_p
    finally:
        c.close()


# ── 5. 拒绝用错的判据 / 换证据 ───────────────────────────────────────────────

def test_reappraisal_refuses_a_rubric_from_another_task(env):
    """拿别的题的判据重评，会得到一个"有数字、无意义"的结论——必须当场拒绝。

    抛错而不是返回 0 分：0 分看起来像个正常结论，会被一路落库。
    """
    _submit(env, _payload())
    other = copy.deepcopy(_changed_task())
    other["task_id"] = "some-other-task"

    c = env["conn"]()
    try:
        with pytest.raises(rap.ReappraisalError):
            rap.reappraise(c, "s-1", other, reason=REASON)
        assert store.list_reappraisals(c, "s-1") == []
    finally:
        c.close()


def test_reappraisal_requires_a_reason(env):
    """没有理由的重评与"偷偷改分数"没有区别。"""
    _submit(env, _payload())
    c = env["conn"]()
    try:
        with pytest.raises(rap.ReappraisalError):
            rap.reappraise(c, "s-1", _changed_task(), reason="   ")
    finally:
        c.close()


def test_stored_reappraisal_cannot_swap_the_identity_of_the_evidence(env):
    """落库层再挡一次：报告里的 submission_id / task_id 不许被换掉。

    换掉就不是"重评这份作答"，而是"借重评的名义把另一份证据塞进来"。
    """
    _submit(env, _payload())
    c = env["conn"]()
    try:
        report = store.effective_report(c, "s-1")

        def _mutated(**kw) -> dict:
            bad = copy.deepcopy(report)
            bad["submission"].update(kw)
            return bad

        with pytest.raises(store.StoreError):
            store.save_reappraisal(c, "s-1", _mutated(submission_id="s-other"),
                                   reason=REASON, created_at=AT)
        with pytest.raises(store.StoreError):
            store.save_reappraisal(c, "s-1", _mutated(task_id="other-task"),
                                   reason=REASON, created_at=AT)
        assert store.list_reappraisals(c, "s-1") == []
    finally:
        c.close()


# ── 6. 批量入口 ─────────────────────────────────────────────────────────────

def test_batch_reappraisal_reports_the_ones_it_could_not_redo(env):
    """批量回溯必须把"重评不了"的那几份**也返回**，不能只报成功的。

    只报成功的，看数字的人会以为"这批全处理完了"，而实际上有几份因为缺产物
    根本没动——那正是最该被人知道的数字。
    """
    _submit(env, _payload(submission_id="s-ok"))
    broken = _payload(submission_id="s-broken")
    broken["artifacts"] = None
    _submit(env, broken)

    c = env["conn"]()
    try:
        outs = rap.reappraise_user(c, USER, _changed_task(), reason=REASON, created_at=AT)
        states = {o.submission_id: o.state for o in outs}
        assert states == {"s-ok": "reappraised", "s-broken": "missing_artifacts"}
        assert [o.submission_id for o in outs] == ["s-broken", "s-ok"]   # 顺序稳定

        # 能重评的那份真的动了，重评不了的那份一点没动（它本来就 0 分：没产物 ⇒ 门禁 fail）。
        assert store.effective_ratio(c, "s-ok") == 0.5
        assert store.effective_ratio(c, "s-broken") == 0.0
        assert store.list_reappraisals(c, "s-broken") == []
    finally:
        c.close()


def test_batch_reappraisal_only_touches_the_given_task(env):
    """批量按任务筛：判据是按任务写的，别的任务的作答不该被顺手重评。"""
    _submit(env, _payload(submission_id="s-1"))
    c = env["conn"]()
    try:
        other = copy.deepcopy(_changed_task())
        other["task_id"] = "not-this-one"
        assert rap.reappraise_user(c, USER, other, reason=REASON, apply=False) == []
    finally:
        c.close()


# ── 6. 重评落库与刷新同一笔（B-07d ③，round-93）─────────────────────────────

def test_a_failed_refresh_rolls_back_the_reappraisal_record(env, monkeypatch):
    """刷新 P 失败 ⇒ 重评记录也不许留。

    半状态在这里的具体样子：`reappraisals` 里有一条新结论、而 `mastery_snapshots`
    还是旧的。读成绩的人看到旧分，读重评链的人看到新分，**两边都是"权威"**，
    而且没有任何一层会报错——`effective_ratio()` 只是返回它读到的东西。

    这条与 `test_refresh_failure_rolls_back_the_submission_too` 是同一条理由的
    两个落点：凡是"写结论 + 刷派生值"，两者必须同一笔。
    """
    from arena import mastery_snapshot as snap

    _submit(env, _payload())
    c = env["conn"]()
    try:
        assert store.effective_ratio(c, "s-1") == 1.0

        def boom(*args, **kwargs):
            raise RuntimeError("模拟刷新失败")

        monkeypatch.setattr(snap, "refresh_user", boom)
        with pytest.raises(RuntimeError, match="模拟刷新失败"):
            rap.reappraise(c, "s-1", _changed_task(), reason=REASON, created_at=AT)

        assert store.list_reappraisals(c, "s-1") == [], "重评记录留下了 ⇒ 半状态"
        assert store.effective_ratio(c, "s-1") == 1.0

        # 反面对照：恢复之后同一次重评能正常落地（防"永远回滚"也能过上面几条）。
        monkeypatch.undo()
        out = rap.reappraise(c, "s-1", _changed_task(), reason=REASON, created_at=AT)
        assert out.state == "reappraised" and out.revision == 1
        assert store.effective_ratio(c, "s-1") == 0.5
    finally:
        c.close()
