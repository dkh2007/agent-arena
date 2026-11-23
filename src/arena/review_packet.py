"""把「手工维护的评审判断」+「仓库实况快照」拼成一份可交付的外部评审包。

## 为什么需要这个脚本

v1 评审包是手写的。三天后它里面的数字（`14 passed`、`权重 [1,2,1,1,1]`）就和仓库不符了，
而没人会发现——传给外部 AI 之后，它是在评一个**已经不存在的版本**。
手工维护快照必然失真，这不是粗心，是结构问题。

本脚本把快照改成"从仓库现算"，并把 git sha 写进文件头，使**过期可检测**。

注意一个自指陷阱：评审包自己要进下一个提交。若正文含 HEAD 的 sha / 提交列表，
提交一落地正文就"过期"，`--check` 会永远误报。所以快照段固定在**生成锚点**
（文件头的 `snapshot-sha`），校验时按该锚点重放；后续提交不改变锚点处的历史。

## 两部分的分工

- `docs/review-packet-manual.md`：人维护。写判断、决策、待拍板清单、要问的问题。
  约定：**手工部分不写具体数字**（测试数、权重、分数），那些一律走占位符，
  否则会重新引入"数字漂移"。
- 本脚本：填 `{{AUTO_*}}` 占位符，并在写入前做匿名守卫。

## 用法

    python src/arena/review_packet.py            # 生成 docs/review-packet.md
    python src/arena/review_packet.py --check    # 只检查是否落后于 HEAD，落后则 exit 1
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MANUAL = ROOT / "docs" / "review-packet-manual.md"
DEFAULT_OUT = ROOT / "docs" / "review-packet.md"
TASK = ROOT / "tasks" / "dbg-001-order-report.yaml"
TAXONOMY = ROOT / "docs" / "skill-taxonomy-v0.yaml"

# TD-036：评分策略对账与执行路径共用同一个函数，不在本文件复制常量（避免第二真相源）。
# 脚本模式（python src/arena/review_packet.py）下 sys.path[0] 是 src/arena，
# 需显式补 src 才能 import arena 包。
_SRC = str(ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
from arena.diagnosis_report import ReportError, _check_scoring_policy  # noqa: E402

# 匿名纪律：命中即拒绝写入。宁可生成失败，也不让真名/校名流出去。
# 词表以 Unicode 码点转义保存：避免守卫自身在源码中出现被禁字面量；运行期逐字等价。
FORBIDDEN = ("\u6b66\u6c49\u5927\u5b66", "\u6b66\u5927", "\u5f18\u6bc5", "\u8463\u6587\u6c38", "\u5c0fD")

PLACEHOLDERS = ("{{AUTO_SNAPSHOT}}", "{{AUTO_SCORES}}", "{{AUTO_TASK}}", "{{AUTO_ENV}}")

# Q9：实测结果行是可重放的。--check 不重跑一遍 pytest，而是把包里已记录的那行
# 原样喂回 build()，避免"测试跑两次 / 偶发波动导致假 STALE"。
TEST_RESULT_RE = re.compile(r"^- 单元测试实测：.*$", re.M)

# 夹具摘要行的格式（由 diagnosis_report._run_all_fixtures 打印）：
#   OK DQ raw-empty   ratio=0.0000 (0/4) counterfactual=1 pass=2 fail=2 blocked=1 unscored=0
SCORE_RE = re.compile(
    r"^(OK|FAIL)\s+(DQ)?\s*(\S+)\s+ratio=([\d.]+)\s+\((\d+)/(\d+)\)"
    r"\s+counterfactual=(\d+)\s+pass=(\d+)\s+fail=(\d+)\s+blocked=(\d+)\s+unscored=(\d+)$"
)


class PacketError(RuntimeError):
    """评审包无法生成。宁可失败，也不产出半成品。"""


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise PacketError(f"命令失败（exit={proc.returncode}）：{' '.join(cmd)}\n{proc.stderr[:500]}")
    return proc.stdout


def _git_sha(ref: str = "HEAD") -> str:
    return _run(["git", "rev-parse", "--short", ref]).strip()


def _git_log(ref: str) -> str:
    return _run(
        ["git", "log", "--pretty=format:- `%h` %s（%ad）", "--date=short", "-8", ref]
    ).strip()


def _parse_collect_count(out: str) -> int:
    """从 `--collect-only` 输出数用例，**两条独立路径必须一致**。

    A：汇总行 `N tests collected in Xs`（pytest 自己数的）
    B：逐行 nodeid 的条数（我们自己数的，`-q --collect-only` 一行一个 nodeid）

    A≠B 说明输出格式变了或正则抓错了数。宁可报错，也不写下一个没人核对过的数——
    这个数的唯一用途就是让外部知道"测试规模多大"，抓错它会静默骗人。
    """
    m = re.search(r"(\d+)\s+tests?\s+collected", out)
    if not m:
        raise PacketError("未能从 pytest --collect-only 输出中解析用例数")
    listed = sum(1 for ln in out.splitlines() if "::" in ln)
    summary = int(m.group(1))
    if listed != summary:
        raise PacketError(
            f"两条计数路径不一致：汇总行说 {summary} 个，逐行 nodeid 数出 {listed} 个"
            "（pytest 输出格式变了？先核对再生成，别让包里落一个错的数）"
        )
    if summary < 1:
        raise PacketError(f"收集到 {summary} 个用例：这次收集本身就没成功，不能往下写")
    return summary


# 进程内缓存：同一份 `tests/` 快照只数一次。
#
# 为什么缓存不违背本脚本的立身之本：`_scores()` 明确不缓存，因为它的对象是"代码行为"，
# 一变就该重算。而用例数是**测试文件集合的静态属性**，同一进程里改它的唯一方式是
# 改 `tests/*.py`——下面用文件指纹盯着这件事，指纹一变立刻真收集。
# 所以缓存跨越的是"同一快照的多次调用"，不是"跨时间"，与 `--check` 重放记录行是同一逻辑。
_COUNT_CACHE: tuple[tuple, int] | None = None


def _tests_fingerprint() -> tuple[tuple[str, int, int], ...]:
    """`tests/*.py` 的 (名, 字节数, mtime_ns)。约 60 次 stat，微秒级。"""
    return tuple(
        sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns)
               for p in (ROOT / "tests").glob("*.py"))
    )


def _pytest_count(*, refresh: bool = False) -> int:
    """真实起一次 `pytest --collect-only` 数用例；同快照进程内只起一次。

    `refresh=True` 强制重数（测试用它验证"绕过缓存走的仍是真收集"）。
    """
    global _COUNT_CACHE
    key = _tests_fingerprint()
    if not refresh and _COUNT_CACHE is not None and _COUNT_CACHE[0] == key:
        return _COUNT_CACHE[1]
    out = _run([sys.executable, "-m", "pytest", "tests/", "-q", "--collect-only"])
    n = _parse_collect_count(out)
    _COUNT_CACHE = (key, n)
    return n


def _reset_count_cache() -> None:
    """仅测试用：让下一次 `_pytest_count()` 走真收集。"""
    global _COUNT_CACHE
    _COUNT_CACHE = None


FAST_TEST_LINE = "- 单元测试实测：`--fast` 模式未执行测试（对外发正式评审包时请去掉 --fast）"

# 生成期间给子进程的"我要写入的用例数"。见 `recorded_count_error()` 与 `_pytest_result()`：
# 生成那一跑发生在写文件**之前**，磁盘上仍是上一代包，靠它才能给出正确参照物。
COUNT_ENV = "ARENA_PACKET_COUNT"


def recorded_count_error(body: str, live: int, generating: int | None) -> str | None:
    """「包里记的用例数」是否对得上现况。返回 None 表示通过，否则返回失败原因。

    两个分支对应两种时刻，参照物不同：

    - `generating is not None`：**正在生成**。磁盘那份是**将被替换**的旧件，
      拿它比对等于用上几代产物判这一代对不对——生成历史越长越容易红，与代码状态无关。
      此时唯一正确的参照物是生成器准备写入的那个数；两条计数路径（`--collect-only`
      与全量运行）不一致仍然要报，那是真的不一致。
    - `generating is None`：**正常跑测试**。磁盘那份就是要提交的那份，比它。
    """
    if generating is not None:
        if generating != live:
            return (
                f"生成器准备写入 {generating}，实际收集到 {live}："
                "两条计数路径（`--collect-only` 与全量运行）不一致"
            )
        return None
    if f"**{live} 个用例被收集**" not in body:
        return f"评审包里的测试数与实际（{live}）不一致"
    return None

# TD-040：实测行不只是给人读的，它决定这份包能不能对外发。
# 判据只有两条——有没有实测数据、失败数是否为 0。不解析 passed 数（可由 total 推出，
# 解析三处反而会出现三处不一致的可能）。
_FAILED_RE = re.compile(r"(\d+)\s+failed")


def _test_gate(line: str) -> tuple[bool, str]:
    """(可对外发布, 不可发布的原因)。生成与校验共用，避免两套口径。

    判定只看**记录本身**，不看当前工作区：所以它能拦「生成时就带着失败」，
    拦不住「生成时全绿、之后代码才变红」——后者要靠 `--check --full` 重跑。
    """
    if not line.strip() or line.strip() == FAST_TEST_LINE.strip() or "未执行测试" in line:
        return False, "无实测数据（`--fast` 生成或实测行缺失）"
    failed = _FAILED_RE.search(line)
    if failed and int(failed.group(1)) > 0:
        return False, f"实测含 {failed.group(1)} 条失败"
    return True, ""


def _block_notice(reason: str) -> str:
    """非全绿时插进正文的显式警告。

    必须写进正文而不是只写日志：评审包是**给别人看的文件**，
    日志会随终端滚动消失，文件会被发给外部 AI 或评委。
    """
    return (
        f"> ⚠️ **本包不可对外发布**：{reason}。\n"
        "> 评审包是给外部评审者看的材料，带失败记录或没有实测数据都会直接损害可信度。\n"
        "> 修好测试后去掉 `--fast` 重新生成；确认无意外的失败再发。"
    )



def _parse_pytest_summary(stdout: str) -> tuple[str, int]:
    """从 pytest -q 末行解析 (摘要, 总数)。抽成纯函数，失败/错数才能被单测覆盖。"""
    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    tail = lines[-1] if lines else ""
    counts: dict[str, int] = {}
    for value, key in re.findall(r"(\d+)\s+(passed|failed|errors?|skipped)", tail):
        counts[key] = counts.get(key, 0) + int(value)
    total = sum(counts.values())
    if not counts or total == 0:
        raise PacketError(f"未能解析 pytest 结果：{tail!r}")
    # 顺序固定：失败/错误排在 passed 前面，避免"1 failed, 71 passed"被读成全绿
    order = ("failed", "error", "errors", "passed", "skipped")
    summary = ", ".join(f"{counts[k]} {k}" for k in order if k in counts)
    return summary, total


def _pytest_result(fast: bool, anchor: str, count: int | None = None) -> str:
    """Q9：现场跑一遍测试，如实记录通过率；失败也照实写，不强行全绿。

    刻意不用 `_run()`：pytest 非零退出是**要被记录的结果**，不是生成失败。
    措辞上不写「锚点实测」——测试跑的是当前工作区，不是锚点提交，
    那会是把"存在性/通过性"混为一谈的另一种过度声称。

    **`count` 是"我马上要写进包里的用例数"，传给子进程（`COUNT_ENV`）。**
    原因是这一跑发生在写文件**之前**，磁盘上仍是上一代包，而
    `tests/test_review_packet.py` 里那条「包里记的数 == 实际收集数」的断言读的正是
    磁盘那份。于是只要这一轮改过用例数，第一次生成**必然**红——但红的原因是
    "拿上几代产物判这一代"，与代码对错无关，而且这个假的 `1 failed` 会被如实
    写进实测行、进而让包被判不可发布。

    实测（2026-11-17）：把包里计数改成 1293（实际 1294），跑一次生成器，实测行得到
    `1 failed, 1293 passed / 1294 total`，失败的正是那条计数断言。所以修法不是放宽
    断言，而是给它正确的参照物——生成期间比"准备写入的数"（见 `recorded_count_error`）。
    """
    if fast:
        return FAST_TEST_LINE
    if os.environ.get("PYTEST_CURRENT_TEST"):
        # 真实踩过的坑：测试里写裸 build() 会再起一轮 pytest，无限套娃直到超时。
        # 与其慢慢挂死，不如立刻给出可操作的报错。
        raise PacketError(
            "检测到在 pytest 进程内以默认模式生成评审包（会递归启动 pytest）。"
            "测试里请用 build(fast=True) / write_packet(..., fast=True)，"
            "或显式传 test_result 重放已记录的那一行。"
        )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=None if count is None else {**os.environ, COUNT_ENV: str(count)},
    )
    summary, total = _parse_pytest_summary(proc.stdout)
    return (
        f"- 单元测试实测：当前工作区本地实测 **{summary} / {total} total**"
        f"（`pytest tests/ -q`；本地 HEAD `{_git_sha()}`，生成锚点 `{anchor}`；"
        f"测试跑的是当前工作区而非锚点提交，仅陈述本次本地结果，不宣称普适正确）"
    )


def _snapshot(ref: str, test_line: str, count: int | None = None) -> str:
    """生成锚点处的仓库状态。只放"会变且该被评审看到"的东西。

    认 `ref` 不认 HEAD：锚点处固定的 sha / 提交列表不会因为打包提交而改变，
    而单测数、实测结果、评分表、任务结构、依赖仍从**当前工作区**现算，
    仓库真漂移照样报红。

    `count` 由 `build()` 传进来（它必须与交给测试子进程的那个数**是同一个**，
    否则两处各算一次，遇到收集不稳定就会写下一份自相矛盾的包）。
    """
    sha = _git_sha(ref)
    log = _git_log(ref)
    tests = _pytest_count() if count is None else count
    publishable, reason = _test_gate(test_line)
    gate = "" if publishable else "\n\n" + _block_notice(reason)
    return (
        f"- 生成锚点：`{sha}`（本节提交列表固定在该提交，不随后续提交变化）\n"
        f"- 单元测试：**{tests} 个用例被收集**（`pytest tests/ --collect-only` 计数）\n"
        f"{test_line}{gate}\n"
        f"\n**锚点处最近 8 条提交**\n\n{log}"
    )


def _scores() -> str:
    """现场跑五份夹具，把分数摊开。**不缓存**——它测的是"代码行为"，一变就该重算。

    与 `_pytest_count` 的进程内缓存不冲突：那个数只取决于 `tests/*.py` 的文件集合
    （用指纹盯着变没变），跨时间不跨快照；这里跨一次运行就不可信。
    """
    out = _run(
        [sys.executable, str(ROOT / "src" / "arena" / "diagnosis_report.py"), "--fixture", "all"]
    )
    rows = [line.strip() for line in out.splitlines() if line.strip()]

    parsed = [SCORE_RE.match(r) for r in rows]
    if not parsed or not all(parsed):
        # 格式变了就退化成原始输出，不猜、不崩
        return (
            "> 夹具摘要格式未被识别，以下为原始输出：\n\n```text\n"
            + "\n".join(rows)
            + "\n```"
        )

    table = [
        "| 夹具 | 门禁 | 得分 | 反事实得分 | pass | fail | blocked | unscored |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m in parsed:
        _, dq, name, ratio, num, den, credit, p, f, b, u = m.groups()
        table.append(
            f"| `{name}` | {'未过' if dq else '通过'} | {ratio} ({num}/{den}) "
            f"| {credit} | {p} | {f} | {b} | {u} |"
        )
    return "\n".join(table)


def _task_struct() -> str:
    """任务资产的结构摘要。注意只输出结构，不输出期望值——期望值该由评审自己算。"""
    task = yaml.safe_load(TASK.read_text(encoding="utf-8"))
    # TD-036：执行路径（rubric_layers）在加载期对账同一份 YAML，生成器是第二个消费者。
    # 若不在这里也拦一次，篡改后的策略值会被印进对外文档，两遍重生成还会把它写死。
    try:
        _check_scoring_policy(task)
    except ReportError as exc:
        raise PacketError(f"评分策略漂移，拒绝生成评审包：{exc}") from exc
    score = task["scoring"]["score"]
    gate_cfg, credit_cfg = score["gate"], score["credit"]
    gate = task["rubric_gate"]
    credit = task["rubric_credit"]

    declared = credit_cfg["total_weight"]
    actual = sum(r["weight"] for r in credit)
    if declared != actual:
        # 评审包不替 YAML 掩盖自相矛盾——这类错误必须在生成阶段就炸，不能印到对外文档里
        raise PacketError(f"YAML 自相矛盾：credit.total_weight={declared}，但权重和为 {actual}")

    lines = [
        f"- 任务：`{task['task_id']}`",
        f"- 断言总数：{len(gate)} 门禁 + {len(credit)} 积分 = {len(gate) + len(credit)}",
        f"- 计分式：`{score['formula']}`；credit 分母 `{credit_cfg['denominator']}`（= {declared}）",
        f"- 门禁未过：`disqualify_ratio={gate_cfg['disqualify_ratio']}`；"
        f"门禁遇 `unscored`：`{gate_cfg['on_unscored']}`",
        "",
        "**门禁层（不计权重，任一 fail 即 0 分）**",
        "",
        "| ID | 类型 | on_fail | 说明 |",
        "|---|---|---|---|",
    ]
    for r in gate:
        lines.append(f"| `{r['id']}` | `{r['type']}` | `{r.get('on_fail')}` | {r.get('detects', '—')} |")

    lines += [
        "",
        "**积分层（分母 = 权重和）**",
        "",
        "| ID | 类型 | 权重 | skill_id | 声明错误模式数 |",
        "|---|---|---|---|---|",
    ]
    for r in credit:
        lines.append(
            f"| `{r['id']}` | `{r['type']}` | {r['weight']} | "
            f"`{r.get('skill_id')}` | {len(r.get('error_patterns') or [])} |"
        )
    return "\n".join(lines)


def _env() -> str:
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").strip()
    return (
        "依赖（`requirements.txt`，全部 pin 版本）：\n\n```text\n"
        + req
        + "\n```\n\n"
        "复现三条命令（Windows + Git Bash，Python 3.11+）：\n\n"
        "```bash\n"
        "# 1. 全部单测\n"
        "python -m pytest tests/ -q\n\n"
        "# 2. 五份假提交的评分（本文件 4.1 节的来源）\n"
        "python src/arena/diagnosis_report.py --fixture all\n\n"
        "# 3. 重生成本评审包（本文件 4、7 节的来源）\n"
        "python src/arena/review_packet.py\n"
        "```"
    )


def _guard(text: str) -> None:
    hits = [w for w in FORBIDDEN if w in text]
    if hits:
        raise PacketError(
            f"匿名守卫拦下：生成内容含禁用词 {hits}。请检查 git 提交信息或 manual 文件。"
        )


def _core_fact_tokens() -> dict[str, list[str]]:
    """Q7 白名单的期望值，**全部从仓库机器可读事实派生**。

    脚本里不写死"结论"，只写"手工段该出现哪个标记"；标记内容由 YAML 决定。
    这样 YAML 一改，白名单跟着改，不会变成需要人工同步的第二真相源。
    """
    task = yaml.safe_load(TASK.read_text(encoding="utf-8"))
    two_layer = "rubric_gate" in task and "rubric_credit" in task
    credit_total = sum(r["weight"] for r in task["rubric_credit"])
    missing_states = sorted(set(task["scoring"]["attribution"]) - {"pass", "fail"})
    return {
        "计分模式": ["gate / credit" if two_layer else "单段加权"],
        "credit 分母": [f"分母恒为 {credit_total}"],
        "非通过态枚举": missing_states,
    }


def _check_core_facts(manual: str) -> None:
    """Q7：只扫第 3 节的**数字 / 枚举**，不校验措辞、不校验判断句。

    判断句没有唯一正确表述，锁它只会逼人统一话术；历史失真全是硬事实写反
    （14 vs 46 用例、权重 [1,2,1,1,1] vs gate/credit），所以只锁硬事实。
    缺任一个标记即拒绝生成——警告容易被批处理吞掉，不如直接不出包。
    """
    start = manual.find("## 3.")
    end = manual.find("## 4.", start) if start >= 0 else -1
    if start < 0 or end < 0:
        raise PacketError("核心事实校验：手工模板里找不到完整的第 3 节（## 3. ... ## 4.）")
    section = manual[start:end]
    missing = [
        f"{label} 缺 `{token}`"
        for label, tokens in _core_fact_tokens().items()
        for token in tokens
        if token not in section
    ]
    if missing:
        raise PacketError(
            "核心事实不匹配：" + "；".join(missing) + "。请改 docs/review-packet-manual.md 第 3 节"
        )


def build(
    ref: str = "HEAD", fast: bool = False, test_result: str | None = None
) -> tuple[str, str, str]:
    """返回 (正文, 正文指纹, 快照 sha)。正文不含时间戳，保证同样输入产出同样指纹。

    `ref` 只用来重放快照锚点：生成时传 HEAD，校验时传文件头记录的 snapshot-sha。
    其余四段（分数 / 任务 / 环境 / 单测数）始终取当前工作区，不受 `ref` 影响。

    `fast=True` 跳过测试执行；`test_result` 供 `--check` 重放包里已记录的那行，
    避免校验时再跑一遍 pytest（两次执行 + 偶发波动会制造假 STALE）。
    """
    if not MANUAL.is_file():
        raise PacketError(f"缺少手工模板：{MANUAL}")

    manual = MANUAL.read_text(encoding="utf-8")
    _check_core_facts(manual)
    missing = [p for p in PLACEHOLDERS if p not in manual]
    if missing:
        raise PacketError(f"手工模板缺少占位符：{missing}")

    # 用例数只数一次：交给人看的（快照）和交给测试子进程的（COUNT_ENV）必须是同一个数。
    tests = _pytest_count()
    line = (
        test_result
        if test_result is not None
        else _pytest_result(fast, _git_sha(ref), tests)
    )
    body = manual
    body = body.replace("{{AUTO_SNAPSHOT}}", _snapshot(ref, line, tests))
    body = body.replace("{{AUTO_SCORES}}", _scores())
    body = body.replace("{{AUTO_TASK}}", _task_struct())
    body = body.replace("{{AUTO_ENV}}", _env())

    leftover = re.findall(r"\{\{AUTO_\w+\}\}", body)
    if leftover:
        raise PacketError(f"存在未处理的占位符：{leftover}")

    _guard(body)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    return body, digest, _git_sha(ref)


def write_packet(out: Path, fast: bool = False, test_result: str | None = None) -> None:
    """`test_result` 供测试构造特定记录（例如造一份「带失败记录」的包）。

    产品路径不传它——真实生成时永远现场跑测试，否则就退化成自欺。
    """
    body, digest, snap = build(fast=fast, test_result=test_result)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    meta = f"<!-- packet-sha256={digest} snapshot-sha={snap} generated={stamp} -->"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(meta + "\n" + body)
    print(f"OK: {out}")
    print(f"    packet-sha256 = {digest}   snapshot-sha = {snap}")


def _verify_recorded_run(recorded_line: str) -> int:
    """`--check --full`：重跑一遍 pytest，与包内记录的那行比对。

    为什么非有它不可：常规 `--check` 把记录行**原样喂回** `build()` 重算指纹，
    等于拿记录证明记录。真正的盲格是「生成时全绿、之后代码变红而包没重生成」——
    那份包对外声称全绿，实际是红的。只有重跑看得见。

    代价：一次全量 `pytest`（本仓库约 2 分钟）。所以默认不跑，由人显式加 `--full`。
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        print("FAIL: 在 pytest 进程内不能重跑测试（会递归）。请在命令行执行 --check --full")
        return 1
    rec_total = re.search(r"/\s*(\d+)\s*total", recorded_line)
    if not rec_total:
        print("FAIL: 包内实测行缺失或格式无法解析，无法复核")
        return 1
    rec_failed = _FAILED_RE.search(recorded_line)
    rec_failed_n = int(rec_failed.group(1)) if rec_failed else 0
    rec_total_n = int(rec_total.group(1))

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    summary, now_total = _parse_pytest_summary(proc.stdout)
    now_failed = _FAILED_RE.search(summary)
    now_failed_n = int(now_failed.group(1)) if now_failed else 0

    print(f"- 包内记录：{rec_failed_n} failed / {rec_total_n} total")
    print(f"- 现在重跑：{now_failed_n} failed / {now_total} total（{summary}）")
    if rec_failed_n == now_failed_n and rec_total_n == now_total:
        print("OK_FULL: 包内实测记录仍然成立")
        return 0
    if rec_failed_n == 0 < now_failed_n:
        print("TEST_DRIFT: 包声称全绿，但当前工作区有失败——对外材料与实际相反，必须重生成")
    else:
        print("TEST_DRIFT: 包内记录与当前实测不符（多半是测试增删或已修好未重生成）")
    print("            请重新生成：python src/arena/review_packet.py")
    return 3


def check(out: Path, full: bool = False) -> int:
    """判断这份评审包的内容是否还和仓库一致。

    刻意不用 HEAD 比较：生成物本身要进下一个提交，HEAD 必然变化，
    那样刚生成就会被判过期。真正该问的是"内容还准不准"，所以重算内容指纹。

    两件事分开查：
    - 文件正文的实际指纹 vs 头部记录的指纹 → 检测**手改**（头部不会跟着变）
    - 头部记录的指纹 vs 按**同一锚点**重算的指纹 → 检测**过期**（仓库内容变了）

    重算时把快照段冻结在头部记录的 snapshot-sha，否则打包提交会让 HEAD 的
    sha / git log 变化，制造一个永远清不掉的假阳性。
    """
    if not out.is_file():
        print(f"FAIL: 评审包尚未生成（{out}）。先跑 python src/arena/review_packet.py")
        return 1

    text = out.read_text(encoding="utf-8")
    meta, _, old_body = text.partition("\n")
    recorded = re.search(r"packet-sha256=([0-9a-f]+)", meta)
    snap = re.search(r"snapshot-sha=([0-9a-f]+)", meta)
    if not recorded:
        print("FAIL: 评审包头部缺少 packet-sha256，无法判断内容是否过期")
        return 1

    actual = hashlib.sha256(old_body.encode("utf-8")).hexdigest()[:16]
    if actual != recorded.group(1):
        print(f"TAMPERED: 文件正文被手改过（头部记 {recorded.group(1)}，正文实为 {actual}）")
        print("          评审包是生成物，请改 docs/review-packet-manual.md 后重新生成")
        return 1

    def behind_count() -> str:
        """锚点落后当前 HEAD 几个提交。算不出来返回空串，绝不因此阻塞校验。"""
        if not snap:
            return ""
        try:
            return _run(["git", "rev-list", "--count", f"{snap.group(1)}..HEAD"]).strip()
        except PacketError:
            return ""

    # TD-040：先问「能不能对外发」，再问「内容是否最新」。
    # 顺序不能反——一个带失败记录的包，先说它 STALE 会把注意力引到"重新生成"，
    # 而重新生成出来的仍是带失败的包。实测依据：487ff6f 那次的包 --check 判 OK。
    recorded_line = TEST_RESULT_RE.search(old_body)
    publishable, reason = _test_gate(recorded_line.group(0) if recorded_line else "")
    if not publishable:
        print(f"NOT_RELEASABLE: 这份评审包不可对外发布——{reason}")
        print("                包内已记录该状态（见正文警告）；修好测试后重新生成，不要用 --fast")
        return 2

    ref = snap.group(1) if snap else "HEAD"
    try:
        fresh_body, fresh_digest, _ = build(
            ref, test_result=recorded_line.group(0) if recorded_line else None
        )
    except PacketError as exc:
        print(f"FAIL: 按锚点 {ref} 重算评审包失败：{exc}")
        return 1

    if recorded.group(1) == fresh_digest:
        print(f"OK: 评审包内容与仓库一致（{fresh_digest}）")
        if full:
            return _verify_recorded_run(recorded_line.group(0) if recorded_line else "")
        # 内容绿 ≠ 锚点新。以前只在 STALE 分支报落后，OK 时静默，
        # 于是"代码已改但恰好没改到自动段"这类脱节完全没有提示（round-6 ②-1）。
        behind = behind_count()
        if behind and behind != "0":
            print(
                f"NOTE: 生成锚点 {snap.group(1)} 落后当前 HEAD {behind} 个提交。"
                "自动段按当前工作区现算，此提示不阻塞；"
                "不要为对齐 HEAD 重新生成（那只会把锚点再推后一格）"
            )
        return 0

    diff = list(
        difflib.unified_diff(
            old_body.splitlines(), fresh_body.splitlines(),
            fromfile="包内", tofile="仓库现况", lineterm="", n=0,
        )
    )
    behind = behind_count()
    stale = f"，锚点落后当前 HEAD {behind} 个提交" if behind and behind != "0" else ""

    print(f"STALE: 评审包内容已与仓库不符（{len(diff)} 行差异{stale}）")
    for line in diff[:20]:
        print("   " + line)
    if len(diff) > 20:
        print(f"   ... 另有 {len(diff) - 20} 行")
    print("       传给外部 AI 前请重新生成：python src/arena/review_packet.py")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成/校验外部评审包")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出路径，默认 docs/review-packet.md")
    ap.add_argument("--check", action="store_true", help="只校验内容与仓库是否一致，不写文件")
    ap.add_argument(
        "--full",
        action="store_true",
        help="配合 --check：重跑一遍 pytest，复核包内实测记录是否仍成立（约 2 分钟）",
    )
    ap.add_argument("--fast", action="store_true", help="跳过测试执行，只收集用例数（本地迭代用）")
    args = ap.parse_args(argv)

    if args.full and not args.check:
        print("FAIL: --full 只用于 --check（生成时本来就跑测试，无需 --full）", file=sys.stderr)
        return 1

    try:
        if args.check:
            return check(Path(args.out), full=args.full)
        write_packet(Path(args.out), fast=args.fast)
    except PacketError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
