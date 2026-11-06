# 任务 YAML 结构校验（M-1 第 3 步的验证沉淀，协作规范 3.4：验证写成 pytest 留档）
# 这些用例同时是后续变体模板生成器的约束来源
import math
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "tasks"
TAXONOMY = ROOT / "docs" / "skill-taxonomy-v0.yaml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _task() -> dict:
    return _load(TASKS / "dbg-001-order-report.yaml")


def test_task_yaml_parses():
    task = _task()
    assert task["task_id"] == "dbg-001-order-report"
    assert task["title"]


def test_rubric_atomic_and_unique():
    rubric = _task()["rubric"]
    assert 3 <= len(rubric) <= 5, "原子 rubric 必须在 3-5 条（协作规范 M-1 第 3 步）"
    ids = [r["id"] for r in rubric]
    assert len(ids) == len(set(ids)), "断言 id 必须唯一"
    for r in rubric:
        assert {"id", "type", "target"} <= set(r), f"{r['id']} 缺必填字段"
        # file_exists 只查存在性无期望值；其余类型必须写明 expected
        needs_expected = r["type"] != "file_exists"
        assert ("expected" in r) == needs_expected, f"{r['id']} 的 expected 字段与类型不匹配"
        assert r["id"].startswith("R") and "_" in r["id"], "id 命名：R<序号>_<内容>"


def test_skill_refs_exist_in_taxonomy():
    skills = {s["skill_id"] for s in _load(TAXONOMY)["skills"]}
    task = _task()
    assert task["primary_skill_id"] in skills, "主技能必须在分类学 v0 内"
    assert set(task["secondary_skill_ids"]) <= skills, "次技能必须在分类学 v0 内"


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


def test_fault_injection_and_trace_requirements():
    task = _task()
    assert task["fault_injection"]["enabled"] is True
    assert task["fault_injection"]["target_tool"] == "fetch_orders"
    minimum = task["trace_requirements"]["minimum"]
    assert "tool_calls" in minimum and "final_status" in minimum, "最小 trace 缺关键字段"


def test_tool_params_defined_for_all_tools():
    task = _task()
    tool_names = {t["name"] for t in task["tools"]}
    assert tool_names == {"fetch_orders", "write_report"}
    for t in task["tools"]:
        assert t["parameters"], f"工具 {t['name']} 无参数定义（R4 断言依赖 schema）"
