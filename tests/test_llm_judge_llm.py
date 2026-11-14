"""喂给真模型的材料 + LLMJudge 的约束测试（round-79）。**不联网、不花钱**。

锁的边界：
  ① 材料里**不得出现标准答案**（`expected` / `assertion_results`）——出现就等于让模型抄，
     那样的「双评一致」证明不了任何事；
  ② 模式清单不把 `detection_rule` 交给模型——给了它会照着规则模拟，一致性测出来的是
     「同一套规则实现两遍」；
  ③ 模型越界（档位 5、清单外的 pattern_id）必须报错，不许夹成合法值；
  ④ 两次评价必须各打一次接口，不许缓存——否则 `judge_agreement` 恒 1.0，指标作废。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import llm_judge as lj  # noqa: E402

FIXTURE = json.loads((ROOT / "fixtures" / "raw-fake-retry.json").read_text(encoding="utf-8"))
TASK = {
    "task_id": "dbg-001-order-report",
    "title": "订单数据核对报告（调试向）",
    "scenario": "使用 fetch_orders 分页拉取全部订单…",
    # 真实任务 YAML 里这个字段带着标准答案，必须被白名单挡住
    "test_inputs": {"expected": {"valid_count": 17, "total_amount": 1837.99}},
    "rubric_credit": [{"id": "R2_report_correct", "expect": {"valid_count": 17}}],
}

CATALOG = [
    {"pattern_id": "no_retry", "label": "无重试", "description": "失败后未重试"},
    {"pattern_id": "retry_wrong_params", "label": "错误参数重试", "description": "重试但参数没修正"},
]


class FakeClient:
    """假 transport 的替身：直接实现 complete()，把每次请求记下来。"""

    class _Cfg:
        model = "judge-model"

    def __init__(self, replies, *, latency_ms=120, model="judge-model"):
        self.config = self._Cfg()
        self.replies = list(replies)
        self.requests: list[dict] = []
        self._latency = latency_ms
        self._model = model

    def __repr__(self) -> str:
        return "FakeClient()"

    def complete(self, messages, *, max_tokens=1024, temperature=0.0, retries=2, response_format_json=False):
        from arena.llm_client import Completion

        self.requests.append(
            {
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "response_format_json": response_format_json,
            }
        )
        if not self.replies:
            raise AssertionError("FakeClient 回复用尽：调用次数超出预期")
        reply = self.replies.pop(0)
        return Completion(
            text=reply,
            model=self._model,
            finish_reason="stop",
            prompt_tokens=100,
            completion_tokens=20,
            reasoning_tokens=0,
            latency_ms=self._latency,
        )


# ---------------------------------------------------------------------------
# ① 材料里不得有标准答案
# ---------------------------------------------------------------------------


def test_context_never_contains_expected_values():
    """最重要的那条：把整个上下文序列化成字符串，逐个查标准答案的痕迹。"""
    ctx = lj.build_judge_context(TASK, FIXTURE)
    blob = json.dumps(ctx, ensure_ascii=False, sort_keys=True)
    assert "expected" not in blob
    assert "assertion_results" not in blob
    assert "R2_report_correct" not in blob, "rubric id 也不该给"
    assert "1837.99" not in blob, "标准答案的数值一个都不许露"
    # 任务说明书里本来就有这些词的（如 limit 上限），那是考生也能看到的，允许


def test_context_carries_what_the_agent_saw():
    ctx = lj.build_judge_context(TASK, FIXTURE)
    assert ctx["task_id"] == "dbg-001-order-report"
    assert ctx["task_instructions"].startswith("使用 fetch_orders")
    assert "/workspace/report.json" in ctx["answer"]
    assert ctx["final_status"] == "completed"
    assert ctx["call_count"] == 4
    assert len(ctx["process"]) == 4
    assert ctx["process"][1]["status"] == "error"
    assert ctx["process"][1]["http_status"] == 500


def test_context_keeps_the_error_signal():
    """失败与重试的痕迹必须留着——那是 judge 判断「会不会重试」的唯一依据。"""
    ctx = lj.build_judge_context(TASK, FIXTURE)
    statuses = [c["status"] for c in ctx["process"]]
    assert statuses == ["success", "error", "success", "success"]
    assert ctx["process"][1]["error"] == "internal_error"


def test_context_truncates_long_traces():
    long_raw = json.loads(json.dumps(FIXTURE))
    base = long_raw["trace"]["tool_calls"][0]
    long_raw["trace"]["tool_calls"] = [dict(base, call_index=str(i)) for i in range(40)]
    ctx = lj.build_judge_context(TASK, long_raw, max_calls=10)
    assert len(ctx["process"]) == 10
    assert ctx["call_count"] == 40
    assert ctx["process_truncated"] is True, "被截断必须显式标出来，不能让模型以为只有 10 次"


def test_context_clips_long_result_summary():
    raw = json.loads(json.dumps(FIXTURE))
    raw["trace"]["tool_calls"][0]["result_summary"] = "x" * 5000
    ctx = lj.build_judge_context(TASK, raw)
    assert len(ctx["process"][0]["result"]) < 300
    assert "截断" in ctx["process"][0]["result"]


# ---------------------------------------------------------------------------
# ② 清单不含检测规则
# ---------------------------------------------------------------------------


def test_catalog_omits_detection_rule():
    """给了 `detection_rule`，模型就变成规则的第二个实现，不是独立观察者。"""
    catalog = lj.load_pattern_catalog()
    assert len(catalog) >= 10
    for item in catalog:
        assert set(item) == {"pattern_id", "label", "description"}


def test_system_prompt_lists_every_pattern_id():
    judge = lj.LLMJudge(FakeClient([]), lj.load_pattern_catalog())
    for item in judge.catalog:
        assert item["pattern_id"] in judge.system_prompt
    assert "detection_rule" not in judge.system_prompt


def test_system_prompt_forbids_inventing_patterns():
    judge = lj.LLMJudge(FakeClient([]), CATALOG)
    assert "清单之外" in judge.system_prompt


# ---------------------------------------------------------------------------
# ③ 越界必须报错
# ---------------------------------------------------------------------------


def test_judge_rejects_pattern_outside_catalog():
    client = FakeClient(['{"rating": 2, "suspected_patterns": ["made_up"], "rationale": "x"}'])
    judge = lj.LLMJudge(client, CATALOG)
    with pytest.raises(lj.JudgeError, match="清单外"):
        judge(lj.build_judge_context(TASK, FIXTURE))


def test_judge_rejects_out_of_range_rating():
    client = FakeClient(['{"rating": 5, "suspected_patterns": [], "rationale": "x"}'])
    judge = lj.LLMJudge(client, CATALOG)
    with pytest.raises(lj.JudgeError, match="越界"):
        judge(lj.build_judge_context(TASK, FIXTURE))


def test_judge_rejects_malformed_json():
    client = FakeClient(["这次提交还不错，给三分。"])
    judge = lj.LLMJudge(client, CATALOG)
    with pytest.raises(lj.JudgeError, match="不是合法 JSON"):
        judge(lj.build_judge_context(TASK, FIXTURE))


# ---------------------------------------------------------------------------
# 正常路径 + 不缓存
# ---------------------------------------------------------------------------


def test_judge_happy_path_records_model_and_latency():
    client = FakeClient(['{"rating": 3, "suspected_patterns": ["retry_wrong_params"], "rationale": "重试了但参数没改"}'])
    judge = lj.LLMJudge(client, CATALOG)
    verdict = judge(lj.build_judge_context(TASK, FIXTURE))
    assert verdict.rating == 3
    assert verdict.patterns == ("retry_wrong_params",)
    assert verdict.model == "judge-model"
    assert verdict.latency_ms == 120
    assert "重试了但参数没改" in verdict.rationale


def test_judge_passes_json_mode_and_temperature_zero():
    client = FakeClient(['{"rating": 4, "suspected_patterns": [], "rationale": "ok"}'])
    judge = lj.LLMJudge(client, CATALOG)
    judge(lj.build_judge_context(TASK, FIXTURE))
    req = client.requests[0]
    assert req["response_format_json"] is True, "要求模型输出 JSON 对象"
    assert req["temperature"] == 0.0, "双评测确定性，不是创意"


def test_double_judge_actually_calls_twice_without_cache():
    """两次必须各打一次接口。缓存会让 judge_agreement 恒为 1.0，指标直接作废。"""
    client = FakeClient(
        [
            '{"rating": 3, "suspected_patterns": [], "rationale": "a"}',
            '{"rating": 3, "suspected_patterns": [], "rationale": "b"}',
        ]
    )
    judge = lj.LLMJudge(client, CATALOG)
    ctx = lj.build_judge_context(TASK, FIXTURE)
    res = lj.double_judge(judge, ctx, vocab={"no_retry", "retry_wrong_params"})
    assert len(client.requests) == 2
    assert res.rating_agreement == 1.0
    assert [v.rationale for v in res.verdicts] == ["a", "b"], "两次结果必须各自独立"


def test_double_judge_detects_real_disagreement():
    client = FakeClient(
        [
            '{"rating": 4, "suspected_patterns": [], "rationale": "好"}',
            '{"rating": 2, "suspected_patterns": ["retry_wrong_params"], "rationale": "不行"}',
        ]
    )
    judge = lj.LLMJudge(client, CATALOG)
    res = lj.double_judge(judge, lj.build_judge_context(TASK, FIXTURE), vocab={"no_retry", "retry_wrong_params"})
    assert res.rating_agreement == 0.0
    assert res.rating_spread == 2
    assert res.needs_arbitration(0.5) is True


def test_judge_records_every_prompt_it_sent():
    """审计用：要能事后回看「当时到底把什么喂给了模型」。"""
    client = FakeClient(
        ['{"rating": 3, "suspected_patterns": [], "rationale": "a"}',
         '{"rating": 3, "suspected_patterns": [], "rationale": "b"}']
    )
    judge = lj.LLMJudge(client, CATALOG)
    lj.double_judge(judge, lj.build_judge_context(TASK, FIXTURE))
    assert len(judge.prompts) == 2
    assert "1837.99" not in judge.prompts[0], "prompt 里也不能带标准答案"
