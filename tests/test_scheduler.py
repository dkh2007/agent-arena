"""复习调度的约束测试（round-84，B-08）。

锁的是 `src/arena/scheduler.py` 文件头那七条决定，外加三条"能被一份红测试否掉"的边界：

  ① rating 映射的阈值是**闭区间右侧**且必须降序（顺序错会让整张表静默错档）；
  ② 调度**可复现**：关 fuzz、卡 id 与 due 全部确定性——否则"复核一遍"对不上账；
  ③ 队列只截断**视图**、不删数据（每日上限不是删除键）。

另外两条本轮新增的口径判断也在这儿被锁住（它们不是既有口径的推论，是我的判断）：
  - 「本次不算数」的提交（门禁不合格 / 版本不一致 / 缺过程）**不动卡、不建项**；
  - 旧的待复习项在"该技能做了一次有效提交"后标 `done`——否则队列只增不减。
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import scheduler as sch  # noqa: E402
from arena import store  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402

TASK_YAML = ROOT / "tasks" / "dbg-001-order-report.yaml"
VARIANT_YAML = ROOT / "tasks" / "ret-002-second-page-fault.yaml"
USER = "user-sched"
AT = "2026-11-15T00:00:00Z"

MSP = "multi_step_planning"
FR = "failure_retry"
TPV = "tool_param_validation"


def _report(name: str, *, submission_id: str | None = None, at: str | None = None) -> dict:
    report = copy.deepcopy(run_fixture(name))
    if submission_id is not None:
        report["submission"]["submission_id"] = submission_id
    if at is not None:
        report["submission"]["evaluated_at"] = at
    return report


@pytest.fixture()
def db(tmp_path):
    conn = store.connect(tmp_path / "sched.db")
    store.init_db(conn)
    store.register_skills(conn)
    store.register_patterns(conn)
    store.register_task(conn, TASK_YAML)
    yield conn
    conn.close()


# --- ① rating 映射：阈值边界与降序 -------------------------------------------------

@pytest.mark.parametrize("c, expected", [
    (0.0, 1), (0.3999, 1),
    (0.4, 2), (0.6999, 2),
    (0.7, 3), (0.8999, 3),
    (0.9, 4), (0.95, 4), (1.0, 4),
])
def test_rating_map_boundaries(c, expected):
    """阈值取闭区间右侧：`c == 0.9` 落 4（不是 3）。

    这一条把「差一档」的判定钉死。若把 `>=` 写成 `>`，`0.9 / 0.7 / 0.4` 三个点全会掉档，
    而它们在真实数据里恰恰是最常见的值（权重比往往就是 1/2、2/3、1/4 这类）。
    """
    assert sch.rating_for(c) == expected


@pytest.mark.parametrize("bad", [-0.0001, 1.0001, 2, -1, True, "0.5", None])
def test_rating_rejects_out_of_range_and_bool(bad):
    """越界报错、不 clamp；`True` 也不行——它是 `1`，会静默变成满分。"""
    with pytest.raises(sch.SchedulerError):
        sch.rating_for(bad)


def test_threshold_table_must_be_descending(monkeypatch):
    """阈值顺序写反（`(0.4,2)` 在 `(0.9,4)` 前面）⇒ 导入期就报错。

    真陷阱：`rating_for` 是"第一条命中即返回"，顺序反了会让所有 c≥0.4 都返回 2，
    而且**看起来有值、没有异常**——只有 4 档全测一遍才发现。
    """
    monkeypatch.setattr(sch, "RATING_THRESHOLDS", ((0.4, 2), (0.9, 4)))
    with pytest.raises(sch.SchedulerError, match="降序"):
        sch._check_thresholds()


def test_floor_must_be_smaller_than_every_rating(monkeypatch):
    """兜底档不能与任何阈值档同高——否则"全错"和"过 0.4"拿同一个评分。"""
    monkeypatch.setattr(sch, "RATING_FLOOR", 2)
    with pytest.raises(sch.SchedulerError, match="FLOOR"):
        sch._check_thresholds()


# --- ② 可复现：关 fuzz、卡 id 与 due 确定性 ---------------------------------------

def test_scheduler_is_deterministic_because_fuzz_is_off():
    """天级间隔上跑五次，`due` 只有一种取值。开了 fuzz 这条会红（实测五次四种）。

    **必须用天级间隔的卡做起点**：py-fsrs 的 fuzz 只在 `interval > 2.5 天` 时生效，
    拿新卡（1 分钟 learning step）去测，开不开 fuzz 结果都一样——那测不出问题，
    是个看着绿色、其实什么都没锁的测试。
    """
    card = sch.new_card(USER, MSP, at="2026-11-15T00:00:00Z")
    for at in ("2026-11-15T00:00:00Z", "2026-11-15T00:10:00Z", "2026-11-16T00:00:00Z"):
        card, _ = sch.review(card, 1.0, at=at)
    assert card.stability > 2.5, "前提：稳定性已进入天级，否则下面测不到 fuzz"

    dues = set()
    for _ in range(5):
        nxt, _ = sch.make_scheduler().review_card(
            card, sch.Rating(4), review_datetime=sch.mastery.parse_instant("2026-11-16T00:00:00Z"))
        dues.add(nxt.due)
    assert len(dues) == 1, f"关掉 fuzz 后 due 必须唯一，实际出现 {len(dues)} 种"


def test_card_id_is_stable_across_processes():
    """换一个 `PYTHONHASHSEED` 的子进程算出同一个 card_id——证明用的是 sha256 不是 `hash()`。

    内置 `hash()` 在同一进程内稳定，所以**同进程比两次测不出问题**（那是个假绿的测试）。
    只有跨进程才能锁住"重启后卡片还对得上"这件事。
    """
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "from arena import scheduler as s;"
        "c = s.new_card('u1', 'failure_retry', at='2026-11-15T00:00:00Z');"
        "print(c.card_id, c.due.isoformat())" % str(ROOT / "src")
    )
    outs = []
    for seed in ("0", "12345"):
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              encoding="utf-8", env={**os.environ, "PYTHONHASHSEED": seed})
        assert proc.returncode == 0, proc.stderr
        outs.append(proc.stdout.strip())
    assert outs[0] == outs[1], f"跨进程不一致：{outs}"


def test_review_rejects_backwards_time():
    """补录更早的提交时报错，而不是让 FSRS 算出一个看着合理的负间隔。"""
    card, _ = sch.review(sch.new_card(USER, MSP, at=AT), 0.5, at=AT)
    with pytest.raises(sch.SchedulerError, match="升序"):
        sch.review(card, 0.5, at="2026-11-14T00:00:00Z")


# --- ③ 队列：只截断视图，不删数据 --------------------------------------------------

def _item(rid: str, due: str, *, status: str = "pending") -> dict:
    return {"review_id": rid, "user_id": USER, "skill_id": MSP, "kind": "variant",
            "task_id": None, "source_submission_id": "s", "due": due, "status": status}


def test_take_daily_caps_without_dropping():
    """上限 2、到期 3 条 ⇒ 给 2 条、报 1 条被推迟；**没有任何东西被删**。"""
    items = [_item("a", "2026-11-15T00:00:00+00:00"),
             _item("b", "2026-11-15T01:00:00+00:00"),
             _item("c", "2026-11-15T02:00:00+00:00")]
    kept, deferred = sch.take_daily(items, at="2026-11-15T03:00:00Z", cap=2)
    assert [x["review_id"] for x in kept] == ["a", "b"]
    assert deferred == 1
    assert len(items) == 3, "入参不许被就地修改——被推迟的项还在库里"


def test_take_daily_excludes_future_and_finished():
    items = [_item("past", "2026-11-15T00:00:00+00:00"),
             _item("future", "2026-11-20T00:00:00+00:00"),
             _item("done", status="done", due="2026-11-15T00:00:00+00:00")]
    kept, deferred = sch.take_daily(items, at="2026-11-16T00:00:00Z", cap=5)
    assert [x["review_id"] for x in kept] == ["past"]
    assert deferred == 0


def test_take_daily_zero_cap_is_legal_and_negative_is_not():
    """「今天不复习」写 0；-1 是错输入，不是"不限"。"""
    items = [_item("a", "2026-11-15T00:00:00+00:00")]
    kept, deferred = sch.take_daily(items, at="2026-11-16T00:00:00Z", cap=0)
    assert kept == [] and deferred == 1
    with pytest.raises(sch.SchedulerError, match="不能为负"):
        sch.take_daily(items, at="2026-11-16T00:00:00Z", cap=-1)
    with pytest.raises(sch.SchedulerError, match="整数"):
        sch.take_daily(items, at="2026-11-16T00:00:00Z", cap=True)


def test_queue_order_parses_instants_instead_of_comparing_strings():
    """`+08:00` 的 12:00（=04:00Z）早于 `Z` 的 06:00。字符串比较会判反。

    根因与 `mastery.replay` 同一条：混偏移的 ISO 串直接比字典序，结果**看着有顺序但错了**。
    """
    items = [_item("z-six", "2026-11-15T06:00:00+00:00"),
             _item("plus8-twelve", "2026-11-15T12:00:00+08:00")]
    kept, _ = sch.take_daily(items, at="2026-11-15T07:00:00Z", cap=5)
    assert [x["review_id"] for x in kept] == ["plus8-twelve", "z-six"]


# --- 谁该建复习项：三条排除 -------------------------------------------------------

def test_perfect_run_creates_no_item_but_updates_cards():
    """全过 ⇒ 0 个复习项，但**卡片照样更新**（做题即复习，间隔往后推）。"""
    result = sch.schedule_submission(_report("raw-perfect"), user_id=USER, at=AT)
    assert result.items == ()
    assert set(result.cards) == {MSP, FR, TPV}
    assert {d.action for d in result.decisions} == {"passed"}
    for decision in result.decisions:
        assert decision.rating == 4


def test_failed_run_creates_one_item_per_gapped_skill():
    """`raw-fake-retry`：planning 0/2、retry 0/1 都建项；param 1/1 不建。"""
    result = sch.schedule_submission(_report("raw-fake-retry"), user_id=USER, at=AT)
    assert {i.skill_id for i in result.items} == {MSP, FR}
    actions = {d.skill_id: d.action for d in result.decisions}
    assert actions == {MSP: "review", FR: "review", TPV: "passed"}
    for item in result.items:
        assert item.status == "pending"
        assert item.source_submission_id == "sub-demo-fake-retry"
        assert item.due > result.at[:19], "due 必须在本次提交之后"
        assert "." not in item.due, (
            "due 统一到秒级：带微秒会让 review_id 的派生在不同调用间漂，"
            "而 review_id 是幂等键——漂一次就多一条重复队列项"
        )


def test_no_evidence_skill_gets_no_item():
    """`raw-no-trace` 的 retry / param 是 `unscored`：没有证据 ⇒ 不推复习（决策 7）。"""
    report = _report("raw-no-trace")
    # 先把"缺过程"这个抑制条件摘掉，单独测 no_evidence 分支
    report["process_fact"] = {**report["process_fact"], "missing": False}
    result = sch.schedule_submission(report, user_id=USER, at=AT)
    actions = {d.skill_id: d.action for d in result.decisions}
    assert actions == {MSP: "passed", FR: "no_evidence", TPV: "no_evidence"}
    assert result.items == ()


def test_item_kind_follows_the_task_fault_flag():
    """题本身带故障注入 ⇒ `fault_injection`，否则 `replay`。

    2026-11-24 更正：原来是 `variant`。`variant` 的词表标签是「同族换一道」，
    而本模块给 `task_id` 填的是 `sub.task_id`——刚提交那道题。两者并存就是页面上
    的假话（标着换一道、点进去还是刚才那道）。本模块不挑题，只能产"再做同一道"。
    """
    report = _report("raw-fake-retry")
    assert {i.kind for i in sch.schedule_submission(report, user_id=USER, at=AT,
                                                    task_has_fault=True).items} == {"fault_injection"}
    assert {i.kind for i in sch.schedule_submission(report, user_id=USER, at=AT).items} == {"replay"}


def test_item_kind_never_contradicts_its_task_id():
    """不变式：kind 与 task_id 必须自洽。

    这是 2026-11-24 那个 bug 的回归。当时 `kind=variant`（同族换一道）+ `task_id=当前题`
    同时出现，页面显示「同族换一道（订单数据核对报告）」，用户点进去发现还是同一道。
    演示数据手工把 task_id 写成族内另一道题（`dbg-003`），把这个矛盾遮住了——假绿。
    所以这里不查标签文案，直接查**两个字段之间的关系**：只要本模块不挑题，
    就不许出现"换一道"的 kind。
    """
    report = _report("raw-fake-retry")
    submitted = report["submission"]["task_id"]
    for fault in (False, True):
        result = sch.schedule_submission(report, user_id=USER, at=AT, task_has_fault=fault)
        assert result.items, "夹具应当产出复习项，否则这条什么都没测到"
        for item in result.items:
            assert item.kind in sch.AUTO_KINDS, f"产出了不可自动决定的形态 {item.kind!r}"
            assert item.kind != "variant", (
                "不挑题却给了『同族换一道』——task_id 会指回刚做过的那道，页面在说假话"
            )
            assert item.task_id == submitted, (
                f"复习项应对准刚提交的题 {submitted!r}，实际是 {item.task_id!r}"
            )


def test_review_id_depends_on_due_so_rerun_is_idempotent():
    """同一份提交重跑 ⇒ 同一批 id（幂等）；due 不同 ⇒ id 不同（历史不会被覆盖）。"""
    report = _report("raw-fake-retry")
    first = sch.schedule_submission(report, user_id=USER, at=AT)
    again = sch.schedule_submission(report, user_id=USER, at=AT)
    assert [i.review_id for i in first.items] == [i.review_id for i in again.items]
    later = sch.schedule_submission(report, user_id=USER, at="2027-01-01T00:00:00Z")
    assert {i.review_id for i in later.items}.isdisjoint({i.review_id for i in first.items})


@pytest.mark.parametrize("mutate, match", [
    (lambda r: r["submission"].pop("submission_id"), "submission_id"),
    (lambda r: r["submission"].pop("evaluated_at"), "evaluated_at"),
])
def test_missing_traceability_fields_are_loud(mutate, match):
    """没有来源 id / 没有时刻 ⇒ 报错。编一个"现在"会让补录数据的间隔全错。"""
    report = _report("raw-fake-retry")
    mutate(report)
    with pytest.raises(sch.SchedulerError, match=match):
        sch.schedule_submission(report, user_id=USER)


# --- 「本次不算数」不动卡：本轮的口径判断 ------------------------------------------

def test_disqualified_submission_updates_nothing():
    """白卷（`R1` fail ⇒ disqualified）**不动卡、不建项**。

    否则 FSRS 会按 R3/R4 的两个 pass 给出 rating=4(Easy)，把该技能的间隔推到 8 天后——
    用一次"没交产物"的提交冒充一次通过的复习，真实需求被推远且看不出来。
    注意这**不改** P 的口径（那是 B-07b 待人拍的事）：P 仍按 A 现状照记。
    """
    report = _report("raw-no-file")
    assert dr.suppression_reasons(report) == ["disqualified"]
    result = sch.schedule_submission(report, user_id=USER, at=AT)
    assert result.cards == {}
    assert result.items == ()
    assert {d.action for d in result.decisions} == {"suppressed"}
    assert {d.reasons for d in result.decisions} == {("disqualified",)}


def test_missing_process_submission_updates_nothing():
    """缺过程（`process_fact.missing`）同样不动卡：报告已经写明"本次不算数"。"""
    report = _report("raw-no-trace")
    assert dr.suppression_reasons(report) == ["process_missing"]
    result = sch.schedule_submission(report, user_id=USER, at=AT)
    assert result.cards == {} and result.items == ()


def test_suppression_reasons_covers_all_three_build_conditions():
    """三项与 `build_report` 的 `suppressed` 同源：门禁 / 版本 / 缺过程。

    这三项分散在报告的 `score` 与 `process_fact` 两处，所以用逐一打开的方式锁：
    任何一项成立都判"不算数"，三项都不成立才允许动卡。
    """
    base = _report("raw-perfect")
    assert dr.suppression_reasons(base) == []

    for field, key in (("disqualified", "disqualified"), ("voided", "voided")):
        report = copy.deepcopy(base)
        report["score"][field] = True
        assert dr.suppression_reasons(report) == [key], f"{field} 没被认成抑制条件"

    report = copy.deepcopy(base)
    report["process_fact"] = {**report["process_fact"], "missing": True}
    assert dr.suppression_reasons(report) == ["process_missing"]

    # 三项同时成立时按固定顺序全列出（便于日志比对，不依赖 dict 顺序）
    report["score"]["disqualified"] = True
    report["score"]["voided"] = True
    assert dr.suppression_reasons(report) == ["disqualified", "voided", "process_missing"]


# --- store：落库、幂等、关闭旧项 ---------------------------------------------------

def _submit(conn, name: str, sid: str, at: str, *, user: str = USER) -> dict:
    report = _report(name, submission_id=sid, at=at)
    store.save_submission(conn, report, user_id=user, created_at=at)
    result = sch.schedule_submission(
        report, user_id=user, at=at,
        cards=sch.cards_from_rows(store.load_fsrs_cards(conn, user)),
    )
    return store.save_schedule(conn, result)


def test_cards_roundtrip_through_sqlite(db):
    """写进去的卡读回来逐字段相同（含 `due` / `stability` / `last_review`）。"""
    summary = _submit(db, "raw-fake-retry", "s1", AT)
    assert summary["cards_written"] == 3
    cards = store.load_fsrs_cards(db, USER)
    assert set(cards) == {MSP, FR, TPV}
    card = sch.cards_from_rows({MSP: cards[MSP]})[MSP]
    assert card.due == sch.mastery.parse_instant("2026-11-15T00:01:00+00:00")
    assert card.stability is not None


def test_save_schedule_is_idempotent(db):
    """同一份结果落两次：卡片不重复、复习项不重复、关闭数第二次为 0。"""
    report = _report("raw-fake-retry", submission_id="s1", at=AT)
    store.save_submission(db, report, user_id=USER, created_at=AT)
    result = sch.schedule_submission(report, user_id=USER, at=AT)
    first = store.save_schedule(db, result)
    again = store.save_schedule(db, result)
    assert (first["items_added"], again["items_added"]) == (2, 0)
    assert again["items_skipped"] == 2
    assert len(store.list_review_items(db, USER)) == 2


def test_second_failure_closes_previous_items_and_opens_new_ones(db):
    """同一技能再失败一次：旧那条变 `done`，新的一条 `pending`——队列不堆积。"""
    _submit(db, "raw-fake-retry", "s1", "2026-11-15T00:00:00Z")
    _submit(db, "raw-fake-retry", "s2", "2026-11-16T00:00:00Z")
    rows = store.list_review_items(db, USER)
    assert len(rows) == 4
    done = [r for r in rows if r["status"] == "done"]
    pending = [r for r in rows if r["status"] == "pending"]
    assert len(done) == len(pending) == 2
    assert all(r["due"].startswith("2026-11-15") for r in done)
    assert all(r["due"].startswith("2026-11-16") for r in pending)


def test_pass_closes_items_and_creates_none(db):
    """做对一次 ⇒ 该技能的待复习项全部关闭，且不产生新项（队列清空）。"""
    _submit(db, "raw-fake-retry", "s1", "2026-11-15T00:00:00Z")
    summary = _submit(db, "raw-perfect", "s2", "2026-11-16T00:00:00Z")
    assert summary["items_added"] == 0 and summary["items_closed"] == 2
    assert [r for r in store.list_review_items(db, USER) if r["status"] == "pending"] == []


def test_suppressed_run_does_not_close_pending_items(db):
    """白卷不算一次复习 ⇒ 旧项**不许**被关掉（关掉等于用不算数的提交抹掉排期）。"""
    _submit(db, "raw-fake-retry", "s1", "2026-11-15T00:00:00Z")
    summary = _submit(db, "raw-no-file", "s2", "2026-11-20T00:00:00Z")
    assert summary["items_closed"] == 0 and summary["cards_written"] == 0
    pending = [r for r in store.list_review_items(db, USER) if r["status"] == "pending"]
    assert len(pending) == 2


def test_future_items_are_not_closed_by_an_early_submission(db):
    """只关**已到期**的项：一条还没到期的计划不该被提前的提交关掉。"""
    _submit(db, "raw-fake-retry", "s1", "2026-11-15T00:00:00Z")
    # 手工把一条 pending 项的 due 推到很远的未来，再提交一次
    db.execute("UPDATE review_items SET due = '2027-06-01T00:00:00+00:00' "
               "WHERE user_id = ? AND skill_id = ?", (USER, FR))
    summary = _submit(db, "raw-fake-retry", "s2", "2026-11-16T00:00:00Z")
    assert summary["items_closed"] == 1, "只该关掉 planning 那条到期的"
    future = [r for r in store.list_review_items(db, USER)
              if r["status"] == "pending" and r["due"].startswith("2027")]
    assert len(future) == 1


def test_daily_cap_comes_from_the_user_row(db):
    """默认上限 2；改小后队列真的变短，但库里的 pending 条数不变。"""
    for i, name in enumerate(("raw-fake-retry",) * 3):
        _submit(db, name, f"s{i}", f"2026-11-{15 + i:02d}T00:00:00Z")
    pending = [r for r in store.list_review_items(db, USER) if r["status"] == "pending"]
    assert len(pending) == 2, "三次失败只剩最近一轮的两条 pending"

    assert store.daily_review_cap(db, USER) == 2
    kept, deferred = sch.take_daily(pending, at="2026-11-30T00:00:00Z",
                                    cap=store.daily_review_cap(db, USER))
    assert (len(kept), deferred) == (2, 0)

    store.set_daily_review_cap(db, USER, 1)
    kept, deferred = sch.take_daily(pending, at="2026-11-30T00:00:00Z",
                                    cap=store.daily_review_cap(db, USER))
    assert (len(kept), deferred) == (1, 1)
    assert len([r for r in store.list_review_items(db, USER) if r["status"] == "pending"]) == 2


def test_card_for_unregistered_skill_is_loud(db):
    """技能没注册就写卡 ⇒ 报错，不让 `skills` 外键变成装饰。"""
    with pytest.raises(store.StoreError, match="未注册"):
        store.save_fsrs_cards(db, "u-x", {"not_a_skill": {"due": AT}})


def test_register_task_reads_variant_inheritance(db):
    """变体注册时元数据必须等于**继承后**的结果。

    round-83 的变体只写差异，`register_task` 若直接 `yaml.safe_load`，会记成
    `rubric_count=2`、`fault_injection=0`（母本是 5 / 1）。后者正是 `kind` 的依据，
    错了就等于"故障注入课永远不推故障注入复习"。
    """
    store.register_task(db, VARIANT_YAML)
    parent = db.execute("SELECT rubric_count, fault_injection FROM tasks WHERE task_id = ?",
                        ("dbg-001-order-report",)).fetchone()
    variant = db.execute("SELECT rubric_count, fault_injection FROM tasks WHERE task_id = ?",
                         ("ret-002-second-page-fault",)).fetchone()
    assert tuple(variant) == tuple(parent) == (5, 1)


# --- 判据：一次失败提交 → 1 个 pending，due 由 py-fsrs 给出，且不超上限 -------------

def test_criterion_failed_submission_yields_pending_item_within_cap(db):
    """B-08 的判据原文：「一次失败提交产出 1 个 `pending` 的 ReviewItem，`due` 由 py-fsrs 给出；
    每日队列上限生效（`daily_review_cap`）」。

    用**单一技能缺口**的形态验证"1 个"：手工把报告裁到只剩失败的那条积分断言。
    用 `raw-fake-retry` 整份会得到 2 个（两个技能都挂了），那不是判据要的形状。
    """
    report = _report("raw-fake-retry")
    report["assertions"] = [a for a in report["assertions"]
                            if a["id"] not in ("R2_report_correct", "R4_no_param_hallucination")]
    store.save_submission(db, report, user_id=USER, created_at=AT)
    result = sch.schedule_submission(report, user_id=USER, at=AT)
    store.save_schedule(db, result)

    rows = [r for r in store.list_review_items(db, USER) if r["status"] == "pending"]
    assert len(rows) == 1 and rows[0]["skill_id"] == FR
    due = sch.mastery.parse_instant(rows[0]["due"])
    now = sch.mastery.parse_instant(AT)
    assert now < due <= now + timedelta(days=1), "首次失败落在 learning step（1 分钟内），不是天级间隔"
    kept, _ = sch.take_daily(rows, at=(now + timedelta(minutes=5)).isoformat(),
                             cap=store.daily_review_cap(db, USER))
    assert len(kept) <= store.daily_review_cap(db, USER)


def test_due_is_normalised_to_seconds_even_if_at_has_microseconds():
    """`at` 带微秒时（平台时间戳常带毫秒），`due` 仍归到整秒。

    这是**约定**而不是正确性：带微秒的 `due` 自身也是确定的。但 `review_id` 是
    due 字符串的 sha256，而 `at` 的来源精度不一（平台毫秒 / 手写整秒）——
    不统一，同一时刻的两种写法就会变成两条队列项。约定统一到秒，把这类重复挡在门外。
    """
    stamp = "2026-11-15T00:00:00.123456Z"
    report = _report("raw-fake-retry", submission_id="s-micro", at=stamp)
    result = sch.schedule_submission(report, user_id=USER, at=stamp)
    assert result.items, "前提：这份提交确实该产出复习项"
    for item in result.items:
        assert "." not in item.due, f"due 未归到秒：{item.due}"
