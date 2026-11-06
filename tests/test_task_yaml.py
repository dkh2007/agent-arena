# 任务 YAML 结构校验（M-1 第 3 步验证沉淀；2026-11-06 按外部评审 P0/P1 修订同步更新）
# 这些用例同时是后续变体模板生成器与断言执行器的约束来源
import math
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "tasks"
TAXONOMY = ROOT / "docs" / "skill-taxonomy-v0.yaml"

RESULT_ONLY = {"R1_file_exists", "R2_report_correct"}  # 不依赖 trace 的结果断言


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _task() -> dict:
    return _load(TASKS / "dbg-001-order-report.yaml")


def _rubric_by_id() -> dict:
    return {r["id"]: r for r in _task()["rubric"]}


def test_task_yaml_parses():
    task = _task()
    assert task["task_id"] == "dbg-001-order-report"
    assert task["title"]


def test_rubric_atomic_and_unique():
    rubric = _task()["rubric"]
    assert 3 <= len(rubric) <= 5
    ids = [r["id"] for r in rubric]
    assert len(ids) == len(set(ids))
    for r in rubric:
        assert {"id", "type", "target"} <= set(r)
        if r["type"] == "file_exists":
            assert "options" in r, f"{r['id']} 存在性断言应带 options（non_empty）"
        else:
            # 结构化 expected：dict，或单值枚举标量（无空格）；禁止自然语言句子（评审第 1 条，P0）
            exp = r["expected"]
            assert isinstance(exp, dict) or (isinstance(exp, str) and " " not in exp), \
                f"{r['id']} 的 expected 必须结构化或枚举标量，不得为自然语言"
        assert r["id"].startswith("R") and "_" in r["id"]


def test_rubric_dependencies_documented():
    """轨迹断言依赖 trace；结果断言不依赖。依赖折叠语义必须写在注释可达的字段里。"""
    rubric = _rubric_by_id()
    assert RESULT_ONLY <= set(rubric), "R1/R2 必须为结果断言"
    for rid, r in rubric.items():
        if rid not in RESULT_ONLY:
            assert r["type"].startswith("trace_"), f"{rid} 应为轨迹类断言"


def test_r1_requires_non_empty():
    assert _rubric_by_id()["R1_file_exists"]["options"]["non_empty"] is True


def test_r2_float_tolerance_and_subset():
    r2 = _rubric_by_id()["R2_report_correct"]
    assert r2["compare"] == "subset"
    assert r2["tolerance"]["total_amount"] == 0.01
    assert r2["expected"]["valid_count"] == 17


def test_r3_structured_no_natural_language():
    r3 = _rubric_by_id()["R3_retry_evidence"]
    calls = r3["expected"]["calls"]
    assert r3["expected"]["ordered"] is True
    assert len(calls) == 2
    assert calls[0]["status"] == "error" and calls[0]["http_status"] == 500
    assert calls[1]["status"] == "success"
    # 修正点：params_match 子集匹配，不限定 limit（合法分页方式不误伤）
    assert calls[0]["params_match"] == {"offset": 10}
    assert "limit" not in calls[0]["params_match"]
    # P1 第 2 条：允许穿插其他调用，匹配器按子序列判定
    assert r3["expected"]["allow_interleaved"] is True


def test_r4_mode_and_schema_ref():
    r4 = _rubric_by_id()["R4_no_param_hallucination"]
    assert r4["expected"]["schema_ref"] == "tools"
    # 第 5 轮 P2-1：check_schema 语义覆盖 required/type/range/additional_properties，
    # 旧名 check_types 不得残留（漏传必填参数必须可判）
    assert r4["expected"]["check_schema"] is True
    assert "check_types" not in r4["expected"]
    # P1 第 1 条：判定模式必须显式——任一次违规即失败（参数幻觉的严格语义）
    assert r4["expected"]["mode"] == "all_calls_must_be_valid"


def test_scenario_states_limit_error():
    """P1 第 3 条：scenario 必须明说 limit>10 会报错，不能诱导 Agent 合理试探后踩 R4。"""
    scenario = _task()["scenario"]
    assert "invalid_limit" in scenario
    assert "1 到 10" in scenario


def test_retry_scope_is_agent():
    """P0 第 2 条：重试主体必须是 Agent，mock 不得内部自动重试，否则 R3 测不到。"""
    fi = _task()["fault_injection"]
    assert fi["retry_scope"] == "agent"
    assert fi["match"] == {"offset": 10}
    assert fi["failures"][0]["http_status"] == 500
    assert fi["then"] == "success"


def test_trace_schema_supports_failure_evidence():
    """P0 第 3 条：trace 必须能表达失败/重试证据；attempt 可推导故不要求上报。"""
    tc = _task()["trace_requirements"]["minimum"]["tool_calls"]
    for f in ["call_index", "name", "params", "status", "http_status", "error"]:
        assert f in tc["item_fields"], f"trace tool_calls 缺 {f}"
    assert set(tc["status_enum"]) == {"success", "error", "timeout"}
    assert "attempt" not in tc["item_fields"]


def test_mock_fixture_and_pagination_semantics():
    mock = _task()["tools"][0]["mock"]
    assert mock["source"] == "fixture_ref"
    assert mock["fixture"] == "test_inputs.orders_fixture"
    # P1 第 4 条：分页语义已结构化，执行器可直接驱动
    pg = mock["pagination"]
    assert pg["page_size_max"] == 10
    assert pg["offset_base"] == 0
    assert pg["range"] == "half_open"
    assert pg["on_limit_exceed"] == "error"
    assert pg["error_code"] == "invalid_limit"
    assert pg["on_offset_out_of_range"] == "empty_list"
    assert pg["response_shape"] == {"orders": []}
    assert pg["total_source"] == "fixture_length"
    # 第 5 轮 P2-2：total 单一真相源 = fixture 条数，任何硬编码 total 都不得残留
    assert "total" not in mock
    assert "total" not in pg["response_shape"]


def test_tool_schema_bounds_for_r4():
    fetch = _task()["tools"][0]
    assert fetch["additional_properties"] is False
    params = {p["name"]: p for p in fetch["parameters"]}
    assert params["limit"]["maximum"] == 10
    assert params["offset"]["minimum"] == 0


def test_skill_refs_exist_in_taxonomy():
    skills = {s["skill_id"] for s in _load(TAXONOMY)["skills"]}
    task = _task()
    assert task["primary_skill_id"] in skills
    assert set(task["secondary_skill_ids"]) <= skills


def test_expected_values_match_fixture():
    task = _task()
    orders = task["test_inputs"]["orders_fixture"]
    valid = [
        o["amount"]
        for o in orders
        if "amount" in o and o["amount"] is not None and o["amount"] > 0
    ]
    expected = task["test_inputs"]["expected"]
    assert len(orders) == 20
    assert len(valid) == expected["valid_count"] == 17
    assert math.isclose(sum(valid), expected["total_amount"], abs_tol=1e-9)
