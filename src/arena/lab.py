"""学生侧「本地跑起来」入口（B-32 第 0 层，2026-11-22）。

## 为什么需要它

扩到 20 题时发现的缺口：题面只说「使用工具 `fetch_orders`」，全仓**没有一句**告诉学习者
这个工具从哪来、怎么调。于是学习者只能改一行代码就提交一次，等判分告回来。
这一层只解决**最前面那一步**：本地起 mock 服务，拿到能直接对着调的端点。

## 只做 0 层，不做 1/2 层

| 层 | 内容 | 为什么不在本模块 |
|---|---|---|
| 0 | 起服务 + 打印端点 | **本模块** |
| 1 | 提交后看自己的调用序列 | 涉及「给不给返回体」的口径，待人拍（BACKLOG B-32） |
| 2 | 分层提示按钮 | 涉及「看提示要不要降权」，待人拍（BACKLOG B-32） |

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

from .diagnosis_report import ReportError, load_task
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

def describe(task: dict, base_url: str) -> str:
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
# 服务
# ---------------------------------------------------------------------------

def serve(task: dict, *, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
          quiet: bool = True):
    """起服务，返回 (server, service)。端口被占用转成 LabError。"""
    try:
        return make_server(task, host=host, port=port, quiet=quiet)
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
    parser.add_argument("--no-probe", action="store_true", help="跳过启动自检")
    parser.add_argument("--quiet", action="store_true", help="不打印每次 HTTP 请求")
    args = parser.parse_args(argv)

    catalog = Path(args.catalog)
    try:
        if args.list:
            for task_id, title, name in list_tasks(catalog):
                print(f"{task_id:34s} {title}    ({name})")
            return 0
        if not args.task:
            parser.error("要 --task <任务> 或 --list 之一")

        path = resolve_task_file(args.task, catalog)
        task = load_task(path)
        server, _service = serve(task, host=args.host, port=args.port, quiet=args.quiet)
    except (LabError, ReportError) as exc:
        print(str(exc), flush=True)
        return 2

    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    print(describe(task, base_url), flush=True)

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
    return 0


if __name__ == "__main__":  # pragma: no cover - 手工入口
    raise SystemExit(main())
