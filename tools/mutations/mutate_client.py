"""LLM 传输层的变异检验（临时脚本，不提交）。判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）。"""

from __future__ import annotations

from pathlib import Path

# 站到仓根再干活：脚本里全是相对路径（src/… / tests/…），在别的目录直接跑会
# FileNotFoundError。round-86 收尾时实测踩到（用户要求给完整路径，才发现"完整路径"
# 反而跑不通）。这里按"往上找 src/arena"定位仓根，从 .git/ 或 tools/mutations/ 调用都成立。
import os as _os

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
_os.chdir(_ROOT)

from _runner import run_mutations  # 同目录；判定只有一处实现

SRC = Path("src/arena/llm_client.py")
T = "tests/test_llm_client.py"


MUTATIONS = [
    ("M1 手写 repr 不打码", "key={mask(self.api_key)}", "key={self.api_key}",
     f"{T}::test_config_repr_masks_secret"),
    ("M1b dataclass 默认 repr 也不打码", "    api_key: str = field(repr=False)", "    api_key: str = field()",
     f"{T}::test_api_key_field_is_repr_false_as_second_layer"),
    ("M2 截断与空正文合并", "    if not text and completion.truncated:", "    if False:",
     f"{T}::test_truncated_by_reasoning_tokens_is_reported_as_budget_not_failure"),
    ("M3 所有状态码都算可重试", "    return LLMError(msg, retryable=status in RETRYABLE_STATUS, status=status)",
     "    return LLMError(msg, retryable=True, status=status)",
     f"{T}::test_401_is_not_retried"),
    ("M4 5xx 不重试", "    return LLMError(msg, retryable=status in RETRYABLE_STATUS, status=status)",
     "    return LLMError(msg, retryable=False, status=status)",
     f"{T}::test_server_errors_are_retried_until_budget_exhausted"),
    # 锚点带上前一行 `err = _http_error(...)`：`self._sleep(...)` 在文件里有两处
    # （网络层异常与 HTTP 错误各一条重试路径），只写 sleep 会命中 2 次而判 INVALID。
    # 测试走的是 503（HTTP 错误）那条路，所以锚在这里才对得上。
    ("M5 退避固定不递增",
     '            err = _http_error(status, data)\n'
     '            if err.retryable and attempt < retries:\n'
     '                attempt += 1\n'
     '                self._sleep(min(2 ** attempt * 0.5, 4.0))',
     '            err = _http_error(status, data)\n'
     '            if err.retryable and attempt < retries:\n'
     '                attempt += 1\n'
     '                self._sleep(0.5)',
     f"{T}::test_backoff_grows"),
    ("M6 不查键名重复", '            if values[k].startswith(f"{k}="):', "            if False:",
     f"{T}::test_from_env_rejects_duplicated_key_name"),
    ("M7 .env 整块覆盖环境变量", '                values[k] = values[k] or file_env.get(k, "")',
     '                values[k] = file_env.get(k, "")',
     f"{T}::test_from_env_env_wins_over_file"),
    ("M8 不解析 reasoning_tokens", '        reasoning_tokens=int(details.get("reasoning_tokens") or 0),',
     "        reasoning_tokens=0,",
     f"{T}::test_reasoning_tokens_are_extracted"),
    ("M9 网络错误不重试", "                if exc.retryable and attempt < retries:", "                if False:",
     f"{T}::test_network_error_is_retried"),
    ("M10 端点不去尾斜杠", '        return f"{self.base_url.rstrip(\'/\')}/chat/completions"',
     '        return f"{self.base_url}/chat/completions"',
     f"{T}::test_endpoint_trims_trailing_slash"),
]

def main() -> int:
    """判定与还原全交给 `_runner`：本文件只负责"变异长什么样"。

    以前这里有一份自己的判定代码，窄看只是重复；实际是两套规则会分叉——旧那套
    把 rc=2/3/4 也当 `DETECTED`，于是节点名写错时会报"抓住了"（round-89 M14 实测）。
    """
    return run_mutations(MUTATIONS, default_path=SRC, temp_prefix="pt-client-")

if __name__ == "__main__":
    raise SystemExit(main())
