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

import json
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
TASK_FILES = sorted(TASKS_DIR.glob("*.yaml"))
GIT_HASH = "deadbee"

# 骨架 → 母本（round-86 / B-05b：从 1 套变 2 套）。变体只能继承**自己那套**的母本，
# 跨骨架继承会让"改母本 ⇒ 该骨架下所有任务一起变"这条保证失效。
MOTHER_OF = {
    "tpl-tool-debug": TASKS_DIR / "dbg-001-order-report.yaml",
    "tpl-bug-locate": TASKS_DIR / "fix-001-token-expiry.yaml",
}
DEFAULT_TEMPLATE = "tpl-tool-debug"

# 家族 → (主技能, 所属骨架)。放在测试里而不是"随手填"：任务的主技能与骨架必须与家族一致，
# 否则 tasks 表里的 primary_skill_id 只是装饰。
FAMILY_PRIMARY = {
    "order-report": "multi_step_planning",
    "retry-drill": "failure_retry",
    "param-surface": "tool_param_validation",
    "locate-drill": "multi_step_planning",
}
FAMILY_TEMPLATE = {
    "order-report": "tpl-tool-debug",
    "retry-drill": "tpl-tool-debug",
    "param-surface": "tpl-tool-debug",
    "locate-drill": "tpl-bug-locate",
}
# 骨架字段：同一骨架内必须逐项相同，只有这里没列的（expected / error_pattern_detection）才允许不同
SKELETON_FIELDS = ("type", "target", "weight", "skill_id", "depends_on",
                   "on_fail", "error_patterns", "detects")


def _template(task: dict) -> str:
    return task.get("variant_template_id") or DEFAULT_TEMPLATE


def _groups(all_tasks: dict[str, dict]) -> dict[str, dict[str, dict]]:
    """按骨架分组：同骨架内的断言与计分口径必须逐字段相同。"""
    out: dict[str, dict[str, dict]] = {}
    for stem, task in all_tasks.items():
        out.setdefault(_template(task), {})[stem] = task
    return out



@pytest.fixture(scope="module")
def all_tasks() -> dict[str, dict]:
    return {p.stem: dr.load_task(p) for p in TASK_FILES}


# ── A 层：继承机制 ──────────────────────────────────────────────────────────

def test_every_variant_declares_its_parent_and_does_not_restate_skeleton():
    """变体文件里不许出现 rubric_gate / scoring / trace_requirements。

    这三块是骨架。一旦某个变体把它们抄进来，"改母本 ⇒ 所有变体跟着变"就断了，
    而且两份口径从此各自演化——这种漂移在测试跑绿的时候完全看不出来。

    第二套骨架之后，这里多一条：变体必须继承**自己那套**的母本。跨骨架继承
    （定位题继承汇报题母本）会拿到一套跟题面完全无关的断言，而文件里看不出来。
    """
    for path in TASK_FILES:
        head = dr.load_task(path)
        tpl = _template(head)
        assert tpl in MOTHER_OF, f"{path.name} 的 variant_template_id={tpl!r} 未登记母本"
        mother = MOTHER_OF[tpl]
        if path == mother:
            continue
        assert head.get("base_task") == mother.stem, (
            f"{path.name} 继承的是 {head.get('base_task')!r}，骨架 {tpl} 的母本是 {mother.stem}"
        )
        text = path.read_text(encoding="utf-8")
        for block in ("rubric_gate:", "scoring:", "trace_requirements:", "submission_requirements:"):
            assert block not in text, f"{path.name} 抄了骨架里的 {block}，应改为继承"


def _write_variant(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _base_copy(tmp_path: Path) -> Path:
    """把母本复制到临时目录，让变体的 base_task 能解析到它。"""
    mother = MOTHER_OF[DEFAULT_TEMPLATE]
    target = tmp_path / mother.name
    target.write_text(mother.read_text(encoding="utf-8"), encoding="utf-8")
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
    mother_path = MOTHER_OF[DEFAULT_TEMPLATE]
    before = mother_path.read_text(encoding="utf-8")
    mother = dr.load_task(mother_path)
    dr.load_task(TASKS_DIR / "dbg-002-invoice-report.yaml")
    after = dr.load_task(mother_path)
    assert mother_path.read_text(encoding="utf-8") == before
    assert mother == after
    assert mother["test_inputs"]["expected"]["valid_count"] == 17


# ── B 层：骨架不变性（逐骨架比，不跨骨架比）─────────────────────────────────

def test_skeleton_is_identical_within_each_template(all_tasks):
    for tpl, tasks in _groups(all_tasks).items():
        mother = tasks[MOTHER_OF[tpl].stem]
        for layer in ("rubric_gate", "rubric_credit"):
            base = {r["id"]: r for r in mother[layer]}
            for stem, task in tasks.items():
                got = {r["id"]: r for r in task[layer]}
                assert list(got) == list(base), (
                    f"{stem} 的 {layer} 断言顺序/组成与骨架 {tpl} 的母本不一致"
                )
                for rid, want in base.items():
                    for field in SKELETON_FIELDS:
                        if field not in want:
                            assert field not in got[rid], f"{stem}.{rid} 凭空多出 {field}"
                            continue
                        assert got[rid][field] == want[field], (
                            f"{stem}.{rid}.{field} = {got[rid][field]!r}，母本是 {want[field]!r}"
                        )


def test_two_skeletons_are_really_different(all_tasks):
    """第二套骨架必须真的不同：断言 id 集合不一样，且题面里没有分页。

    这条锁的是 B-05b 的存在意义。如果两套骨架的断言 id 完全相同，那"第二套骨架"
    就只是换了题面文案，学习者在"分数怎么算"这个维度上仍然只做了一种任务。
    """
    groups = _groups(all_tasks)
    assert set(groups) == set(MOTHER_OF), f"任务库只覆盖了骨架 {set(groups)}"
    ids = {tpl: {r["id"] for r in tasks[MOTHER_OF[tpl].stem]["rubric_credit"]}
           for tpl, tasks in groups.items()}
    assert ids["tpl-tool-debug"] != ids["tpl-bug-locate"], f"两套骨架的 credit 断言 id 相同：{ids}"

    locate = groups["tpl-bug-locate"]
    for stem, task in locate.items():
        assert "pagination" not in json.dumps(task["tools"], ensure_ascii=False), (
            f"{stem} 是定位题，工具里不该出现分页语义"
        )
        names = {t["name"] for t in task["tools"]}
        assert "fetch_orders" not in names, f"{stem} 仍在拉订单，不是定位题"


def test_scoring_and_execution_blocks_agree(all_tasks):
    """计分与执行契约**全库**统一：换题型不该换"分数怎么算"和"平台怎么跑"。"""
    base = all_tasks[MOTHER_OF[DEFAULT_TEMPLATE].stem]
    for stem, task in all_tasks.items():
        for key in ("scoring", "execution", "submission_requirements"):
            assert task[key] == base[key], f"{stem} 的 {key} 与全库口径不一致"


def test_trace_requirements_mechanical_parts_agree(all_tasks):
    """trace 契约的**机械部分**全库相同；note 允许逐骨架写不同的话。"""
    base = all_tasks[MOTHER_OF[DEFAULT_TEMPLATE].stem]["trace_requirements"]["minimum"]
    for stem, task in all_tasks.items():
        got = task["trace_requirements"]["minimum"]
        for key in ("final_status", "tool_calls"):
            lhs, rhs = dict(got.get(key) or {}), dict(base.get(key) or {})
            lhs.pop("note", None)
            rhs.pop("note", None)
            assert lhs == rhs, f"{stem} 的 trace_requirements.{key} 与全库口径不一致"


def test_family_maps_to_declared_primary_skill_and_template(all_tasks):
    seen = set()
    for stem, task in all_tasks.items():
        meta = task["variant_meta"]
        family = meta["family_id"]
        assert family in FAMILY_PRIMARY, f"{stem} 的 family_id={family} 未登记"
        assert task["primary_skill_id"] == FAMILY_PRIMARY[family], (
            f"{stem} 的主技能是 {task['primary_skill_id']}，但家族 {family} 声明的是 "
            f"{FAMILY_PRIMARY[family]}"
        )
        assert _template(task) == FAMILY_TEMPLATE[family], (
            f"{stem} 的骨架是 {_template(task)}，但家族 {family} 声明的是 "
            f"{FAMILY_TEMPLATE[family]}"
        )
        assert meta["kind"] == "variant"
        assert meta["difficulty"] in (1, 2, 3)
        seen.add(family)
    assert seen == set(FAMILY_PRIMARY), f"有家族一个任务都没有：{set(FAMILY_PRIMARY) - seen}"


def test_every_skill_is_primary_somewhere(all_tasks):
    """任务库要能覆盖三个技能，否则复习层只能反复推同一类题。"""
    tax = yaml.safe_load((ROOT / "docs" / "skill-taxonomy-v0.yaml").read_text(encoding="utf-8"))
    skills = {s["skill_id"] for s in tax["skills"]}
    primaries = [t["primary_skill_id"] for t in all_tasks.values()]
    for sid in skills:
        assert primaries.count(sid) >= 2, f"技能 {sid} 作为主技能只出现 {primaries.count(sid)} 次"
    # 故意写死：加题时必须停下来确认"这是一道真独立的题，不是复制粘贴"（docs/authoring-tasks.md §3）。
    # 12 → 13：2026-11-21 新增 ret-004-narrow-page（retry-drill 家族首道 page=5 的题）。
    assert len(all_tasks) == 20, f"任务库应为 20 个任务，实际 {len(all_tasks)}"


def test_difficulty_is_not_decorative(all_tasks):
    """难度不是随手填的数：汇报题与「要翻几页」正相关，定位题与「日志里几条 ERROR」正相关。

    两者都是"要处理多少东西"的代理量。不要求它是唯一口径，但必须**单调可检**——
    否则难度字段会退化成随手填的 1/2/3，而排程层与掌握度都拿它当输入。
    """
    levels = set()
    for tpl, tasks in _groups(all_tasks).items():
        for stem, task in tasks.items():
            diff = task["variant_meta"]["difficulty"]
            levels.add(diff)
            if tpl == "tpl-tool-debug":
                page = task["tools"][0]["mock"]["pagination"]["page_size_max"]
                rows = len(task["test_inputs"]["orders_fixture"])
                pages = -(-rows // page)
                need = {1: 1, 2: 3, 3: 4}[diff]
                assert pages >= need, (
                    f"{stem} 标了难度 {diff}，但只需翻 {pages} 页（难度 {diff} 至少要 {need} 页）"
                )
            else:
                log = task["test_inputs"]["files"][task["locate"]["log_path"]]
                errors = sum(1 for ln in log.splitlines() if " ERROR " in ln)
                need = {1: 2, 2: 3, 3: 4}[diff]
                assert errors >= need, (
                    f"{stem} 标了难度 {diff}，但日志只有 {errors} 条 ERROR（难度 {diff} 至少要 {need} 条）"
                )
    assert levels == {1, 2, 3}, f"难度没有梯度：只有 {levels}"



# ── 一致性不变量：同一件事在两处出现时必须相等 ──────────────────────────────

def test_fault_declaration_matches_retry_expectation(all_tasks):
    """YAML 里故障位/故障码写了两处（fault_injection 与 R3.expected），必须相等。

    这是变体最容易出错的地方：改故障偏移/故障文件却忘了改 R3 的期望序列，
    结果是"Agent 正确重试了，但 R3 判失败"——看起来像学习者的问题。
    `params_match` 与 `match` 整体比，不写死键名：汇报骨架锁 offset、定位骨架锁 path，
    写死一个键名就等于默认只有一种骨架（round-86 把 `["offset"]` 去掉了）。
    """
    for stem, task in all_tasks.items():
        fi = task["fault_injection"]
        calls = next(r for r in task["rubric_credit"] if r["id"] == "R3_retry_evidence")["expected"]["calls"]
        assert len(calls) == 2, f"{stem}: R3 期望应为「失败 → 成功」两步"
        assert calls[0]["params_match"] == fi["match"], (
            f"{stem}: R3 与 fault_injection 的匹配条件不一致"
            f"（{calls[0]['params_match']!r} vs {fi['match']!r}）"
        )
        assert calls[0]["http_status"] == fi["failures"][0]["http_status"], (
            f"{stem}: R3 与 fault_injection 的故障码不一致"
        )
        assert calls[0]["status"] == "error" and calls[1]["status"] == "success"
        assert calls[0]["params_match"] == calls[1]["params_match"]


def test_fault_target_tool_is_declared(all_tasks):
    """故障打在一个必须真的会被调用的工具上，否则故障永远不会触发、R3 永远判失败。"""
    for stem, task in all_tasks.items():
        target = task["fault_injection"]["target_tool"]
        names = {t["name"] for t in task["tools"]}
        assert target in names, f"{stem}: 故障目标 {target!r} 不在工具表里"
        r3 = next(r for r in task["rubric_credit"] if r["id"] == "R3_retry_evidence")
        assert r3["expected"]["calls"][0]["name"] == target, (
            f"{stem}: R3 期望调用的工具与故障目标不是同一个"
        )


def test_page_ceiling_and_report_expectations_are_self_consistent(all_tasks):
    """汇报骨架专属：页大小上限 / 故障偏移 / 期望值三处必须自洽。"""
    for stem, task in _groups(all_tasks).get("tpl-tool-debug", {}).items():
        tool = next(t for t in task["tools"] if t["name"] == "fetch_orders")
        page = tool["mock"]["pagination"]["page_size_max"]
        limit = next(p for p in tool["parameters"] if p["name"] == "limit")["maximum"]
        assert page == limit, f"{stem}: 分页上限 {page} 与 limit.maximum {limit} 不一致"

        rows = task["test_inputs"]["orders_fixture"]
        offset = task["fault_injection"]["match"]["offset_covers"]
        # B-31 前这里还有一条 `assert offset % page == 0`（故障偏移必须是页上限的整数倍）。
        # 它把「只能用最大页拉」写进了测试：limit 本就允许 1..上限，学习者用 limit=3 时
        # 偏移序列是 0/3/6/…，永远踩不到 10，数据拉全了却被判「没重试」。
        # 改覆盖语义后这条不再需要——按上限分页时 [0,page),[page,2page)… 必然有一页覆盖
        # 任意 offset，是数学必然，不用断言。留下的只有「故障点必须在数据范围内」。
        assert 0 < offset < len(rows), f"{stem}: 故障偏移 {offset} 不在 {len(rows)} 条数据的有效页内"

        r2 = next(r for r in task["rubric_credit"] if r["id"] == "R2_report_correct")
        assert r2["expected"] == task["test_inputs"]["expected"], (
            f"{stem}: R2 期望值与 test_inputs.expected 不一致（同一件事写了两处，必须相等）"
        )


def test_locate_paths_and_expectations_are_self_consistent(all_tasks):
    """定位骨架专属：三处路径与期望值必须自洽，且 R2 必须等于 test_inputs.expected。"""
    for stem, task in _groups(all_tasks).get("tpl-bug-locate", {}).items():
        locate = task["locate"]
        files = task["test_inputs"]["files"]
        for key in ("log_path", "source_path"):
            assert locate[key] in files, f"{stem}: locate.{key}={locate[key]!r} 不在夹具里，读不到"
        r1 = next(r for r in task["rubric_gate"] if r["id"] == "R1_file_exists")
        r2 = next(r for r in task["rubric_credit"] if r["id"] == "R2_diagnosis_correct")
        assert r1["target"] == locate["diagnosis_path"], (
            f"{stem}: R1 的产物路径与 locate.diagnosis_path 不一致"
        )
        assert r2["target"] == locate["diagnosis_path"], (
            f"{stem}: R2 的产物路径与 locate.diagnosis_path 不一致"
        )
        assert r2["expected"] == task["test_inputs"]["expected"], (
            f"{stem}: R2 期望值与 test_inputs.expected 不一致（同一件事写了两处，必须相等）"
        )
        # 根因取值必须落在 scenario 里公布的取值域内，否则学习者照着文案做也会被判错
        assert r2["expected"]["root_cause"] in (
            "type_mismatch", "off_by_one", "null_deref", "other"
        ), f"{stem}: 期望根因 {r2['expected']['root_cause']!r} 不在 scenario 的取值域里"


def test_expected_values_match_the_fixture(all_tasks):
    """期望值必须能从数据现算出来：口径取自参考 Agent 的同一段代码，不另写一份。"""
    for stem, task in all_tasks.items():
        if _template(task) == "tpl-tool-debug":
            rows = task["test_inputs"]["orders_fixture"]
            got = ra._summarize_orders(rows)
        else:
            files = task["test_inputs"]["files"]
            got = ra._diagnose(files[task["locate"]["log_path"]],
                               files[task["locate"]["source_path"]])
        assert got == task["test_inputs"]["expected"], (
            f"{stem}: 现算 {got} != YAML {task['test_inputs']['expected']}"
        )


def test_scenario_mentions_the_real_ceiling_and_row_count(all_tasks):
    """情景文案里的数字必须与配置一致，否则学习者照着文案做反而违规。"""
    for stem, task in _groups(all_tasks).get("tpl-tool-debug", {}).items():
        text = task["scenario"]
        tool = next(t for t in task["tools"] if t["name"] == "fetch_orders")
        limit = next(p for p in tool["parameters"] if p["name"] == "limit")["maximum"]
        rows = len(task["test_inputs"]["orders_fixture"])
        assert f"1 到 {limit}" in text, f"{stem}: 文案没写清页大小上限 {limit}"
        assert "invalid_limit" in text, f"{stem}: 文案没提越界会报 invalid_limit"
        assert f"共 {rows} 条" in text, f"{stem}: 文案里总条数与夹具 {rows} 不一致"


def test_locate_scenario_mentions_the_real_paths(all_tasks):
    """定位题的文案必须写明要读哪两份文件、写到哪，且与 locate 块一致。

    文案与配置不一致时，学习者照着文案做会被判"没交产物"——而错在题面，不在他。
    """
    for stem, task in _groups(all_tasks).get("tpl-bug-locate", {}).items():
        text, locate = task["scenario"], task["locate"]
        for key in ("log_path", "source_path", "diagnosis_path"):
            assert locate[key] in text, f"{stem}: 文案里没写 locate.{key}={locate[key]}"
        for label in ("type_mismatch", "off_by_one", "null_deref", "other"):
            assert label in text, f"{stem}: 文案没公布根因取值 {label}"
        assert "最后一条 ERROR" in text, f"{stem}: 文案没说该看哪一条 ERROR"



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
