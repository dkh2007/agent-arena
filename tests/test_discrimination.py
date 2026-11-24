"""区分度：同一道题上不同质量的提交，必须被分到不同的分数或不同的诊断上。

## 为什么这条要单独存在

`test_task_runs_to_full_marks` 只证明「满分可达」，`test_each_task_classifies_its_own_failure`
只证明「放弃重试会落到 no_retry」。两条合起来仍然允许一件事：**分数动、诊断不动**，
或者两种完全不同的失败拿到一模一样的报告。那正是复习层推错题的入口——
学习者明明是不会重试，系统却建议他重做一遍剔除逻辑。

所以这里验的不是「有没有分」，是**分不分得开**：形态之间要么分数不同，
要么失败断言集合不同，要么错误模式不同；三者全同 = 不可区分 = 红。

## 形态从哪来（不写手工 JSON）

`fixtures/raw-*.json` 那五份是 2026-11-08 的产物，那时还没有 `reference_agent` 与
`mock_tools`，只能手写一份「看起来像」的提交。手写的东西会漂——`raw-empty` 的
`artifacts` 其实是空对象（它是「0 次调用的地板哨兵」，不是「交了空文件」），
于是 `empty_output_file` 这条模式在手写夹具里从来没被触发过。

现在有两条更硬的路：
1. **骨架缺陷**（算错 / 定位错行 / 不问 / 问晚了 / 参数瞎填）：`ReferenceAgent(flaw=...)`
   真的跑一遍，trace 是真实产生的；
2. **前提失败**（无产物 / 空文件 / 无 trace）：`assemble_raw` 能从任意 trace + 产物组出
   合法提交，id 壳按 rubric 现生成，所以与题目内容无关，每题零成本。

两条路都不需要为 27 道题各写 5 份 JSON——那是 135 份，写完还会漂。

## 判据怎么算「分得开」

签名 = (ratio, 非 pass 断言的 (id, state) 集合, 这些断言上的错误模式集合)。
用元组比较，不用「大概不同」。
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import mock_tools as mt  # noqa: E402
from arena import reference_agent as ra  # noqa: E402
from arena import trace_pipeline as tp  # noqa: E402

GIT_HASH = "deadbee"

# 骨架 → 它自己的「答错」形态。与 `reference_agent._TEMPLATE_FLAWS` 对齐：
# 一套骨架不能假装实现另一套的缺陷，接错会在构造期抛 AgentError。
WRONG_ANSWER_FLAW = {
    "tpl-tool-debug": "wrong_filter",
    "tpl-bug-locate": "wrong_line",
    "tpl-ask-first": "no_ask",
}

# 骨架 → 只打掉一条的「单点缺陷」，用来证明维度之间互相独立。
SINGLE_POINT_FLAW = {
    "tpl-tool-debug": None,       # 汇报族没有「只错一处」的形态，靠 wrong_filter 覆盖
    "tpl-bug-locate": "wrong_line",
    "tpl-ask-first": "ask_late",
}

# 前提失败三形态：与题目内容无关，靠 assemble_raw 造。
PREMISE_FORMS = ("no_artifact", "empty_artifact", "no_trace")

TASK_FILES = sorted((ROOT / "tasks").glob("*.yaml"))


def _template(task: dict) -> str:
    return task.get("variant_template_id") or "tpl-tool-debug"


def _submission(task: dict) -> dict:
    return {
        "submission_id": "sub-discrimination",
        "task_id": task["task_id"],
        "task_git_hash": GIT_HASH,
        "task_version": task.get("task_version", 1),
        "agent_id": ra.AGENT_ID,
        "evaluated_at": "2026-11-24T00:00:00Z",
    }


def _evaluate(task: dict, trace, artifacts) -> dict:
    built = tp.recompute_all(
        tp.assemble_raw(_submission(task), task, trace=trace, artifacts=artifacts), task=task
    )
    report = dr.build_report(task, built)
    dr.validate_report(report)
    return report


def _drive(task: dict, flaw: str):
    """起一次 mock 服务、跑一遍参考 Agent。返回 AgentRun（trace + artifacts）。"""
    srv, svc = mt.make_server(task, port=0)
    host, port = srv.server_address[:2]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        svc.reset()
        return ra.ReferenceAgent(task, ra.ToolClient(f"http://{host}:{port}"), flaw=flaw).run()
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def _wrong_value(value):
    """把期望值改成一个**必然判错**的值。类型保持一致，否则测的就成了类型错而不是值错。"""
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    return "__wrong__"


def _partial_forms(task: dict, artifacts: dict) -> dict[str, dict]:
    """「部分正确」：答对一半——只改期望里的**一个**字段，其余保持满分。

    这个形态 2026-11-24 之前一直没做（B-38 记的缺口），理由是 `wrong_filter` 整份算错，
    没有"半对"形态。其实不需要改参考 Agent：R2 是 `json_field_equals`，
    产物就是一份 JSON，改其中一个键即可，而且**纯计算、不起服务**。

    为什么要它：区分度不只是"能不能判 0 分"，还要看**诊断粒度**。
    学习者答对一个字段、错一个字段时，如果系统报"结论错"而不说错在哪个字段，
    复习建议就会连带把已经会的那件事也推一遍。
    """
    spec = next(
        c for c in task["rubric_credit"] if c.get("type") == "json_field_equals"
    )
    target = spec["target"]
    expected = dict(spec["expected"])
    out: dict[str, dict] = {}
    for i, field in enumerate(expected):
        data = json.loads(artifacts[target])
        data[field] = _wrong_value(expected[field])
        mutated = dict(artifacts)
        mutated[target] = json.dumps(data, ensure_ascii=False)
        out[f"partial_{i + 1}"] = mutated
    return out


def _signature(report: dict) -> tuple:
    bad = tuple(
        sorted((a["id"], a["state"]) for a in report["assertions"] if a["state"] != "pass")
    )
    patterns = tuple(
        sorted({p for a in report["assertions"] for p in (a.get("error_patterns") or ())})
    )
    return (round(report["score"]["ratio"], 6), bad, patterns)


def _forms_of(task: dict) -> dict[str, dict]:
    """一道题的全部形态报告。顺序固定，便于失败时对照。"""
    tpl = _template(task)
    correct = _drive(task, "correct")
    trace, artifacts = correct.trace, correct.artifacts

    forms: dict[str, dict] = {"correct": _evaluate(task, trace, artifacts)}
    forms["no_artifact"] = _evaluate(task, trace, {})
    forms["empty_artifact"] = _evaluate(task, trace, {k: "" for k in artifacts})
    # 参数面：三套骨架都实现了这条，且它是唯一能让 R4 单独掉分的形态
    # （no_trace 只让 R4 变 unscored，那是「没过程」，不是「参数错」）。
    run = _drive(task, "extra_param")
    forms["extra_param"] = _evaluate(task, run.trace, run.artifacts)
    forms["no_trace"] = _evaluate(task, None, artifacts)

    wrong = WRONG_ANSWER_FLAW.get(tpl)
    if wrong is None:
        pytest.fail(f"{task['task_id']}: 骨架 {tpl!r} 没有登记「答错」形态，区分度无从验起")
    run = _drive(task, wrong)
    forms[wrong] = _evaluate(task, run.trace, run.artifacts)

    single = SINGLE_POINT_FLAW.get(tpl)
    if single and single not in forms:
        run = _drive(task, single)
        forms[single] = _evaluate(task, run.trace, run.artifacts)

    # 部分正确**不进 forms**：它验的是另一件事（见 `test_partial_answers_are_diagnosed_field_by_field`）。
    # 定位族的 `wrong_line` 本身只错一个字段（行号），所以它的签名必然与「改行号」那个 partial 相同——
    # 那是同一个缺陷的两种造法，不是区分度缺口。放进与骨架形态一起比，会要求它们必须不同，等于测错东西。
    partials = {
        name: _evaluate(task, trace, mutated)
        for name, mutated in _partial_forms(task, artifacts).items()
    }
    return forms, partials


_CACHE: dict[str, tuple[dict[str, dict], dict[str, dict]]] = {}


def _data(stem: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """一道题的 (骨架形态, 部分正确形态)。两个 fixture 共用一次计算——每道题只跑一遍 Agent。"""
    if stem not in _CACHE:
        _CACHE[stem] = _forms_of(dr.load_task(ROOT / "tasks" / f"{stem}.yaml"))
    return _CACHE[stem]


@pytest.fixture(scope="module")
def matrix() -> dict[str, dict[str, dict]]:
    """{题 stem: {形态: 报告}}。整个模块只算一遍——每次形态都要起服务跑 Agent，不便宜。"""
    assert TASK_FILES, "tasks/ 下没有任务，下面的用例会全部空过"
    return {path.stem: _data(path.stem)[0] for path in TASK_FILES}


@pytest.fixture(scope="module")
def partials() -> dict[str, dict[str, dict]]:
    """{题 stem: {partial_N: 报告}}——只答对一半的形态。"""
    return {path.stem: _data(path.stem)[1] for path in TASK_FILES}


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_upper_and_lower_bounds_are_reachable(path, matrix):
    """上界与下界都要真的够得着：全是 0 或者全是 1 的题目没有区分度可言。"""
    forms = matrix[path.stem]
    assert forms["correct"]["score"]["ratio"] == 1.0, "满分必须可达，否则上界没有锚点"
    assert min(f["score"]["ratio"] for f in forms.values()) == 0, "判不出 0 分就没有下界"


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_every_flaw_costs_score(path, matrix):
    """有缺陷就必须掉分。掉不下来的缺陷形态等于没在测它。

    注意不能 `pop` 掉 correct：`matrix` 是模块级 fixture，弹出会**静默削弱**
    后面「两两可分」那条判据（满分形态从对比里消失了，测试照样绿）。
    """
    forms = matrix[path.stem]
    full = forms["correct"]["score"]["ratio"]
    slack = [
        f"{name}={f['score']['ratio']}"
        for name, f in forms.items()
        if name != "correct" and f["score"]["ratio"] >= full
    ]
    assert not slack, f"{path.stem}: 这些形态没掉分：{slack}"


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_forms_are_pairwise_distinguishable(path, matrix):
    """两两可分：分数、失败断言、错误模式三者全同 = 这两种失败在系统里无法区分。

    只看分数不够——`ask_late` 与 `extra_param` 都是 0.75，
    但一个掉 R6、一个掉 R4，诊断指向完全不同的复习内容。
    """
    forms = matrix[path.stem]
    seen: dict[tuple, list[str]] = {}
    for name, report in forms.items():
        seen.setdefault(_signature(report), []).append(name)
    clashes = {sig: names for sig, names in seen.items() if len(names) > 1}
    assert not clashes, f"{path.stem}: 这些形态不可区分：{list(clashes.values())}"


def test_fault_mapping_is_uniform_within_a_skeleton(matrix):
    """同一骨架内，形态 → 签名必须逐题相同。

    这是「变体只换数据、不换判定行为」的第二道证据（第一道是
    `test_task_variants.py` 的骨架逐字段比对）。它抓的是那种 YAML 看起来一样、
    跑起来不一样的情况：比如某题的故障位刚好被别的分支遮住，
    `no_retry` 于是没有掉 R3——分数照掉，诊断却落到了别处。
    """
    by_template: dict[str, dict[str, dict[str, tuple]]] = {}
    for stem, forms in matrix.items():
        task = dr.load_task(ROOT / "tasks" / f"{stem}.yaml")
        tpl = _template(task)
        by_template.setdefault(tpl, {})[stem] = {n: _signature(r) for n, r in forms.items()}

    problems: list[str] = []
    for tpl, per_task in by_template.items():
        baseline_stem, baseline = next(iter(per_task.items()))
        for stem, sigs in per_task.items():
            if set(sigs) != set(baseline):
                problems.append(f"{tpl}/{stem}: 形态集合 {sorted(sigs)} ≠ {sorted(baseline)}")
                continue
            for name, sig in sigs.items():
                if sig != baseline[name]:
                    problems.append(
                        f"{tpl}/{stem}: 形态 {name} 的签名与 {baseline_stem} 不同\n"
                        f"    本题 {sig}\n    基线 {baseline[name]}"
                    )
    assert not problems, "\n".join(problems)


def test_single_point_flaws_only_break_their_own_dimension(matrix):
    """单点缺陷只许打掉它自己那一条——这是「维度互相独立」的硬证据。

    `ask_late`（先干完再补问）如果连带把答案也判错，"R6 独立于 R2"就只是句空话，
    复习层也无从判断该教「确认要放在动手前」还是「剔除规则要看清」。
    """
    checked = 0
    for stem, forms in matrix.items():
        if "ask_late" not in forms:
            continue
        bad = [a["id"] for a in forms["ask_late"]["assertions"] if a["state"] != "pass"]
        assert bad == ["R6_ask_before_act"], f"{stem}: ask_late 只该掉 R6，实际 {bad}"
        patterns = {
            p for a in forms["ask_late"]["assertions"] for p in (a.get("error_patterns") or ())
        }
        assert patterns == {"clarified_too_late"}, f"{stem}: 归因 {patterns}"
        checked += 1
    assert checked, "没有任何题跑到 ask_late，这条测试等于没验"


def test_absent_output_and_empty_output_are_different_patterns(matrix):
    """「没交产物」与「交了个空文件」必须落到不同错误模式。

    两者分数都是 0、状态都是 R1 fail，唯一能分开的就是模式。
    若模式也相同，`empty_output_file` 就是一条永远不会出现的死词条，
    而学习者看到"没交产物"的建议时，其实他只是交了个空文件。
    """
    for stem, forms in matrix.items():
        no_art = {
            p for a in forms["no_artifact"]["assertions"] for p in (a.get("error_patterns") or ())
        }
        empty = {
            p for a in forms["empty_artifact"]["assertions"] for p in (a.get("error_patterns") or ())
        }
        assert no_art == {"no_output_file"}, f"{stem}: 无产物归因 {no_art}"
        assert empty == {"empty_output_file"}, f"{stem}: 空文件归因 {empty}"


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_partial_answers_are_diagnosed_field_by_field(path, partials):
    """答对一半时，必须指到**具体哪个字段**错，而不是"结论错"一把抓。

    R2 是 `json_field_equals`，期望里有两个字段。只改其中一个（另一个保持正确）时：
    - 失败的断言只能有 R2 一条（流程没坏、参数没错）；
    - 错误模式只能有**一个**，且不同字段错报出的模式必须不同。

    为什么要单独验：复习层按错误模式推题。若两个字段错都报"结论错"，
    学习者已经会的那一半也会被再推一遍；反过来，若报错字段报错了，
    他会去补一件本来就没问题的事——比不推还糟。
    """
    forms = partials[path.stem]
    assert len(forms) >= 2, f"{path.stem}: 期望里不到两个字段，部分正确无从验起：{list(forms)}"

    task = dr.load_task(ROOT / "tasks" / f"{path.stem}.yaml")
    r2_id = next(c["id"] for c in task["rubric_credit"] if c.get("type") == "json_field_equals")

    blamed: dict[str, set[str]] = {}
    for name, report in sorted(forms.items()):
        failed = [a["id"] for a in report["assertions"] if a["state"] != "pass"]
        assert failed == [r2_id], f"{path.stem}/{name}: 只改一个字段却打掉了 {failed}"
        patterns = {p for a in report["assertions"] for p in (a.get("error_patterns") or ())}
        assert len(patterns) == 1, f"{path.stem}/{name}: 只错一个字段却报 {sorted(patterns)}"
        blamed[name] = patterns

    assert len({frozenset(v) for v in blamed.values()}) == len(blamed), (
        f"{path.stem}: 不同字段错报了同一个模式，等于没指到字段：{blamed}"
    )
