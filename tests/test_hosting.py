"""B-51 的判据锁：托管会话把"过程记录"从学生手上拿走之后，剩下什么还可能出错。

分五组：

1. **隔离**：两个会话不能互相影响故障额度。这是搬上服务器**最容易出事**的一条——
   共用一个 mock 实例时，A 调了几次会决定 B 第几次遇到 500，而这种错在单人测试里
   永远看不见（本地每个人一个进程）。
2. **记账**：编号由服务端数、失败的调用照记、重复提交不重判。
3. **预算**：调用次数有上限，超了给 429 而不是把服务拖死。
4. **产物**：`write_report` 的正文在入参里，抄不出来就等于交了半份。
5. **页面**：学习者那一页是白名单题面，不能夹带答案（期望金额、权重、故障配置）。

不起 socket 的测试放在这里；路由与状态码在 `test_submission_api.py`。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import hosting  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

TASK_ID = "dbg-001-order-report"


@pytest.fixture()
def env(tmp_path):
    """一个真库 + 一个真注册表 + 判分函数。会话与判分共用同一个 db（托管就是一个进程一个库）。"""
    db = tmp_path / "host.db"
    registry = api.TaskRegistry(ROOT / "tasks")
    api.bootstrap(db, registry)

    def conn_factory():
        return store.connect(db)

    sessions = hosting.SessionRegistry(ROOT / "tasks", conn_factory=conn_factory)
    submit_fn = lambda payload: api.submit(  # noqa: E731 - 只是把两个依赖钉住
        payload, registry=registry, conn_factory=conn_factory
    )
    return sessions, submit_fn, registry


# ── 1. 隔离 ──────────────────────────────────────────────────────────────────


def test_two_sessions_do_not_share_the_fault_quota(env):
    """A 的重试不能改变 B 第几次遇到失败。

    故障配置是「命中条件的第 1 次调用返回 500，之后成功」。若两个会话共用一个
    `MockToolService`，A 先打一次就把额度用掉了，B 的第一次会直接成功——他这题
    等于没考"失败后会不会重试"，而报告上看不出任何异常。
    """
    sessions, _submit, _registry = env
    a = sessions.create(TASK_ID)
    b = sessions.create(TASK_ID)

    _, status_a1, _ = sessions.call(a["token"], "fetch_orders", {"offset": 10, "limit": 10})
    _, status_b1, _ = sessions.call(b["token"], "fetch_orders", {"offset": 10, "limit": 10})
    _, status_a2, _ = sessions.call(a["token"], "fetch_orders", {"offset": 10, "limit": 10})

    assert (status_a1, status_b1, status_a2) == (500, 500, 200), (
        "两个人应该各自遇到一次 500 再成功；"
        f"实际 A 第一次={status_a1}、B 第一次={status_b1}、A 第二次={status_a2}"
    )


def test_one_sessions_calls_are_not_visible_in_another(env):
    """按人取报告的前提：A 的调用不出现在 B 的那份记录里。"""
    sessions, _submit, _registry = env
    a = sessions.create(TASK_ID)
    b = sessions.create(TASK_ID)
    sessions.call(a["token"], "fetch_orders", {"offset": 0, "limit": 5})

    calls_a, _ = sessions.calls(a["token"])
    calls_b, _ = sessions.calls(b["token"])
    assert len(calls_a) == 1 and calls_b == []


def test_tokens_are_long_random_and_distinct(env):
    """token 就是凭证：短了能被猜，重了两个人共用一个会话。"""
    sessions, _submit, _registry = env
    tokens = {sessions.create(TASK_ID)["token"] for _ in range(20)}
    assert len(tokens) == 20
    for token in tokens:
        assert len(token) >= hosting.MIN_TOKEN_LEN
        assert set(token) <= hosting._TOKEN_ALPHABET


# ── 2. 记账 ──────────────────────────────────────────────────────────────────


def test_call_index_is_counted_by_the_server_and_includes_failures(env):
    """编号 1 起、连续，**失败的调用也占一个号**。

    这是"过程可审计"的根：编号若可由学习者给，他就能删掉一次乱加参数的调用再重编号；
    现在记录根本不在他手上。
    """
    sessions, _submit, _registry = env
    token = sessions.create(TASK_ID)["token"]
    _, s1, _ = sessions.call(token, "fetch_orders", {"offset": 0, "limit": 10})
    idx2, s2, _ = sessions.call(token, "fetch_orders", {"offset": 10, "limit": 10})
    idx3, s3, _ = sessions.call(token, "fetch_orders", {"offset": 10, "limit": 10})

    assert (s1, s2, s3) == (200, 500, 200)
    calls, _ = sessions.calls(token)
    assert [c["call_index"] for c in calls] == [1, 2, 3]
    assert calls[1]["http_status"] == 500 and idx2 == 2 and idx3 == 3


def test_unknown_tool_is_recorded_as_evidence(env):
    """调了题面里没有的工具：服务返回 404，但**这一行要留下**——它是"没读题面"的证据。"""
    sessions, _submit, _registry = env
    token = sessions.create(TASK_ID)["token"]
    _, status, body = sessions.call(token, "delete_everything", {})
    calls, _ = sessions.calls(token)
    assert status == 400 and body["error"] == "unknown_tool"
    assert calls[0]["name"] == "delete_everything" and calls[0]["http_status"] == 400


def test_bad_params_shape_is_rejected_before_reaching_the_tool(env):
    """参数不是对象是调用方的错，400 出去且不记账（它连一次工具调用都不是）。"""
    sessions, _submit, _registry = env
    token = sessions.create(TASK_ID)["token"]
    with pytest.raises(hosting.HostError) as exc:
        sessions.call(token, "fetch_orders", ["offset", 0])
    assert exc.value.status == 400 and exc.value.code == "bad_params"
    assert sessions.calls(token)[0] == []


# ── 3. 预算 ──────────────────────────────────────────────────────────────────


def test_budget_exhausted_is_429_with_a_readable_reason(env):
    """上限不是防黑客，是防"脚本死循环把服务刷满"。文案要说明该怎么办。"""
    sessions, _submit, _registry = env
    sessions._max_calls = 2                     # 内部常量，测试里调小
    token = sessions.create(TASK_ID)["token"]
    sessions.call(token, "fetch_orders", {"offset": 0, "limit": 5})
    sessions.call(token, "fetch_orders", {"offset": 5, "limit": 5})
    with pytest.raises(hosting.HostError) as exc:
        sessions.call(token, "fetch_orders", {"offset": 10, "limit": 5})
    assert exc.value.status == 429 and exc.value.code == "session_exhausted"
    assert "重试" in exc.value.message


def test_garbage_token_is_404_and_costs_no_query(env):
    """扫路径的请求连一次 SQL 都不该花；形状不对直接 404。"""
    sessions, _submit, _registry = env
    for bad in ("", "short", "../../etc/passwd", "a" * 43 + "/"):
        with pytest.raises(hosting.HostError) as exc:
            sessions.get(bad)
        assert exc.value.status == 404, bad


# ── 4. 产物与交卷 ────────────────────────────────────────────────────────────


def test_artifacts_are_taken_from_the_call_parameters(env):
    """`write_report` 的返回体只有路径和字节数，正文在入参里；抄不出来就等于交了半份。"""
    sessions, _submit, _registry = env
    token = sessions.create(TASK_ID)["token"]
    sessions.call(token, "write_report",
                  {"path": "/workspace/report.json", "content": '{"valid_count": 1}'})
    calls, artifacts = sessions.calls(token)
    assert len(calls) == 1
    assert artifacts == {"/workspace/report.json": '{"valid_count": 1}'}


def test_grading_twice_returns_the_same_submission(env):
    """重复点提交不重判：否则一个人两份分数，报告页也说不清是哪一份。

    这条在本地方式里不存在（学习者只发一次文件），托管后一定有人反复点。
    """
    sessions, submit_fn, _registry = env
    token = sessions.create(TASK_ID)["token"]
    sessions.call(token, "fetch_orders", {"offset": 0, "limit": 10})
    sessions.call(token, "write_report",
                  {"path": "/workspace/report.json", "content": "{}"})

    first, created_first = sessions.grade(token, submit_fn)
    second, created_second = sessions.grade(token, submit_fn)
    assert created_first is True and created_second is False
    assert (first["submission"]["submission_id"]
            == second["submission"]["submission_id"])
    assert first["score"] == second["score"]


def test_submitting_before_doing_anything_is_a_409(env):
    """空会话交卷要说明"先去做题"，而不是判一份 0 分——0 分会让人以为自己做错了。"""
    sessions, submit_fn, _registry = env
    token = sessions.create(TASK_ID)["token"]
    with pytest.raises(hosting.HostError) as exc:
        sessions.grade(token, submit_fn)
    assert exc.value.status == 409 and exc.value.code == "nothing_to_grade"


def test_a_full_session_still_produces_a_real_report(env):
    """端到端：三个调用（含一次 500）进去，一份有断言的报告出来。"""
    sessions, submit_fn, _registry = env
    token = sessions.create(TASK_ID)["token"]
    sessions.call(token, "fetch_orders", {"offset": 0, "limit": 10})
    sessions.call(token, "fetch_orders", {"offset": 10, "limit": 10})   # 500
    sessions.call(token, "write_report",
                  {"path": "/workspace/report.json", "content": '{"valid_count": 9}'})

    report, created = sessions.grade(token, submit_fn)
    assert created is True
    assert report["trace_audit"]["call_count"] == 3
    assert report["trace_audit"]["schema_valid"] is True
    assert report["score"]["ratio"] < 1.0, "只拉了半页就交，不该满分"
    assert len(report["assertions"]) >= 3


# ── 5. 页面 ──────────────────────────────────────────────────────────────────


def test_landing_page_gives_the_address_without_leaking_answers(env):
    """那一页是"把这段话贴给 AI 助手"的来源，所以要有地址和工具；不能有答案。"""
    sessions, _submit, registry = env
    session = sessions.create(TASK_ID)
    page = hosting.render_landing(session,
                                  api.public_task(sessions.task(TASK_ID), registry.items()),
                                  "http://127.0.0.1:9999")
    assert f"/s/{session['token']}/tools/" in page
    assert "fetch_orders" in page and "write_report" in page
    for leaked in ("rubric", "expected", "fault_injection", "1837.99"):
        assert leaked not in page, f"页面泄露了 {leaked}"
    assert "<script" not in page.lower()


def test_landing_page_escapes_the_scenario(env):
    """题面是项目内文本，但"只在项目内"不是安全边界——转义是一行的事。"""
    sessions, _submit, _registry = env
    session = sessions.create(TASK_ID)
    task = {"title": "标题", "scenario": "<script>alert(1)</script>", "tools": []}
    page = hosting.render_landing(session, task, "http://x")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


# ── 6. 办一场活动：批量发链接、事后出名单 ───────────────────────────────────

def test_mint_creates_links_without_building_services(env):
    """一次发 30 条链接时不该建 30 个 mock 实例——那是"做题那一刻"才需要的东西。

    判据不用看内存，看 `_services`：mint 之后仍然是空的，第一次调用才补建。
    """
    sessions, _submit, _registry = env
    rows = sessions.mint(TASK_ID, 5)
    assert len(rows) == 5
    assert len({r["token"] for r in rows}) == 5
    assert sessions._services == {}, "mint 顺手建了实例，白占内存"
    assert all(sessions.get(r["token"])["used"] == 0 for r in rows)


def test_mint_refuses_an_unknown_task_and_creates_nothing(env):
    """题不存在时一个都不能建：半批会话是最难处理的残留。"""
    sessions, _submit, _registry = env
    with pytest.raises(hosting.HostError) as exc:
        sessions.mint("no-such-task", 3)
    assert exc.value.status == 404
    assert sessions.mint(TASK_ID, 1), "库还活着"
    conn = sessions._conn_factory()
    try:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    finally:
        conn.close()


def test_mint_rejects_a_silly_count(env):
    sessions, _submit, _registry = env
    for bad in (0, -1, hosting.MAX_MINT + 1):
        with pytest.raises(hosting.HostError) as exc:
            sessions.mint(TASK_ID, bad)
        assert exc.value.status == 400


def test_roster_shows_progress_and_never_leaks_full_tokens(env):
    """活动结束给的那张表：编号、调了几次、交没交、几分。

    只给 token 前 12 位——学生报编号就够了；完整 token 是凭证，不该跟着成绩到处传。
    """
    sessions, submit_fn, _registry = env
    a, b = sessions.mint(TASK_ID, 2)
    sessions.call(a["token"], "fetch_orders", {"offset": 0, "limit": 10})
    sessions.call(a["token"], "write_report",
                  {"path": "/workspace/report.json", "content": '{"valid_count": 9}'})
    sessions.grade(a["token"], submit_fn)

    conn = sessions._conn_factory()
    try:
        rows = hosting.roster(conn)
    finally:
        conn.close()
    assert len(rows) == 2
    by_code = {row["code"]: row for row in rows}
    assert set(by_code) == {a["token"][:12], b["token"][:12]}
    done = by_code[a["token"][:12]]
    assert done["calls"] == 2 and done["submitted"] is True
    assert done["ratio"] is not None and done["ratio"] < 1.0
    idle = by_code[b["token"][:12]]
    assert idle["calls"] == 0 and idle["submitted"] is False and idle["ratio"] is None
    assert all(len(row["code"]) == 12 for row in rows)
    assert all(a["token"] not in row["code"] for row in rows)


def test_mint_cli_writes_a_roster_and_a_gradeable_link(tmp_path, capsys):
    """命令行这一层也要能跑：`mint` 写出的链接，服务端认。"""
    db = tmp_path / "act.db"
    out = tmp_path / "名单.csv"
    code = hosting.main(["mint", "--db", str(db), "--tasks", str(ROOT / "tasks"),
                         "--task", TASK_ID, "--count", "2",
                         "--base-url", "http://10.0.0.5:8080", "--out", str(out)])
    assert code == 0
    text = out.read_text(encoding="utf-8-sig")
    assert "http://10.0.0.5:8080/s/" in text
    printed = capsys.readouterr().out
    assert "就是凭证" in printed

    assert hosting.main(["roster", "--db", str(db), "--tasks", str(ROOT / "tasks")]) == 0
    assert "共 2 个会话，0 份已交卷" in capsys.readouterr().out


def test_creating_and_calling_does_not_deadlock(env):
    """回归锁：`task()` 自己也要同一把锁，而 `threading.Lock` 不可重入。

    真踩过——`create` 里在锁内解析任务，整个测试文件挂住 7 分钟，报错信息是"没有"。
    所以这里用超时兜住：真死锁时这条测试失败，而不是让 CI 卡住。
    """
    import threading

    sessions, _submit, _registry = env

    def work():
        token = sessions.create(TASK_ID)["token"]
        sessions.call(token, "fetch_orders", {"offset": 0, "limit": 5})

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout=20)
    assert not thread.is_alive(), "create/call 死锁了"


# ── 7. 助教视角：哪条要求最多人没做到 ────────────────────────────────────────

def _do_and_submit(sessions, submit_fn, calls, write_content):
    session = sessions.create(TASK_ID)
    token = session["token"]
    for name, params in calls:
        sessions.call(token, name, params)
    sessions.call(token, "write_report",
                  {"path": "/workspace/report.json", "content": write_content})
    report, _created = sessions.grade(token, submit_fn)
    return token, report["score"]["ratio"]


def test_summary_aggregates_missed_requirements(env):
    """两三个人交卷后，助教要能一眼看出"哪条要求大家都没做到"。"""
    import json

    sessions, submit_fn, _registry = env
    sessions.mint(TASK_ID, 1)                       # 一个开了没做的，不该进分母
    _do_and_submit(sessions, submit_fn, [
        ("fetch_orders", {"offset": 0, "limit": 10}),
        ("fetch_orders", {"offset": 10, "limit": 10}),
        ("fetch_orders", {"offset": 10, "limit": 10}),
    ], json.dumps({"valid_count": 17, "total_amount": 1837.99}))
    _do_and_submit(sessions, submit_fn, [
        ("fetch_orders", {"offset": 0, "limit": 10}),
    ], json.dumps({"valid_count": 9, "total_amount": 861.24}))

    conn = sessions._conn_factory()
    try:
        data = hosting.summary(conn)
    finally:
        conn.close()

    assert data["sessions"] == 3 and data["submitted"] == 2
    by_title = {row["title"]: row for row in data["assertions"]}
    assert by_title["结论是否与判据一致"]["fail"] == 1
    assert by_title["结论是否与判据一致"]["pass"] == 1
    assert by_title["交付物是否存在"]["fail"] == 0
    assert data["assertions"][0]["share"] >= data["assertions"][-1]["share"], "没按占比排序"
    assert data["patterns"], "命中的错法没聚合出来"
    assert all(item["label"] and not item["label"].startswith(("wrong_", "no_"))
               for item in data["patterns"]), "错法显示成机器码了"


def test_summary_does_not_count_platform_failures_as_the_learners_fault():
    """`blocked`（平台没判成）与 `unscored`（这条不适用）都不算"没做到"。

    把它们混进去，等于替学习者认错——而这两种状态恰恰是"没拿到证据"。
    """
    data = {
        "sessions": 1, "submitted": 1, "avg_ratio": 0.0,
        "assertions": [{"id": "R1", "title": "交付物是否存在", "fail": 0,
                        "blocked": 1, "pass": 0, "unscored": 0, "share": 0.0}],
        "patterns": [],
    }
    text = hosting.render_summary(data)
    assert "0/1" in text, "blocked 被算成了没做到"


def test_summary_verdict_refuses_to_advise_on_a_tiny_sample():
    """2 份提交里 1 个人没做到，不能拿去改题面——这是判读门槛存在的全部理由。"""
    data = {
        "sessions": 2, "submitted": 2, "avg_ratio": 0.6,
        "assertions": [{"id": "R1", "title": "结论是否与判据一致", "fail": 1,
                        "blocked": 0, "pass": 1, "unscored": 0, "share": 0.5}],
        "patterns": [],
    }
    text = hosting.render_summary(data)
    assert "样本只有 2 份" in text
    assert "先看这一条" not in text, "样本不足却给出了改题面的建议"


def test_summary_verdict_points_at_the_worst_requirement_with_enough_samples():
    data = {
        "sessions": 10, "submitted": 10, "avg_ratio": 0.5,
        "assertions": [
            {"id": "R2", "title": "结论是否与判据一致", "fail": 8, "blocked": 0,
             "pass": 2, "unscored": 0, "share": 0.8},
            {"id": "R3", "title": "关键步骤是否走到", "fail": 2, "blocked": 0,
             "pass": 8, "unscored": 0, "share": 0.2},
        ],
        "patterns": [],
    }
    text = hosting.render_summary(data)
    assert "先看这一条" in text and "结论是否与判据一致" in text
    assert "优先怀疑题面" in text


def test_summary_flags_a_task_with_no_discrimination():
    data = {
        "sessions": 6, "submitted": 6, "avg_ratio": 1.0,
        "assertions": [{"id": "R1", "title": "结论是否与判据一致", "fail": 0,
                        "blocked": 0, "pass": 6, "unscored": 0, "share": 0.0}],
        "patterns": [],
    }
    text = hosting.render_summary(data)
    assert "区分度" in text


def test_summary_on_an_empty_activity_says_so(env):
    sessions, _submit, _registry = env
    conn = sessions._conn_factory()
    try:
        assert hosting.summary(conn)["submitted"] == 0
    finally:
        conn.close()
    assert "还没有人交卷" in hosting.render_summary(
        {"sessions": 0, "submitted": 0, "avg_ratio": None, "assertions": [], "patterns": []})


def test_summary_cli_prints_the_table_and_keeps_ids_in_the_csv(env, tmp_path, capsys):
    """表里不出现内部编号，CSV 里要有——CSV 是给维护者对题面用的。"""
    import json

    sessions, submit_fn, _registry = env
    _do_and_submit(sessions, submit_fn, [
        ("fetch_orders", {"offset": 0, "limit": 10}),
    ], json.dumps({"valid_count": 9, "total_amount": 861.24}))
    # 夹具里的库里已经有数据，直接走 CLI 读同一个文件
    db_path = _db_path_of(sessions)
    out = tmp_path / "汇总.csv"
    assert hosting.main(["summary", "--db", str(db_path), "--tasks", str(ROOT / "tasks"),
                         "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "没做到" in printed and "1/1" in printed
    assert "R2_" not in printed, "表里出现了断言 id"
    assert "断言id" in out.read_text(encoding="utf-8-sig")


def _db_path_of(sessions) -> Path:
    """从 registry 拿库路径：`conn_factory` 是个闭包，测试里不拆它，直接问注册表。"""
    conn = sessions._conn_factory()
    try:
        return Path(conn.execute("PRAGMA database_list").fetchone()[2])
    finally:
        conn.close()


# ── 8. 一个人跨题累积 + 复习项回到他手上（B-55） ─────────────────────────────

def test_mint_with_a_user_binds_several_links_to_one_person(env):
    """做三题 = 三个人，复习队列永远是空的。

    掌握度、FSRS 卡、复习项都以 `user_id` 为键，所以组织者要能把多条链接绑到同一个人。
    """
    sessions, _submit, _registry = env
    rows = sessions.mint(TASK_ID, 3, user_id="zhang-san")
    assert {r["user_id"] for r in rows} == {"zhang-san"}
    assert len({r["token"] for r in rows}) == 3, "链接本身仍是一人一条，只是同一个人"

    with pytest.raises(hosting.HostError) as exc:
        sessions.mint(TASK_ID, 1, user_id="zhang san")
    assert exc.value.code == "bad_user"


def test_a_low_score_submission_leaves_review_items_for_that_person(env):
    """交完卷要长出复习项——这是"提醒页"能对真人说话的前提。

    以前只有两个 walk 脚本会调 `save_schedule`，真实提交路径上从来不排期；
    于是提醒页读的是打包时烘好的 `reminder.json`，真学生做完题库里一条都没有。
    """
    import json

    sessions, submit_fn, _registry = env
    session = sessions.create(TASK_ID, user_id="li-si")
    token = session["token"]
    sessions.call(token, "fetch_orders", {"offset": 0, "limit": 10})
    sessions.call(token, "write_report",
                  {"path": "/workspace/report.json",
                   "content": json.dumps({"valid_count": 9, "total_amount": 861.24})})
    sessions.grade(token, submit_fn)

    conn = sessions._conn_factory()
    try:
        items = store.list_review_items(conn, "li-si", status="pending")
        # 刚交完卷那一刻还没到复习点（FSRS 给的是"一分钟后"这种学习步长），
        # 所以这里把"现在"推到 5 分钟后——这才是学生第二天打开页面的真实情形。
        later = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds")
        review = hosting.review_of(conn, "li-si", at=later)
    finally:
        conn.close()
    assert items, "做完一道错题却没有复习项"
    assert review and not review["empty"]
    assert review["today"], "今天该复习的列表是空的"
    assert all(row["skill"] and not row["skill"].startswith(("multi_", "failure_"))
               for row in review["today"]), "页面上露了英文技能 id"


def test_review_of_a_stranger_is_none_not_an_error(env):
    """还没做过题的人没有 users 行：这一块本来就不该出现，不是出错。"""
    sessions, _submit, _registry = env
    conn = sessions._conn_factory()
    try:
        assert hosting.review_of(conn, "nobody-here") is None
    finally:
        conn.close()


def test_landing_after_submission_leads_with_the_report(env):
    """回访的人先看到自己那次的结果；做题说明不该再占屏。"""
    import json

    sessions, submit_fn, _registry = env
    session = sessions.create(TASK_ID, user_id="wang-wu")
    token = session["token"]
    sessions.call(token, "fetch_orders", {"offset": 0, "limit": 10})
    sessions.call(token, "write_report",
                  {"path": "/workspace/report.json", "content": json.dumps({"valid_count": 9})})
    report, _ = sessions.grade(token, submit_fn)
    sid = report["submission"]["submission_id"]

    page = hosting.render_landing(
        sessions.get(token), {"title": "标题", "scenario": "题面", "tools": []},
        "http://x", report_url=f"/submissions/{sid}/report.html",
    )
    assert "已经交过卷" in page and f"/submissions/{sid}/report.html" in page
    assert "/tools/" not in page, "交过卷了还把做题地址摆在最上面"
    assert "找老师要一条新的链接" in page, "没说清这条链接不能重复用"


def test_landing_progress_block_names_the_fixed_and_the_remaining_assertions(env):
    """走一遍真实路由取页面的做法：学生页上的对比块只印中文要求名，不印断言 id。"""
    import json

    sessions, submit_fn, _registry = env

    def one_round(tries: int, count: int) -> str:
        token = sessions.create(TASK_ID, user_id="zhao-liu")["token"]
        for _ in range(tries):
            sessions.call(token, "fetch_orders", {"offset": 10, "limit": 10})
        sessions.call(token, "write_report", {
            "path": "/workspace/report.json", "content": json.dumps({"valid_count": count})})
        sessions.grade(token, submit_fn)
        return token

    one_round(tries=1, count=9)
    token = one_round(tries=2, count=17)
    session = sessions.get(token)

    db = sessions._conn_factory()
    try:
        since_last = hosting.since_last_of(db, session["submission_id"])
        assert since_last is not None and since_last["comparable"] is True
        page = hosting.render_landing(session, {"title": "标题", "scenario": "题面", "tools": []},
                                      "http://x", since_last=since_last)
    finally:
        db.close()

    assert "和上一次比" in page
    assert "这次改好了：关键步骤是否走到" in page
    assert "还是没过：结论是否与判据一致" in page
    assert "未重试" in page, "上次犯的错法应该用中文名告诉他这次没再出现"
    for leaked in ("R3_retry_evidence", "R2_report_correct", "still_failing", "fixed"):
        assert leaked not in page, f"页面上出现了机器码 {leaked}"


def test_landing_progress_block_hides_the_numbers_when_incomparable(env):
    """题目改过时**只印那一句原因**：两个分数并排摆着，任何人都会自己相减。"""
    sessions, _submit, _registry = env
    session = sessions.create(TASK_ID)
    page = hosting.render_landing(
        session, {"title": "标题", "scenario": "题面", "tools": []}, "http://x",
        since_last={"comparable": False, "reason": "这两次之间题目改过，检查项的含义可能变了，不能直接比。",
                    "headline": "这两次之间题目改过，检查项的含义可能变了，不能直接比。",
                    "score": {"before": 0.2, "after": 0.95}, "fixed": [], "still_failing": [],
                    "broke": [], "unjudged": [], "patterns": {}, "counts": {}},
    )
    assert "题目改过" in page
    assert "0.95" not in page and "0.20" not in page


def test_landing_shows_todays_review_and_hides_it_when_empty(env):
    """有复习项就摆出来，没有就整块不出现——空盒子比没有更让人困惑。"""
    sessions, _submit, _registry = env
    session = sessions.create(TASK_ID)
    task = {"title": "标题", "scenario": "题面", "tools": []}

    review = {
        "headline": "今天有 1 项该复习", "notice": "看不完的会留到明天",
        "today": [{"skill": "失败重试策略", "kind_label": "重做上次出错的那一页",
                   "task_title": "订单数据核对报告", "overdue_text": "逾期 2 天"}],
        "empty": False, "due_total": 1, "deferred": 0,
    }
    page = hosting.render_landing(session, task, "http://x", review=review)
    assert "今天该复习" in page
    assert "失败重试策略" in page and "逾期 2 天" in page
    assert "看不完的会留到明天" in page

    empty = dict(review, **{"empty": True, "today": []})
    assert "今天该复习" not in hosting.render_landing(session, task, "http://x", review=empty)


# ── 「你做过的其他题」 ────────────────────────────────────────────────────


def test_others_lists_only_this_persons_other_papers(env):
    """`--user` 把三条链接绑成一个人，但那三条彼此看不见——这一块是唯一的横向视图。

    安全边界就一条：只列 `user_id` 相同的会话。列错一次就是"一个学生看到别人的成绩"。
    """
    sessions, _submit, _registry = env
    mine = sessions.create(TASK_ID, user_id="same-person")["token"]
    sibling = sessions.create(TASK_ID, user_id="same-person")["token"]
    sessions.create(TASK_ID, user_id="someone-else")

    conn = sessions._conn_factory()
    try:
        others = hosting.others_of(conn, "same-person", mine)
    finally:
        conn.close()

    tokens = [row["token"] for row in others]
    assert sibling in tokens
    assert mine not in tokens, "不该把当前这条列进去"
    assert len(tokens) == 1, "同一道题只出现最近一次，不能连出两行同名"


def test_others_carries_the_score_and_the_link(env):
    """分数取生效值（重评过按新结论）：与报告页显示两个数是最容易被投诉的不一致。"""
    sessions, submit_fn, _registry = env
    first = sessions.create(TASK_ID, user_id="person")["token"]
    current = sessions.create(TASK_ID, user_id="person")["token"]
    sessions.call(first, "fetch_orders", {"offset": 0, "limit": 10})
    sessions.grade(first, submit_fn)

    conn = sessions._conn_factory()
    try:
        others = hosting.others_of(conn, "person", current)
        assert others and others[0]["ratio"] is not None
        page = hosting.render_landing(
            sessions.get(current), {"title": "标题", "scenario": "题面", "tools": []},
            "http://x", others=others,
        )
    finally:
        conn.close()

    assert "你做过的其他题" in page
    assert others[0]["token"] in page, "没有链接就只能看不能用"
    assert f"{others[0]['ratio']:.2f} 分" in page


def test_others_block_disappears_when_there_is_nothing_to_show(env):
    """只有一条链接的人不该看到一块空盒子。"""
    sessions, _submit, _registry = env
    token = sessions.create(TASK_ID)["token"]
    page = hosting.render_landing(
        sessions.get(token), {"title": "标题", "scenario": "题面", "tools": []},
        "http://x", others=[],
    )
    assert "你做过的其他题" not in page


def test_roster_csv_flags_a_full_mark_that_was_never_explained(env, tmp_path, capsys):
    """结果.csv 里要能一眼看出「满分、但没讲过」——那是活动结束后最该先问的几份。

    三行分别对应三种状态：讲过 / 满分没讲 / 没讲。把它们混成两档（讲过 / 没讲），
    助教就得自己对分数——那这两列就白加了。

    同时锁一条：命令行那张小表**不印复述原文**。一行几百字塞进终端表格，
    会把"谁交了谁没交"这个主视图挤没。
    """
    sessions, submit_fn, _registry = env

    def perfect(user_id: str) -> str:
        token = sessions.create(TASK_ID, user_id=user_id)["token"]
        for offset in (0, 10, 10):                       # 第二页那次必然 500，重试才算走到
            sessions.call(token, "fetch_orders", {"offset": offset, "limit": 10})
        sessions.call(token, "write_report", {
            "path": "/workspace/report.json",
            "content": json.dumps({"valid_count": 17, "total_amount": 1837.99})})
        report, _created = sessions.grade(token, submit_fn)
        assert report["score"]["ratio"] == 1.0
        return token

    told = perfect("u-told")
    silent = perfect("u-silent")
    broken = sessions.create(TASK_ID, user_id="u-broken")["token"]
    sessions.call(broken, "fetch_orders", {"offset": 0, "limit": 10})
    sessions.call(broken, "write_report",
                  {"path": "/workspace/report.json", "content": '{"valid_count": 9}'})
    sessions.grade(broken, submit_fn)

    conn = sessions._conn_factory()
    try:
        store.save_recall(conn, sessions.get(told)["submission_id"], user_id="u-told",
                          text="第二页 500，我拿同一个 offset 又试了一次")
        db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    finally:
        conn.close()

    out = tmp_path / "结果.csv"
    assert hosting.main(["roster", "--db", db_path, "--tasks", str(ROOT / "tasks"),
                         "--out", str(out)]) == 0

    import csv

    with open(out, encoding="utf-8-sig", newline="") as handle:
        rows = {row["user_id"]: row for row in csv.DictReader(handle)}
    assert rows["u-told"]["recall"] == "讲过"
    assert "同一个 offset" in rows["u-told"]["recall_text"]
    assert rows["u-silent"]["recall"] == "满分没讲"
    assert rows["u-silent"]["recall_text"] == ""
    assert rows["u-broken"]["recall"] == "没讲"

    printed = capsys.readouterr().out
    assert "同一个 offset" not in printed, "原文只进 CSV，不进终端主视图"
    assert silent[:12] in printed and broken[:12] in printed


def test_the_learner_page_asks_him_to_retell_when_he_has_not(env):
    """交了卷却没讲一遍：学生自己这一页要提这件事（B-58 的学生侧）。

    只放到助教的 `host.py recall` 里等于假定"几天后会有人来问他"。最可能的情形是
    他刚看完报告、觉得自己做对了——而"满分但讲不出来"正是这套工具要抓的那一格。
    """
    sessions, submit_fn, _registry = env
    token = sessions.create(TASK_ID, user_id="u-retell")["token"]
    sessions.call(token, "fetch_orders", {"offset": 0, "limit": 10})
    sessions.call(token, "write_report",
                  {"path": "/workspace/report.json", "content": '{"valid_count": 9}'})
    report, _created = sessions.grade(token, submit_fn)
    sid = report["submission"]["submission_id"]

    base = {"title": "标题", "scenario": "题面", "tools": []}
    nudged = hosting.render_landing(sessions.get(token), base, "http://x",
                                    report_url=f"/s/{token}/report", retell_pending=True)
    assert "还差一步" in nudged and "讲一遍" in nudged
    assert f'href="/s/{token}/report"' in nudged
    assert "不打分" in nudged, "得说清它不打分，否则会被当成附加题"

    quiet = hosting.render_landing(sessions.get(token), base, "http://x",
                                   report_url=f"/s/{token}/report", retell_pending=False)
    assert "还差一步" not in quiet

    # 没交卷时也不该出现：那时没有"这一份"可讲（页面此时在讲做题步骤）
    blank = hosting.render_landing(sessions.create(TASK_ID, user_id="u-fresh"),
                                   base, "http://x", retell_pending=False)
    assert "还差一步" not in blank and "让 AI 助手对着这个地址做" in blank


def test_the_learner_page_asks_him_to_retell_when_he_has_not(env):
    """交了卷却没讲一遍：学生自己这一页要提这件事（B-58 的学生侧）。

    只放到助教的 `host.py recall` 里等于假定"几天后会有人来问他"。最可能的情形是
    他刚看完报告、觉得自己做对了——而"满分但讲不出来"正是这套工具要抓的那一格。
    """
    sessions, submit_fn, _registry = env
    token = sessions.create(TASK_ID, user_id="u-retell")["token"]
    sessions.call(token, "fetch_orders", {"offset": 0, "limit": 10})
    sessions.call(token, "write_report",
                  {"path": "/workspace/report.json", "content": '{"valid_count": 9}'})
    report, _created = sessions.grade(token, submit_fn)
    sid = report["submission"]["submission_id"]

    base = {"title": "标题", "scenario": "题面", "tools": []}
    nudged = hosting.render_landing(sessions.get(token), base, "http://x",
                                    report_url=f"/s/{token}/report", retell_pending=True)
    assert "还差一步" in nudged and "讲一遍" in nudged
    assert f'href="/s/{token}/report"' in nudged
    assert "不打分" in nudged, "得说清它不打分，否则会被当成附加题"

    quiet = hosting.render_landing(sessions.get(token), base, "http://x",
                                   report_url=f"/s/{token}/report", retell_pending=False)
    assert "还差一步" not in quiet

    # 没交卷时也不该出现：那时没有"这一份"可讲（页面此时在讲做题步骤）
    blank = hosting.render_landing(sessions.create(TASK_ID, user_id="u-fresh"),
                                   base, "http://x", retell_pending=False)
    assert "还差一步" not in blank and "让 AI 助手对着这个地址做" in blank
