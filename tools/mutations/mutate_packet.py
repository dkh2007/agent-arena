"""评审包「参照物」的变异检验：证明这几条判据在管**比对谁**，不是在管注释。

判定三档与 `README.md` 第 5 坑一致：

    DETECTED  变异后 pytest 退出码 **1** —— 真被抓住
    MISSED    变异后仍全绿 —— 这条测试没在管这事
    INVALID   锚点没命中 / 收集失败 —— **不算抓住**

只有 `returncode == 1` 记 DETECTED（`no tests ran` 的退出码是 4，按"非 0 即抓住"
会白跑一轮还留下假绿）。

## 为什么单独立一组

这一族错误的共同点是：**不报错，只是判错了东西**。

- 生成期间比磁盘上那份（上一代）包 ⇒ 每次改过用例数，第一次生成必然记一个假
  `1 failed`；这个假失败会被如实写进实测行，进而让包被判「不可对外发布」。
- 生成期间一律放行 ⇒ 两个独立的计数路径（`--collect-only` 与全量运行）不一致
  也看不出来，包里可能出现"正文写 72、实测行写 99"。
- 快照自己再数一次 ⇒ 同一份包里两个数不同源，而两边都不报错。

M1 打"不告子进程"，M2/M3 打"生成期间参照物写错"（退回磁盘 / 一律放行），
M4 打"非生成期间不比对"（提交旧计数没人拦），M5 打"快照自己再数一次"。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

RP = Path("src/arena/review_packet.py")
T = "tests/test_review_packet.py"
PYEXE = sys.executable

MUTATIONS = [
    # ── 生成期间：参照物要么错、要么失效 ────────────────────────────────────
    ("M1 不把「准备写入的用例数」告给子进程（子进程只能拿上一代包比）",
     RP,
     "        env=None if count is None else {**os.environ, COUNT_ENV: str(count)},\n",
     "        env=None,\n",
     f"{T}::test_generator_tells_the_child_which_count_it_is_about_to_write"),

    ("M2 生成期间仍比磁盘那份（改过用例数 ⇒ 第一次生成必然记假 failed）",
     RP,
     "    if generating is not None:\n        if generating != live:",
     "    if False:  # 变异：永远比磁盘那份\n        if generating != live:",
     f"{T}::test_count_check_uses_the_right_reference_in_each_moment"),

    ("M3 生成期间一律放行（两条计数路径不一致也看不出来）",
     RP,
     "        if generating != live:",
     "        if False:  # 变异：生成期间不做任何比对",
     f"{T}::test_count_check_uses_the_right_reference_in_each_moment"),

    # ── 非生成期间：该红的没红 ───────────────────────────────────────────────
    ("M4 非生成期间不比对（提交了旧计数的包也没人拦）",
     RP,
     '    if f"**{live} 个用例被收集**" not in body:',
     "    if False:  # 变异：正常跑测试时不再核对磁盘那份",
     f"{T}::test_count_check_uses_the_right_reference_in_each_moment"),

    # ── 同一个数：两处各数一次 ───────────────────────────────────────────────
    ("M5 快照自己再数一次（包里两个数不同源）",
     RP,
     "    tests = _pytest_count() if count is None else count\n",
     "    tests = _pytest_count()  # 变异：无视传进来的那个数\n",
     f"{T}::test_snapshot_writes_the_same_count_the_child_was_told"),
]


def _run(node: str) -> tuple[int, str]:
    env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-pk-")}
    r = subprocess.run([PYEXE, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                       capture_output=True, text=True, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()


def main() -> int:
    results: list[tuple[str, str, str]] = []
    originals = {p: p.read_text(encoding="utf-8") for p in {m[1] for m in MUTATIONS}}
    try:
        for name, path, old, new, node in MUTATIONS:
            orig = originals[path]
            hits = orig.count(old)
            if hits != 1:
                results.append((name, "INVALID", f"锚点命中 {hits} 次（应为 1）"))
                continue
            path.write_text(orig.replace(old, new, 1), encoding="utf-8")
            try:
                rc, out = _run(node)
            finally:
                path.write_text(orig, encoding="utf-8")
            if rc == 1:
                tail = [line for line in out.splitlines() if line.strip()][-1:]
                results.append((name, "DETECTED", tail[0] if tail else ""))
            elif rc == 0:
                results.append((name, "MISSED", "测试仍绿——没在管这事"))
            else:
                results.append((name, "INVALID", f"收集/环境错误 rc={rc}"))
    finally:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")

    for name, verdict, detail in results:
        print(f"{verdict:9} {name}  {detail}")
    ok = all(v == "DETECTED" for _, v, _ in results)
    print("---")
    print(f"共 {len(results)} 组；判定：", "全 DETECTED" if ok else "有漏网或无效变异")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
