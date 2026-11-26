#!/usr/bin/env python3
"""演示脚本 —— **这不是你的作答**，是用来看"同一道题，报告会长成什么样"。

为什么有这个：试用里有一半人手上没有 Claude Code / Cursor 这类 Agent。
没有 Agent 不代表不能试——这个脚本就是替你"发几次 HTTP 请求"，跑两次能对比出报告的差别。

    python try.py http://127.0.0.1:8765             # 初学者常见写法：只拉到一半、报错不重试
    python try.py http://127.0.0.1:8765 --mode ok   # 拉全、重试、剔掉无效行

两种模式都**如实**发请求、如实写 `/workspace/report.json`；区别只在"怎么处理那次 500"和
"有没有把无效行剔掉"。跑完把 `calls.jsonl` 发回即可（服务端记的，不用你手写）。

⚠️ 别把这个脚本当答案抄。它存在的意义是让你先看见"错的时候报告说什么"。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

LIMIT = 10          # 题面允许 1..10
TOTAL_HINT = 20     # 题面说共 20 条


def call(base: str, tool: str, body: dict) -> tuple[int, dict]:
    """发一次工具调用。返回 (状态码, 返回体)；4xx/5xx 也照实返回，不抛异常。"""
    req = urllib.request.Request(
        f"{base}/tools/{tool}", json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def naive(base: str) -> dict:
    """初学者最常见的写法：翻页不看状态码，出错就当这页没数据，接着往下走。"""
    orders: list[dict] = []
    offset = 0
    while offset < TOTAL_HINT:
        status, body = call(base, "fetch_orders", {"offset": offset, "limit": LIMIT})
        if status != 200:
            print(f"  [第 {offset} 条起这一页失败] {status} {body.get('error')} —— 不重试，跳过")
            break          # ← 这就是问题所在：一次失败，后面的数据全丢了
        orders.extend(body["orders"])
        offset += LIMIT
    return summarize(orders, note="只拉到一部分")


def careful(base: str) -> dict:
    """把那次 500 重试一次，拉全，再按题面剔掉无效行。"""
    orders: list[dict] = []
    offset = 0
    while offset < TOTAL_HINT:
        status, body = call(base, "fetch_orders", {"offset": offset, "limit": LIMIT})
        if status != 200:
            print(f"  [第 {offset} 条起这一页失败] {status} {body.get('error')} —— 重试一次")
            status, body = call(base, "fetch_orders", {"offset": offset, "limit": LIMIT})
        if status != 200:
            raise SystemExit(f"重试仍然失败：{status} {body}")
        orders.extend(body["orders"])
        offset += LIMIT
    return summarize(orders, note="拉全了")


def summarize(orders: list[dict], *, note: str) -> dict:
    valid = []
    for order in orders:
        amount = order.get("amount")
        if isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount >= 0:
            valid.append(amount)
    result = {"valid_count": len(valid), "total_amount": round(float(sum(valid)), 2)}
    print(f"  {note}：拿到 {len(orders)} 条，有效 {result['valid_count']} 条，"
          f"合计 {result['total_amount']}")
    return result


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    base = argv[1].rstrip("/")
    mode = "naive"
    if "--mode" in argv:
        mode = argv[argv.index("--mode") + 1]

    print(f"[模式 {mode}] 对着 {base} 做题")
    result = naive(base) if mode == "naive" else careful(base)
    content = json.dumps(result, ensure_ascii=False)
    status, body = call(base, "write_report",
                        {"path": "/workspace/report.json", "content": content})
    print(f"  写入 report.json：{status} {body}")
    if mode == "naive":
        print("\n换一个模式再跑一次，两边报告对着看：")
        print(f"  python try.py {base} --mode ok")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
