"""B-09 的十分钟验证：四因子建议排程到底怎么排的，自己算一遍能不能对上。

用法（不联网、不写正式库，只动 `.git/` 下的临时库与临时审计文件）：

    python scripts/advice_walk.py
    python scripts/advice_walk.py --flip      # 把权重反过来再排一遍，看哪些名次是权重敏感的

分两段，**第 1 段用真数据、第 2 段用算例**，这个分界不能含糊：

  第 1 段 真数据：真交几份提交（跑夹具）、真落库、真读回，看四个因子里
                  哪三个有值、哪个（项目截止）只能弃权。
  第 2 段 算例：五条构造的复习项，把综合分的算式一条条打出来——
                  因为现实数据下队列里最多两条（三个技能里只有两个会被判失败），
                  只靠真数据看不出"排序到底凭什么"。算例的每一条都与
                  `tests/test_schedule_advisor.py::test_five_items_match_hand_calculation` 同源。

第 3 段演示改序：把一条往后挪、一条往前挪，打出审计行与 deviations()，
并把审计写进 JSONL 再读回来——这是"改序会被抓到"的全部含义（注意：本地文件不是防篡改的）。

第 4 段做权重敏感性：权重反过来重排一次，列出名次变了的项。若全部名次都不变，
说明这个结论不依赖"8/4/2/1"这个拍的数；若变了，就把"结论依赖假设"这句话摆在明面上。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import mastery  # noqa: E402
from arena import schedule_advisor as adv  # noqa: E402
from arena import scheduler as sch  # noqa: E402
from arena import store  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402

DB_PATH = ROOT / ".git" / "advice-demo.db"
AUDIT_PATH = ROOT / ".git" / "advice-audit.jsonl"
TASK_YAML = ROOT / "tasks" / "dbg-001-order-report.yaml"
USER = "demo-user"
MSP, FR, TPV = "multi_step_planning", "failure_retry", "tool_param_validation"

# 剧本：两次失败（都落在 msp + fr）→ 一次满分 → 再失败一次。
# 目的只是让队列里有到期项，且两技能的"错误热度"和"掌握度"真的不同。
PLAN = [
    ("raw-fake-retry", "2026-11-06T00:00:00+00:00"),
    ("raw-fake-retry", "2026-11-08T00:00:00+00:00"),
    ("raw-perfect", "2026-11-10T00:00:00+00:00"),
    ("raw-fake-retry", "2026-11-12T00:00:00+00:00"),
    # 白卷：整份不合格 ⇒ 不动卡、不建项（B-08 的口径），但它**照样计入 P**（A 现状，B-07b），
    # 而且它检出了 `no_retry` ⇒ 只抬高 failure_retry 的错误热度。
    # 加这一步是为了让两个技能的因子值**真的不同**——否则两份提交让 msp 与 fr 的 c 完全同步，
    # 排出来的两条会分数相同，看不出排程在做什么。
    ("raw-empty", "2026-11-13T00:00:00+00:00"),
]
AT = "2026-11-15T00:00:00Z"


def _report(name: str, submission_id: str, at: str) -> dict:
    report = copy.deepcopy(run_fixture(name))
    report["submission"]["submission_id"] = submission_id
    report["submission"]["evaluated_at"] = at
    return report


def _arithmetic(judgment: adv.Judgment) -> str:
    """把综合分摊成一条算式，让人能按键计算器复核。"""
    live = [f for f in judgment.factors if f.value is not None]
    parts = [f"{f.weight:g}×{f.value:.2f}" for f in live]
    total = sum(f.weight for f in live)
    left = " + ".join(parts)
    return f"({left}) / {total:g} = {judgment.score:.4f}"


def _print_judged(judgment: adv.Judgment, *, width: int = 0) -> None:
    conf = f"覆盖 {judgment.confidence * 100:.0f}%"
    print(f"  #{judgment.rank} {judgment.skill_id:<22} due={judgment.due}  "
          f"分数={judgment.score:.4f}  {conf}")
    print(f"      算式：{_arithmetic(judgment)}")
    print(f"      理由：{judgment.summary}")


def _section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def _demo_example() -> list[dict]:
    """第 2 段的算例：五条构造的复习项（与测试同源）。"""
    def item(rid: str, skill: str, due: str) -> dict:
        return {"review_id": rid, "user_id": USER, "skill_id": skill, "kind": "variant",
                "task_id": None, "source_submission_id": "sub-x", "due": due,
                "status": "pending", "created_at": "2026-11-01T00:00:00+00:00"}

    return [
        item("rv-a", "sA", "2026-11-14T00:00:00+00:00"),
        item("rv-b", "sB", "2026-11-13T00:00:00+00:00"),
        item("rv-c", "sC", "2026-11-12T00:00:00+00:00"),
        item("rv-d", "sD", "2026-11-14T00:00:00+00:00"),
        item("rv-e", "sE", "2026-11-13T00:00:00+00:00"),
    ]


EXAMPLE_INPUTS = dict(
    p_by_skill={"sA": 0.60, "sB": 0.30, "sC": 0.90, "sD": 0.50, "sE": 0.40},
    r_by_skill={"sA": 0.80, "sB": 0.50, "sC": 0.95, "sD": 0.70, "sE": 0.60},
    error_counts={"sA": 1, "sB": 4, "sC": 0, "sD": 2, "sE": 1},
)


def main() -> int:
    ap = argparse.ArgumentParser(description="四因子建议排程的可复核演示")
    args = ap.parse_args()

    print(f"用户 {USER} / 排程时刻 {AT} / 权重 "
          f"{' '.join(f'{k}={v:g}' for k, v in adv.FACTOR_WEIGHTS.items())}")
    print(f"优先级（计划书 §八 开放问题 4，暂定）：{' > '.join(adv.FACTOR_ORDER)}")
    print("因子中文名：" + "、".join(f"{k}={v}" for k, v in adv.FACTOR_LABELS.items()))

    DB_PATH.unlink(missing_ok=True)
    AUDIT_PATH.unlink(missing_ok=True)
    conn = store.connect(DB_PATH)
    try:
        store.init_db(conn)
        store.register_skills(conn)
        store.register_patterns(conn)
        store.register_task(conn, TASK_YAML)

        _section("第 1 段 · 真数据：交 5 份提交，看队列里真实的三因子")
        for i, (fixture, at) in enumerate(PLAN, start=1):
            report = _report(fixture, f"demo-{i}", at)
            store.save_submission(conn, report, user_id=USER, created_at=at)
            result = sch.schedule_submission(
                report, user_id=USER, at=at,
                cards=sch.cards_from_rows(store.load_fsrs_cards(conn, USER)),
                task_has_fault=True,
            )
            store.save_schedule(conn, result)
            print(f"  [{i}] {at[:10]} {fixture:<16} ratio={report['score']['ratio']:<5} "
                  f"不算数={'有' if sch.dr.suppression_reasons(report) else '无'} "
                  f"→ 动卡 {len(result.cards)} 张 / 建项 {len(result.items)} 条")

        before = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')]
                  for t in ("review_items", "user_skill_states", "submissions")}
        advice = adv.advise_from_store(conn, USER, at=AT)
        after = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')]
                 for t in ("review_items", "user_skill_states", "submissions")}

        print()
        print(f"  到期待办 {len(advice.judged)} 条 / 每日上限 {advice.cap} 条 / "
              f"弃权因子 {list(advice.abstained) or '无'}")
        for judgment in advice.judged:
            _print_judged(judgment)
        print()
        print("  ⇒ 只有两条：三个技能里能判失败的只有 msp 与 fr（tpv 在现有夹具里从没失败过），")
        print("    这是数据的边界，不是排程的 bug。所以下面第 2 段用算例把排序逻辑摊开。")
        print("  ⇒ 项目截止因子在这里弃权：`SCHEMA_SQL` 里没有任何截止字段，也没有项目实体。")
        print("    弃权 ≠ 记 0 —— 记 0 会把每条的分数整体压到 7/15，却不改变顺序（假数据、真危害）。")
        print(f"  ⇒ 排程前后库里三张表逐行相同："
              f"{before == after}（排程只读不写）")

        _section("第 2 段 · 算例：五条构造项，算式逐条摊开（可与测试对账）")
        example = adv.advise(_demo_example(), user_id=USER, at=AT, cap=2, **EXAMPLE_INPUTS)
        print("  因子取值：A P=.60 R=.80 heat 1｜B P=.30 R=.50 heat 4｜C P=.90 R=.95 heat 0")
        print("            D P=.50 R=.70 heat 2｜E P=.40 R=.60 heat 1")
        print("  截止数据整批缺失 ⇒ 该因子退出，分母从 15 收到 7（4+2+1）")
        print()
        for judgment in example.judged:
            _print_judged(judgment)
        print()
        print(f"  今日视图（上限 {example.cap}）：{list(example.today())}")
        print(f"  顺延（仍是 pending，不是被删）：{list(example.deferred())}")
        print("  D 与 E 综合分完全相同（都是 2.70/7）⇒ 同一平手格 ⇒ 按 due 先到先做，E 在前。")
        print("  平手格 0.02 是拍的：分差 0.003 就说'A 比 B 急'是假精度，不装。")

        _section("第 3 段 · 改序：留痕、理由、审计落盘")
        last = example.order[-1]
        plan = adv.apply_overrides(example, [
            adv.Override(last, 1, "今天只有半小时，先把明天到期的那条做掉", AT),
            adv.Override(example.order[-2], 2, "这条上次就跳过，不能再拖", AT),
        ])
        for audit in plan.audits:
            print(f"  {audit.text()}")
        print(f"  最终顺序：{list(plan.order)}")
        print(f"  今日视图：{list(plan.today())}")
        print(f"  真正挪动过名次的（deviations）："
              f"{[(a.review_id, a.suggested_rank, a.final_rank) for a in plan.deviations()]}")
        written = adv.append_audit(AUDIT_PATH, plan)
        print(f"  审计写入 {written} 行 → {AUDIT_PATH.relative_to(ROOT)}")
        for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines():
            print(f"    {line}")
        print("  边界：本地 JSONL 谁都能改，**不是防篡改**的；它的作用是'改过就会被看见'。")
        print("        要'改不了'需要服务端 append-only + 身份认证（B-10 有前端之后）。")
        print("  另：空理由、同一条改两次、名次越界、不存在的 id，四种都会被拒——")
        print("      '尽力而为'式地忽略一条改序，用户会以为它生效了。")

        _section("第 4 段 · 权重敏感性：换成别的假设，顺序会不会翻")
        variants = {
            "默认 8:4:2:1": {"deadline": 8.0, "forgetting": 4.0, "error_heat": 2.0,
                             "mastery_gap": 1.0},
            "压平 2:1.5:1.1:1": {"deadline": 2.0, "forgetting": 1.5, "error_heat": 1.1,
                                 "mastery_gap": 1.0},
            "拉陡 16:4:2:1": {"deadline": 16.0, "forgetting": 4.0, "error_heat": 2.0,
                              "mastery_gap": 1.0},
        }
        orders = {name: adv.advise(_demo_example(), user_id=USER, at=AT, cap=2,
                                   weights=w, **EXAMPLE_INPUTS).order
                  for name, w in variants.items()}
        for name, order in orders.items():
            print(f"  {name:<18} {list(order)}")
        base_rank = {rid: i + 1 for i, rid in enumerate(example.order)}
        shifted = {}
        for name, order in orders.items():
            for i, rid in enumerate(order):
                if base_rank[rid] != i + 1:
                    shifted.setdefault(rid, []).append(f"{name}: 第{base_rank[rid]}→第{i + 1}")
        print()
        if not shifted:
            print("  ⇒ 三组权重下名次**完全一致**：这个顺序不依赖 8:4:2:1 这个拍的数。")
            print("    可以说'排序稳定'，但**不能**说'权重已标定'——那要 dogfooding 数据（B-09c）。")
        else:
            for rid, notes in sorted(shifted.items()):
                print(f"  ⇒ {rid} 的名次随权重变化：{'；'.join(notes)}")
            print("    这几条在原权重下本来就挨得很近（见第 2 段的分差），")
            print("    所以它们的先后是'平手格 + due'决定的，不是权重算出来的——引用时要带上这个前提。")

        print()
        print("  再试一次非法权重（把优先级反过来）：")
        try:
            adv.advise(_demo_example(), user_id=USER, at=AT,
                       weights={"deadline": 1.0, "forgetting": 2.0, "error_heat": 4.0,
                                "mastery_gap": 8.0}, **EXAMPLE_INPUTS)
        except adv.AdvisorError as exc:
            print(f"    被拒：{exc}")
            print("    ⇒ 刻意如此：'截止优先'一旦不成立，排序看着仍然正常——错得看不出来。")
            print("      真想改优先级，要同时改 FACTOR_ORDER 并留痕，不能只偷偷调数。")

        _section("收尾：这一步的边界，别越界引用")
        print("  ① 三个因子有真数据（P / FSRS 的 R / 近 7 天错误计数），")
        print("     第四个（项目截止）**没有数据源**，只留了接口，现在一律弃权。")
        print("  ② 权重 8/4/2/1、平手格 0.02、热度窗口 7 天、截止视界 7 天，四个数全是**假设**，")
        print("     没有 dogfooding 数据之前不叫结论（与 rating 阈值 0.9/0.7/0.4 同性质）。")
        print("  ③ 排程只给建议：顺延项仍是 pending，改序要理由、要落审计，系统不抢控制权。")
        print("  ④ P 的衰减仍未实现（B-08b），所以'遗忘风险'只来自 FSRS 的 R，不来自 P。")
        return 0
    finally:
        conn.close()
        DB_PATH.unlink(missing_ok=True)
        AUDIT_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
