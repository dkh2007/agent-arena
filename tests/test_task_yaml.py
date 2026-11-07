# 任务 YAML 结构校验（M-1 第 3 步验证沉淀；2026-11-06 按外部评审 P0/P1 修订同步更新）
# 这些用例同时是后续变体模板生成器与断言执行器的约束来源
import math
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "tasks"
TAXONOMY = ROOT / "docs" / "skill-taxonomy-v0.yaml"
PATTERNS = ROOT / "docs" / "error-patterns-v0.yaml"
TASK_FILE = TASKS / "dbg-001-order-report.yaml"

RESULT_ONLY = {"R1_file_exists", "R2_report_correct"}  # 不依赖 trace 的结果断言
STATES = {"pass", "fail", "blocked", "unscored"}      # 断言四态（第 6 轮定义）


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

# ============================================================================
# 第 6 轮评审（断言级归因 + 依赖折叠）落地后的约束
# ============================================================================


def _patterns() -> dict:
    return {p["pattern_id"]: p for p in _load(PATTERNS)["patterns"]}


def test_stale_zero_score_comment_is_gone():
    """回归锁：第 6 轮评审第 5 节指出的旧注释（不上报 trace 判 0 且无诊断报告）不得重现。

    这条用例故意检查原文：注释错误无法被结构断言发现，只能按字面锁。
    它锁的是"已删除的旧结论不要被贴回来"，与分数无关（分数口径见 test_scoring_block_*）。
    """
    text = TASK_FILE.read_text(encoding="utf-8")
    assert "判 0 且无诊断报告" not in text
    assert "unscored 且不计入掌握度" in text


def test_scoring_score_uses_fixed_denominator():
    """依赖缺失时不得缩小分母，否则"缺产物"反而分数虚高（A2E 满分教训的同构问题）。"""
    score = _task()["scoring"]["score"]
    assert score["denominator"] == "fixed_at_total_weight"
    assert score["total_weight"] == sum(r["weight"] for r in _task()["rubric"]) == 6
    contrib = score["contribution"]
    assert set(contrib) == STATES
    assert contrib["pass"] == "weight"           # 通过计满权重
    for s in ("fail", "blocked", "unscored"):
        assert contrib[s] == 0, f"{s} 对分数贡献必须为 0"


def test_unscored_and_blocked_do_not_touch_mastery():
    """核心口径：数据缺失 ≠ 能力不足。blocked / unscored 一律不更新掌握度、不产出错误模式。"""
    attr = _task()["scoring"]["attribution"]
    assert set(attr) == STATES
    assert attr["pass"]["counts_toward_mastery"] is True
    assert attr["fail"]["counts_toward_mastery"] is True
    assert attr["fail"]["emit_error_patterns"] is True
    for s in ("blocked", "unscored"):
        assert attr[s]["counts_toward_mastery"] is False, f"{s} 不得计入掌握度"
        assert attr[s]["emit_error_patterns"] is False, f"{s} 不得产出错误模式"
        assert attr[s]["reason"], f"{s} 必须在 YAML 里写明理由（否则执行器会猜）"


def test_every_assertion_declares_skill_id():
    """断言级技能绑定：缺 skill_id 键即失败；为 null 表示"只计分不归因"，须显式写出来。"""
    skills = {s["skill_id"] for s in _load(TAXONOMY)["skills"]}
    patterns = _patterns()
    for r in _task()["rubric"]:
        assert "skill_id" in r, f"{r['id']} 未声明 skill_id（无归属也要写 null）"
        sid = r["skill_id"]
        if sid is not None:
            assert sid in skills, f"{r['id']}.skill_id={sid} 不在 skill-taxonomy 中"
        else:
            # 只计分不归因的断言，其错误模式也不能映射到技能，否则归因口径自相矛盾
            for code in r["error_patterns"]:
                assert patterns[code]["mapped_skill_ids"] == [], \
                    f"{r['id']} 声明为只计分不归因，但 {code} 映射了技能"


def test_error_patterns_registered_in_vocabulary():
    """词表先行（data-model.md §ErrorPattern）：rubric 只能引用已登记 pattern_id。"""
    patterns = _patterns()
    skills = {s["skill_id"] for s in _load(TAXONOMY)["skills"]}
    for r in _task()["rubric"]:
        assert r["error_patterns"], f"{r['id']} 未声明任何错误模式"
        assert len(set(r["error_patterns"])) == len(r["error_patterns"])
        for code in r["error_patterns"]:
            assert code in patterns, f"{code} 未登记进 docs/error-patterns-v0.yaml"
        # 检出规则必须回指本断言，防止模式与断言错配
        for code in r["error_patterns"]:
            rule = patterns[code]["detection_rule"]
            aid = rule["assertion_id"]
            assert aid == r["id"], f"{code} 的 detection_rule 指向 {aid}，与所在断言 {r['id']} 不一致"
    for pid, p in patterns.items():
        assert p["label"] and p["description"], f"{pid} 缺 label/description"
        assert set(p["mapped_skill_ids"]) <= skills, f"{pid} 映射了不存在的技能"


def test_dependencies_declared_and_acyclic():
    """依赖折叠：R2 依赖 R1（文件不存在 → R2 blocked），且 depends_on 必须可解析、无环。"""
    rubric = _rubric_by_id()
    for rid, r in rubric.items():
        assert "depends_on" in r, f"{rid} 未声明 depends_on（无依赖也要写 []）"
        for dep in r["depends_on"]:
            assert dep in rubric, f"{rid} 依赖不存在的断言 {dep}"
            assert dep != rid, f"{rid} 自依赖"

    def _walk(node, seen):
        assert node not in seen, f"depends_on 存在环：{seen + [node]}"
        for dep in rubric[node]["depends_on"]:
            _walk(dep, seen + [node])

    for rid in rubric:
        _walk(rid, [])

    assert rubric["R2_report_correct"]["depends_on"] == ["R1_file_exists"]
    assert rubric["R1_file_exists"]["depends_on"] == []


def test_execution_boundaries_declared():
    """第 3 轮 S1 遗留：调用超时与平台重试必须写进 YAML，否则超时算不算一次提交无处可查。"""
    ex = _task()["execution"]
    assert ex["invoke_timeout_seconds"] == 60
    assert ex["platform_retry"] == 0, "平台不得代劳重试，否则 R3（失败重试策略）测不到"
    assert ex["on_timeout"]["final_status"] == "timeout"


def test_fault_injection_resets_per_submission():
    """第 3 轮评审 ①-2 的真实缺口：mock 状态必须在两次提交之间重置。"""
    fi = _task()["fault_injection"]
    assert fi["reset_policy"] == "per_submission"
