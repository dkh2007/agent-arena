"""加题工具（scripts/new_task.py / arena.authoring）的约束测试。

## 这份测试在防什么

加题工具是"以后还要继续加题"这条路本身。它错了不会让现有 24 道题变红，
只会让**下一道题**静默地错——所以它比普通模块更需要测试。

三条判据：

| 组 | 判据 | 失效时会发生什么（不报错、只让人白跑） |
|---|---|---|
| 生成真的自洽 | `--new` 产出的 YAML 过得了 `--check`，且期望值是**现算**的 | 加题人拿到一份看着能跑、实际数错的文件，跑 12 分钟全量才发现 |
| 自检真的会拦 | 故障位在最后一条 / 期望值与数据不符 / 台账没登记 / 难度不够 | 自检报"通过"，人以为没问题就去推了 |
| 台账写入不破格式 | 新行插在**自己家族段内**，不是文件末尾 | 台账按家族分组失效，`--list` 的梯度视图开始串族 |

第三组最容易被忽略：`register_in_log` 一旦退化成"追加到末尾"，台账看起来还是 24 行，
但 `tests/test_task_authoring.py::test_log_order_groups_families_together` 会红——
而那时题已经加完了，得回头挪行。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import authoring as auth  # noqa: E402

MOTHER = ROOT / "tasks" / "dbg-001-order-report.yaml"
LOCATE_MOTHER = ROOT / "tasks" / "fix-001-token-expiry.yaml"
ASK_MOTHER = ROOT / "tasks" / "ask-001-report-handover.yaml"


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """临时任务库：复制三套骨架的母本过去，其余指向 tmp。"""
    shutil.copy(MOTHER, tmp_path / MOTHER.name)
    shutil.copy(LOCATE_MOTHER, tmp_path / LOCATE_MOTHER.name)
    shutil.copy(ASK_MOTHER, tmp_path / ASK_MOTHER.name)
    monkeypatch.setattr(auth, "TASKS_DIR", tmp_path)
    log_path = tmp_path / "authoring-log.yaml"
    monkeypatch.setattr(auth, "LOG_PATH", log_path)
    return tmp_path


def _write(tmp: Path, text: str, name: str) -> Path:
    path = tmp / f"{name}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _errors(findings) -> list[str]:
    return [f.message for f in findings if f.level == auth.X]


# ── 生成：自洽 + 现算 ─────────────────────────────────────────────────────

def test_generated_report_task_passes_its_own_check(sandbox):
    text, why = auth.scaffold(family_id="retry-drill", task_id="ret-900-smoke",
                              title="冒烟题", rows=25, page=10, fault_at=None)
    path = _write(sandbox, text, "ret-900-smoke")
    findings = auth.check_task(path, families=auth.load_families(),
                               log=[{"id": "ret-900-smoke", "why": why}])
    assert not _errors(findings), _errors(findings)


def test_generated_locate_task_passes_its_own_check(sandbox):
    text, why = auth.scaffold(family_id="locate-drill", task_id="fix-900-smoke",
                              title="冒烟定位题", rows=0, page=0, fault_at=None)
    path = _write(sandbox, text, "fix-900-smoke")
    findings = auth.check_task(path, families=auth.load_families(),
                               log=[{"id": "fix-900-smoke", "why": why}])
    assert not _errors(findings), _errors(findings)


def test_generated_locate_task_is_solvable_by_reference_agent(sandbox):
    """生成器产出的定位题，参考 Agent 必须真能解出来。

    只过 `--check` 不够：`--check` 不知道"这个根因能不能从日志推出来"。
    2026-11-22 模板一度默认给 IndexError，`--check` 全绿而参考 Agent 直接抛
    `没有可定位的特征`——定位题不可解，满分用例必红。这条就是那次事故的回归。
    """
    from arena import reference_agent as ra

    text, why = auth.scaffold(family_id="locate-drill", task_id="fix-901-solvable",
                              title="可解性", rows=0, page=0, fault_at=None)
    path = _write(sandbox, text, "fix-901-solvable")
    task = auth.load_task(path)
    files = task["test_inputs"]["files"]
    got = ra._diagnose(files[task["locate"]["log_path"]],
                       files[task["locate"]["source_path"]])
    assert got == task["test_inputs"]["expected"], (
        f"参考 Agent 推出 {got}，与答案键 {task['test_inputs']['expected']} 不符"
    )


def test_generated_ask_task_passes_its_own_check(sandbox):
    """交接族：生成器必须认识第三套骨架，且不许把页大小写进工具 schema。

    B-30b 刚落地时这个分支掉进了 `else`，`--new --family handover-drill` 产出的是一份
    **定位题**（read_file + diagnosis.json），却挂着 `variant_template_id: tpl-ask-first`。
    两份口径互相矛盾，`--check` 报一屏假错——后来加题的人会以为"第三套骨架不能用"。
    """
    text, why = auth.scaffold(family_id="handover-drill", task_id="ask-900-smoke",
                              title="交接冒烟", rows=12, page=5, fault_at=None)
    path = _write(sandbox, text, "ask-900-smoke")
    findings = auth.check_task(path, families=auth.load_families(),
                               log=[{"id": "ask-900-smoke", "why": why}])
    assert not _errors(findings), _errors(findings)

    task = auth.load_task(path)
    assert task["variant_template_id"] == "tpl-ask-first"
    fetch = next(t for t in task["tools"] if t["name"] == "fetch_orders")
    limit = next(p for p in fetch["parameters"] if p["name"] == "limit")
    assert "maximum" not in limit, (
        "交接题不许在 schema 里写页大小上限——那是要问出来的口径，写进 schema 等于白送")
    assert not task.get("fault_injection"), "交接族刻意不注入故障（只考先问再动手）"


def test_generated_ask_task_is_solvable_by_reference_agent(sandbox):
    """交接题真能被解出来：页大小必须能从「一页最多 N 条」那句里读出来。

    `--check` 只比对"答复里的数与分页声明一致"，它不知道参考 Agent 的正则认不认这句话。
    两者不一致时，学习者问了也做不对，而失败会记成"能力不足"。
    """
    import threading

    from arena import mock_tools as mt
    from arena import reference_agent as ra

    text, why = auth.scaffold(family_id="handover-drill", task_id="ask-901-solvable",
                              title="可解性", rows=16, page=4, fault_at=None)
    path = _write(sandbox, text, "ask-901-solvable")
    task = auth.load_task(path)

    srv, svc = mt.make_server(task, port=0)
    host, port = srv.server_address[:2]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        svc.reset()
        run = ra.ReferenceAgent(task, ra.ToolClient(f"http://{host}:{port}")).run()
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)

    assert run.trace["final_status"] == "completed", run.notes
    assert run.answer == task["test_inputs"]["expected"], (
        f"参考 Agent 算出 {run.answer}，与答案键 {task['test_inputs']['expected']} 不符"
    )


def test_generated_expected_tracks_the_fixture(sandbox):
    """期望值是**现算**的，不是模板里的常量：改条数 ⇒ 期望值跟着变。

    这条是"手抄期望值"那件事的唯一机器证据。若哪天有人在 scaffold 里写了个常量，
    这条会红，而别处全绿。
    """
    small, _ = auth.scaffold(family_id="retry-drill", task_id="ret-901-a",
                             title="A", rows=12, page=10, fault_at=3)
    big, _ = auth.scaffold(family_id="retry-drill", task_id="ret-902-b",
                           title="B", rows=48, page=10, fault_at=3)
    load = lambda text: auth.yaml.safe_load(text)  # noqa: E731
    a, b = load(small)["test_inputs"]["expected"], load(big)["test_inputs"]["expected"]
    assert a["valid_count"] < b["valid_count"]
    assert a["total_amount"] < b["total_amount"]


def test_generated_expected_is_never_handwritten(sandbox):
    """生成时写进去的期望值，必须与 derivation 现算值逐位相等（容差同判分侧）。"""
    text, _ = auth.scaffold(family_id="param-surface", task_id="prm-900-smoke",
                            title="冒烟", rows=18, page=4, fault_at=7)
    path = _write(sandbox, text, "prm-900-smoke")
    task = auth.load_task(path)
    got = auth.dv.derive_task(task)
    for key, want in task["test_inputs"]["expected"].items():
        assert abs(got[key] - want) <= 0.005, f"{key}: 写的 {want} ≠ 现算 {got[key]}"


def test_scaffold_refuses_a_taken_id(sandbox):
    """同家族的 id 已被占用时必须拒绝（前缀先对，才轮到查重）。"""
    (sandbox / "ret-900-taken.yaml").write_text("task_id: ret-900-taken\n", encoding="utf-8")
    with pytest.raises(ValueError, match="已存在"):
        auth.scaffold(family_id="retry-drill", task_id="ret-900-taken",
                      title="撞车", rows=20, page=10, fault_at=5)


def test_scaffold_refuses_wrong_id_prefix(sandbox):
    with pytest.raises(ValueError, match="开头"):
        auth.scaffold(family_id="retry-drill", task_id="dbg-900-wrong-prefix",
                      title="前缀错", rows=20, page=10, fault_at=5)


def test_scaffold_refuses_fault_on_the_last_row(sandbox):
    """最后一条做故障，"重试后接着拉"这一步就不存在了。"""
    with pytest.raises(ValueError, match="最后一条"):
        auth.scaffold(family_id="retry-drill", task_id="ret-903-last",
                      title="末尾故障", rows=20, page=10, fault_at=20)


def test_default_fault_site_leaves_work_after_retry(sandbox):
    """不给 --fault-at 时自动选的位置，必须留出"重试后还有数据"的余地。"""
    text, _ = auth.scaffold(family_id="retry-drill", task_id="ret-904-auto",
                            title="自动", rows=20, page=10, fault_at=None)
    raw = auth.yaml.safe_load(text)
    at = raw["fault_injection"]["match"]["offset_covers"]
    assert 1 <= at < 20


# ── 自检：真的会拦 ───────────────────────────────────────────────────────

def _good(sandbox, name="ret-905-good", **over) -> Path:
    text, _ = auth.scaffold(family_id="retry-drill", task_id=name, title="基准",
                            rows=20, page=10, fault_at=5, **over)
    return _write(sandbox, text, name)


def test_check_flags_fault_site_moved_to_the_last_row(sandbox):
    path = _good(sandbox)
    text = path.read_text(encoding="utf-8").replace("offset_covers: 5", "offset_covers: 20")
    path.write_text(text, encoding="utf-8")
    msgs = _errors(auth.check_task(path, families=auth.load_families(),
                                   log=[{"id": "ret-905-good", "why": "x" * 20}]))
    assert any("最后一条" in m for m in msgs), msgs


def test_check_flags_stale_expected_and_says_the_right_value(sandbox):
    """报错必须**直接给出正确值**——只报"不一致"等于让人再算一遍。"""
    path = _good(sandbox)
    text = path.read_text(encoding="utf-8")
    task = auth.load_task(path)
    right = task["test_inputs"]["expected"]["total_amount"]
    path.write_text(text.replace(f"total_amount: {right}", "total_amount: 1.0"), encoding="utf-8")
    msgs = _errors(auth.check_task(path, families=auth.load_families(),
                                   log=[{"id": "ret-905-good", "why": "x" * 20}]))
    assert any("现算" in m and str(right) in m for m in msgs), msgs


def test_check_flags_missing_log_entry(sandbox):
    path = _good(sandbox)
    msgs = _errors(auth.check_task(path, families=auth.load_families(), log=[]))
    assert any("台账" in m for m in msgs), msgs


def test_check_flags_placeholder_why(sandbox):
    path = _good(sandbox)
    msgs = _errors(auth.check_task(path, families=auth.load_families(),
                                   log=[{"id": "ret-905-good", "why": "略"}]))
    assert any("why" in m for m in msgs), msgs


def test_check_flags_difficulty_not_backed_by_pages(sandbox):
    """标难度 3 但只有两页——难度退化成装饰字段。"""
    text, _ = auth.scaffold(family_id="retry-drill", task_id="ret-906-thin",
                            title="薄", rows=12, page=10, fault_at=3)
    path = _write(sandbox, text.replace("difficulty: 2", "difficulty: 3"), "ret-906-thin")
    msgs = _errors(auth.check_task(path, families=auth.load_families(),
                                   log=[{"id": "ret-906-thin", "why": "x" * 20}]))
    assert any("页" in m for m in msgs), msgs


def test_check_flags_scenario_row_count_drift(sandbox):
    """题面写"共 20 条"而夹具改了——学习者照题面做会被判错，错在题面。"""
    path = _good(sandbox)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("共 20 条", "共 22 条"), encoding="utf-8")
    msgs = _errors(auth.check_task(path, families=auth.load_families(),
                                   log=[{"id": "ret-905-good", "why": "x" * 20}]))
    assert any("总条数" in m for m in msgs), msgs


def test_check_flags_restated_skeleton_block(sandbox):
    path = _good(sandbox)
    text = path.read_text(encoding="utf-8") + "\nscoring:\n  total: 4\n"
    path.write_text(text, encoding="utf-8")
    msgs = _errors(auth.check_task(path, families=auth.load_families(),
                                   log=[{"id": "ret-905-good", "why": "x" * 20}]))
    assert any("骨架" in m for m in msgs), msgs


def test_check_warns_when_fault_is_inherited_but_unstated(sandbox):
    """没写 fault_injection 不等于没有故障（继承母本）——这条曾让人写错台账。"""
    path = sandbox / "ret-907-inherit.yaml"
    path.write_text(
        "base_task: dbg-001-order-report\n"
        "task_id: ret-907-inherit\n"
        "task_version: \"2026-11-22.1\"\n"
        "title: 继承\n"
        "primary_skill_id: failure_retry\n"
        "variant_template_id: tpl-tool-debug\n"
        "variant_meta: {family_id: retry-drill, difficulty: 1, kind: variant}\n"
        'scenario: |\n  1. limit 取值必须在 1 到 10 之间，超过会报 invalid_limit；共 20 条\n',
        encoding="utf-8")
    findings = auth.check_task(path, families=auth.load_families(),
                               log=[{"id": "ret-907-inherit", "why": "x" * 20}])
    assert any(f.level == auth.WARN and "继承" in f.message for f in findings)


# ── 台账写入：插对段 ─────────────────────────────────────────────────────

LOG_SEED = """\
tasks:
  # ── 汇报族 order-report ──
  - id: dbg-001-order-report
    why: 母本，其余汇报题从它继承骨架与断言
  # ── 重试族 retry-drill ──
  - id: ret-001-refund-503
    why: 重试族基准，第一次失败第二次成功的最小形态
  # ── 定位族 locate-drill ──
  - id: fix-001-token-expiry
    why: 定位族母本，日志加源码给出根因与行号
"""


def test_register_inserts_inside_own_family_block(sandbox):
    """新题插在自己家族段内，不是文件末尾——否则梯度视图会串族。"""
    log_path = auth.LOG_PATH
    log_path.write_text(LOG_SEED, encoding="utf-8")
    auth.register_in_log("ret-008-new", "重试族新题，说清它变的是什么")
    text = log_path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    ids = [ln.strip().removeprefix("- id: ") for ln in lines if ln.strip().startswith("- id:")]
    assert ids.index("ret-001-refund-503") < ids.index("ret-008-new") < ids.index("fix-001-token-expiry")


def test_register_keeps_the_ledger_parsable(sandbox):
    log_path = auth.LOG_PATH
    log_path.write_text(LOG_SEED, encoding="utf-8")
    auth.register_in_log("prm-900-new", "参数族新题，说清它变的是什么")
    data = auth.yaml.safe_load(log_path.read_text(encoding="utf-8"))
    assert {"id": "prm-900-new"} in [{"id": r["id"]} for r in data["tasks"]]


# ── 视图：--list 看得出缺哪一档 ──────────────────────────────────────────

def test_catalog_report_groups_families_and_marks_gaps(capsys):
    auth.main(["--list"])
    out = capsys.readouterr().out
    for fam in ("order-report", "retry-drill", "param-surface", "locate-drill", "handover-drill"):
        assert fam in out, f"--list 没列出家族 {fam}"
    assert "梯度：" in out
    assert "道（台账" in out


def test_scale_of_a_handover_task_counts_pages_not_log_lines():
    """`--list` 里交接题曾显示成「0 行日志 / 0 条 ERROR」。

    看着像题坏了（"日志呢？"），实际是 `_scale` 把"非默认骨架"一律当定位题。
    这类假错比没有输出更糟：人会被训练成忽略自检报告。
    """
    task = auth.load_task(ASK_MOTHER)
    scale = auth._scale(task)
    assert "ERROR" not in scale, f"交接题被当成定位题数日志了：{scale}"
    assert "页" in scale and "无故障" in scale, scale


def test_check_exit_code_is_one_when_something_is_wrong(tmp_path, capsys):
    """CI 里要能靠退出码拦：有问题返回 1，没问题返回 0。"""
    path = tmp_path / "bad.yaml"
    path.write_text("task_id: nope\n", encoding="utf-8")
    assert auth.main(["--check", str(path)]) == 1


# ── 自检：故障打在「非分页工具」上（2026-11-28，ret-008 撞出来的）──────────────
# 上面所有用例的故障都打在 fetch_orders 上，自检里"故障位"那几项也全按 offset 写。
# ret-008 把故障挪到 write_report 之后暴露两件事：
#   ① 空 match（`match: null`）是合法写法，而原来两个分支都在 `if match and ...` 里，
#      空 match 会静默跳过——"静默跳过"正是故障空转最好的藏身处；
#   ② match 里的键名拼错（`report_path`）会让服务端永远拿不到这个键，故障永不触发，
#      而分数看起来完全正常（学习者照常做对）。
WRITE_FAULT = ROOT / "tasks" / "ret-008-write-gateway-502.yaml"
WF_ID = "ret-008-write-gateway-502"


def _write_fault(tmp: Path, old: str = "", new: str = "") -> Path:
    text = WRITE_FAULT.read_text(encoding="utf-8")
    if old:
        assert old in text, f"基准文件里找不到 {old[:40]!r}——本条测试要跟着题目一起改"
        text = text.replace(old, new, 1)
    path = tmp / WRITE_FAULT.name
    path.write_text(text, encoding="utf-8")
    return path


def _check_fault(path: Path):
    return auth.check_task(path, families=auth.load_families(),
                           log=[{"id": WF_ID, "why": "故障打在写产物上：写完 502 要重写"}])


def test_check_accepts_a_fault_on_a_non_paging_tool(sandbox):
    """基准必须过——不然下面三条"会拦"就没意义（可能只是本来就通不过）。"""
    findings = _check_fault(_write_fault(sandbox))
    assert not _errors(findings), _errors(findings)
    assert any("空 match" in f.message for f in findings), "空 match 该有一条明说'任何一次调用都命中'"


def test_check_flags_a_target_tool_that_does_not_exist(sandbox):
    """`write_reprot` 少一个字母：故障永不触发，而页面一片正常。"""
    path = _write_fault(sandbox, "target_tool: write_report   #", "target_tool: write_reprot   #")
    msgs = _errors(_check_fault(path))
    assert any("不在 tools" in m for m in msgs), msgs


def test_check_flags_r3_pointing_at_another_tool_than_the_injection(sandbox):
    """注入打 write_report、判定指 fetch_orders：各自都"合理"，合起来是错的。

    这种错最难发现，因为两侧单独看都能自圆其说：注入侧"我确实注入了"，
    判定侧"我确实在等一次失败"。只有对着看才知道它们从不是同一个调用。
    """
    path = _write_fault(
        sandbox,
        "        - {name: write_report, params_match: {path: /workspace/report.json}, status: error, http_status: 502}",
        "        - {name: fetch_orders, params_match: {path: /workspace/report.json}, status: error, http_status: 502}",
    )
    msgs = _errors(_check_fault(path))
    assert any("不一致" in m and "target_tool" in m for m in msgs), msgs


def test_check_flags_offset_covers_on_a_tool_that_has_no_pages(sandbox):
    """最真实的错法：抄母本的 `match: {offset_covers: 10}`，只把 target_tool 改掉。

    它不报错、不崩，只是 `page_covers` 去读一个不存在的 `offset` ⇒ 判"不覆盖"
    ⇒ 故障永不触发 ⇒ 这道题悄悄退化成"没有故障"，学习者照常满分。
    这是唯一能**静态**抓住那种退化的检查。
    """
    path = _write_fault(sandbox, "  match: null\n", "  match: {offset_covers: 10}\n")
    msgs = _errors(_check_fault(path))
    assert any("offset_covers" in m and "不是分页工具" in m for m in msgs), msgs
