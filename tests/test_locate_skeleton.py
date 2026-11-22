"""第二套 rubric 骨架（B-05b 定位题）的约束测试。

第一套骨架的核心考点是"分页拉全 + 剔无效行 + 汇总"；这一套是"读日志 + 读源码 → 给根因"。
两套共用同一条 `R3_retry_evidence`（失败后对同一目标重试）与 `R4`（参数幻觉），
所以本文件只锁**定位题特有的那部分**：

**A. mock 的第二种回放形态**：按 `mock.lookup` 指定的入参去夹具字典取文本。
   它只回放、不判分——取不到就是 404，判"是不是乱猜路径"是 R4 的事。
**B. 诊断规则是机械可核的**：最后一条 ERROR 决定根因，行号从源码函数体里找。
   这两步都要留证据：日志只给函数名，源码只给行内容，缺一份就答不出来。
**C. 缺陷形态只挂对应断言**：行号错只打掉 R2、多传参数只打掉 R4、不重试才连带 R1/R5。
   只证明"能拿满分"没用，能证明"错了会被抓在对的地方"才是评测器。
"""

from __future__ import annotations

import copy
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

FIX1 = ROOT / "tasks" / "fix-001-token-expiry.yaml"
FIX2 = ROOT / "tasks" / "fix-002-cart-none-deref.yaml"


def _locate_task_files() -> list[Path]:
    """按 `family_id` 从任务库动态取，不写死题号（B-33①，2026-11-22）。

    写死的代价不是红，是**静默漏验**：扩到 20 题时 `fix-003/004` 自动落在骨架契约之外，
    flaw states / `source_path` 一致性这些结构约束对它们一条都没跑，测试却全绿。
    """
    files: list[Path] = []
    for path in sorted((ROOT / "tasks").glob("*.yaml")):
        if "family_id: locate-drill" in path.read_text(encoding="utf-8"):
            files.append(path)
    return files


LOCATE_TASKS = _locate_task_files()
GIT_HASH = "deadbee"
FLAW_STATES = {
    "correct": {},
    "wrong_line": {"R2_diagnosis_correct": "fail"},
    "extra_param": {"R4_no_param_hallucination": "fail"},
    "no_retry": {"R1_file_exists": "fail", "R5_clean_exit": "fail",
                 "R2_diagnosis_correct": "blocked", "R3_retry_evidence": "fail"},
}


@pytest.fixture(scope="module")
def fix1() -> dict:
    return dr.load_task(FIX1)


@pytest.fixture(scope="module")
def fix2() -> dict:
    return dr.load_task(FIX2)


def _files(task: dict) -> dict[str, str]:
    return task["test_inputs"]["files"]


# ── A 层：按入参回放夹具 ────────────────────────────────────────────────────

def test_read_file_replays_the_fixture_by_path(fix1):
    """按 path 回放夹具原文。用**源码**文件验证：日志那份第一次读会撞上注入的 503。"""
    svc = mt.MockToolService(fix1)
    path = fix1["locate"]["source_path"]
    status, body = svc.call("read_file", {"path": path})
    assert status == 200
    assert body["path"] == path
    assert body["content"] == _files(fix1)[path]
    assert body["lines"] == len(_files(fix1)[path].splitlines())


def test_unknown_path_is_a_404_that_lists_what_is_readable(fix1):
    """取不到就把**可用键列出来**：这不是服务端判错，是如实答"你要的东西不存在"。"""
    svc = mt.MockToolService(fix1)
    status, body = svc.call("read_file", {"path": "/workspace/etc/passwd"})
    assert status == 404
    assert body["error"] == "file_not_found"
    assert body["available"] == sorted(_files(fix1))


def test_missing_lookup_param_is_a_400(fix1):
    svc = mt.MockToolService(fix1)
    status, body = svc.call("read_file", {})
    assert status == 400
    assert body["error"] == "missing_param"


def test_lookup_must_name_a_declared_parameter(fix1):
    task = copy.deepcopy(fix1)
    task["tools"][0]["mock"]["lookup"] = "nope"
    with pytest.raises(mt.MockConfigError, match="不是本工具声明的参数"):
        mt.MockToolService(task)


def test_lookup_and_pagination_cannot_coexist(fix1):
    """一个工具不可能既按 offset 切片又按 path 查表：两种回放形态互斥。"""
    task = copy.deepcopy(fix1)
    task["tools"][0]["mock"]["pagination"] = {
        "page_size_max": 10, "offset_base": 0, "range": "half_open",
        "total_source": "fixture_length",
    }
    with pytest.raises(mt.MockConfigError, match="不能同时声明"):
        mt.MockToolService(task)


def test_lookup_requires_a_mapping_fixture(fix1):
    task = copy.deepcopy(fix1)
    task["test_inputs"]["files"] = ["not-a-mapping"]
    with pytest.raises(mt.MockConfigError, match="键 → 文本"):
        mt.MockToolService(task)


def test_reset_does_not_touch_the_replay_corpus(fix1):
    """reset 的语义是清故障额度，不是清夹具：第二次提交必须看到同一份文本。"""
    svc = mt.MockToolService(fix1)
    path = fix1["locate"]["source_path"]
    first = svc.call("read_file", {"path": path})[1]
    svc.reset()
    assert svc.call("read_file", {"path": path})[1] == first


# ── B 层：诊断规则机械可核 ──────────────────────────────────────────────────

def test_diagnosis_matches_the_declared_expectation(fix1, fix2):
    """两个任务的期望值必须能从夹具现算出来（不另写一份口径）。"""
    for task in (fix1, fix2):
        files = _files(task)
        got = ra._diagnose(files[task["locate"]["log_path"]],
                           files[task["locate"]["source_path"]])
        assert got == task["test_inputs"]["expected"], f"{task['task_id']}: 现算 {got}"


def test_last_error_line_wins_not_the_first(fix1):
    """把干扰项挪到最后，结论必须跟着变——否则"取最后一条"这条规则没被执行。"""
    files = _files(fix1)
    log = files[fix1["locate"]["log_path"]]
    lines = log.splitlines()
    tail = [ln for ln in lines if " ERROR " in ln][-1]
    reordered = "\n".join([ln for ln in lines if ln != tail] + [tail])
    got = ra._diagnose(reordered, files[fix1["locate"]["source_path"]])
    # 原样的最后一条确实是 TypeError；打乱后最后一条仍是它 ⇒ 结论不变
    assert got == fix1["test_inputs"]["expected"]

    swapped = "\n".join([tail] + [ln for ln in lines if ln != tail])
    with pytest.raises(ra.AgentError):
        # 把 AttributeError in refresh_session 挪到最后：它要求的属性在
        # refresh_session 体内不存在 ⇒ 机械规则找不到行，必须报错而不是猜一个
        ra._diagnose(swapped, files[fix1["locate"]["source_path"]])


def test_log_without_any_error_is_loud(fix1):
    with pytest.raises(ra.AgentError, match="没有任何 ERROR 行"):
        ra._diagnose("2026-11-15 09:00:00,000 INFO fine\n",
                     _files(fix1)[fix1["locate"]["source_path"]])


def test_unknown_exception_maps_to_other_and_still_locates(fix1):
    """异常类不在表里 ⇒ 根因记 other，但**行号仍然要能定位**（不能因此放弃取证）。"""
    src = _files(fix1)[fix1["locate"]["source_path"]]
    log = ("2026-11-15 09:00:00,000 ERROR request_id=r-1 "
           "ValueError in is_expired: '<' not supported between instances of 'int' and 'int'\n")
    got = ra._diagnose(log, src)
    assert got == {"root_cause": "other", "offending_line": 20}


def test_index_error_message_without_a_locator_is_refused(fix1):
    """规则没覆盖的消息一律拒答：给一个"看起来像"的行号比不给更糟。"""
    src = _files(fix1)[fix1["locate"]["source_path"]]
    log = ("2026-11-15 09:00:00,000 ERROR request_id=r-1 "
           "IndexError in is_expired: list index out of range\n")
    with pytest.raises(ra.AgentError, match="没有可定位的特征"):
        ra._diagnose(log, src)


def test_attribute_error_without_nonetype_is_other(fix1):
    """非 NoneType 的属性错记 other，但仍按属性名定位（parse_token 里的 json.loads）。"""
    src = _files(fix1)[fix1["locate"]["source_path"]]
    log = ("2026-11-15 09:00:00,000 ERROR request_id=r-1 "
           "AttributeError in parse_token: 'dict' object has no attribute 'loads'\n")
    got = ra._diagnose(log, src)
    assert got == {"root_cause": "other", "offending_line": 15}


def test_line_search_does_not_leak_across_functions(fix1):
    """在 `is_expired` 里找 `<`，不能跑到后面的函数里去。"""
    src = _files(fix1)[fix1["locate"]["source_path"]]
    assert ra._line_in_function(src, "is_expired", "<") == 20
    # 后面 refresh_session 体内没有 `<` ⇒ 越界即报错，而不是拿一个更靠后的行号充数
    with pytest.raises(ra.AgentError, match="找不到含"):
        ra._line_in_function(src, "refresh_session", "<")


def test_unknown_function_is_loud(fix1):
    with pytest.raises(ra.AgentError, match="找不到函数"):
        ra._line_in_function("def a():\n    pass\n", "nope", "<")


# ── 投影：不写死字段名 ───────────────────────────────────────────────────────

def test_projected_scalar_takes_the_only_value():
    from arena import assertion_evaluator as ae

    assert ae._projected_scalar({"path": "/a"}) == "/a"
    assert ae._projected_scalar({"offset": None}) is None
    assert ae._projected_scalar({"a": 1, "b": 2}) == {"a": 1, "b": 2}
    assert ae._projected_scalar(7) == 7


# ── C 层：跑通 + 只挂对应断言 ───────────────────────────────────────────────

def _drive(task: dict, flaw: str) -> dict:
    srv, svc = mt.make_server(task, port=0)
    host, port = srv.server_address[:2]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        svc.reset()
        run = ra.ReferenceAgent(task, ra.ToolClient(f"http://{host}:{port}"), flaw=flaw).run()
        sub = {
            "submission_id": "sub-locate",
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
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


@pytest.mark.parametrize("path", LOCATE_TASKS, ids=lambda p: p.stem)
def test_locate_task_runs_to_full_marks(path):
    report = _drive(dr.load_task(path), "correct")
    states = {a["id"]: a["state"] for a in report["assertions"]}
    assert report["score"]["ratio"] == 1.0, f"{path.stem} 未拿满分：{states}"
    assert report["skill_gaps"] == []


@pytest.mark.parametrize("flaw", sorted(FLAW_STATES))
@pytest.mark.parametrize("path", LOCATE_TASKS, ids=lambda p: p.stem)
def test_locate_flaw_only_breaks_its_own_assertions(path, flaw):
    """每种缺陷形态只该打掉它对应的断言——这是"诊断能定位到哪一步走错"的实证。"""
    report = _drive(dr.load_task(path), flaw)
    got = {a["id"]: a["state"] for a in report["assertions"]}
    want = FLAW_STATES[flaw]
    for rid, state in got.items():
        assert state == want.get(rid, "pass"), (
            f"{path.stem}/{flaw}: {rid} 是 {state}，期望 {want.get(rid, 'pass')}"
        )


@pytest.mark.parametrize("path", LOCATE_TASKS, ids=lambda p: p.stem)
def test_wrong_line_is_classified_as_wrong_offending_line(path):
    report = _drive(dr.load_task(path), "wrong_line")
    patterns = {p for a in report["assertions"] for p in (a.get("error_patterns") or [])}
    assert patterns == {"wrong_offending_line"}, f"{path.stem}: 分类落到 {patterns}"


@pytest.mark.parametrize("path", LOCATE_TASKS, ids=lambda p: p.stem)
def test_giving_up_on_503_is_classified_as_no_retry(path):
    """放弃重试必须落到 R3 失败且模式是 no_retry，不能退到兜底的 retry_wrong_params。"""
    report = _drive(dr.load_task(path), "no_retry")
    r3 = next(a for a in report["assertions"] if a["id"] == "R3_retry_evidence")
    assert r3["state"] == "fail"
    assert "no_retry" in (r3.get("error_patterns") or []), r3.get("error_patterns")


def test_unknown_template_is_loud(fix1):
    task = copy.deepcopy(fix1)
    task["variant_template_id"] = "tpl-nope"
    with pytest.raises(ra.AgentError, match="未实现"):
        ra.ReferenceAgent(task, ra.ToolClient("http://127.0.0.1:1"))


def test_missing_locate_block_is_loud(fix1):
    task = copy.deepcopy(fix1)
    del task["locate"]
    with pytest.raises(ra.AgentError, match="缺 locate"):
        ra.ReferenceAgent(task, ra.ToolClient("http://127.0.0.1:1"))


def test_locate_path_not_in_fixture_is_loud(fix1):
    """locate 指向的路径不在夹具里 ⇒ 构造期就报错。

    不拦的话要等到运行时才 404，而那时 Agent 已经发过一次调用——trace 里多一条
    "读了不存在的文件"，看起来像学习者乱猜路径（R4 不判 404，于是没人背这个锅）。
    """
    task = copy.deepcopy(fix1)
    task["locate"]["source_path"] = "/workspace/nope.py"
    with pytest.raises(ra.AgentError, match="读不到内容"):
        ra.ReferenceAgent(task, ra.ToolClient("http://127.0.0.1:1"))


def test_flaw_must_belong_to_the_template(fix1, ):
    """形态与骨架接错要在构造期就炸，不能跑到一半变成一份 crashed 的 trace。"""
    with pytest.raises(ra.AgentError, match="不属于骨架"):
        ra.ReferenceAgent(copy.deepcopy(fix1), ra.ToolClient("http://127.0.0.1:1"),
                          flaw="wrong_filter")
    debug = dr.load_task(ROOT / "tasks" / "dbg-001-order-report.yaml")
    with pytest.raises(ra.AgentError, match="不属于骨架"):
        ra.ReferenceAgent(debug, ra.ToolClient("http://127.0.0.1:1"), flaw="wrong_line")


def test_parametrization_covers_every_locate_task():
    """参数化列表必须覆盖全家族——它写死过，也差点空过，两次都是静默的。

    这里用两条互相独立的路径交叉验证：一条是文本匹配（`LOCATE_TASKS` 的取法），
    一条是解析后的 `variant_meta.family_id`。两条不一致就说明取法漏了或错抓了。
    """
    in_catalog = {
        path.stem for path in (ROOT / "tasks").glob("*.yaml")
        if dr.load_task(path)["variant_meta"].get("family_id") == "locate-drill"
    }
    assert in_catalog, "题库里一道定位题都没有——前置条件已经崩了，不该继续跑骨架契约"
    assert {path.stem for path in LOCATE_TASKS} == in_catalog
