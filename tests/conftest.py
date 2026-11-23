"""只做一件事：记下**本次会话到底收集到多少个用例**，供评审包计数交叉校验。

`tests/test_review_packet.py::test_count_agrees_with_this_session` 要用它当第二条
真相源——生产代码那条路是"起子进程、全新解释器重新导入、再数"，这条路是"当前
进程里 pytest 自己数过的"。两者相等才说明包里那个数字来自一次真收集。

为什么不用 `session.testscollected` 了事：`-k` / `-m` 在收集之后 deselect，
那个属性只剩**选中的**（实测 `-k` 命中 1 条时它等于 1，而仓库有 1908 条）。
被 deselect 的数量由 `pytest_deselected` 通知，加回来才是收集总数。

xdist（`-n`）下每个 worker 各数一遍、语义不同，那种情况由用例自己跳过，
不在这里猜。
"""

from __future__ import annotations

_deselected = 0


def pytest_sessionstart(session) -> None:
    global _deselected
    _deselected = 0


def pytest_deselected(items) -> None:
    global _deselected
    _deselected += len(items)


def pytest_collection_finish(session) -> None:
    # 用 `len(session.items)` 而不是 `session.testscollected`：后者在这个时点还没被赋值
    # （实测恒为 0，会让总数少算被选中的那部分）。
    session.config._arena_collected_total = len(session.items) + _deselected
