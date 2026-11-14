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
    """`raw-no-trace`：答案对、过程全缺。

    multi_step_planning 是 pass(2) ⇒ c=1.0；failure_retry / tool_param_validation
    各有一条 `unscored` ⇒ **没有证据**。若把 unscored 记 0 分，
    这两个技能会被判成"完全不会"，而事实是"没上报过程，无法判定"。
    """
    report = _report("raw-no-trace")
    assert m.skill_evidence(report, MSP).c == 1.0
    for skill in (FR, TPV):
        ev = m.skill_evidence(report, skill)
        assert not ev.counts
        assert ev.ignored_reasons() == {"not_counted": 1}


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


def test_gate_status_is_recorded_but_does_not_change_p():
    """A2 护栏（round-81）：门禁状态**留痕但不参与计算**，切换成本才是低的。

    现行口径是 A（证据照记）。若将来切 C（降权）或 B（排除），只需在计入层按
    `gate_status` 过滤——但前提是历史每一步都记了它，否则只能追溯改历史。
    这里同时锁住：记录不影响 P（口径没变），且拼错的状态名会报错而不是静默溜过。
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


# --- ④ 衰减：尚未实现（现状锁，B-08 反转） ----------------------------------------

def test_decay_is_not_implemented_yet():
    """现状锁：间隔一天与间隔三百天，P 完全一样。

    data-model §5.2 说「衰减只沿用复习间隔这一个时间轴」，而复习间隔来自 ReviewItem（B-08）。
    在它落地前 P **不会**随自然时间下降。这条测试存在的意义是：等 B-08 做完，
    它会红——那时把断言反过来，就等于"衰减已接上"的实证。**不许悄悄改掉这条测试。**
    """
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    def series_with_gap(days: int) -> m.MasterySeries:
        ev = [
            _ev("s1", base.isoformat(), 1, 1),
            _ev("s2", (base + timedelta(days=days)).isoformat(), 1, 1),
        ]
        return m.replay(ev, user_id=USER, skill_id=MSP, lam=0.3)

    assert series_with_gap(1).p == series_with_gap(300).p == pytest.approx(1.0)


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
    """`raw-no-trace` 在 failure_retry 上只有 unscored：系列为空、P 为 None。"""
    store.save_submission(db, _report("raw-no-trace", submission_id="nt", at="2026-11-01T00:00:00Z"),
                          user_id=USER)
    series = m.build_series(db, USER, FR, lam=0.3)
    assert series.p is None
    assert len(series.skipped) == 1
    assert series.ignored == {"not_counted": 1}


def test_p_is_not_written_anywhere(db):
    """P 还没有落字段（data-model「P 的落字段与初值未定」）。

    这条锁的是"别偷偷加一张表/一列"：算完前后 `user_skill_states` 的列集合与行数都不许变。
    """
    store.save_submission(db, _report("raw-perfect", submission_id="s1", at="2026-11-01T00:00:00Z"),
                          user_id=USER)
    cols_before = [r[1] for r in db.execute("PRAGMA table_info(user_skill_states)")]
    m.build_series(db, USER, MSP, lam=0.3)
    cols_after = [r[1] for r in db.execute("PRAGMA table_info(user_skill_states)")]
    assert cols_before == cols_after
    assert db.execute("SELECT COUNT(*) FROM user_skill_states").fetchone()[0] == 0


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
