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
