"""掌握度 P 的衰减（round-87，B-08b）。

锁的是 `src/arena/decay.py` 文档里的六条设计决定，每条都要能被一份红测试否掉：

  ① `P_eff = P_raw × R`，不设下限（长期不练会趋近 0，这是代价，不是 bug）；
  ② 锚点＝`max(卡片的 last_review, 最后一条计入证据的时刻)`，**不是**计划到期时刻；
  ③ `as_of` 必填，早于锚点直接报错（时间不许倒着用）；
  ④ 缺卡 / 缺 S ⇒ **不衰减**（py-fsrs 对无历史卡返回 0，照乘会把 P 变成 0）；
  ⑤ 不写库、不推进递推（`series.p` 一动不动）；
  ⑥ 退化路径返回完整对象 + 原因，不返回 `None`。

另有一条**外部对照**：`retrievability()` 的数必须与直接调 py-fsrs 得到的相同——
自己跟自己比不算验证，所以这里真的另调一次。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import decay as d  # noqa: E402
from arena import mastery as m  # noqa: E402

MSP = "multi_step_planning"
BASE = "2026-01-01T00:00:00Z"


def _series(*spans: int, p_last: float = 1.0) -> m.MasterySeries:
    """造一条 P 序列：每次 c=1（全过），第 n 次距第一次 `spans[n-1]` 天。"""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ev = [
        m.Evidence(submission_id=f"s{i}", skill_id=MSP,
                   at=(start + timedelta(days=g)).isoformat(),
                   passed_weight=1, counted_weight=1)
        for i, g in enumerate(spans, start=1)
    ]
    return m.replay(ev, user_id="u", skill_id=MSP, lam=0.3)


def _as_of(days: int) -> str:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return (start + timedelta(days=days)).isoformat()


# --- ① P_eff = P_raw × R ------------------------------------------------------

def test_effective_p_is_raw_times_r_and_never_exceeds_raw():
    series = _series(0)
    res = d.decay_series(series, as_of=_as_of(10), stability=10.0)
    assert res.applied is True
    assert res.r is not None and 0.0 < res.r < 1.0
    assert res.p_effective == pytest.approx(res.p_raw * res.r)
    assert res.p_effective < res.p_raw


def test_decay_curve_is_heavy_tailed_so_p_never_reaches_zero():
    """**实测值锁定**：不设下限也不会到 0，因为 FSRS 的曲线是重尾的。

    下表这些数是逐点跑出来的（`S=1` 天），不是拍的；写进测试是因为它推翻了我原先
    "长期不练 P 会趋近 0"的说法——真实风险是**衰减太慢**（550 天还有 0.38）。
    将来若换曲线或加 floor，这里必须改，改的时候就绕不开"凭什么"。
    """
    measured = {1: 0.9000, 7: 0.7276, 30: 0.5906, 365: 0.4037, 550: 0.3790,
                1825: 0.3151, 7300: 0.2544}
    for days, want in measured.items():
        series = _series(0)
        res = d.decay_series(series, as_of=_as_of(days), stability=1.0)
        assert res.r == pytest.approx(want, abs=1e-4), days
        assert res.p_effective == pytest.approx(res.p_raw * res.r)
    assert min(measured.values()) > 0.2      # 20 年也没到 0


def test_retrievability_at_the_stability_point_is_the_desired_retention():
    """`R(t=S) = 0.9`：这个 0.9 是 FSRS 的默认 desired_retention，不是我们拍的数。"""
    assert d.retrievability(7.0, 7.0) == pytest.approx(0.9, abs=1e-6)
    assert d.retrievability(30.0, 30.0) == pytest.approx(0.9, abs=1e-6)


def test_longer_stability_decays_slower():
    easy = d.decay_series(_series(0), as_of=_as_of(30), stability=100.0)
    hard = d.decay_series(_series(0), as_of=_as_of(30), stability=1.0)
    assert easy.r > hard.r
    assert easy.p_effective > hard.p_effective


# --- ② 锚点是"最后一次练过" ----------------------------------------------------

def test_anchor_is_the_later_of_card_and_evidence():
    """卡片比最后一条证据更晚 ⇒ 从卡片算起（更晚＝间隔更短＝衰减更少）。"""
    series = _series(0, 1)                      # 最后一条证据在 1-02
    later = (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=20)).isoformat()
    res = d.decay_series(series, as_of=_as_of(30), last_review=later, stability=10.0)
    assert res.anchor == later
    assert res.elapsed_days == pytest.approx(10.0)


def test_anchor_uses_evidence_when_card_is_older():
    series = _series(0, 5)
    older = _as_of(1)
    res = d.decay_series(series, as_of=_as_of(30), last_review=older, stability=10.0)
    assert res.anchor == _as_of(5)
    assert res.elapsed_days == pytest.approx(25.0)


def test_mixed_offsets_do_not_flip_the_anchor():
    """按字符串比大小会选错锚点——这里造一个"字符串更大、实际更早"的对子。

    证据 `20:00Z`、卡片 `次日 02:00+08:00`（＝前一日 18:00Z）。字符串上 `2026-01-02` 更大，
    实际卡片更早 ⇒ 正确锚点是证据。若实现改成 `max(字符串)`，`elapsed_days` 会多出 2 小时。
    """
    series = m.replay(
        [m.Evidence(submission_id="s1", skill_id=MSP, at="2026-01-01T20:00:00Z",
                    passed_weight=1, counted_weight=1)],
        user_id="u", skill_id=MSP,
    )
    res = d.decay_series(
        series, as_of="2026-01-11T20:00:00Z",
        last_review="2026-01-02T02:00:00+08:00",
        stability=10.0,
    )
    assert res.anchor == "2026-01-01T20:00:00Z"
    assert res.elapsed_days == pytest.approx(10.0)


# --- ③ as_of 必填、不许倒流 ----------------------------------------------------

def test_missing_as_of_is_a_type_error_not_a_default():
    with pytest.raises(TypeError):
        d.decay_series(_series(0), stability=10.0)   # type: ignore[call-arg]


def test_as_of_before_anchor_raises_instead_of_decaying_backwards():
    series = _series(5)
    with pytest.raises(d.DecayError, match="早于锚点"):
        d.decay_series(series, as_of=_as_of(1), stability=10.0)


def test_same_instant_is_not_later_and_keeps_p_exactly():
    series = _series(5)
    res = d.decay_series(series, as_of=_as_of(5), stability=10.0)
    assert res.applied is False
    assert res.reason == "not_later"
    assert res.r == 1.0
    assert res.p_effective == res.p_raw


# --- ④ 缺卡 / 缺 S 不衰减（py-fsrs 返回 0 的坑） --------------------------------

def test_missing_stability_keeps_p_and_says_no_card():
    series = _series(0)
    res = d.decay_series(series, as_of=_as_of(300))
    assert res.applied is False
    assert res.reason == "no_card"
    assert res.r is None
    assert res.p_effective == res.p_raw


def test_non_positive_stability_is_treated_as_no_history_not_as_forgotten():
    series = _series(0)
    res = d.decay_series(series, as_of=_as_of(300), stability=0.0)
    assert res.applied is False
    assert res.reason == "no_history"
    assert res.p_effective == res.p_raw != 0.0


def test_py_fsrs_returns_zero_for_historyless_card_which_is_why_we_guard():
    """把坑本身固定下来：**这是我们的理由，不是我们的实现**。

    若哪天 py-fsrs 改成返回 None，这条会红——那时该回头检查守卫是不是还必要，
    而不是把断言改绿。
    """
    from fsrs import Card, Scheduler

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    got = Scheduler(enable_fuzzing=False).get_card_retrievability(
        Card(due=now), now + timedelta(days=30)
    )
    assert got == 0       # 不是 None


# --- ⑤ 不改 series ------------------------------------------------------------

def test_decay_does_not_touch_the_series():
    series = _series(0, 3)
    before = series.to_dict()
    d.decay_series(series, as_of=_as_of(60), stability=5.0)
    assert series.to_dict() == before


def test_two_as_of_values_read_two_p_effs_from_one_series():
    series = _series(0)
    near = d.decay_series(series, as_of=_as_of(1), stability=10.0)
    far = d.decay_series(series, as_of=_as_of(100), stability=10.0)
    assert near.p_raw == far.p_raw
    assert near.p_effective > far.p_effective


# --- ⑥ 退化路径给原因，不给 None ----------------------------------------------

def test_empty_series_is_no_history_not_zero_p():
    empty = m.replay([], user_id="u", skill_id=MSP)
    res = d.decay_series(empty, as_of=_as_of(10), stability=10.0)
    assert res.applied is False
    assert res.reason == "no_history"
    assert empty.p is None          # 序列本身仍然说"没有证据"，没被抹成 0


def test_reason_vocabulary_is_closed():
    assert set(d.FALLBACK_REASONS) == {"no_card", "no_history", "not_later"}
    # applied 也是合法取值，但它不是"退化"，所以单独校验：不在词表里的只可能是 applied
    res = d.decay_series(_series(0), as_of=_as_of(3), stability=10.0)
    assert res.reason == "applied"


# --- 单调性（B-08b 的做完判据） ------------------------------------------------

def test_p_is_monotone_non_increasing_in_as_of():
    series = _series(0, 2)
    values = [d.decay_series(series, as_of=_as_of(day), stability=8.0).p_effective
              for day in (2, 3, 7, 30, 90, 365, 1000)]
    assert all(a >= b for a, b in zip(values, values[1:])), values
    assert values[0] > values[-1]


def test_gap_of_one_day_differs_from_gap_of_300_days_at_the_same_as_of():
    """同一个 `as_of`，一次隔 1 天练过、一次隔 300 天没碰，P 必须不同。"""
    fresh = d.decay_p(0.9, as_of=_as_of(301), last_evidence_at=_as_of(300), stability=10.0)
    stale = d.decay_p(0.9, as_of=_as_of(301), last_evidence_at=_as_of(1), stability=10.0)
    assert fresh.p_effective > stale.p_effective
    assert fresh.p_effective != pytest.approx(stale.p_effective)


# --- 外部对照：不自己跟自己比 --------------------------------------------------

@pytest.mark.parametrize("days", [0, 1, 7, 30, 300])
def test_retrievability_matches_py_fsrs_directly(days):
    from fsrs import Card, Scheduler, State

    anchor = datetime(2026, 1, 1, tzinfo=timezone.utc)
    card = Card(due=anchor, stability=12.5, difficulty=5.0, last_review=anchor,
                state=State.Review)
    expected = Scheduler(enable_fuzzing=False).get_card_retrievability(
        card, anchor + timedelta(days=days)
    )
    assert d.retrievability(12.5, days) == pytest.approx(expected)


@pytest.mark.parametrize("bad", [0, -1.0])
def test_non_positive_stability_is_rejected_by_the_low_level_function(bad):
    with pytest.raises(d.DecayError, match="stability 必须为正"):
        d.retrievability(bad, 1.0)


def test_negative_elapsed_is_rejected_by_the_low_level_function():
    with pytest.raises(d.DecayError, match="不能为负"):
        d.retrievability(10.0, -0.5)


# --- 与调度层对账：同一张卡、同一个时刻，两处算出的 R 必须相同 -------------------

def test_scheduler_and_decay_agree_on_the_same_card():
    """调度层存的卡与衰减读的卡是**同一张**，R 不许出现两个版本。"""
    from arena import scheduler as sch

    card = sch.new_card("u", MSP, at="2026-01-01T00:00:00Z")
    card, _ = sch.review(card, 0.9, at="2026-02-01T00:00:00Z")
    later = "2026-03-01T00:00:00Z"
    sched_r = sch.retrievability_at(card, later)
    ours = d.decay_p(
        1.0, as_of=later,
        last_evidence_at=card.last_review.isoformat(),
        last_review=card.last_review.isoformat(),
        stability=card.stability,
    )
    assert ours.r == pytest.approx(sched_r)
    assert ours.applied is True


def test_scheduler_returns_none_for_a_card_that_never_started():
    """这条语义是"全项目只此一处"的核心：没开始 ⇒ None，不是 0。"""
    from arena import scheduler as sch

    fresh = sch.new_card("u", MSP, at="2026-01-01T00:00:00Z")
    assert fresh.stability is None
    assert sch.retrievability_at(fresh, "2026-02-01T00:00:00Z") is None


# --- 端到端：从库里读 P 并按 as_of 衰减（mastery.decayed_at） --------------------

@pytest.fixture()
def db(tmp_path):
    from arena import store

    conn = store.connect(tmp_path / "decay.db")
    store.init_db(conn)
    store.register_skills(conn)
    store.register_patterns(conn)
    store.register_task(conn, ROOT / "tasks" / "dbg-001-order-report.yaml")
    yield conn
    conn.close()


def _seed(db, *, with_card: bool, at: str = "2026-01-01T00:00:00Z") -> None:
    """落一份真提交；`with_card` 决定有没有卡片历史。"""
    import copy

    from arena import scheduler as sch
    from arena import store
    from arena.trace_pipeline import run_fixture

    # 用 raw-perfect 而不是 raw-empty：白卷那份在 msp 上一条计入断言都没有
    # （门禁不合格 ⇒ R1/R2 被标 blocked），读出来 P 是 None，测不到衰减。
    report = copy.deepcopy(run_fixture("raw-perfect"))
    report["submission"]["submission_id"] = "sub-1"
    report["submission"]["evaluated_at"] = at
    store.save_submission(db, report, user_id="u", created_at=at)

    if with_card:
        card = sch.new_card("u", MSP, at=at)
        card, _ = sch.review(card, 0.4, at=at)       # 一次"没考好"，S 不会太长
        store.save_fsrs_cards(db, "u", {MSP: card.to_dict()})


def test_decayed_at_reads_p_from_db_and_applies_the_curve(db):
    _seed(db, with_card=True)
    series, res = m.decayed_at(db, "u", MSP, as_of="2026-03-01T00:00:00Z", lam=0.3)
    assert series.p == pytest.approx(1.0)          # 一次满分：初值播种 P_1 = c_1
    assert res.applied is True
    assert res.p_effective < series.p
    assert res.p_effective == pytest.approx(series.p * res.r)


def test_decayed_at_without_a_card_keeps_p_and_says_no_card(db):
    """从没失败过 ⇒ 没有卡 ⇒ **不衰减**，而不是"忘光了"。"""
    _seed(db, with_card=False)
    series, res = m.decayed_at(db, "u", MSP, as_of="2026-03-01T00:00:00Z")
    assert series.p == pytest.approx(1.0)
    assert res.applied is False
    assert res.reason == "no_card"
    assert res.p_effective == series.p


def test_decayed_at_rejects_as_of_before_the_last_submission(db):
    _seed(db, with_card=True)
    with pytest.raises(d.DecayError, match="早于锚点"):
        m.decayed_at(db, "u", MSP, as_of="2025-12-31T00:00:00Z")


def test_decayed_at_is_pure_read_only(db):
    """读一次不许改库：衰减是"读法"，不是新状态。"""
    from arena import store

    _seed(db, with_card=True)
    before = (store.load_fsrs_cards(db, "u"), len(store.list_review_items(db, "u")))
    m.decayed_at(db, "u", MSP, as_of="2026-03-01T00:00:00Z")
    after = (store.load_fsrs_cards(db, "u"), len(store.list_review_items(db, "u")))
    assert before == after


# --- 成绩单：能给"现在"这一列，且不传 --as-of 时行为不变 ------------------------

def test_scorecard_without_as_of_says_nothing_about_decay(tmp_path):
    import subprocess

    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "mastery_scorecard.py")],
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    head = out.stdout.split("说明：")[0]
    assert "衰减" not in head                    # 表头里不出现衰减列
    assert "掌握度(原始)" not in head             # 也不出现"原始/现在"两列
    assert "现在(" not in head


def test_scorecard_with_as_of_says_which_skills_have_no_card(tmp_path):
    import subprocess

    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "mastery_scorecard.py"),
         "--as-of", "2026-12-31T00:00:00Z"],
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert "现在(2026-12-31)" in out.stdout
    # 内置演示数据不建卡 ⇒ 三行都该写"未衰减：no_card"，而不是编一个衰减数
    assert out.stdout.count("未衰减：no_card") >= 3
