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


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """临时任务库：复制两套骨架的母本过去，其余指向 tmp。"""
    shutil.copy(MOTHER, tmp_path / MOTHER.name)
    shutil.copy(LOCATE_MOTHER, tmp_path / LOCATE_MOTHER.name)
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
    for fam in ("order-report", "retry-drill", "param-surface", "locate-drill"):
        assert fam in out, f"--list 没列出家族 {fam}"
    assert "梯度：" in out
    assert "道（台账" in out


def test_check_exit_code_is_one_when_something_is_wrong(tmp_path, capsys):
    """CI 里要能靠退出码拦：有问题返回 1，没问题返回 0。"""
    path = tmp_path / "bad.yaml"
    path.write_text("task_id: nope\n", encoding="utf-8")
    assert auth.main(["--check", str(path)]) == 1
