"""拿一份 `calls.jsonl` 回到同一份任务上重放，验证它没被改过（B-49）。

## 为什么需要它

`--log` 把过程记在服务端，这挡掉了"Agent 自己少写一次失败调用"这条路。
但日志落在**磁盘上**，是文本：谁都能改一行 `http_status` 再发回来。

判据很简单，也不需要信任何人：**同一份任务、同一串参数，服务端的返回是确定的**。
把记录里的每次调用重放一遍，状态码与返回体逐字节比对——
一致说明这份日志只能来自真跑；不一致就点名第几次、哪个字段。

## 它与 `platform_capture` 的分工

| | 何时 | 防的是什么 |
|---|---|---|
| `platform_capture` | 跑的时候 | Agent 自报 trace 与平台账本不符（实时对账） |
| 本模块 | 收回来之后 | 日志文件被人改过（事后复核） |

两条都只在"工具服务是我们起的"这个前提下成立——这已经是 B-46 的既定部署方式。

## 一条不做的事

**不重放 `write_report` 之外没有声明的工具**：日志里出现任务没声明的工具，直接判
`unknown_tool`，不去猜它该返回什么。
"""

from __future__ import annotations

import argparse
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .diagnosis_report import ReportError, load_task
from .lab import DEFAULT_CATALOG, LabError, read_call_log, resolve_task_file, serve

TASK_PATH = DEFAULT_CATALOG


def _post(base: str, tool: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{base}/tools/{tool}", json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _first_difference(recorded: Any, replayed: Any, path: str = "") -> str:
    """指出第一个不同的位置。整段 diff 打出来没人看，且可能很长。"""
    if isinstance(recorded, dict) and isinstance(replayed, dict):
        for key in recorded:
            if key not in replayed:
                return f"{path}.{key}（重放里没有）"
            found = _first_difference(recorded[key], replayed[key], f"{path}.{key}")
            if found:
                return found
        for key in replayed:
            if key not in recorded:
                return f"{path}.{key}（记录里没有）"
        return ""
    if isinstance(recorded, list) and isinstance(replayed, list):
        if len(recorded) != len(replayed):
            return f"{path}（长度 {len(recorded)} vs {len(replayed)}）"
        for index, (left, right) in enumerate(zip(recorded, replayed)):
            found = _first_difference(left, right, f"{path}[{index}]")
            if found:
                return found
        return ""
    return "" if recorded == replayed else path or "值不同"


def verify_log(log_path: str | Path, task: dict, *,
               port: int = 0) -> dict:
    """重放日志并比对。返回 `{total, matched, mismatches, unknown_tools}`。"""
    calls, _artifacts = read_call_log(log_path)
    declared = {tool.get("name") for tool in (task.get("tools") or []) if isinstance(tool, dict)}

    server, _service = serve(task, port=port, quiet=True)
    host, real_port = server.server_address[:2]
    base = f"http://{host}:{real_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    mismatches: list[dict] = []
    unknown: list[str] = []
    try:
        for record in calls:
            name = str(record.get("name"))
            index = record.get("call_index")
            if name not in declared:
                unknown.append(f"第 {index} 次：{name}（任务没声明这个工具）")
                continue
            status, body = _post(base, name, record.get("params") or {})
            if status != record.get("http_status"):
                mismatches.append({
                    "call_index": index, "name": name, "field": "http_status",
                    "recorded": record.get("http_status"), "replayed": status,
                })
                continue
            where = _first_difference(record.get("body") or {}, body, "body")
            if where:
                mismatches.append({
                    "call_index": index, "name": name, "field": where,
                    "recorded": record.get("body"), "replayed": body,
                })
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return {
        "total": len(calls),
        "matched": len(calls) - len(mismatches) - len(unknown),
        "mismatches": mismatches,
        "unknown_tools": unknown,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重放一份 calls.jsonl，验证它没被改过（B-49）")
    parser.add_argument("--log", required=True, help="calls.jsonl 路径")
    parser.add_argument("--task", required=True, help="任务 YAML 或 task_id")
    parser.add_argument("--catalog", default=str(TASK_PATH), help="任务目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON（给人看是一段话）")
    args = parser.parse_args(argv)

    try:
        task = load_task(resolve_task_file(args.task, args.catalog))
        result = verify_log(args.log, task)
    except (LabError, ReportError) as exc:
        print(str(exc), flush=True)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"重放 {result['total']} 次调用：{result['matched']} 次逐字节一致")
        for item in result["mismatches"]:
            print(f"  第 {item['call_index']} 次 {item['name']} 不一致：{item['field']}"
                  f"  记录={item['recorded']!r} 重放={item['replayed']!r}")
        for line in result["unknown_tools"]:
            print(f"  {line}")

    ok = not result["mismatches"] and not result["unknown_tools"]
    if not ok:
        print("\n结论：这份日志**对不上**真跑的返回。要么被改过，要么不是这套服务产生的。",
              flush=True)
        return 3
    print("\n结论：全部一致——这份日志来自真跑，没有被改过。", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover - 手工入口
    raise SystemExit(main())
