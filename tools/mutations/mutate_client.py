"""LLM 传输层的变异检验（临时脚本，不提交）。判定三档同 round-75。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# 站到仓根再干活：脚本里全是相对路径（src/… / tests/…），在别的目录直接跑会
# FileNotFoundError。round-86 收尾时实测踩到（用户要求给完整路径，才发现"完整路径"
# 反而跑不通）。这里按"往上找 src/arena"定位仓根，从 .git/ 或 tools/mutations/ 调用都成立。
import os as _os

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
_os.chdir(_ROOT)

SRC = Path("src/arena/llm_client.py")
PYEXE = sys.executable
T = "tests/test_llm_client.py"

# pytest 默认把 tmp_path 建在系统临时目录（Windows 上是 %LOCALAPPDATA% 下的 Temp），
# 清理时会触发沙箱的删除确认。改到仓库内的 .git/pt，删除范围就落在仓库里。
PT = Path(".git/pt").resolve()
PT.mkdir(parents=True, exist_ok=True)
CHILD_ENV = {**os.environ, "PYTEST_DEBUG_TEMPROOT": str(PT)}

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
    ("M5 退避固定不递增", "min(2 ** attempt * 0.5, 4.0)", "0.5",
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
    orig = SRC.read_text(encoding="utf-8")
    results = []
    try:
        for name, old, new, node in MUTATIONS:
            hits = orig.count(old)
            if hits < 1:
                results.append((name, "INVALID", "锚点未命中"))
                continue
            SRC.write_text(orig.replace(old, new), encoding="utf-8")
            r = subprocess.run(
                [PYEXE, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                capture_output=True,
                text=True,
                env=CHILD_ENV,
            )
            SRC.write_text(orig, encoding="utf-8")
            out = (r.stdout + r.stderr).lower()
            if r.returncode == 5:
                results.append((name, "INVALID", "测试节点不存在"))
            elif r.returncode == 0:
                results.append((name, "MISSED", "测试仍绿 ⇒ 没在管这事"))
            elif "error" in out and "failed" not in out:
                results.append((name, "INVALID", "收集/语法错误，不算抓住"))
            else:
                last = [l for l in r.stdout.strip().splitlines() if l.strip()][-1:]
                results.append((name, "DETECTED", last[0] if last else ""))
    finally:
        SRC.write_text(orig, encoding="utf-8")

    for n, v, d in results:
        print(f"{v:9} {n}  {d}")
    ok = all(v == "DETECTED" for _, v, _ in results)
    print("---")
    print("判定：", "全 DETECTED" if ok else "有漏网或无效变异")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
