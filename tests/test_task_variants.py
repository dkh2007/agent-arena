"""任务库变体（B-05）的约束测试。

任务库从 1 个长到 10 个，如果每个都写完整 YAML，重复配置会超过一半，而且
改一处口径要改十处、漏一处就漂移。所以变体用 `base_task` 继承母本，只写差异。

分三层锁，缺一层都不够：

**A. 继承真的发生了**——不是"看起来像变体"：文件里不能重新抄一遍骨架，
   拼错 id / 成环 / 找不到母本 / task_id 撞车都必须报错而不是静默兜底。
**B. 骨架真的没变**——10 个任务的断言 id / 类型 / 权重 / 技能绑定 / 依赖 / 计分口径
   必须与母本逐字段相同。允许不同的只有"情景变量"：测试数据、期望值、故障位、参数面。
   这一层防的是「某个变体悄悄把 R2 权重改成 1」这种会让分数不可比的漂移。
**C. 变体真的能跑**——每个任务起 mock 服务、跑参考 Agent、走完整评测链路。
   只跑通不算数：故障位被改过的变体，`no_retry` 形态必须被判成 `no_retry`
   而不是落到兜底的 `retry_wrong_params`（否则错误模式分类已经被改坏了，
   而分数看不出来）。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import mock_tools as mt  # noqa: E402
from arena import reference_agent as ra  # noqa: E402
from arena import trace_pipeline as tp  # noqa: E402

TASKS_DIR = ROOT / "tasks"
MOTHER = TASKS_DIR / "dbg-001-order-report.yaml"
TASK_FILES = sorted(TASKS_DIR.glob("*.yaml"))
GIT_HASH = "deadbee"

# 家族 → 主技能。放在测试里而不是"随手填"：10 个任务的主技能必须与家族一致，
# 否则 tasks 表里的 primary_skill_id 只是装饰。
FAMILY_PRIMARY = {
    "order-report": "multi_step_planning",
    "retry-drill": "failure_retry",
    "param-surface": "tool_param_validation",
}
# 骨架字段：变体之间必须逐项相同，只有这里没列的（expected / error_pattern_detection）才允许不同
SKELETON_FIELDS = ("type", "target", "weight", "skill_id", "depends_on",
                   "on_fail", "error_patterns", "detects")


@pytest.fixture(scope="module")
def all_tasks() -> dict[str, dict]:
    return {p.stem: dr.load_task(p) for p in TASK_FILES}


# ── A 层：继承机制 ──────────────────────────────────────────────────────────

def test_every_variant_declares_its_parent_and_does_not_restate_skeleton():
    """变体文件里不许出现 rubric_gate / scoring / trace_requirements。

    这三块是骨架。一旦某个变体把它们抄进来，"改母本 ⇒ 所有变体跟着变"就断了，
    而且两份口径从此各自演化——这种漂移在测试跑绿的时候完全看不出来。
    """
    variants = [p for p in TASK_FILES if p != MOTHER]
    assert variants, "任务库应当有变体"
    for path in variants:
        text = path.read_text(encoding="utf-8")
        head = dr.load_task(path)
        assert head.get("base_task") == "dbg-001-order-report", f"{path.name} 未声明母本"
        for block in ("rubric_gate:", "scoring:", "trace_requirements:", "submission_requirements:"):
            assert block not in text, f"{path.name} 抄了骨架里的 {block}，应改为继承"


def _write_variant(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _base_copy(tmp_path: Path) -> Path:
    """把母本复制到临时目录，让变体的 base_task 能解析到它。"""
    target = tmp_path / MOTHER.name
    target.write_text(MOTHER.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_typo_in_patch_id_is_loud(tmp_path):
    """补丁 id 拼错必须报错。否则它会静默变成"新增一条断言"，分数悄悄多一条。"""
    _base_copy(tmp_path)
    path = _write_variant(tmp_path, "bad", """\
base_task: dbg-001-order-report
task_id: bad-patch
variant_template_id: tpl-tool-debug
variant_meta: {family_id: order-report, difficulty: 1, kind: variant}
rubric_credit:
  - id: R2_report_corret
    expected: {valid_count: 1, total_amount: 1.0}
""")
    with pytest.raises(dr.ReportError, match="没有 id=R2_report_corret"):
        dr.load_task(path)


def test_missing_parent_is_loud(tmp_path):
    path = _write_variant(tmp_path, "orphan", """\
base_task: dbg-999-not-exist
task_id: orphan
""")
    with pytest.raises(dr.ReportError, match="找不到"):
        dr.load_task(path)


def test_inheritance_cycle_is_loud(tmp_path):
    _base_copy(tmp_path)
    _write_variant(tmp_path, "loop-a", """\
base_task: loop-b
task_id: loop-a
""")
    _write_variant(tmp_path, "loop-b", """\
base_task: loop-a
task_id: loop-b
""")
    with pytest.raises(dr.ReportError, match="成环"):
        dr.load_task(tmp_path / "loop-a.yaml")


def test_variant_may_not_reuse_parent_task_id(tmp_path):
    _base_copy(tmp_path)
    path = _write_variant(tmp_path, "same", """\
base_task: dbg-001-order-report
task_id: dbg-001-order-report
""")
    with pytest.raises(dr.ReportError, match="task_id 与母本相同"):
        dr.load_task(path)


def test_plain_lists_are_replaced_not_merged(tmp_path):
    """测试数据必须整体替换。按下标合并两份 fixture，期望值就再也说不清对不对了。"""
    _base_copy(tmp_path)
    path = _write_variant(tmp_path, "one-row", """\
base_task: dbg-001-order-report
task_id: one-row
test_inputs:
  orders_fixture:
    - {order_id: x-1, amount: 10.0}
""")
    task = dr.load_task(path)
    assert task["test_inputs"]["orders_fixture"] == [{"order_id": "x-1", "amount": 10.0}]
    assert task["test_inputs"]["expected"]["valid_count"] == 17, "没被覆盖的键应继承母本"


def test_loading_does_not_mutate_the_mother():
    """深合并必须深拷贝：读一次变体不能污染母本（否则同一进程里顺序不同结果不同）。"""
    before = MOTHER.read_text(encoding="utf-8")
    mother = dr.load_task(MOTHER)
    dr.load_task(TASKS_DIR / "dbg-002-invoice-report.yaml")
    after = dr.load_task(MOTHER)
    assert MOTHER.read_text(encoding="utf-8") == before
    assert mother == after
    assert mother["test_inputs"]["expected"]["valid_count"] == 17


# ── B 层：骨架不变性 ────────────────────────────────────────────────────────

def test_skeleton_is_identical_across_tasks(all_tasks):
    mother = all_tasks[MOTHER.stem]
    for layer in ("rubric_gate", "rubric_credit"):
        base = {r["id"]: r for r in mother[layer]}
        for stem, task in all_tasks.items():
            got = {r["id"]: r for r in task[layer]}
            assert list(got) == list(base), f"{stem} 的 {layer} 断言顺序/组成与母本不一致"
            for rid, want in base.items():
                for field in SKELETON_FIELDS:
                    if field not in want:
                        assert field not in got[rid], f"{stem}.{rid} 凭空多出 {field}"
                        continue
                    assert got[rid][field] == want[field], (
                        f"{stem}.{rid}.{field} = {got[rid][field]!r}，母本是 {want[field]!r}"
                    )


def test_scoring_and_execution_blocks_are_identical(all_tasks):
    mother = all_tasks[MOTHER.stem]
    for stem, task in all_tasks.items():
        for key in ("scoring", "execution", "submission_requirements", "trace_requirements"):
            assert task[key] == mother[key], f"{stem} 的 {key} 与母本不一致（计分口径必须全库统一）"


def test_family_maps_to_declared_primary_skill(all_tasks):
    seen = set()
    for stem, task in all_tasks.items():
        meta = task["variant_meta"]
        family = meta["family_id"]
        assert family in FAMILY_PRIMARY, f"{stem} 的 family_id={family} 未登记"
        assert task["primary_skill_id"] == FAMILY_PRIMARY[family], (
            f"{stem} 的主技能是 {task['primary_skill_id']}，但家族 {family} 声明的是 "
            f"{FAMILY_PRIMARY[family]}"
        )
        assert meta["kind"] == "variant"
        assert meta["difficulty"] in (1, 2, 3)
        assert task["variant_template_id"] == "tpl-tool-debug"
        seen.add(family)
    assert seen == set(FAMILY_PRIMARY), f"有家族一个任务都没有：{set(FAMILY_PRIMARY) - seen}"


def test_every_skill_is_primary_somewhere(all_tasks):
    """任务库要能覆盖三个技能，否则复习层只能反复推同一类题。"""
    tax = yaml.safe_load((ROOT / "docs" / "skill-taxonomy-v0.yaml").read_text(encoding="utf-8"))
    skills = {s["skill_id"] for s in tax["skills"]}
    primaries = [t["primary_skill_id"] for t in all_tasks.values()]
    for sid in skills:
        assert primaries.count(sid) >= 2, f"技能 {sid} 作为主技能只出现 {primaries.count(sid)} 次"
    assert len(all_tasks) == 10, f"任务库应为 10 个任务，实际 {len(all_tasks)}"


def test_difficulty_is_not_decorative(all_tasks):
    """难度不是随手填的数：它必须与「要翻几页」正相关（页数是最硬的成本旋钮）。"""
    levels = set()
    for stem, task in all_tasks.items():
        page = task["tools"][0]["mock"]["pagination"]["page_size_max"]
        rows = len(task["test_inputs"]["orders_fixture"])
        pages = -(-rows // page)
        diff = task["variant_meta"]["difficulty"]
        levels.add(diff)
        need = {1: 1, 2: 3, 3: 4}[diff]
        assert pages >= need, f"{stem} 标了难度 {diff}，但只需翻 {pages} 页（难度 {diff} 至少要 {need} 页）"
    assert levels == {1, 2, 3}, f"难度没有梯度：只有 {levels}"


# ── 一致性不变量：同一件事在两处出现时必须相等 ──────────────────────────────

def test_fault_declaration_matches_retry_expectation(all_tasks):
    """YAML 里故障位/故障码写了两处（fault_injection 与 R3.expected），必须相等。

    这是变体最容易出错的地方：改故障偏移却忘了改 R3 的期望序列，
    结果是"Agent 正确重试了，但 R3 判失败"——看起来像学习者的问题。
    """
    for stem, task in all_tasks.items():
        fi = task["fault_injection"]
        offset = fi["match"]["offset"]
        status = fi["failures"][0]["http_status"]
        calls = next(r for r in task["rubric_credit"] if r["id"] == "R3_retry_evidence")["expected"]["calls"]
        assert calls[0]["params_match"]["offset"] == offset, f"{stem}: R3 与 fault_injection 的偏移不一致"
        assert calls[0]["http_status"] == status, f"{stem}: R3 与 fault_injection 的故障码不一致"
        assert calls[0]["status"] == "error" and calls[1]["status"] == "success"
        assert calls[0]["params_match"] == calls[1]["params_match"]


def test_page_ceiling_and_report_expectations_are_self_consistent(all_tasks):
    for stem, task in all_tasks.items():
        tool = next(t for t in task["tools"] if t["name"] == "fetch_orders")
        page = tool["mock"]["pagination"]["page_size_max"]
        limit = next(p for p in tool["parameters"] if p["name"] == "limit")["maximum"]
        assert page == limit, f"{stem}: 分页上限 {page} 与 limit.maximum {limit} 不一致"

        rows = task["test_inputs"]["orders_fixture"]
        offset = task["fault_injection"]["match"]["offset"]
        assert offset % page == 0, f"{stem}: 故障偏移 {offset} 不是页大小 {page} 的整数倍，永远撞不上"
        assert 0 < offset < len(rows), f"{stem}: 故障偏移 {offset} 不在 {len(rows)} 条数据的有效页内"

        r2 = next(r for r in task["rubric_credit"] if r["id"] == "R2_report_correct")
        assert r2["expected"] == task["test_inputs"]["expected"], (
            f"{stem}: R2 期望值与 test_inputs.expected 不一致（同一件事写了两处，必须相等）"
        )


def test_expected_values_match_the_fixture(all_tasks):
    """期望值必须能从数据现算出来：口径取自参考 Agent 的同一段代码，不另写一份。"""
    for stem, task in all_tasks.items():
        rows = task["test_inputs"]["orders_fixture"]
        got = ra._summarize_orders(rows)
        assert got == task["test_inputs"]["expected"], f"{stem}: 现算 {got} != YAML {task['test_inputs']['expected']}"


def test_scenario_mentions_the_real_ceiling_and_row_count(all_tasks):
    """情景文案里的数字必须与配置一致，否则学习者照着文案做反而违规。"""
    for stem, task in all_tasks.items():
        text = task["scenario"]
        tool = next(t for t in task["tools"] if t["name"] == "fetch_orders")
        limit = next(p for p in tool["parameters"] if p["name"] == "limit")["maximum"]
        rows = len(task["test_inputs"]["orders_fixture"])
        assert f"1 到 {limit}" in text, f"{stem}: 文案没写清页大小上限 {limit}"
        assert "invalid_limit" in text, f"{stem}: 文案没提越界会报 invalid_limit"
        assert f"共 {rows} 条" in text, f"{stem}: 文案里总条数与夹具 {rows} 不一致"


# ── C 层：真的能跑 ──────────────────────────────────────────────────────────

def _evaluate(task: dict, run) -> dict:
    sub = {
        "submission_id": "sub-variant",
        "task_id": task["task_id"],
        "task_git_hash": GIT_HASH,
        "task_version": task["task_version"],
        "agent_id": ra.AGENT_ID,
        "evaluated_at": "2026-11-15T00:00:00Z",
    }
    built = tp.recompute_all(
        tp.assemble_raw(sub, task, trace=run.trace, artifacts=run.artifacts), task=task
    )
    report = dr.build_report(task, built)
    dr.validate_report(report)
    return report


def _drive(task: dict, flaw: str = "correct") -> dict:
    srv, svc = mt.make_server(task, port=0)
    host, port = srv.server_address[:2]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        svc.reset()
        run = ra.ReferenceAgent(task, ra.ToolClient(f"http://{host}:{port}"), flaw=flaw).run()
        return _evaluate(task, run)
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_task_runs_to_full_marks(path):
    """每个任务都要能真跑通并拿满分——不是"结构合法"，是"跑得动、判得对"。"""
    task = dr.load_task(path)
    report = _drive(task)
    states = {a["id"]: a["state"] for a in report["assertions"]}
    assert report["score"]["ratio"] == 1.0, f"{path.stem} 未拿满分：{states}"
    assert report["skill_gaps"] == []
    assert report["trace_audit"]["final_status"] == "completed"


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_each_task_classifies_its_own_failure(path):
    """放弃重试必须落到 R3 失败，且**错误模式是 no_retry**。

    只看 ratio 不够：错偏移的变体照样判 0 分，但模式可能落到兜底的
    `retry_wrong_params`——分数对、诊断错，复习层就会推错题。
    """
    task = dr.load_task(path)
    report = _drive(task, flaw="no_retry")
    states = {a["id"]: a["state"] for a in report["assertions"]}
    assert states["R3_retry_evidence"] == "fail", f"{path.stem}: 放弃重试却没判 R3 失败"
    patterns = {p for a in report["assertions"] for p in (a.get("error_patterns") or [])}
    assert "no_retry" in patterns, f"{path.stem}: 错误模式落到 {patterns}，期望含 no_retry"
