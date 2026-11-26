"""打一个「解压就能跑」的试用包（B-47）。

## 为什么要单独打包，而不是让他们 `git clone`

`git clone` 对非技术同学不是一步——要装 git、要知道 clone 到哪、要 cd 对目录。
试用是 3–5 个人一次性的事，任何一步都可能变成"算了不试了"。所以打一个 zip：
解压 → 双击 `start.bat`（Windows）或 `sh start.sh`（macOS/Linux）。

## 包里放什么、不放什么

放：`src/arena`（跑题与转提交需要的模块）、`scripts/lab.py`、**一道**任务的 YAML、
两个启动脚本、一页说明。
不放：测试、安卓、文档、`.git`、以及**任何能推出答案的字段**。

## 为什么要裁掉任务里的判分字段

任务 YAML 里有 `rubric_gate` / `rubric_credit` / `scoring` / `test_inputs.expected`——
那是**答案与权重**。整份发出去，好奇的同学翻一眼就知道该写什么值、第几次调用会失败，
这一轮试用收到的就不是"他做题的过程"了。

裁的边界写死：只删这五处，**其余一个字不动**（尤其 `tools` 与 `fault_injection`，
它们是 mock 服务按声明回放数据的依据）。裁完用 `--verify` 实测：
同一串调用在「原任务」与「裁过的任务」上返回体逐字节相同——否则就是裁过头了。

## 用法

    python scripts/make_trial_pack.py                 # 产出 dist/arena-trial-v1.zip
    python scripts/make_trial_pack.py --verify        # 打完再解压实跑一遍（推荐）
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TRIAL_TASK = "dbg-001-order-report"
PACK_DIR = "arena-trial"
PACK_VERSION = "v1"
DEFAULT_OUT = ROOT / "dist" / f"arena-trial-{PACK_VERSION}.zip"

# 只裁这五处。加字段前先想清楚：它能不能推出答案？
DROP_TOP = ("rubric_gate", "rubric_credit", "scoring", "derivation", "trace_requirements")
DROP_TEST_INPUTS = ("expected",)

# README 从这里截断：维护者侧的内容不该发到同学手上
README_CUT = "\n---\n\n## 维护者侧"

FIXED_TIME = (2026, 1, 1, 0, 0, 0)  # 固定时间戳 ⇒ 同样的内容打出同样的 sha


class PackError(RuntimeError):
    """打包条件不满足：消息要能直接给人看。"""


def _readme() -> str:
    doc = (ROOT / "docs" / "试用包-跑一道题-v1.md").read_text(encoding="utf-8")
    if README_CUT not in doc:
        raise PackError(
            "docs/试用包-跑一道题-v1.md 里找不到维护者侧的分隔线——"
            "README 会把维护者步骤一起发出去，先补回 `---` + `## 维护者侧`"
        )
    body = doc.split(README_CUT)[0].rstrip()
    # 原文档开头的标题与"不联网"那段，和下面的头部重复；从第一人称的小标题接上
    marker = "## 你要做的一件事"
    if marker not in body:
        raise PackError(f"docs/试用包-跑一道题-v1.md 里找不到 `{marker}`，README 拼不起来")
    body = marker + body.split(marker, 1)[1]

    header = f"""# 试用包 {PACK_VERSION}（先看这一页）

这个文件夹是一道小题的练习环境，**全程在你自己的电脑上，不联网、不用装 App、不用注册**。
一次大约 10–15 分钟。

## 最简开始

- **Windows**：双击 `start.bat`
- **macOS / Linux**：在这个文件夹里执行 `sh start.sh`

第一次会自动装两个依赖（要联网，几十秒）。起来之后屏幕上会打印题面和一段地址，
用你自己的 Agent（Claude Code / Cursor / 自己写的脚本）对着那个地址做题；
做完回到窗口按 `Ctrl+C`，把生成的 `calls.jsonl` 发回给我。

> 下面这几条命令是同一件事的手动版——双击没跑起来时照这个排查。

"""
    return header + body + "\n"


def sanitize_task(source: Path) -> str:
    """把任务 YAML 裁成"能跑但推不出答案"的版本。只删 `DROP_*` 里列的字段。"""
    import yaml

    task = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(task, dict):
        raise PackError(f"{source.name} 顶层不是对象")
    for key in DROP_TOP:
        task.pop(key, None)
    test_inputs = task.get("test_inputs")
    if isinstance(test_inputs, dict):
        for key in DROP_TEST_INPUTS:
            test_inputs.pop(key, None)
    # 裁完必须还能被 mock 服务认：这两块是它唯一的输入
    for required in ("tools", "test_inputs", "fault_injection"):
        if required not in task:
            raise PackError(f"{source.name} 裁完缺了 {required}——mock 服务就没依据了")
    return yaml.safe_dump(task, allow_unicode=True, sort_keys=False)


def _requirements() -> str:
    """依赖照抄仓库的 pin，不另写一份版本号。"""
    pins = {}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, _, version = line.partition("==")
        pins[name.strip().lower()] = version.strip()
    need = ["pyyaml", "jsonschema"]
    missing = [n for n in need if n not in pins]
    if missing:
        raise PackError(f"requirements.txt 里找不到 {missing} 的 pin")
    return "".join(f"{n}=={pins[n]}\n" for n in need)


def _files() -> list[tuple[str, bytes]]:
    """包内文件清单（相对路径 → 字节）。任何源文件缺失都直接报错，不静默少一个。"""
    entries: list[tuple[str, bytes]] = []

    def add(rel: str, data: bytes) -> None:
        entries.append((rel, data))

    add("README.md", _readme().encode("utf-8"))
    for name in ("start.bat", "start.sh"):
        src = ROOT / "pack" / "trial" / name
        if not src.is_file():
            raise PackError(f"缺启动脚本：pack/trial/{name}")
        add(name, src.read_bytes())
    add("scripts/lab.py", (ROOT / "scripts" / "lab.py").read_bytes())
    add("requirements.txt", _requirements().encode("utf-8"))

    task_src = ROOT / "tasks" / f"{TRIAL_TASK}.yaml"
    if not task_src.is_file():
        raise PackError(f"找不到任务文件：tasks/{TRIAL_TASK}.yaml")
    add(f"tasks/{TRIAL_TASK}.yaml", sanitize_task(task_src).encode("utf-8"))

    arena = ROOT / "src" / "arena"
    modules = sorted(p for p in arena.glob("*.py"))
    if not modules:
        raise PackError("src/arena 下一个 .py 都没有")
    for path in modules:
        add(f"src/arena/{path.name}", path.read_bytes())
    return entries


def _forbidden_hits(entries: list[tuple[str, bytes]]) -> list[str]:
    """包里不许出现本机绝对路径、密钥。

    用形状而不是关键词匹配：`sk-` 会误命中 `task-`，`API_KEY` 是代码里合法的环境变量名。
    真正要拦的是「长得像一把钥匙」和「某台机器的路径」。
    """
    import re

    patterns = [
        (re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\\\s\"']+"), "本机绝对路径（Windows）"),
        (re.compile(r"/Users/[A-Za-z0-9._-]+/"), "本机绝对路径（macOS）"),
        (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "疑似 API key"),
        (re.compile(r"(API_KEY|SECRET|TOKEN)\s*[=:]\s*[\"'][^\"'\s]{8,}[\"']"), "写死的凭据"),
        (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "疑似 GitHub token"),
    ]
    hits: list[str] = []
    for rel, data in entries:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for pattern, label in patterns:
            match = pattern.search(text)
            if match:
                hits.append(f"{rel} 出现{label}：{match.group(0)[:40]!r}")
    return hits


def build(out: Path = DEFAULT_OUT) -> Path:
    entries = _files()
    hits = _forbidden_hits(entries)
    if hits:
        raise PackError("包里有不该发出去的东西：\n  " + "\n  ".join(hits))

    out.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in sorted(entries):
            info = zipfile.ZipInfo(f"{PACK_DIR}/{rel}", date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    out.write_bytes(buffer.getvalue())
    return out


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_ready(base: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/tools", timeout=1) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    raise PackError(f"{base} 30 秒没起来——解压后的包跑不动")


def _post(base: str, tool: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{base}/tools/{tool}", json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def verify(zip_path: Path) -> str:
    """解压到临时目录，用**包里的**东西跑一遍：起服务 → 调两次 → 转提交 → 出报告。

    最后一步的判分用本仓的任务（未裁剪），因为报告本来就由我们这边生成——
    包里那份只负责回放数据，两者若能出同一份报告，说明裁剪没改行为。
    """
    from arena import store, submission_api as api

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        root = Path(tmp) / PACK_DIR
        # 1) 目录结构对：解压出来就能 --list
        listed = subprocess.run(
            [sys.executable, str(root / "scripts" / "lab.py"), "--list"],
            capture_output=True, text=True, timeout=120, cwd=str(root),
        )
        if listed.returncode != 0:
            raise PackError(f"解压后 --list 失败：{listed.stderr[-500:]}")
        if TRIAL_TASK not in listed.stdout:
            raise PackError(f"--list 里没有 {TRIAL_TASK}：{listed.stdout!r}")

        # 2) 真起服务、真调两次（含一次失败调用），再转提交
        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        proc = subprocess.Popen(
            [sys.executable, str(root / "scripts" / "lab.py"), "--task", TRIAL_TASK,
             "--port", str(port), "--log", "calls.jsonl", "--quiet"],
            cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        try:
            _wait_ready(base)
            _post(base, "fetch_orders", {"offset": 0, "limit": 5})
            _post(base, "fetch_orders", {"offset": 0, "limit": 99})
            _post(base, "write_report", {"path": "/workspace/report.json", "content": "{}"})
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - 兜底
                proc.kill()

        out = root / "submission.json"
        converted = subprocess.run(
            [sys.executable, str(root / "scripts" / "lab.py"), "--from-log", "calls.jsonl",
             "--task", TRIAL_TASK, "--user", "u-verify", "--out", str(out)],
            capture_output=True, text=True, timeout=120, cwd=str(root),
        )
        if converted.returncode != 0:
            raise PackError(f"解压后 --from-log 失败：{converted.stdout[-500:]}{converted.stderr[-300:]}")
        payload = json.loads(out.read_text(encoding="utf-8"))
        if len(payload["trace"]["tool_calls"]) != 3:
            raise PackError(f"日志转了 {len(payload['trace']['tool_calls'])} 次调用，应为 3")
        if not payload["artifacts"]:
            raise PackError("产物没跟出来")

        # 3) 拿我们这边（未裁剪）的任务判一次，确认报告出得来
        db = Path(tmp) / "verify.db"
        registry = api.TaskRegistry(ROOT / "tasks")

        def conn_factory():
            return store.connect(db)

        api.bootstrap(db, registry)
        report = api.submit(payload, registry=registry, conn_factory=conn_factory)
        if not report["trace_audit"]["schema_valid"]:
            raise PackError(f"报告说过程不合规：{report['trace_audit']['invalid_reasons']}")
        if not report["assertions"]:
            raise PackError("报告里没有断言")

    return "OK：解压后起服务 → 3 次调用 → 转提交 → 出报告"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打试用 zip（B-47）")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--verify", action="store_true", help="打完解压实跑一遍")
    args = parser.parse_args(argv)

    try:
        path = build(Path(args.out))
    except PackError as exc:
        print(f"[失败] {exc}", flush=True)
        return 2

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    print(f"已写出 {path}")
    print(f"  文件数 {len(names)}／大小 {path.stat().st_size / 1024:.0f} KB")
    print(f"  sha256 {digest[:16]}")

    if args.verify:
        try:
            print(verify(path), flush=True)
        except PackError as exc:
            print(f"[验证失败] {exc}", flush=True)
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
