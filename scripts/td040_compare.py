#!/usr/bin/env python
"""TD-040 对比试验：两个候选修法各实现一版，用真实历史样本 + 一个构造样本判优劣。

背景
----
`--check` 只把包里**已记录**的那行实测结果原样喂回 `build()` 重算指纹，
不重跑 pytest。于是它能抓「代码漂移」，抓不到「**记录本身**过期或不该发布」。

真实案例（本仓库历史）：`487ff6f` 那次生成的评审包，实测行诚实写着
`1 failed, 666 passed / 667 total`——**一个带着失败记录的包被提交并推送了**，
之后测试修好，但直到 `6da5f23` 才重生成。这段时间里 `--check` 对它是绿的。

两个候选修法（§62.6 登记）
--------------------------
A 生成门：生成时若实测非全绿（含 `--fast` 无数据），包内显式标注「禁止对外发布」，
           头部 meta 加 `test-green=false`；`--check` 读到 false 即报 NOT_RELEASABLE。
B 陈旧度：`--check` 把「锚点落后 HEAD N 个提交」从 NOTE 升级为告警，N 可配。

试验设计
--------
样本 = 3 个真实历史版本（从 git 直接读，不读工作区）+ 1 个构造样本（--fast 情形）。
真值 = 该版本的包**是否可对外发布**，逐条给理由（见 SAMPLES）。
不追求统计显著性：样本量 n=4，本试验只做**反例检验**——谁能被真实案例否掉。

判据
----
每个方案对每个样本给出 PASS / BLOCK 两档，与真值比对，统计误报（FP）与漏报（FN）。
另附一项独立实证：把 `487ff6f` 的包原样喂给**当前** `check()`，看它报什么。

退出码：0 = 结论已得出（不论谁赢）；1 = 试验装置本身失效（读不到样本等）。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import arena.review_packet as rp  # noqa: E402

PACKET = "docs/review-packet.md"

# 真值：该版本是否可对外发布（True = 可发布）
# 理由必须写清，不许只给布尔——否则等于把判断权藏起来。
SAMPLES = [
    {
        "sha": "a89e6ae",
        "publishable": True,
        "why": "记录 652 passed 全绿，且与当时工作区一致；此后再无失败记录",
    },
    {
        "sha": "487ff6f",
        "publishable": False,
        "why": "记录含 1 failed；对外材料带失败记录，无论是否已修好都不该发布",
    },
    {
        "sha": "6da5f23",
        "publishable": True,
        "why": "记录 688 passed 全绿，且是刚重生成的（锚点仅落后 1 个打包提交）",
    },
    {
        "sha": "CONSTRUCTED-FAST",
        "publishable": False,
        "why": "构造样本：--fast 生成时无实测数据，对外材料缺这一项即不可发布",
    },
]

FAST_LINE = rp.FAST_TEST_LINE
LINE_RE = re.compile(r"^- 单元测试实测：.*$", re.M)
META_RE = re.compile(r"packet-sha256=([0-9a-f]+)\s+snapshot-sha=([0-9a-f]+)")


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{proc.stderr[:200]}")
    return proc.stdout


def load_sample(sha: str) -> dict:
    """从 git 直接读该版本的评审包文本；构造样本不碰 git。"""
    if sha == "CONSTRUCTED-FAST":
        real = load_sample("6da5f23")
        body = real["body"].replace(real["line"], FAST_LINE)
        return {**real, "sha": sha, "body": body, "line": FAST_LINE, "behind": 0}
    text = _git("show", f"{sha}:{PACKET}")
    meta, _, body = text.partition("\n")
    m = META_RE.search(meta)
    if not m:
        raise RuntimeError(f"{sha} 的头部缺少 packet-sha256 / snapshot-sha")
    line_m = LINE_RE.search(body)
    line = line_m.group(0) if line_m else ""
    behind = int(_git("rev-list", "--count", f"{m.group(2)}..{sha}").strip() or 0)
    return {
        "sha": sha, "meta": meta, "body": body, "line": line,
        "packet_sha": m.group(1), "snapshot": m.group(2), "behind": behind,
    }


def parse_record(line: str) -> dict:
    """把实测行解析成结构：失败数 / 是否 --fast / 是否真有数据。"""
    failed = re.search(r"(\d+)\s+failed", line)
    total = re.search(r"/\s*(\d+)\s+total", line)
    fast = line.strip() == FAST_LINE.strip() or "未执行测试" in line
    return {
        "failed": int(failed.group(1)) if failed else 0,
        "total": int(total.group(1)) if total else 0,
        "fast": fast,
        "has_data": bool(line) and not fast,
    }


# ── 候选 A：生成门（判定只看包内文本） ──────────────────────────────
def variant_a(rec: dict) -> tuple[str, str]:
    if not rec["has_data"]:
        return "BLOCK", "无实测数据（--fast 生成或行缺失）"
    if rec["failed"] > 0:
        return "BLOCK", f"记录含 {rec['failed']} 条失败"
    return "PASS", "记录全绿"


# ── 候选 B：陈旧度（判定只看锚点落后提交数） ────────────────────────
def variant_b(behind: int, threshold: int) -> tuple[str, str]:
    if behind > threshold:
        return "BLOCK", f"锚点落后 {behind} > N={threshold}"
    return "PASS", f"锚点落后 {behind} ≤ N={threshold}"


def score(judgements: list[tuple[str, bool]]) -> dict:
    fp = sum(1 for verdict, truth in judgements if verdict == "BLOCK" and truth)
    fn = sum(1 for verdict, truth in judgements if verdict == "PASS" and not truth)
    ok = sum(1 for verdict, truth in judgements if (verdict == "BLOCK") != truth)
    return {"correct": ok, "fp": fp, "fn": fn, "n": len(judgements)}


def probe_check_at(sha: str) -> tuple[int, str]:
    """在**那个提交自己的工作树**里跑**当时那一版** check()。

    不能把历史包喂给当前 check()：自动段（分数表、用例数等）是按当前工作区现算的，
    旧包在今天必然 STALE——那只能证明"仓库变了"，证明不了"当时漏报"。
    要在原时刻判定，就得连代码带包一起回到那个提交。
    """
    wt = ROOT / ".td040-wt"
    if wt.exists():
        # 上次异常退出可能留下目录；先清干净，否则 worktree add 会说 already exists
        try:
            _git("worktree", "remove", "--force", str(wt))
        except RuntimeError:
            _git("worktree", "prune")
    try:
        _git("worktree", "add", "-f", str(wt), sha)
        proc = subprocess.run(
            [sys.executable, "src/arena/review_packet.py", "--check"],
            cwd=wt, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    finally:
        try:
            _git("worktree", "remove", "--force", str(wt))
        except RuntimeError:
            _git("worktree", "prune")


def main() -> int:
    print("=" * 78)
    print("TD-040 对比试验：候选 A（生成门） / 候选 B（陈旧度）")
    print("=" * 78)

    rows = []
    for spec in SAMPLES:
        try:
            s = load_sample(spec["sha"])
        except Exception as exc:  # noqa: BLE001 - 装置失效要如实报，不静默跳过
            print(f"FAIL: 读取样本 {spec['sha']} 失败：{exc}")
            return 1
        rec = parse_record(s["line"])
        va, ra = variant_a(rec)
        rows.append({"spec": spec, "s": s, "rec": rec, "va": va, "ra": ra})
        print(f"\n[{spec['sha']}]  真值={'可发布' if spec['publishable'] else '不可发布'}")
        print(f"  理由：{spec['why']}")
        print(f"  记录：failed={rec['failed']} total={rec['total']} "
              f"fast={rec['fast']} 锚点落后={s['behind']} 个提交")
        print(f"  A → {va}: {ra}")

    print("\n" + "-" * 78)
    print("候选 A 逐样本")
    for r in rows:
        truth = r["spec"]["publishable"]
        hit = (r["va"] == "BLOCK") != truth
        print(f"  {r['spec']['sha']:18s} A={r['va']:5s} 真值={'T' if truth else 'F'} "
              f"{'✓' if hit else '✗'}")
    sa = score([(r["va"], r["spec"]["publishable"]) for r in rows])
    print(f"  合计：{sa['correct']}/{sa['n']} 正确，误报 {sa['fp']}，漏报 {sa['fn']}")

    print("\n候选 B 逐 N 取值")
    b_results = {}
    for n in (1, 2, 3):
        js = [(variant_b(r["s"]["behind"], n)[0], r["spec"]["publishable"]) for r in rows]
        sb = score(js)
        b_results[n] = sb
        detail = " ".join(
            f"{r['spec']['sha']}={'B' if variant_b(r['s']['behind'], n)[0] == 'BLOCK' else 'P'}"
            for r in rows
        )
        print(f"  N={n}: {sb['correct']}/{sb['n']} 正确，误报 {sb['fp']}，漏报 {sb['fn']}  | {detail}")

    print("\n" + "-" * 78)
    print("独立实证：在 487ff6f 的工作树里，跑**当时那一版** check()")
    print("  （该版本的包内实测行写着 1 failed，真值 = 不可发布）")
    rc, out = probe_check_at("487ff6f")
    print(f"  返回码 = {rc}")
    for line in out.splitlines()[:3]:
        print(f"  | {line}")
    print(f"  ⇒ 当时自检 {'判绿（漏）' if rc == 0 else '已拦住'}")

    print("\n" + "=" * 78)
    print("结论")
    print("=" * 78)
    print(f"- 候选 A：{sa['correct']}/{sa['n']} 正确（误报 {sa['fp']} / 漏报 {sa['fn']}）")
    best_n = max(b_results, key=lambda k: (b_results[k]["correct"], -b_results[k]["fp"]))
    bb = b_results[best_n]
    print(f"- 候选 B：最好情况 N={best_n} 也只有 {bb['correct']}/{bb['n']}"
          f"（误报 {bb['fp']} / 漏报 {bb['fn']}）")
    b_fp_at_1 = b_results[1]["fp"]
    print(f"  · B 的缺陷不是「阈值没调对」，而是它测的东西与测试红绿无因果关系：")
    print(f"    N=1 时在正常发布的 a89e6ae（落后 2 个提交）上误报 {b_fp_at_1} 次，")
    print(f"    N≥2 时对 487ff6f（落后 1）与 --fast 样本全部放行 —— 灵敏度与准确度不可兼得")
    print(f"- 当时那一版 check() 对带 1 failed 的 487ff6f 判 {'OK（漏）' if rc == 0 else '非 OK'}")
    print("\n两项都覆盖不到的格子：")
    print("  包声称全绿 + 生成之后代码才变红 ⇒ A 输入里没有「当前实际状态」，结构上不可检出；")
    print("  B 只看提交数，与测试红绿无因果关系，同样不可检出。")
    print("\n由此本轮转向候选 C：给 check 增加 --full，重跑 pytest 并比对记录行。")

    # 判据：装置必须先证明自己不是永绿——真实样本里必须至少有一个被判 BLOCK
    blocked = [r["spec"]["sha"] for r in rows if r["va"] == "BLOCK"]
    if not blocked:
        print("\nFAIL: 候选 A 对全部样本都放行 ⇒ 试验装置失效，结论不成立")
        return 1
    print(f"\n装置自检：A 判 BLOCK 的样本 = {blocked}（非空，装置有效）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
