"""B-20a：把 `scenario` 里那两句自然语言，变成一份**可执行的推导规则**。

## 它补的是哪一个洞

`reconciliation.py` 只能找**逐字出现**的数：工具返回值里有 `120.5`，就能对上 `120.5`。
但答案里的 `17` / `1837.99` 从来不会出现在任何一次响应里——它们是从多行数据算出来的
（条数与合计）。于是「答案是否来自过程」这件事，在代码里**没有执行者**：
口径只写在任务 YAML 的 `scenario` 里，是给人读的中文。

「重放」能重算出「当时返回了什么」（`tests/test_replay_premise.py` 钉住了这条），
但它算不出「答案该是什么」。差的就是这一层：**哪些行算有效、怎么聚合**。

## 关键设计：规则只吃「一组行」，不知道行从哪来

同一份规则，喂不同的行集就是两件事：

| 喂什么 | 算出什么 | 回答什么问题 |
|---|---|---|
| 全量夹具 `test_inputs.orders_fixture` | 期望答案 | 答案键对不对（`test_inputs.expected` 的现算版本） |
| 重放回来的行（该 Agent 实际看到的） | 「过程能推出什么」 | 答案有没有出处（P3 对账，B-20b 接线） |

所以本模块**不读 trace、不读提交、不碰 mock**：它只做「行 → 数」。
把两个行集拼起来的责任在调用方——因为「拉全了没有」正是要被判定的东西，
不能在这里顺手替它补上。

## 规则长什么样（写在任务 YAML 里）

```yaml
derivation:
  rows: test_inputs.orders_fixture     # 行集从哪儿取（点路径）
  distinct_by: order_id                # 可省；同一 id 只算一次
  valid_when:                          # 全部条件同时成立才算有效行；顺序即求值顺序
    - {field: amount, rule: not_null}
    - {field: amount, rule: gte, value: 0}
  outputs:
    valid_count: {op: count}
    total_amount: {op: sum, field: amount}
```

## 三条语义决定（都是选的，不是推出来的）

1. **"剔除"只能由 `not_null` 声明，比较谓词不在背后替它做事**。
   第一版写成「缺字段 ⇒ 所有谓词为假」，看着宽容，实际把 `present` / `not_null`
   变成了装饰：`gte` 自己就把缺失与 null 吞掉了，删掉那两条结果一模一样。
   现在的口径是：`gte/lte/eq` 拿到的不是数值（缺字段、null、字符串、布尔）就**拒跑**，
   要剔掉这些行，必须在规则里写明 `not_null`。于是「哪些行算有效」没有一条是隐式的：
   删掉任何一条条件，要么答案变、要么当场报错。
2. **非数值（字符串、布尔）⇒ 拒跑**。文案没写过「字符串金额算不算有效」，
   规则不替它决定。（参考 Agent 的 `_summarize_orders` 把非数值当无效行——
   那是**解题者**的选择，数据里没有这种行，两者从未对撞过。判定层不该学解题者的口味。）
3. **`sum` 用 `math.fsum`，不取整**。重放时行是按页拼起来的，页序可以变；
   `fsum` 是正确舍入的求和，与顺序无关（朴素 `+=` 会随页序在末位漂）。
   取整属**呈现**口径，交给带容差的比较（rubric 的 `tolerance.total_amount = 0.01`），
   不在推导里做——否则换个三位小数的任务就得改代码。

**去重在筛之前**：`distinct_by` 先按身份去重、再判有效。反过来的话，
「同一条记录抄两行、一份有效一份无效」就能挑出能过的那份，去重形同虚设。
同一 id 的两行内容若不同，保留**首次出现**的那行；这里不判谁对（那是数据质量问题），
只保证同一身份不重复计。
为什么它必须在规则里而不是留给拼行集的人：重放把两次分页拼起来时，同一页被拉两次就会
重复计数，而「重复行算一条还是两条」属于「哪些行算有效」。今天的夹具 id 全唯一，
这一项是空转的——`tests/test_derivation.py` 用合成数据单独验它。

## 它不是什么

- **不是断言**：产出的是数值，不是 pass/fail；更不产生 `error_patterns`。
- **不进 credit 断言**（B-20b 硬约束）：credit 分母恒为权重和，一条"有代理才过得了"
  的断言等于给每份提交收税。落点是中性流程事实或诊断区。
"""

from __future__ import annotations

import math
from typing import Any

# 规则里允许出现的键。写错的键**必须报错**：静默忽略等于"声明了但没生效"，
# 而这类失效不报错、只让判定结果悄悄变样（第 6 轮评审对"没有执行者的声明"的口径）。
_RULE_KEYS = {"rows", "distinct_by", "valid_when", "outputs"}
_COND_KEYS = {"field", "rule", "value"}
_OUT_KEYS = {"op", "field"}

# `not_null` 不需要比较值；其余谓词必须有 `value`。
# 刻意**没有** `present`：它和 `not_null` 的差别是"null 算不算存在"，没人需要这个区别，
# 留着只会让规则出现两条写法却只生效一条。
_PREDICATES = {"not_null", "gte", "lte", "eq"}
_NEEDS_VALUE = {"gte", "lte", "eq"}

# `count` 不取字段；`sum` 必须有字段。
_OPS = {"count", "sum"}


class DerivationError(ValueError):
    """规则不自洽，或数据形状超出规则声明：宁可拒跑，也不猜。"""


def _resolve(task: dict, path: str) -> Any:
    """按 `test_inputs.orders_fixture` 这种点路径取值；取不到就报错，不返回 None。"""
    node: Any = task
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise DerivationError(f"derivation.rows 指向的路径不存在：{path}（断在 {part}）")
        node = node[part]
    return node


def _bad_keys(where: str, got: Any, allowed: set[str]) -> None:
    if not isinstance(got, dict):
        raise DerivationError(f"{where} 必须是映射，实际是 {type(got).__name__}")
    extra = set(got) - allowed
    if extra:
        raise DerivationError(
            f"{where} 里有未声明的键 {sorted(extra)}（只认 {sorted(allowed)}）——"
            "写错的键不会生效，静默忽略等于声明了却没有执行者"
        )


def load_rule(task: dict, key: str = "derivation") -> dict:
    """读出并校验任务里的推导规则，返回归一化后的副本。

    校验只做「自洽性」，不碰数据：`rows` 能不能取到在 `derive_task()` 里才验，
    因为调用方也可能直接喂一组行（重放侧），那时不需要 `rows` 这个路径。
    """
    raw = task.get(key)
    if raw is None:
        raise DerivationError(f"任务没有声明 {key}:（scenario 里的口径没有可执行版本）")
    _bad_keys(key, raw, _RULE_KEYS)

    rows_path = raw.get("rows")
    if not isinstance(rows_path, str) or not rows_path:
        raise DerivationError(f"{key}.rows 必须是非空点路径字符串（例：test_inputs.orders_fixture）")

    distinct_by = raw.get("distinct_by")
    if distinct_by is not None and (not isinstance(distinct_by, str) or not distinct_by):
        raise DerivationError(f"{key}.distinct_by 必须是字段名或省略，实际是 {distinct_by!r}")

    conds = raw.get("valid_when")
    if not isinstance(conds, list) or not conds:
        raise DerivationError(f"{key}.valid_when 必须是非空列表（没有条件就等于全部行都有效，不猜）")
    norm_conds: list[dict] = []
    for i, cond in enumerate(conds):
        where = f"{key}.valid_when[{i}]"
        _bad_keys(where, cond, _COND_KEYS)
        field = cond.get("field")
        if not isinstance(field, str) or not field:
            raise DerivationError(f"{where}.field 必须是非空字段名")
        rule = cond.get("rule")
        if rule not in _PREDICATES:
            raise DerivationError(f"{where}.rule={rule!r} 未实现（只认 {sorted(_PREDICATES)}）")
        if rule in _NEEDS_VALUE:
            if "value" not in cond:
                raise DerivationError(f"{where}（rule={rule}）缺 value")
            norm_conds.append({"field": field, "rule": rule, "value": cond["value"]})
        else:
            if "value" in cond:
                raise DerivationError(f"{where}（rule={rule}）不该带 value：它只判断「有没有值」")
            norm_conds.append({"field": field, "rule": rule})

    outputs = raw.get("outputs")
    if not isinstance(outputs, dict) or not outputs:
        raise DerivationError(f"{key}.outputs 必须是非空映射（一个都不算，就没有可对账的东西）")
    norm_outputs: dict[str, dict] = {}
    for name, spec in outputs.items():
        where = f"{key}.outputs.{name}"
        _bad_keys(where, spec, _OUT_KEYS)
        op = spec.get("op")
        if op not in _OPS:
            raise DerivationError(f"{where}.op={op!r} 未实现（只认 {sorted(_OPS)}）")
        field = spec.get("field")
        if op == "sum":
            if not isinstance(field, str) or not field:
                raise DerivationError(f"{where}（op=sum）必须声明 field")
            norm_outputs[name] = {"op": op, "field": field}
        else:
            if field is not None:
                raise DerivationError(f"{where}（op=count）不该带 field：数的是行数，不是某列")
            norm_outputs[name] = {"op": op}

    return {
        "rows": rows_path,
        "distinct_by": distinct_by,
        "valid_when": norm_conds,
        "outputs": norm_outputs,
    }


def _number(value: Any, *, where: str) -> float:
    """只接受真数值：`bool` 是 `int` 的子类，金额里出现 `True` 是数据出错，不是 1。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DerivationError(
            f"{where} 不是数值（{type(value).__name__}: {value!r}）——"
            "规则只声明了对数值的比较，这种形状没有口径，拒跑而不是替它决定算不算有效"
        )
    return float(value)


def _passes(index: int, row: dict, conds: list[dict]) -> bool:
    """逐条求值，全部成立才算有效行。

    只有 `not_null` 能把「缺字段 / null」判成无效；比较谓词碰到这两种形状就**拒跑**——
    否则规则里写不写 `not_null` 都一样，那条声明就成了装饰（见模块文档第 1 条）。
    """
    for cond in conds:
        field = cond["field"]
        where = f"第 {index} 行的 {field}"
        rule = cond["rule"]

        if rule == "not_null":
            if field not in row or row[field] is None:
                return False
            continue

        if field not in row:
            raise DerivationError(
                f"{where} 缺字段，而规则只用比较谓词（{rule}）碰它——"
                "要把它判成无效行，请在 valid_when 里写明 not_null"
            )
        value = row[field]
        if value is None:
            raise DerivationError(
                f"{where} 是 null，而规则只用比较谓词（{rule}）碰它——"
                "要把它判成无效行，请在 valid_when 里写明 not_null"
            )
        number = _number(value, where=where)
        if rule == "gte" and not number >= cond["value"]:
            return False
        elif rule == "lte" and not number <= cond["value"]:
            return False
        elif rule == "eq" and not number == cond["value"]:
            return False
    return True


def _dedupe(rows: list[Any], distinct_by: str | None) -> list[Any]:
    """按 `distinct_by` 保留**首次出现**的那一行。

    在**筛选之前**做：否则「同一条记录抄两行、一份有效一份无效」就能挑出能过的那份。
    """
    if distinct_by is None:
        return rows
    seen: set[Any] = set()
    out: list[Any] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue                      # 形状问题交给 derive() 里那一步统一报
        if distinct_by not in row:
            raise DerivationError(
                f"第 {i} 行没有 {distinct_by}，无法按它去重——"
                "缺值时不猜「算唯一」还是「算重复」，把规则或数据改到说清楚为止"
            )
        key = row[distinct_by]
        try:
            if key in seen:
                continue
            seen.add(key)
        except TypeError as exc:          # 列表/字典当键：规则要求的是可比较的身份
            raise DerivationError(f"第 {i} 行的 {distinct_by}={key!r} 不能当去重键：{exc}") from None
        out.append(row)
    return out


def derive(rows: list[Any], rule: dict) -> dict[str, float]:
    """按规则把一组行算成输出值。`rule` 应是 `load_rule()` 的产物。"""
    if not isinstance(rows, list):
        raise DerivationError(f"行集必须是列表，实际是 {type(rows).__name__}")
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DerivationError(f"第 {i} 行不是映射（{type(row).__name__}），规则无法作用")

    kept = [row for i, row in enumerate(_dedupe(rows, rule["distinct_by"]))
            if _passes(i, row, rule["valid_when"])]

    out: dict[str, float] = {}
    for name, spec in rule["outputs"].items():
        if spec["op"] == "count":
            out[name] = len(kept)
            continue
        field = spec["field"]
        values = [_number(row.get(field), where=f"有效行的 {field}") for row in kept]
        # fsum：正确舍入且与顺序无关（重放把分页拼起来时页序可能变，朴素 += 会在末位漂）
        out[name] = math.fsum(values)
    return out


def derive_task(task: dict, key: str = "derivation") -> dict[str, float]:
    """任务侧的用法：取 `rows` 路径指向的那组行，按规则算期望答案。"""
    rule = load_rule(task, key)
    rows = _resolve(task, rule["rows"])
    if not isinstance(rows, list):
        raise DerivationError(f"derivation.rows={rule['rows']} 取到的不是列表（{type(rows).__name__}）")
    return derive(rows, rule)


if __name__ == "__main__":  # pragma: no cover - 手工探查入口
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from arena.diagnosis_report import load_task

    t = load_task(Path(__file__).resolve().parents[2] / "tasks" / "dbg-001-order-report.yaml")
    print(json.dumps({"derived": derive_task(t), "expected": t["test_inputs"]["expected"]},
                     ensure_ascii=False, indent=2))
