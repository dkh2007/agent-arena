"""B-04e 判据指纹的变异检验：证明这 17 条判据真的在管指纹，而不是在管注释。

判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）：

    DETECTED  变异后 pytest 退出码 **1** —— 真被抓住
    MISSED    变异后仍全绿 —— 这条测试没在管这事
    INVALID   锚点没命中 / 收集失败 —— **不算抓住**

只有 `returncode == 1` 记 DETECTED（`no tests ran` 的退出码是 4，按"非 0 即抓住"
会白跑一轮还留下假绿）。

## 这 10 组各自防的是什么

指纹的失效方式和别的模块不一样：它**不会报错**，只会安静地少算或多算一个字段。
少算 ⇒ 标准改了但指纹没变 ⇒ 那批提交永远不进重评范围（B-04d 护栏失效，无人发现）；
多算 ⇒ 只改个注释就触发全库重评（成本失控，最后一定有人去关掉这个机制）。

M1 打"门禁标准改了不算改判据"，M2 打"归因字段改了不用重评"（本轮的核心论点），
M3 打"等价写法被当成改动"，M4 打"数组顺序语义丢失"，M5 打"整个功能空转"，
M6 打"给非法 rubric 也起名字"，M7 打"换算法版本后旧指纹无法识别"；
M8–M10 打**接线**：函数写对了但报告里没写（M8）、可选键被塞进 `required`
让全部历史报告校验失败（M9）、格式不校验导致"缺指纹"与"指纹写坏"混成一样（M10）。
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

from _runner import run_mutations  # 同目录；判定只有一处实现

DR = Path("src/arena/diagnosis_report.py")
SCHEMA = Path("docs/diagnosis-report.schema.json")
T = "tests/test_rubric_fingerprint.py"

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
    # 锚点带上 version 那一行：round-99 加了逐条规则的指纹函数，`sort_keys=True,`
    # 在文件里变成两处，只写它命中 2 次会判 INVALID。整份指纹才该被这条变异打到。
    ("M3 不排序键（YAML 调换书写顺序被算成判据变更）",
     DR,
     '        {"version": FINGERPRINT_VERSION, GATE: gate, CREDIT: credit},\n'
     "        ensure_ascii=False,\n"
     "        sort_keys=True,",
     '        {"version": FINGERPRINT_VERSION, GATE: gate, CREDIT: credit},\n'
     "        ensure_ascii=False,\n"
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
    # 同上：`gate, credit = rubric_layers(task)` 在 round-99 之后出现两处，
    # 锚点必须带上下一步（`payload` 用的是整份版本号）才唯一。
    ("M6 不调 rubric_layers（非法 rubric 也有指纹，与合法标准无法区分）",
     DR,
     "    gate, credit = rubric_layers(task)\n"
     "    payload = json.dumps(\n"
     '        {"version": FINGERPRINT_VERSION',
     '    gate = list(task.get("rubric_gate") or [])\n'
     '    credit = list(task.get("rubric_credit") or [])\n'
     "    payload = json.dumps(\n"
     '        {"version": FINGERPRINT_VERSION',
     f"{T}::test_refuses_to_fingerprint_an_invalid_rubric"),

    # ── 版本串：换归一化规则后旧指纹必须可辨 ────────────────────────────────
    ("M7 版本串不进 payload（换算法后历史指纹完全失真且无从发现）",
     DR,
     PAYLOAD,
     "{GATE: gate, CREDIT: credit}",
     f"{T}::test_bumping_the_algorithm_version_moves_every_fingerprint"),

    # ── 接线：指纹得真进报告、得真可选、得真校验格式 ────────────────────────
    ("M8 build_report 不写指纹（函数有了但报告里没有，等于没接）",
     DR,
     '    report["rubric_fingerprint"] = rubric_fingerprint(task)',
     "    pass  # 变异：不写指纹",
     f"{T}::test_build_report_carries_the_fingerprint"),

    ("M9 指纹进 required（全部历史报告与旧夹具一律校验失败）",
     SCHEMA,
     '    "skill_gaps",\n    "unattributed_patterns"\n  ],',
     '    "skill_gaps",\n    "unattributed_patterns",\n    "rubric_fingerprint"\n  ],',
     f"{T}::test_historical_report_without_fingerprint_still_passes_schema"),

    ("M10 schema 不校验格式（缺指纹与指纹写坏混成一样）",
     SCHEMA,
     '      "pattern": "^[0-9a-f]{64}$",\n',
     "",
     f"{T}::test_schema_rejects_a_malformed_fingerprint"),
]

def _run(node: str) -> tuple[int, str]:
    env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-fp-")}
    r = subprocess.run([PYEXE, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                       capture_output=True, text=True, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()

def main() -> int:
    """判定与还原全交给 `_runner`：本文件只负责"变异长什么样"。

    以前这里有一份自己的判定代码，窄看只是重复；实际是两套规则会分叉——旧那套
    把 rc=2/3/4 也当 `DETECTED`，于是节点名写错时会报"抓住了"（round-89 M14 实测）。
    """
    return run_mutations(MUTATIONS, temp_prefix="pt-fingerprint-")

if __name__ == "__main__":
    raise SystemExit(main())
