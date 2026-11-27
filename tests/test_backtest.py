"""掌握度 P 的 AUC 回测（`backtest.py`）的判据锁。

四组，每组挡一种具体的错：

1. **AUC 本身算错**：完美/全反/全并列/手算档位——尤其是并列要取平均秩，
   `上次结果` 基线只有 0/1 两个值，并列极多，算错会让基线虚高。
2. **有泄漏**：先更新再预测等于拿答案预测答案，AUC 会接近 1。本文件既验证
   "打乱标签会掉回 0.5"（检漏手段有效），也验证"故意写反顺序会被抓到"。
3. **指标选错**：全局平均的 Brier 比 P 低、AUC 只有 0.48。若只看 Brier，
   会选出一个"永远说 0.5、对排序毫无用"的模型。
4. **结论漂了**：首练无信息、第 2 次起才赢"上次结果"、天花板在 0.77~0.80。
   这三条是 `docs/BACKLOG.md` B-07b 写参数标定的依据，改生成器就要重新对。

所有阈值都先跑过再写（`--rounds 20 --skills 2`，20 个种子），不是凭印象预填。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import backtest as b  # noqa: E402
from arena.mastery import update_p  # noqa: E402

DENSE = {"rounds": 20, "skills": 2}      # 每人每技能约 10 次，用来让 P 有历史可累积


# ── 1. AUC 本身 ──────────────────────────────────────────────────────────────


def test_perfect_and_reversed_rankings():
    assert b.auc([0.1, 0.5, 0.9], [False, False, True]) == 1.0
    assert b.auc([0.9, 0.5, 0.1], [False, False, True]) == 0.0


def test_all_ties_is_exactly_one_half():
    """全并列 = 没有区分力 = 0.5。给 0 或 1 都是算错。"""
    assert b.auc([0.5] * 6, [True, False, True, False, True, False]) == 0.5


def test_ties_use_average_ranks():
    """手算：正样本在 0.5 与 0.9，负样本在 0.5。

    0.5 那对算 0.5 分，0.9 那对算 1 分 ⇒ AUC = (0.5 + 1) / 2 = 0.75。
    若按排序先后给秩（不取平均），会算成 0.5——那个实现会让"大量并列的基线"
    看起来更弱，从而高估模型。
    """
    assert b.auc([0.5, 0.5, 0.9], [True, False, True]) == 0.75


def test_auc_needs_both_classes():
    with pytest.raises(ValueError):
        b.auc([0.1, 0.2], [True, True])


def test_length_mismatch_is_rejected():
    with pytest.raises(ValueError):
        b.auc([0.1, 0.2], [True])
    with pytest.raises(ValueError):
        b.brier([0.1, 0.2], [True])


# ── 2. 泄漏：既验检漏手段，也验"顺序写反会被抓" ──────────────────────────────


def test_shuffling_the_outcomes_collapses_the_model_to_random():
    """打乱标签后 AUC 必须回到 0.5 附近。

    这条是整套回测的"检漏开关"：如果有任何一处用了本次结果，打乱标签也打不乱它，
    AUC 会停在 0.7 以上。20 个种子均值留 ±0.06 的余量（实测约 0.49 ~ 0.51）。
    """
    normal = [b.evaluate(seed, **DENSE)["P"]["auc"] for seed in range(20)]
    shuffled = [b.evaluate(seed, shuffle_labels=True, **DENSE)["P"]["auc"] for seed in range(20)]
    mean_normal = sum(normal) / len(normal)
    mean_shuffled = sum(shuffled) / len(shuffled)

    assert abs(mean_shuffled - 0.5) < 0.06, f"打乱后仍有信号：{mean_shuffled:.3f}"
    assert mean_normal - mean_shuffled > 0.05, "没打乱和不打乱差不多，说明模型本身没在学东西"


def test_updating_before_predicting_shows_up_as_a_near_perfect_auc():
    """把顺序写反（先更新、再预测），AUC 会窜到 0.9 以上——这正是必须被抓住的错误。

    这里刻意内联实现一份"错误版"，证明判据对这个错误敏感：
    如果哪天有人重写 `_walk` 时把两行调换，`evaluate` 会立刻出现 0.91 的 AUC，
    而不是悄悄变好（实测 0.913；正常是 0.63）。

    注意它**不会到 1.0**：λ=0.3 的平滑把本次结果只挪进去三成，所以泄漏被"稀释"了。
    这意味着只看"有没有到 1.0"是抓不住它的——阈值必须贴在正常值附近。
    """
    attempts = b.simulate(0, **DENSE)
    labels = [a.passed for a in attempts]
    p: dict[tuple[str, str], float] = {}
    scores: list[float] = []
    for attempt, label in zip(attempts, labels):
        key = (attempt.user, attempt.skill)
        previous = p.get(key)
        p[key] = float(label) if previous is None else update_p(previous, float(label))
        scores.append(p[key])          # ← 错：更新之后才"预测"
    assert b.auc(scores, labels) > 0.88


# ── 3. 指标选择：只看 Brier 会选出一个对排序无用的预测器 ────────────────────


def test_brier_alone_would_pick_a_useless_predictor():
    """全局平均的 Brier 更低、AUC 却在 0.48 左右：它靠"永远说 0.5"换来低误差。

    这条锁住"主判据是 AUC"：用途是排序（谁该复习），不是报概率。
    """
    rows = [b.evaluate(seed, **DENSE) for seed in range(12)]
    mean = lambda name, metric: sum(row[name][metric] for row in rows) / len(rows)  # noqa: E731

    assert mean("global", "brier") < mean("P", "brier"), "前提变了：全局平均的 Brier 不再更低"
    assert mean("global", "auc") < 0.55, "全局平均居然有排序能力，先查是不是有泄漏"
    assert mean("P", "auc") > mean("global", "auc") + 0.10


# ── 4. 结论：首练无信息、第 2 次起才赢、天花板在哪 ──────────────────────────


def test_first_attempt_carries_no_information_but_the_ceiling_does():
    """首练 P 恒为 0.5（没有历史），而天花板有 0.77 左右。

    这两件事一起读才成立：不是"第一次本来就预测不了"，而是"我们没用上任何
    可用的信息"（该生的其他技能）。这是 B-07b/跨技能先验的动机。
    """
    strata = [b.stratified(seed, **DENSE) for seed in range(12)]
    first_p = sum(s[0]["P"] for s in strata) / len(strata)
    first_ceiling = sum(s[0]["ceiling"] for s in strata) / len(strata)

    assert first_p == 0.5, "首练预测不是中性的 0.5，说明某处偷偷用了数据"
    assert first_ceiling > 0.70, f"首练的天花板只有 {first_ceiling:.3f}，结论要重写"


def test_model_beats_last_outcome_only_after_two_rounds():
    """分层事实：0/1 次时不赢，2 次起小赢，3 次以上拉开约 0.08。

    这三档是复习调度的"何时可以信 P"的依据。判据用 3 次+ 这一档（样本最多、最稳），
    阈值留给 0.03 的余量（实测 ≈ 0.08）。
    """
    strata = [b.stratified(seed, **DENSE) for seed in range(20)]
    mean = lambda cap, name: sum(s[cap][name] for s in strata) / len(strata)  # noqa: E731

    assert mean(0, "P") == 0.5, "首练必须是没有信息的中性值"
    assert mean(1, "P") == mean(1, "last"), "1 次时 P 就是播种值，与「上次结果」必然相等"
    assert mean(1, "P") < 0.60, "1 次时不该有强信号"
    assert mean(3, "P") > mean(3, "last") + 0.03, "3 次以上该明显赢过「上次结果」"
    assert mean(3, "ceiling") > mean(3, "P") + 0.08, "天花板与模型挨得太近，说明噪声设定被改小了"


def test_forgetting_is_invisible_to_auc():
    """`forget=0` 与 `forget=0.15` 的 AUC 相同——这是指标性质，不是 bug。

    锁住它，是为了让"接不接衰减"这件事的判据必须落到 Brier 上。
    哪天有人拿 AUC 去论证"衰减没用/有用"，这条会提醒他指标选错了。
    """
    without = b.run(6, forget=0.0, **DENSE)
    with_forget = b.run(6, forget=0.15, **DENSE)
    assert abs(without["auc"]["P"]["mean"] - with_forget["auc"]["P"]["mean"]) < 0.02


def test_every_seed_stays_above_random():
    """不许"平均好看、个别种子翻车"。

    实测最差种子 0.572（密集设定）；阈值放到 0.5 以上一点，只挡"整体失效"。
    """
    results = [b.evaluate(seed, **DENSE)["P"]["auc"] for seed in range(20)]
    assert min(results) > 0.52, f"有种子掉到随机水平：{min(results):.3f}"
