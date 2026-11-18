"""变异检验：`tests/test_replay_premise.py` 的四条判据能不能真的变红。

四条各自对应一种「安静失效」——它们都不会让任何测试报错，只会让「不存返回体」这个
决定在某天变成错的：

| 变异 | 模拟的现实 |
|---|---|
| M1 返回体带上时间戳 | 有人让 mock 依赖时间 / 随机数 ⇒ 事后重建与当时返回不一致 |
| M2 分页返回与数据脱钩 | 有人把数据换成常量 / 缓存 ⇒ 改数据已看不出差别，"冻结数据版本"失去意义 |
| M3 match 过滤失效 | 有人删掉参数过滤 ⇒ 别的页也消耗故障额度，重建输入描述要改 |
| M4 mock 真写盘 | 有人加了真实副作用工具 ⇒ 「重放不需要沙箱」这条成本判断失效 |

判定交给唯一执行器（`_runner.judge`，只认 `rc == 1`）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # tools/mutations/ 比 tests/ 深一层
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _runner import run_mutations  # noqa: E402

T = "tests/test_replay_premise.py"
MT = ROOT / "src" / "arena" / "mock_tools.py"

MUTATIONS = [
    (
        "M1 返回体带时间戳（重建与当时返回不再一致）",
        MT,
        '        page = rows[start:start + limit]\n'
        '        return 200, {"orders": copy.deepcopy(page), "total": total}',
        '        page = rows[start:start + limit]\n'
        '        import time\n'
        '        return 200, {"orders": copy.deepcopy(page), "total": total,\n'
        '                     "served_at": time.time()}',
        f"{T}::test_every_response_is_rebuildable_from_data_and_params",
    ),
    (
        "M2 分页金额与数据脱钩（改数据看不出差别）",
        MT,
        '        page = rows[start:start + limit]\n'
        '        return 200, {"orders": copy.deepcopy(page), "total": total}',
        '        page = rows[start:start + limit]\n'
        '        page = [{"order_id": r.get("order_id"), "amount": 0} for r in page]\n'
        '        return 200, {"orders": copy.deepcopy(page), "total": total}',
        f"{T}::test_changing_the_data_changes_the_response",
    ),
    (
        "M3 match 过滤失效（不匹配的页也消耗故障额度）",
        MT,
        '        for key, want in self._fault_match.items():',
        '        for key, want in ():',
        f"{T}::test_the_same_params_can_answer_differently_by_position",
    ),
    (
        "M4 mock 真写盘（重放不再无副作用）",
        MT,
        '        self._written: dict[str, str] = {}',
        '        self._written: dict[str, str] = {}\n'
        '        import os\n'
        '        open(os.devnull, "w").close()  # 变异：制造一次真实文件系统调用',
        f"{T}::test_mock_touches_no_filesystem",
    ),
]


def main() -> int:
    return run_mutations(MUTATIONS, default_path=MT)


if __name__ == "__main__":
    raise SystemExit(main())
