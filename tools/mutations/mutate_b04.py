"""B-04 机制层的变异检验（临时脚本，不提交）。

判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）：
  DETECTED 变异被指定测试抓住
  MISSED   测试仍绿 ⇒ 这条测试没在管这事
  INVALID  变异自身崩（锚点未命中 / 语法错 / 节点不存在）⇒ 不算抓住
"""

from __future__ import annotations

from pathlib import Path

# 站到仓根再干活：脚本里全是相对路径（src/… / tests/…），在别的目录直接跑会
# FileNotFoundError。round-86 收尾时实测踩到（用户要求给完整路径，才发现"完整路径"
# 反而跑不通）。这里按"往上找 src/arena"定位仓根，从 .git/ 或 tools/mutations/ 调用都成立。
import os as _os

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
_os.chdir(_ROOT)

from _runner import run_mutations  # 同目录；判定只有一处实现

SRC = Path("src/arena/llm_judge.py")
T = "tests/test_llm_judge.py"

# (名称, 锚点, 替换, 应抓住它的测试)
MUTATIONS = [
    (
        "M1 越界不报错（改成 clamp）",
        "        if not RATING_MIN <= self.rating <= RATING_MAX:",
        "        if False:",
        f"{T}::test_rating_out_of_range_is_loud",
    ),
    (
        "M2 放过 bool / 非 int",
        "        if isinstance(self.rating, bool) or not isinstance(self.rating, int):",
        "        if False:",
        f"{T}::test_rating_type_must_be_int",
    ),
    (
        "M3 词表外模式静默放过",
        "    unknown = [p for p in verdict.patterns if p not in known]",
        "    unknown = []",
        f"{T}::test_pattern_outside_vocabulary_is_loud",
    ),
    (
        "M4 一致率改多数派占比",
        "        if a.rating == b.rating:\n            same += 1",
        "        pass",
        f"{T}::test_three_runs_use_pairwise_not_vote",
    ),
    (
        "M5 接口故障记成不一致",
        "            raise JudgeError(f\"第 {k + 1}/{runs} 次评价失败：{type(exc).__name__}: {exc}\") from exc",
        "            verdict = JudgeVerdict(rating=RATING_MIN)",
        f"{T}::test_judge_exception_is_not_disagreement",
    ),
    (
        "M6 输出里塞进 ratio（碰分数）",
        '            "judge_agreement": self.rating_agreement,',
        '            "judge_agreement": self.rating_agreement,\n            "ratio": 1.0,',
        f"{T}::test_report_fields_never_touch_score_fields",
    ),
    (
        "M7 needs_arbitration 自带默认阈值",
        "    def needs_arbitration(self, threshold: float) -> bool:",
        "    def needs_arbitration(self, threshold: float = 0.8) -> bool:",
        f"{T}::test_threshold_has_no_default",
    ),
    (
        "M8 模式一致率混进 rating_agreement",
        "        return self.rating_agreement\n\n    def needs_arbitration",
        "        return (self.rating_agreement + self.pattern_agreement) / 2\n\n    def needs_arbitration",
        f"{T}::test_pattern_jaccard_is_separate_from_rating_agreement",
    ),
    (
        "M9 n<2 也返回 1.0",
        "    if runs < DEFAULT_RUNS:\n        raise JudgeError(f\"runs={runs} 太小，「双评」至少 {DEFAULT_RUNS} 次\")",
        "    if False:\n        raise JudgeError('x')",
        f"{T}::test_single_run_cannot_yield_agreement",
    ),
]

def main() -> int:
    """判定与还原全交给 `_runner`：本文件只负责“变异长什么样”。

    以前这里有一份自己的判定代码，窄看只是重复；实际是两套规则会分叉——旧那套
    把 rc=2/3/4 也当 `DETECTED`，于是节点名写错时会报“抓住了”（round-89 M14 实测）。
    """
    return run_mutations(MUTATIONS, default_path=SRC, temp_prefix="pt-b04-")

if __name__ == "__main__":
    raise SystemExit(main())
