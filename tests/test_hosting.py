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
