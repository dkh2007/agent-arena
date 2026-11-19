"""C 口径：扰动测试前先测稳定性；剧本模型只证链路不抖。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from arena import stability as st


def _calls(*pairs):
    return [{"name": n, "params": p, "status": 200, "body": {}} for n, p in pairs]


def test_identical_decisions_are_stable():
    fp = st.decision_fingerprint(_calls(("fetch_orders", {"offset": 0, "limit": 10})))
    v = st.judge_stability([fp] * 5)
    assert v.stable and v.runs == 5 and v.distinct_fingerprints == 1


def test_param_key_order_does_not_matter():
    """同一组参数换 key 顺序仍算同一决策——否则 JSON 生成顺序会造成假抖动。"""
    a = st.decision_fingerprint(_calls(("f", {"x": 1, "y": 2})))
    b = st.decision_fingerprint(_calls(("f", {"y": 2, "x": 1})))
    assert a == b


def test_different_decision_breaks_stability():
    """这就是要抓的"模型抖了"：同一输入两次给出不同决策。"""
    a = st.decision_fingerprint(_calls(("fetch_orders", {"offset": 0})))
    b = st.decision_fingerprint(_calls(("fetch_orders", {"offset": 10})))
    v = st.judge_stability([a, a, b])
    assert not v.stable and v.distinct_fingerprints == 2


def test_order_matters():
    """同样的两步调换次序 = 不同决策（故障额度按到达序计）。"""
    a = st.decision_fingerprint(_calls(("a", {}), ("b", {})))
    b = st.decision_fingerprint(_calls(("b", {}), ("a", {})))
    assert a != b


def test_status_and_body_are_excluded():
    """返回体/status 不属于模型决策，变化不该报抖动。"""
    a = [{"name": "f", "params": {}, "status": 200, "body": {"v": 1}}]
    b = [{"name": "f", "params": {}, "status": 500, "body": {"v": 2}}]
    assert st.decision_fingerprint(a) == st.decision_fingerprint(b)


def test_model_tag_separates_baselines():
    calls = _calls(("f", {}))
    assert st.decision_fingerprint(calls, model="m1") != st.decision_fingerprint(calls, model="m2")


def test_empty_runs_rejected():
    with pytest.raises(ValueError):
        st.judge_stability([])


def test_malformed_call_rejected_not_skipped():
    with pytest.raises(ValueError):
        st.decision_fingerprint([{"name": "f"}])
    with pytest.raises(ValueError):
        st.decision_fingerprint([{"name": "f", "params": [1, 2]}])


def test_scripted_model_is_by_construction_stable():
    """剧本模型同一串决策重跑 N 次必一致——这只证链路不抖，不证真模型稳定。"""
    scripted = [_calls(("a", {}), ("b", {}))] * 3
    fps = [st.decision_fingerprint(c, model="scripted") for c in scripted]
    v = st.judge_stability(fps)
    assert v.stable  # 且仅此而已
