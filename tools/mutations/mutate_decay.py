"""P 的衰减（B-08b）的变异测试。判定三档沿用既有约定：`DETECTED` / `INVALID` / `MISSED`。

**`INVALID` 不算抓住**——脚本自己写错锚点会让它看起来像"抓住了"。
每条变异必须换一个新的 `PYTEST_DEBUG_TEMPROOT`：复用同一目录时 pytest 开场清理旧编号目录、
被沙箱的 bulk-delete 守卫拦掉，整场 INTERNALERROR（输出里既没 failed 也没断言信息）。
round-84 / round-85 各栽过一次。

跑法（在仓根执行，或给绝对路径；脚本自己会站到仓根）：
    python tools/mutations/mutate_decay.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

# 站到仓根再干活：脚本里全是相对路径（src/… / tests/…），在别的目录直接跑会
# FileNotFoundError。这里按"往上找 src/arena"定位仓根，从 .git/ 或 tools/mutations/ 调用都成立。
_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

D = Path("src/arena/decay.py")
S = Path("src/arena/scheduler.py")
T = "tests/test_decay.py"
PYEXE = sys.executable

MUTATIONS = [
    # 缺卡时若不退化，py-fsrs 对无历史卡返回 0 ⇒ P 直接变 0（把"还没开始"说成"忘光了"）
    ("M1 缺 stability 时照乘（r 记 0）而不是保留原值",
     D, '    if stability is None:\n        return DecayResult(p, p, False, "no_card", None, elapsed, None, anchor)',
     '    if stability is None:\n        return DecayResult(p, 0.0, True, "applied", 0.0, elapsed, None, anchor)',
     f"{T}::test_missing_stability_keeps_p_and_says_no_card"),

    # 时间倒流若静默放行，会给出一个"看着合理"的错数
    ("M2 as_of 早于锚点不报错（时间倒着用）",
     D, '    if seconds < 0:\n        raise DecayError(f"as_of={as_of} 早于锚点 {anchor}：时间不许倒着用")',
     '    if False:\n        raise DecayError("unused")',
     f"{T}::test_as_of_before_anchor_raises_instead_of_decaying_backwards"),

    # 锚点取 min ⇒ 卡片更新后会按更早的证据算，间隔偏大、衰减偏多
    ("M3 锚点取 min 而不是 max（用更早的那个时刻）",
     D, "    anchor = max(candidates, key=mastery.parse_instant)",
     "    anchor = min(candidates, key=mastery.parse_instant)",
     f"{T}::test_anchor_is_the_later_of_card_and_evidence"),

    # 按字符串比大小 ⇒ 混合时区下锚点选错（2026-01-02T02:00+08:00 实际早于 01-01T20:00Z）
    ("M4 锚点按字符串比大小（混合偏移下选错）",
     D, "    anchor = max(candidates, key=mastery.parse_instant)",
     "    anchor = max(candidates)",
     f"{T}::test_mixed_offsets_do_not_flip_the_anchor"),

    # 衰减没接上：P_eff 抄了 P_raw，所有"会掉"的断言都该红
    ("M5 p_effective 直接等于 p_raw（衰减没接上）",
     D, "        p_raw=p, p_effective=p * r, applied=True, reason=\"applied\", r=r,",
     "        p_raw=p, p_effective=p, applied=True, reason=\"applied\", r=r,",
     f"{T}::test_effective_p_is_raw_times_r_and_never_exceeds_raw"),

    # 秒当天用 ⇒ 所有间隔都被放大 86400 倍，曲线表全错
    ("M6 间隔按秒算（当成天用）",
     D, "    return seconds / 86400.0",
     "    return seconds",
     f"{T}::test_decay_curve_is_heavy_tailed_so_p_never_reaches_zero"),

    # 同一时刻也标 applied=True ⇒ "没变"与"算过"混为一谈
    ("M7 同一时刻也记 applied=True（把没变说成算过）",
     D, '        return DecayResult(p, p, False, "not_later", 1.0, 0.0, float(stability), anchor)',
     '        return DecayResult(p, p, True, "applied", 1.0, 0.0, float(stability), anchor)',
     f"{T}::test_same_instant_is_not_later_and_keeps_p_exactly"),

    # 调度层不再守 stability=None ⇒ 排程层的"遗忘风险"与衰减层同时被污染
    ("M8 scheduler.retrievability_at 不守 stability=None（返回 0.0）",
     S, "    if card.stability is None:\n        return None",
     "    if card.stability is None:\n        return 0.0",
     f"{T}::test_scheduler_returns_none_for_a_card_that_never_started"),

    # S 非正时当成"忘光了"，而不是"没有可用历史"
    ("M9 S<=0 时按遗忘处理（P 直接置 0）",
     D, '        return DecayResult(p, p, False, "no_history", None, elapsed, None, anchor)\n\n    if elapsed == 0.0:',
     '        return DecayResult(p, 0.0, True, "applied", 0.0, elapsed, 0.0, anchor)\n\n    if elapsed == 0.0:',
     f"{T}::test_non_positive_stability_is_treated_as_no_history_not_as_forgotten"),

    # 只用卡片时刻、忽略"最后一条证据" ⇒ 抑制掉的提交也能把间隔缩短
    ("M10 只用 last_review、忽略最后一条证据时刻",
     D, "    candidates = [t for t in (last_evidence_at, last_review) if t]",
     "    candidates = [t for t in (last_review,) if t]",
     f"{T}::test_anchor_uses_evidence_when_card_is_older"),
]


def main() -> int:
    results = []
    for name, path, old, new, node in MUTATIONS:
        env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-dec-")}
        orig = path.read_text(encoding="utf-8")
        if orig.count(old) != 1:
            results.append((name, "INVALID", f"锚点命中 {orig.count(old)} 次（应恰好 1 次）"))
            continue
        try:
            path.write_text(orig.replace(old, new, 1), encoding="utf-8")
            r = subprocess.run([PYEXE, "-m", "pytest", "-q", node, "-p", "no:cacheprovider"],
                               capture_output=True, text=True, env=env)
            if r.returncode == 0:
                status, detail = "MISSED", "测试仍绿——这条测试没在管这事"
            elif r.returncode == 5:
                status, detail = "INVALID", "测试节点不存在"
            else:
                lines = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
                if "error" in r.stdout.lower() and "failed" not in r.stdout.lower():
                    status, detail = "INVALID", "收集/语法错误，不算抓住"
                else:
                    status, detail = "DETECTED", lines[-1] if lines else ""
        finally:
            path.write_text(orig, encoding="utf-8")
        results.append((name, status, detail))

    for name, status, detail in results:
        print(f"{status:9} {name}  {detail}")
    ok = all(status == "DETECTED" for _, status, _ in results)
    print("---")
    print("判定：", f"全 DETECTED（{len(results)} 条）" if ok else "有漏网或无效变异，需处理")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
