"""B-03 mock 工具服务的约束测试。

锁的是任务 YAML 里**已经声明**的两件事真的会发生：
  ① `fault_injection`：该 (tool, match) 的第 N 次调用按声明失败，之后（then）放行；
  ② `tools[].mock.pagination`：页大小上限、越界返回空、`total` 由夹具长度推导。

外加一条最容易被忽略的：`reset_policy: per_submission`——不重置的话，
上一个学习者的提交会用掉故障额度，下一个本该看到 500 的一次成功，R3 就测不到重试。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import mock_tools as mt  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"


@pytest.fixture(scope="module")
def task() -> dict:
    return dr.load_task(TASK_PATH)


@pytest.fixture()
def svc(task) -> mt.MockToolService:
    return mt.MockToolService(task)


# ── 故障注入 ────────────────────────────────────────────────────────────────

def test_fault_point_fails_once_then_succeeds(svc):
    """声明：offset=10 的第 1 次调用 500 internal_error，then=success。"""
    assert svc.call("fetch_orders", {"offset": 0, "limit": 10})[0] == 200, "非故障点不该失败"

    status, body = svc.call("fetch_orders", {"offset": 10, "limit": 10})
    assert status == 500 and body["error"] == "internal_error"

    status, body = svc.call("fetch_orders", {"offset": 10, "limit": 10})
    assert status == 200, "then=success：第二次必须放行"
    assert len(body["orders"]) == 10


def test_fault_budget_is_per_submission(svc):
    """`reset_policy: per_submission` 的执行者：reset 后额度恢复。"""
    assert svc.call("fetch_orders", {"offset": 10, "limit": 10})[0] == 500
    svc.reset()
    assert svc.call("fetch_orders", {"offset": 10, "limit": 10})[0] == 500, (
        "reset 后仍应失败——否则下一个学习者会白捡一次成功，R3 永远测不到重试"
    )


def test_fault_does_not_leak_to_other_offsets(svc):
    """match 限定了 offset=10：别的子页不该被牵连。"""
    assert svc.call("fetch_orders", {"offset": 20, "limit": 10})[0] == 200
    assert svc.call("fetch_orders", {"offset": 0, "limit": 10})[0] == 200


# ── 分页语义 ────────────────────────────────────────────────────────────────

def test_pagination_half_open_range_and_total_from_fixture(task, svc):
    """`range: half_open` + `total_source: fixture_length`：总数由夹具推导，不写死。"""
    expected_total = len(task["test_inputs"]["orders_fixture"])

    status, body = svc.call("fetch_orders", {"offset": 10, "limit": 10})
    assert status == 500, "先吃掉故障点"
    status, body = svc.call("fetch_orders", {"offset": 10, "limit": 10})
    assert status == 200
    assert body["total"] == expected_total
    assert body["orders"][0]["order_id"] == "o-011", "半开区间 [10, 20) 的第 1 条"


def test_offset_out_of_range_returns_empty_list(svc):
    status, body = svc.call("fetch_orders", {"offset": 20, "limit": 10})
    assert status == 200 and body["orders"] == []
    assert body["total"] == 20


def test_limit_over_page_size_uses_declared_error_code(svc):
    status, body = svc.call("fetch_orders", {"offset": 0, "limit": 11})
    assert status == 400 and body["error"] == "invalid_limit"


# ── 边界 ────────────────────────────────────────────────────────────────────

def test_unknown_tool_is_rejected(svc):
    status, body = svc.call("delete_everything", {})
    assert status == 400 and body["error"] == "unknown_tool"


def test_write_report_records_artifact(svc):
    status, body = svc.call("write_report", {"path": "/workspace/report.json", "content": "{}"})
    assert status == 200 and body["written"] == "/workspace/report.json"
    assert svc.written()["/workspace/report.json"] == "{}"


def test_fixture_rows_are_not_mutated_by_callers(svc):
    """返回的是深拷贝：调用方改返回值不能污染下一次提交的数据。"""
    status, body = svc.call("fetch_orders", {"offset": 0, "limit": 10})
    body["orders"][0]["amount"] = 999999
    status, again = svc.call("fetch_orders", {"offset": 0, "limit": 10})
    assert again["orders"][0]["amount"] != 999999


# ── 不发明语义 ──────────────────────────────────────────────────────────────

def test_missing_fixture_path_is_config_error(task):
    """引用了不存在的夹具路径 ⇒ 构造期报错，不静默当成空列表。"""
    broken = dict(task)
    broken["tools"] = [dict(task["tools"][0], mock=dict(task["tools"][0]["mock"],
                                                       fixture="test_inputs.nope"))]
    with pytest.raises(mt.MockConfigError, match="fixture 路径不存在"):
        mt.MockToolService(broken)


def test_unsupported_pagination_semantics_are_refused(task):
    """YAML 换成本模块没实现的分页语义（如 closed 区间）⇒ 报错，不假装支持。"""
    raw = dict(task["tools"][0])
    raw["mock"] = dict(raw["mock"], pagination=dict(raw["mock"]["pagination"],
                                                    range="closed"))
    broken = dict(task, tools=[raw])
    with pytest.raises(mt.MockConfigError, match="range"):
        mt.MockToolService(broken)


def test_missing_required_param_is_a_service_error(svc):
    """本模块自定义的边界（YAML 未声明）：缺必填 ⇒ 400 missing_param。"""
    status, body = svc.call("fetch_orders", {"offset": 0})
    assert status == 400 and body["error"] == "missing_param"


def test_extra_param_is_ignored_by_server(svc):
    """额外参数由服务端忽略，是否算幻觉交给 R4 从 trace 判——不在这层判分。"""
    status, body = svc.call("fetch_orders", {"offset": 0, "limit": 10, "debug": True})
    assert status == 200 and len(body["orders"]) == 10
