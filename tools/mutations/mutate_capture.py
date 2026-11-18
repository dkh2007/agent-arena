"""B-03b ①「平台侧采集 + 逐条对账」的变异检验（round-106）。

判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）：
DETECTED / MISSED / INVALID。

## 这一层的失效方式

采集层错了不会报错，只会**安静地说"一致"**——而"一致"正是它唯一的产出，所以格外的危险：

- M1 编号交给 Agent 自己编（账本记的是别人给号）⇒ "平台真的在编号"这句话变成假的；
- M2 对账按数组次序而不是按 `call_index` ⇒ 删掉中间一条时全都报"参数不符"，
  看着像记错，实际是**有一次调用消失了**；
- M3 平台有、自报缺不报 ⇒ 最要紧的那种篡改（把失败藏起来）变成静默通过；
- M4 参数不比 ⇒ 改写 `offset` 假装拉的是另一页，也能过；
- M5 失败调用缺 `http_status` 放行 ⇒ 与 B-22 的口径打对台（那是唯一必须给 `http_status` 的场合）；
- M6 条数不符不报 ⇒ 单看逐条会比较，会漏掉"整条多出来/少掉"这种最粗的形态。
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

from _runner import run_mutations  # 同目录；判定只有一处实现

PC = Path("src/arena/platform_capture.py")
T = "tests/test_platform_capture.py"

MUTATIONS = [
    ("M1 编号不归平台（call_index 交给 Agent）",
     PC,
     "                call_index=len(self._calls) + 1,",
     "                call_index=0,  # 变异：平台不给号，等 Agent 自己填",
     f"{T}::test_ledger_numbers_calls_from_one_by_arrival_order"),

    ("M2 对账按数组次序，而不是按 call_index",
     PC,
     "    for idx in sorted(set(cap) | set(rep)):",
     "    for idx, (cap_item, rep_item) in enumerate(zip(cap_list, rep_list), start=1):\n"
     "        # 变异：按数组次序配对 —— 中间少一条时全体错位，错位后仍然「对得上」\n"
     "        problems.extend(_compare_one(idx, cap_item, rep_item))\n"
     "        continue",
     f"{T}::test_renumbering_the_failed_call_is_named_not_silently_shifted"),

    ("M3 平台有、自报缺 —— 静默跳过",
     PC,
     "        if idx not in rep:\n            problems.append(",
     "        if idx not in rep:\n            continue\n        if False and (idx not in rep):\n            problems.append(",
     f"{T}::test_agent_dropping_the_failed_call_is_named"),

    ("M4 参数不比",
     PC,
     '    if cap.get("params") != rep.get("params"):',
     '    if False and cap.get("params") != rep.get("params"):  # 变异：参数不比',
     f"{T}::test_param_tampering_says_which_key"),

    ("M5 失败调用缺 http_status 放行",
     PC,
     '        if cap.get("status") == "error":',
     '        if False:  # 变异：失败调用缺 http_status 也不报',
     f"{T}::test_missing_http_status_on_a_failure_is_named"),

    ("M6 条数不符不报",
     PC,
     "    if len(cap_list) != len(rep_list):",
     "    if False and len(cap_list) != len(rep_list):  # 变异：条数不符不报",
     f"{T}::test_agent_dropping_the_failed_call_is_named"),
]


def main() -> int:
    """判定与还原全交给 `_runner`：本文件只负责"变异长什么样"。"""
    return run_mutations(MUTATIONS, temp_prefix="pt-capture-")


if __name__ == "__main__":
    raise SystemExit(main())
