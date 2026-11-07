"""JMESPath 表达力测试 —— 外部评审建议「用 JMESPath 替代自造 matcher」的核证。

对应 `docs/review-response-v1.md` §2 E1。本文件的断言就是处置结论本身：
  · 判定层可外借 JMESPath（4 类轨迹全判对，且 allow_interleaved 天然成立）
  · 分类层不能外借（假重试与无重试的过滤后序列相同，需第二条表达式 + 规则）
  · 三类能力是它做不到的（时序距离 / 跨产物因果 / 参数 schema 四项）

若哪天上游 JMESPath 语义变化导致这里变红，处置结论要重新评估。

运行：pytest spike/test_jmespath_expressiveness.py -v
"""

from __future__ import annotations

import jmespath
import pytest

# R3 的判定表达式原型：按 (工具名 + 参数子集) 过滤，投影出状态序列
R3_STATUS_SEQ = "calls[?name=='fetch_orders' && params.offset==`10`].status"
R3_OFFSET_SEQ = "calls[?name=='fetch_orders'].params.offset"

BASE = {"name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "http_status": 200}
PAGE2_FAIL = {"name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500}
PAGE2_OK = {"name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "success", "http_status": 200}
REPORT = {"name": "write_report", "params": {"path": "/workspace/report.json"}, "status": "success", "http_status": 200}


def judge(calls):
    """R3 判定：过滤后的状态序列里，error 之后是否出现 success。"""
    seq = jmespath.search(R3_STATUS_SEQ, {"calls": calls})
    if not seq or "error" not in seq:
        return False
    return "success" in seq[seq.index("error") + 1 :]


def classify_failure(calls):
    """错误模式分类：重试错了 vs 根本没重试。"""
    offsets = jmespath.search(R3_OFFSET_SEQ, {"calls": calls})
    failed_page = 10
    if failed_page not in offsets:
        return "no_error_observed"
    idx = offsets.index(failed_page)
    revisited = any(o in offsets[:idx] for o in offsets[idx + 1 :])
    return "retry_wrong_params" if revisited else "no_retry"


# ---------------------------------------------------------------- 判定层：可外借
@pytest.mark.parametrize(
    ("name", "calls", "expected"),
    [
        ("真重试", [BASE, PAGE2_FAIL, PAGE2_OK, REPORT], True),
        ("假重试", [BASE, PAGE2_FAIL, BASE, REPORT], False),
        ("真重试+穿插", [BASE, PAGE2_FAIL, REPORT, PAGE2_OK], True),
        ("无重试", [BASE, PAGE2_FAIL, REPORT], False),
    ],
)
def test_r3_judgement_matches_expectation(name, calls, expected):
    assert judge(calls) is expected, f"{name} 判错了"


def test_interleaving_needs_no_extra_mechanism():
    """「允许穿插」不是特性，是过滤的副产品 —— 所以 YAML 里的 allow_interleaved 可简化。"""
    no_interleave = judge([BASE, PAGE2_FAIL, PAGE2_OK])
    with_interleave = judge([BASE, PAGE2_FAIL, REPORT, PAGE2_OK])
    assert no_interleave is with_interleave is True


def test_adjacent_calls_both_matched():
    """过滤后序列能保留两次同键调用的先后，这正是判定所需。"""
    seq = jmespath.search(R3_STATUS_SEQ, {"calls": [BASE, PAGE2_FAIL, PAGE2_OK, REPORT]})
    assert seq == ["error", "success"]


# ---------------------------------------------------------------- 分类层：不可外借
def test_failure_modes_are_indistinguishable_by_judgement_expression_alone():
    """关键缺口：两种不同失败模式，判定表达式给出完全相同的序列。"""
    fake_retry = jmespath.search(R3_STATUS_SEQ, {"calls": [BASE, PAGE2_FAIL, BASE, REPORT]})
    no_retry = jmespath.search(R3_STATUS_SEQ, {"calls": [BASE, PAGE2_FAIL, REPORT]})
    assert fake_retry == no_retry == ["error"]


def test_second_expression_separates_the_two_failure_modes():
    """补第二条表达式即可分离 —— 所以分类层自研的成本是 3 行规则，不是一套引擎。"""
    fake = classify_failure([BASE, PAGE2_FAIL, BASE, REPORT])
    none_ = classify_failure([BASE, PAGE2_FAIL, REPORT])
    assert fake == "retry_wrong_params"
    assert none_ == "no_retry"
    assert fake != none_


# ---------------------------------------------------------------- 能力边界
def test_filter_drops_original_index():
    """时序距离约束（'失败后 N 次调用内必须成功'）表达不了：过滤后索引已丢失。"""
    calls = [BASE, PAGE2_FAIL, REPORT, REPORT, REPORT, PAGE2_OK]
    matched = jmespath.search(R3_STATUS_SEQ, {"calls": calls})
    assert matched == ["error", "success"]  # 距离 3 次和距离 0 次，结果一样 → 区分不了


def test_parameter_schema_cannot_be_validated():
    """参数四项校验（required/type/range/additionalProperties）表达不了。"""
    calls = [{"name": "fetch_orders", "params": {"offset": 99, "bogus": True}, "status": "error"}]
    keys = jmespath.search("calls[].params.keys(@)", {"calls": calls})
    assert keys == [["offset", "bogus"]]
    # 能列出键名，但 offset=99 越界、bogus 非法——JMESPath 不做 schema 判定
    assert jmespath.search(R3_STATUS_SEQ, {"calls": calls}) == []
