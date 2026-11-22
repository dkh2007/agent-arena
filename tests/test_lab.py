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
    assert len(rows) == len(_all_task_files()) == 20, (
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
    assert out.count("\n") == 20
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
    assert result.stdout.count("\n") == 20


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
