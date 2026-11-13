"""决策演示台的自检（round-72）。

演示台的价值全部押在一件事上：**它会不会红**。一个从不报红的检查表，和没有检查表
是一回事。所以这里不测"演示台输出好看"，只测两件事：

1. 现状下 11 条都不得为 FAIL（否则等于宣称作废）；
2. 对实现做**定向破坏**后，对应演示必须变红。

第 2 类才是真正的外部裁判：破坏点由我挑，但"变红"不是我说的，是断言说的。
用 monkeypatch 在进程内破坏，不碰任何产品代码与夹具文件。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import demo_decision as dd  # noqa: E402
from arena import diagnosis_report as dr  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 现状：没有 FAIL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", sorted(dd.DEMOS))
def test_demo_is_not_fail_now(n):
    """任何一条在干净仓库上就该不是 FAIL；红了说明决策未落地或演示过期。"""
    verdict, lines = dd.DEMOS[n]()
    assert verdict != dd.FAIL, f"决策 {n} 判 FAIL：\n" + "\n".join(lines)


def test_partial_and_noexec_are_not_counted_as_pass():
    """PARTIAL / NOT_EXECUTABLE 不许被当成 PASS——四档必须互斥。

    round-75 订正：NOEXEC 从 1 降到 0（M1 建了 `review_items` 表，决策 3 从"无载体"
    变成"有形态、无读写"，档位由 NOT_EXECUTABLE 升到 PARTIAL）。这是**有载体的演进**，
    不是把降级档偷偷升成 PASS，所以不再断言"NOEXEC 必然存在"；
    保留"必须有降级档"这条底线——若哪天全变 PASS，这条仍会红。
    """
    verdicts = {dd.DEMOS[n]()[0] for n in dd.DEMOS}
    assert verdicts <= {dd.PASS, dd.PARTIAL, dd.NOEXEC, dd.FAIL}
    assert dd.PARTIAL in verdicts


def test_decision_3_stays_partial_until_review_items_are_written():
    """表建了但没有任何代码读写 `review_items` ⇒ 决策 3 只能是 PARTIAL。

    这条红了的正常原因是 M4 真的落地了（有 INSERT/SELECT review_items）。
    那时候把断言改成 PASS 并写清依据，**不要直接删掉**——删掉之后就没有东西
    阻止"建了张表就宣布复习闭环完成"。
    """
    assert dd.DEMOS[3]()[0] == dd.PARTIAL


def test_exit_code_zero_when_no_fail():
    assert dd.main(["--all"]) == 0


def test_unknown_decision_id_is_rejected():
    assert dd.main(["99"]) == 2


# ---------------------------------------------------------------------------
# 2. 变异：破坏了就必须红
# ---------------------------------------------------------------------------


@pytest.fixture()
def _mute_prints(monkeypatch, capsys):
    """演示台输出很长，测试里静音；失败时 pytest 仍会显示断言消息。"""
    monkeypatch.setattr(dd, "say", lambda *a, **k: None)
    return capsys


def test_mutation_counterfactual_zero_turns_demo5_red(monkeypatch, _mute_prints):
    """M1：把反事实得分抹成 0 = 门禁失败后连观测都不留（决策 5 的反面）。"""
    real = dr.build_report

    def fake(task, raw, vocab=None):
        report = real(task, raw, vocab)
        report["score"]["counterfactual_credit"] = 0
        return report

    monkeypatch.setattr(dr, "build_report", fake)
    verdict, _ = dd.demo_5()
    assert verdict == dd.FAIL


def test_mutation_ignore_process_gate_turns_demo9_red(monkeypatch, _mute_prints):
    """M2：让「缺过程」永不成立 = 决策 9 的空转实现。"""
    monkeypatch.setattr(dr, "_process_fact",
                        lambda task, raw, trace_ok: {"required": True,
                                                     "submitted": True,
                                                     "missing": False})
    verdict, _ = dd.demo_9()
    assert verdict == dd.FAIL


def test_mutation_voided_always_false_turns_demo11_red(monkeypatch, _mute_prints):
    """M3：作废逻辑失效（版本对不上也照常算分）= 决策 11 未被执行。"""
    real = dr.build_report

    def fake(task, raw, vocab=None):
        report = real(task, raw, vocab)
        report["score"]["voided"] = False
        report["score"].pop("void_reason", None)
        return report

    monkeypatch.setattr(dr, "build_report", fake)
    verdict, _ = dd.demo_11()
    assert verdict == dd.FAIL


def test_mutation_missing_data_counted_turns_demo7_red(monkeypatch, _mute_prints):
    """M4：让 unscored 计入掌握度 = 把「数据缺失」记成「能力不足」（决策 7 的反面）。"""
    real = dr.build_report

    def fake(task, raw, vocab=None):
        report = real(task, raw, vocab)
        report["attribution_policy"]["unscored"]["counts_toward_mastery"] = True
        return report

    monkeypatch.setattr(dr, "build_report", fake)
    verdict, _ = dd.demo_7()
    assert verdict == dd.FAIL


def test_mutation_gate_weight_turns_demo4_red(monkeypatch, _mute_prints):
    """M5：给门禁条目加权重 = 两段式退化成单段加权和。"""
    real = dr.rubric_layers

    def fake(task):
        gate, credit = real(task)
        gate = [dict(r, weight=1) for r in gate]
        return gate, credit

    monkeypatch.setattr(dr, "rubric_layers", fake)
    verdict, _ = dd.demo_4()
    assert verdict == dd.FAIL


# ---------------------------------------------------------------------------
# 3. 期望值必须来自声明，不能写死
# ---------------------------------------------------------------------------


def test_credit_total_reads_from_task_declaration(monkeypatch):
    """分母读不到就该炸，不许默认 0 让判据静默变红。"""
    broken = {k: v for k, v in dd.TASK.items() if k != "scoring"}
    monkeypatch.setattr(dd, "TASK", broken)
    with pytest.raises(RuntimeError, match="total_weight"):
        dd._credit_total()


def test_decision_text_matches_packet():
    """标题现读评审包：改一行 YAML/文档不会让演示台说旧话。"""
    text = dd.decision_text(4)
    packet = (ROOT / "docs" / "review-packet.md").read_text(encoding="utf-8")
    assert "评分两段式 gate / credit" in text
    assert "评分两段式 gate / credit" in packet
    assert "**" not in text and "`" not in text, "终端显示不应带 markdown 标记"
