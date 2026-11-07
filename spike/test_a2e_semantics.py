"""上游行为哨兵测试 —— A2E 的 self_correction_rate 能否区分"真重试"与"假重试"。

这不是产品测试，是 spike 证据（M-1 第 4 步 4c-3）。
它断言的是**上游的缺陷**：断言通过 = 缺陷仍存在。若哪天 A2E 修好了，本测试会变红，
提醒我们重新评估"能否直接用 A2E 承载 R3"。

背景：dbg-001 的 R3 要求
    fetch_orders(offset=10) -> error(500) 之后，同参数 fetch_orders(offset=10) -> success
即"重试要落在同一页"。锁 offset 是这个断言的全部价值所在。

A2E 的 self_correction_rate 实现（eval/process_values/tool_eval.py:936）只看
「同名工具 + 后续有非错误结果」，不看参数，因此下面的"假重试"会被判成已修正。

运行：
    cd tools/A2E-main/server
    no_proxy=127.0.0.1,localhost ./.venv/Scripts/python.exe -m pytest \
        ../../../agent-arena/spike/test_a2e_semantics.py -v

找不到 A2E 源码时整体 skip（用 A2E_EVAL_ROOT 指向 eval 目录）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "tools" / "A2E-main" / "eval"
EVAL_ROOT = Path(os.environ.get("A2E_EVAL_ROOT", DEFAULT_ROOT))

pytestmark = pytest.mark.skipif(
    not (EVAL_ROOT / "process_values" / "tool_eval.py").exists(),
    reason=f"A2E eval 源码不存在（找的是 {EVAL_ROOT}），设 A2E_EVAL_ROOT 指定",
)


@pytest.fixture(scope="module")
def self_correction_rate():
    sys.path.insert(0, str(EVAL_ROOT))
    from process_values.tool_eval import make_self_correction_rate

    return make_self_correction_rate({})


class _Example:
    id = "dbg-001-example"


WRONG_RETRY = [
    {"name": "fetch_orders", "arguments": {"offset": 0, "limit": 10}, "result": {"orders": [], "total": 20}},
    {"name": "fetch_orders", "arguments": {"offset": 10, "limit": 10}, "result": {"error": "internal_error", "http_status": 500}},
    # 假重试：游标退回第一页，请求重复
    {"name": "fetch_orders", "arguments": {"offset": 0, "limit": 10}, "result": {"orders": [], "total": 20}},
    {"name": "write_report", "arguments": {"path": "/workspace/report.json"}, "result": {"ok": True}},
]

CORRECT_RETRY = [
    {"name": "fetch_orders", "arguments": {"offset": 0, "limit": 10}, "result": {"orders": [], "total": 20}},
    {"name": "fetch_orders", "arguments": {"offset": 10, "limit": 10}, "result": {"error": "internal_error", "http_status": 500}},
    # 真重试：同一页再来一次
    {"name": "fetch_orders", "arguments": {"offset": 10, "limit": 10}, "result": {"orders": [], "total": 20}},
]


def _score(ev, calls):
    return ev({"tool_calls_full": calls}, {}, {}, example=_Example())


def test_wrong_retry_is_scored_as_corrected(self_correction_rate):
    """假重试被判成已修正 —— R3 的核心判别点在这里丢失。"""
    result = _score(self_correction_rate, WRONG_RETRY)
    assert result["label"] == "corrected"
    assert result["score"] == 1.0


def test_wrong_and_correct_retry_are_indistinguishable(self_correction_rate):
    """R3 要分的两类轨迹，在 A2E 这个指标下完全同分 —— 不能作为 R3 的判定器。"""
    wrong = _score(self_correction_rate, WRONG_RETRY)
    correct = _score(self_correction_rate, CORRECT_RETRY)
    assert (wrong["label"], wrong["score"]) == (correct["label"], correct["score"])


def test_missing_trace_yields_full_score_not_unscored(self_correction_rate):
    """没有工具调用时给 1.0 满分而非 unscored —— 漏报 trace 会被奖励。

    （实跑 httpx 时确认过：span 读不到就走这条分支，label=no_tool_errors, score=1.0）
    """
    result = _score(self_correction_rate, [])
    assert result["score"] == 1.0
    assert result["label"] == "no_tool_errors"
