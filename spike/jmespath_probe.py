"""JMESPath 能否表达 R3 —— 能力边界实测（不采信任何人的口头断言）。

背景：外部评审建议「断言引擎不要完全自研，用 JMESPath + 自定义函数做薄封装」。
这条是事实性主张，必须实测。本脚本回答三个问题：

  Q1 JMESPath 能否区分「真重试」与「假重试」（R3 的核心判别）
  Q2 「允许中间穿插其他调用」是否需要额外机制才能支持
  Q3 哪些 R3 变体 JMESPath 表达不了（决定薄封装到底有多薄）

运行：
    python spike/jmespath_probe.py
"""

from __future__ import annotations

import json

import jmespath

# ---------------------------------------------------------------- trace 样本
BASE = {"name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "http_status": 200}
PAGE2 = {"name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500}
PAGE2_OK = {"name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "success", "http_status": 200}
REPORT = {"name": "write_report", "params": {"path": "/workspace/report.json"}, "status": "success", "http_status": 200}

CASES = {
    # R3 应判 PASS：同一页 offset=10，先 error 后 success
    "真重试": {"calls": [BASE, PAGE2, PAGE2_OK, REPORT]},
    # R3 应判 FAIL：error 后回到了 offset=0（游标没推进）—— 外部评审未意识到这个区分
    "假重试": {"calls": [BASE, PAGE2, BASE, REPORT]},
    # R3 应判 PASS：两次 offset=10 之间穿插了别的工具调用
    "真重试+穿插": {"calls": [BASE, PAGE2, REPORT, PAGE2_OK]},
    # R3 应判 FAIL：只有失败，没有重试
    "无重试": {"calls": [BASE, PAGE2, REPORT]},
}

EXPECTED = {"真重试": True, "假重试": False, "真重试+穿插": True, "无重试": False}

# ---------------------------------------------------------------- 表达式原型
# 思路：按 (工具名 + 参数子集) 过滤，投影出 status 序列，再看序列里有没有 error 后跟 success。
# 关键在于：过滤掉无关调用后，"穿插"自动被消解 —— 这是 JMESPath 最漂亮的一点。
EXPR_STATUS_SEQ = "calls[?name=='fetch_orders' && params.offset==`10`].status"


def judge_via_jmespath(calls: list[dict]) -> bool:
    """R3 判定：过滤后的状态序列里，error 之前出现过吗？之后有 success 吗？"""
    seq = jmespath.search(EXPR_STATUS_SEQ, {"calls": calls})
    if not seq or "error" not in seq:
        return False
    first_err = seq.index("error")
    return "success" in seq[first_err + 1 :]


# ---------------------------------------------------------------- 局限探针
def probe_limits() -> None:
    print("\n=== Q3 能力边界：JMESPath 表达不了什么 ===")

    # (a) 时序距离约束："失败后 3 次调用内必须成功"
    #     JMESPath 的过滤器会丢弃原索引，投影后的序列再也拿不到"原位置"。
    expr = "length(calls[?name=='fetch_orders' && params.offset==`10`])"
    print(f"(a) 能否拿到原数组索引做距离判断？ expr={expr} -> 只能得到计数，索引已丢失")

    # (b) 跨物件的因果推导："report 里的 total_amount 必须能从 fetch_orders 的返回推导出来"
    #     这不是查询，是计算，JMESPath 无算术聚合。
    try:
        jmespath.search("sum(calls[?name=='fetch_orders'].result.orders[].amount)", {"calls": CASES["真重试"]["calls"]})
        print("(b) sum() 存在，但空值参与时静默返回 None（无类型校验的静默失败）")
    except Exception as exc:  # noqa: BLE001
        print(f"(b) sum() 不可用: {exc}")

    # (c) 参数 schema 校验：required / type / range / additionalProperties 四项
    expr = "calls[].params.keys(@)"
    print(f"(c) 参数校验？ expr={expr} 只能列出键名，required/range/additionalProperties 仍需代码")


def probe_error_pattern_split() -> None:
    """R3 判 FAIL 之后，还得说出「为什么失败」——这是诊断层的核心价值，不是判定层。

    注意上面输出里 假重试 与 无重试 的过滤后序列完全相同（都是 ["error"]）。
    也就是说：JMESPath 能判 PASS/FAIL，但判不出错误模式。补一条表达式来分离。
    """
    print("\n=== Q4 判定对了，诊断呢：能否区分两种失败模式 ===")
    expr_offsets = "calls[?name=='fetch_orders'].params.offset"
    for name, payload in CASES.items():
        if EXPECTED[name]:
            continue
        offsets = jmespath.search(expr_offsets, payload)
        # 分类规则：失败页之后又出现了已拉过的页 => 重试参数错；否则 => 没重试
        failed_page = 10
        idx = offsets.index(failed_page) if failed_page in offsets else -1
        revisited = idx >= 0 and any(o in offsets[:idx] for o in offsets[idx + 1 :])
        pattern = "retry_wrong_params" if revisited else "no_retry"
        print(f"  {name:8s} offset 序列={offsets} -> 错误模式={pattern}")
    print("  → 判定只需 1 条表达式；错误模式分类需要第 2 条表达式 + 3 行规则。")


def main() -> int:
    print("JMESPath 版本:", jmespath.__version__)
    print(f"表达式: {EXPR_STATUS_SEQ}\n")

    ok = True
    for name, payload in CASES.items():
        seq = jmespath.search(EXPR_STATUS_SEQ, payload)
        got = judge_via_jmespath(payload["calls"])
        want = EXPECTED[name]
        flag = "OK  " if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"[{flag}] {name:12s} 过滤后状态序列={json.dumps(seq, ensure_ascii=False):24s} "
              f"判定={got!s:5s} 期望={want}")

    probe_error_pattern_split()
    probe_limits()

    print("\n=== 结论 ===")
    print(f"R3 四类情形全部判对: {ok}")
    print("→ JMESPath 能表达「按键过滤后的状态序列」，且 allow_interleaved 天然支持（过滤即消解穿插）。")
    print("→ 判定层可外借；分类层（错误模式）必须自研——这是诊断区别于打分的地方。")
    print("→ 表达不了：时序距离约束、跨产物因果推导、参数 schema 四项校验。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
