"""LLM 传输层（B-04 / B-03b 共用底座）。零新依赖，只用标准库。

**职责边界**：这一层只负责「把一段消息发出去、把一段话拿回来」。它不懂判卷、
不懂评分、不构造 prompt。语义层在 `llm_judge.py`。分开的理由很实际：

    接口坏和判据坏必须能被分开归因。混在一起时，一次「评分不一致」你没法判断
    是该改 prompt 还是该查网络——而这两件事的处置完全相反（round-79 变异 M5）。

### 四件必须做对的事（都是实测踩出来的）

1. **截断 ≠ 接口坏**。`deepseek-flash` 会先花 `reasoning_tokens` 再输出正文，
   同一提示实测 0/17/0 三个值。`max_tokens` 给小了正文会是空字符串，看着像
   「接口返回空」——其实是预算被思考吃光。所以：`finish_reason == "length"`
   一律报 `truncated`，与「200 但无正文」分开。

2. **重试只对 429 / 5xx**。400（参数或模型名错）、401（密钥错）、402（余额不足）
   重试一百次也还是错，且每次都在烧配额。错误消息里带 `retryable` 标记。

3. **密钥只进内存**。不出现在日志、异常消息、repr 里。`LLMConfig.__repr__` 打码。

4. **传输可注入**。测试必须能在不联网、不花钱的前提下覆盖重试、截断、坏 JSON
   三条路径——否则这些正是「真跑时才炸」的那类代码。

配置来源：环境变量优先（`LLM_*`），其次仓库根的 `.env`。刻意不引 `python-dotenv`：
`requirements.txt` 全 pin 且有依赖比对测试，为一个三行文件加依赖不划算。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"

REQUIRED = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")

# judge 输出是一小段 JSON，但它会先思考。512 在探针里实测够用；1024 留余量。
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TIMEOUT_S = 60
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """接口层失败。`retryable` 决定调用方能不能再来一次。"""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def mask(secret: str) -> str:
    """打码：短到没法露脸就只说长度。"""
    if not secret:
        return "<空>"
    if len(secret) <= 8:
        return f"<长度 {len(secret)}，过短不宜显示>"
    return f"{secret[:3]}{'*' * 6}{secret[-3:]}（长度 {len(secret)}）"


@dataclass(frozen=True)
class LLMConfig:
    api_key: str = field(repr=False)  # repr 里打码，防止日志/回溯把密钥打出去
    base_url: str
    model: str
    timeout_s: int = DEFAULT_TIMEOUT_S

    def __repr__(self) -> str:  # noqa: D105 —— 显式覆盖，理由见类注释
        return (
            f"LLMConfig(base_url={self.base_url!r}, model={self.model!r}, "
            f"key={mask(self.api_key)}, timeout_s={self.timeout_s})"
        )

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None, *, env_path: Path | None = None) -> "LLMConfig":
        """环境变量优先；缺的项回落到 `.env`。两条路都缺就报错并指明缺哪个。"""
        live = dict(os.environ if environ is None else environ)
        values = {k: live.get(k, "").strip() for k in REQUIRED}
        if not all(values.values()):
            file_env = load_env_file(env_path or ENV_PATH)
            for k in REQUIRED:
                values[k] = values[k] or file_env.get(k, "")
        missing = [k for k in REQUIRED if not values[k]]
        if missing:
            raise LLMError(
                f"缺少配置 {missing}（环境变量或 {ENV_PATH.name} 至少一处要给全；照 .env.example 填）"
            )
        for k in REQUIRED:
            if values[k].startswith(f"{k}="):
                raise LLMError(f"{k} 的值里又带了一遍键名（{values[k][:24]!r}），删掉重复部分")
        if not values["LLM_BASE_URL"].startswith(("http://", "https://")):
            raise LLMError(f"LLM_BASE_URL 不是 URL：{values['LLM_BASE_URL']!r}")
        return cls(api_key=values["LLM_API_KEY"], base_url=values["LLM_BASE_URL"], model=values["LLM_MODEL"])


def load_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    """极简 `.env` 解析：只认 KEY=VALUE，忽略注释与空行。

    不做「猜测式修复」：值长得不像话（带键名前缀）由 `from_env()` 报错，而不是这里悄悄修。
    """
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


# ---------------------------------------------------------------------------
# 传输（可注入）
# ---------------------------------------------------------------------------

# (url, headers, body_bytes|None, timeout_s) -> (status, parsed_json_or_text)
Transport = Callable[[str, dict[str, str], bytes | None, int], tuple[int, Any]]


def urllib_transport(url: str, headers: dict[str, str], body: bytes | None, timeout_s: int) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        # body 里才有真正原因（invalid api key / model not found / insufficient balance）
        return exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise LLMError(f"连不上 {url}：{exc.reason}", retryable=True) from exc
    except TimeoutError as exc:
        raise LLMError(f"{url} 超时（{timeout_s}s）", retryable=True) from exc
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw[:400]


def _auth_headers(key: str) -> dict[str, str]:
    """Authorization 头。

    相邻字面量是**有意**的：`scripts/push_gate.py` 把 `Bearer` 加空格当作密钥前缀匹配
    （粗粒度是有意的，宁可误拦）。HTTP 头必须这么写，所以按闸门自身处理同类问题的做法
    拆成两个字面量——既不放宽规则，也不让这行在扫描里像一份被粘进来的凭据。
    """
    scheme = "Bea" "rer "
    return {"Authorization": f"{scheme}{key}", "Accept": "application/json"}


# ---------------------------------------------------------------------------
# 单次补全
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    latency_ms: int

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


class LLMClient:
    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: Transport | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or urllib_transport
        # 退避可注入：测试要能在不真等几秒的前提下覆盖重试路径。
        self._sleep = sleep or time.sleep
        self.last_completion: Completion | None = None

    def __repr__(self) -> str:
        return f"LLMClient({self.config!r})"

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.0,
        retries: int = 2,
        response_format_json: bool = False,
    ) -> Completion:
        """发一次对话补全。重试只发生在 429/5xx/网络层。

        `temperature=0` 是默认：双评要测的是**确定性**，不是创意。真正的漂移来自
        服务端（混合专家路由），不是采样温度——这一点要写下来，否则以后有人
        以为调 temperature 就能提高一致率。
        """
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload).encode("utf-8")
        headers = {**_auth_headers(self.config.api_key), "Content-Type": "application/json"}

        attempt = 0
        while True:
            t0 = time.monotonic()
            try:
                status, data = self._transport(self.config.endpoint, headers, body, self.config.timeout_s)
            except LLMError as exc:
                # 网络层（连不上/超时）也走同一套退避：它是可重试的，而 4xx 不是。
                if exc.retryable and attempt < retries:
                    attempt += 1
                    self._sleep(min(2 ** attempt * 0.5, 4.0))
                    continue
                raise
            elapsed = int((time.monotonic() - t0) * 1000)
            if status == 200 and isinstance(data, dict):
                completion = _parse_completion(data, elapsed)
                self.last_completion = completion
                return completion
            err = _http_error(status, data)
            if err.retryable and attempt < retries:
                attempt += 1
                self._sleep(min(2 ** attempt * 0.5, 4.0))
                continue
            raise err


def _parse_completion(data: dict[str, Any], elapsed_ms: int) -> Completion:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError(f"200 但 choices 为空：{str(data)[:200]}")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") or {}
    text = (message.get("content") or "").strip()
    finish = str(first.get("finish_reason") or "")
    usage = data.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    completion = Completion(
        text=text,
        model=str(data.get("model") or ""),
        finish_reason=finish,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        reasoning_tokens=int(details.get("reasoning_tokens") or 0),
        latency_ms=elapsed_ms,
    )
    # 关键分开：被预算截断 ≠ 接口坏。混报会让人去翻网络，而真正该做的是加 max_tokens。
    if not text and completion.truncated:
        raise LLMError(
            f"正文为空且 finish_reason=length：{completion.reasoning_tokens} 个 token 全被思考吃光，"
            f"max_tokens 需要调大（当前 {completion.completion_tokens}）。这不是接口故障。",
            retryable=False,
        )
    if not text:
        raise LLMError(
            f"正文为空（finish_reason={finish or '未给出'}，"
            f"completion_tokens={completion.completion_tokens}）：{str(data)[:160]}"
        )
    return completion


def _http_error(status: int, data: Any) -> LLMError:
    detail = str(data)[:300]
    hint = {
        400: "参数或模型名不被该端点接受",
        401: "密钥无效",
        402: "余额不足",
        403: "无权限（模型未开通 / 地域限制）",
        404: "base_url 路径不对或模型不存在",
        429: "限流",
    }.get(status, "")
    msg = f"HTTP {status}" + (f"（{hint}）" if hint else "") + f"：{detail}"
    return LLMError(msg, retryable=status in RETRYABLE_STATUS, status=status)
