"""「每个任务都要能跑满分」这条不变式的变异检验（round-108）。

判定三档由 `_runner.judge()` 统一给，**只认 `rc == 1`**。

## 为什么这一层需要单独的变异

这条测试的全部价值在于"真跑一次"——它验的是**声明与执行是否一致**。
纯静态检查（`test_task_variants.py` 那 20 条）看得见字段，看不见后果：

- M1 故障注入改打第 2 次调用 ⇒ 任务声明依旧自洽（`failures[].attempt=2` 本身合法），
  但参考 Agent 只调一次就成功 ⇒ **故障永不触发** ⇒ R3 拿不到"失败后重试"的证据。
  静态检查全绿，只有真跑会红。
- M2 把超时降到 1 毫秒 ⇒ 执行虽然做完了，但 `final_status` 记成 `timeout` ⇒ R5 失败。
  这条同样只有真跑才看得见。

跑法：  python tools/mutations/mutate_task_exec.py
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

from _runner import run_mutations  # 同目录；判定只有一处实现

RET3 = Path("tasks/ret-003-fourth-page-fault.yaml")
FIX1 = Path("tasks/fix-001-token-expiry.yaml")
T = "tests/test_task_executability.py"

MUTATIONS = [
    ("M1 故障改打第 2 次调用（声明仍自洽，但永不触发）",
     RET3,
     "    - attempt: 1",
     "    - attempt: 2",
     f"{T}::test_reference_agent_scores_full_marks[ret-003-fourth-page-fault]"),

    ("M2 超时压到 1 毫秒（做完了也记成 timeout）",
     FIX1,
     "  invoke_timeout_seconds: 60",
     "  invoke_timeout_seconds: 0.001",
     f"{T}::test_reference_agent_scores_full_marks[fix-001-token-expiry]"),
]


def main() -> int:
    return run_mutations(MUTATIONS)


if __name__ == "__main__":
    raise SystemExit(main())
