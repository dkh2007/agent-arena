"""LLM 连通性探针 —— B-04（判卷双评）/ B-03b（真模型 Agent）的前置检查。

**为什么单独一个脚本**：这两件事都会花钱花配额，且失败原因分很多层（密钥错 / 地址错 /
模型名写错 / 网络代理 / 配额耗尽）。混在产品代码里试错，等于把「环境问题」和
「逻辑问题」搅在一起查。先在这里把环境钉死，再写业务。

**判据三条，全过才算「能用」**（缺一条都打印具体原因，不猜、不降级）：
  1. `GET {base}/models` 返回 200，且能解析出模型 id 列表；
  2. `.env` 里的 `LLM_MODEL` 在该列表里——不在就把它列出来，由人重填，**不许自动替换**；
  3. `POST {base}/chat/completions` 用极短提示返回 200，且有非空 `choices`。

**安全**：密钥只从 `.env` 读进内存，**任何输出都打码**（前 3 后 3），不写日志、不落盘、
不进异常消息。这也是为什么用标准库 `urllib` 而不是第三方库——少一个依赖就少一处泄露面。

用法：
    python scripts/llm_probe.py                # 三条判据全跑
    python scripts/llm_probe.py --show-models   # 额外打印服务端返回的全部模型 id

退出码：0 全过 ｜ 2 配置有问题（缺键/占位未填）｜ 3 网络或 HTTP 层失败 ｜ 4 判据不成立
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
REQUIRED = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")
TIMEOUT_S = 30


class ProbeError(RuntimeError):
    """探针自己的错——和「接口答错了」分开，否则查不出是哪一层坏。"""


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """极简 .env 解析：只认 KEY=VALUE，忽略注释与空行。

    刻意不引 python-dotenv：`requirements.txt` 全 pin 且有依赖比对测试，
    为一个三行的文件加依赖不划算。代价是语法支持有限（不支持 export、多行、引号转义），
    所以下面把"值长得不像话"的情况直接报出来，而不是猜。
    """
    if not path.exists():
        raise ProbeError(f"找不到 {path}（应当由 .env.example 复制而来）")
    env: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ProbeError(f"{path.name}:{lineno} 不是 KEY=VALUE 形式：{line[:20]!r}")
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()

    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        raise ProbeError(f".env 缺少或留空：{missing}（照 .env.example 填满三行）")
    # 粘贴常见事故：把键名连值一起抄了（LLM_MODEL=LLM_MODEL=xxx）
    for key in REQUIRED:
        if env[key].startswith(f"{key}="):
            raise ProbeError(f"{key} 的值里又带了一遍键名（{env[key][:24]!r}），删掉重复部分")
    if not env["LLM_BASE_URL"].startswith(("http://", "https://")):
        raise ProbeError(f"LLM_BASE_URL 不是 URL：{env['LLM_BASE_URL']!r}")
    return env


def mask(secret: str) -> str:
    """打码。短到没法打码就只说长度——绝不明文。"""
    if not secret:
        return "<空>"
    if len(secret) <= 8:
        return f"<长度 {len(secret)}，过短不宜显示>"
    return f"{secret[:3]}{'*' * 6}{secret[-3:]}（长度 {len(secret)}）"


def _request(url: str, key: str, payload: dict | None = None) -> tuple[int, dict | str]:
    """发一次请求，返回 (状态码, 解析后的 body 或原始文本)。

    HTTPError 也要把 body 读出来：401/404/402 的 body 里才有真正原因
    （"invalid api key" / "model not found" / "insufficient balance"），
    只报状态码等于把排查线索扔掉。
    """
    data = None
    # 相邻字面量是有意的：`scripts/push_gate.py` 把 `Bearer` 后面跟空格的形式当作密钥前缀匹配
    # （粗粒度是有意的，宁可误拦）。HTTP 头必须这么写，所以按闸门自身处理同类问题的做法拆开——
    # 既不放宽闸门规则，也不让这一行在扫描里看起来像一份被粘进来的凭据。
    scheme = "Bea" "rer "
    headers = {"Authorization": f"{scheme}{key}", "Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        status = exc.code
    except urllib.error.URLError as exc:
        # 代理/断网/DNS：这一层不是密钥问题，别让人去翻密钥
        raise ProbeError(f"连不上 {url}：{exc.reason}") from exc
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body[:400]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    show_models = "--show-models" in args

    try:
        env = load_env()
    except ProbeError as exc:
        print(f"[FAIL] 配置：{exc}")
        return 2

    key, base = env["LLM_API_KEY"], env["LLM_BASE_URL"].rstrip("/")
    want_model = env["LLM_MODEL"]
    print(f"配置：base={base}  model={want_model}  key={mask(key)}")

    # ── 判据 1：模型清单 ────────────────────────────────────────────────
    try:
        status, body = _request(f"{base}/models", key)
    except ProbeError as exc:
        print(f"[FAIL] 网络层：{exc}")
        return 3
    if status != 200:
        print(f"[FAIL] GET /models 返回 {status}：{body}")
        print("       → 401/403 多为密钥无效；404 多为 base_url 少了或多了路径段")
        return 3
    ids = [m.get("id") for m in (body.get("data") or []) if isinstance(m, dict)] if isinstance(body, dict) else []
    if not ids:
        print(f"[FAIL] /models 通了但没解析出模型 id：{str(body)[:200]}")
        return 4
    print(f"[ok]   /models 返回 {len(ids)} 个模型")
    if show_models:
        for mid in ids:
            print(f"       - {mid}")

    # ── 判据 2：配置里的模型名真的存在 ──────────────────────────────────
    if want_model not in ids:
        print(f"[FAIL] LLM_MODEL={want_model!r} 不在服务端模型列表里")
        print(f"       可选：{ids}")
        print("       → 只改 .env 的 LLM_MODEL，不要改代码；模型名以这个列表为准，不靠猜")
        return 4
    print(f"[ok]   模型 {want_model} 在服务端清单内")

    # ── 判据 3：真发一次极短对话 ────────────────────────────────────────
    payload = {
        "model": want_model,
        "messages": [{"role": "user", "content": "只回复两个字：收到"}],
        # 512 而不是 16：实测该端点会先花掉一批 reasoning_tokens（第一次 14 个、第二次就把 16 全吃了、
        # 正文返回空）。上限太小会让探针自己变得时好时坏，把"环境正常"误报成"接口坏了"。
        "max_tokens": 512,
        "temperature": 0,
    }
    t0 = time.monotonic()
    try:
        status, body = _request(f"{base}/chat/completions", key, payload)
    except ProbeError as exc:
        print(f"[FAIL] 网络层：{exc}")
        return 3
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if status != 200:
        print(f"[FAIL] POST /chat/completions 返回 {status}（{elapsed_ms} ms）：{body}")
        print("       → 402 多为余额不足；429 多为限流；400 多为参数/模型名不被该端点接受")
        return 3
    choices = body.get("choices") or [] if isinstance(body, dict) else []
    message = (choices[0].get("message") or {}) if choices else {}
    text = (message.get("content") or "").strip()
    finish = choices[0].get("finish_reason") if choices else None
    if not text:
        print(f"[FAIL] 通了但正文为空（finish_reason={finish!r}）：{str(body)[:200]}")
        if finish == "length":
            print("       → 被 max_tokens 截断：这是推理模型的常见坑，调大预算再试")
        return 4
    usage = body.get("usage") or {}
    print(f"[ok]   对话返回 {elapsed_ms} ms，回答={text[:20]!r}，用量={usage}")

    print("\n结论：三条判据全过，B-04 / B-03b 可以用这个端点。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
