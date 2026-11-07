"""A2E 承载能力探针 —— 判定 A2E 能否承载 dbg-001 的 R1–R5 断言。

用途：M-1 第 4 步（4c-3）。不是基准测试，是**语义对齐测试**：
故意造一条"错误重试"轨迹（参数没变的假重试），检查 A2E 的
`self_correction_rate` 会不会把它判成"已修正"。

判据（预登记，跑完对照，不许事后改口径）：
  P1  A2E 能否存下 http_status / call_index / attempt —— 存在则 R3 有位置
  P2  self_correction_rate 是否区分"正确重试(offset=10 再试)"与"错误重试(退回 offset=0)"
      判"已修正"即 P2 失败 → 语义不对，不能承载 R3
  P3  tool_execution_error_rate 输出是聚合数字还是布尔断言

运行（必须用 server 的 venv，且不要走代理）：
  cd tools/A2E-main/server
  no_proxy=127.0.0.1,localhost ./.venv/Scripts/python.exe \
      ../../agent-arena/spike/a2e_probe.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

BASE = "http://127.0.0.1:6006"
PROJECT = "agent-arena-spike"
DATASET = "dbg-001-order-report"
EXPERIMENT = "dbg001-wrong-retry"

HERE = Path(__file__).resolve().parent
OUT = HERE / "a2e_probe_result.json"


def _req(method: str, path: str, body=None):
    url = f"{BASE}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {exc.code} on {method} {path}\n{raw[:2000]}") from None


def _post(path, body=None):
    return _req("POST", path, body)


def _get(path):
    return _req("GET", path)


def _rows(body):
    """A2E 列表端点统一包在 {"data": [...], "next_cursor": ...}，这里拆包。"""
    if isinstance(body, dict):
        return body.get("data") or []
    return body or []


def ensure_project() -> None:
    _, body = _get("/v1/projects")
    names = {p.get("name") for p in _rows(body)}
    if PROJECT in names:
        print(f"[project] 复用已存在的 {PROJECT}")
        return
    _post("/v1/projects", {"name": PROJECT, "description": "M-1 spike: A2E 承载能力探针"})
    print(f"[project] 新建 {PROJECT}")


def _find_by_name(list_path: str, name: str):
    _, body = _get(list_path)
    for item in _rows(body):
        if item.get("name") == name:
            return item
    return None


def ensure_dataset():
    """A2E 的 upload/create 端点不回传 id，统一按名字回查（幂等）。"""
    existing = _find_by_name("/v1/datasets", DATASET)
    if existing:
        print(f"[dataset] 复用已存在的 {DATASET} id={existing['id']}")
        return existing
    yaml_expected = {"valid_count": 17, "total_amount": 1837.99}
    _post(
        "/v1/datasets/upload",
        {
            "action": "create",
            "name": DATASET,
            "description": "任务定义见 tasks/dbg-001-order-report.yaml",
            "inputs": [
                {
                    "task_id": "dbg-001-order-report",
                    "scenario": "分页拉取 20 条订单（limit<=10），剔除无效订单，统计有效数与总额，写 /workspace/report.json",
                }
            ],
            "outputs": [yaml_expected],
            "metadata": [{"primary_skill_id": "multi_step_planning"}],
        },
    )
    created = _find_by_name("/v1/datasets", DATASET)
    print(f"[dataset] 新建 {DATASET} id={(created or {}).get('id')}")
    return created


def ensure_experiment(dataset_id):
    """A2E 建 experiment 时会自动分配一个 project_name（Experiment-<hash>），spans 必须写进那个 project。"""
    _, body = _get(f"/v1/datasets/{dataset_id}/experiments")
    rows = _rows(body)
    for item in rows:
        if (item.get("metadata") or {}).get("purpose") == EXPERIMENT:
            print(f"[experiment] 复用 {EXPERIMENT} id={item['id']} project={item.get('project_name')}")
            return item
    if rows:
        # 已存在但 metadata 没打标（早期手建），直接复用第一条，避免每跑一次新建一个
        item = rows[0]
        print(f"[experiment] 复用第一条已存在 experiment id={item['id']} project={item.get('project_name')}")
        return item
    _post(
        f"/v1/datasets/{dataset_id}/experiments",
        {"name": EXPERIMENT, "metadata": {"purpose": EXPERIMENT}},
    )
    _, body = _get(f"/v1/datasets/{dataset_id}/experiments")
    created = _rows(body)[-1] if _rows(body) else None
    print(f"[experiment] 新建 {EXPERIMENT} id={(created or {}).get('id')} project={(created or {}).get('project_name')}")
    return created


def build_spans(trace_id: bytes, base_time: datetime):
    """构造 4 次工具调用。

    关键设计：第 2 次（offset=10）失败，第 3 次**回到 offset=0** —— 这是错误的
    重试（没有向前推进游标），如果 A2E 只看"同名工具的后续成功调用"就会误判已修正。
    """
    spans = []

    def add(idx, name, arguments, output, *, status_code="OK", start_offset=0):
        span_id = uuid4().hex[:16].encode()
        start = base_time + timedelta(seconds=start_offset)
        spans.append(
            {
                "name": name,
                "context": {"trace_id": trace_id.hex(), "span_id": span_id.hex()},
                "span_kind": "TOOL",
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(milliseconds=300)).isoformat(),
                "status_code": status_code,
                "attributes": {
                    "tool.name": name,
                    "tool.arguments": json.dumps(arguments, ensure_ascii=False),
                    "output.value": json.dumps(output, ensure_ascii=False),
                    "call_index": idx,
                },
            }
        )

    add(1, "fetch_orders", {"offset": 0, "limit": 10}, {"orders": [{"order_id": "o-001", "amount": 120.5}], "total": 20}, start_offset=0)
    add(
        2,
        "fetch_orders",
        {"offset": 10, "limit": 10},
        {"error": "internal_error", "http_status": 500},
        status_code="ERROR",
        start_offset=1,
    )
    # ← 错误重试：参数退回 offset=0，游标没推进
    add(3, "fetch_orders", {"offset": 0, "limit": 10}, {"orders": [{"order_id": "o-001", "amount": 120.5}], "total": 20}, start_offset=2)
    add(4, "write_report", {"path": "/workspace/report.json", "content": "{\"valid_count\": 10, \"total_amount\": 900.0}"}, {"ok": True}, start_offset=3)
    return spans


def main() -> int:
    dataset = ensure_dataset() or {}
    dataset_id = dataset.get("id")
    if not dataset_id:
        raise SystemExit("dataset id 缺失，无法继续")

    _, examples = _get(f"/v1/datasets/{dataset_id}/examples")
    # 这个端点结构与列表端点不同：{"data": {"dataset_id":..., "examples": [...]}}
    payload = examples.get("data") if isinstance(examples, dict) else examples
    rows = (payload or {}).get("examples") if isinstance(payload, dict) else payload
    first = (rows or [{}])[0]
    example_id = first.get("id")
    print(f"[dataset] example id={example_id}")

    experiment = ensure_experiment(dataset_id) or {}
    experiment_id = experiment.get("id")
    # A2E 的 experiment 自带 project；spans 必须写进这个 project，否则 eval 取不到
    span_project = experiment.get("project_name") or PROJECT
    print(f"[experiment] id={experiment_id} span_project={span_project}")

    trace_id = uuid4().bytes
    base_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    spans = build_spans(trace_id, base_time)
    status, _ = _post(f"/v1/projects/{span_project}/spans", {"data": spans})
    print(f"[spans] POST -> {status}, 写入 {len(spans)} 条")

    _, fetched = _get(f"/v1/projects/{span_project}/spans?trace_id={trace_id.hex()}&limit=100")
    stored = _rows(fetched)
    print(f"[spans] 回读 {len(stored or [])} 条")
    if stored:
        attrs = (stored[0] or {}).get("attributes") or {}
        print(f"[P1] 回读第一条 span 的属性键: {sorted(attrs)}")

    run_body = {
        "dataset_example_id": example_id,
        "output": {"final_status": "completed", "note": "错误重试轨迹（游标未推进）"},
        "repetition_number": 1,
        "start_time": base_time.isoformat(),
        "end_time": (base_time + timedelta(seconds=10)).isoformat(),
        "trace_id": trace_id.hex(),
    }
    status, run = _post(f"/v1/experiments/{experiment_id}/runs", run_body)
    print(f"[run] POST -> {status}, run id={(run or {}).get('id')}")

    result = {
        "dataset_id": dataset_id,
        "example_id": example_id,
        "experiment_id": experiment_id,
        "span_project": span_project,
        "trace_id": trace_id.hex(),
        "span_count_readback": len(stored or []),
        "stored_attribute_keys": sorted(((stored or [{}])[0] or {}).get("attributes") or {}),
    }
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n写入 {OUT}")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
