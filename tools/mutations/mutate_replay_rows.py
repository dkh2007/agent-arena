"""变异检验：`tests/test_replay_rows.py` 是不是真的在管「行集怎么拼」。

六条变异各对应一种"拼行悄悄变宽"的方式——都不会崩溃，只会让推导值看起来合理：

| 变异 | 模拟的现实 |
|---|---|
| M1 编号不连续不拒跑 | 账本丢了中间一条，按数组顺序重放把故障额度打到别的调用上 |
| M2 失败页也算用上的页 | 诊断里 `pages` 含着失败页 ⇒ "用它重放过了"这句是假的 |
| M3 失败页当成功页处理 | 直接去取行键 ⇒ 契约错误被伪装成"这页没有行" |
| M4 多分页工具默认取第一个 | 「行集该来自哪个工具」没有口径，猜一个就是替作者决定 |
| M5 无分页工具默认放过 | 修复向任务也被当成"能重放"，报错变成空集 |
| M6 缺声明键当空页 | 推导值悄悄变小，看着像 Agent 少拉了一页（归因指错方向） |

判定交给唯一执行器（`_runner.judge`，只认 `rc == 1`）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _runner import run_mutations  # noqa: E402

T = "tests/test_replay_rows.py"
SRC = ROOT / "src" / "arena" / "replay.py"

MUTATIONS = [
    (
        "M1 编号不连续不拒跑（按数组顺序重放）",
        "    if sorted(seen) != want:",
        "    if False:",
        f"{T}::test_hole_in_call_index_is_rejected",
    ),
    (
        "M2 失败页也算用上的页",
        '            out.skipped.append({"call_index": idx, "reason": f"http_{status}"})',
        "            out.used.append(idx)",
        f"{T}::test_a_failed_page_is_recorded_but_not_refilled",
    ),
    (
        "M3 失败页当成功页处理（去取行键）",
        "        if not (200 <= status < 300):",
        "        if False:",
        f"{T}::test_a_failed_page_is_recorded_but_not_refilled",
    ),
    (
        "M4 多分页工具默认取第一个",
        "    if len(found) > 1:",
        "    if False:",
        f"{T}::test_two_paginated_tools_is_rejected",
    ),
    (
        "M5 无分页工具默认放过",
        "    if not found:",
        "    if False:",
        f"{T}::test_task_without_paginated_tool_is_rejected",
    ),
    (
        "M6 缺声明键当空页",
        '        if not isinstance(page, list):\n'
        '            raise ReplayError(\n'
        '                f"第 {idx} 条调用成功了，但返回体没有 {key!r} 这一项（实际键 {sorted(body)}）——"\n'
        '                "工具声明与实现对不上，不是这个 Agent 的问题"\n'
        '            )',
        '        if not isinstance(page, list):\n'
        '            page = []',
        f"{T}::test_successful_page_without_the_declared_key_is_rejected",
    ),
]


def main() -> int:
    return run_mutations(MUTATIONS, default_path=SRC, temp_prefix="pt-replay-")


if __name__ == "__main__":
    raise SystemExit(main())
