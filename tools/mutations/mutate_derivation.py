"""变异检验：`tests/test_derivation.py` 是不是真的在管「哪些行算有效、怎么聚合」。

七条变异各自对应一种"规则悄悄变宽"的方式——都不会抛异常，只会让对账拿到一份
看起来合理、实际不对的期望值：

| 变异 | 模拟的现实 |
|---|---|
| M1 合计按全部行算 | 筛条件写了但没进聚合（改了一处忘了另一处） |
| M2 条数按原始行数算 | 同上，反向：去重与筛选都没进 count |
| M3 `>=` 写成 `>` | 边界被挪一位：金额为 0 的行被当成无效（0 不是负数） |
| M4 去重被跳过 | 重放把同一页拼两次时重复计数 |
| M5 未知聚合静默放过 | 规则写了 `mean` 却按别的口径算，报错消失 |
| M6 非数值不再拒跑 | 字符串/布尔金额被 `float()` 悄悄转成数 |
| M7 缺字段静默当无效 | 退回第一版口径 ⇒ `not_null` 变成装饰（本轮已修过一次的真事故） |

判定交给唯一执行器（`_runner.judge`，只认 `rc == 1`）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # tools/mutations/ 比 tests/ 深一层
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _runner import run_mutations  # noqa: E402

D = "tests/test_derivation.py"
SRC = ROOT / "src" / "arena" / "derivation.py"

MUTATIONS = [
    (
        "M1 合计按全部行算（筛选没进聚合）",
        '        values = [_number(row.get(field), where=f"有效行的 {field}") for row in kept]',
        '        values = [_number(r.get(field), where="全部行") for r in rows]',
        f"{D}::test_rule_reproduces_every_answer_key",
    ),
    (
        "M2 条数按原始行数算（去重与筛选都被绕过）",
        '            out[name] = len(kept)',
        '            out[name] = len(rows)',
        f"{D}::test_not_null_is_the_only_way_to_drop_missing_and_null",
    ),
    (
        "M3 >= 写成 >（金额为 0 的行被误判无效）",
        '        if rule == "gte" and not number >= cond["value"]:',
        '        if rule == "gte" and not number > cond["value"]:',
        f"{D}::test_each_predicate_means_its_own_thing",
    ),
    (
        "M4 去重被跳过（重复拉同一页会重复计数）",
        '    if distinct_by is None:',
        '    if True:',
        f"{D}::test_duplicate_identity_counts_once",
    ),
    (
        "M5 未知聚合静默放过（mean 不再报错）",
        '        if op not in _OPS:',
        '        if False:',
        f"{D}::test_bad_rules_are_loud",
    ),
    (
        "M6 非数值不再拒跑（float() 悄悄转字符串/布尔）",
        '    if isinstance(value, bool) or not isinstance(value, (int, float)):',
        '    if False:',
        f"{D}::test_non_numeric_values_are_refused",
    ),
    (
        "M7 缺字段静默当无效（not_null 退回装饰）",
        '        if field not in row:\n'
        '            raise DerivationError(\n'
        '                f"{where} 缺字段，而规则只用比较谓词（{rule}）碰它——"\n'
        '                "要把它判成无效行，请在 valid_when 里写明 not_null"\n'
        '            )',
        '        if field not in row:\n'
        '            return False',
        f"{D}::test_not_null_is_the_only_way_to_drop_missing_and_null",
    ),
]


def main() -> int:
    return run_mutations(MUTATIONS, default_path=SRC, temp_prefix="pt-deriv-")


if __name__ == "__main__":
    raise SystemExit(main())
