"""B-04e 判据指纹的变异检验：证明这 17 条判据真的在管指纹，而不是在管注释。

判定三档与 `README.md` 第 5 坑一致：

    DETECTED  变异后 pytest 退出码 **1** —— 真被抓住
    MISSED    变异后仍全绿 —— 这条测试没在管这事
    INVALID   锚点没命中 / 收集失败 —— **不算抓住**

只有 `returncode == 1` 记 DETECTED（`no tests ran` 的退出码是 4，按"非 0 即抓住"
会白跑一轮还留下假绿）。

## 这 7 组各自防的是什么

指纹的失效方式和别的模块不一样：它**不会报错**，只会安静地少算或多算一个字段。
少算 ⇒ 标准改了但指纹没变 ⇒ 那批提交永远不进重评范围（B-04d 护栏失效，无人发现）；
多算 ⇒ 只改个注释就触发全库重评（成本失控，最后一定有人去关掉这个机制）。

M1 打"门禁标准改了不算改判据"，M2 打"归因字段改了不用重评"（本轮的核心论点），
M3 打"等价写法被当成改动"，M4 打"数组顺序语义丢失"，M5 打"整个功能空转"，
M6 打"给非法 rubric 也起名字"，M7 打"换算法版本后旧指纹无法识别"。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

DR = Path("src/arena/diagnosis_report.py")
T = "tests/test_rubric_fingerprint.py"
PYEXE = sys.executable

PAYLOAD = '{"version": FINGERPRINT_VERSION, GATE: gate, CREDIT: credit}'

MUTATIONS = [
    # ── 漏算：少一个字段 ⇒ 该重评的永远进不了范围 ──────────────────────────
    ("M1 只算 credit、不算 gate（门禁标准改了不算改判据）",
     DR,
     PAYLOAD,
     '{"version": FINGERPRINT_VERSION, GATE: [], CREDIT: credit}',
     f"{T}::test_gate_condition_change_moves_the_fingerprint"),

    ("M2 漏掉 skill_id（分数没变所以不重评——B-04d 要防的正是这个）",
     DR,
     PAYLOAD,
     '{"version": FINGERPRINT_VERSION, GATE: gate, '
     'CREDIT: [{k: v for k, v in r.items() if k != "skill_id"} for r in credit]}',
     f"{T}::test_skill_id_change_moves_the_fingerprint_even_though_score_is_unchanged"),

    # ── 多算：等价写法被当成改动 ⇒ 补一句注释触发全库重评 ────────────────────
    ("M3 不排序键（YAML 调换书写顺序被算成判据变更）",
     DR,
     "        sort_keys=True,",
     "        sort_keys=False,",
     f"{T}::test_key_order_does_not_move_the_fingerprint"),

    # ── 顺序语义：执行器按数组顺序跑，调位置是真改动 ────────────────────────
    ("M4 按 id 排序后再哈希（数组顺序语义丢失）",
     DR,
     PAYLOAD,
     '{"version": FINGERPRINT_VERSION, '
     'GATE: sorted(gate, key=lambda r: r["id"]), '
     'CREDIT: sorted(credit, key=lambda r: r["id"])}',
     f"{T}::test_credit_order_change_moves_the_fingerprint"),

    # ── 整个功能空转 ────────────────────────────────────────────────────────
    ("M5 指纹恒为常量（所有比对都恒成立）",
     DR,
     "    return hashlib.sha256(payload.encode(\"utf-8\")).hexdigest()",
     "    return \"0\" * 64",
     f"{T}::test_expected_value_change_moves_the_fingerprint"),

    # ── 校验旁路：非法 rubric 也拿到指纹 ────────────────────────────────────
    ("M6 不调 rubric_layers（非法 rubric 也有指纹，与合法标准无法区分）",
     DR,
     "    gate, credit = rubric_layers(task)",
     "    gate = list(task.get(\"rubric_gate\") or [])\n"
     "    credit = list(task.get(\"rubric_credit\") or [])",
     f"{T}::test_refuses_to_fingerprint_an_invalid_rubric"),

    # ── 版本串：换归一化规则后旧指纹必须可辨 ────────────────────────────────
    ("M7 版本串不进 payload（换算法后历史指纹完全失真且无从发现）",
     DR,
     PAYLOAD,
     "{GATE: gate, CREDIT: credit}",
     f"{T}::test_bumping_the_algorithm_version_moves_every_fingerprint"),
]


def _run(node: str) -> tuple[int, str]:
    env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-fp-")}
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
