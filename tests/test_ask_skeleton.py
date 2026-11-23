"""第三套 rubric 骨架（tpl-ask-first 交接题）的约束测试。

前两套考的是「信息齐了能不能干对」（拉全/剔除/汇总）与「证据齐了能不能推对」（日志/源码）。
这一套考的是**信息不齐时怎么办**：三条口径只在人嘴里，题面明说文档里没有。

所以本文件只锁这件新事，不重复验另两套已有的部分：

**A. 判分真的分得开三种人**
   correct   问 → 拉 → 算        ⇒ 满分
   ask_late  先干完再补问一句      ⇒ **只** R6 失败（R2/R3 仍满分）
   no_ask    不问、按旧口径猜着干  ⇒ R6 失败，且口径错导致 R2 也失败
   `ask_late` 那条是关键：如果它连带把 R2 也打掉，"R6 是独立维度"就只是句空话。

**B. 错误模式分得清"没问"与"问晚了"**
   两种给学习者的建议不同（一个教"先别猜"，一个教"确认要放在动手前"）。
   分数都是 0.6，但复习层推的题不能一样。

**C. 缺席的另一半：题面不许写口径**
   汇报族有 `test_scenario_mentions_the_real_ceiling_and_row_count` 逼题面写清上限；
   本骨架**故意不写**——口径就是那个要问的东西。这里反向锁住：题面不得出现页大小上限，
   否则学习者照着题面抄就能绕过 ask_policy，R6 变成背课文。
"""

from __future__ import annotations

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


def _ask_tasks() -> list[Path]:
    """按 `family_id` 从任务库动态取，不写死题号。

    写死的代价不是红，是**静默漏验**：以后往这个家族加变体时，它自动落在骨架约束之外，
    测试却全绿——locate 族在扩到 5 题时踩过这个坑（见 test_locate_skeleton.py）。
    """
    files: list[Path] = []
    for path in sorted((ROOT / "tasks").glob("*.yaml")):
        if "family_id: handover-drill" in path.read_text(encoding="utf-8"):
            files.append(path)
    return files


ASK_TASKS = _ask_tasks()

# 形态 → 期望状态（只列**该形态有差异**的断言；没列的必须与 correct 相同）。
FLAW_STATES = {
    "correct": {},
    "ask_late": {"R6_ask_before_act": "fail"},
    "no_ask": {"R6_ask_before_act": "fail", "R2_report_correct": "fail"},
    "extra_param": {"R4_no_param_hallucination": "fail"},
}

# 形态 → 期望错误模式（None 表示"不许归因"）。
FLAW_PATTERNS = {
    "no_ask": "no_clarification",
    "ask_late": "clarified_too_late",
}


def _evaluate(task: dict, run) -> dict:
    sub = {
        "submission_id": "sub-ask",
        "task_id": task["task_id"],
        "task_git_hash": GIT_HASH,
        "task_version": task["task_version"],
        "agent_id": ra.AGENT_ID,
        "evaluated_at": "2026-11-23T00:00:00Z",
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


@pytest.fixture(scope="module")
def baseline() -> dict[str, dict]:
    """每个任务跑一次 correct，作为其他形态的参照——"只有它该变"才叫区分得开。"""
    out = {}
    for path in ASK_TASKS:
        out[path.stem] = _drive(dr.load_task(path), "correct")
    return out


def test_family_has_at_least_one_task():
    """家族动态收集的结果不能是空：空列表会让下面每一条都"通过"。"""
    assert ASK_TASKS, "tasks/ 里没有任何 handover-drill 家族的任务"


@pytest.mark.parametrize("path", ASK_TASKS, ids=lambda p: p.stem)
def test_correct_run_gets_full_marks(path, baseline):
    report = baseline[path.stem]
    states = {a["id"]: a["state"] for a in report["assertions"]}
    assert report["score"]["ratio"] == 1.0, f"{path.stem} 未拿满分：{states}"
    assert report["trace_audit"]["final_status"] == "completed"


@pytest.mark.parametrize("path", ASK_TASKS, ids=lambda p: p.stem)
@pytest.mark.parametrize("flaw", sorted(FLAW_STATES), ids=str)
def test_flaw_hits_exactly_the_expected_assertions(path, flaw, baseline):
    """每种缺陷形态只该打掉它对应的那几条，其余必须与满分那次一模一样。"""
    task = dr.load_task(path)
    expected = FLAW_STATES[flaw]
    got = {a["id"]: a["state"] for a in _drive(task, flaw)["assertions"]}
    base = {a["id"]: a["state"] for a in baseline[path.stem]["assertions"]}
    for rid, state in got.items():
        want = expected.get(rid, base[rid])
        assert state == want, (
            f"{path.stem} / flaw={flaw}: {rid} 是 {state}，期望 {want}"
            f"（全表 {got}）"
        )


@pytest.mark.parametrize("path", ASK_TASKS, ids=lambda p: p.stem)
@pytest.mark.parametrize("flaw", sorted(FLAW_PATTERNS), ids=str)
def test_flaw_yields_the_right_error_pattern(path, flaw):
    """分数只能说明"扣了几分"，复习层还要知道**扣在哪**。"""
    task = dr.load_task(path)
    report = _drive(task, flaw)
    r6 = next(a for a in report["assertions"] if a["id"] == "R6_ask_before_act")
    assert r6["state"] == "fail"
    assert r6["error_patterns"] == [FLAW_PATTERNS[flaw]], (
        f"{path.stem} / flaw={flaw}: R6 归因到 {r6['error_patterns']}，"
        f"期望 {[FLAW_PATTERNS[flaw]]}"
    )


@pytest.mark.parametrize("path", ASK_TASKS, ids=lambda p: p.stem)
def test_ask_late_keeps_the_answer_right(path, baseline):
    """先干后问的那次，答案仍然满分——这条是"R6 独立于 R2"的硬证据。

    如果哪天有人把 ask_late 实现成"忘了剔 cancelled"，它会连带打掉 R2，
    这条就会红：那意味着这个形态不再能证明维度独立，得换个实现。
    """
    task = dr.load_task(path)
    report = _drive(task, "ask_late")
    r2 = next(a for a in report["assertions"] if a["id"] == "R2_report_correct")
    assert r2["state"] == "pass", "ask_late 形态必须仍然算出正确答案，否则它证明不了 R6 独立"
    assert baseline[path.stem]["score"]["ratio"] == 1.0


@pytest.mark.parametrize("path", ASK_TASKS, ids=lambda p: p.stem)
def test_scenario_must_not_reveal_the_policy(path):
    """题面不许写出页大小上限——那是要问的东西。

    汇报族有一条相反的测试（题面**必须**写上上限）。两条一起构成口径：
    什么时候该告诉学习者，什么时候不该。写混了，R6 考的就成了"照着题面抄"。
    """
    task = dr.load_task(path)
    tool = next(t for t in task["tools"] if (t.get("mock") or {}).get("pagination"))
    limit = int(tool["mock"]["pagination"]["page_size_max"])
    scenario = task["scenario"]
    assert f"{limit} 条" not in scenario, "题面把每页条数写出来了，ask_policy 就失去意义"
    assert "invalid_limit" not in scenario, "题面提了越界错误码，等于提示页大小是有限制的"

    # 反过来：口径的**来源**必须写清，否则学习者会以为是自己漏读了文档。
    assert "值班同事" in scenario or "口述" in scenario or "当面" in scenario, (
        "题面必须说明口径来自人而不是文档，否则这道题变成猜谜"
    )
