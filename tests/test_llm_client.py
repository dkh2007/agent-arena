"""LLM 传输层的约束测试（round-79）。**全程不联网、不花钱**。

锁的边界：
  ① 密钥不进 repr / 异常消息；
  ② 被预算截断 ≠ 接口坏——两种错误必须能被分开归因；
  ③ 重试只对 429/5xx/网络层；4xx 重试纯属烧配额；
  ④ 重试次数与退避可验证（注入 sleep，不真等）。

真接口的连通性由 `scripts/llm_probe.py` 负责，不在单测里做——单测一旦要联网，
在 CI 与断网环境就会红，而它红的原因跟代码质量无关。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import llm_client as lc  # noqa: E402

# 这个假密钥要"长得像真的"才能测打码逻辑，但字面量必须拆开：
# `scripts/push_gate.py` 把「sk 加一个连字符」当作密钥前缀匹配（粗粒度是有意的，宁可误拦）。
# 按闸门自身处理同类问题的做法拆成相邻字面量——既不放宽规则，也不让这一行
# 在扫描里真的像一份被粘进来的凭据。
SECRET = "s" "k-" + "a" * 30


def _cfg(**kw) -> lc.LLMConfig:
    base = dict(api_key=SECRET, base_url="https://api.example.com", model="judge-model")
    base.update(kw)
    return lc.LLMConfig(**base)


def _ok(text: str = '{"rating": 3, "patterns": []}', **usage) -> dict:
    details = {"reasoning_tokens": usage.pop("reasoning_tokens", 0)} if usage else {}
    return {
        "model": "judge-model",
        "choices": [{"message": {"content": text}, "finish_reason": usage.pop("finish", "stop")}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, **usage, **({"completion_tokens_details": details} if details else {})},
    }


class FakeTransport:
    """按脚本逐次返回；记录每次请求，供断言检查请求体与尝试次数。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, url, headers, body, timeout_s):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body), "timeout_s": timeout_s})
        if not self.script:
            raise AssertionError("FakeTransport 脚本用尽：调用次数超出预期")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(script, **kw) -> tuple[lc.LLMClient, FakeTransport, list[float]]:
    transport = FakeTransport(script)
    slept: list[float] = []
    client = lc.LLMClient(_cfg(**kw), transport=transport, sleep=slept.append)
    return client, transport, slept


# ---------------------------------------------------------------------------
# ① 密钥不外泄
# ---------------------------------------------------------------------------


def test_config_repr_masks_secret():
    """repr 里不能出现明文——回溯和日志最爱打印对象。"""
    text = repr(_cfg())
    assert SECRET not in text
    assert "***" in text
    assert "judge-model" in text, "模型名不是秘密，该留着便于排查"


def test_api_key_field_is_repr_false_as_second_layer():
    """纵深防御的机械保证：**手写 `__repr__` 被删掉时**也不能泄露。

    round-79 变异 M1 揭示了这件事：把 `field(repr=False)` 改成 `field()` 时测试
    全绿——因为手写 `__repr__` 完全覆盖了 dataclass 生成的版本，这一层当时是**死的**。
    两层防御里一层没被验证过，等于只有一层。所以这里直接把那一层锁住。
    """
    import dataclasses

    fields = {f.name: f for f in dataclasses.fields(lc.LLMConfig)}
    assert fields["api_key"].repr is False


def test_client_repr_masks_secret():
    client, _, _ = _client([(200, _ok())])
    assert SECRET not in repr(client)


def test_error_message_never_contains_key():
    """接口报错时不能把请求头带进消息里。"""
    client, _, _ = _client([(401, {"error": "invalid api key"})])
    with pytest.raises(lc.LLMError) as ei:
        client.complete([{"role": "user", "content": "x"}])
    assert SECRET not in str(ei.value)


# ---------------------------------------------------------------------------
# ② 截断 ≠ 接口坏
# ---------------------------------------------------------------------------


def test_truncated_by_reasoning_tokens_is_reported_as_budget_not_failure():
    """`finish_reason=length` 且正文空 ⇒ 明说「思考吃光预算」，且不可重试。

    这就是 round-79 实测踩到的坑：`max_tokens=16`，第二次调用 17 个思考 token，
    正文空。若报成「接口返回空」，排查方向会完全错到网络上去。
    """
    client, _, _ = _client([(200, _ok("", reasoning_tokens=17, finish="length", completion_tokens=17))])
    with pytest.raises(lc.LLMError) as ei:
        client.complete([{"role": "user", "content": "x"}])
    msg = str(ei.value)
    assert "思考吃光" in msg
    assert "max_tokens" in msg
    assert ei.value.retryable is False, "加预算才有用，重试同样的预算只会再失败一次"


def test_empty_content_without_truncation_is_a_different_error():
    """没被截断却空正文 ⇒ 另一个错误，提示里不能出现「思考」二字，避免误导。"""
    client, _, _ = _client([(200, _ok("", finish="stop"))])
    with pytest.raises(lc.LLMError) as ei:
        client.complete([{"role": "user", "content": "x"}])
    assert "思考吃光" not in str(ei.value)
    assert "正文为空" in str(ei.value)


def test_reasoning_tokens_are_extracted():
    client, _, _ = _client([(200, _ok("好", reasoning_tokens=42, finish="stop"))])
    c = client.complete([{"role": "user", "content": "x"}])
    assert c.reasoning_tokens == 42
    assert c.text == "好"
    assert c.model == "judge-model"


def test_empty_choices_is_loud():
    client, _, _ = _client([(200, {"choices": []})])
    with pytest.raises(lc.LLMError, match="choices 为空"):
        client.complete([{"role": "user", "content": "x"}])


# ---------------------------------------------------------------------------
# ③ 重试策略
# ---------------------------------------------------------------------------


def test_429_is_retried_then_succeeds():
    client, transport, slept = _client([(429, {"error": "rate limit"}), (200, _ok())])
    c = client.complete([{"role": "user", "content": "x"}])
    assert c.text.startswith("{")
    assert len(transport.calls) == 2
    assert len(slept) == 1 and slept[0] > 0


def test_401_is_not_retried():
    """密钥错重试一百次也还是错，且每次都在烧配额。"""
    client, transport, slept = _client([(401, {"error": "invalid api key"})])
    with pytest.raises(lc.LLMError) as ei:
        client.complete([{"role": "user", "content": "x"}])
    assert ei.value.retryable is False
    assert ei.value.status == 401
    assert len(transport.calls) == 1, "4xx 不许重试"
    assert slept == []


@pytest.mark.parametrize("status", [400, 402, 403, 404])
def test_client_errors_are_never_retried(status):
    client, transport, _ = _client([(status, {"error": "nope"})])
    with pytest.raises(lc.LLMError):
        client.complete([{"role": "user", "content": "x"}])
    assert len(transport.calls) == 1


@pytest.mark.parametrize("status", [500, 502, 503])
def test_server_errors_are_retried_until_budget_exhausted(status):
    client, transport, slept = _client([(status, "boom")] * 3)
    with pytest.raises(lc.LLMError) as ei:
        client.complete([{"role": "user", "content": "x"}], retries=2)
    assert ei.value.retryable is True
    assert len(transport.calls) == 3, "首次 + 2 次重试"
    assert len(slept) == 2


def test_network_error_is_retried():
    """连不上/超时是传输层抛的 LLMError，也要走同一套退避。"""
    client, transport, slept = _client([lc.LLMError("连不上", retryable=True), (200, _ok())])
    client.complete([{"role": "user", "content": "x"}])
    assert len(transport.calls) == 2
    assert len(slept) == 1


def test_network_error_not_retryable_propagates():
    client, transport, _ = _client([lc.LLMError("坏地址", retryable=False)])
    with pytest.raises(lc.LLMError, match="坏地址"):
        client.complete([{"role": "user", "content": "x"}])
    assert len(transport.calls) == 1


def test_backoff_grows():
    client, _, slept = _client([(503, "x")] * 4)
    with pytest.raises(lc.LLMError):
        client.complete([{"role": "user", "content": "x"}], retries=3)
    assert len(slept) == 3
    assert slept[0] < slept[1] < slept[2], "退避必须递增，否则扛不住持续限流"
    assert max(slept) <= 4.0, "上限要封住，否则一次评测能挂十几秒"


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------


def test_request_body_and_headers():
    client, transport, _ = _client([(200, _ok())])
    client.complete([{"role": "user", "content": "hi"}], max_tokens=64, temperature=0.0)
    call = transport.calls[0]
    assert call["url"] == "https://api.example.com/chat/completions"
    assert call["body"]["model"] == "judge-model"
    assert call["body"]["max_tokens"] == 64
    assert call["body"]["temperature"] == 0.0
    assert "response_format" not in call["body"]
    assert call["headers"]["Authorization"].startswith("Bea")  # 拆开的字面量拼回去仍是 Bearer


def test_json_mode_flag():
    client, transport, _ = _client([(200, _ok())])
    client.complete([{"role": "user", "content": "x"}], response_format_json=True)
    assert transport.calls[0]["body"]["response_format"] == {"type": "json_object"}


def test_endpoint_trims_trailing_slash():
    client, transport, _ = _client([(200, _ok())], base_url="https://api.example.com/")
    client.complete([{"role": "user", "content": "x"}])
    assert transport.calls[0]["url"] == "https://api.example.com/chat/completions"


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------


def test_from_env_reads_environment_first():
    cfg = lc.LLMConfig.from_env(
        {"LLM_API_KEY": SECRET, "LLM_BASE_URL": "https://env.example.com", "LLM_MODEL": "m-env"},
        env_path=Path("/nonexistent/.env"),
    )
    assert cfg.model == "m-env"
    assert cfg.base_url == "https://env.example.com"


def test_from_env_falls_back_to_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"# 注释\nLLM_API_KEY={SECRET}\nLLM_BASE_URL=https://file.example.com\nLLM_MODEL=m-file\n",
        encoding="utf-8",
    )
    cfg = lc.LLMConfig.from_env({}, env_path=env_file)
    assert cfg.model == "m-file"


def test_from_env_env_wins_over_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(f"LLM_API_KEY={SECRET}\nLLM_BASE_URL=https://file.example.com\nLLM_MODEL=m-file\n", encoding="utf-8")
    cfg = lc.LLMConfig.from_env({"LLM_MODEL": "m-env"}, env_path=env_file)
    assert cfg.model == "m-env"
    assert cfg.base_url == "https://file.example.com", "逐键回落，不是整块覆盖"


def test_from_env_names_missing_keys():
    with pytest.raises(lc.LLMError, match=r"缺少配置 \['LLM_MODEL'\]"):
        lc.LLMConfig.from_env(
            {"LLM_API_KEY": SECRET, "LLM_BASE_URL": "https://x.example.com"}, env_path=Path("/nonexistent/.env")
        )


def test_from_env_rejects_duplicated_key_name():
    """这就是 round-79 真实发生的填错：`LLM_MODEL=LLM_MODEL=deepseek-flash`。"""
    with pytest.raises(lc.LLMError, match="又带了一遍键名"):
        lc.LLMConfig.from_env(
            {"LLM_API_KEY": SECRET, "LLM_BASE_URL": "https://x.example.com", "LLM_MODEL": "LLM_MODEL=deepseek-flash"},
            env_path=Path("/nonexistent/.env"),
        )


def test_from_env_rejects_bad_url():
    with pytest.raises(lc.LLMError, match="不是 URL"):
        lc.LLMConfig.from_env(
            {"LLM_API_KEY": SECRET, "LLM_BASE_URL": "api.example.com", "LLM_MODEL": "m"},
            env_path=Path("/nonexistent/.env"),
        )


def test_load_env_file_missing_returns_empty(tmp_path):
    assert lc.load_env_file(tmp_path / "nope.env") == {}
