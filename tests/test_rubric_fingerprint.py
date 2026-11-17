"""B-04e 第一步：判据指纹（`rubric_fingerprint`）的判据锁。

**这一步解决什么**：B-04d 的护栏承诺「标准改完后批量回溯重算」，但今天没人知道
「本次改动影响了全库哪些提交」，只能让人手工指定 `user_id` + `task`。原因是报告里
没有任何字段记录"这份提交按哪版判据判的"。指纹就是补这个缺口的地基。

**这一步刻意不做什么**（避免把地基写成半成品）：
  - 不改 schema、不加列、不动 `build_report()`。指纹先作为一个纯函数存在，
    什么时候写进报告、写进哪一层，是下一个独立小步；
  - 不回填历史。历史报告没有指纹，是因为**当时没有记**，事后补算等于伪造
    （你不知道那一刻的 rubric 长什么样）。这与 `artifacts` 为 NULL 的记法一致。

**判据的设计取向：宁可多跑一次重评，也不许漏掉一次。** 所以下面「必须变红」的
断言全部是"改了会影响判定或归因"的字段；「必须不变」的只有纯等价写法（键序/缩进/注释）。
凡是不对称的地方，一律选保守侧，并把代价写在测试名里。
"""

from __future__ import annotations

import copy
import glob
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena.diagnosis_report import (  # noqa: E402
    FINGERPRINT_VERSION,
    ReportError,
    load_task,
    rubric_fingerprint,
)

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"


def _task() -> dict:
    return load_task(TASK_PATH)


def _mutated(fn) -> str:
    """把 task 拷一份交给 fn 改，返回改完后的指纹。"""
    task = copy.deepcopy(_task())
    fn(task)
    return rubric_fingerprint(task)


# ── 稳定性：同一份 rubric 必须给出同一个值，否则一切比对无意义 ──────────────

def test_same_task_twice_gives_the_same_fingerprint():
    assert rubric_fingerprint(_task()) == rubric_fingerprint(_task())


def test_fingerprint_is_a_full_sha256_hex_digest():
    fp = rubric_fingerprint(_task())
    assert len(fp) == 64, "不截断：截一半省不下什么，却要多论证一遍碰撞概率"
    assert set(fp) <= set("0123456789abcdef")
    assert FINGERPRINT_VERSION == "rubric-fp-v1", "换归一化规则必须升版本串"


def test_bumping_the_algorithm_version_moves_every_fingerprint(monkeypatch):
    """版本串必须参与哈希，否则"换了归一化规则"和"换了判据"在库里长得一样。

    这是唯一能区分「这批旧指纹是旧算法算的，别拿它跟新指纹比」的机制：
    升版本 ⇒ 所有指纹同时变 ⇒ 旧记录一眼可辨，不会被当成"判据改了"去触发重评。
    """
    from arena import diagnosis_report as dr

    before = rubric_fingerprint(_task())
    monkeypatch.setattr(dr, "FINGERPRINT_VERSION", "rubric-fp-v2-probe")
    assert rubric_fingerprint(_task()) != before


# ── 必须变：影响 score.ratio / 门禁的字段 ──────────────────────────────────

def test_expected_value_change_moves_the_fingerprint():
    """改标准答案（最典型的"判据改了"）。round-91 的回溯实验就是改这个。"""
    fp = _mutated(lambda t: t["rubric_credit"][0]["expected"].__setitem__("valid_count", 18))
    assert fp != rubric_fingerprint(_task())


def test_tolerance_change_moves_the_fingerprint():
    """容差改了，同一份产物的 pass/fail 会变——是判据变更，不是格式变更。"""
    fp = _mutated(lambda t: t["rubric_credit"][0]["tolerance"].__setitem__("total_amount", 0.5))
    assert fp != rubric_fingerprint(_task())


def test_weight_change_moves_the_fingerprint():
    """权重进入分母，改了它 ratio 直接变。"""
    fp = _mutated(lambda t: t["rubric_credit"][0].__setitem__("weight", 3))
    assert fp != rubric_fingerprint(_task())


def test_gate_condition_change_moves_the_fingerprint():
    """门禁的判定条件改了（"存在且非空" → "只要存在"），同一份产物会由 fail 变 pass。

    这里刻意不测 `on_fail`：把它改成 `deduct` 会被分层校验拒掉（门禁必须
    `disqualify`），那造出来的是**非法 rubric**，不是"另一版合法判据"。
    指纹只允许在合法 rubric 上算，这种改动连算都算不出来——见文件末尾那条。
    """
    fp = _mutated(lambda t: t["rubric_gate"][0]["options"].__setitem__("non_empty", False))
    assert fp != rubric_fingerprint(_task())


def test_depends_on_change_moves_the_fingerprint():
    """依赖变化会改 blocked 的传播范围（blocked 计 0 分）。"""
    fp = _mutated(lambda t: t["rubric_credit"][0].__setitem__("depends_on", []))
    assert fp != rubric_fingerprint(_task())


# ── 必须变：不动分数、但动归因的字段（本文件最重要的三条） ──────────────────

def test_skill_id_change_moves_the_fingerprint_even_though_score_is_unchanged():
    """`skill_id` 不改 ratio，但改了技能的掌握度 P（P 按技能聚合）。

    如果指纹漏掉它，就会出现"判据改了、分数没变、于是没人重评"——而这正好是
    B-04d 护栏要防的那类静默漏算。这条测试锁的是这个论点，不是实现细节。
    """
    original = _task()
    fp = _mutated(lambda t: t["rubric_credit"][0].__setitem__("skill_id", "failure_retry"))
    assert fp != rubric_fingerprint(original)


def test_error_patterns_change_moves_the_fingerprint():
    """错误模式决定诊断结论与技能缺口，改它等于改"这份提交被读成什么毛病"。"""
    fp = _mutated(lambda t: t["rubric_credit"][0].__setitem__("error_patterns", ["wrong_total_amount"]))
    assert fp != rubric_fingerprint(_task())


def test_error_pattern_detection_change_moves_the_fingerprint():
    def _drop_first_case(t: dict) -> None:
        t["rubric_credit"][0]["error_pattern_detection"]["cases"].pop(0)

    assert _mutated(_drop_first_case) != rubric_fingerprint(_task())


# ── 必须变：数组顺序（执行器按顺序执行，depends_on 要求前置先算） ──────────

def test_credit_order_change_moves_the_fingerprint():
    def _swap(t: dict) -> None:
        t["rubric_credit"].reverse()

    assert _mutated(_swap) != rubric_fingerprint(_task())


# ── 必须不变：纯等价写法 ───────────────────────────────────────────────────

def test_key_order_does_not_move_the_fingerprint():
    """YAML 里调换键的书写顺序是纯格式改动，判分逻辑一字未变。"""

    def _reorder(t: dict) -> None:
        for layer in ("rubric_gate", "rubric_credit"):
            for i, item in enumerate(t[layer]):
                t[layer][i] = {k: item[k] for k in reversed(list(item))}

    assert _mutated(_reorder) == rubric_fingerprint(_task())


def test_yaml_comments_do_not_move_the_fingerprint(tmp_path: Path):
    """注释不是数据，加载器根本不读——所以它进不了指纹。

    这条测的是加载链路，但它锁住的是一个对外承诺：**改注释不算改判据**，
    否则每次给 rubric 补一句评审说明都会触发一次全库重评。
    """
    text = TASK_PATH.read_text(encoding="utf-8")
    marker = "rubric_credit:\n"
    assert marker in text
    commented = text.replace(marker, marker + "  # 这是一行测试注释，不该影响指纹\n", 1)
    patched = tmp_path / TASK_PATH.name
    patched.write_text(commented, encoding="utf-8")

    assert rubric_fingerprint(load_task(patched)) == rubric_fingerprint(_task())


# ── 覆盖全库：每个任务都能算出指纹，且变体与母本不同 ────────────────────────

def test_every_task_in_the_library_has_a_fingerprint():
    paths = sorted(glob.glob(str(ROOT / "tasks" / "*.yaml")))
    assert len(paths) >= 10, "任务库应有 ≥10 个任务（B-05）"
    fingerprints = {}
    for path in paths:
        fp = rubric_fingerprint(load_task(path))
        assert len(fp) == 64, f"{Path(path).name} 的指纹长度异常"
        fingerprints[Path(path).name] = fp
    duplicated = [name for name, fp in fingerprints.items() if list(fingerprints.values()).count(fp) > 1]
    assert not duplicated, f"这些任务判据完全相同，疑似变体没真正继承差异：{duplicated}"


def test_variant_differs_from_its_parent():
    """变体靠"只写差异"生成；指纹不同是最便宜的一条"差异真的存在"的证据。"""
    parent = rubric_fingerprint(load_task(ROOT / "tasks" / "dbg-001-order-report.yaml"))
    variant = rubric_fingerprint(load_task(ROOT / "tasks" / "dbg-002-invoice-report.yaml"))
    assert parent != variant


# ── 不许给不合法的 rubric 起名字 ───────────────────────────────────────────

def test_refuses_to_fingerprint_an_invalid_rubric():
    """指纹会调 `rubric_layers()`，所以校验不通过时抛错而不是返回一个值。

    理由：一个"本身不成立的标准"如果也有指纹，它就会在库里跟合法标准长得一样，
    之后没有任何办法区分"这两份提交用了不同标准"还是"其中一份用了坏标准"。
    """

    def _empty_gate(t: dict) -> None:
        t["rubric_gate"] = []

    task = copy.deepcopy(_task())
    _empty_gate(task)
    with pytest.raises(ReportError):
        rubric_fingerprint(task)
