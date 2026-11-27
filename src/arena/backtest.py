"""掌握度 P 能不能预测下一次——AUC 回测（B-07b 的「可证伪」一半）。

## 它证什么

`mastery.py` 只说"P 按公式算出来了"。公式算对不等于**有用**：如果 P 的高低和
"下一次做同技能的题会不会过"毫无关系，那复习层推题、排程层排序全建在噪声上。

所以这里做一次回测：造一批**已知底细**的学生，按真实概率作答，看 P 能不能把
"下一定会过的人"排到"下一定不会过的人"前面。判据是 AUC，不是"看起来合理"。

## 它不证什么（两句话，别读漏）

1. **不证真实遗忘曲线**。模拟里的遗忘是我设的（`forget` 参数），不是从真人数据估的。
   它证明的是"在有遗忘的世界里，不做时间衰减会掉分"——这是模型选择的依据，
   不是对真人参数的断言。
2. **不证判分准不准**。整条链路的输入是"这次过没过"，而"过没过"由判分器决定。
   判分器错了，这里的 AUC 照样好看。两件事分别在 `test_discrimination.py`
   与真人试跑里管。

## 为什么这不是自证

`P_k = (1-λ)·P_{k-1} + λ·c_k` 是**指数滑动平均**；而生成数据的机制是
**能力 + 题目难度 + 遗忘**的 logistic 抽样，两者的函数形式不一样。

这不是自我循环的关键：
- 如果生成器也写成 EMA，那"EMA 能预测 EMA"是恒真的废话；
- 现在是"一个带干扰项（题目难度我控制、模型看不到）的世界里，EMA 还能不能排对人"。
  模型必须在**自己不知道难度**的条件下工作，和线上一样。

## 三条基线，都得打赢

| 基线 | 它代表"不做这件事"的哪种做法 |
|---|---|
| 随机（0.5） | 完全不管，AUC 期望 0.5 |
| 全局平均 | 不看技能结构，所有人所有技能的历史通过率混在一起 |
| 上次结果 | 只看最近一次过没过，不累积、不遗忘 |

只跟随机比是不够的：谁都能赢随机。要赢"上次结果"才说明**累积**有用；
要赢"全局平均"才说明**按技能分开算**有用。

## 无泄漏

每一步的预测只能用到**这一步之前**的信息。这是本模块最容易写错的地方，也是
唯一会让结果虚高的地方（先更新再预测，等于用答案预测答案）。
判据见 `tests/test_backtest.py::test_shuffling_the_outcomes_collapses_the_model_to_random`：
把结果标签打乱之后，AUC 必须掉回 0.5 附近——有泄漏就掉不回去。

## 2026-11-27 实测（`--rounds 20 --skills 2`，20 个种子，λ=0.3，forget=0.15）

| 该技能已练 | P 的 AUC | 上次结果 | 天花板 |
|---|---|---|---|
| 0 次 | 0.500 | 0.500 | 0.769 |
| 1 次 | 0.575 | 0.575 | 0.782 |
| 2 次 | 0.594 | 0.549 | 0.791 |
| 3 次+ | 0.651 | 0.571 | 0.793 |

三句话，都对这个模型不利，但都是可复现的事实：

1. **首练是瞎猜**（0.500），而天花板有 0.77——说明"这个人的其他技能表现"本可提供信号，
   现在一点没用上。跨技能先验的对照实验在"技能独立"的模拟里**无效**（0.495），
   因为模拟假设了技能无关；真实世界里技能相关有多大，只能靠真人数据回答。
2. **第 2 次起 P 才赢"上次结果"**，3 次以上拉开 0.08。所以复习调度不该在只有 1 次记录时
   按 P 排序——那是按噪声排序。
3. **遗忘在 AUC 上看不出来**（forget=0 与 0.15 的 AUC 完全相同，因为 AUC 只对排序敏感、
   不对整体通过率敏感）。要判断"该不该接衰减"，判据必须换成 Brier。这条本身是个结论：
   选错指标会让"衰减有没有用"永远测不出来。"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

if __package__ in (None, ""):          # 允许 `python src/arena/backtest.py` 直接跑
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from arena.mastery import LAMBDA_DEFAULT, update_p
else:
    from .mastery import LAMBDA_DEFAULT, update_p

#: 难度噪声。模型**看不到**它，所以这是 AUC 的天花板来源（不是 bug）。
DIFFICULTY_SIGMA = 0.6

#: 每轮之间的间隔（天）。遗忘按"距上次练这个技能多久"算，不是按全局时间。
ROUND_GAP_DAYS = 3.0


@dataclass(frozen=True)
class Attempt:
    """模拟里的一次作答。`passed` 是标签，其余是生成时用到的隐藏量。

    `true_p` 是生成这份数据时的**真实通过概率**（能力 − 遗忘 − 难度）。它不是任何
    预测器能看到的量——只用来算天花板：若真实概率都排不准人，说明这道题的难度噪声
    本身就是天花板，此时"P 只有 0.63"不该被读成"P 很差"。
    """

    user: str
    skill: str
    day: float
    gap: float          # 距该用户上一次练这个技能的天数；第一次为 0
    difficulty: float
    true_p: float
    passed: bool


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def simulate(seed: int, *, users: int = 40, skills: int = 3, rounds: int = 8,
             forget: float = 0.15, difficulty_sigma: float = DIFFICULTY_SIGMA) -> list[Attempt]:
    """造一条"已知底细"的作答流水。

    `forget` 是每空一天掉多少能力：0 表示不遗忘（此时 EMA 的"不看时间"不吃亏），
    0.15 表示三天不练掉 0.45 个标准差——这是**假设值**，用来区分"要不要接衰减"，
    不要当成对真人的断言。

    学生之间、技能之间的能力都是独立抽的：真实数据里技能相关（会算数的人多半也会核对），
    这里故意不建模，因为它只会让 P 更好看，属于往有利方向放宽。
    """
    rnd = random.Random(seed)
    out: list[Attempt] = []
    for index in range(users):
        user = f"u{index:02d}"
        ability = {f"s{k}": rnd.gauss(0.0, 1.0) for k in range(skills)}
        last_day: dict[str, float] = {}
        for step in range(rounds):
            skill = f"s{rnd.randrange(skills)}"
            day = step * ROUND_GAP_DAYS
            gap = 0.0 if skill not in last_day else day - last_day[skill]
            last_day[skill] = day
            effective = ability[skill] - forget * gap
            difficulty = rnd.gauss(0.0, difficulty_sigma)
            true_p = _sigmoid(effective - difficulty)
            passed = rnd.random() < true_p
            out.append(Attempt(user=user, skill=skill, day=day, gap=gap,
                               difficulty=difficulty, true_p=true_p, passed=passed))
    return out


def auc(scores: list[float], labels: list[bool]) -> float:
    """ROC AUC，用平均秩算（不用库）。

    **并列必须取平均秩**：`上次结果` 基线只有 0/1 两个取值，大量并列；
    按先后顺序给秩会把并列拆成"有差别"，AUC 系统性偏高——那种偏高会让人以为
    基线很强，从而低估模型。
    """
    n = len(scores)
    if n != len(labels):
        raise ValueError(f"scores 与 labels 长度不同：{n} vs {len(labels)}")
    positives = sum(1 for value in labels if value)
    negatives = n - positives
    if positives == 0 or negatives == 0:
        raise ValueError("AUC 需要正负样本都有；只有一类时它没有定义")

    order = sorted(range(n), key=lambda i: scores[i])
    ranks = [0.0] * n
    start = 0
    while start < n:
        end = start
        while end + 1 < n and scores[order[end + 1]] == scores[order[start]]:
            end += 1
        average_rank = (start + end) / 2.0 + 1.0     # 1-based 平均秩
        for position in range(start, end + 1):
            ranks[order[position]] = average_rank
        start = end + 1

    rank_sum = sum(ranks[i] for i in range(n) if labels[i])
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def brier(scores: list[float], labels: list[bool]) -> float:
    """均方误差（概率校准）。**AUC 看排序、Brier 看"说的概率准不准"**。

    两个都要：复习时机取决于"他现在还剩多少"，那是概率问题，不是排序问题。
    只看 AUC 会让"永远输出 0.9"这种不校准的模型看起来没事。
    """
    if len(scores) != len(labels):
        raise ValueError(f"scores 与 labels 长度不同：{len(scores)} vs {len(labels)}")
    if not scores:
        raise ValueError("空序列没有 Brier 分数")
    return sum((float(s) - float(y)) ** 2 for s, y in zip(scores, labels)) / len(scores)


def _walk(attempts: list[Attempt], labels: list[bool], *,
          lam: float = LAMBDA_DEFAULT) -> list[dict]:
    """逐步走一遍，返回每个预测点的**预测值 + 该技能此前练过几次 + 标签**。

    这是本模块唯一的正确性要害：**先预测、再更新**。反过来等于拿本次结果预测本次，
    AUC 会虚高到接近 1，而且看起来毫无异常。所以顺序只在这里写一次，
    `evaluate()` 与 `stratified()` 都从这一份记录读，不各写一套循环。

    `seen` 是"该用户该技能此前练过几次"，用来分层：0 次时的预测是纯瞎猜（固定 0.5），
    把它和"练过 5 次"混在一个 AUC 里，会得到一个既不代表新手也不代表老手的中间数。
    """
    p_by_key: dict[tuple[str, str], float] = {}
    last_by_key: dict[tuple[str, str], float] = {}
    seen_by_key: dict[tuple[str, str], int] = {}
    global_passed = 0
    global_seen = 0
    rows: list[dict] = []

    for attempt, label in zip(attempts, labels):
        key = (attempt.user, attempt.skill)
        seen = seen_by_key.get(key, 0)
        rows.append({
            "user": attempt.user, "skill": attempt.skill, "seen": seen,
            # 产品里"没有证据"是 None、不进递推；回测需要一个预测值，取 0.5 是中性选择，
            # 且对模型不利（第一次必错一半），不会美化结果。
            "P": p_by_key.get(key, 0.5),
            "last": last_by_key.get(key, 0.5),
            "global": (global_passed / global_seen) if global_seen else 0.5,
            "ceiling": attempt.true_p,      # 用了本次隐藏量，不可实现，只当天花板
            "y": label,
        })
        previous = p_by_key.get(key)
        p_by_key[key] = float(label) if previous is None else update_p(previous, float(label), lam=lam)
        last_by_key[key] = float(label)
        seen_by_key[key] = seen + 1
        global_seen += 1
        global_passed += int(label)

    return rows


def evaluate(seed: int, *, lam: float = LAMBDA_DEFAULT, shuffle_labels: bool = False,
             **sim_kwargs) -> dict:
    """跑一条流水，返回各预测器的 AUC 与 Brier。

    `shuffle_labels=True` 是本模块的"检漏开关"：打乱标签后若 AUC 不回到 0.5，
    说明某处用了本次结果（有泄漏）。

    `oracle` 不是可实现的预测器：它直接读生成数据时的真实概率。它只回答一个问题——
    **这道题的噪声天花板在哪**。P 与天花板之间的差才是"模型还能改多少"，
    P 与"上次结果"之间的差才是"累积到底值不值"。
    """
    attempts = simulate(seed, **sim_kwargs)
    labels = [a.passed for a in attempts]
    if shuffle_labels:
        rnd = random.Random(seed + 10_000)
        rnd.shuffle(labels)

    rows = _walk(attempts, labels, lam=lam)
    names = ("P", "global", "last", "ceiling")
    got = [row["y"] for row in rows]
    return {name: {"auc": auc([row[name] for row in rows], got),
                   "brier": brier([row[name] for row in rows], got)}
            for name in names}


def stratified(seed: int, *, lam: float = LAMBDA_DEFAULT, **sim_kwargs) -> dict:
    """按"该技能此前练过几次"分层给 AUC：0 / 1 / 2 / 3次及以上。

    为什么要分层：第一次的预测按定义是 0.5，把它和练过五次的人平均在一起，
    得到的数既不代表新手也不代表老手，还会随时间稀释——它只会让"P 到底什么时候
    开始有用"这个问题永远答不出来。

    某一层只有一类标签时 AUC 无定义，返回 `None`；不要拿 0.5 冒充"随机"。
    """
    attempts = simulate(seed, **sim_kwargs)
    rows = _walk(attempts, [a.passed for a in attempts], lam=lam)
    out: dict[int, dict] = {}
    for cap in (0, 1, 2, 3):
        selected = [row for row in rows if min(row["seen"], 3) == cap]
        entry: dict = {"n": len(selected)}
        for name in ("P", "last", "ceiling"):
            try:
                entry[name] = auc([row[name] for row in selected], [row["y"] for row in selected])
            except ValueError:
                entry[name] = None
        out[cap] = entry
    return out


def run(trials: int = 20, *, forget: float = 0.15, lam: float = LAMBDA_DEFAULT,
        **sim_kwargs) -> dict:
    """多种子跑一遍，给均值、中位、最差、最好。

    **必须报最差**：只报均值时，20 个种子里有 2 个低于基线也能说"平均更好"。
    判据写成"每一个种子都赢"，才挡得住"挑种子"。
    """
    rows = [evaluate(seed, lam=lam, forget=forget, **sim_kwargs) for seed in range(trials)]

    def stat(name: str, metric: str) -> dict:
        values = sorted(row[name][metric] for row in rows)
        return {
            "mean": sum(values) / len(values),
            "min": values[0],
            "max": values[-1],
            "median": values[len(values) // 2],
        }

    names = ("P", "global", "last", "ceiling")
    return {
        "trials": trials, "forget": forget, "lam": lam,
        "auc": {name: stat(name, "auc") for name in names},
        "brier": {name: stat(name, "brier") for name in names},
    }


def _fmt(data: dict) -> str:
    lines = [f"{data['trials']} 个种子，遗忘系数 {data['forget']}，λ={data['lam']}", ""]
    labels = {"P": "掌握度 P", "global": "全局平均", "last": "上次结果", "ceiling": "天花板(不可用)"}
    lines.append(f"{'预测器':<14}{'AUC均值':>8}{'AUC中位':>8}{'AUC最差':>8}{'Brier':>8}")
    for name in ("P", "last", "global", "ceiling"):
        a, b = data["auc"][name], data["brier"][name]
        lines.append(f"{labels[name]:<14}{a['mean']:>8.3f}{a['median']:>8.3f}"
                     f"{a['min']:>8.3f}{b['mean']:>8.4f}")
    lines.append("")
    lines.append("AUC 看排序（越大越好），Brier 看概率准不准（越小越好）。**两个一起读**：")
    lines.append("全局平均的 Brier 往往比 P 还低，但它的 AUC 在 0.48 左右——那是靠"
                 "「永远说 0.5」换来的低误差，对「谁该复习」毫无用。")
    lines.append("天花板不可实现，只说明这道题的噪声上限；P 与它的差才是模型还能改的空间。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="掌握度 P 的 AUC 回测（B-07b）")
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--forget", type=float, default=0.15, help="每空一天掉多少能力；0 = 不遗忘")
    parser.add_argument("--lam", type=float, default=LAMBDA_DEFAULT)
    parser.add_argument("--users", type=int, default=40)
    parser.add_argument("--skills", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=8)
    args = parser.parse_args(argv)

    data = run(args.trials, forget=args.forget, lam=args.lam,
               users=args.users, skills=args.skills, rounds=args.rounds)
    print(_fmt(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
