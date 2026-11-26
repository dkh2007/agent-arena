"""学生侧入口（B-32 第 0 层 + 第 1 层的「看回放」，2026-11-22；B-46 加收日志）。

## 为什么需要它

扩到 20 题时发现的缺口：题面只说「使用工具 `fetch_orders`」，全仓**没有一句**告诉学习者
这个工具从哪来、怎么调。于是学习者只能改一行代码就提交一次，等判分告回来。
这一层只解决**最前面那一步**：本地起 mock 服务，拿到能直接对着调的端点。
第二件事是提交之后**看见自己发了什么**：`--show-trace` 把序列排成表。

## 四层缺口的分工

| 层 | 内容 | 状态 |
|---|---|---|
| 0 | 起服务 + 打印端点 | **本模块（`--task`）** |
| 1 | 看自己的调用序列（`offset` / `limit` / 状态码，**不含返回体**） | **本模块（`--show-trace`）** |
| 2 | 分层提示按钮 | 待人拍（涉及「看提示要不要降权」，BACKLOG B-32） |
| — | 跑完怎么交回来 | **本模块（`--log` 记、`--from-log` 转提交，B-46）** |

## `--log` 为什么记在服务端，而不是解析他们的 Agent 会话

试用要收的是「过程」。两条路可选：解析 Claude Code / Cursor 的会话文件，或者由**我们起的
工具服务**自己记账。选后者，理由三条：

1. **方言少一半**：会话格式随 Agent 版本变；HTTP 请求不变。
2. **不可伪造**：服务端按到达顺序自己编号，与 `platform_capture` 同一条纪律（先记账再返回）。
3. **和判分口径对齐**：断言读的就是 trace，而 trace 要的正是「调了哪个工具、参数、状态码」。

代价写清：学习者**必须对着本服务调工具**。绕过它去调别的端点，日志里就没有那次调用——
这本来也就是 `trace_requirements` 的语义（只认声明的工具）。

第 1 层的边界写在 `diagnosis_report.trace_view_items()` 的注释里：不含返回体。

## 一条硬约束：**不泄露判分信息**

本模块打印的一切都必须能在题面里找到。故障位、故障码、期望值、断言 id 一律不出现——
给了这些，学习者就不用"发现服务会失败"了，B-31 那类题会直接失效。
`tests/test_lab.py::test_banner_does_not_leak_judging_internals` 就是盯这条的。

同理，自检只打 `GET /tools`（工具清单），**不**去调 `fetch_orders`：调了会消耗故障额度
（`per_submission` 语义下，额度是按提交重置的，本地预热一次就可能让学习者看到的第一次
请求和别人不一样），也等于替他把第一页数据取出来了。
"""

from __future__ import annotations

import argparse
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from .diagnosis_report import (
    ReportError,
    format_trace_view,
    load_task,
    trace_view_items,
)
from .mock_tools import MockConfigError, make_server

DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / "tasks"
DEFAULT_PORT = 8765


class LabError(RuntimeError):
    """入口配置错误（任务找不到、端口被占用等），消息要能直接给人看。"""


# ---------------------------------------------------------------------------
# 任务定位
# ---------------------------------------------------------------------------

def resolve_task_file(spec: str, catalog: Path = DEFAULT_CATALOG) -> Path:
    """`--task` 既接受路径，也接受 task_id。

    task_id 这一支是为了「照题面写：task_id=dbg-003-purchase-report」也能跑起来，
    不用先知道文件名。
    """
    direct = Path(spec)
    if direct.suffix in (".yaml", ".yml") and direct.is_file():
        return direct.resolve()

    catalog = Path(catalog)
    if not catalog.is_dir():
        raise LabError(f"任务目录不存在：{catalog}")
    for path in sorted(catalog.glob("*.yaml")):
        if path.stem == spec or path.name == spec:
            return path.resolve()
    # 变体的 task_id 与文件名不总相同（母本/变体命名习惯不同），再按内容认一次。
    for path in sorted(catalog.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if f"task_id: {spec}\n" in text or text.rstrip().endswith(f"task_id: {spec}"):
            return path.resolve()
    raise LabError(f"找不到任务 {spec!r}（在 {catalog} 下按文件名与 task_id 都没命中）")


def list_tasks(catalog: Path = DEFAULT_CATALOG) -> list[tuple[str, str, str]]:
    """返回 [(task_id, title, 文件名)]，按 task_id 排序。读不动单个文件不吞异常。"""
    catalog = Path(catalog)
    if not catalog.is_dir():
        raise LabError(f"任务目录不存在：{catalog}")
    rows: list[tuple[str, str, str]] = []
    for path in sorted(catalog.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        task_id = _first_scalar(text, "task_id") or path.stem
        title = _first_scalar(text, "title") or ""
        rows.append((task_id, title, path.name))
    rows.sort()
    return rows


def _first_scalar(text: str, key: str) -> str | None:
    """从 YAML 文本里取一行顶层标量。只为列表展示，不引入 yaml 解析失败面。"""
    prefix = f"{key}:"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip().strip("'\"")
    return None


# ---------------------------------------------------------------------------
# 横幅（纯函数，好测）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 看自己的调用序列（B-32 第 1 层）
# ---------------------------------------------------------------------------

def load_trace_view(path: str | Path) -> tuple[list[dict], str]:
    """从「报告 JSON」或「原始提交 JSON」里取逐次调用序列，返回 (items, 抬头说明)。

    两种输入都收：报告里有 `trace_view`（平台算好的），原始提交里有 `trace`
    （自己手上那份）——学习者手里是哪种就用哪种，别为格式再教一遍。
    """
    p = Path(path)
    if not p.is_file():
        raise LabError(f"找不到文件：{p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LabError(f"{p} 不是合法 JSON（{exc}）") from exc
    if not isinstance(data, dict):
        raise LabError(f"{p} 顶层应是对象，实际是 {type(data).__name__}")

    task_id = ""
    submission = data.get("submission")
    if isinstance(submission, dict) and submission.get("task_id"):
        task_id = str(submission["task_id"])
    title = f"任务 {task_id}" if task_id else p.name

    if isinstance(data.get("trace_view"), list):
        return list(data["trace_view"]), title
    if "trace" in data:
        items = trace_view_items(data.get("trace"))
        if not items:
            raise LabError(
                f"{p} 里 trace 没有可读的调用记录（可能没上报，或结构不合法）"
            )
        return items, title
    raise LabError(
        f"{p} 里既没有 trace_view（报告）也没有 trace（提交）——"
        "先把它 POST 给平台拿到报告，或直接给原始提交文件"
    )


def describe(task: dict, base_url: str, log_path: str | Path | None = None) -> str:
    """生成开跑前给人看的那段字。只含题面已有的信息。"""
    lines: list[str] = []
    task_id = task.get("task_id", "?")
    lines.append("=" * 72)
    lines.append(f"任务 {task_id} — {task.get('title', '')}")
    lines.append("=" * 72)
    lines.append("")
    lines.append(f"服务地址  {base_url}")
    lines.append(f"工具清单  GET  {base_url}/tools")
    lines.append(f"调用工具  POST {base_url}/tools/<工具名>    body 为 JSON 参数")
    lines.append("")
    lines.append("可用工具：")
    for tool in task.get("tools") or []:
        params = tool.get("parameters") or []
        sig = ", ".join(
            f"{p['name']}: {p.get('type', '?')}"
            + ("  必填" if p.get("required") else "  可选")
            + (f"  ∈ [{p['minimum']}, {p['maximum']}]"
               if "minimum" in p and "maximum" in p else "")
            for p in params
        )
        lines.append(f"  - {tool['name']}({sig})")
        if tool.get("description"):
            lines.append(f"      {tool['description']}")
    lines.append("")
    lines.append("一条可以直接跑的示例（只用标准库，拷走就能改）：")
    first = (task.get("tools") or [{}])[0].get("name", "<工具名>")
    sample = _sample_params(task, first)
    lines.append("  python - <<'EOF'")
    lines.append("  import json, urllib.request")
    lines.append(f"  url = {base_url + '/tools/' + first!r}")
    lines.append(f"  body = json.dumps({sample!r}).encode()")
    lines.append("  req = urllib.request.Request(url, body, {'Content-Type': 'application/json'})")
    lines.append("  with urllib.request.urlopen(req) as r:")
    lines.append("      print(r.status, json.loads(r.read()))")
    lines.append("  EOF")
    lines.append("")
    lines.append("注意：**这个服务只按声明回放数据，不判对错**。是否合规、该不该重试、")
    lines.append("产物对不对，都在提交后由判分侧判——本地调通不等于能拿分。")
    lines.append("")
    scenario = (task.get("scenario") or "").strip()
    if scenario:
        lines.append("-" * 72)
        lines.append("题面：")
        lines.append(scenario)
        lines.append("-" * 72)
    lines.append("")
    if log_path is not None:
        lines.append(f"本次每一次工具调用都会记进  {Path(log_path).resolve()}")
        lines.append("做完 Ctrl+C 停服务，把这个文件发回即可——不用你手写过程。")
        lines.append("")
    lines.append("Ctrl+C 停止服务。")
    return "\n".join(lines)


def _sample_params(task: dict, tool_name: str) -> dict:
    """给示例用的最小合法参数：只填必填项，取值取声明里的下限（没有就填空串）。"""
    for tool in task.get("tools") or []:
        if tool.get("name") != tool_name:
            continue
        out: dict = {}
        for p in tool.get("parameters") or []:
            if not p.get("required"):
                continue
            name = p["name"]
            if p.get("type") == "integer":
                out[name] = int(p.get("minimum", 0))
            elif p.get("type") == "number":
                out[name] = float(p.get("minimum", 0))
            elif p.get("type") == "boolean":
                out[name] = False
            else:
                out[name] = ""
        return out
    return {}


# ---------------------------------------------------------------------------
# 跑完怎么交回来（B-46）：服务端记日志 → 一行命令转成提交
# ---------------------------------------------------------------------------

def _log_path(path: str | Path) -> Path:
    p = Path(path)
    if p.parent and not p.parent.is_dir():
        raise LabError(f"日志目录不存在：{p.parent}")
    return p


def artifact_record(name: str, status: int, params: dict) -> tuple[str, str] | None:
    """从一次调用里取产物：`write_report` 的正文在**入参**里，返回体只有路径和字节数。

    放进这里是因为它被两处用到：`CallLog` 写 JSONL（B-46）、`hosting` 写库（B-51）。
    两处各写一份的话，改工具名时必然只改一处——那会导致"本地跑有产物、托管跑没有"，
    而症状是报告里"结论"一致但"产物"缺失，最难对。

    返回 `(路径, 正文)`；这条调用不算产物就返回 `None`（不抛异常：产物缺失由判分说话）。
    """
    if name != "write_report" or not (200 <= status < 300):
        return None
    path = params.get("path")
    if not path:
        return None
    return str(path), str(params.get("content") or "")


class CallLog:
    """服务端逐次记录工具调用，追加写 JSONL。**先落盘、再返回**（同 `platform_capture`）。

    记录形状刻意贴着提交契约：`call_index` / `name` / `params` / `http_status` / `body`。
    转换时直接喂给 `agent_trace.trace_item()`——那是 trace 字段的唯一实现，这里不重写一份。
    """

    def __init__(self, path: str | Path):
        self.path = _log_path(path)
        self._handle = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self._seq = self._count_existing_calls()

    def _count_existing_calls(self) -> int:
        """断点续记：同一天跑第二遍不重置编号。

        否则第二次起服务又从 1 数，日志里出现重复的 `call_index`，交上去会被判
        `not_sequential` ——学习者只是重跑了一次，报告却说过程不合规。
        """
        if not self.path.is_file():
            return 0
        count = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # 坏行由 read_call_log 报，这里只数得出来的
            if isinstance(record, dict) and "name" in record:
                count += 1
        return count

    def __call__(self, name: str, params: dict, status: int, body: dict) -> None:
        with self._lock:
            self._seq += 1
            self._write({"call_index": self._seq, "name": name, "params": params,
                         "http_status": status, "body": body})
            # write_report 的响应体里只有 path/bytes，正文在入参里；不记就等于把产物丢了
            record = artifact_record(name, status, params)
            if record is not None:
                self._write({"artifact_path": record[0], "content": record[1]})

    def _write(self, record: dict) -> None:
        self._handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


def read_call_log(path: str | Path) -> tuple[list[dict], dict[str, str]]:
    """读日志，返回 (调用记录, 产物)。坏行直接报第几行——手工改过日志要看得出来。"""
    p = Path(path)
    if not p.is_file():
        raise LabError(f"找不到日志：{p}")
    calls: list[dict] = []
    artifacts: dict[str, str] = {}
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LabError(f"{p} 第 {lineno} 行不是合法 JSON（{exc}）") from exc
        if not isinstance(record, dict):
            raise LabError(f"{p} 第 {lineno} 行应为对象，实际 {type(record).__name__}")
        if "artifact_path" in record:
            artifacts[str(record["artifact_path"])] = record.get("content") or ""
        elif "name" in record:
            calls.append(record)
        else:
            raise LabError(f"{p} 第 {lineno} 行既不是调用也不是产物：{line[:60]}")
    if not calls:
        raise LabError(f"{p} 里没有一次工具调用——日志是空的，或服务没带 --log 起来")
    return calls, artifacts


def build_submission(calls: list[dict], artifacts: dict[str, str], task: dict, *,
                     user_id: str, agent_id: str = "trial-agent",
                     final_status: str = "completed") -> dict:
    """日志 → 可直接 POST 给 `/submissions` 的提交体。

    `call_index` 用服务端自己数的号（不是抄 Agent 自报），所以天然连续；
    `trace_audit` / `assertion_results` **不给**——它们是平台派生物（F1 决策），
    谁给谁就在自报结论。这里只交 `submission` + `trace` + `artifacts`。
    """
    from .agent_trace import trace_item  # 契约只有一处实现，复用而不是重写

    tool_calls: list[dict] = []
    for record in calls:
        tool_calls.append(trace_item(
            int(record["call_index"]),
            str(record["name"]),
            record.get("params") or {},
            int(record.get("http_status") or 0),
            record.get("body") or {},
        ))
    return {
        "task_id": str(task.get("task_id", "")),
        "task_version": str(task.get("task_version", "")),
        "user_id": user_id,
        "agent_id": agent_id,
        "trace": {"final_status": final_status, "tool_calls": tool_calls},
        "artifacts": dict(artifacts),
    }


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------

def serve(task: dict, *, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
          quiet: bool = True, on_call=None):
    """起服务，返回 (server, service)。端口被占用转成 LabError。"""
    try:
        return make_server(task, host=host, port=port, quiet=quiet, on_call=on_call)
    except OSError as exc:
        raise LabError(
            f"端口 {port} 起不来（{exc}）。换一个：--port 8766"
        ) from exc
    except MockConfigError as exc:
        raise LabError(f"任务声明的工具有问题：{exc}") from exc


def probe(base_url: str) -> list[str]:
    """只打 `GET /tools`——证明服务活着，且不碰数据、不消耗故障额度。"""
    try:
        with urllib.request.urlopen(f"{base_url}/tools", timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise LabError(f"服务起来了但自检失败：{exc}") from exc
    tools = body.get("tools")
    if not isinstance(tools, list):
        raise LabError(f"自检返回格式不对：{body!r}")
    return tools


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m arena.lab",
        description="本地起 mock 工具服务，拿到能直接对着调的端点（B-32 第 0 层）",
    )
    parser.add_argument("--task", help="任务 YAML 路径，或 task_id（如 dbg-001-order-report）")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG), help="任务目录")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认只听本机）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"端口（默认 {DEFAULT_PORT}；填 0 让系统分配）")
    parser.add_argument("--list", action="store_true", help="只列任务，不起服务")
    parser.add_argument("--show-trace", metavar="文件",
                        help="打印一次提交（或报告）的逐次调用序列，不起服务（B-32 第 1 层）")
    parser.add_argument("--no-probe", action="store_true", help="跳过启动自检")
    parser.add_argument("--quiet", action="store_true", help="不打印每次 HTTP 请求")
    parser.add_argument("--log", metavar="文件",
                        help="把每次工具调用追加记进这个 JSONL（试用交付用，B-46）")
    parser.add_argument("--from-log", metavar="文件",
                        help="把 --log 记下的日志转成提交 JSON，不起服务")
    parser.add_argument("--user", default="u-trial", help="--from-log 的 user_id")
    parser.add_argument("--agent", default="trial-agent", help="--from-log 的 agent_id")
    parser.add_argument("--final-status", default="completed",
                        help="--from-log 的 trace.final_status（默认 completed）")
    parser.add_argument("--out", help="--from-log 的输出文件；不给就打到标准输出")
    args = parser.parse_args(argv)

    catalog = Path(args.catalog)
    if args.show_trace:
        try:
            items, title = load_trace_view(args.show_trace)
        except LabError as exc:
            print(str(exc), flush=True)
            return 2
        print(f"调用序列（{title}）")
        print(format_trace_view(items))
        print()
        print("只列请求参数与状态码，**不含返回体**——返回里有几行、合计多少，")
        print("正是要你自己发现的东西。改了参数再提交一次，把两份序列对着看。")
        return 0
    try:
        if args.list:
            for task_id, title, name in list_tasks(catalog):
                print(f"{task_id:34s} {title}    ({name})")
            return 0
        if args.from_log:
            if not args.task:
                parser.error("--from-log 也要给 --task（提交里必须带 task_id 与 task_version）")
            task = load_task(resolve_task_file(args.task, catalog))
            calls, artifacts = read_call_log(args.from_log)
            submission = build_submission(
                calls, artifacts, task,
                user_id=args.user, agent_id=args.agent,
                final_status=args.final_status,
            )
            text = json.dumps(submission, ensure_ascii=False, indent=2)
            if args.out:
                Path(args.out).write_text(text + "\n", encoding="utf-8")
                print(f"已写出 {args.out}：{len(submission['trace']['tool_calls'])} 次调用 / "
                      f"{len(submission['artifacts'])} 份产物")
            else:
                print(text)
            return 0
        if not args.task:
            parser.error("要 --task <任务> 或 --list 之一")

        path = resolve_task_file(args.task, catalog)
        task = load_task(path)
        call_log = CallLog(args.log) if args.log else None
        server, _service = serve(task, host=args.host, port=args.port,
                                 quiet=args.quiet, on_call=call_log)
    except (LabError, ReportError) as exc:
        if "call_log" in locals() and call_log is not None:
            call_log.close()
        print(str(exc), flush=True)
        return 2

    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    print(describe(task, base_url, args.log), flush=True)

    # 先起 accept 循环，再自检：make_server 只是把 socket 绑上，不调 serve_forever
    # 就没有人在 accept，自检会一直等到超时（2026-11-22 端到端实测踩到过）。
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    if not args.no_probe:
        try:
            tools = probe(base_url)
        except LabError as exc:
            server.shutdown()
            server.server_close()
            print(str(exc), flush=True)
            return 2
        print(f"[自检] GET /tools 通，{len(tools)} 个工具：{', '.join(tools)}", flush=True)

    try:
        thread.join()
    except KeyboardInterrupt:
        print("\n[停止] 服务已关闭", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        if call_log is not None:
            call_log.close()
            print(f"[日志] 已记在 {call_log.path}——把这个文件发回即可", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover - 手工入口
    raise SystemExit(main())
