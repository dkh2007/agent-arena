"""把一次全量测试跑成可归档的证据（Markdown + JSON + 索引）。

## 为什么要这个脚本

比赛材料里写「1556 个用例全绿」是一句话；评委要看的是**这句话怎么来的、什么时候来的、
对应哪个提交、换台机器还能不能复现**。手工截图和口头数字都会被当成自评，
所以这里把每次运行钉成一份带 commit / 环境 / 逐文件计数的存档。

## 它不做什么

- 不修改任何测试结果，也不把失败写成通过：失败就照原样写进档案，退出码非 0。
- 不猜缺失字段：拿不到 commit、拿不到 junit 就报错退出，不产出一份「看起来完整」的空档案。
- 不合并多次运行：一份档案对应一次运行（`--reuse` 也只认单份 junit 文件）。

## 用法

    python scripts/test_evidence.py                     # 跑全量并归档
    python scripts/test_evidence.py --reuse out.xml     # 用已有 junit 归档（不重跑）
    python scripts/test_evidence.py --no-run --note "..."  # 只记录环境，不跑（不推荐）
    python scripts/test_evidence.py --rerender docs/test-evidence/xxx.json  # 改了模板只重排 md

产物：
    docs/test-evidence/<日期>-<sha7>.md
    docs/test-evidence/<日期>-<sha7>.json
    docs/test-evidence/INDEX.md（追加一行）
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "docs" / "test-evidence"

# 本脚本自己的输出目录。判定「工作区是否干净」时要把它排除掉：
# 否则这份档案永远在描述一个「因为要写这份档案而变脏」的工作区，
# 等于自己给自己抹黑，也和事实不符（跑测试时真正该干净的是代码）。
OWN_OUTPUT_PREFIX = "docs/test-evidence/"


def _run(cmd: list[str], cwd: Path = ROOT) -> str:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    ).stdout.strip()


def split_status(lines: list[str]) -> tuple[list[str], list[str]]:
    """把 `git status --porcelain` 的行分成（本工具自身产物, 其他改动）。

    porcelain 每行前 3 个字符是状态码 + 空格，之后是路径；
    `?? docs/test-evidence/` 这种整目录未跟踪时路径就是目录名本身。
    """
    own: list[str] = []
    others: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        path = line[3:].strip()
        # 重命名是 `R  old -> new`，取新路径判断归属
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path == OWN_OUTPUT_PREFIX.rstrip("/") or path.startswith(OWN_OUTPUT_PREFIX):
            own.append(line)
        else:
            others.append(line)
    return own, others


def git_facts() -> dict[str, object]:
    """提交与环境事实。拿不到就抛错——没有 commit 的测试记录没法回溯。"""
    sha = _run(["git", "rev-parse", "HEAD"])
    if not sha:
        raise SystemExit("拿不到 git HEAD：这份档案要能对上提交，先确认在仓库里")
    subject = _run(["git", "log", "-1", "--pretty=%s"])
    status = _run(["git", "status", "--porcelain"])
    own, others = split_status(status.splitlines())
    return {
        "commit": sha,
        "commit_short": sha[:7],
        "commit_subject": subject,
        # dirty 只认「本工具输出以外」的改动，这样「干净」才有意义
        "dirty": bool(others),
        "dirty_files": others,
        "own_output_files": own,
    }


def dependency_facts() -> dict[str, str]:
    """读 requirements.txt 的 pin，不现场 import：记录的是「声明要装的版本」。"""
    out: dict[str, str] = {}
    path = ROOT / "requirements.txt"
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" not in line:
            continue
        name, _, version = line.partition("==")
        out[name.strip()] = version.strip()
    return out


def parse_junit(path: Path) -> dict[str, object]:
    """按文件聚合 junit。junit 是 pytest 自己写的，比解析 `-q` 的末行更可靠。"""
    root = ET.parse(path).getroot()
    suites = root.findall("testsuite") or [root]
    totals = Counter()
    seconds = 0.0
    by_file: Counter[str] = Counter()
    failed_cases: list[str] = []
    skipped_cases: list[str] = []

    for suite in suites:
        totals["tests"] += int(suite.get("tests", 0))
        totals["failures"] += int(suite.get("failures", 0))
        totals["errors"] += int(suite.get("errors", 0))
        totals["skipped"] += int(suite.get("skipped", 0))
        seconds += float(suite.get("time", 0.0))

        for case in suite.iter("testcase"):
            classname = case.get("classname", "")
            # classname 形如 tests.test_training 或 tests.test_training.TestX
            parts = classname.split(".")
            key = ".".join(parts[:2]) if len(parts) >= 2 else classname
            by_file[key] += 1
            name = f"{classname}::{case.get('name')}"
            if case.find("failure") is not None or case.find("error") is not None:
                failed_cases.append(name)
            elif case.find("skipped") is not None:
                skipped_cases.append(name)

    passed = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    return {
        "tests": totals["tests"],
        "passed": passed,
        "failures": totals["failures"],
        "errors": totals["errors"],
        "skipped": totals["skipped"],
        "seconds": round(seconds, 2),
        "by_file": dict(sorted(by_file.items())),
        "failed_cases": failed_cases,
        "skipped_cases": skipped_cases,
    }


def selfcheck_counts() -> dict[str, int]:
    """数「自校验」与「变异」两类测试。

    口径写死在这里，和计划书一致：函数名里带 tamper / reject 的算自校验，
    带 mutat 的算变异。用正则逐文件扫，不依赖 pytest 的 node id 格式。
    """
    selfcheck = mutation = 0
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        # 名字里任意位置出现关键词都算：tamper / reject（自校验）、mutat（变异）。
        # 不要写成 `def test_[a-z0-9_]*\b(tamper|reject)`——`*` 会一路吞到名字末尾，
        # 回溯后中间位置又不是词边界，结果是恒为 0（2026-11-21 实测踩过）。
        selfcheck += len(re.findall(r"def test_\w*(?:tamper|reject)\w*\s*\(", text))
        mutation += len(re.findall(r"def test_\w*mutat\w*\s*\(", text))
    return {"selfcheck": selfcheck, "mutation": mutation}


def run_pytest(junit_path: Path) -> int:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-q",
        f"--junitxml={junit_path}",
    ]
    print("$", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT)


def render_markdown(meta: dict[str, object]) -> str:
    t = meta["tests"]  # type: ignore[assignment]
    env = meta["env"]  # type: ignore[assignment]
    git = meta["git"]  # type: ignore[assignment]
    quality = meta["quality"]  # type: ignore[assignment]

    lines: list[str] = []
    lines.append(f"# 测试记录 · {meta['run_at']}")
    lines.append("")
    lines.append(
        "> 本文件由 `scripts/test_evidence.py` 生成，未经手工编辑。"
        "任何数字都可以用文末命令在本机重跑复现。"
    )
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    if t["failures"] == 0 and t["errors"] == 0:
        lines.append(
            f"**{t['passed']} 个用例通过 / 共 {t['tests']} 个收集**，"
            f"失败 {t['failures']}、错误 {t['errors']}、跳过 {t['skipped']}；"
            f"总耗时 {t['seconds']:.1f} 秒。"
        )
    else:
        lines.append(
            f"**未全绿**：通过 {t['passed']} / 共 {t['tests']}，"
            f"失败 {t['failures']}、错误 {t['errors']}、跳过 {t['skipped']}。"
            "下面「失败用例」一节列出了每一个用例名。"
        )
    if git["dirty"]:
        lines.append("")
        lines.append(
            f"⚠️ 运行时刻工作区有 {len(git['dirty_files'])} 个未提交改动，"
            "本档案对应的是「工作区状态」，不是干净的提交树。未提交的文件："
        )
        for item in git["dirty_files"]:  # type: ignore[union-attr]
            lines.append(f"- `{item}`")
        lines.append("")
        lines.append(
            "若这些文件只是文档或证据产物，测试结论不受影响；若是 `src/` 或 `tests/` 下的代码，"
            "这份档案就不能代表该提交——先提交再重跑。"
        )
    own_output = git.get("own_output_files") or []  # type: ignore[union-attr]
    if own_output:
        lines.append("")
        lines.append(
            f"（本档案自身写在 `{OWN_OUTPUT_PREFIX}` 下，共 {len(own_output)} 个改动，"
            "不计入脏树判定——否则每份档案都会因为「要写这份档案」而自称不干净。）"
        )
    note = str(meta.get("note") or "").strip()
    if note:
        lines.append("")
        lines.append("## 备注（运行者所加，不是自动测出来的）")
        lines.append("")
        lines.append(note)
    lines.append("")
    lines.append("## 运行身份")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 提交 | `{git['commit']}` |")
    lines.append(f"| 提交说明 | {git['commit_subject'] or '（空）'} |")
    lines.append(f"| 分支 | `{meta['branch']}` |")
    lines.append(f"| 工作区干净 | {'否' if git['dirty'] else '是'}{'（已排除本档案自身）' if not git['dirty'] and own_output else ''} |")
    lines.append(f"| Python | {env['python']} |")
    lines.append(f"| 平台 | {env['platform']} |")
    lines.append(f"| pytest | {env['pytest']} |")
    lines.append(f"| 测试文件 | {quality['test_files']} 个 |")
    lines.append("")
    lines.append("依赖（取自 `requirements.txt` 的 pin，不是运行期探测）：")
    lines.append("")
    for name, version in env["dependencies"].items():  # type: ignore[union-attr]
        lines.append(f"- `{name}=={version}`")
    lines.append("")
    lines.append("## 口径与自校验")
    lines.append("")
    lines.append("| 指标 | 数量 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(
        f"| 自校验类测试 | {quality['selfcheck']} | 用例名含 `tamper` / `reject`："
        "把报告字段、判据、评审包改掉后必须报错 |"
    )
    lines.append(
        f"| 变异类测试 | {quality['mutation']} | 用例名含 `mutat`：改一处实现，断言行为随之变化 |"
    )
    lines.append("")
    lines.append("## 逐文件用例数")
    lines.append("")
    lines.append("| 测试文件 | 用例数 |")
    lines.append("|---|---|")
    for name, count in t["by_file"].items():  # type: ignore[union-attr]
        lines.append(f"| `{name}` | {count} |")
    lines.append("")
    if t["failed_cases"]:  # type: ignore[index]
        lines.append("## 失败用例")
        lines.append("")
        for case in t["failed_cases"]:  # type: ignore[union-attr]
            lines.append(f"- `{case}`")
        lines.append("")
    if t["skipped_cases"]:  # type: ignore[index]
        lines.append("## 跳过用例")
        lines.append("")
        for case in t["skipped_cases"]:  # type: ignore[union-attr]
            lines.append(f"- `{case}`")
        lines.append("")
    lines.append("## 怎么复现")
    lines.append("")
    lines.append("```bash")
    lines.append("python scripts/test_evidence.py")
    lines.append("# 或者只跑一遍看结果：")
    lines.append("python -m pytest tests -q")
    lines.append("```")
    lines.append("")
    lines.append(
        f"本次命令：`{meta['command']}`；"
        f"junit 原件：`{meta['junit_name']}`（未入库，重跑即可再生）。"
    )
    lines.append("")
    return "\n".join(lines)


def rerender(json_path: Path) -> int:
    """按归档 JSON 重排同一份运行的 Markdown（模板改了不必重跑十分钟）。

    只重排、不重算：所有数字都来自 JSON 里记录的那一次运行，
    所以文首「未经手工编辑」的声明仍然成立。它**不追加**索引行。
    """
    if not json_path.exists():
        raise SystemExit(f"档案 JSON 不存在：{json_path}")
    meta = json.loads(json_path.read_text(encoding="utf-8"))
    md_path = json_path.with_suffix(".md")
    md_path.write_text(render_markdown(meta), encoding="utf-8")
    print(f"重排 {md_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跑全量测试并归档成证据")
    parser.add_argument("--reuse", help="复用已有 junit xml，不重新跑测试")
    parser.add_argument("--rerender", help="按已有档案 JSON 重排同名 .md，不跑测试、不写索引")
    parser.add_argument("--out-dir", default=str(EVIDENCE_DIR), help="归档目录")
    parser.add_argument("--note", default="", help="本次运行的备注，写进档案")
    args = parser.parse_args(argv)

    if args.rerender:
        return rerender(Path(args.rerender))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_at = datetime.now().astimezone()
    stem = f"{run_at:%Y-%m-%d-%H%M}-"

    junit_path: Path
    exit_code = 0
    if args.reuse:
        junit_path = Path(args.reuse)
        if not junit_path.exists():
            raise SystemExit(f"junit 文件不存在：{junit_path}")
        command = f"--reuse {junit_path}"
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
        tmp.close()
        junit_path = Path(tmp.name)
        exit_code = run_pytest(junit_path)
        command = "python scripts/test_evidence.py"

    git = git_facts()
    stem += git["commit_short"]  # type: ignore[operator]

    tests = parse_junit(junit_path)
    quality = selfcheck_counts()
    quality["test_files"] = len(list((ROOT / "tests").glob("test_*.py")))

    meta = {
        "run_at": run_at.isoformat(timespec="seconds"),
        "command": command,
        "note": args.note,
        "git": git,
        "branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "env": {
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "pytest": _run([sys.executable, "-m", "pytest", "--version"]).splitlines()[0]
            if _run([sys.executable, "-m", "pytest", "--version"])
            else "unknown",
            "dependencies": dependency_facts(),
        },
        "quality": quality,
        "tests": tests,
        "junit_name": junit_path.name,
    }

    md_path = out_dir / f"{stem}.md"
    json_path = out_dir / f"{stem}.json"
    md_path.write_text(render_markdown(meta), encoding="utf-8")
    json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    index_path = out_dir / "INDEX.md"
    if not index_path.exists():
        index_path.write_text(
            "# 测试记录索引\n\n"
            "> 由 `scripts/test_evidence.py` 追加，勿手改。每行对应一次真实运行。\n\n"
            "| 时间 | 提交 | 通过 / 总数 | 失败 | 耗时（秒） | 档案 |\n"
            "|---|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    row = (
        f"| {run_at:%Y-%m-%d %H:%M} | `{git['commit_short']}` | "
        f"{tests['passed']} / {tests['tests']} | {tests['failures'] + tests['errors']} | "
        f"{tests['seconds']:.1f} | [{md_path.name}]({md_path.name}) |\n"
    )
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(row)

    if not args.reuse:
        junit_path.unlink(missing_ok=True)

    print(f"写入 {md_path}")
    print(f"写入 {json_path}")
    print(f"索引 {index_path}")
    print(
        f"结果：{tests['passed']} passed / {tests['tests']} total，"
        f"失败 {tests['failures']}、错误 {tests['errors']}"
    )
    return 0 if exit_code == 0 and tests["failures"] == 0 and tests["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
