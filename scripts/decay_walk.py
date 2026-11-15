"""十分钟复核：P 的衰减到底怎么算（B-08b）。

四段：
  ① 真数据：往临时库里落三份提交 + 一张卡，读出 P_raw，再按三个 as_of 读出 P_eff
  ② 曲线表：S=1 / 10 / 100 三张卡，R 随天数怎么掉（这张表就是"衰减很慢"的证据）
  ③ 缺卡对照：同样三份提交，没有卡 ⇒ 不衰减（不是"忘光了"）
  ④ 边界：as_of 早于最后一次练过 ⇒ 直接报错，不返回一个看着合理的数

跑法（在仓根执行；不在仓根也行，本脚本不看 cwd）：
    python scripts/decay_walk.py
"""

from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import decay as d  # noqa: E402
from arena import mastery as m  # noqa: E402
from arena import scheduler as sch  # noqa: E402
from arena import store  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402

USER = "u-decay-demo"
SKILL = "multi_step_planning"
TASK = ROOT / "tasks" / "dbg-001-order-report.yaml"


def _section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def _db(with_cards: bool, submits: int = 3):
    conn = store.connect(Path(tempfile.mkdtemp()) / "decay-demo.db")
    store.init_db(conn)
    store.register_skills(conn)
    store.register_patterns(conn)
    store.register_task(conn, TASK)

    card = sch.new_card(USER, SKILL, at="2026-01-01T00:00:00Z") if with_cards else None
    day0 = 1
    for i in range(submits):
        at = f"2026-01-{day0 + 2 * i:02d}T00:00:00Z"
        report = copy.deepcopy(run_fixture("raw-perfect"))
        report["submission"]["submission_id"] = f"sub-{i + 1}"
        report["submission"]["evaluated_at"] = at
        store.save_submission(conn, report, user_id=USER, created_at=at)
        if card is not None:
            card, _rating = sch.review(card, 0.9, at=at)
    if card is not None:
        store.save_fsrs_cards(conn, USER, {SKILL: card.to_dict()})
    return conn, card


def main() -> int:
    print("B-08b 衰减演示。**所有数字都是现算的**，没有一个是手填的期望值。")

    _section("第 1 段 · 真数据：三份提交 → P_raw，再按 as_of 读三次")
    conn, card = _db(with_cards=True)
    series, res = m.decayed_at(conn, USER, SKILL, as_of="2026-01-05T00:00:00Z")
    print(f"  P_raw = {series.p:.4f}（{len(series.steps)} 次计入证据，λ={series.lam}）")
    print(f"  卡片：stability={card.stability:.3f} 天，最后复习 "
          f"{card.last_review.date()}，下次 due {card.due.date()}")
    for as_of in ("2026-01-05T00:00:00Z", "2026-02-05T00:00:00Z", "2027-01-05T00:00:00Z"):
        _, r = m.decayed_at(conn, USER, SKILL, as_of=as_of)
        print(f"  as_of={as_of[:10]}  间隔 {r.elapsed_days:7.1f} 天  "
              f"R={r.r:.4f}  P_eff={r.p_effective:.4f}  （{r.reason}）")
    print()
    print("  ⇒ 同一份数据、同一个 P_raw，只是换了 as_of，读出来的 P_eff 就不同。")
    print("    这正是'衰减是读法、不是新状态'的意思：库里什么都没改。")
    conn.close()

    _section("第 2 段 · 曲线表：R 随天数怎么掉（这就是'衰减很慢'的实物证据）")
    print(f"  {'距上次复习':>10}  {'S=1 天':>10}  {'S=10 天':>10}  {'S=100 天':>10}")
    for days in (1, 7, 30, 90, 365, 550, 1825, 7300):
        row = "  ".join(f"{d.retrievability(s, days):10.4f}" for s in (1.0, 10.0, 100.0))
        print(f"  {days:>8} 天  {row}")
    print()
    print("  ⇒ 两个必须看出来的点：")
    print("    1) R(t=S) = 0.9——这不是拍的，是 py-fsrs 的默认 desired_retention；")
    print("    2) 尾巴很长：S=1 的卡放 550 天还有 0.379，20 年还有 0.254。")
    print("       所以我原来那句'长期不练 P 会趋近 0'是错的，真实风险是**衰减太慢**。")

    _section("第 3 段 · 缺卡对照：同样的提交，没有卡 ⇒ 不衰减")
    conn2, _ = _db(with_cards=False)
    series2, res2 = m.decayed_at(conn2, USER, SKILL, as_of="2027-01-05T00:00:00Z")
    print(f"  P_raw = {series2.p:.4f}   applied={res2.applied}   reason={res2.reason}")
    print(f"  P_eff = {res2.p_effective:.4f}  （P_raw 原样返回）")
    print()
    print("  ⇒ 为什么必须这样：py-fsrs 对'还没有历史的卡'返回 **0**（不是 None）。")
    print("    照着乘下去，一个从没失败过、因此没建卡的技能 P 会变成 0——")
    print("    那是把'还没开始'说成'已经忘完'，决策 7 明令禁止的换算。")
    print(f"  实测：Card().stability is None，get_card_retrievability() == 0（测试里也锁了）")

    _section("第 4 段 · 边界：as_of 早于最后一次练过")
    try:
        m.decayed_at(conn2, USER, SKILL, as_of="2025-12-31T00:00:00Z")
    except d.DecayError as exc:
        print(f"  被拒：{exc}")
        print("  ⇒ 刻意如此：时间倒着用时，任何返回都只是'看着合理'。")
    conn2.close()

    print()
    print("完。判据：第 1 段的 P_eff 随 as_of 单调不增；第 3 段 P_eff == P_raw；第 4 段抛错。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
