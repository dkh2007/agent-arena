"""B-49 的判据：日志能不能当证据。

规则只有一条：**同一份任务、同一串参数，服务端返回是确定的**。所以重放一次，
逐字节比对——一致就放行，不一致就点名第几次、哪个字段。

这组测试守三件事：
1. 真跑出来的日志必须全部一致（否则工具会误伤诚实的人）；
2. 改了状态码、改了返回体里的数字，都要被抓出来并指出位置；
3. 日志里出现任务没声明的工具，判 unknown，**不去猜它该返回什么**。
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import lab, log_verify  # noqa: E402
from arena.diagnosis_report import load_task  # noqa: E402

TASKS = ROOT / "tasks"


def _post(base: str, tool: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{base}/tools/{tool}", json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture(scope="module")
def real_log(tmp_path_factory) -> Path:
    """跑一遍真流程，留一份没动过手脚的日志。"""
    task = load_task(TASKS / "dbg-001-order-report.yaml")
    path = tmp_path_factory.mktemp("log") / "calls.jsonl"
    call_log = lab.CallLog(path)
    server, _ = lab.serve(task, port=0, on_call=call_log)
    base = "http://%s:%d" % server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _post(base, "fetch_orders", {"offset": 0, "limit": 10})
        _post(base, "fetch_orders", {"offset": 0, "limit": 99})   # 越界 → 400
        _post(base, "write_report", {"path": "/workspace/report.json", "content": "{}"})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        call_log.close()
    return path


def _tamper(source: Path, target: Path, mutate) -> Path:
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    mutate(rows)
    target.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return target


def test_clean_log_matches_every_call(real_log):
    """诚实的人不能被误伤：真跑出来的日志，一次都不能报不一致。"""
    task = load_task(TASKS / "dbg-001-order-report.yaml")
    result = log_verify.verify_log(real_log, task)
    assert result["mismatches"] == []
    assert result["unknown_tools"] == []
    assert result["matched"] == result["total"] == 3


def test_tampered_status_points_at_the_call(real_log, tmp_path):
    """把那次 400 改成 200（伪造"参数一次就对"）必须被抓，并说清是第几次。"""
    def mutate(rows):
        for row in rows:
            if row.get("call_index") == 2:
                row["http_status"] = 200

    path = _tamper(real_log, tmp_path / "t1.jsonl", mutate)
    task = load_task(TASKS / "dbg-001-order-report.yaml")
    result = log_verify.verify_log(path, task)
    assert len(result["mismatches"]) == 1
    item = result["mismatches"][0]
    assert item["call_index"] == 2 and item["field"] == "http_status"
    assert item["recorded"] == 200 and item["replayed"] == 400


def test_tampered_body_is_located_inside_the_payload(real_log, tmp_path):
    """改返回体里的一个数字，也要指到具体位置，不是只说"不一致"。"""
    def mutate(rows):
        for row in rows:
            if row.get("call_index") == 1:
                row["body"]["orders"][0]["amount"] = 999.0

    path = _tamper(real_log, tmp_path / "t2.jsonl", mutate)
    task = load_task(TASKS / "dbg-001-order-report.yaml")
    item = log_verify.verify_log(path, task)["mismatches"][0]
    assert item["call_index"] == 1
    assert "orders" in item["field"] and "amount" in item["field"], item["field"]


def test_unknown_tool_is_flagged_not_guessed(real_log, tmp_path):
    """任务没声明的工具：判 unknown，**不请求**。猜它该返回什么就是把真相源搞成两份。"""
    def mutate(rows):
        rows.insert(1, {"call_index": 9, "name": "fetch_invoices",
                        "params": {}, "http_status": 200, "body": {"ok": True}})

    path = _tamper(real_log, tmp_path / "t3.jsonl", mutate)
    task = load_task(TASKS / "dbg-001-order-report.yaml")
    result = log_verify.verify_log(path, task)
    assert result["unknown_tools"], "多出来的工具没被发现"
    assert "fetch_invoices" in result["unknown_tools"][0]


def test_cli_exit_codes(real_log, tmp_path, capsys):
    """0 = 一致，3 = 对不上，2 = 用法/任务错。三个码要分得开，脚本才判得出来。"""
    assert log_verify.main(["--log", str(real_log), "--task", "dbg-001-order-report"]) == 0
    assert "没有被改过" in capsys.readouterr().out

    def mutate(rows):
        rows[0]["http_status"] = 500

    path = _tamper(real_log, tmp_path / "t4.jsonl", mutate)
    assert log_verify.main(["--log", str(path), "--task", "dbg-001-order-report"]) == 3
    assert "对不上" in capsys.readouterr().out

    assert log_verify.main(["--log", str(path), "--task", "no-such-task"]) == 2


def test_first_difference_names_the_deepest_path():
    """差异定位要一路走到底：报 `body` 等于没报。"""
    left = {"orders": [{"amount": 1.0}]}
    right = {"orders": [{"amount": 2.0}]}
    assert log_verify._first_difference(left, right, "body") == "body.orders[0].amount"
    assert log_verify._first_difference({"a": 1}, {"a": 1}, "body") == ""
    assert "b" in log_verify._first_difference({"a": 1}, {"a": 1, "b": 2}, "body")


# ---------------------------------------------------------------------------
# 一个**故意锁住的缺陷**：重放抓不到"删行"
# ---------------------------------------------------------------------------

def _run_five_calls(task, log_path, on_call):
    from arena import lab as _lab

    server, _service = _lab.serve(task, port=0, quiet=True, on_call=on_call)
    host, port = server.server_address[:2]
    base = f"http://{host}:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(name, body):
        req = urllib.request.Request(f"{base}/tools/{name}", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code

    try:
        post("fetch_orders", {"offset": 0, "limit": 10})
        # 第 2 次乱加一个题面没有的参数：工具服务照常返回 200，判分侧才判 R4
        post("fetch_orders", {"offset": 0, "limit": 10, "sort": "amount"})
        post("fetch_orders", {"offset": 10, "limit": 10})
        post("fetch_orders", {"offset": 10, "limit": 10})
        post("write_report", {"path": "/workspace/report.json",
                              "content": '{"valid_count": 17, "total_amount": 1837.99}'})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_replay_cannot_detect_a_deleted_extra_param_call(tmp_path):
    """**这是本仓已知的缺陷，不是期望行为**——锁住它，免得有人以为重放够了。

    过程：跑出 5 次调用（第 2 次乱加参数，返回 200），然后删掉那一行、重编号。
    结果：重放 0 处不一致，分数从 0.75 变成 1.00。

    为什么删不掉：
    - 重放校验的对象是"现有的这些行"，它没有"应该有几行"的独立真相源；
    - 故障额度按"第 N 次命中"计，可第 2 次命中的是 offset=0，不在故障条件里，
      抽掉它不改变其余调用的返回体。

    这也是 B-51（托管会话）存在的唯一理由：记录不经过学生的手，就没有可删的东西。
    哪天真能补上（比如服务端签名 + 单调序号回执），这条测试应该连同注释一起改掉。
    """
    from arena import lab as _lab
    from arena import store, submission_api as api

    task = load_task(TASKS / "dbg-001-order-report.yaml")
    kept_log = tmp_path / "calls.jsonl"
    call_log = _lab.CallLog(kept_log)
    _run_five_calls(task, kept_log, call_log)
    call_log.close()

    registry = api.TaskRegistry(TASKS)
    side = tmp_path / "r.db"
    api.bootstrap(side, registry)

    def grade(path, user):
        calls, artifacts = _lab.read_call_log(path)
        payload = _lab.build_submission(calls, artifacts, task, user_id=user)

        def conn_factory():
            return store.connect(side)

        return api.submit(payload, registry=registry, conn_factory=conn_factory)

    before = grade(kept_log, "u-honest")
    assert before["score"]["ratio"] < 1.0, "这次调用本来就该被扣分（乱加参数）"

    rows = [json.loads(line) for line in kept_log.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    kept = [row for row in rows if row.get("call_index") != 2]
    for index, row in enumerate(kept, 1):
        if "call_index" in row:
            row["call_index"] = index
    pruned = tmp_path / "pruned.jsonl"
    pruned.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in kept),
                      encoding="utf-8")

    result = log_verify.verify_log(pruned, task)
    assert result["mismatches"] == [], "前提变了：重放已经能抓到删行，请更新这条测试与 B-51 的理由"
    assert grade(pruned, "u-edited")["score"]["ratio"] == 1.0, (
        "删掉一行就满分——这就是本地方式的天花板"
    )
