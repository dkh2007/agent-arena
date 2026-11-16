"""B-04b 的判据锁：judge 接进提交管线 + 人工仲裁队列。

分五组，每组盯一个可能悄悄出错的地方：

1. **默认关闭**：不传 judge 的部署，行为与 round-89 逐字相同（报告无新字段、列仍 NULL、
   队列仍空）。接了 judge 也不许把队列变成闸门——分数、断言、掌握度、排期一个字不能变。
2. **可判定**：`judge_agreement < threshold` 的提交进得了队列；≥ 阈值的不进。
   两者都要有，否则「全都入队」和「从不入队」都能过。
3. **阈值不拍脑瓜**：缺阈值不许跑，且**不允许有默认值**——那是 B-04c 的活。
4. **故障不冒充判决**：judge 崩了要 503 且**不落库**，不能写成「一致率 0」再入队。
5. **队列自身**：幂等、必须先有提交、裁决必须写理由、不许改判已裁决的条目。

judge 全部走 `StubJudge`（不花钱、不联网、可复现）。真模型的一致性分布不在这里测，
见 `scripts/judge_agreement_experiment.py` 与 `docs/judge-agreement-distribution.json`。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import judge_pipeline as jp  # noqa: E402
from arena import llm_judge as lj  # noqa: E402
from arena import scheduler as sch  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
AT = "2026-11-16T09:00:00Z"


# ── 夹具 ─────────────────────────────────────────────────────────────────────

def _fixture(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def _payload(name: str = "raw-perfect", submission_id: str = "sub-judge-1", **over) -> dict:
    raw = _fixture(name)
    sub = raw["submission"]
    body = {
        "task_id": sub["task_id"],
        "task_version": "2026-11-13.1",
        "user_id": "u-judge",
        "agent_id": sub["agent_id"],
        "submission_id": submission_id,
        "task_git_hash": sub["task_git_hash"],
        "evaluated_at": AT,
        "invoke": sub.get("invoke"),
        "trace": raw.get("trace"),
        "artifacts": raw.get("artifacts"),
    }
    body.update(over)
    return {k: v for k, v in body.items() if v is not None or k in ("trace", "artifacts")}


@pytest.fixture()
def env(tmp_path):
    """一套真库 + 真注册表。judge 一律由测试注入，不碰网络。"""
    db = tmp_path / "arena.db"
    registry = api.TaskRegistry()
    api.bootstrap(db, registry)

    def conn():
        return store.connect(db)

    return {"db": db, "registry": registry, "conn": conn}


def _submit(env, payload, **kw) -> dict:
    return api.submit(payload, registry=env["registry"], conn_factory=env["conn"], **kw)


def _verdict(rating: int, *patterns: str) -> lj.JudgeVerdict:
    return lj.JudgeVerdict(rating=rating, patterns=tuple(patterns))


def _queue(env, status: str | None = "pending") -> list[dict]:
    c = env["conn"]()
    try:
        return store.list_arbitration(c, status=status)
    finally:
        c.close()


# ── 1. 默认关闭 + 不许碰分数 ─────────────────────────────────────────────────

def test_without_judge_nothing_changes(env):
    """不传 judge ⇒ 报告没有新字段、两个派生列仍 NULL、队列空。

    这条是「默认行为与上一轮逐字相同」的机械保证：给默认值加上双评，
    会让每一个部署在升级后突然开始花钱、且模型掉线时挡住提交。
    """
    report = _submit(env, _payload())
    assert "judge_scores" not in report
    assert "judge_agreement" not in report

    c = env["conn"]()
    try:
        row = store.load_submission(c, "sub-judge-1")
        assert row["judge_scores"] is None
        assert row["judge_agreement"] is None
        assert store.list_arbitration(c, status=None) == []
    finally:
        c.close()


def _report_in_fresh_db(tmp_path, tag: str, *, submission_id: str = "s-same", **kw) -> dict:
    """在独立库里跑一次提交，返回报告。

    两份变体用**同一个 submission_id**：不同的 id 会让报告里的
    `submission.submission_id` 与快照的 `last_submission_id` 天然不同，
    「逐字段相等」就变成在比 id 而不是比口径。
    """
    db = tmp_path / f"{tag}.db"
    registry = api.TaskRegistry()
    api.bootstrap(db, registry)
    return api.submit(_payload(submission_id=submission_id), registry=registry,
                      conn_factory=lambda: store.connect(db), **kw)


def test_judge_never_moves_score_assertions_or_attribution(tmp_path):
    """同一份夹具：跑 judge 与不跑，除两个新字段外逐字段相等。

    judge 一旦渗进 `ratio`，报告就变成「模型说了算」——复算不出来，也无法向第三方证明。
    这里用同一份输入跑两遍再对撞，比读代码可靠。
    """
    plain = _report_in_fresh_db(tmp_path, "plain")
    judged = _report_in_fresh_db(
        tmp_path, "judged",
        judge=lj.StubJudge([_verdict(3), _verdict(3)]), arbitration_threshold=0.5,
    )

    assert set(judged) - set(plain) == {"judge_scores", "judge_agreement"}
    for key in plain:
        assert judged[key] == plain[key], f"{key} 被 judge 改动了"
    assert judged["report_attribution"] == plain["report_attribution"]


def test_judge_fields_pass_the_report_schema(env):
    """新字段必须被 schema 认下来（否则线上报告一落库就校验不过）。"""
    report = _submit(env, _payload(), judge=lj.StubJudge([_verdict(4), _verdict(3)]),
                     arbitration_threshold=0.5)
    dr.validate_report(report)          # 不抛即合规
    assert report["judge_agreement"] == 0.0
    assert [s["rating"] for s in report["judge_scores"]] == [4, 3]
    assert all("suspected_patterns" in s for s in report["judge_scores"])


# ── 2. 低一致率进队列，够一致不进 ────────────────────────────────────────────

def test_disagreement_lands_in_the_queue(env):
    """两次给不同档 ⇒ 进队列，且队列里记的是**当时**的一致率与阈值。"""
    report = _submit(env, _payload(), judge=lj.StubJudge([_verdict(3), _verdict(2)]),
                     arbitration_threshold=1.0)
    assert report["judge_agreement"] == 0.0

    rows = _queue(env)
    assert len(rows) == 1
    row = rows[0]
    assert row["submission_id"] == "sub-judge-1"
    assert row["status"] == "pending"
    assert row["judge_agreement"] == 0.0
    assert row["threshold"] == 1.0
    assert row["reason"] == "judge_disagreement"
    assert row["user_id"] == "u-judge"          # 从 submissions 现读，不是调用方编的
    assert row["task_id"] == "dbg-001-order-report"

    # 两个派生列真被填了：只在报告里挂字段、库里留 NULL，下游按列查时会当成"没评过"。
    c = env["conn"]()
    try:
        stored = store.load_submission(c, "sub-judge-1")
        assert stored["judge_agreement"] == 0.0
        assert [s["rating"] for s in stored["judge_scores"]] == [3, 2]
    finally:
        c.close()


def test_agreement_at_or_above_threshold_does_not_enqueue(env):
    """两次同一档 ⇒ 一致率 1.0，不进队列；字段仍然写进报告（"评过"要留痕）。"""
    report = _submit(env, _payload(), judge=lj.StubJudge([_verdict(3), _verdict(3)]),
                     arbitration_threshold=0.5)
    assert report["judge_agreement"] == 1.0
    assert _queue(env) == []


def test_threshold_boundary_is_strictly_less_than(env):
    """一致率**恰好等于**阈值不入队（`<` 不是 `<=`），低于才入队。

    判据全项目只在一处实现（`llm_judge.needs_arbitration`）；这条断言是防止某天
    有人在本模块里再写一次比较，把边界写过界——那种重复实现不会报错，只会让两条路慢慢分叉。
    """
    same = _submit(env, _payload(submission_id="s-equal"),
                   judge=lj.StubJudge([_verdict(3), _verdict(3)]),
                   arbitration_threshold=1.0)
    assert same["judge_agreement"] == 1.0
    assert _queue(env) == [], "一致率 == 阈值时不该入队"

    lower = _submit(env, _payload(submission_id="s-lower"),
                    judge=lj.StubJudge([_verdict(3), _verdict(2)]),
                    arbitration_threshold=1.0)
    assert lower["judge_agreement"] == 0.0
    assert [r["submission_id"] for r in _queue(env)] == ["s-lower"]


# ── 3. 阈值必须显式给，没有默认值 ────────────────────────────────────────────

def test_judge_without_threshold_is_a_config_error(env):
    with pytest.raises(api.SubmissionError) as exc:
        _submit(env, _payload(), judge=lj.StubJudge([_verdict(3), _verdict(3)]))
    assert exc.value.code == "judge_config_incomplete"
    assert exc.value.status == 500


def test_threshold_without_judge_is_also_a_config_error(env):
    """只给阈值不给 judge：同样是配置错误，不能当作"开了但没评"默默跳过。"""
    with pytest.raises(api.SubmissionError) as exc:
        _submit(env, _payload(), arbitration_threshold=0.5)
    assert exc.value.code == "judge_config_incomplete"


@pytest.mark.parametrize("bad", [0, 0.0, -0.1, 1.01, True, "0.5"])
def test_bad_threshold_is_rejected(env, bad):
    with pytest.raises(jp.JudgePipelineError):
        _submit(env, _payload(), judge=lj.StubJudge([_verdict(3), _verdict(3)]),
                arbitration_threshold=bad)


# ── 4. 故障不冒充判决 ───────────────────────────────────────────────────────

def test_judge_crash_is_503_and_writes_nothing(env):
    """judge 抛异常 ⇒ 503，且**一条提交都不落**。

    若落库，库里就会留下一份"评过、但没有 judge 字段"的证据；
    与"根本没评"无法区分，事后所有一致性统计都会偏。
    """
    class Boom:
        def __call__(self, context):
            raise RuntimeError("模型端点了抽")

    with pytest.raises(api.SubmissionError) as exc:
        _submit(env, _payload(), judge=Boom(), arbitration_threshold=0.5)
    assert exc.value.code == "judge_unavailable"
    assert exc.value.status == 503

    c = env["conn"]()
    try:
        assert store.count_submissions(c) == 0
        assert store.list_arbitration(c, status=None) == []
    finally:
        c.close()


def test_out_of_vocabulary_pattern_is_rejected(env):
    """judge 报词表外的模式 ⇒ 也走 503（不是静默丢弃）。

    放过它会让 `skill_gaps` 出现词表里没有的技能；这与 `llm_judge` 的约束 2 同一条。
    """
    with pytest.raises(api.SubmissionError) as exc:
        _submit(env, _payload(),
                judge=lj.StubJudge([_verdict(3, "invented_pattern"), _verdict(3)]),
                arbitration_threshold=0.5)
    assert exc.value.code == "judge_unavailable"


# ── 5. 队列不碰掌握度、不碰排期（本轮的取舍，写进测试免得被误读）───────────

def test_queue_membership_does_not_change_mastery_or_scheduling(tmp_path):
    """带 judge（且入队）与不带 judge 的两份提交：P 快照与复习项必须一致。

    这是口径判断的机械边界：judge 低一致率说明**判据表述**有歧义（系统侧），
    把它冻结成学习者的成绩缺失，等于把自己的缺陷记在对方账上（决策 7）。
    所以队列是"改判据"的待办，不是"这份不算数"的闸门。
    要改成冻结（读法 B），这条会红——那正是它该红的地方。
    """
    results = {}
    for tag, kw in (("plain", {}),
                    ("judged", {"judge": lj.StubJudge([_verdict(3), _verdict(2)]),
                                "arbitration_threshold": 1.0})):
        db = tmp_path / f"{tag}.db"
        registry = api.TaskRegistry()
        api.bootstrap(db, registry)
        api.submit(_payload(submission_id="s-sched"), registry=registry,
                   conn_factory=lambda p=db: store.connect(p), **kw)
        c = store.connect(db)
        try:
            snaps = [dict(r) for r in store.list_mastery_snapshots(c)]
            report = store.load_report(c, "s-sched")
            sched = sch.schedule_submission(report, user_id="u-judge", at=AT)
            results[tag] = (snaps, [i.to_row() for i in sched.items],
                            [d.to_dict() for d in sched.decisions],
                            [dict(r) for r in c.execute("SELECT * FROM user_skill_states")])
        finally:
            c.close()

    plain, judged = results["plain"], results["judged"]
    assert plain[0] == judged[0], "入队改变了 P 快照"
    assert plain[1] == judged[1], "入队改变了该建的复习项"
    assert plain[2] == judged[2], "入队改变了逐技能调度决策"
    assert list(plain[3]) == list(judged[3]), "入队动了 FSRS 卡表"


# ── 6. 队列自身 ─────────────────────────────────────────────────────────────

def test_queue_requires_a_saved_submission(env):
    """先入队后落库 ⇒ 报错。外键指向一条不存在的证据时，队列就没有复核价值。"""
    c = env["conn"]()
    try:
        with pytest.raises(store.StoreError, match="还没落库"):
            store.save_arbitration_entry(c, "ghost-submission",
                                         judge_agreement=0.0, threshold=0.5)
    finally:
        c.close()


def test_queue_is_idempotent_and_keeps_the_original_threshold(env):
    """同一份提交重复入队只留一条，且**不改**当时记下的阈值。

    改掉 threshold 等于把"当时按多少判的"抹了，事后无法复核这次为什么进队。
    """
    _submit(env, _payload(), judge=lj.StubJudge([_verdict(3), _verdict(2)]),
            arbitration_threshold=1.0)
    c = env["conn"]()
    try:
        first = store.load_arbitration(c, "sub-judge-1")
        again = store.save_arbitration_entry(c, "sub-judge-1",
                                             judge_agreement=0.0, threshold=0.25)
        assert again["threshold"] == first["threshold"] == 1.0
        assert len(store.list_arbitration(c, status=None)) == 1
    finally:
        c.close()


def test_resolution_requires_a_note_and_is_not_overwritable(env):
    _submit(env, _payload(), judge=lj.StubJudge([_verdict(3), _verdict(2)]),
            arbitration_threshold=1.0)
    c = env["conn"]()
    try:
        with pytest.raises(store.StoreError, match="必须写 note"):
            store.resolve_arbitration(c, "sub-judge-1", note="   ")
        with pytest.raises(store.StoreError, match="pending"):
            store.resolve_arbitration(c, "sub-judge-1", status="pending", note="x")

        row = store.resolve_arbitration(c, "sub-judge-1", note="判据 R3 措辞改成精确到页数")
        assert row["status"] == "resolved"
        assert row["resolution_note"].startswith("判据")
        assert row["resolved_at"]

        with pytest.raises(store.StoreError, match="已经裁决过"):
            store.resolve_arbitration(c, "sub-judge-1", status="dismissed", note="再改一次")
    finally:
        c.close()


def test_db_check_constraint_blocks_a_pending_row_with_a_note(env):
    """绕过 store 直接写库也写不进"pending 却带裁决痕迹"的行。

    代码层的校验只说人话；表级 CHECK 才是最后一道——两个都要有，
    否则将来有人写脚本直接 INSERT 就绕过去了。
    """
    _submit(env, _payload(), judge=lj.StubJudge([_verdict(3), _verdict(2)]),
            arbitration_threshold=1.0)
    c = env["conn"]()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with c:
                c.execute("UPDATE arbitration_queue SET resolved_at = ? "
                          "WHERE submission_id = 'sub-judge-1'", (AT,))
    finally:
        c.close()


def test_unknown_status_is_rejected(env):
    c = env["conn"]()
    try:
        with pytest.raises(store.StoreError, match="未知的队列状态"):
            store.list_arbitration(c, status="done")
    finally:
        c.close()


# ── 7. attach_judgement 本身的三条边界 ──────────────────────────────────────

def test_attach_refuses_to_overwrite_and_to_attach_to_a_fragment():
    result = lj.double_judge(lj.StubJudge([_verdict(3), _verdict(3)]), {}, vocab=set())
    report = _submit_env_report()
    once = jp.attach_judgement(report, result)
    with pytest.raises(jp.JudgePipelineError, match="不覆盖"):
        jp.attach_judgement(once, result)
    with pytest.raises(jp.JudgePipelineError, match="不是一份完整报告"):
        jp.attach_judgement({"submission": {"submission_id": "x"}}, result)


def test_attach_returns_a_copy_and_leaves_the_original_alone():
    """返回副本：调用方可能还持有"未经 judge 的报告"（落库、对拍、对比实验都要它）。"""
    result = lj.double_judge(lj.StubJudge([_verdict(4), _verdict(4)]), {}, vocab=set())
    report = _submit_env_report()
    before = json.dumps(report, sort_keys=True, ensure_ascii=False)
    out = jp.attach_judgement(report, result)
    assert json.dumps(report, sort_keys=True, ensure_ascii=False) == before
    assert out is not report
    assert out["score"] is not report["score"] or out["score"] == report["score"]


def _submit_env_report() -> dict:
    """造一份真报告用于 attach 的单测（不落库，临时目录用完即弃）。"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "a.db"
        registry = api.TaskRegistry()
        api.bootstrap(db, registry)
        report = api.submit(_payload(submission_id="s-attach"), registry=registry,
                            conn_factory=lambda: store.connect(db))
    return report
