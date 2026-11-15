"""排程层的约束测试（round-85，B-09）。

锁的是 `src/arena/schedule_advisor.py` 文件头那六条决定。最核心的一条是这个文件里
唯一"手推一遍"的算例：五条同一批到期的复习项，因子值、算式、期望名次全部写死在测试里。
它同时也是 B-09 的做完判据——「给定 5 个 ReviewItem 的 due 集合，排序结果与手推一致」。

另外三类：
  ① 缺数据的三条处置（弃权 / 同批均值顶 / 整批没有就退出）——**都不许变成 0 分**；
  ② 改序必须留痕且必须给理由（空理由、重复改、越界、不存在，五种拒绝）；
  ③ 从库里取值的那一层（错误热度窗口、提交粒度计数、未复习卡不编 R、只读不写库）。
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

from arena import mastery  # noqa: E402
from arena import schedule_advisor as adv  # noqa: E402
from arena import scheduler as sch  # noqa: E402
from arena import store  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402

TASK_YAML = ROOT / "tasks" / "dbg-001-order-report.yaml"
USER = "user-advice"
AT = "2026-11-15T00:00:00Z"

MSP = "multi_step_planning"
FR = "failure_retry"
TPV = "tool_param_validation"


def item(review_id: str, skill_id: str, due: str = "2026-11-14T00:00:00+00:00",
         *, kind: str = "variant", user: str = USER) -> dict:
    return {
        "review_id": review_id,
        "user_id": user,
        "skill_id": skill_id,
        "kind": kind,
        "task_id": None,
        "source_submission_id": "sub-x",
        "due": due,
        "status": "pending",
        "created_at": "2026-11-01T00:00:00+00:00",
    }


@pytest.fixture()
def db(tmp_path):
    conn = store.connect(tmp_path / "advice.db")
    store.init_db(conn)
    store.register_skills(conn)
    store.register_patterns(conn)
    store.register_task(conn, TASK_YAML)
    yield conn
    conn.close()


def _report(name: str, submission_id: str, at: str) -> dict:
    report = copy.deepcopy(run_fixture(name))
    report["submission"]["submission_id"] = submission_id
    report["submission"]["evaluated_at"] = at
    return report


def _submit(conn, name: str, submission_id: str, at: str) -> dict:
    """交一份并落库 + 走一次调度（复习项与卡片都进库）。"""
    report = _report(name, submission_id, at)
    store.save_submission(conn, report, user_id=USER, created_at=at)
    result = sch.schedule_submission(
        report, user_id=USER, at=at,
        cards=sch.cards_from_rows(store.load_fsrs_cards(conn, USER)),
        task_has_fault=True,
    )
    store.save_schedule(conn, result)
    return result


def _snapshot(conn) -> dict[str, list]:
    tables = ("users", "review_items", "user_skill_states", "submissions")
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()] for t in tables}


# ---------------------------------------------------------------------------
# ① 五条手推算例（B-09 的做完判据）
# ---------------------------------------------------------------------------


def test_five_items_match_hand_calculation():
    """手推一遍，排序必须一模一样。

    全批都没有截止数据 ⇒ 截止因子整批退出，权重归一化到 (遗忘 4 + 热度 2 + 缺口 1) = 7。
    错误热度按本批最高（B 的 4 次）归一化。

        A  P=.60 R=.80 heat 1  →  (4×.20 + 2×.25 + 1×.40)/7 = 1.70/7 = 0.2429
        B  P=.30 R=.50 heat 4  →  (4×.50 + 2×1.00 + 1×.70)/7 = 4.70/7 = 0.6714
        C  P=.90 R=.95 heat 0  →  (4×.05 + 2×.00 + 1×.10)/7 = 0.30/7 = 0.0429
        D  P=.50 R=.70 heat 2  →  (4×.30 + 2×.50 + 1×.50)/7 = 2.70/7 = 0.3857
        E  P=.40 R=.60 heat 1  →  (4×.40 + 2×.25 + 1×.60)/7 = 2.70/7 = 0.3857

    D 与 E 综合分**完全相同**（都是 2.70/7）⇒ 落同一平手格 ⇒ 按 due 升序：
    E 的 11-13 早于 D 的 11-14，所以 E 在前。期望顺序：B, E, D, A, C。
    """
    items = [
        item("rv-a", "sA", "2026-11-14T00:00:00+00:00"),
        item("rv-b", "sB", "2026-11-13T00:00:00+00:00"),
        item("rv-c", "sC", "2026-11-12T00:00:00+00:00"),
        item("rv-d", "sD", "2026-11-14T00:00:00+00:00"),
        item("rv-e", "sE", "2026-11-13T00:00:00+00:00"),
    ]
    result = adv.advise(
        items, user_id=USER, at=AT,
        p_by_skill={"sA": 0.60, "sB": 0.30, "sC": 0.90, "sD": 0.50, "sE": 0.40},
        r_by_skill={"sA": 0.80, "sB": 0.50, "sC": 0.95, "sD": 0.70, "sE": 0.60},
        error_counts={"sA": 1, "sB": 4, "sC": 0, "sD": 2, "sE": 1},
        cap=2,
    )
    assert [j.review_id for j in result.judged] == ["rv-b", "rv-e", "rv-d", "rv-a", "rv-c"]
    assert [j.rank for j in result.judged] == [1, 2, 3, 4, 5]

    scores = {j.review_id: round(j.score, 4) for j in result.judged}
    assert scores == {"rv-b": 0.6714, "rv-e": 0.3857, "rv-d": 0.3857,
                      "rv-a": 0.2429, "rv-c": 0.0429}
    # 今日视图只截断，不删数据
    assert result.today() == ("rv-b", "rv-e")
    assert result.deferred() == ("rv-d", "rv-a", "rv-c")
    assert len(result.judged) == 5


def test_factors_are_always_four_in_priority_order():
    """每条判决都带齐四个因子（含弃权的），顺序与权重必须沿优先级递减。

    弃权的因子也要出现在理由里——否则"为什么没考虑截止"就没人答得上来。
    """
    result = adv.advise([item("rv-1", "s1")], user_id=USER, at=AT, p_by_skill={"s1": 0.5})
    factors = result.judged[0].factors
    assert [f.name for f in factors] == list(adv.FACTOR_ORDER)
    weights = [f.weight for f in factors]
    assert all(a > b for a, b in zip(weights, weights[1:]))
    # 单条成批、只给了 P：另外三个因子在这一批里都没有数据 ⇒ 一起退出
    assert [f.name for f in factors if f.value is None] == [
        "deadline", "forgetting", "error_heat"
    ]


# ---------------------------------------------------------------------------
# ② 缺数据：弃权 / 均值顶 / 退出，都不许变成 0
# ---------------------------------------------------------------------------


def test_deadline_abstains_and_does_not_change_order():
    """没有截止数据时，因子弃权而不是记 0；加/不加空表对顺序零影响。"""
    items = [item("rv-1", "s1", "2026-11-12T00:00:00+00:00"),
             item("rv-2", "s2", "2026-11-14T00:00:00+00:00")]
    kwargs = dict(user_id=USER, at=AT, p_by_skill={"s1": 0.2, "s2": 0.8},
                  r_by_skill={"s1": 0.5, "s2": 0.9}, error_counts={"s1": 1, "s2": 0})
    without = adv.advise(items, **kwargs)
    with_empty = adv.advise(items, deadlines={}, **kwargs)
    assert "deadline" in without.abstained
    assert without.order == with_empty.order
    assert without.judged[0].factors[0].value is None
    assert "没有这项截止日期" in without.judged[0].factors[0].detail


def test_missing_factor_for_one_item_is_filled_with_batch_mean():
    """只有一条没有截止日期时，用同批均值顶，并标 `imputed` + 写进理由。

    为什么不重归一化：重归一化会让缺数据的项**少一个分母**，于是"数据越少分越高"。
    为什么不记 0：那是把"未知"说成"不急着做"（决策 7 的同一个错误方向）。
    """
    items = [item("rv-a", "sA"), item("rv-b", "sB"), item("rv-c", "sC")]
    at = datetime(2026, 11, 15, tzinfo=timezone.utc)
    result = adv.advise(
        items, user_id=USER, at=AT,
        p_by_skill={"sA": 0.5, "sB": 0.5, "sC": 0.5},
        deadlines={
            "sA": at.isoformat(),                                       # 已到截止 ⇒ 1.0
            "sB": (at + timedelta(days=7)).isoformat(),                 # 视界外 ⇒ 0.0
            # sC 没有截止
        },
    )
    deadline = {j.skill_id: j.factors[0] for j in result.judged}
    assert deadline["sA"].value == 1.0 and not deadline["sA"].imputed
    assert deadline["sB"].value == 0.0 and not deadline["sB"].imputed
    assert deadline["sC"].imputed is True
    assert deadline["sC"].value == pytest.approx(0.5)          # (1.0 + 0.0) / 2
    assert "按同批平均 0.50 顶" in deadline["sC"].detail
    assert result.judged[0].skill_id == "sA"                   # 截止最紧的仍排第一


def test_factor_with_no_data_at_all_drops_out_of_the_denominator():
    """整批都没有某个因子的数据 ⇒ 该因子退出、权重归一化（不是给所有人记 0）。

    这两种做法的区别在**量级**上：记 0 会把综合分整体压到 7/15，
    于是"有没有截止数据"这件事会改变分数但**不改变顺序**——正是最容易被忽略的那种错。
    """
    result = adv.advise([item("rv-1", "s1")], user_id=USER, at=AT,
                        p_by_skill={"s1": 0.5}, r_by_skill={"s1": 0.75},
                        error_counts={"s1": 1})
    # (4×0.25 + 2×1.0 + 1×0.5) / 7 = 3.5/7
    assert result.judged[0].score == pytest.approx(0.5)
    assert result.abstained == ("deadline",)
    # 覆盖度只算实测部分：15 的权重里只有 7 有实测
    assert result.judged[0].confidence == pytest.approx(7 / 15)


def test_unseen_skill_gets_no_mastery_value_rather_than_zero():
    """P 为空 ⇒ 掌握度缺口弃权（细节写明"不记 0"），不是"缺口 = 1"。"""
    result = adv.advise([item("rv-1", "s1")], user_id=USER, at=AT)
    gap = [f for f in result.judged[0].factors if f.name == "mastery_gap"][0]
    assert gap.value is None
    assert "P 为空" in gap.detail
    assert "mastery_gap" in result.abstained


def test_item_without_its_own_data_is_imputed_midpack_and_labelled():
    """个别项没数据 ⇒ 均值顶到"平均紧急度"，**不踢到最后**，但理由里必须写明是顶出来的。

    这一条锁的是文件头第 3 条的推论：把"没数据"一律排最后，等于用"我们不知道"
    去惩罚一条可能最该做的项。`rv-blind` 的 P / R 都是顶的（`imputed=True`），
    错误热度 0 则是实测（"近 7 天没在这上面栽过"是真信息，不是缺数据）。
    """
    items = [item("rv-blind", "sX"), item("rv-known", "sY")]
    result = adv.advise(items, user_id=USER, at=AT,
                        p_by_skill={"sY": 0.1}, r_by_skill={"sY": 0.2},
                        error_counts={"sY": 3})
    assert result.order == ("rv-known", "rv-blind")
    blind = result.by_id("rv-blind")
    assert blind.score is not None
    imputed = {f.name: f.imputed for f in blind.factors}
    assert imputed == {"deadline": False, "forgetting": True,
                       "error_heat": False, "mastery_gap": True}
    assert "按同批平均" in blind.summary
    # 只有错误热度（权重 2）是实测，其余靠顶 ⇒ 覆盖度 2/15
    assert blind.confidence == pytest.approx(2 / 15)


def test_batch_without_any_factor_data_falls_back_to_due_order():
    """整批四个因子都没数据时，只能按到期先后——且理由直说是缺口，不装作"它最不急"。"""
    items = [item("rv-late", "sX", "2026-11-15T00:00:00+00:00"),
             item("rv-early", "sY", "2026-11-09T00:00:00+00:00")]
    result = adv.advise(items, user_id=USER, at=AT)
    assert result.order == ("rv-early", "rv-late")
    assert all(j.score is None for j in result.judged)
    assert all("这是缺口，不是结论" in j.summary for j in result.judged)
    assert result.abstained == ("deadline", "forgetting", "error_heat", "mastery_gap")


# ---------------------------------------------------------------------------
# ③ 排序细节：平手格、确定性、上限、坏输入
# ---------------------------------------------------------------------------


def test_score_difference_within_grid_is_a_tie_and_due_wins():
    """分差小于 0.02 视作平手，改按 due 先后——不拿浮点噪声当结论。

    这里 A 的分略高（0.500 对 0.495），但 A 到期更晚，所以 B 先。若把平手格去掉，
    A 会排第一：那是"0.005 的差"决定顺序，属于假精度。
    """
    items = [item("rv-a", "sA", "2026-11-14T00:00:00+00:00"),
             item("rv-b", "sB", "2026-11-10T00:00:00+00:00")]
    result = adv.advise(items, user_id=USER, at=AT,
                        p_by_skill={"sA": 0.500, "sB": 0.505})
    a = result.by_id("rv-a").score
    b = result.by_id("rv-b").score
    assert a > b                       # A 的分确实更高
    assert a - b < adv.TIE_GRID        # 但落在同一平手格
    assert result.order == ("rv-b", "rv-a")


def test_identical_input_gives_identical_output():
    """同一输入两次跑，名次、分数、理由逐字相同（排程要能被复核）。"""
    items = [item("rv-1", "s1"), item("rv-2", "s2"), item("rv-3", "s3")]
    kwargs = dict(user_id=USER, at=AT,
                  p_by_skill={"s1": 0.4, "s2": 0.4, "s3": 0.4},
                  r_by_skill={"s1": 0.7, "s2": 0.7, "s3": 0.7},
                  error_counts={"s1": 1, "s2": 1, "s3": 1})
    first = adv.advise(items, **kwargs).to_dict()
    second = adv.advise(items, **kwargs).to_dict()
    assert first == second


def test_unfinished_and_future_items_are_not_in_the_plan():
    """只排"今天到期且 pending"的项：未到期的、已完成的都不进建议（但也没有被删）。"""
    items = [
        item("rv-due", "s1", "2026-11-15T00:00:00+00:00"),
        item("rv-future", "s2", "2026-12-01T00:00:00+00:00"),
        {**item("rv-done", "s3"), "status": "done"},
    ]
    result = adv.advise(items, user_id=USER, at=AT)
    assert result.order == ("rv-due",)
    assert len(items) == 3  # 输入列表没被改动


def test_cap_zero_is_today_nothing_to_do():
    result = adv.advise([item("rv-1", "s1")], user_id=USER, at=AT, p_by_skill={"s1": 0.5}, cap=0)
    assert result.today() == ()
    assert result.deferred() == ("rv-1",)


@pytest.mark.parametrize("bad", [-1, 1.5, True, "2"])
def test_bad_cap_rejected(bad):
    with pytest.raises(adv.AdvisorError):
        adv.advise([item("rv-1", "s1")], user_id=USER, at=AT, cap=bad)


def test_item_from_another_user_is_rejected():
    """混批排程会算出"别人的复习项排我前面"，这种错无法在结果里看出来，只能在入口拒绝。"""
    with pytest.raises(adv.AdvisorError, match="不属于"):
        adv.advise([item("rv-1", "s1", user="someone-else")], user_id=USER, at=AT)


@pytest.mark.parametrize("bad", [1.2, -0.1, "0.5", True])
def test_out_of_range_factor_value_rejected(bad):
    with pytest.raises(adv.AdvisorError):
        adv.advise([item("rv-1", "s1")], user_id=USER, at=AT, p_by_skill={"s1": bad})


@pytest.mark.parametrize("weights", [
    {"deadline": 4.0, "forgetting": 4.0, "error_heat": 2.0, "mastery_gap": 1.0},  # 不递减
    {"deadline": 8.0, "forgetting": 4.0, "error_heat": 0.0, "mastery_gap": 1.0},  # 零权重
    {"deadline": 8.0, "forgetting": 4.0},                                        # 缺因子
])
def test_bad_weights_rejected(weights):
    with pytest.raises(adv.AdvisorError):
        adv.advise([item("rv-1", "s1")], user_id=USER, at=AT, weights=weights)


# ---------------------------------------------------------------------------
# ④ 改序：必须留痕、必须给理由、不许悄悄少一条
# ---------------------------------------------------------------------------


def _advice5() -> adv.Advice:
    items = [item(f"rv-{i}", f"s{i}", f"2026-11-1{i}T00:00:00+00:00") for i in range(1, 6)]
    return adv.advise(
        items, user_id=USER, at=AT,
        p_by_skill={f"s{i}": 0.5 + 0.05 * i for i in range(1, 6)},
        r_by_skill={f"s{i}": 0.5 + 0.05 * i for i in range(1, 6)},
        cap=2,
    )


def test_override_moves_item_and_records_the_delta():
    advice = _advice5()
    last = advice.order[-1]
    plan = adv.apply_overrides(advice, [adv.Override(last, 1, "今天只来得及看它", AT)])
    assert plan.order[0] == last
    audit = plan.audits[0]
    assert (audit.suggested_rank, audit.final_rank, audit.delta) == (5, 1, 4)
    assert audit.moved is True
    assert "提前 4 位" in audit.text()
    assert [a.review_id for a in plan.deviations()] == [last]


def test_override_keeps_the_set_intact():
    """改序不是删项：五条进去，五条出来，只是顺序变了。"""
    advice = _advice5()
    plan = adv.apply_overrides(advice, [adv.Override(advice.order[3], 1, "先做这条", AT)])
    assert sorted(plan.order) == sorted(advice.order)
    assert len(plan.order) == 5


def test_noop_override_is_audited_but_is_not_a_deviation():
    """点了却没挪动也要留痕（否则"我明明点过"无从对账），但不算 deviation。"""
    advice = _advice5()
    first = advice.order[0]
    plan = adv.apply_overrides(advice, [adv.Override(first, 1, "确认一下它确实最急", AT)])
    assert plan.order == advice.order
    assert len(plan.audits) == 1
    assert plan.deviations() == ()
    assert "保持第 1 位" in plan.audits[0].text()


def test_plan_today_and_deferred_respect_cap_after_reorder():
    advice = _advice5()          # cap = 2
    plan = adv.apply_overrides(advice, [adv.Override(advice.order[4], 2, "这条拖太久了", AT)])
    assert plan.today() == (advice.order[0], advice.order[4])
    assert len(plan.deferred()) == 3


@pytest.mark.parametrize("override, match", [
    (adv.Override("rv-nope", 1, "理由", AT), "没有"),
    (adv.Override("rv-1", 1, "   ", AT), "没写理由"),
    (adv.Override("rv-1", 0, "理由", AT), "越界"),
    (adv.Override("rv-1", 9, "理由", AT), "越界"),
    (adv.Override("rv-1", 1.0, "理由", AT), "整数"),
])
def test_bad_override_rejected(override, match):
    with pytest.raises(adv.AdvisorError, match=match):
        adv.apply_overrides(_advice5(), [override])


def test_same_item_overridden_twice_rejected():
    """同一条两条指令冲突时不做取舍——静默取后者会让用户以为前一条也生效了。"""
    advice = _advice5()
    with pytest.raises(adv.AdvisorError, match="两次"):
        adv.apply_overrides(advice, [
            adv.Override(advice.order[0], 3, "往后放", AT),
            adv.Override(advice.order[0], 1, "还是放前面", AT),
        ])


def test_append_audit_writes_one_json_line_per_audit(tmp_path):
    advice = _advice5()
    plan = adv.apply_overrides(advice, [
        adv.Override(advice.order[4], 1, "先做这条", AT),
        adv.Override(advice.order[0], 5, "这两条已经会了", AT),
    ])
    path = tmp_path / "audit.jsonl"
    assert adv.append_audit(path, plan) == 2
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert [x["final_rank"] for x in lines] == [1, 5]
    assert all(x["kind"] == "override" and x["user_id"] == USER for x in lines)
    assert lines[0]["reason"] == "先做这条"


def test_append_audit_is_a_noop_when_nothing_was_overridden(tmp_path):
    path = tmp_path / "audit.jsonl"
    plan = adv.apply_overrides(_advice5(), [])
    assert adv.append_audit(path, plan) == 0
    assert not path.exists()


# ---------------------------------------------------------------------------
# ⑤ 从库里取因子
# ---------------------------------------------------------------------------


def test_error_heat_counts_submissions_not_patterns(db):
    """一份提交同时踩两个同类错，对该技能只算 1 次。

    `raw-fake-retry` 同时检出 `wrong_valid_count` + `wrong_total_amount`，两者都映射到
    `multi_step_planning`。按模式计数会让它拿 2 次，而"同一份作业踩两个坑"不等于练了两次。
    """
    _submit(db, "raw-fake-retry", "s1", "2026-11-10T00:00:00+00:00")
    counts = adv._error_counts(store.user_reports(db, USER), store.pattern_skill_map(db),
                               at=mastery.parse_instant(AT), window_days=7)
    assert counts[MSP] == 1
    assert counts[FR] == 1
    assert TPV not in counts or counts[TPV] == 0


def test_error_heat_window_is_inclusive_and_ignores_future(db):
    """窗口是闭区间 [at-7d, at]；晚于 `at` 的提交不算（回看历史不许用未来数据）。"""
    _submit(db, "raw-fake-retry", "s1", "2026-11-10T00:00:00+00:00")
    _submit(db, "raw-empty", "s2", "2026-11-20T00:00:00+00:00")
    reports = store.user_reports(db, USER)
    mapping = store.pattern_skill_map(db)

    inside = adv._error_counts(reports, mapping, at=mastery.parse_instant("2026-11-17T00:00:00Z"),
                               window_days=7)          # 窗口起点正好 11-10
    assert inside[FR] == 1

    outside = adv._error_counts(reports, mapping, at=mastery.parse_instant("2026-11-18T00:00:00Z"),
                                window_days=7)         # 窗口起点 11-11，11-10 那条退出
    assert FR not in outside or outside[FR] == 0

    as_of_15th = adv._error_counts(reports, mapping, at=mastery.parse_instant(AT), window_days=7)
    assert as_of_15th[FR] == 1                          # 11-20 的那条还没发生，不计


def test_never_reviewed_card_produces_no_retrievability(db):
    """没复习过的卡没有 R，不许把 py-fsrs 的 0.0 当成"已经忘光"。

    py-fsrs 对 `stability is None` 的卡返回 0.0；若直接拿来算"遗忘风险 = 1 - R"，
    一条刚建、还没复习的项会拿到满格风险——那是把"还没开始"说成"已经忘完"。
    """
    store.ensure_user(db, USER)
    card = sch.new_card(USER, MSP, at=AT).to_dict()
    store.save_fsrs_cards(db, USER, {MSP: card})
    r, last = adv._retrievability(db, USER, [MSP], mastery.parse_instant(AT))
    assert r[MSP] is None and last[MSP] is None


def test_advise_from_store_reads_three_real_factors(db):
    """端到端：库里两条提交 → 建议里的 P / R / 热度都能对回去。"""
    _submit(db, "raw-fake-retry", "s1", "2026-11-10T00:00:00+00:00")
    _submit(db, "raw-empty", "s2", "2026-11-12T00:00:00+00:00")
    assert store.list_review_items(db, USER, status="pending"), "先得有复习项才谈排程"

    advice = adv.advise_from_store(db, USER, at=AT)
    assert advice.judged, "应该有到期的 pending 项"
    assert advice.abstained == ("deadline",), "库里没有截止数据，该因子必须弃权"

    for judgment in advice.judged:
        gap = [f for f in judgment.factors if f.name == "mastery_gap"][0]
        expected_p = mastery.build_series(db, USER, judgment.skill_id).p
        if expected_p is None:
            assert gap.value is None
        else:
            assert gap.value == pytest.approx(1.0 - expected_p, abs=1e-9)
        heat = [f for f in judgment.factors if f.name == "error_heat"][0]
        assert heat.value is not None, "两条提交都在窗口内，热度不该弃权"


def test_advise_from_store_does_not_write_anything(db):
    """排程只读不写：四张表前后逐行相同。

    这一条比"我没写代码"更有说服力——将来谁在取因子时顺手写了个缓存，这里会红。
    """
    _submit(db, "raw-fake-retry", "s1", "2026-11-10T00:00:00+00:00")
    before = _snapshot(db)
    adv.advise_from_store(db, USER, at=AT)
    assert _snapshot(db) == before


def test_advise_from_store_on_empty_queue_returns_empty_advice(db):
    store.ensure_user(db, USER)   # 读上限需要用户存在；建用户是测试的事，不是排程的事
    advice = adv.advise_from_store(db, USER, at=AT)
    assert advice.judged == ()
    assert advice.today() == ()
    assert advice.cap == store.daily_review_cap(db, USER)


def test_advise_from_store_honours_deadlines_when_the_caller_has_them(db):
    """截止数据只能由调用方传（库里没有字段）；传了就必须真的进排序。"""
    _submit(db, "raw-fake-retry", "s1", "2026-11-10T00:00:00+00:00")
    at = datetime(2026, 11, 15, tzinfo=timezone.utc)
    advice = adv.advise_from_store(db, USER, at=AT, deadlines={MSP: at.isoformat()})
    assert advice.abstained != ("deadline",)
    assert advice.by_id(advice.order[0]).skill_id == MSP
    assert advice.judged[0].factors[0].value == 1.0
