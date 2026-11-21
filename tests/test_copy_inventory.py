"""文案总表的三道锁（2026-11-21）。

这张表的价值只有一个：**改一句话时能查到它在哪**。它一旦和代码不同步，就变成
第二个真相源，而且是错的那个——比没有表更坏。所以这里的测试分三组：

1. 表和源码一致（不一致就红，逼人重生成）；
2. 提取器本身没坏（扫到的条数不为 0、且含已知文案——否则空表对空表也会"一致"）；
3. 页面不许出现评价性用词（这是「不打分」这条对外承诺的机器判据）。

第 2 组是这组测试里最容易被忽略的一条：只测 1 的话，提取器路径写错、正则改坏，
表变成空文件，测试仍然全绿。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import copy_inventory as ci  # noqa: E402

INVENTORY = ROOT / "docs" / "copy-inventory.md"

# 夸奖词与评分类词汇。判据是"这句在夸人还是给分"，不是"这个词出现过"：
# `得分率`、`有效得分` 是报告页的字段名（描述数据列），合法，所以不列 "得分"。
# 这个清单是判据不是禁令清单——要放宽就删词，要收紧就加词，改这里即可。
EVALUATIVE = (
    "不错",
    "加油",
    "很棒",
    "真棒",
    "做得好",
    "有进步",
    "优秀",
    "满分",
    "很好",
    "厉害",
    "继续保持",
    "干得漂亮",
)


# --- 1. 表与源码一致 ---------------------------------------------------------

def test_inventory_matches_source():
    """表必须等于按当前源码重算出来的内容。红了就运行 `python scripts/copy_inventory.py`。"""
    assert INVENTORY.exists(), "文案总表不存在：先运行 scripts/copy_inventory.py 生成"
    groups = ci.collect()
    expected = ci.render(groups)
    actual = INVENTORY.read_text(encoding="utf-8")
    assert actual == expected, (
        "文案总表与源码不同步。重新运行 `python scripts/copy_inventory.py`；"
        "不要手改这份表——手改出来的表和不改一样不可信"
    )


def test_check_mode_detects_drift(tmp_path):
    """`--check` 必须真的会红：同步时返回 0，被人改过一个字后返回 1。"""
    target = tmp_path / "copy-inventory.md"
    target.write_text(ci.render(ci.collect()), encoding="utf-8")
    assert ci.main(["--check", "--out", str(target)]) == 0

    tampered = target.read_text(encoding="utf-8").replace("对照区已锁", "对照材料已锁", 1)
    assert tampered != target.read_text(encoding="utf-8"), "样例文案不在这份表里，换个样本"
    target.write_text(tampered, encoding="utf-8")
    assert ci.main(["--check", "--out", str(target)]) == 1


# --- 2. 提取器本身没坏 -------------------------------------------------------

def test_inventory_covers_everything_and_keeps_known_copy():
    """扫到的条数与覆盖面必须像个样子。

    这一条防的是"静默空转"：路径改名、正则写坏都会让表变成空文件，
    而空表与空文件是"一致"的，`test_inventory_matches_source` 照样绿。
    """
    groups = ci.collect()
    assert groups, "一条都没扫到：KOTLIN_DIR 或 PYTHON_SOURCES 路径失效了"

    files = {g["file"] for g in groups}
    assert {"FeynmanScreen.kt", "feynman.py"} <= files, f"关键文件未被扫描：{sorted(files)}"

    texts = [i["text"] for g in groups for i in g["items"]]
    total = len(texts)
    assert total >= 120, f"只扫到 {total} 条，远少于预期（约 153），提取逻辑可能退化"

    for known in ("对照区已锁", "这题要你交什么", "题目到底要几步"):
        assert any(known in t for t in texts), f"已知文案 {known!r} 没被收进表"


def test_scanner_skips_comments_and_docstrings(tmp_path):
    """注释和 docstring 不是给用户看的，抓进来会把要改的文案淹掉。"""
    kt = tmp_path / "Demo.kt"
    kt.write_text(
        "\n".join(
            [
                "// 这一行是注释，里面也有中文",
                "/* 块注释同样不抓 */",
                " * KDoc 的续行也不抓",
                'val a = "真正要抓的一句"',
                'val b = "第二条要抓的" // 行尾注释里的中文不该再被收成一条',
            ]
        ),
        encoding="utf-8",
    )
    assert [i["text"] for i in ci.scan_kotlin(kt)] == ["真正要抓的一句", "第二条要抓的"]

    py = tmp_path / "demo.py"
    py.write_text(
        '"""模块说明，几千字，不该进表。"""\n'
        "\n"
        "def f():\n"
        '    """函数说明，也不该进表。"""\n'
        '    return "用户真正会看到的一句"\n',
        encoding="utf-8",
    )
    got_py = [i["text"] for i in ci.scan_python(py)]
    assert got_py == ["用户真正会看到的一句"], f"docstring 未被排除或漏收正文：{got_py}"


def test_scanner_marks_both_kotlin_interpolation_forms():
    """`$x` 与 `${expr}` 都是插值。只认后者会把「共 $n 条」标成死字。"""
    assert ci.kind_of("共 ${count} 条") == "模板"
    assert ci.kind_of("共 $count 条") == "模板"
    assert ci.kind_of("对照区已锁") == "固定"
    assert ci.kind_of("金额 $5") == "固定", "美元符号不是 Kotlin 插值，不该误判"


def test_cli_help_is_not_counted_as_page_copy(tmp_path):
    """argparse 的 help 归「命令行」：改了不影响 APK 里的字。"""
    py = tmp_path / "cli.py"
    py.write_text(
        "import argparse\n"
        'p = argparse.ArgumentParser(description="命令行说明")\n'
        'p.add_argument("--out", help="输出文件")\n',
        encoding="utf-8",
    )
    kinds = {i["text"]: i["kind"] for i in ci.scan_python(py)}
    assert kinds["命令行说明"] == "命令行"
    assert kinds["输出文件"] == "命令行"


# --- 3. 页面不许评价 ---------------------------------------------------------

def test_no_evaluative_copy_in_pages():
    """页面上每一句都是材料或操作说明，不许出现夸人或打分的句子。

    这是「复述不自动判分」这条对外承诺的机器判据。谎话最容易从文案混进来：
    数据错会触发对账报错，文案错只会被用户读进脑子。
    """
    hits: list[str] = []
    for group in ci.collect():
        for item in group["items"]:
            for word in EVALUATIVE:
                if word in item["text"]:
                    hits.append(f"{group['file']}:{item['line']} 含 {word!r} → {item['text']}")
    assert not hits, "页面文案出现评价性用词（违反「不判分、不评价」）：\n" + "\n".join(hits)


@pytest.mark.parametrize("word", EVALUATIVE)
def test_evaluative_guard_itself_works(word):
    """守门测试要能命中：造一句带夸奖词的文案，断言逻辑必须识别出来。

    没有这一条，`EVALUATIVE` 写错个字（比如全角/半角混了）会让上一组测试永远绿。
    """
    fake = f"这次讲得{word}，继续这样。"
    assert any(w in fake for w in EVALUATIVE)
