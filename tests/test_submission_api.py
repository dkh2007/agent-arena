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
from urllib.parse import urlencode

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
    """起一个真服务，返回可调用的句柄。

    **`call` 默认带管理口令**：这个夹具是"维护者视角"——大部分用例关心的是报告内容与
    协议，不是怎么证明身份。要测"没有凭证"的情形用 `anonymous`（同一条路由、不带 header）。
    这不是给鉴权开后门：`anonymous` 与线上学生的请求一模一样，鉴权的判据在那条路线上测。
    """
    db = tmp_path / "arena.db"
    srv = api.make_server(db, port=0, quiet=True, admin_token=ADMIN)
    host, port = srv.server_address[:2]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()

    def anon(method: str, path: str, body=None, raw: bytes | None = None):
        conn = http.client.HTTPConnection(host, port, timeout=15)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        payload = resp.read().decode("utf-8")
        conn.close()
        return resp.status, (json.loads(payload) if payload else None)

    def call(method: str, path: str, body=None, raw: bytes | None = None):
        conn = http.client.HTTPConnection(host, port, timeout=15)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=data,
                     headers={"Content-Type": "application/json", "X-Admin-Token": ADMIN})
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
        yield SimpleNamespace(host=host, port=port, db=db, srv=srv,
                             call=call, anonymous=anon, count=count,
                             admin={"X-Admin-Token": ADMIN})
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


def _get_html(live, path: str, *, anonymous: bool = False) -> tuple[int, str, str]:
    """取一页 HTML。默认带管理口令（维护者视角）；`anonymous=True` 就是学生/陌生人的裸请求。"""
    headers = {} if anonymous else dict(live.admin or {})
    conn = http.client.HTTPConnection(live.host, live.port, timeout=15)
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    payload = resp.read().decode("utf-8")
    content_type = resp.getheader("Content-Type") or ""
    conn.close()
    return resp.status, content_type, payload


def test_report_by_id_needs_the_session_token(hosted):
    """凭 id 不再能读报告：那等于把全班成绩挂在"猜不猜得到 UUID"上。

    收口前：`roster` 的 CSV、报告页 URL、复查脚本都会带着 id 到处走，
    一场活动结束后，一条链接的持有者能看全班的逐条断言与期望值。

    一律回 **404 而不是 403**：403 等于告诉对方"这个 id 存在，只是你没权限"。
    """
    session = _open(hosted)
    token = session["session"]["token"]
    hosted.json_call("POST", f"/s/{token}/tools/fetch_orders", {"offset": 0, "limit": 10})
    status, payload = hosted.json_call("POST", f"/s/{token}/submit")
    assert status in (200, 201), payload
    sid = payload["submission_id"]

    # 1) 裸的上旧路 → 看不见
    assert hosted.call("GET", f"/submissions/{sid}/report.html")[0] == 404
    assert hosted.json_call("GET", f"/submissions/{sid}")[0] == 404
    assert hosted.json_call("GET", f"/submissions/{sid}/reappraisals")[0] == 404

    # 2) 带上自己的 token → 通（两条路都通）
    assert hosted.call("GET", f"/submissions/{sid}/report.html?token={token}")[0] == 200
    status, body = hosted.json_call("GET", f"/submissions/{sid}?token={token}")
    assert status == 200 and 0.0 <= body["score"]["ratio"] <= 1.0

    # 3) 别人的 token → 同样看不见
    other = _open(hosted)["session"]["token"]
    assert hosted.json_call("GET", f"/submissions/{sid}?token={other}")[0] == 404


def test_report_by_session_token_is_the_front_door(hosted):
    """`/s/<token>/report` 是正规入口：交完卷 303 就落在这里，凭手上的链接就能看。"""
    token = _open(hosted)["session"]["token"]
    hosted.json_call("POST", f"/s/{token}/tools/fetch_orders", {"offset": 0, "limit": 10})
    hosted.json_call("POST", f"/s/{token}/submit")

    status, page, _loc, ctype = hosted.call("GET", f"/s/{token}/report")
    assert status == 200 and ctype.startswith("text/html")
    assert "订单数据核对报告" in page
    assert not re.search(r"\bR\d+_[a-z_]+\b", page)


def test_report_before_submitting_says_so(hosted):
    """还没交卷时看报告：409 说"还没有交卷记录"，不是 404"没有这份"、也不是空报告。"""
    token = _open(hosted)["session"]["token"]
    status, body = hosted.json_call("GET", f"/s/{token}/report")
    assert status == 409 and body["error"]["code"] == "nothing_to_grade"


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


# ── 7. 托管会话：学生只拿一条链接（B-51） ───────────────────────────────────

ADMIN = "k-trial"


def _hosted_server(tmp_path, *, max_calls: int, name: str):
    db = tmp_path / f"{name}.db"
    srv = api.make_server(db, port=0, quiet=True, admin_token=ADMIN, max_calls=max_calls)
    host, port = srv.server_address[:2]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()

    def call(method: str, path: str, body=None, headers: dict | None = None):
        conn = http.client.HTTPConnection(host, port, timeout=20)
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Content-Type": "application/json"}
        hdrs.update(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read().decode("utf-8")
        location = resp.getheader("Location")
        ctype = resp.getheader("Content-Type") or ""
        conn.close()
        return resp.status, payload, location, ctype

    def json_call(method: str, path: str, body=None, headers: dict | None = None):
        status, payload, _loc, _ct = call(method, path, body, headers)
        return status, (json.loads(payload) if payload else None)

    def form_call(method: str, path: str, fields: dict[str, str]):
        """浏览器表单：`application/x-www-form-urlencoded`，与页面上的 `<form>` 一致。"""
        conn = http.client.HTTPConnection(host, port, timeout=20)
        data = urlencode(fields).encode("utf-8")
        conn.request(method, path, body=data,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        payload = resp.read().decode("utf-8")
        location = resp.getheader("Location")
        ctype = resp.getheader("Content-Type") or ""
        conn.close()
        return resp.status, payload, location, ctype

    try:
        yield SimpleNamespace(host=host, port=port, db=db, call=call, json_call=json_call,
                             form_call=form_call, admin={"X-Admin-Token": ADMIN})
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


@pytest.fixture()
def hosted(tmp_path):
    """预算只有 2 次：让"超预算"能在三次调用内测到。"""
    yield from _hosted_server(tmp_path, max_calls=2, name="hosted")


@pytest.fixture()
def roomy(tmp_path):
    """预算宽松：测正常做题时要能连打三五次（含一次故意失败）。"""
    yield from _hosted_server(tmp_path, max_calls=10, name="roomy")


def _open(hosted, task_id: str = "dbg-001-order-report") -> dict:
    status, body = hosted.json_call("POST", "/sessions", {"task_id": task_id}, hosted.admin)
    assert status == 201, body
    return body


def test_opening_a_session_needs_the_admin_token(hosted):
    """没有口令就不该能开会话——这条路由能消耗服务器资源，默认必须是关的。"""
    status, body = hosted.json_call("POST", "/sessions", {"task_id": "dbg-001-order-report"})
    assert status == 403 and body["error"]["code"] == "forbidden"

    status, body = hosted.json_call("POST", "/sessions", {"task_id": "dbg-001-order-report"},
                                    {"X-Admin-Token": "wrong"})
    assert status == 403


def test_hosting_is_off_unless_the_server_was_started_with_a_token(tmp_path):
    """没加 `--admin-token` 的服务上，这条路由存在但拒绝——不是"给个默认口令"。

    自己起一个不带口令的服务：`live` 夹具现在带口令（读报告要凭证），
    不能拿它来测"没配口令"。
    """
    srv = api.make_server(tmp_path / "no-token.db", port=0, quiet=True)
    host, port = srv.server_address[:2]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        conn = http.client.HTTPConnection(host, port, timeout=15)
        conn.request("POST", "/sessions", body=json.dumps({"task_id": "dbg-001-order-report"}),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        conn.close()
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)
    assert resp.status == 403
    assert payload["error"]["code"] == "session_hosting_disabled"


def test_unknown_task_is_refused_before_a_session_exists(hosted):
    status, body = hosted.json_call("POST", "/sessions", {"task_id": "no-such-task"}, hosted.admin)
    assert status == 404 and body["error"]["code"] == "unknown_task"


def test_the_landing_page_holds_the_address_and_no_answers(hosted):
    """学生打开链接看到的那一页：地址、工具、次数。答案字段一个都不能有。"""
    session = _open(hosted)
    status, page, _loc, ctype = hosted.call("GET", f"/s/{session['session']['token']}")
    assert status == 200 and ctype.startswith("text/html")
    token = session["session"]["token"]
    assert f"/s/{token}/tools/" in page
    assert "fetch_orders" in page and "write_report" in page
    for leaked in ("rubric", "expected", "fault_injection", "1837.99"):
        assert leaked not in page, f"页面上出现了 {leaked}"
    assert "<script" not in page.lower()


def test_tool_calls_pass_the_real_status_through(roomy):
    """故意失败那次必须**原样回 500**。吞成 200，这道题就没有"会不会重试"可考了。"""
    session = _open(roomy)
    token = session["session"]["token"]
    status, body = roomy.json_call(
        "POST", f"/s/{token}/tools/fetch_orders", {"offset": 0, "limit": 10})
    assert status == 200 and body["orders"], "第一页应该拿到数据"
    status, _body = roomy.json_call(
        "POST", f"/s/{token}/tools/fetch_orders", {"offset": 10, "limit": 10})
    assert status == 500, "第一次翻到第 2 页要失败，这是题面写明的考点"
    status, body = roomy.json_call(
        "POST", f"/s/{token}/tools/fetch_orders", {"offset": 10, "limit": 10})
    assert status == 200 and body["orders"]


def test_budget_is_enforced_per_session_over_http(hosted):
    """上限 2 次：第三次 429，且说明该怎么办——不是让脚本对着 500 乱撞。"""
    session = _open(hosted)
    token = session["session"]["token"]
    hosted.json_call("POST", f"/s/{token}/tools/fetch_orders", {"offset": 0, "limit": 10})
    hosted.json_call("POST", f"/s/{token}/tools/fetch_orders", {"offset": 10, "limit": 10})
    status, body = hosted.json_call(
        "POST", f"/s/{token}/tools/fetch_orders", {"offset": 10, "limit": 10})
    assert status == 429 and body["error"]["code"] == "session_exhausted"
    assert "重试" in body["error"]["message"]


def test_submitting_from_the_browser_redirects_to_the_report(roomy):
    """整条链在浏览器里走完：做题 → 点交卷 → 303 → 报告页（含训练单与复述框）。"""
    session = _open(roomy)
    token = session["session"]["token"]
    roomy.json_call("POST", f"/s/{token}/tools/fetch_orders", {"offset": 0, "limit": 10})
    roomy.json_call("POST", f"/s/{token}/tools/write_report",
                    {"path": "/workspace/report.json", "content": '{"valid_count": 9}'})

    status, _payload, location, _ctype = roomy.call("GET", f"/s/{token}/submit")
    # 交卷后落在**凭 token 的报告地址**上，不再落在 `/submissions/<id>/report.html`：
    # 后者现在也要凭证，而 token 就在手里，没有理由再绕一圈。
    assert status == 303 and location == f"/s/{token}/report"

    status, page, _loc, ctype = roomy.call("GET", location)
    assert status == 200 and ctype.startswith("text/html")
    assert "订单数据核对报告" in page
    assert not re.search(r"\bR\d+_[a-z_]+\b", page)
    assert "<textarea" in page, "复述框没渲染，说明报告页没拿到 task"


def test_submitting_twice_lands_on_the_same_report(roomy):
    """刷新或重复点交卷，不能判出两份分数。"""
    session = _open(roomy)
    token = session["session"]["token"]
    roomy.json_call("POST", f"/s/{token}/tools/fetch_orders", {"offset": 0, "limit": 10})

    _, _, first, _ = roomy.call("GET", f"/s/{token}/submit")
    status, payload = roomy.json_call("POST", f"/s/{token}/submit")
    assert status == 200 and payload["reused"] is True
    assert f"/s/{token}/report" == first


def test_submitting_an_empty_session_says_so_in_html(hosted):
    """还没做题就点交卷：给一页人话 + 回得去的链接，不是一串 JSON。"""
    session = _open(hosted)
    status, page, _loc, ctype = hosted.call("GET", f"/s/{session['session']['token']}/submit")
    assert status == 409 and ctype.startswith("text/html")
    assert "还没有任何调用记录" in page
    assert "回到做题那一页" in page


def test_a_bad_token_is_404_on_every_session_path(roomy):
    """扫路径的请求一律 404，连"这个链接存在但你没权限"都不透露。"""
    for path in ("/s/" + "a" * 43, "/s/" + "a" * 43 + "/submit",
                 "/s/" + "a" * 43 + "/tools/fetch_orders"):
        status, page, _loc, _ctype = roomy.call("GET" if "tools" not in path else "POST",
                                                 path, {} if "tools" in path else None)
        assert status == 404, (path, status, page[:80])


# ── 8. 交完卷要排期（B-55） ──────────────────────────────────────────────────

def test_a_submission_leaves_review_items_in_the_database(live):
    """真实提交路径上必须排期。

    以前 `save_schedule()` 只有两个 walk 脚本在调——复习页能显示，但那是打包时烘好的
    `reminder.json`；一个真学生做完题，库里一条复习项都没有。这条盯的就是那个断点。
    """
    status, report = live.call("POST", "/submissions", _payload(_fixture("raw-fake-retry")))
    assert status == 201
    user_id = "u-test"          # `_payload()` 里写死的提交人；报告里不带 user_id（schema 不装它）

    conn = store.connect(live.db)
    try:
        items = store.list_review_items(conn, user_id, status="pending")
        cards = store.load_fsrs_cards(conn, user_id)
    finally:
        conn.close()
    assert cards, "连 FSRS 卡都没写"
    assert items, "这道题只拿了 0.25，却没有任何复习项"
    assert all(item["source_submission_id"] for item in items), "复习项没记来源提交"


def test_a_perfect_submission_schedules_cards_but_no_review_items(live):
    """全对不排复习——这是 `scheduler` 既有口径（`c >= 1.0` 记 `passed`），这里只是钉住它。

    钉住的原因：它决定"满分的人打开提醒页什么都看不到"。若哪天要改成「成功也间隔复习」，
    这条会红，改的人必须看到这段说明再决定。
    """
    status, report = live.call("POST", "/submissions", _payload(_fixture("raw-perfect")))
    assert status == 201
    conn = store.connect(live.db)
    try:
        items = store.list_review_items(conn, "u-test", status="pending")
    finally:
        conn.close()
    assert items == []


def test_scheduling_failure_does_not_take_the_report_away(live, monkeypatch, capsys):
    """排期出错不能让学习者拿不到报告，但**不能静默**——服务器日志里要能看到。"""
    def boom(*args, **kwargs):
        raise api.sch.SchedulerError("模拟：复习排期算不出来")

    monkeypatch.setattr(api.sch, "schedule_submission", boom)
    status, report = live.call("POST", "/submissions", _payload(_fixture("raw-fake-retry")))
    assert status == 201 and report["score"]["ratio"] > 0, "报告没返回，等于把功能做没了"
    assert "排期失败" in capsys.readouterr().err


# ── 8. 复述原文收回服务器（B-58） ───────────────────────────────────────────

def _finish_a_paper(roomy) -> str:
    """开一条链接 → 翻两页 → 撞 500 → 同参数重试 → 写报告 → 交卷。返回 token。"""
    token = _open(roomy)["session"]["token"]
    for offset in (0, 10, 10):
        roomy.json_call("POST", f"/s/{token}/tools/fetch_orders",
                        {"offset": offset, "limit": 10})
    roomy.json_call("POST", f"/s/{token}/tools/write_report",
                    {"path": "/workspace/report.json",
                     "content": json.dumps({"valid_count": 17, "total_amount": 1837.99})})
    status, _payload, location, _ctype = roomy.call("GET", f"/s/{token}/submit")
    assert status == 303 and location == f"/s/{token}/report"
    return token


def test_the_report_page_offers_a_form_and_stops_promising_nothing_is_uploaded(roomy):
    """服务端这一页必须有出口（表单），而且脚注不能再说"不会上传任何东西"。

    以前那句话对离线导出的 .html 是真的，对服务器渲染的这一页是假的——
    同一句话覆盖两种形态，就是在其中一种上撒谎。
    """
    token = _finish_a_paper(roomy)
    status, page, _loc, ctype = roomy.call("GET", f"/s/{token}/report")
    assert status == 200 and ctype.startswith("text/html")
    assert f'action="/s/{token}/report/recall"' in page
    assert 'name="text"' in page, "表单里得有个能装字的字段"
    assert "<script" not in page.lower(), "这页仍然不许有脚本"
    assert "不会上传任何东西" not in page, "这一页会把他写的话交回服务器"
    assert "不打分" in page


def test_a_retell_comes_back_on_the_page_and_lands_in_the_database(roomy):
    """交上去 → 303 回报告页 → 页面上有他写的话 + 一句回执；库里存的是原文。

    这是本轮补的那个洞：以前那句"把这段文字发回给我"没有任何出口。
    """
    token = _finish_a_paper(roomy)
    typed = "第二页返回 500，我没有换参数，同一个 offset=10 又试了一次，然后才拿到数据。"

    status, _payload, location, _ctype = roomy.form_call(
        "POST", f"/s/{token}/report/recall", {"text": typed})
    assert status == 303 and location == f"/s/{token}/report?recall=saved"

    status, page, _loc, _ctype = roomy.call("GET", location)
    assert status == 200
    assert typed in page, "交上去的话要能看见，否则他不知道存的是不是这版"
    assert "记下了" in page

    conn = store.connect(roomy.db)
    try:
        rows = conn.execute("SELECT text, user_id FROM recalls").fetchall()
    finally:
        conn.close()
    assert [row["text"] for row in rows] == [typed], "库里存的必须是他打的原文，不是改写版"


def test_an_empty_retell_says_so_and_stores_nothing(roomy):
    """手滑点一下不该变成"他讲过了"。回执要说清"什么都没写"，库里一行都不多。"""
    token = _finish_a_paper(roomy)
    status, _payload, location, _ctype = roomy.form_call(
        "POST", f"/s/{token}/report/recall", {"text": "   \n  "})
    assert status == 303 and location == f"/s/{token}/report?recall=empty"

    status, page, _loc, _ctype = roomy.call("GET", location)
    assert status == 200 and "什么都没写" in page

    conn = store.connect(roomy.db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM recalls").fetchone()[0] == 0
    finally:
        conn.close()


def test_a_retell_before_submitting_is_refused(roomy):
    """没交卷就没有"这一份"，也就不该有一篇复述挂在空气上。"""
    token = _open(roomy)["session"]["token"]
    status, payload, _loc, _ctype = roomy.form_call(
        "POST", f"/s/{token}/report/recall", {"text": "我还没交卷"})
    assert status == 409
    assert json.loads(payload)["error"]["code"] == "nothing_to_grade"


def test_a_retell_on_an_unknown_link_is_404_not_500(roomy):
    """凭证不认识就 404——不是 500，也不回一句"这个 token 不存在"以外的东西。"""
    status, payload, _loc, _ctype = roomy.form_call(
        "POST", "/s/no-such-token/report/recall", {"text": "随便写写"})
    assert status == 404
    assert json.loads(payload)["error"]["code"] == "unknown_session"


def test_too_long_a_retell_is_refused_with_the_count(roomy):
    """超长不截断：告诉他写了多少字，让他自己删——截断会让他以为讲完了。"""
    token = _finish_a_paper(roomy)
    too_long = "啊" * (store.RECALL_MAX_CHARS + 1)
    status, payload, _loc, _ctype = roomy.form_call(
        "POST", f"/s/{token}/report/recall", {"text": too_long})
    assert status == 413
    error = json.loads(payload)["error"]
    assert error["code"] == "recall_too_long"
    assert str(store.RECALL_MAX_CHARS + 1) in error["message"]

    conn = store.connect(roomy.db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM recalls").fetchone()[0] == 0
    finally:
        conn.close()


def test_the_nudge_is_computed_from_the_database_not_from_a_flag(roomy):
    """端到端：交卷后学生页提「讲一遍」，交完复述后这句消失。

    这一条验的是**现算**（`store.load_recall`），不是一个由页面自己传进来的布尔值——
    传进来的布尔值在"他换了设备打开"时就会失真。
    """
    token = _finish_a_paper(roomy)

    _status, page, _loc, _ctype = roomy.call("GET", f"/s/{token}")
    assert "还差一步" in page

    roomy.form_call("POST", f"/s/{token}/report/recall", {"text": "第二页 500，我重试了"})

    _status, page, _loc, _ctype = roomy.call("GET", f"/s/{token}")
    assert "还差一步" not in page
