"""`tools/node_diff.py` 的判据：C-02 契约（§52.4）里那句「集合 diff」真的看得见吗。

这个脚本存在的理由只有一个：**数条数会骗人**。所以它的测试也以那条为核心——

```text
before: a, b        after: a, c
条数: 2 → 2        集合: -b, +c        ← 后者才是发生过的事
```

其余几条守着"别把不同用例合成一个"（参数化 id 的反转义）与"别把重复当正常"。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import node_diff as nd  # noqa: E402
from arena import review_packet  # noqa: E402


SAMPLE = """tests/test_a.py::test_one
tests/test_a.py::test_two[\\u771f]
tests/test_a.py::test_two[\\u5047\\u91cd\\u8bd5]

1479 tests collected in 2.64s
"""


def test_parse_keeps_only_node_ids():
    ids = nd.parse(SAMPLE)
    assert ids == [
        "tests/test_a.py::test_one",
        "tests/test_a.py::test_two[\\u771f]",
        "tests/test_a.py::test_two[\\u5047\\u91cd\\u8bd5]",
    ], f"汇总行/空行被当成 node id 了：{ids}"


def test_escaped_and_literal_ids_stay_distinct():
    """**不要反转义**：`[\\u771f]` 与 `[真]` 是两个用例，合并后 diff 会漏掉改名。

    pytest 对参数化 id 里的非 ASCII 用 `ascii_escaped`，这与终端编码无关；
    在这里"好心"解一层转义，等价于把两个不同用例的 id 变成同一个字符串。
    """
    ids = nd.parse("tests/x.py::t[\\\\u771f]\ntests/x.py::t[真]\n")
    d = nd.diff(ids, ids)
    assert d["added"] == [] and d["removed"] == []
    assert len(set(nd._unique(ids, "x"))) == 2, "两条不同的 id 被合成了一条"


def test_a_same_size_swap_is_visible():
    """回件 §4.2 的反例：删一条加一条，条数不变。这正是"必须做集合 diff"的理由。"""
    d = nd.diff(["t::a", "t::b"], ["t::a", "t::c"])
    assert d["after_count"] - d["before_count"] == 0, "净增量应当是 0 —— 否则这条测试没抓住要害"
    assert d["removed"] == ["t::b"] and d["added"] == ["t::c"]


def test_duplicate_node_ids_are_refused():
    """重复 id 会让集合把两条并一条 ⇒ diff 结果"看起来干净"，比报错难查得多。"""
    with pytest.raises(nd.NodeDiffError, match="重复"):
        nd.diff(["t::a", "t::a"], ["t::a"])


def test_snapshot_round_trip(tmp_path):
    """快照必须能被自己读回来，且读回来的东西再比一次是空的。"""
    path = tmp_path / "snap.json"
    data = nd.snapshot(path)

    again = nd._load(path)
    assert again["count"] == data["count"] == len(again["ids"])
    assert again["git_sha"] == data["git_sha"]
    assert json.loads(path.read_text(encoding="utf-8"))["ids"] == data["ids"]

    report = nd._report(again, again, limit=5)
    assert "+0 / -0" in report and "WARN" not in report


def test_collection_matches_the_packet_counter():
    """同一个命令、两处解析：数量必须一致。

    评审包把「收集到多少用例」写进对外文档，本脚本把同一批 node id 拿去做 diff。
    两者的解析一旦分叉，契约里"每步记增量"就会和包里那个数互相打脸。
    """
    assert len(nd._collect()) == review_packet._pytest_count()
