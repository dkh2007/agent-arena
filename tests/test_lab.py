"""B-32 第 0 层「本地跑起来」入口的约束测试。

这个模块只做一件事：起 mock 服务、打印端点。所以测试也分成两组：

**能跑** — `--list` 认得全部任务、`--task` 路径与 task_id 都认、服务真能应答、
自检只碰工具清单不碰业务数据、端口被占用给得出下一步指令。

**不泄露** — 横幅里的每个字都得能在题面里找到。这条是硬约束：一旦把故障位、
故障码或期望值印出来，学习者就不用"发现服务会失败"了，B-31 那类题会直接失效。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import lab  # noqa: E402
from arena.diagnosis_report import load_task  # noqa: E402

TASKS = ROOT / "tasks"


def _all_task_files() -> list[Path]:
    return sorted(TASKS.glob("*.yaml"))


# ---------------------------------------------------------------------------
# 定位任务
# ---------------------------------------------------------------------------

def test_list_tasks_lists_every_task_in_the_catalog():
    rows = lab.list_tasks(TASKS)
    # 不写死题数（2026-11-22）：核心是「lab 扫到的 == tasks/ 下全部任务」，那是漏扫判据；
    # 题数本身由加题台账管（tests/test_task_authoring.py），这里再写一份就多一个真相源。
    assert len(rows) == len(_all_task_files()), (
        f"--list 只列出 {len(rows)} 道，而题库有 {len(_all_task_files())} 道——"
        "新增任务没被扫到，学习者就找不到它"
    )
    ids = {row[0] for row in rows}
    assert "dbg-001-order-report" in ids
    assert rows == sorted(rows), "--list 应按 task_id 排序，否则 20 道题里找起来是乱的"


@pytest.mark.parametrize("spec", ["dbg-001-order-report", "dbg-001-order-report.yaml"])
def test_resolve_task_accepts_both_id_and_filename(spec):
    path = lab.resolve_task_file(spec, TASKS)
    assert path.name == "dbg-001-order-report.yaml"


def test_resolve_task_accepts_an_absolute_path():
    target = TASKS / "ret-005-export-poll.yaml"
    assert lab.resolve_task_file(str(target), TASKS) == target.resolve()


def test_resolve_task_accepts_a_variant_whose_filename_differs_from_task_id():
    # 变体文件名与 task_id 同名前缀不总成立，按内容兜底那一支要有测试盯着
    path = lab.resolve_task_file("fix-003-tz-window-mismatch", TASKS)
    assert path.name == "fix-003-tz-window-mismatch.yaml"


def test_unknown_task_error_tells_where_it_looked():
    with pytest.raises(lab.LabError) as exc:
        lab.resolve_task_file("no-such-task", TASKS)
    msg = str(exc.value)
    assert "no-such-task" in msg
    assert str(TASKS) in msg, "报错要说清在哪个目录找的，否则用户不知道该改哪"


# ---------------------------------------------------------------------------
# 横幅：说清怎么调
# ---------------------------------------------------------------------------

def test_banner_states_endpoint_and_tool_signatures():
    task = load_task(TASKS / "dbg-001-order-report.yaml")
    text = lab.describe(task, "http://127.0.0.1:8765")
    assert "http://127.0.0.1:8765/tools/fetch_orders" in text
    assert "fetch_orders" in text and "write_report" in text
    assert "∈ [1, 10]" in text, "参数的合法区间是题面公开信息，横幅里应写出来"
    assert "不判对错" in text, "必须写明服务只回放、不判分，否则本地调通会被当成能拿分"
    assert "你是一个数据助理 Agent" in text, "题面要一起打出来，否则学习者得另外开一个窗口"


def test_banner_sample_call_uses_only_required_params():
    task = load_task(TASKS / "dbg-001-order-report.yaml")
    sample = lab._sample_params(task, "fetch_orders")
    declared = {p["name"]: p for p in task["tools"][0]["parameters"]}
    assert set(sample) == {k for k, v in declared.items() if v.get("required")}
    assert sample["limit"] == 1, "示例用声明下限，别替学习者选一个页大小"
    assert sample["offset"] == 0


def test_banner_does_not_leak_judging_internals():
    """每个字都得能在题面里找到。泄露判分信息 = 直接把题做掉一半。"""
    # 禁的是**内部字段名与判分枚举值**，不是普通词：`ret-002-second-page-fault`
    # 的 task_id 里本来就有 `fault`，题目标题也写着「故障在第三页」——那是题面信息，
    # 禁掉它等于禁止打印题号。
    banned = ("offset_covers", "http_status:", "no_retry", "fault_then_moved_on",
              "R2_report_correct", "rubric_", "weight:", "test_inputs.expected")
    for path in _all_task_files():
        task = load_task(path)
        text = lab.describe(task, "http://127.0.0.1:8765")
        for word in banned:
            assert word not in text, f"{path.name} 的横幅泄露了 {word!r}"
        expected = (task.get("test_inputs") or {}).get("expected") or {}
        for key, value in expected.items():
            if isinstance(value, float):
                assert str(value) not in text, (
                    f"{path.name} 的横幅泄露了期望值 {key}={value}"
                )


def test_banner_omits_fault_position_even_when_task_has_one():
    task = load_task(TASKS / "ret-005-export-poll.yaml")
    offset = task["fault_injection"]["match"]["offset_covers"]
    text = lab.describe(task, "http://127.0.0.1:8765")
    assert f"offset_covers" not in text
    # 故障位是个小整数，单独查会有误报（题面里的条数可能撞上），
    # 所以查"它和相邻字段一起出现"的形态
    assert f"第 {offset + 1} 条" not in text


# ---------------------------------------------------------------------------
# 服务真的能应答
# ---------------------------------------------------------------------------

@pytest.fixture()
def running(tmp_path):
    task = load_task(TASKS / "dbg-001-order-report.yaml")
    server, service = lab.serve(task, port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}", service
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_server_answers_tool_list(running):
    base, _service = running
    assert lab.probe(base) == ["fetch_orders", "write_report"]


def test_server_answers_a_pagination_call(running):
    base, _service = running
    req = urllib.request.Request(
        f"{base}/tools/fetch_orders",
        json.dumps({"offset": 0, "limit": 2}).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200
    assert len(body["orders"]) == 2
    assert body["total"] == 20


def test_probe_does_not_touch_business_data(running):
    """自检只打 /tools：调了 fetch_orders 就等于替学习者消耗了一次故障额度。"""
    base, service = running
    lab.probe(base)
    assert service.calls_served == 0, (
        f"自检触发了 {service.calls_served} 次工具调用——"
        "per_submission 额度会被本地预热吃掉，学习者看到的第一次请求就变了"
    )


def test_port_in_use_says_which_flag_to_change():
    task = load_task(TASKS / "dbg-001-order-report.yaml")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    busy = sock.getsockname()[1]
    try:
        with pytest.raises(lab.LabError) as exc:
            lab.serve(task, port=busy)
        msg = str(exc.value)
        assert str(busy) in msg and "--port" in msg
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_list_prints_every_task(capsys):
    assert lab.main(["--list", "--catalog", str(TASKS)]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == len(_all_task_files()), "一行一道题，别漏也别多空行"
    assert "dbg-001-order-report" in out


def test_cli_without_task_or_list_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        lab.main(["--catalog", str(TASKS)])
    assert exc.value.code == 2
    assert "--list" in capsys.readouterr().err


def test_cli_unknown_task_exits_2_with_message(capsys):
    assert lab.main(["--task", "nope", "--catalog", str(TASKS)]) == 2
    assert "nope" in capsys.readouterr().out


def test_shim_runs_without_pythonpath():
    """`python scripts/lab.py` 必须零配置可用——学习者第一步不该懂 PYTHONPATH。"""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "lab.py"), "--list"],
        capture_output=True, text=True, timeout=120,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("\n") == len(_all_task_files())


def test_cli_really_serves_after_starting():
    """子进程跑真 CLI，再发请求。

    进程内测试抓不到的一类错误：`make_server` 只绑 socket，不调 `serve_forever`
    就没人 accept。如果自检排在起线程之前，进程内测试照样绿（夹具先起了线程），
    而学习者敲命令时看到的是卡死。2026-11-22 就是这么抓到一次的。
    """
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "arena.lab", "--task", "dbg-001-order-report",
         "--port", "0", "--catalog", str(TASKS)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=env, cwd=str(ROOT),
    )
    lines: list[str] = []

    def _read_until_probe() -> None:
        for line in proc.stdout:
            lines.append(line)
            if line.startswith("[自检]"):
                return

    reader = threading.Thread(target=_read_until_probe, daemon=True)
    reader.start()
    try:
        reader.join(timeout=40)
        assert not reader.is_alive(), (
            "CLI 40 秒内没打出 [自检] 行，服务多半卡在 accept 之前。已收到：\n"
            + "".join(lines)
        )
        base = next(
            line.split()[1] for line in lines if line.startswith("服务地址")
        )
        with urllib.request.urlopen(f"{base}/tools", timeout=5) as resp:
            assert resp.status == 200
            assert json.loads(resp.read().decode("utf-8"))["tools"] == [
                "fetch_orders", "write_report"
            ]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - 兜底
            proc.kill()


# ---------------------------------------------------------------------------
# 第 1 层：--show-trace（看自己的调用序列）
#
# 两种输入都得认：报告里平台算好的 `trace_view`，和自己手上那份原始提交的 `trace`。
# 一条硬约束跟第 0 层同源：**不打印返回体**——返回里的行数就是答案。
# ---------------------------------------------------------------------------

def _raw_trace_file() -> Path:
    return ROOT / "fixtures" / "raw-perfect.json"


def test_show_trace_accepts_a_raw_submission(capsys):
    assert lab.main(["--show-trace", str(_raw_trace_file())]) == 0
    out = capsys.readouterr().out
    assert "调用序列" in out
    assert "fetch_orders" in out and "offset=10" in out
    assert "error (500)" in out, "失败那次的状态码必须露出来，否则看不出自己遇没遇到故障页"


def test_show_trace_accepts_a_report_file(tmp_path, capsys):
    report = {"submission": {"task_id": "dbg-001-order-report"},
              "trace_view": [{"call_index": 1, "name": "fetch_orders",
                              "params": {"offset": 0, "limit": 5}, "status": "success"}]}
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    assert lab.main(["--show-trace", str(path)]) == 0
    out = capsys.readouterr().out
    assert "dbg-001-order-report" in out and "limit=5" in out


def test_show_trace_never_prints_the_response_body(capsys):
    """答案就是返回里的『10 orders』『合计』。序列里只该有请求与状态码。"""
    assert lab.main(["--show-trace", str(_raw_trace_file())]) == 0
    out = capsys.readouterr().out
    assert "10 orders" not in out
    assert "result_summary" not in out


def test_show_trace_missing_file_exits_2(capsys):
    assert lab.main(["--show-trace", "no-such-file.json"]) == 2
    assert "找不到文件" in capsys.readouterr().out


def test_show_trace_rejects_json_without_trace(tmp_path, capsys):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    assert lab.main(["--show-trace", str(path)]) == 2
    out = capsys.readouterr().out
    assert "trace_view" in out and "trace" in out, "要说清两种输入都行，别只说『格式不对』"


def test_show_trace_does_not_need_a_task_or_start_a_server(capsys):
    """这条入口是纯读文件：给 --show-trace 就返回，别把 mock 服务起起来占端口。"""
    assert lab.main(["--show-trace", str(_raw_trace_file()), "--catalog", str(TASKS)]) == 0
    assert "服务地址" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 跑完怎么交回来（B-46）：服务端日志 → 提交
# ---------------------------------------------------------------------------

def _post(base: str, tool: str, body: dict) -> tuple[int, dict]:
    """打一次工具，成功和 4xx 都要拿到 (状态码, 返回体) —— 失败调用才是重试题的证据。"""
    req = urllib.request.Request(
        f"{base}/tools/{tool}", json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture()
def logged(tmp_path):
    task = load_task(TASKS / "dbg-001-order-report.yaml")
    log_path = tmp_path / "calls.jsonl"
    call_log = lab.CallLog(log_path)
    server, _service = lab.serve(task, port=0, on_call=call_log)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}", log_path, task
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        call_log.close()


def test_call_log_numbers_calls_on_the_server_side(logged):
    """编号由服务端自己数：1 起、连续、按到达顺序。

    这条是「日志能不能当证据」的根——编号若由学习者/Agent 给，就能删掉一次失败调用。
    """
    base, log_path, _task = logged
    _post(base, "fetch_orders", {"offset": 0, "limit": 5})
    _post(base, "fetch_orders", {"offset": 5, "limit": 5})
    calls, _artifacts = lab.read_call_log(log_path)
    assert [c["call_index"] for c in calls] == [1, 2]
    assert [c["name"] for c in calls] == ["fetch_orders", "fetch_orders"]


def test_call_log_keeps_the_failed_call_with_http_status(logged):
    """200 的日志不稀奇，要证明 400 也照记——不然 R3/R4 就没有可判的证据。"""
    base, log_path, _task = logged
    status, _body = _post(base, "fetch_orders", {"offset": 0, "limit": 99})
    assert status == 400, "这个参数越界本来就该被服务拒绝"
    calls, _ = lab.read_call_log(log_path)
    assert calls[0]["http_status"] == 400

    task = load_task(TASKS / "dbg-001-order-report.yaml")
    submission = lab.build_submission(calls, {}, task, user_id="u-01")
    item = submission["trace"]["tool_calls"][0]
    assert item["status"] == "error" and item["http_status"] == 400


def test_call_log_keeps_written_artifact_content(logged):
    """`write_report` 的返回体里只有路径和字节数，正文藏在入参里——不记就等于丢了产物。"""
    base, log_path, task = logged
    _post(base, "write_report", {"path": "/workspace/report.json", "content": '{"valid_count": 1}'})
    calls, artifacts = lab.read_call_log(log_path)
    assert artifacts == {"/workspace/report.json": '{"valid_count": 1}'}
    submission = lab.build_submission(calls, artifacts, task, user_id="u-01")
    assert submission["artifacts"]["/workspace/report.json"] == '{"valid_count": 1}'


def test_build_submission_withholds_platform_derivations(logged):
    """F1：`trace_audit` / `assertion_results` 是平台算的，提交里出现就是自报结论。"""
    base, log_path, task = logged
    _post(base, "fetch_orders", {"offset": 0, "limit": 5})
    calls, artifacts = lab.read_call_log(log_path)
    submission = lab.build_submission(calls, artifacts, task, user_id="u-01")
    assert set(submission) == {"task_id", "task_version", "user_id", "agent_id",
                               "trace", "artifacts"}
    assert submission["task_version"] == task["task_version"], "版本号要从任务里取，不能让提交方自己写"


def test_from_log_cli_writes_a_submittable_file(logged, tmp_path, capsys):
    base, log_path, _task = logged
    _post(base, "fetch_orders", {"offset": 0, "limit": 5})
    _post(base, "write_report", {"path": "/workspace/report.json", "content": "{}"})
    out = tmp_path / "submission.json"
    code = lab.main(["--from-log", str(log_path), "--task", "dbg-001-order-report",
                     "--user", "u-trial-01", "--out", str(out), "--catalog", str(TASKS)])
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["user_id"] == "u-trial-01"
    assert len(data["trace"]["tool_calls"]) == 2
    assert data["artifacts"], "产物没跟出来，等于交了半份"
    assert "已写出" in capsys.readouterr().out


def test_from_log_rejects_a_log_without_calls(tmp_path, capsys):
    """空日志是最常见的失败：服务没带 --log 起来，或他们发的是别的文件。"""
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    code = lab.main(["--from-log", str(path), "--task", "dbg-001-order-report",
                     "--catalog", str(TASKS)])
    assert code == 2
    assert "没有一次工具调用" in capsys.readouterr().out


def test_from_log_needs_a_task(tmp_path, capsys):
    path = tmp_path / "c.jsonl"
    path.write_text(json.dumps({"call_index": 1, "name": "fetch_orders",
                                "params": {}, "http_status": 200, "body": {}}) + "\n",
                    encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        lab.main(["--from-log", str(path), "--catalog", str(TASKS)])
    assert exc.value.code == 2
    assert "--task" in capsys.readouterr().err


def test_logged_trial_ends_in_a_real_report(logged, tmp_path):
    """整条链的判据：他们交回来的日志，能走完提交接口变成一份报告。

    这是「让他们跑」这件事的唯一验收点——只看日志长得对不够，要出得来报告。
    """
    from arena import store, submission_api as api

    base, log_path, task = logged
    _post(base, "fetch_orders", {"offset": 0, "limit": 5})
    _post(base, "fetch_orders", {"offset": 0, "limit": 99})   # 越界，留一条失败调用
    _post(base, "write_report", {"path": "/workspace/report.json", "content": "{}"})
    calls, artifacts = lab.read_call_log(log_path)
    payload = lab.build_submission(calls, artifacts, task, user_id="u-01")

    db = tmp_path / "trial.db"
    registry = api.TaskRegistry(TASKS)

    def conn_factory():
        return store.connect(db)

    api.bootstrap(db, registry)
    report = api.submit(payload, registry=registry, conn_factory=conn_factory)
    assert report["trace_audit"]["call_count"] == 3
    assert report["trace_audit"]["schema_valid"] is True, report["trace_audit"]["invalid_reasons"]
    assert len(report["assertions"]) >= 3, "报告里没有断言就不是诊断"


def test_call_log_continues_numbering_across_restarts(tmp_path):
    """重跑一次不重置编号。

    不续记就会出现两条 `call_index=1`，交上去被判 `not_sequential`——
    学习者只是把服务停了一次，报告却说他的过程不合规。
    """
    path = tmp_path / "calls.jsonl"
    first = lab.CallLog(path)
    first("fetch_orders", {"offset": 0, "limit": 5}, 200, {"orders": [1], "total": 20})
    first.close()
    second = lab.CallLog(path)
    second("write_report", {"path": "/workspace/report.json", "content": "{}"}, 200, {"written": 1})
    second.close()

    calls, artifacts = lab.read_call_log(path)
    assert [c["call_index"] for c in calls] == [1, 2]
    assert artifacts, "第二次跑的产物也要在"
