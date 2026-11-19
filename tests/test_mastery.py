"""掌握度 P 的约束测试（round-80，B-07）。

锁的是 `src/arena/mastery.py` 文档里那四条设计决定，每条都要能被一份红测试否掉：

  ① `c_k` 是**按技能加权通过率**，不是整份 `score.ratio`（否则 R2 答错会拉低别的技能）；
  ② `unscored` / `blocked` 从分子分母**一起剔除**，不是记 0 分（决策 7）；
  ③ 时间轴按**解析后的时刻**排序，不按字符串、不按插入顺序；
  ④ 初值不发明（默认首次观测播种），衰减**尚未实现**——后者是现状锁，B-08 落地时反转它。

另有三条硬边界：λ 越界报错不 clamp、无证据时 P 是 `None` 而不是 0、P 只读库不写库。
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import mastery as m  # noqa: E402
from arena import diagnosis_report as dr  # noqa: E402
from arena import store  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402

TASK_YAML = ROOT / "tasks" / "dbg-001-order-report.yaml"
USER = "user-mastery"

MSP = "multi_step_planning"
FR = "failure_retry"
TPV = "tool_param_validation"


def _report(name: str, *, submission_id: str | None = None,
            at: str | None = None) -> dict:
    """取一份夹具报告；需要造历史时改 id 与时刻。"""
    report = copy.deepcopy(run_fixture(name))
    if submission_id is not None:
        report["submission"]["submission_id"] = submission_id
    if at is not None:
        report["submission"]["evaluated_at"] = at
    return report


def _voided_report(name: str, *, submission_id: str, at: str) -> dict:
    """真流水线造一份作废报告：版本对不上 ⇒ `voided`（不是手改 `score.voided`）。

    手改字段只能测"我这个函数读不读这个布尔值"，测不出"报告真的会变成 voided"
    ——那样口径改回 A 时测试仍绿。这里走 `build_report` 的同一条路。
    """
    task = dr.load_task(TASK_YAML)
    raw = json.loads((ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))
    raw["submission"]["submission_id"] = submission_id
    raw["submission"]["evaluated_at"] = at
    raw["submission"]["task_version"] = "1999-01-01.1"
    report = dr.build_report(task, raw, dr.load_vocabulary())
    assert report["score"]["voided"] is True and report["score"]["voided"] is not False
    assert report["score"]["void_reason"] == "task_version_mismatch"
    return report


def _ev(submission_id: str, at: str, passed: float, counted: float,
        skill: str = MSP) -> m.Evidence:
    """直接造证据，绕开报告——递推逻辑应该能在纯数据上验证。"""
    return m.Evidence(
        submission_id=submission_id,
        skill_id=skill,
        at=at,
        passed_weight=passed,
        counted_weight=counted,
    )


@pytest.fixture()
def db(tmp_path):
    conn = store.connect(tmp_path / "mastery.db")
    store.init_db(conn)
    store.register_skills(conn)
    store.register_patterns(conn)
    store.register_task(conn, TASK_YAML)
    yield conn
    conn.close()


# --- ① c_k 是按技能的加权通过率 ---------------------------------------------------

def test_c_is_weighted_pass_ratio_per_skill():
    """同一份提交里三个技能各拿各的 c：R4 通过不能给 R2 的失败打掩护。

    `raw-fake-retry`：multi_step_planning 0/2、failure_retry 0/1、
    tool_param_validation 1/1。若改用整份 `ratio`(=0.25) 更新三个技能，
    `tool_param_validation` 会被记成 0.25——它自己的断言其实全过了。
    """
    report = _report("raw-fake-retry")
    msp = m.skill_evidence(report, MSP)
    tpv = m.skill_evidence(report, TPV)
    assert (msp.passed_weight, msp.counted_weight) == (0.0, 2.0)
    assert msp.c == 0.0
    assert (tpv.passed_weight, tpv.counted_weight) == (1.0, 1.0)
    assert tpv.c == 1.0
    assert report["score"]["ratio"] == 0.25, "整份分与技能分本就应该不同，这是设计而不是 bug"


def test_partial_pass_within_one_skill_is_fractional():
    """同技能两条等权断言过一条 ⇒ c=0.5，不是 0 也不是 1。

    这条用最小报告手搭，而不是改夹具：夹具里每个技能只有一条积分断言（或权重相同的两条），
    测不出"分子分母都按权重累计"这件事。有了这条，把 `passed += weight` 写成 `passed += 1`
    会立刻红。
    """
    report = {
        "submission": {"submission_id": "mini", "evaluated_at": "2026-11-01T00:00:00Z"},
        # 门禁状态是必填项（缺了会报错，见 test_missing_score_is_loud_not_defaulted_to_ok）：
        # 手搭最小报告时也得带上，否则测的就不是"权重累计"而是"缺字段必炸"。
        "score": {"disqualified": False, "voided": False},
        # 手搭报告也要带 process_fact：缺字段报错（不许默认「过程齐全」，见 B-04i）。
        "process_fact": {"required": False, "submitted": True, "missing": False},
        "attribution_policy": {
            "pass": {"counts_toward_mastery": True},
            "fail": {"counts_toward_mastery": True},
            "blocked": {"counts_toward_mastery": False},
            "unscored": {"counts_toward_mastery": False},
        },
        "assertions": [
            {"id": "A", "state": "pass", "weight": 3, "skill_id": MSP},
            {"id": "B", "state": "fail", "weight": 1, "skill_id": MSP},
            {"id": "C", "state": "pass", "weight": 5, "skill_id": FR},
        ],
    }
    ev = m.skill_evidence(report, MSP)
    assert (ev.passed_weight, ev.counted_weight) == (3.0, 4.0)
    assert ev.c == pytest.approx(0.75)
    assert m.skill_evidence(report, FR).c == 1.0


# --- ② 决策 7：unscored / blocked 不记 0 分 --------------------------------------

def test_unscored_and_blocked_are_dropped_from_both_sides():
    """`raw-no-trace`：答案对、过程全缺 ⇒ 甲口径下**整份**不入 P。

    拍板前这里断言 `multi_step_planning` 的 pass(2) ⇒ c=1.0，
    即「不交过程在分数上吃亏、掌握度上反而记一次做对」（B-04i 的病灶）。
    2026-11-19 人拍甲后，这条 pass 随整份排除；FR/TPV 的 `unscored` 同样
    不再被当 0 分——两者都用 `counts=False` 表达，而不是 c=0。
    """
    report = _report("raw-no-trace")
    msp = m.skill_evidence(report, MSP)
    assert not msp.counts and msp.process_missing is True
    assert (msp.passed_weight, msp.counted_weight) == (0.0, 0.0)
    assert msp.ignored_reasons() == {"process_missing": 1}
    for skill in (FR, TPV):
        ev = m.skill_evidence(report, skill)
        assert not ev.counts and ev.process_missing is True
        assert ev.ignored_reasons() == {"process_missing": 1}


def test_unscored_and_blocked_are_dropped_even_with_process_present():
    """决策 7 的独立守卫：**过程齐全**时 unscored/blocked 仍从分子分母一起剔除。

    为什么要有这条：B-04i 后 `raw-no-trace` 整份排除，原来的
    `test_unscored_and_blocked_are_dropped_from_both_sides` 用它做材料，
    unscored 那条规则被整份排除挡在前面——变异 M2（把
    `counts_toward_mastery` 判断改成恒真）因此变成 MISSED，决策 7 没人管了。
    这里手搭一份**过程齐全**的最小报告，让决策 7 与 B-04i 各有各的守卫，
    互不遮挡。
    """
    report = {
        "submission": {"submission_id": "mini-with-process", "evaluated_at": "2026-11-01T00:00:00Z"},
        "score": {"disqualified": False, "voided": False},
        "process_fact": {"required": True, "submitted": True, "missing": False},
        "attribution_policy": {
            "pass": {"counts_toward_mastery": True},
            "fail": {"counts_toward_mastery": True},
            "blocked": {"counts_toward_mastery": False},
            "unscored": {"counts_toward_mastery": False},
        },
        "assertions": [
            {"id": "A", "state": "pass", "weight": 1, "skill_id": MSP},
            {"id": "B", "state": "unscored", "weight": 1, "skill_id": MSP},
            {"id": "C", "state": "blocked", "weight": 1, "skill_id": FR},
        ],
    }
    msp = m.skill_evidence(report, MSP)
    assert (msp.passed_weight, msp.counted_weight) == (1.0, 1.0)
    assert msp.ignored_reasons() == {"not_counted": 1}, "unscored 不许进分子分母"
    assert not m.skill_evidence(report, FR).counts, "blocked 也不许进证据"


def test_blocked_skill_gets_no_evidence_not_zero():
    """`raw-empty`：门禁失败 ⇒ R2 是 `blocked`，不能算成"多步执行 0 分"。"""
    report = _report("raw-empty")
    msp = m.skill_evidence(report, MSP)
    assert not msp.counts
    assert msp.ignored[0].reason == "not_counted:state=blocked"
    with pytest.raises(m.MasteryError, match="不构成证据"):
        _ = msp.c


def test_real_fail_still_counts_as_zero():
    """决策 7 的另一半：真失败**要**记 0 分，别把"剔除缺数据"顺手扩大成"都不算"。"""
    report = _report("raw-empty")
    ev = m.skill_evidence(report, FR)
    assert ev.counts and ev.c == 0.0


def test_zero_weight_counted_assertion_is_loud_in_ignored():
    """现状锁（B-07b）：门禁断言 `weight=0` 却 `counts_toward_mastery=true`。

    它失败时既进不了分子也进不了分母，等于**掌握度上不留痕**。当前任务的四条门禁断言
    `skill_id` 全是 null，所以现状无实际影响；一旦有任务给门禁断言写 `skill_id`，
    这里就会漏记一次真失败。把它记进 `ignored` 而不是静默跳过，是为了将来能被查出来。
    """
    report = _report("raw-perfect")
    report["assertions"] = copy.deepcopy(report["assertions"])
    for a in report["assertions"]:
        if a["id"] == "R1_file_exists":
            a["state"] = "fail"
            a["skill_id"] = MSP
    ev = m.skill_evidence(report, MSP)
    assert ev.counted_weight == 2.0, "门禁的 0 权重不能进分母"
    assert any(i.reason == "zero_weight:state=fail" for i in ev.ignored)


def test_gate_failure_still_lifts_credit_skills():
    """现状锁（B-07b，待口径决策）：**整份不合格**的提交仍会抬高两个技能的 P。

    `raw-no-file` 是"没交产物"：门禁 R1 失败、整份 `disqualified=True`、`ratio=0`。
    但 data-model §5.2 的口径是「门禁只管分数，不管观测」——R3/R4 照常判定，
    而这次它俩都真的过了（有重试、参数没编）。于是 `failure_retry` 与
    `tool_param_validation` 的 c 都是 1.0。

    这说明一件事：**"不交产物但重试规范"可以刷高这两个技能的 P**。
    本测试不判它对错（那属产品口径，已登记 B-07b），只把它钉成可复现的事实——
    将来要改（比如"门禁失败一律不计证据"），改完这条会红，正好提醒同步改文档。
    """
    report = _report("raw-no-file")
    assert report["score"]["ratio"] == 0.0 and report["score"]["disqualified"] is True
    assert m.skill_evidence(report, FR).c == 1.0
    assert m.skill_evidence(report, TPV).c == 1.0
    assert not m.skill_evidence(report, MSP).counts, "R2 是 blocked，不进证据"


def test_one_white_paper_looks_like_a_perfect_run():
    """现状锁（B-07b）：**一份"没交产物"的提交 = 一次满分**，在 P 上不可区分。

    这条把"漏洞有多大"从观感问题量化成数字（外部评审 round-81 提的第 3 个未知量）：
    白卷的 `c` 是 1.0，而 `p0=None` 时**首次观测播种** `P_1 = c_1`
    ⇒ 第一份白卷就把 `P` 直接顶到 1.0000，与真做对一次完全相同。
    所以"刷几十次才涨一点"这个乐观假设不成立：**一次就够**。

    只有已积累过证据的用户才会被 EMA 拉平：从 P=0 起 7 次白卷到 0.9176（`1-0.7^7`）。
    改口径（C 降权 / B 排除）时这条必红，正好提醒同步改文档与 `gate_status` 过滤。
    """
    white = _report("raw-no-file", submission_id="fp-1", at="2026-11-01T00:00:00+00:00")
    perfect = _report("raw-perfect", submission_id="ok-1", at="2026-11-01T00:00:00+00:00")
    assert white["score"]["ratio"] == 0.0 and white["score"]["disqualified"] is True

    p_white = m.replay([m.skill_evidence(white, FR)], user_id=USER, skill_id=FR).p
    p_perfect = m.replay([m.skill_evidence(perfect, FR)], user_id=USER, skill_id=FR).p
    assert p_white == p_perfect == 1.0


def test_disqualified_still_counts_under_policy_a_and_is_recorded():
    """`disqualified` 仍按 A 口径照记（2026-11-15 人拍板），且逐层留痕。

    **注意别再把它读成"所有门禁状态都不参与计算"**：`voided` 自 2026-11-16 起被排除
    （见下一条），两者刻意分开——作废是"这次不算数"，不合格是"这次算数但没做好"。
    这里锁住三件事：A 口径没被顺手改掉、`gate_status` 逐层留痕（切 C/B 才不用改历史）、
    拼错的状态名会报错而不是静默溜过。
    """
    white = _report("raw-no-file", submission_id="fp-1", at="2026-11-01T00:00:00+00:00")
    ev = m.skill_evidence(white, FR)
    assert ev.gate_status == "disqualified"

    series = m.replay([ev], user_id=USER, skill_id=FR)
    assert series.p == 1.0, "现行口径＝A：留痕字段不许偷偷改变结果"
    assert series.steps[0].gate_status == "disqualified"
    assert series.to_dict()["steps"][0]["gate_status"] == "disqualified"

    ok = m.skill_evidence(_report("raw-perfect"), FR)
    assert ok.gate_status == "ok"

    typo = m.Evidence("s1", MSP, "2026-11-01T00:00:00+00:00", 1.0, 1.0, gate_status="白卷")
    with pytest.raises(m.MasteryError, match="gate_status"):
        m.replay([typo], user_id=USER, skill_id=MSP)


def test_voided_evidence_is_excluded_not_scored_as_zero():
    """人拍板「甲」（2026-11-16）：作废**不计**掌握度，是排除不是记 0 分。

    三分之二要分开锁：
    ① 作废那份一条都不计入（不是 c=0——c=0 会把它变成"这个技能完全不会"）；
    ② 只有作废提交时 `P is None`（决策 7 的同一个形状），不是 0.0；
    ③ 它夹在正常提交之间时**不推动** P，但会作为 `skipped` 留痕。
    """
    voided = _voided_report("raw-perfect", submission_id="voided-1",
                            at="2026-11-02T00:00:00+00:00")
    ev = m.skill_evidence(voided, FR)
    assert ev.gate_status == "voided"
    assert not ev.counts and ev.counted_weight == 0.0 and ev.passed_weight == 0.0
    assert set(ev.ignored_reasons()) == {"voided"}, "每条断言都要留「为什么没算」"
    with pytest.raises(m.MasteryError, match="不构成证据"):
        _ = ev.c

    only = m.replay([ev], user_id=USER, skill_id=FR)
    assert only.p is None, "只有作废提交 ⇒ 没有掌握度，不是 0 分"
    assert len(only.steps) == 0 and len(only.skipped) == 1

    normal = m.skill_evidence(
        _report("raw-fake-retry", submission_id="ok-1", at="2026-11-01T00:00:00+00:00"), FR
    )
    mixed = m.replay([normal, ev], user_id=USER, skill_id=FR)
    assert mixed.p == m.replay([normal], user_id=USER, skill_id=FR).p, "作废那条不许推动 P"
    assert len(mixed.steps) == 1 and len(mixed.skipped) == 1


def test_voided_submission_stays_in_history_but_never_lifts_p(db):
    """「保留练习提交记录」那半句也要有测试：提交行照留、快照不跟着动。

    人拍板原话是「作废不计成绩/不计掌握度，但保留练习提交记录」。
    只测 P 会漏掉一半——若有人为了"不计"去删提交行，历史就丢了。
    """
    good = _report("raw-perfect", submission_id="s-good", at="2026-11-01T00:00:00+00:00")
    store.save_submission(db, good, user_id=USER, refresh_mastery=True)
    p_good = m.build_series(db, USER, FR).p
    assert p_good == 1.0

    voided = _voided_report("raw-perfect", submission_id="s-voided",
                            at="2026-11-02T00:00:00+00:00")
    store.save_submission(db, voided, user_id=USER, refresh_mastery=True)

    rows = db.execute("SELECT submission_id FROM submissions ORDER BY created_at").fetchall()
    assert [r[0] for r in rows] == ["s-good", "s-voided"], "作废的提交必须留在历史里"

    assert m.build_series(db, USER, FR).p == p_good, "作废提交不许抬高掌握度"
    snap = store.load_mastery_snapshot(db, USER, FR)
    assert snap is not None and snap["mastery_p"] == p_good
    assert snap["evidence_count"] == 1, "快照只数计入的那一条"
    assert snap["last_submission_id"] == "s-good", "最后一条**计入**证据仍是 s-good"


# --- B-04i：缺过程整份排除（2026-11-19 人拍甲）------------------------------------

def test_missing_process_evidence_is_excluded_not_scored_as_zero():
    """缺过程是**排除**，不是 c=0；不推动 P，但 `skipped` 写明为什么。

    三点分开锁：① 只有缺过程提交时 P is None（与 voided、决策 7 同形状）；
    ② 夹在正常提交之间时 P 原封不动；③ 序列化后能查到 `process_missing`。
    """
    missing = m.skill_evidence(
        _report("raw-no-trace", submission_id="nt-1", at="2026-11-02T00:00:00+00:00"), MSP
    )
    assert missing.process_missing is True
    assert not missing.counts and missing.counted_weight == 0.0
    assert missing.ignored_reasons() == {"process_missing": 1}
    payload = missing.to_dict()
    assert payload["process_missing"] is True and payload["c"] is None
    with pytest.raises(m.MasteryError, match="不构成证据"):
        _ = missing.c

    only = m.replay([missing], user_id=USER, skill_id=MSP)
    assert only.p is None and len(only.steps) == 0 and len(only.skipped) == 1
    assert only.skipped[0].reason == "process_missing"

    normal = m.skill_evidence(
        _report("raw-perfect", submission_id="ok-1", at="2026-11-01T00:00:00+00:00"), MSP
    )
    mixed = m.replay([normal, missing], user_id=USER, skill_id=MSP)
    assert mixed.p == m.replay([normal], user_id=USER, skill_id=MSP).p == 1.0
    assert len(mixed.steps) == 1 and len(mixed.skipped) == 1


def test_missing_process_submission_stays_in_history_but_never_lifts_p(db):
    """「排除」不等于「删掉」：提交行照留，快照不跟着动。"""
    good = _report("raw-perfect", submission_id="s-good", at="2026-11-01T00:00:00+00:00")
    store.save_submission(db, good, user_id=USER, refresh_mastery=True)
    p_good = m.build_series(db, USER, MSP).p
    assert p_good == 1.0

    store.save_submission(
        db,
        _report("raw-no-trace", submission_id="s-nt", at="2026-11-02T00:00:00+00:00"),
        user_id=USER, refresh_mastery=True,
    )
    rows = [r[0] for r in db.execute("SELECT submission_id FROM submissions ORDER BY created_at")]
    assert rows == ["s-good", "s-nt"], "缺过程的提交必须留在历史里"

    series = m.build_series(db, USER, MSP)
    assert series.p == p_good, "缺过程提交不许抬高掌握度"
    assert [s.submission_id for s in series.skipped] == ["s-nt"]
    snap = store.load_mastery_snapshot(db, USER, MSP)
    assert snap is not None and snap["mastery_p"] == p_good
    assert snap["evidence_count"] == 1 and snap["last_submission_id"] == "s-good"


def test_missing_process_and_disqualified_are_different_dimensions():
    """两个排除/照记口径不能混：`disqualified` 照记（A），`process_missing` 排除（甲）。

    `raw-no-file` 是门禁不合格但过程齐全 ⇒ 仍抬高 P；
    `raw-no-trace` 是过程缺失但门禁没挂 ⇒ 整份排除。
    若把它们并成一格，白卷与缺过程会互相污染，B-07b 的 A 口径也就被悄悄改了。
    """
    dq = _report("raw-no-file", submission_id="dq", at="2026-11-01T00:00:00Z")
    nt = _report("raw-no-trace", submission_id="nt", at="2026-11-02T00:00:00Z")
    assert dq["score"]["disqualified"] is True and dq["process_fact"]["missing"] is False
    assert nt["score"]["disqualified"] is False and nt["process_fact"]["missing"] is True

    ev_dq = m.skill_evidence(dq, FR)
    ev_nt = m.skill_evidence(nt, FR)
    assert ev_dq.gate_status == "disqualified" and ev_dq.process_missing is False
    assert ev_dq.counts and ev_dq.c == 1.0, "A 口径没被顺手改掉"
    assert ev_nt.gate_status == "ok" and ev_nt.process_missing is True
    assert not ev_nt.counts


def test_missing_process_fact_is_loud_not_defaulted_to_false():
    """缺 `process_fact.missing` 不许默认 false——那等于把「没记」当「过程齐全」。"""
    report = _report("raw-perfect")
    report.pop("process_fact")
    with pytest.raises(m.MasteryError, match="process_fact.missing"):
        m.skill_evidence(report, MSP)

    report = _report("raw-perfect")
    report["process_fact"]["missing"] = "false"  # 字符串真值会骗过 bool() 兜底
    with pytest.raises(m.MasteryError, match="process_fact.missing"):
        m.skill_evidence(report, MSP)


def test_contradictory_process_missing_evidence_is_loud():
    """标了缺过程却带正权重进来是口径冲突，必须炸，不能静默选一种解释。"""
    bad = m.Evidence("s-bad", MSP, "2026-11-01T00:00:00Z", 1.0, 1.0, process_missing=True)
    with pytest.raises(m.MasteryError, match="口径冲突"):
        m.replay([bad], user_id=USER, skill_id=MSP)


def test_missing_score_is_loud_not_defaulted_to_ok():
    """缺 `score.disqualified` 不许默认成 `ok`——那正好抹掉切 B/C 唯一要的信号。"""
    report = _report("raw-no-file")
    report.pop("score")
    with pytest.raises(m.MasteryError, match="score.disqualified"):
        m.skill_evidence(report, FR)


def test_missing_attribution_policy_is_loud():
    """没有 `attribution_policy` 就不知道该不该计——猜一个等于自造口径。"""
    report = _report("raw-perfect")
    report.pop("attribution_policy")
    with pytest.raises(m.MasteryError, match="attribution_policy"):
        m.skill_evidence(report, MSP)


def test_unknown_state_in_policy_is_loud():
    report = _report("raw-perfect")
    report["attribution_policy"].pop("pass")
    with pytest.raises(m.MasteryError, match="不在 attribution_policy"):
        m.skill_evidence(report, MSP)


def test_missing_time_is_loud_not_guessed():
    """时间轴不能靠"当前时刻"兜底——补录历史会被记成今天，间隔全错。"""
    report = _report("raw-perfect")
    report["submission"].pop("evaluated_at")
    with pytest.raises(m.MasteryError, match="evaluated_at"):
        m.skill_evidence(report, MSP)


# --- 递推式本身 -------------------------------------------------------------------

def test_update_p_matches_hand_calculation():
    """手算对照表：λ=0.3 时 0→1 得 0.3，0.3→0 得 0.21，1→0 得 0.7。"""
    assert m.update_p(0.0, 1.0, 0.3) == pytest.approx(0.3)
    assert m.update_p(0.3, 0.0, 0.3) == pytest.approx(0.21)
    assert m.update_p(1.0, 0.0, 0.3) == pytest.approx(0.7)
    assert m.update_p(0.5, 0.5, 0.3) == pytest.approx(0.5)


def test_lambda_one_means_last_observation_wins():
    """λ=1 是公式的端点：忘掉历史，P 直接等于最新证据。"""
    assert m.update_p(0.123, 1.0, 1.0) == pytest.approx(1.0)


@pytest.mark.parametrize("bad", [(-0.1, 0.5, 0.3), (0.5, 1.5, 0.3), (0.5, 0.5, 0.0), (0.5, 0.5, 1.5)])
def test_out_of_range_inputs_raise_instead_of_clamping(bad):
    """越界就报错：clamp 会把"数据/模型错"伪装成一个合法的低分，之后没人查得出来。"""
    with pytest.raises(m.MasteryError):
        m.update_p(*bad)


def test_three_consecutive_submissions_follow_formula():
    """BACKLOG 的判据原文：连续 3 次提交，P 按公式单调变化，且与手算一致。

    c = 1, 0, 1（λ=0.3，初值播种）：P = 1 → 0.7 → 0.79。
    """
    series = m.replay(
        [_ev("s1", "2026-11-01T00:00:00Z", 1, 1),
         _ev("s2", "2026-11-02T00:00:00Z", 0, 1),
         _ev("s3", "2026-11-03T00:00:00Z", 1, 1)],
        user_id=USER, skill_id=MSP, lam=0.3,
    )
    assert [round(s.p_after, 6) for s in series.steps] == [1.0, 0.7, 0.79]
    assert series.steps[0].seeded is True and series.steps[0].p_before is None
    assert series.steps[1].p_before == pytest.approx(1.0)
    assert series.p == pytest.approx(0.79)


def test_seeded_initialisation_vs_explicit_p0():
    """默认（不发明先验）与显式 p0 的差别只在第一步，且必须能在留痕里看出来。"""
    ev = [_ev("s1", "2026-11-01T00:00:00Z", 1, 1), _ev("s2", "2026-11-02T00:00:00Z", 1, 1)]

    seeded = m.replay(ev, user_id=USER, skill_id=MSP, lam=0.5)
    assert [s.p_after for s in seeded.steps] == [1.0, 1.0]
    assert seeded.steps[0].seeded is True

    prior = m.replay(ev, user_id=USER, skill_id=MSP, lam=0.5, p0=0.5)
    assert [round(s.p_after, 6) for s in prior.steps] == [0.75, 0.875]
    assert prior.steps[0].seeded is False
    assert prior.steps[0].p_before == 0.5


def test_skipped_evidence_does_not_advance_p():
    """没有证据的提交必须**原地不动**，不能当成"插值一次"。

    c = 1, 无证据, 0（λ=0.3）：P = 1 → 0.7。若把无证据那次当 0 分插入，
    结果会变成 0.49——两个数字都"看起来合理"，所以必须用测试钉死。
    """
    series = m.replay(
        [_ev("s1", "2026-11-01T00:00:00Z", 1, 1),
         _ev("s2", "2026-11-02T00:00:00Z", 0, 0),
         _ev("s3", "2026-11-03T00:00:00Z", 0, 1)],
        user_id=USER, skill_id=MSP, lam=0.3,
    )
    assert [round(s.p_after, 6) for s in series.steps] == [1.0, 0.7]
    assert [s.submission_id for s in series.skipped] == ["s2"]
    assert series.skipped[0].reason == "no_counted_assertion"


def test_converges_to_constant_evidence():
    """常数证据下 P 收敛到 c——EMA 的基本性质，也是"λ 不改变稳态"的证明。"""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ev = [
        _ev(f"s{i}", (base + timedelta(days=i)).isoformat(), 0.4, 1.0)
        for i in range(40)
    ]
    series = m.replay(ev, user_id=USER, skill_id=MSP, lam=0.3, p0=0.0)
    assert series.p == pytest.approx(0.4, abs=1e-6)


def test_p_never_leaves_unit_interval():
    values = [1.0, 0.0, 1.0, 0.0, 0.0, 1.0]
    series = m.replay(
        [_ev(f"s{i}", f"2026-11-{i:02d}T00:00:00Z", v, 1.0) for i, v in enumerate(values, start=1)],
        user_id=USER, skill_id=MSP, lam=0.7,
    )
    assert all(0.0 <= s.p_after <= 1.0 for s in series.steps)


def test_replay_rejects_evidence_for_another_skill():
    with pytest.raises(m.MasteryError, match="不一致"):
        m.replay([_ev("s1", "2026-11-01T00:00:00Z", 1, 1, skill=FR)],
                 user_id=USER, skill_id=MSP)


def test_empty_history_yields_none_not_zero():
    """没做过题 ⇒ `p=None`。返回 0 会被排程层读成"全不会"，凭空生成复习任务。"""
    series = m.replay([], user_id=USER, skill_id=MSP)
    assert series.p is None
    assert series.steps == () and series.skipped == ()
    assert series.to_dict()["p"] is None


def test_replay_does_not_mutate_input():
    ev = [_ev("s1", "2026-11-01T00:00:00Z", 1, 1)]
    before = (ev[0].submission_id, ev[0].at, ev[0].passed_weight)
    m.replay(ev, user_id=USER, skill_id=MSP)
    assert (ev[0].submission_id, ev[0].at, ev[0].passed_weight) == before


# --- ③ 时间轴：按时刻，不按字符串、不按插入顺序 ------------------------------------

def test_order_is_by_instant_not_by_string_or_input_order():
    """`2026-11-07T12:00:00+08:00`(=04:00Z) 早于 `2026-11-07T05:00:00Z`，
    但字符串比较会判反。排错就会把 P 的递推方向整体倒过来，而数仍然"单调"、看不出异常。
    """
    series = m.replay(
        [_ev("late", "2026-11-07T05:00:00Z", 0, 1),
         _ev("early", "2026-11-07T12:00:00+08:00", 1, 1)],
        user_id=USER, skill_id=MSP, lam=0.5,
    )
    assert [s.submission_id for s in series.steps] == ["early", "late"]
    assert [s.p_after for s in series.steps] == [1.0, 0.5]


def test_same_instant_is_ordered_by_submission_id_for_determinism():
    """同一时刻的两份提交必须有稳定次序，否则重跑结果会抖。"""
    ev = [_ev("b", "2026-11-07T00:00:00Z", 1, 1), _ev("a", "2026-11-07T00:00:00Z", 0, 1)]
    first = m.replay(ev, user_id=USER, skill_id=MSP, lam=0.5)
    second = m.replay(list(reversed(ev)), user_id=USER, skill_id=MSP, lam=0.5)
    assert [s.submission_id for s in first.steps] == ["a", "b"]
    assert [s.p_after for s in first.steps] == [s.p_after for s in second.steps]


def test_naive_and_offset_timestamps_can_be_compared():
    """`Z`、`+08:00`、无时区三种写法都要能排进同一条时间轴。"""
    a = m.parse_instant("2026-11-07T00:00:00Z")
    b = m.parse_instant("2026-11-07T08:00:00+08:00")
    c = m.parse_instant("2026-11-07T00:00:00")
    assert a == b == c


def test_unparsable_time_is_loud():
    with pytest.raises(m.MasteryError, match="无法解析时刻"):
        m.parse_instant("昨天下午")


# --- ④ 衰减：尚未实现（现状锁，改到 B-08b 反转） -----------------------------------

def test_decay_is_opt_in_and_plane_replay_ignores_the_gap():
    """**反转的现状锁**（B-08b，round-87）：衰减做了，但**只在显式给 `as_of` 时**。

    原先这条叫 `test_decay_is_not_implemented_yet`，断言"隔 1 天与隔 300 天 P 完全一样"。
    B-08b 落地后它必须反过来：不给 `as_of` ⇒ 仍然一样（老调用行为没变）；
    给了 `as_of` ⇒ 必须不同（否则衰减等于没接）。

    **刻意不删原来那条断言**：它是"P 不随墙钟变化"的唯一守卫。删了之后就没人区分
    「衰减没接」与「衰减接错了地方、把无 as_of 的调用也弄脏了」。
    """
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def series_with_gap(days: int) -> m.MasterySeries:
        ev = [
            _ev("s1", base.isoformat(), 1, 1),
            _ev("s2", (base + timedelta(days=days)).isoformat(), 1, 1),
        ]
        return m.replay(ev, user_id=USER, skill_id=MSP, lam=0.3)

    # ① 不给 as_of：递推只看证据顺序，与间隔无关（老行为，一个字没变）
    assert series_with_gap(1).p == series_with_gap(300).p == pytest.approx(1.0)

    # ② 给 as_of：同一个 as_of 下，隔 1 天练过与隔 300 天没碰必须读出不同 P
    from arena import decay as d

    as_of = (base + timedelta(days=301)).isoformat()
    fresh = d.decay_series(series_with_gap(300), as_of=as_of, stability=10.0)
    stale = d.decay_series(series_with_gap(1), as_of=as_of, stability=10.0)
    assert fresh.p_raw == stale.p_raw == pytest.approx(1.0)
    assert fresh.p_effective > stale.p_effective
    # ③ 衰减不写回序列：P 还是 1.0，说明"读法"与"状态"是两件事
    assert series_with_gap(300).p == pytest.approx(1.0)



# --- 读库：历史真的来自 SQLite，且只读 ---------------------------------------------

def test_build_series_reads_history_from_sqlite(db):
    """三份提交按 `created_at` 折出 P，与手算一致——这是 B-07 判据的完整形态。"""
    plan = [
        ("raw-perfect", "2026-11-01T00:00:00Z"),
        ("raw-fake-retry", "2026-11-02T00:00:00Z"),
        ("raw-perfect", "2026-11-03T00:00:00Z"),
    ]
    for i, (name, at) in enumerate(plan, start=1):
        store.save_submission(db, _report(name, submission_id=f"sub-{i}", at=at), user_id=USER)

    series = m.build_series(db, USER, MSP, lam=0.3)
    # c = 1, 0, 1 ⇒ 1 → 0.7 → 0.79
    assert [round(s.p_after, 6) for s in series.steps] == [1.0, 0.7, 0.79]
    assert [s.submission_id for s in series.steps] == ["sub-1", "sub-2", "sub-3"]
    assert series.p == pytest.approx(0.79)


def test_build_series_uses_db_created_at_as_time_axis(db):
    """库里 `created_at` 是时间轴权威：报告里的 `evaluated_at` 与它相反也不能改变顺序。

    实测造法：`a` 的报告时刻晚于 `b`，但落库的 `created_at` 早于 `b`。
    两种读法给出的顺序正好相反，所以这条能真正分开"读库锚点"与"再读一遍报告"。
    """
    store.save_submission(
        db, _report("raw-fake-retry", submission_id="a", at="2026-11-09T00:00:00Z"),
        user_id=USER, created_at="2026-11-01T00:00:00Z",
    )
    store.save_submission(
        db, _report("raw-perfect", submission_id="b", at="2026-11-03T00:00:00Z"),
        user_id=USER, created_at="2026-11-02T00:00:00Z",
    )
    series = m.build_series(db, USER, MSP, lam=0.5)
    assert [s.submission_id for s in series.steps] == ["a", "b"]


def test_build_series_ignores_other_users(db):
    store.save_submission(db, _report("raw-perfect", submission_id="mine", at="2026-11-01T00:00:00Z"),
                          user_id=USER)
    store.save_submission(db, _report("raw-fake-retry", submission_id="other", at="2026-11-02T00:00:00Z"),
                          user_id="someone-else")
    series = m.build_series(db, USER, MSP, lam=0.5)
    assert [s.submission_id for s in series.steps] == ["mine"]


def test_skill_without_any_counted_history_returns_none(db):
    """`raw-no-trace` 在 failure_retry 上整份排除：系列为空、P 为 None。"""
    store.save_submission(db, _report("raw-no-trace", submission_id="nt", at="2026-11-01T00:00:00Z"),
                          user_id=USER)
    series = m.build_series(db, USER, FR, lam=0.3)
    assert series.p is None
    assert len(series.skipped) == 1
    assert series.ignored == {"process_missing": 1}


def test_replay_never_writes_to_the_fsrs_card_table(db):
    """**round-88 / B-07c 反转的现状锁**：原锁是「P 不落任何字段」。

    现在 P 落库了，但落在**独立的 `mastery_snapshots` 表**，不碰 FSRS 卡片宿主。
    本条改成锁住这个边界：① `build_series()` 本身仍然是纯读（重放不许写库）；
    ② `user_skill_states` 的列集合与行数都不许变——B-08b「没卡 ≠ 忘光了」的
    契约靠这张表，往里塞 P 会把空卡和没卡混成一件事。
    """
    store.save_submission(db, _report("raw-perfect", submission_id="s1", at="2026-11-01T00:00:00Z"),
                          user_id=USER, refresh_mastery=False)
    cols_before = [r[1] for r in db.execute("PRAGMA table_info(user_skill_states)")]
    rows_before = db.execute("SELECT * FROM user_skill_states").fetchall()
    snap_before = db.execute("SELECT * FROM mastery_snapshots").fetchall()

    m.build_series(db, USER, MSP, lam=0.3)

    cols_after = [r[1] for r in db.execute("PRAGMA table_info(user_skill_states)")]
    assert cols_before == cols_after
    assert db.execute("SELECT * FROM user_skill_states").fetchall() == rows_before
    assert db.execute("SELECT * FROM mastery_snapshots").fetchall() == snap_before


def test_series_dict_is_json_serialisable():
    series = m.replay(
        [_ev("s1", "2026-11-01T00:00:00Z", 1, 2), _ev("s2", "2026-11-02T00:00:00Z", 0, 0)],
        user_id=USER, skill_id=MSP, lam=0.3,
    )
    payload = json.loads(json.dumps(series.to_dict(), ensure_ascii=False))
    assert payload["p"] == 0.5
    assert payload["observations"] == 1 and payload["skipped"] == 1
    assert payload["steps"][0]["c"] == 0.5


def test_lambda_sensitivity_is_visible():
    """λ 未标定时，结论随参数移动多少必须能一眼看到（这是"参数是假设"的可操作表达）。"""
    ev = [_ev(f"s{i}", f"2026-11-{i:02d}T00:00:00Z", 1, 1) for i in (1, 2)]
    ev.append(_ev("s3", "2026-11-03T00:00:00Z", 0, 1))
    table = m.series_for_lambdas(ev, user_id=USER, skill_id=MSP)
    assert table[0.1].p == pytest.approx(0.9)   # 0.9·1 + 0.1·0 = 0.9（历史几乎不被一次失败撼动）
    assert table[0.7].p == pytest.approx(0.3)   # 0.3·1 + 0.7·0 = 0.3
    assert table[0.1].p > table[0.7].p, "λ 越小越保守，最新一次失败的影响越小"


# --- 展示层（round-82）：给自己看的成绩单，只有两条硬规矩 -------------------------

def test_scorecard_shows_p_and_never_rank():
    """成绩单可以显示掌握度，但**不许先长出名次**。

    这是产品口径的机械表达（2026-11-15 人拍板）：成绩先给自己看，排名是后继的、有条件的。
    界面一旦先有"第几名"，口径会被界面倒逼着改（排名要求有后果 ⇒ 必须切口径 A→C）。
    所以这里直接跑脚本，断言输出里出现数字、且不出现名次类字样。
    """
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "mastery_scorecard.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "0.79" in out, "成绩单要给出可复核的掌握度数字（来自 build_series，不是手填）"
    assert "不参与任何排名" in out
    for word in ("第 1 名", "第1名", "排名第", "名次"):
        assert word not in out, f"排名未开，输出里不该出现 {word!r}"

