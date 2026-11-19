"""C-02 放行契约（`review-response-v1.md` §52.4）的执行器：计数增量 + node id 集合 diff。

## 为什么「数条数」不够

§52.4 第 2 条的原话：只数条数挡不住「同名覆盖 + 另加一条」的抵消。
具体到本仓库的形状——把 `test_a` 删掉、同时加一条 `test_b`：

```text
before: 1478 passed     after: 1479 passed     ← 净 +1，"看起来正常"
真实变化: -test_a, +test_b                      ← 一删一加，语义已经变了
```

参数化用例更容易踩：`test_x[case1]` 改成 `test_x[case-1]` 只改 id 不改条数。
所以契约要求的是**集合 diff**，本脚本就是把那句话变成命令。

## 两条口径

1. **快照带 `git_sha`**。没有提交锚点的 node 集合，diff 出来无法归因到"哪一步改的"；
   `after` 侧默认取现况，若与 `before` 的 sha 相同而集合不同 ⇒ 说明工作区有未提交改动，
   会打一行 WARN（不是错误：改一半时对比正是常见用法）。
2. **node id 必须唯一**。重复说明参数化 id 撞了，集合会静默把两条并成一条，
   diff 结果失真——这种"看起来干净"的失真比报错难查得多，所以直接拒。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# `--collect-only` 的尾巴与空行都不算 node id。
_SUMMARY_RE = re.compile(r"^\d+\s+(tests?\s+collected|deselected|error)")
_NOISE_PREFIX = ("=", "ERROR", "no tests ran", "short test summary")


class NodeDiffError(RuntimeError):
    """收集或解析失败（不是"两次没差别"）。"""


def parse(output: str) -> list[str]:
    """从 `pytest --collect-only -q` 的 stdout 里取 node id，按出现顺序。

    参数化 id 里的非 ASCII 会被 pytest 转成 `\\uXXXX`（它自己的 `ascii_escaped` 策略），
    与终端编码无关——因此不同机器上的快照可以直接比。**不要**在这里做反转义：
    那会让 `[\\u771f]` 与 `[真]` 变成同一个 id，两个不同用例会被合成一条。
    """
    ids: list[str] = []
    for line in output.splitlines():
        text = line.strip()
        if not text or "::" not in text:
            continue
        if _SUMMARY_RE.match(text) or text.startswith(_NOISE_PREFIX):
            continue
        ids.append(text)
    return ids


def _collect() -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--collect-only"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    ids = parse(proc.stdout)
    if proc.returncode != 0 or not ids:
        raise NodeDiffError(
            f"收集失败（exit={proc.returncode}，取到 {len(ids)} 个 node id）\n"
            f"stdout 尾部：{proc.stdout[-400:]}\nstderr 尾部：{proc.stderr[-400:]}"
        )
    return ids


def _unique(ids: list[str], where: str) -> set[str]:
    dup = sorted(k for k, n in Counter(ids).items() if n > 1)
    if dup:
        raise NodeDiffError(
            f"{where} 里有重复 node id（{len(dup)} 个，例：{dup[:3]}）——"
            "重复会让集合 diff 把两个用例并成一条，先修参数化 id"
        )
    return set(ids)


def diff(before: list[str], after: list[str]) -> dict:
    """集合层面的差异。`count_delta` 与 `net` 分开给：前者是 pytest 会报的数，后者是真相。"""
    b, a = _unique(before, "before"), _unique(after, "after")
    return {
        "before_count": len(b),
        "after_count": len(a),
        "added": sorted(a - b),
        "removed": sorted(b - a),
    }


def _git_sha() -> str:
    proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8")
    return proc.stdout.strip() or "unknown"


def snapshot(path: Path) -> dict:
    ids = _collect()
    _unique(ids, "快照")
    data = {"git_sha": _git_sha(), "count": len(ids), "ids": ids}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def _load(path: Path) -> dict:
    if not path.is_file():
        raise NodeDiffError(f"快照不存在：{path}（先用 snapshot 生成）")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("ids"), list):
        raise NodeDiffError(f"{path} 不是本脚本写的快照（缺 ids）")
    return data


def _report(before: dict, after: dict, limit: int) -> str:
    d = diff(before["ids"], after["ids"])
    lines = [
        f"before: {d['before_count']} 个 node（{before.get('git_sha', '?')}）",
        f"after : {d['after_count']} 个 node（{after.get('git_sha', '?')}）",
        f"delta : +{len(d['added'])} / -{len(d['removed'])}"
        f"（净 {d['after_count'] - d['before_count']:+d}）",
    ]
    if before.get("git_sha") == after.get("git_sha") and (d["added"] or d["removed"]):
        lines.append("WARN: 两侧锚点相同而集合不同 ⇒ 工作区有未提交改动，这份 diff 不能归因到某个提交")

    for tag, items in (("+", d["added"]), ("-", d["removed"])):
        for node in items[:limit]:
            lines.append(f"  {tag} {node}")
        if len(items) > limit:
            lines.append(f"  {tag} ...（还有 {len(items) - limit} 条未列出）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="pytest node id 集合快照与 diff（§52.4 契约）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_snap = sub.add_parser("snapshot", help="收集当前 node 集合并存快照")
    p_snap.add_argument("path")

    p_diff = sub.add_parser("diff", help="把快照与现况（或另一份快照）比")
    p_diff.add_argument("before")
    p_diff.add_argument("after", nargs="?", help="省略则现场收集")
    p_diff.add_argument("--limit", type=int, default=40, help="每侧最多列几条（默认 40）")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "snapshot":
            data = snapshot(Path(args.path))
            print(f"OK: {data['count']} 个 node → {args.path}（锚点 {data['git_sha']}）")
            return 0

        before = _load(Path(args.before))
        after = _load(Path(args.after)) if args.after else {
            "git_sha": _git_sha(), "ids": _collect()
        }
        print(_report(before, after, args.limit))
        return 0
    except NodeDiffError as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
