"""变异脚本的公共执行器 —— 判定只认一种规则：**`rc == 1` 才算抓住**。

以前每个脚本各写一份判定，分叉成两套：

- 新规则（`rc == 1` → `DETECTED`，其余非 0 → `INVALID`）；
- 旧规则（`rc == 5` 判 `INVALID`、输出含 `error` 判 `INVALID`，**其余非 0 一律 `DETECTED`**）。

旧规则的问题不是"风格不统一"，是**它会把退出码 2/3/4 当成抓住**：

| pytest 退出码 | 含义 | 旧规则判成 |
|---|---|---|
| 0 | 全绿 | MISSED（对） |
| 1 | 有用例失败 | DETECTED（对） |
| 2 | 会话被中断（收集期崩） | **DETECTED（假绿）** |
| 3 | 内部错误 | **DETECTED（假绿）** |
| 4 | 用法错误（节点名写错） | **DETECTED（假绿）** |
| 5 | 没收集到用例 | INVALID（对） |

也就是说：改过测试名之后，脚本会打印 `DETECTED` 告诉你"抓住了"，而实际是那条用例根本没跑。
round-89 M14 实测过这条路径（`no tests ran` 走 rc=4 被判 `DETECTED`）。

**`INVALID` 与 `MISSED` 都不是抓住**。前者是脚本自己写错（锚点没命中、节点名不存在），
后者是变异没被测试管到——两种都要人看一眼，不许合并成"通过"。

用法（各脚本 main 里只剩一句）：

    from _runner import run_mutations
    return run_mutations(MUTATIONS, default_path=SRC, temp_prefix="pt-judge-")

`MUTATIONS` 里每项可以是 4 元组 `(说明, 旧片段, 新片段, 节点)`（单文件脚本，
路径取 `default_path`）或 5 元组 `(说明, 路径, 旧片段, 新片段, 节点)`（多文件脚本）。

自查（不跑任何真实变异之外的东西，几十秒）：`python tools/mutations/_runner.py --self-test`
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

# 站到仓根再干活：脚本里全是相对路径（src/… / tests/…），在别的目录直接跑会
# FileNotFoundError。round-86 收尾时实测踩到（用户要求给完整路径，才发现"完整路径"
# 反而跑不通）。这里按"往上找 src/arena"定位仓根，从 .git/ 或 tools/mutations/ 调用都成立。
_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

PYEXE = sys.executable

DETECTED, MISSED, INVALID = "DETECTED", "MISSED", "INVALID"

# 退出码的含义**写进报错文案**：只说 `rc=4` 的话，读的人得去查表才知道是自己把名字写错了。
RC_MEANING = {
    0: "全绿",
    1: "有用例失败",
    2: "会话被中断（收集期崩溃/收集错误）",
    3: "pytest 内部错误",
    4: "用法错误（节点名可能写错）",
    5: "没收集到用例（节点不存在）",
}


def judge(rc: int) -> tuple[str, str]:
    """退出码 → 判定。**唯一一处**决定"什么算抓住"。"""
    if rc == 1:
        return DETECTED, ""
    if rc == 0:
        return MISSED, "测试仍绿——没在管这事"
    return INVALID, f"{RC_MEANING.get(rc, '未知退出码')} rc={rc}"


def _child_env(prefix: str) -> dict[str, str]:
    """每条变异一个**新的** `PYTEST_DEBUG_TEMPROOT`。

    复用同一个目录（曾有脚本固定成 `.git/pt`）时，pytest 开场会清理旧编号目录，
    而沙箱的 bulk-delete 守卫会拦掉删除 ⇒ 整场 INTERNALERROR，输出里既没有 `failed`
    也没有断言信息，看起来像"变异无效"。round-84 / round-85 各栽过一次。
    """
    return {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix=prefix)}


def _one(name: str, path: Path, old: str, new: str, node: str,
         temp_prefix: str, repeats: int = 1) -> tuple[str, str, str]:
    """跑一条变异，返回 `(说明, 判定, 详情)`。**无论判定如何都会还原文件**。

    `repeats > 1` 时同一变异连跑几遍（文件保持变异状态），**只有每一遍都给同一个码才算数**：
    两次结果不一致说明这条用例本身在抖，此时报 `DETECTED` 等于把抖动当成"抓住了"。
    """
    path = Path(path)
    orig = path.read_text(encoding="utf-8")
    hits = orig.count(old)
    if hits != 1:
        return name, INVALID, f"锚点命中 {hits} 次（应为 1）——锚点写错或代码已改"
    runs: list[subprocess.CompletedProcess] = []
    try:
        path.write_text(orig.replace(old, new, 1), encoding="utf-8")
        for _ in range(max(1, repeats)):
            runs.append(subprocess.run(
                [PYEXE, "-m", "pytest", "-q", node, "-p", "no:cacheprovider"],
                capture_output=True, text=True, env=_child_env(temp_prefix),
            ))
    finally:
        path.write_text(orig, encoding="utf-8")
    codes = [r.returncode for r in runs]
    if len(set(codes)) > 1:
        return name, INVALID, f"同一变异连跑 {len(codes)} 遍结果不一致 rc={codes}（用例在抖）"
    status, detail = judge(codes[-1])
    if status == DETECTED:
        tail = [line for line in (runs[-1].stdout or "").strip().splitlines() if line.strip()][-1:]
        detail = tail[0] if tail else ""
    return name, status, detail


def _normalize(mutations: Sequence[tuple],
               default_path: Path | None) -> list[tuple[str, Path, str, str, str]]:
    out: list[tuple[str, Path, str, str, str]] = []
    for item in mutations:
        if len(item) == 4:
            name, old, new, node = item
            if default_path is None:
                raise ValueError(f"{name}：4 元组必须配 default_path")
            out.append((name, Path(default_path), old, new, node))
        elif len(item) == 5:
            name, path, old, new, node = item
            out.append((name, Path(path), old, new, node))
        else:
            raise ValueError(f"变异项必须是 4 或 5 元组，收到 {len(item)} 项：{item!r}")
    return out


def run_mutations(mutations: Sequence[tuple], *, default_path: Path | None = None,
                  temp_prefix: str = "pt-mut-", repeats: int = 1) -> int:
    """跑一组变异，返回 0（全 DETECTED）或 1（有漏网/无效）。"""
    results = [_one(*item, temp_prefix, repeats) for item in _normalize(mutations, default_path)]
    for name, verdict, detail in results:
        print(f"{verdict:9} {name}  {detail}")
    counts = {k: sum(1 for _, v, _ in results if v == k) for k in (DETECTED, MISSED, INVALID)}
    ok = counts[DETECTED] == len(results)
    print("---")
    print(f"共 {len(results)} 组：DETECTED {counts[DETECTED]} / MISSED {counts[MISSED]} / "
          f"INVALID {counts[INVALID]}")
    print("判定：", "全 DETECTED" if ok else "有漏网或无效变异，需处理")
    return 0 if ok else 1


# ── 自查：判定器本身对不对 ──────────────────────────────────────────────────

_TARGET = Path("src/arena/schedule_advisor.py")
_REAL_NODE = "tests/test_schedule_advisor.py::test_bad_weights_rejected"
_REAL_OLD = "    if any(a <= b for a, b in zip(values, values[1:])):"


def self_test() -> int:
    """四种情形各跑一遍，证明只有 rc=1 会得到 `DETECTED`。

    | 情形 | 期望 | 为什么必须测 |
    |---|---|---|
    | 真变异（权重递减校验删掉） | DETECTED | 判定器不能把正常变异判成无效 |
    | 无害变异（改模块文档字符串首行） | MISSED | 不能把"测试没管"判成抓住 |
    | 锚点不存在 | INVALID | 脚本自己写错时必须显名，不许静默放过 |
    | 节点名写错 | INVALID | **这条就是旧规则假绿的那条路**（rc=4） |
    """
    cases = [
        ("S1 真变异：删掉权重递减校验", _TARGET, _REAL_OLD, "    if False:", _REAL_NODE, DETECTED),
        ("S2 无害变异：只改模块文档首行", _TARGET,
         '"""排程层（B-09）：', '"""排程层 (B-09)：', _REAL_NODE, MISSED),
        ("S3 锚点不存在", _TARGET, _REAL_OLD.replace("])):", "] )):"), "    if False:",
         _REAL_NODE, INVALID),
        ("S4 节点名写错（旧规则在这条上假绿）", _TARGET, _REAL_OLD, "    if False:",
         _REAL_NODE + "d", INVALID),
    ]
    ok = True
    for name, path, old, new, node, want in cases:
        _, got, detail = _one(name, path, old, new, node, "pt-selftest-")
        good = got == want
        ok &= good
        suffix = "" if good else f"  {detail}"
        print(f"{'OK  ' if good else 'BAD '} {name} ⇒ 期望 {want}，实得 {got}{suffix}")
    print("---")
    print(f"SELF_TEST_PASS={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    print(__doc__)
    raise SystemExit(0)
