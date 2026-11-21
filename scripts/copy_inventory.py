"""从源码提取所有用户可见文案，生成可检索的总表。

为什么要有这个脚本：文案散在 5 个源文件、161 条，改一句要先找到它在哪。
手写一张表一定会腐烂——改代码不改表，表就成了第二真相源，而且是错的那个。
所以表**整份由脚本生成**，不许手改；`--check` 用来拦"改了代码没重生成表"。

它回答的是两个不同的问题，别混：
- 「改完了什么时候生效」→ 永远要重新编译打包，APK 资源改不了（见 --check 输出的提示）。
- 「改一句话该去哪改」→ 这张表。

提取口径（都可复核）：
- Kotlin：逐行找含中文字符串字面量，跳过 `//`、`*`、`/*` 开头的注释行。
  多行拼接的相邻两段算两条，各自记自己的行号——拼起来看会丢定位。
- Python：走 ast，排除 docstring（那是给维护者看的，不是给用户看的）；
  f-string 用源码原样（`ast.get_source_segment`），因为要保留占位符本身。

用法：
    python scripts/copy_inventory.py            # 重新生成 docs/copy-inventory.md
    python scripts/copy_inventory.py --check    # 只校验表与代码是否同步，不同则退出 1
"""

from __future__ import annotations

import argparse
import ast
import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "docs" / "copy-inventory.md"

# Android 侧：用户能看见的字符串都在这几个文件里（.kt 全部扫，不写死清单，
# 免得以后加页面忘了加进名单——漏扫一份，那张表就会悄悄少一半）。
KOTLIN_DIR = ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "arena" / "companion"
# 数据层：页面上展示的材料由它产出（三问提纲、提示条标签、来源说明、错误信息）。
PYTHON_SOURCES = [ROOT / "src" / "arena" / "feynman.py"]

_CJK = re.compile(r"[\u4e00-\u9fa5]")
_STR_LIT = re.compile(r'"((?:[^"\\]|\\.)*)"')
_COMMENT_LINE = re.compile(r"^\s*(?://|/\*|\*)")


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


def _unescape(lit: str) -> str:
    """把 Kotlin 字面量里的 `\\"` 还原成 `"`，其余转义原样保留（`\\n` 要看得见）。"""
    return lit.replace('\\"', '"')


def scan_kotlin(path: Path) -> list[dict]:
    """逐行提取含中文字符串字面量。行号按原文算，跳过的注释行不占位。

    已知边界：只按"整行以注释符号开头"过滤，**行内块注释**（`val a = "x" /* 说明 */`）
    或行尾 `//` 里的中文仍会被当成文案收进来。当前 5 个文件里没有这种写法
    （2026-11-21 扫过一遍），所以没为它加解析；真出现时表里会多出几条假文案，
    肉眼能看出来，但不会报错。要根治得上真正的词法扫描，那是另一个量级的活。
    """
    out: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if _COMMENT_LINE.match(line):
            continue
        for lit in _STR_LIT.findall(line):
            text = _unescape(lit)
            if has_cjk(text):
                out.append({"line": lineno, "text": text})
    return out


def _python_docstrings(tree: ast.AST) -> set[str]:
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                seen.add(doc)
    return seen


def _cli_strings(tree: ast.AST) -> set[int]:
    """命令行文案的节点 id：`add_argument(help=...)` / `ArgumentParser(description=...)`。

    这一条不是人工判断，是 ast 结构判断——argparse 的说明文字必然是这两种调用的
    关键字值。分开标它不是洁癖：命令行 help 改了不影响 APK 里的任何一个字，
    混在同一类里，改页面文案的人会白读五条命令行说明。
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in {"add_argument", "ArgumentParser"}:
            continue
        for kw in node.keywords:
            if kw.arg in {"help", "description", "epilog"}:
                for child in ast.walk(kw.value):
                    if isinstance(child, ast.Constant):
                        ids.add(id(child))
    return ids


def scan_python(path: Path) -> list[dict]:
    """走 ast：普通常量记「固定」，f-string 记「模板」并保留源码原文。

    docstring 排除的判据是"这行字会不会出现在用户眼前"，不是"它是不是字符串"。
    模块头的设计说明有几千字，混进文案表会把真正要改的那些淹掉。
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    docs = _python_docstrings(tree)
    cli = _cli_strings(tree)

    templated: set[int] = set()
    out: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            segment = ast.get_source_segment(source, node) or ""
            if has_cjk(segment):
                inner = [c for c in ast.walk(node) if isinstance(c, ast.Constant)]
                for child in inner:
                    templated.add(id(child))
                kind = "命令行" if any(id(c) in cli for c in inner) else "模板"
                out.append({"line": node.lineno, "text": segment, "kind": kind})
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if not has_cjk(text) or text in docs or id(node) in templated:
                continue
            out.append(
                {
                    "line": node.lineno,
                    "text": text,
                    "kind": "命令行" if id(node) in cli else "固定",
                }
            )
    return sorted(out, key=lambda item: item["line"])


def collect() -> list[dict]:
    """按文件分组返回。组间顺序固定（Android 按文件名，数据层固定），保证 --check 稳定。"""
    groups: list[dict] = []
    for kt in sorted(KOTLIN_DIR.glob("*.kt")):
        items = scan_kotlin(kt)
        if items:
            groups.append(
                {
                    "file": kt.name,
                    "layer": "Android 页面",
                    "items": [{"line": i["line"], "text": i["text"], "kind": kind_of(i["text"])} for i in items],
                }
            )
    for py in PYTHON_SOURCES:
        items = scan_python(py)
        if items:
            groups.append({"file": py.name, "layer": "数据层", "items": items})
    return groups


_KOTLIN_INTERP = re.compile(r"\$\{|\$[A-Za-z_]")


def kind_of(text: str) -> str:
    """含插值的是运行时填空，其余是编译进去的死字。

    Kotlin 有 `$name` 和 `${expr}` 两种插值写法，只认后者会把 `"共 $n 条"` 误判成死字，
    于是表里给出一条"固定"文案、实际每次显示不同——这种错判比漏一条更坏，
    因为改它的人会以为改的是通用文案。
    """
    return "模板" if _KOTLIN_INTERP.search(text) else "固定"


def _cell(text: str) -> str:
    """表格安全：竖线要转义，换行要变成看得见的 `\\n`，否则表格会被撑破。"""
    return (
        text.replace("|", "\\|")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\t", " ")
    )


def render(groups: list[dict], check_hint: bool = False) -> str:
    total = sum(len(g["items"]) for g in groups)
    android = sum(len(g["items"]) for g in groups if g["layer"] == "Android 页面")
    data = total - android
    fixed = sum(1 for g in groups for i in g["items"] if i["kind"] == "固定")
    tpl = sum(1 for g in groups for i in g["items"] if i["kind"] == "模板")
    cli = sum(1 for g in groups for i in g["items"] if i["kind"] == "命令行")

    lines = [
        "# 用户可见文案总表",
        "",
        "> **本文件由 `scripts/copy_inventory.py` 生成，不许手改。** 手改会让表和代码不一致，",
        "> 而表一旦和代码不一致，它就是错的第二真相源。校验：`python scripts/copy_inventory.py --check`。",
        "",
        f"共 **{total}** 条：Android 页面 {android} 条（编译进 APK）、数据层 {data} 条（生成进 `assets/*.json`）。",
        f"其中固定文案 {fixed} 条、含运行时占位符 {tpl} 条、命令行说明 {cli} 条。",
        "",
        "## 两个问题，别混",
        "",
        "1. **改完什么时候生效？** 永远要重新编译打包。这些字要么在 Kotlin 源码里、要么在",
        "   `assets/` 里的 JSON 里，两者都随 APK 一起编译。想让文案改完立刻生效，只能改成运行时",
        "   从网络或外部存储读——而这一页的既定纪律是「不发任何网络请求」，所以不走那条路。",
        "2. **改一句话去哪改？** 查下面这张表，`文件:行` 就是位置。",
        "",
        "## 怎么读这张表",
        "",
        "| 类别 | 含义 | 改它要做什么 |",
        "|---|---|---|",
        "| 固定 | 编译进 APK 的死字 | 改源码 → 重新编译 → 重装 |",
        "| 模板 | 含 `${…}` / `$x` / f-string 占位符 | 只能改变量以外的字，别动占位符名 |",
        "| 命令行 | argparse 的 help / description | 改了不影响 APK，页面看不到 |",
        "",
        "「命令行」这一类是 **ast 结构判定**（`add_argument(help=…)` / `ArgumentParser(description=…)`），",
        "不是人工标注——任何需要人工同步的列，都会先于表本身腐烂。",
        "",
        "另有一类**页面看不到但不是命令行**的字：`raise` 抛出的诊断信息（如",
        "「错误模式 X 在词表里没有 description」）。它们看起来和普通固定文案一样，表不替人分，",
        "因为那只能靠读代码判断。改到这类字时留意一下它是否真会显示给用户。",
        "",
        "数据层的文案多走一步：改完 `feynman.py` 后要重新生成资源包，否则 APK 里仍是旧内容——",
        "",
        "```bash",
        "python -m arena.feynman --from-report android/app/src/main/assets/sample-report.json \\",
        "    --task tasks/dbg-001-order-report.yaml \\",
        "    --out android/app/src/main/assets/feynman-prompt.json",
        "```",
        "",
    ]

    for group in groups:
        lines.append(f"### `{group['file']}`（{len(group['items'])} 条 · {group['layer']}）")
        lines.append("")
        lines.append("| 行 | 类别 | 文案 |")
        lines.append("|---:|---|---|")
        for item in group["items"]:
            lines.append(f"| {item['line']} | {item['kind']} | {_cell(item['text'])} |")
        lines.append("")

    if check_hint:
        lines.append("> 校验失败：上表与源码不一致，请重新运行 `python scripts/copy_inventory.py`。")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成/校验用户可见文案总表")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件；不一致时退出 1")
    parser.add_argument("--out", default=str(OUT_PATH), help="输出路径")
    args = parser.parse_args(argv)

    groups = collect()
    if not groups:
        print("一条文案都没扫到：路径或过滤器写错了，先修脚本再谈生成", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    current = render(groups)

    if args.check:
        if not out_path.exists():
            print(f"表不存在：{out_path}。先运行一次生成。", file=sys.stderr)
            return 1
        existing = out_path.read_text(encoding="utf-8")
        if existing == current:
            print(f"文案总表与源码一致（{sum(len(g['items']) for g in groups)} 条）。")
            return 0
        diff = list(
            difflib.unified_diff(
                existing.splitlines(),
                current.splitlines(),
                fromfile=str(out_path.name) + "（当前）",
                tofile=str(out_path.name) + "（按源码应有）",
                lineterm="",
                n=1,
            )
        )
        print("文案总表与源码不一致，差异如下（前 40 行）：", file=sys.stderr)
        for line in diff[:40]:
            print(line, file=sys.stderr)
        print("\n重新运行 `python scripts/copy_inventory.py` 生成。", file=sys.stderr)
        return 1

    out_path.write_text(current, encoding="utf-8")
    print(
        f"已写入 {out_path}（{sum(len(g['items']) for g in groups)} 条，"
        f"{len(groups)} 个文件）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
