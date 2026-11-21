"""参数敏感性：λ 变了，**下游真正吃的东西**会不会变。

## 为什么数"排序"而不是数"P 变了多少"

外部评审（2026-11-21）建议"先做决策敏感性分析，再谈校准"。但 P 的绝对值没人直接用：
`schedule_advisor` 把 `1-P` 当 `mastery_gap` 因子（权重 1.0）参与排序，
用户看到的顺序才是决策。所以本脚本数的是**技能排序翻转次数**——
P 从 0.40 变到 0.45 但顺序没动，不算问题；顺序翻一次，才是真问题。

## 它不做什么

- 不改任何表、不写任何字段（只读重放）。
- 不宣称结论普适：演示数据只有 4 份提交、3 个技能，够看机制、不够定参数。
- 没卡的技能不假装算过衰减：`decay` 会给出 `no_card`，这里如实汇总原因，不当成"衰减后=P"。

## 用法

    python scripts/param_sensitivity.py                  # 内置演示数据
    python scripts/param_sensitivity.py --db x.db        # 读真实库（有 FSRS 卡时才算衰减维）
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from arena import mastery as m  # noqa: E402
from arena import store  # noqa: E402

# 演示数据的构造方式只有一处定义，复用成绩单脚本，避免两份"演示口径"漂移。
from mastery_scorecard import PLAN, TASK_YAML, USER, _report  # noqa: E402

DEFAULT_LAMBDAS = (0.1, 0.3, 0.5, 0.7, 0.9, 1.0)
DECAY_DAYS = (1, 7, 30, 90)


def build_demo(conn) -> None:
    """与成绩单同一套四份演示提交（满分 / 假重试 / 没产物 / 没过程）。"""
    from datetime import datetime, timezone

    from arena import scheduler as sch

    store.init_db(conn)
    store.register_skills(conn)
    store.register_patterns(conn)
    store.register_task(conn, TASK_YAML)
    base = datetime(2026, 11, 1, tzinfo=timezone.utc)
    for i, (fixture, _) in enumerate(PLAN, start=1):
        at = (base + timedelta(days=i)).isoformat()
        store.save_submission(
            conn, _report(fixture, f"demo-{i}", at), user_id=USER, created_at=at
        )

    # 造 FSRS 卡：否则衰减维一律 `no_card`（恒等），这一维等于没测。
    # S 由 scheduler.review() 用最后一条证据的 c 算出来，不手写——
    # 手写一个 S 等于替 FSRS 拍参数，测出来的就不是真实链路。
    sched = sch.make_scheduler()
    cards: dict[str, dict] = {}
    stamp = None
    for skill in store.list_skill_ids(conn):
        series = m.build_series(conn, USER, skill)
        if not series.steps:
            continue
        anchor = series.steps[-1].at
        card = sch.new_card(USER, skill, at=anchor)
        card, _ = sch.review(card, series.steps[-1].c, at=anchor, scheduler=sched)
        cards[skill] = card.to_dict()
        stamp = stamp or anchor
    if cards:
        store.save_fsrs_cards(conn, USER, cards, at=stamp)


def rank(values: dict[str, float | None]) -> list[str]:
    """按 P 降序排技能。None（没有证据）垫底，且**不与 0 混同**。"""
    return sorted(values, key=lambda k: (values[k] is None, -(values[k] or 0.0), k))


def pair_flips(a: list[str], b: list[str]) -> int:
    """两种排序之间，相对次序发生反转的技能对数量。"""
    def order(seq: list[str]) -> dict[tuple[str, str], bool]:
        pos = {s: i for i, s in enumerate(seq)}
        return {(x, y): pos[x] < pos[y] for i, x in enumerate(seq) for y in seq[i + 1:]}

    oa, ob = order(a), order(b)
    return sum(1 for key in oa if oa[key] != ob.get(key, oa[key]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="λ 与衰减时刻的排序敏感性")
    ap.add_argument("--db", help="真实库路径；不给则用内置演示数据")
    ap.add_argument("--user", default=USER)
    ap.add_argument("--lambdas", default=",".join(str(x) for x in DEFAULT_LAMBDAS))
    args = ap.parse_args(argv)

    lambdas = [float(x) for x in args.lambdas.split(",") if x.strip()]

    if args.db:
        conn = store.connect(Path(args.db))
        source = f"真实库 {args.db}"
    else:
        demo = ROOT / ".git" / "param-sensitivity-demo.db"
        demo.unlink(missing_ok=True)
        conn = store.connect(demo)
        build_demo(conn)
        source = "内置演示数据（4 份提交）"

    skills = store.list_skill_ids(conn)
    print(f"# 数据来源：{source}")
    print(f"# 技能 {len(skills)} 个，λ 网格 {lambdas}")
    print()

    table: dict[float, dict[str, float | None]] = {}
    for lam in lambdas:
        table[lam] = {
            skill: m.build_series(conn, args.user, skill, lam=lam).p for skill in skills
        }

    # ── 自检：λ=1.0 时递推退化为"直接取最后一条证据的 c"（数学上必然）。
    # 不等就说明脚本自己算错了，此时**不许**输出"看起来很稳"的结论。
    if 1.0 in lambdas:
        for skill in skills:
            series = m.build_series(conn, args.user, skill, lam=1.0)
            if not series.steps:
                continue
            if abs(series.p - series.steps[-1].c) > 1e-9:
                print(f"自检失败：{skill} λ=1 时 P={series.p} 应等于 c={series.steps[-1].c}")
                return 2
        print("自检通过：λ=1.0 时 P 等于最后一条计入证据的 c。")
        print()

    print("| 技能 | " + " | ".join(f"λ={lam}" for lam in lambdas) + " |")
    print("|---" * (len(lambdas) + 1) + "|")
    for skill in skills:
        cells = []
        for lam in lambdas:
            value = table[lam][skill]
            cells.append("—" if value is None else f"{value:.3f}")
        print(f"| {skill} | " + " | ".join(cells) + " |")
    print()

    base_lam = 0.3 if 0.3 in lambdas else lambdas[0]
    base_order = rank(table[base_lam])
    print(f"基准排序（λ={base_lam}）：{' > '.join(base_order)}")
    total_flips = 0
    for lam in lambdas:
        if lam == base_lam:
            continue
        flips = pair_flips(base_order, rank(table[lam]))
        total_flips += flips
        print(f"  λ={lam}: 相对次序反转 {flips} 对")
    print()

    # ── 衰减维：看 `P_eff = P × R` 会不会改变排序。
    # 外部评审说"掌握度衰减与 FSRS 会打架"——这是可测的：若 P 排序与 P_eff 排序不同，
    # 说明两个信号在排序上确实给出不一致的意见；若相同，至少在排序层面没打起来。
    series_best = {
        skill: m.build_series(conn, args.user, skill, lam=base_lam) for skill in skills
    }
    base_p_order = rank({skill: series_best[skill].p for skill in skills})
    print(f"衰减维（距各自最后一条证据 N 天，括号内是 FSRS 的 R）")
    decay_flips = 0
    for days in DECAY_DAYS:
        row: dict[str, float | None] = {}
        cells: list[str] = []
        for skill in skills:
            series = series_best[skill]
            if not series.steps:
                continue
            as_of = (
                m.parse_instant(series.steps[-1].at) + timedelta(days=days)
            ).isoformat()
            _, result = m.decayed_at(conn, args.user, skill, as_of=as_of, lam=base_lam)
            row[skill] = result.p_effective if result.applied else series.p
            cells.append(
                f"{skill}={row[skill]:.3f}(R={result.r:.2f})"
                if result.applied
                else f"{skill}={row[skill]:.3f}({result.reason})"
            )
        flips = pair_flips(base_p_order, rank(row))
        decay_flips += flips
        print(f"  +{days:>3} 天：{' · '.join(cells)} → 排序反转 {flips} 对")
    print()
    if decay_flips == 0:
        print("  衰减在网格内没有改变排序：'P 与 R 打架'在排序层面没有出现（不等于不会出现）。")
    else:
        print(f"  衰减改变了排序共 {decay_flips} 对：P 与 R 在排序上给出不一致的意见，需要指定权威。")
    print()

    verdict = (
        "λ 在给定网格内不改变技能排序（当前数据下，参数不是真问题）"
        if total_flips == 0
        else f"λ 改变技能排序共 {total_flips} 对（参数会影响决策，值得校准）"
    )
    print(f"结论：{verdict}；衰减维反转 {decay_flips} 对。")
    print("注意：本结论只覆盖此库此用户此技能集；要论证普适性，需要更多提交与更多技能。")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
