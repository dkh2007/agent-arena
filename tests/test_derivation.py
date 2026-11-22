"""B-20a：推导规则的执行者 —— 把 `scenario` 的中文变成能跑的规则，并当心它悄悄变宽。

判据分两组，各有各的失效方式：

| 组 | 判据 | 失效时会发生什么（不报错、只让对账变假） |
|---|---|---|
| 对上答案键 | 规则现算 == `test_inputs.expected`（10 个任务） | 规则与题面悄悄分叉：对账拿一份"错的期望"去比答案 |
| 拒跑该拒的 | 未声明的键 / 谓词 / 聚合 / 数据形状一律报错 | 静默按默认处理 ⇒ 声明了却没有执行者 |

第一组顺带替代了"期望值是手抄的"这件事：`test_task_variants.py` 已经用参考 Agent 的
`_summarize_orders` 现算过一遍，但那是**解题者**的实现；这里用的是任务 YAML 自己声明的规则。
两者同时相等 ⇒ 题面、答案键、规则三处对齐（三处相等的成本很低，分叉的代价很高）。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import derivation as dv  # noqa: E402
from arena.diagnosis_report import load_task  # noqa: E402

TASKS = ROOT / "tasks"

# 期望值在 YAML 里按两位小数书写，规则本身不取整（取整是呈现口径）。
# 容差取 rubric 的一半（`tolerance.total_amount = 0.01`）：比"两位小数"更严，
# 又不会因为浮点求和顺序在末位漂而误报。
TOL = 0.005

# 合成数据：每种形状各一行，用来单独验每条谓词
ROWS = [
    {"order_id": "a", "amount": 10},      # 有效
    {"order_id": "b", "amount": 0},       # 有效：0 不是负数
    {"order_id": "c"},                    # 缺字段
    {"order_id": "d", "amount": None},    # null
    {"order_id": "e", "amount": -1},      # 负数
]


def _all_tasks() -> dict[str, dict]:
    return {p.stem: load_task(p) for p in sorted(TASKS.glob("*.yaml"))}


def _rule(**over) -> dict:
    """一份最小可跑规则（不含 rows 路径），`over` 覆盖字段用来造坏规则。"""
    base = {
        "rows": "test_inputs.orders_fixture",
        "distinct_by": "order_id",
        "valid_when": [
            {"field": "amount", "rule": "not_null"},
            {"field": "amount", "rule": "gte", "value": 0},
        ],
        "outputs": {"valid_count": {"op": "count"}, "total_amount": {"op": "sum", "field": "amount"}},
    }
    base.update(over)
    return base


def _norm(raw: dict) -> dict:
    """把 `_rule()` 的原始形状过一遍 load_rule 的校验（借一个只带 derivation 的任务壳）。"""
    return dv.load_rule({"derivation": raw})


# ── 第一组：对上答案键 ──────────────────────────────────────────────────────


def test_rule_reproduces_every_answer_key():
    """10 个任务的「现算答案」必须等于 YAML 里的答案键。"""
    checked = 0
    for stem, task in _all_tasks().items():
        if "derivation" not in task:
            continue
        want = task["test_inputs"]["expected"]
        got = dv.derive_task(task)
        assert set(got) == set(want), f"{stem}: 规则输出字段 {sorted(got)} 与答案键 {sorted(want)} 不一致"
        for name, expected in want.items():
            assert abs(got[name] - expected) <= TOL, (
                f"{stem}: 规则现算 {name}={got[name]} 与答案键 {expected} 不符"
            )
        checked += 1
    # 数量**不写死**（2026-11-22，B-30 扩题前置）。写死一个数会逼人每加一道题就改一次，
    # 而"该有几道"这件事本来就能从磁盘算出来：答案键里有 `valid_count` 的题，
    # 就是必须声明推导规则的题；定位题（fix-*）的答案是根因判断，不在其中。
    # 这样守住的仍是原来那个意图——漏声明 derivation 就红——但加题时不用再动这里一行。
    tasks = _all_tasks()
    needs_rule = [stem for stem, t in tasks.items()
                  if "valid_count" in (t.get("test_inputs", {}).get("expected") or {})]
    missing = sorted(s for s in needs_rule if "derivation" not in tasks[s])
    assert checked == len(needs_rule), (
        f"{checked} 个任务声明了 derivation，但有 {len(needs_rule)} 个任务的答案键含 valid_count；"
        f"缺规则的是：{missing}"
    )


def test_tasks_with_answer_keys_declare_a_rule():
    """答案键里有 `valid_count` 的任务，必须声明推导规则。

    新加一个汇报类任务时最容易漏这一步：rubric 照抄、数据换了，规则没写 ⇒
    对账拿不到可执行的期望，只能继续靠人手抄的数。
    """
    missing = [stem for stem, t in _all_tasks().items()
               if "valid_count" in (t.get("test_inputs", {}).get("expected") or {})
               and "derivation" not in t]
    assert not missing, f"这些任务的答案键是推导值，但没有 derivation 规则：{missing}"


def test_every_condition_is_load_bearing():
    """母本规则里删掉任何一条条件，结果必须**变**或当场报错——不许删了没反应。

    第一版规则写了三条（present / not_null / gte），实测删掉前两条结果一模一样：
    比较谓词自己就把缺失与 null 吞了，"剔除缺失行"这条声明没有执行者。
    这条用例就是那次事故的锁：以后谁再加一条装饰性条件，删它的那条断言会绿，于是红。
    """
    task = load_task(TASKS / "dbg-001-order-report.yaml")
    rows = task["test_inputs"]["orders_fixture"]
    base = dv.derive_task(task)

    conds = list(task["derivation"]["valid_when"])
    assert len(conds) >= 2, "条件少于两条就无所谓『删一条』了"
    for i, cond in enumerate(conds):
        survived = dict(task["derivation"], valid_when=[c for j, c in enumerate(conds) if j != i])
        rule = dv.load_rule({"derivation": survived})
        try:
            got = dv.derive(rows, rule)
        except dv.DerivationError:
            continue                       # 拒跑也算"这条条件有用"
        assert got != base, (
            f"删掉第 {i} 条条件（{cond}）后结果没变（{got}）——"
            "这条声明没有执行者，应当从规则里去掉"
        )


# ── 第二组：拒跑该拒的 ──────────────────────────────────────────────────────


def test_not_null_is_the_only_way_to_drop_missing_and_null():
    """缺字段 / null 只能由 `not_null` 声明；只写比较谓词 ⇒ 拒跑，不静默剔除。"""
    rule = _norm(_rule(valid_when=[{"field": "amount", "rule": "gte", "value": 0}]))
    for bad in ({"order_id": "x"}, {"order_id": "x", "amount": None}):
        with pytest.raises(dv.DerivationError) as ei:
            dv.derive([bad], rule)
        assert "not_null" in str(ei.value), "报错必须告诉人该怎么改，只报『不合法』不算"

    ok = _norm(_rule())
    assert dv.derive(ROWS, ok) == {"valid_count": 2, "total_amount": 10.0}


def test_each_predicate_means_its_own_thing():
    """四个谓词各自的边界：`gt` 那种"差不多"的写法不算数。"""
    def one(cond, row):
        # 这里只考谓词，关掉去重（否则单行还缺 order_id，会先在去重那步报错）
        rule = _norm(_rule(valid_when=[cond], distinct_by=None, outputs={"n": {"op": "count"}}))
        return dv.derive([row], rule)["n"]

    assert one({"field": "amount", "rule": "not_null"}, {"amount": None}) == 0
    assert one({"field": "amount", "rule": "not_null"}, {"amount": 5}) == 1
    assert one({"field": "amount", "rule": "gte", "value": 0}, {"amount": 0}) == 1   # 0 不是负数
    assert one({"field": "amount", "rule": "gte", "value": 0}, {"amount": -0.01}) == 0
    assert one({"field": "amount", "rule": "lte", "value": 10}, {"amount": 10}) == 1
    assert one({"field": "amount", "rule": "lte", "value": 10}, {"amount": 10.01}) == 0
    assert one({"field": "amount", "rule": "eq", "value": 3}, {"amount": 3}) == 1
    assert one({"field": "amount", "rule": "eq", "value": 3}, {"amount": 3.01}) == 0


def test_non_numeric_values_are_refused():
    """字符串金额 / 布尔金额：没有口径，拒跑而不是替它决定算不算有效。"""
    rule = _norm(_rule(valid_when=[{"field": "amount", "rule": "not_null"}]))
    for bad in ("120.5", True, {"x": 1}):
        with pytest.raises(dv.DerivationError) as ei:
            dv.derive([{"order_id": "x", "amount": bad}], rule)
        assert "amount" in str(ei.value), f"报错要点名是哪个字段：{ei.value}"


def test_sum_does_not_depend_on_row_order():
    """重放把分页拼起来时页序可以变：和不能跟着变（否则对账会对出个位数差异）。"""
    rule = _norm(_rule())
    forward = dv.derive(ROWS, rule)["total_amount"]
    backward = dv.derive(list(reversed(ROWS)), rule)["total_amount"]
    assert forward == backward == 10.0


def test_duplicate_identity_counts_once():
    """同一 `order_id` 出现两次只算一行——重放把同一页拉两次时就是这个形状。"""
    rule = _norm(_rule())
    twice = ROWS + [dict(ROWS[0])]
    assert dv.derive(twice, rule) == {"valid_count": 2, "total_amount": 10.0}


def test_dedupe_runs_before_filtering():
    """同一 id 的两行内容冲突时，保留首次出现的那行，**不给"挑一份能过的"留缝**。

    反过来的话（先筛后去重），把一条负数记录抄一份改成正数就能既保住数据又通过判定。
    """
    rule = _norm(_rule())
    rows = [{"order_id": "z", "amount": -5}, {"order_id": "z", "amount": 5}]
    assert dv.derive(rows, rule) == {"valid_count": 0, "total_amount": 0.0}


def test_missing_distinct_key_is_refused():
    rule = _norm(_rule())
    with pytest.raises(dv.DerivationError) as ei:
        dv.derive([{"amount": 1}], rule)
    assert "order_id" in str(ei.value)


@pytest.mark.parametrize("bad, needle", [
    ({"extra": 1}, "extra"),                                        # 顶层写错键
    ({"valid_when": []}, "valid_when"),                             # 没有条件 ⇒ 等于全部有效
    ({"valid_when": [{"field": "amount", "rule": "gt", "value": 0}]}, "gt"),   # 未实现的谓词
    ({"valid_when": [{"field": "amount", "rule": "gte"}]}, "value"),           # 比较缺 value
    ({"valid_when": [{"field": "amount", "rule": "not_null", "value": 1}]}, "not_null"),
    ({"outputs": {"n": {"op": "mean"}}}, "mean"),                    # 未实现的聚合
    ({"outputs": {"n": {"op": "sum"}}}, "field"),                     # sum 缺字段
    ({"outputs": {"n": {"op": "count", "field": "amount"}}}, "count"),
    ({"outputs": {}}, "outputs"),                                     # 一个都不算就没东西可对账
    ({"rows": ""}, "rows"),
])
def test_bad_rules_are_loud(bad, needle):
    """规则写错必须在加载期报错，并点名错在哪——静默忽略等于声明了不生效。"""
    with pytest.raises(dv.DerivationError) as ei:
        dv.load_rule({"derivation": _rule(**bad)})
    assert needle in str(ei.value), f"报错没点名「{needle}」：{ei.value}"


def test_missing_rule_and_missing_rows_path_are_loud():
    with pytest.raises(dv.DerivationError) as ei:
        dv.derive_task({"test_inputs": {}})
    assert "derivation" in str(ei.value)

    with pytest.raises(dv.DerivationError) as ei:
        dv.derive_task({"derivation": _rule(rows="test_inputs.nope"), "test_inputs": {}})
    assert "nope" in str(ei.value)


def test_load_rule_does_not_alias_the_task():
    """归一化要返回副本：调用方改规则不该改到任务原文（任务对象会被复用）。"""
    task = {"derivation": _rule()}
    snapshot = copy.deepcopy(task)
    rule = dv.load_rule(task)
    rule["valid_when"][0]["field"] = "tampered"
    assert task == snapshot, "load_rule 把任务里的规则对象交出去了"
