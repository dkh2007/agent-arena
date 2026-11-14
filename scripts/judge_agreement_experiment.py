"""B-04 打分布：同一份提交用真模型评 N 次，看一致率到底是多少。

**这个脚本会真调接口、真花钱。** 所以它刻意做成可控制粒度：

    python scripts/judge_agreement_experiment.py --dry-run              # 只看会发什么，不花钱
    python scripts/judge_agreement_experiment.py --fixture raw-perfect  # 只跑一份
    python scripts/judge_agreement_experiment.py --runs 3               # 同一份评三次
    python scripts/judge_agreement_experiment.py --all                  # 五份全跑

**为什么不设默认阈值**：`docs/BACKLOG.md` B-04 的验收判据是「跑 10 次 → 一致率有数」，
先有数才定线。这个脚本只产数字，不产结论；阈值定在哪里是后面的事。

**为什么只报 token 不报钱**：单价随时会变，编一个数比不报更糟。要算成本自己乘。

输出两块：
  1. 逐份明细（档位序列、一致率、可疑模式、耗时、token）
  2. 汇总分布（一致率取值与出现次数）——这是定阈值的唯一依据
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import llm_client as lc  # noqa: E402
from arena import llm_judge as lj  # noqa: E402
from arena.diagnosis_report import load_task  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
DEFAULT_FIXTURES = ("raw-perfect", "raw-empty", "raw-fake-retry")


def _load_fixture(name: str) -> dict:
    path = ROOT / "fixtures" / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"找不到夹具 {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B-04 双评一致性实测")
    ap.add_argument("--fixture", action="append", help="夹具名（可多次），默认三份代表样本")
    ap.add_argument("--all", action="store_true", help="跑 fixtures/ 下全部 raw-*.json")
    ap.add_argument("--runs", type=int, default=2, help="每份评价几次（默认 2）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要发送的材料，不调接口")
    ap.add_argument("--model", default="", help="覆盖 .env 里的模型名（默认用 LLM_MODEL）")
    ap.add_argument("--purpose", default="", help="这批实验想验证什么，写进记录里供以后复核")
    ap.add_argument("--max-tokens", type=int, default=4096,
                    help="单次输出上限。这是上限不是花销：给小了会把思考预算吃光、正文返回空")
    args = ap.parse_args(argv)

    task = load_task(TASK_PATH)
    catalog = lj.load_pattern_catalog()
    vocab = {p["pattern_id"] for p in catalog}

    if args.all:
        names = sorted(p.stem for p in (ROOT / "fixtures").glob("raw-*.json"))
    elif args.fixture:
        names = list(args.fixture)
    else:
        names = list(DEFAULT_FIXTURES)

    try:
        config = lc.LLMConfig.from_env(env_path=ROOT / ".env")
    except lc.LLMError as exc:
        print(f"[FAIL] 配置：{exc}")
        return 2
    if args.model:
        config = lc.LLMConfig(api_key=config.api_key, base_url=config.base_url, model=args.model)

    print(f"模型：{config.model}   端点：{config.base_url}   每份评 {args.runs} 次")
    print(f"样本：{len(names)} 份 → {names}")
    print(f"计划调用：{len(names) * args.runs} 次" + ("（dry-run，不实际调用）" if args.dry_run else ""))
    print()

    if args.dry_run:
        for name in names:
            ctx = lj.build_judge_context(task, _load_fixture(name))
            blob = json.dumps(ctx, ensure_ascii=False)
            print(f"--- {name}：材料 {len(blob)} 字符，{ctx['call_count']} 次工具调用，"
                  f"最终态 {ctx['final_status']} ---")
            print(blob[:600] + ("…" if len(blob) > 600 else ""))
            print()
        return 0

    client = lc.LLMClient(config)
    judge = lj.LLMJudge(client, catalog, max_tokens=args.max_tokens)

    rows: list[dict] = []
    failures: list[dict] = []
    total_prompt = total_completion = total_reasoning = 0

    for name in names:
        ctx = lj.build_judge_context(task, _load_fixture(name))
        try:
            res = lj.double_judge(judge, ctx, runs=args.runs, vocab=vocab)
        except (lj.JudgeError, lc.LLMError) as exc:
            # 单份失败不中断全批：一份的失败率本身就是要报告的数据，
            # 一旦 break，剩下的样本永远测不到，分布也就永远不完整。
            failures.append({"fixture": name, "error": f"{type(exc).__name__}: {exc}"})
            print(f"[FAIL] {name}：{type(exc).__name__}: {exc}")
            continue
        last = client.last_completion
        if last:
            total_prompt += last.prompt_tokens
            total_completion += last.completion_tokens
            total_reasoning += last.reasoning_tokens
        rows.append(
            {
                "fixture": name,
                "ratings": [v.rating for v in res.verdicts],
                "agreement": res.rating_agreement,
                "pattern_agreement": res.pattern_agreement,
                "spread": res.rating_spread,
                "suspected": sorted({p for v in res.verdicts for p in v.patterns}),
                "latency_ms": [v.latency_ms for v in res.verdicts],
                "rationale": [v.rationale[:80] for v in res.verdicts],
            }
        )
        print(f"{name:16} 档位={rows[-1]['ratings']} 一致={res.rating_agreement:.2f} "
              f"跨档={res.rating_spread} 模式Jaccard={res.pattern_agreement:.2f} "
              f"可疑={rows[-1]['suspected'] or '无'} 耗时={rows[-1]['latency_ms']}")

    print()
    print("=== 档位与理由（人看的部分）===")
    for row in rows:
        print(f"[{row['fixture']}]")
        for i, (r, why) in enumerate(zip(row["ratings"], row["rationale"]), 1):
            print(f"  第{i}次 {r} 档：{why}")

    dist: dict[float, int] = {}
    for row in rows:
        dist[row["agreement"]] = dist.get(row["agreement"], 0) + 1
    print()
    print("=== 一致率分布（定阈值的唯一依据）===")
    if not rows:
        print("  没有成功样本 —— 分布算不出来，先解决上面的失败")
    else:
        for value in sorted(dist):
            print(f"  {value:.2f} × {dist[value]}")
        mean = sum(r["agreement"] for r in rows) / len(rows)
        print(f"  均值 {mean:.3f}   样本 {len(rows)} 份 × {args.runs} 次")

    if failures:
        print()
        print(f"=== 失败 {len(failures)} 份（失败率本身也是要报告的数据）===")
        for row in failures:
            print(f"  {row['fixture']}：{row['error']}")

    print()
    print(f"token：prompt {total_prompt} / completion {total_completion} / reasoning {total_reasoning}")
    print("（只报 token 不报钱：单价会变，编一个数比不报更糟）")

    out = ROOT / "docs" / "judge-agreement-distribution.json"
    record = {
        "purpose": args.purpose or "手工运行（未写 purpose）",
        "runs_per_fixture": args.runs,
        "max_tokens": args.max_tokens,
        "rows": rows,
        "failures": failures,
        "tokens": {"prompt": total_prompt, "completion": total_completion, "reasoning": total_reasoning},
    }
    # **累积不覆盖**：一次重跑就把上一次的证据冲掉，等于自己不让自己复核。
    # 读不出来（文件被手工改过、或不存在）就从头开始，不静默丢掉旧内容——
    # 旧内容丢了会打印出来，让人知道发生过什么。
    previous: dict = {}
    if out.exists():
        try:
            loaded = json.loads(out.read_text(encoding="utf-8"))
            previous = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError as exc:
            print(f"[WARN] 已有 {out.name} 解析失败（{exc}），本次将从空开始，旧内容已被覆盖")
            previous = {}
    experiments = previous.get("experiments")
    if not isinstance(experiments, list):
        experiments = []
    experiments.append(record)
    doc = {"model": config.model, "endpoint": config.base_url, "task": task.get("task_id")}
    doc.update({k: v for k, v in previous.items() if k not in {"model", "endpoint", "task", "experiments"}})
    doc["experiments"] = experiments
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"明细已写入 {out.relative_to(ROOT)}（累积第 {len(experiments)} 批）")
    return 0 if not failures else 4


if __name__ == "__main__":
    raise SystemExit(main())
