"""M1 / B-02 的判据锁：黑盒提交服务的契约。

分四组，每组盯一个可能悄悄出错的地方：

1. **等价性**：HTTP 入口与 `run_fixture()` 必须逐字段相等。服务最容易出的不是
   崩，而是「悄悄走了另一条判定路」——分数看起来正常，口径却已经分成两份。
2. **协议**：状态码与错误码。这类东西错不错都不影响分数，但客户端没法写。
3. **端点不可达**：判据是「200 + 全 unscored」，不是 500，也不是 fail。
   还要盯住一句文案——不能把平台采集失败写成学习者没交步骤。
4. **落库**：拒绝的请求不许留半条记录；重复 id 不覆盖。

服务用真实 socket（`port=0` 让系统分配，测试不猜端口）。不起真 socket 的话，
`ThreadingHTTPServer` 那条跨线程路径、Content-Length 与 keep-alive 的行为
全都测不到，测的只是一个假服务。
"""

from __future__ import annotations

import http.client
import re
import json
import socket
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402
from arena import trace_pipeline as tp  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"


# ── 夹具 ─────────────────────────────────────────────────────────────────────

@pytest.fixture()
def live(tmp_path):
    """起一个真服务，返回可调用的句柄。"""
    db = tmp_path / "arena.db"
    srv = api.make_server(db, port=0, quiet=True)
    host, port = srv.server_address[:2]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()

    def call(method: str, path: str, body=None, raw: bytes | None = None):
        conn = http.client.HTTPConnection(host, port, timeout=15)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        payload = resp.read().decode("utf-8")
        conn.close()
        return resp.status, (json.loads(payload) if payload else None)

    def count() -> int:
        c = store.connect(db)
        try:
            return store.count_submissions(c)
        finally:
            c.close()

    try:
        yield SimpleNamespace(host=host, port=port, db=db, srv=srv, call=call, count=count)
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def _fixture(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def _payload(raw: dict, **over) -> dict:
    """把一份夹具翻译成一次真实提交请求（外部视角：只交 submission/trace/artifacts）。

    `submission_id` 必须带上：对拍要求两边是同一个 id，否则 API 自动生成的
    id 与内部链路算出的不同，比较必然失败——那样测的是「id 不同」，不是口径。
    """
    sub = raw["submission"]
    body = {
        "task_id": sub["task_id"],
        "task_version": "2026-11-13.1",
        "user_id": "u-test",
        "agent_id": sub["agent_id"],
        "submission_id": sub["submission_id"],
        "task_git_hash": sub["task_git_hash"],
        "evaluated_at": sub["evaluated_at"],
        "invoke": sub.get("invoke"),
        "trace": raw.get("trace"),
        "artifacts": raw.get("artifacts"),
    }
    body.update(over)
    return {k: v for k, v in body.items() if v is not None or k in ("trace", "artifacts")}


# ── 1. 等价性 ────────────────────────────────────────────────────────────────

FIXTURES = ("raw-empty", "raw-no-file", "raw-fake-retry", "raw-no-trace", "raw-perfect")


@pytest.mark.parametrize("name", FIXTURES)
def test_api_equals_internal_pipeline(live, name):
    """同一份夹具，HTTP 入口与 run_fixture() 的报告必须逐字段相等。

    这是整组测试里最重要的一条：它把「HTTP 层只是一层薄协议」变成可验证的事实，
    而不是靠注释声明。不等价就意味着两套口径，分数迟早对不上。
    """
    raw = _fixture(name)
    status, body = live.call("POST", "/submissions", _payload(raw))
    assert status == 201, body
    assert body == _internal_report(raw)


def _internal_report(raw: dict) -> dict:
    """不经过 API，直接走内部链路，作为对拍的对照组。

    夹具写在 TD-038 之前，`submission` 里没有 `task_version`；真实客户端必须带
    （B-21），所以对照组也补上，否则比出来的是「夹具比客户端旧」，不是口径分歧。
    """
    task = dr.load_task(TASK_PATH)
    submission = dict(raw["submission"], task_version="2026-11-13.1")
    built = tp.recompute_all(
        tp.assemble_raw(submission, task, trace=raw.get("trace"),
                        artifacts=raw.get("artifacts")),
        task=task,
    )
    report = dr.build_report(task, built)
    dr.validate_report(report)
    return report


# ── 2. 协议 ──────────────────────────────────────────────────────────────────

def test_post_returns_report_that_passes_schema(live):
    """判据原文：返回 DTO v2 并通过 schema 校验。"""
    status, body = live.call("POST", "/submissions", _payload(_fixture("raw-perfect")))
    assert status == 201
    dr.validate_report(body)  # 不通过会抛 ReportError
    assert body["report_version"] == "v2"
    assert body["score"]["ratio"] == 1.0


def test_get_reads_back_the_same_report(live):
    raw = _fixture("raw-perfect")
    _, posted = live.call("POST", "/submissions", _payload(raw))
    sid = posted["submission"]["submission_id"]
    status, got = live.call("GET", f"/submissions/{sid}")
    assert status == 200
    assert got == posted


def test_healthz_lists_tasks_that_can_be_submitted(live):
    status, body = live.call("GET", "/healthz")
    assert status == 200
    assert body["ok"] is True
    assert "dbg-001-order-report" in body["tasks"]
    assert body["schema_version"] == store.SCHEMA_VERSION


def test_unknown_path_is_404_not_500(live):
    assert live.call("GET", "/nope")[0] == 404
    assert live.call("POST", "/nope", {})[0] == 404


def test_invalid_json_is_400(live):
    status, body = live.call("POST", "/submissions", raw=b"{not json")
    assert status == 400
    assert body["error"]["code"] == "invalid_json"


def test_unknown_task_is_404_and_names_the_known_ones(live):
    status, body = live.call("POST", "/submissions", _payload(_fixture("raw-perfect")) | {
        "task_id": "no-such-task",
    })
    assert status == 404
    assert body["error"]["code"] == "unknown_task"
    assert body["error"]["details"]["known_tasks"]


def test_trace_must_be_object_or_null(live):
    status, body = live.call("POST", "/submissions", _payload(_fixture("raw-perfect")) | {
        "trace": "not-an-object",
    })
    assert status == 400
    assert body["error"]["details"]["field"] == "trace"


# ── 3. B-21：task_version 必填 ───────────────────────────────────────────────

def test_missing_task_version_is_rejected_and_creates_nothing(live):
    """判据：缺 task_version ⇒ 拒绝入队，**不产生 Submission**。

    只断言状态码不够——真正要防的是「先落库再报错」，那会留下一条
    没有版本、永远无法归因的半条记录。
    """
    body = _payload(_fixture("raw-perfect"))
    body.pop("task_version")
    status, resp = live.call("POST", "/submissions", body)
    assert status == 400
    assert resp["error"]["code"] == "task_version_missing"
    assert live.count() == 0


def test_version_mismatch_is_a_report_not_a_rejection(live):
    """给了但版本不一致 ≠ 客户端有问题：出 voided 报告，分数不算但不判能力。"""
    body = _payload(_fixture("raw-perfect")) | {"task_version": "2020-01-01.0"}
    status, resp = live.call("POST", "/submissions", body)
    assert status == 201
    assert resp["score"]["voided"] is True
    assert resp["score"]["void_reason"] == "task_version_mismatch"
    assert resp["score"]["ratio"] == 0.0


# ── 4. 端点不可达 ────────────────────────────────────────────────────────────

def _closed_port() -> int:
    """拿一个确定没人听的端口：开一个 socket 再关掉，端口短时间内不会被复用。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_unreachable_endpoint_returns_report_not_5xx(live):
    """B-02 判据：端点不可达返回可读结果，而不是 500。"""
    body = _payload(_fixture("raw-perfect")) | {
        "agent_endpoint_url": f"http://127.0.0.1:{_closed_port()}/run",
    }
    body.pop("trace")
    body.pop("artifacts")
    status, resp = live.call("POST", "/submissions", body)
    assert status == 201, resp
    assert "error" not in resp
    dr.validate_report(resp)
    assert resp["score"]["by_state"]["unscored"] == len(resp["assertions"])


def test_unreachable_is_unscored_not_fail(live):
    """不能判 fail：产物根本没到手，判 fail 等于把平台故障记成学习者失败。"""
    body = _payload(_fixture("raw-perfect")) | {
        "agent_endpoint_url": f"http://127.0.0.1:{_closed_port()}/run",
    }
    body.pop("trace")
    body.pop("artifacts")
    _, resp = live.call("POST", "/submissions", body)
    states = {a["state"] for a in resp["assertions"]}
    assert states == {"unscored"}
    assert resp["score"]["ratio"] == 0.0
    assert resp["report_attribution"]["counts_toward_mastery"] is False
    assert resp["report_attribution"]["emit_error_patterns"] is False
    assert resp["skill_gaps"] == []


def test_unreachable_does_not_blame_learner_for_missing_process(live):
    """文案判据：本任务 requires_process=true，但"没过程"是平台没采集到。

    如果这里写成「请补交步骤」，报告就把基础设施故障算到学习者头上了——
    这正是决策 7 要禁止的那类误伤，且它只体现在文案里，很容易漏测。
    """
    body = _payload(_fixture("raw-perfect")) | {
        "agent_endpoint_url": f"http://127.0.0.1:{_closed_port()}/run",
    }
    body.pop("trace")
    body.pop("artifacts")
    _, resp = live.call("POST", "/submissions", body)
    joined = " ".join(resp["notes"])
    assert "请补交步骤" not in joined
    assert "平台未能采集" in joined
    assert "agent_endpoint_unreachable" in joined


def test_reachable_endpoint_without_evidence_is_501(live):
    """端点活着但本服务还不会主动采集（B-03）——如实说，不假装评过。"""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        body = _payload(_fixture("raw-perfect")) | {
            "agent_endpoint_url": f"http://127.0.0.1:{port}/run",
        }
        body.pop("trace")
        body.pop("artifacts")
        status, resp = live.call("POST", "/submissions", body)
        assert status == 501
        assert resp["error"]["code"] == "endpoint_capture_not_implemented"
        assert live.count() == 0
    finally:
        listener.close()


def test_probe_classifies_unsupported_scheme():
    ok, why = api.probe_endpoint("ftp://example.com/x")
    assert ok is False and why == "unsupported_scheme"


def test_probe_reports_reachable_on_live_socket():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        ok, why = api.probe_endpoint(f"http://127.0.0.1:{port}/")
        assert ok is True and why == "reachable"
    finally:
        listener.close()


# ── 5. 落库 ──────────────────────────────────────────────────────────────────

def test_duplicate_submission_id_conflicts_and_keeps_the_first(live):
    """不覆盖：分数有争议时要能翻回「当时那份」。"""
    raw = _fixture("raw-perfect")
    body = _payload(raw) | {"submission_id": "sub-dup-1"}
    assert live.call("POST", "/submissions", body)[0] == 201

    first = live.call("GET", "/submissions/sub-dup-1")[1]
    status, resp = live.call("POST", "/submissions", body | {"artifacts": {}})
    assert status == 409
    assert resp["error"]["code"] == "store_rejected"
    assert live.call("GET", "/submissions/sub-dup-1")[1] == first
    assert live.count() == 1


def test_server_generates_submission_id_when_absent(live):
    body = _payload(_fixture("raw-perfect"))
    body.pop("submission_id", None)
    status, resp = live.call("POST", "/submissions", body)
    assert status == 201
    assert resp["submission"]["submission_id"].startswith("sub-")
    assert live.count() == 1


def test_missing_user_id_is_rejected(live):
    body = _payload(_fixture("raw-perfect"))
    body.pop("user_id")
    status, resp = live.call("POST", "/submissions", body)
    assert status == 400
    assert resp["error"]["code"] == "missing_field"
    assert live.count() == 0


def test_default_binding_is_localhost_only():
    """当前没有鉴权，默认绑定必须是本机——这是安全边界，不是配置偏好。"""
    import inspect
    sig = inspect.signature(api.make_server)
    assert sig.parameters["host"].default == "127.0.0.1"


# ── 6. 读数口径：生效版 vs 当时那份（round-93）──────────────────────────────
#
# round-92 把重评接上了，但**只改了数据层，没改读接口**：库里、P 里、复习排期里
# 都是新结论，而学习者从 `GET /submissions/{id}` 拉到的还是旧的那份。
# 这组测试锁的就是"三个端点各自只表示一件事"，且默认方向是"给人看的当前结论"。

def _reappraise(live, submission_id: str, *, reason: str = "rubric: R2 标准答案改了") -> str:
    """在 live 服务的同一个库上真跑一次重评，返回用的理由。"""
    import copy

    from arena import reappraisal as rap

    task = copy.deepcopy(dr.load_task(TASK_PATH))
    hit = False
    for spec in task["rubric_credit"]:
        if spec["id"] == "R2_report_correct":
            spec["expected"]["total_amount"] += 1.0
            hit = True
    assert hit, "夹具变了：R2_report_correct 不在 rubric 里，本测试失去意义"

    c = store.connect(live.db)
    try:
        out = rap.reappraise(c, submission_id, task, reason=reason,
                             created_at="2026-11-16T09:00:00Z")
        assert out.state == "reappraised", out.state
    finally:
        c.close()
    return reason


def test_get_by_default_returns_the_effective_report_not_the_frozen_one(live):
    """重评之后，学习者拉到的必须是**当前生效**的分数。

    这是 round-92 遗留的一致性缺陷。判据不能写成"接口没报错"——旧实现同样不报错，
    它只是把 1.00 当成 0.50 之后还回 1.00。所以这条只认数字：
    默认视图的 ratio 必须等于重评后的值，而 `?view=original` 仍是 1.00。
    """
    raw = _fixture("raw-perfect")
    _, posted = live.call("POST", "/submissions", _payload(raw))
    sid = posted["submission"]["submission_id"]
    assert posted["score"]["ratio"] == 1.0

    _reappraise(live, sid)

    status, body = live.call("GET", f"/submissions/{sid}")
    assert status == 200
    assert body["score"]["ratio"] == 0.5, "默认视图还是旧结论 ⇒ 读接口没跟着改口"
    dr.validate_report(body)                       # 换了一份也仍须过 schema

    status, frozen = live.call("GET", f"/submissions/{sid}?view=original")
    assert status == 200
    assert frozen["score"]["ratio"] == 1.0, " `view=original` 必须给当时那份"
    assert frozen == posted, "当时那份被改动了 ⇒ 历史证据不再逐字节可比"

    # 生效分与"当时算出的分"是两个数，库里必须同时留得住。
    c = store.connect(live.db)
    try:
        assert store.load_submission(c, sid)["assert_score"] == 1.0
        assert store.effective_ratio(c, sid) == 0.5
    finally:
        c.close()


def test_a_typo_in_view_is_a_400_not_a_silent_fallback(live):
    """`?view=raw` 不许当成默认值。

    两份报告都能过同一份 schema、长得几乎一样，静默回落会让人把新结论
    当"当时那份"拿去对账，而且没有任何信号。
    """
    _, posted = live.call("POST", "/submissions", _payload(_fixture("raw-perfect")))
    sid = posted["submission"]["submission_id"]

    status, body = live.call("GET", f"/submissions/{sid}?view=raw")
    assert status == 400
    assert body["error"]["code"] == "bad_request"
    assert "view" in body["error"]["message"]

    # 重复给值也不算"取第一个"——两种意图同时出现时没有正确答案。
    assert live.call("GET", f"/submissions/{sid}?view=original&view=effective")[0] == 400


def test_reappraisal_chain_distinguishes_missing_from_never_reappraised(live):
    """链为空 = 「交过、没人改过分」；404 = 「这份根本不存在」。

    混成一样（都返回空数组），"我拼错了 id"会显示成"这份没被改过"——
    而这两件事的处置完全不同。
    """
    _, posted = live.call("POST", "/submissions", _payload(_fixture("raw-perfect")))
    sid = posted["submission"]["submission_id"]

    status, body = live.call("GET", f"/submissions/{sid}/reappraisals")
    assert status == 200
    assert body["reappraisals"] == []
    assert body["effective_ratio"] == body["original_ratio"] == 1.0

    assert live.call("GET", "/submissions/no-such-id/reappraisals")[0] == 404

    reason = _reappraise(live, sid)
    status, body = live.call("GET", f"/submissions/{sid}/reappraisals")
    assert status == 200
    assert len(body["reappraisals"]) == 1
    rec = body["reappraisals"][0]
    assert rec["revision"] == 1 and rec["reason"] == reason
    assert body["original_ratio"] == 1.0 and body["effective_ratio"] == 0.5
    dr.validate_report(rec["report"])


# ── 5. 申诉端点（v7 / 2026-11-24）────────────────────────────────────────────
# 「我认为判错了」是学习者唯一的主动入口。端点只落库 + 读回，不做重评——
# 接受申诉后按什么标准重算，是人的判断（`reappraisal.reappraise(task=...)`）。

def _submit_one(live, *, submission_id: str, user_id: str = "u-test") -> str:
    status, posted = live.call("POST", "/submissions", _payload(
        _fixture("raw-perfect"), submission_id=submission_id, user_id=user_id))
    assert status == 201, posted
    return posted["submission"]["submission_id"]


def test_appeal_endpoint_lands_and_reads_back(live):
    sid = _submit_one(live, submission_id="s-appeal")
    status, row = live.call("POST", f"/submissions/{sid}/appeals", {
        "user_id": "u-test",
        "reason_text": "R3 判我没重试，但第二次请求我确实重发了",
        "target_assertion": "R3_retry_evidence",
    })
    assert status == 201, row
    assert row["status"] == "pending"
    assert row["target_assertion"] == "R3_retry_evidence"
    assert row["resolved_at"] is None and row["resolution_note"] is None

    status, body = live.call("GET", f"/submissions/{sid}/appeals")
    assert status == 200
    assert [a["appeal_id"] for a in body["appeals"]] == [row["appeal_id"]]
    assert body["submission_id"] == sid


def test_appeals_on_an_unknown_submission_is_404_not_an_empty_list(live):
    """空数组的语义是"交过、没申诉过"。混成一样，"id 拼错了"会显示成"没人有异议"。"""
    assert live.call("GET", "/submissions/no-such-id/appeals")[0] == 404
    assert live.call("POST", "/submissions/no-such-id/appeals",
                     {"user_id": "u-test", "reason_text": "判错了"})[0] == 404


def test_a_second_pending_appeal_is_409(live):
    """等结果就好，别反复提——两个不同理由都算一条待办，裁决的人会以为有两件事。"""
    sid = _submit_one(live, submission_id="s-appeal-twice")
    body = {"user_id": "u-test", "reason_text": "判错了"}
    assert live.call("POST", f"/submissions/{sid}/appeals", body)[0] == 201
    status, err = live.call("POST", f"/submissions/{sid}/appeals",
                            {**body, "reason_text": "再补一句"})
    assert status == 409, err
    assert err["error"]["code"] == "appeal_already_pending"


def test_appeal_rejects_empty_reason_and_bogus_assertion(live):
    sid = _submit_one(live, submission_id="s-appeal-bad")
    assert live.call("POST", f"/submissions/{sid}/appeals",
                     {"user_id": "u-test", "reason_text": "   "})[0] == 400
    status, err = live.call("POST", f"/submissions/{sid}/appeals", {
        "user_id": "u-test", "reason_text": "判错了", "target_assertion": "R9_made_up",
    })
    assert status == 400, err
    assert err["error"]["code"] == "invalid_appeal"
    # 不存在的断言被拒之后，不许留半条记录
    assert live.call("GET", f"/submissions/{sid}/appeals")[1]["appeals"] == []


def test_appeal_cannot_be_filed_on_behalf_of_someone_else(live):
    sid = _submit_one(live, submission_id="s-appeal-other")
    status, err = live.call("POST", f"/submissions/{sid}/appeals",
                            {"user_id": "someone-else", "reason_text": "判错了"})
    assert status == 400, err
    assert "只能对自己的判分提申诉" in err["error"]["message"]


# ── 6. 只读端点：题面与报告页（B-50） ────────────────────────────────────────

TASK_PUBLIC_KEYS = {
    "task_id", "task_version", "title", "domain", "scenario",
    "primary_skill_id", "secondary_skill_ids", "difficulty",
    "family_id", "levels_total", "tools",
}


def test_tasks_endpoint_lists_every_task_with_a_whitelist_of_fields(live):
    """`/tasks` 是给前端做选题页的，任务 YAML 里有答案与故障位——**只能是白名单**。

    这条用"键集合恰等于白名单"判：多带一个键也算泄露，不靠人工比对。
    """
    status, body = live.call("GET", "/tasks")
    assert status == 200
    tasks = body["tasks"]
    assert len(tasks) == len(list((ROOT / "tasks").glob("*.yaml")))
    for task in tasks:
        assert set(task) == TASK_PUBLIC_KEYS, f"{task.get('task_id')} 多带或少带了键"
        for tool in task["tools"]:
            assert set(tool) == {"name", "description", "parameters"}, "mock 块不该发出去"
    raw = json.dumps(body, ensure_ascii=False)
    for word in ("rubric_gate", "rubric_credit", "scoring", "expected",
                 "fault_injection", "fixture_ref", "pagination"):
        assert word not in raw, f"/tasks 泄露了 {word}"


def test_task_endpoint_gives_the_prompt_a_learner_needs(live):
    status, body = live.call("GET", "/tasks/dbg-001-order-report")
    assert status == 200
    assert body["title"] and body["scenario"], "题面与标题是选题/做题的前提"
    assert [t["name"] for t in body["tools"]] == ["fetch_orders", "write_report"]
    params = body["tools"][0]["parameters"]
    assert {p["name"] for p in params} == {"offset", "limit"}
    assert body["difficulty"] == 1 and body["levels_total"] >= 1


def test_unknown_task_is_a_404_with_the_code(live):
    status, body = live.call("GET", "/tasks/no-such-task")
    assert status == 404
    assert body["error"]["code"] == "unknown_task"


def _get_html(live, path: str) -> tuple[int, str, str]:
    conn = http.client.HTTPConnection(live.host, live.port, timeout=15)
    conn.request("GET", path)
    resp = conn.getresponse()
    payload = resp.read().decode("utf-8")
    content_type = resp.getheader("Content-Type") or ""
    conn.close()
    return resp.status, content_type, payload


def test_report_html_endpoint_renders_the_page_for_a_browser(live):
    """交完就能在浏览器里看——不用先存 JSON、再跑一条命令。

    页面里带训练单与复述框（`--task` 那条路的内容），所以这里连 textarea 一起判。
    """
    _, posted = live.call("POST", "/submissions", _payload(_fixture("raw-perfect")))
    sid = posted["submission"]["submission_id"]

    status, content_type, page = _get_html(live, f"/submissions/{sid}/report.html")
    assert status == 200
    assert content_type.startswith("text/html"), content_type
    assert "订单数据核对报告" in page, "标题没带出来"
    assert not re.search(r"\bR\d+_[a-z_]+", page), "报告页里出现了断言 id"
    assert "<textarea" in page, "复述框没渲染（说明 task 没传进去）"
    assert "<script" not in page.lower()


def test_report_html_for_an_unknown_submission_is_404(live):
    status, _content_type, _page = _get_html(live, "/submissions/sub-nope/report.html")
    assert status == 404
