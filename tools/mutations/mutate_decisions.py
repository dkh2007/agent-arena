"""本轮三项拍板的变异检验：B 脏提示 / C 稳定性基线 / D 对账落点。

各挑每条判定最承重的那一句做变异，确认有测试会红——防止"写了但没人管"。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _runner import run_mutations  # noqa: E402

ROOT = HERE.parents[1]
SRC_VH = ROOT / "src" / "arena" / "version_hint.py"
SRC_ST = ROOT / "src" / "arena" / "stability.py"
SRC_RR = ROOT / "src" / "arena" / "reconciliation_report.py"
D_VH = "tests/test_version_hint.py"
D_ST = "tests/test_stability.py"
D_RR = "tests/test_reconciliation_report.py"

MUTATIONS = [
    # B：脏工作区不报（把"有输出⇒提示"改成恒 None）——提示形同虚设
    (
        "B1 脏工作区不再提示",
        str(SRC_VH),
        '        if out.returncode == 0 and out.stdout.strip():\n            return HINT',
        '        if False:\n            return HINT',
        f"{D_VH}::test_dirty_tasks_returns_exactly_one_hint",
    ),
    # B：把整个仓库都当任务改动（范围放宽，噪声误报）
    (
        "B2 检测范围放宽到整仓",
        str(SRC_VH),
        '["git", "-C", str(directory.parent), "status", "--porcelain", "--", str(directory)]',
        '["git", "-C", str(directory.parent), "status", "--porcelain"]',
        f"{D_VH}::test_changes_outside_tasks_do_not_trigger",
    ),
    # C：稳定性判定恒真（区分了指纹也算稳定）
    (
        "C1 不同指纹也算稳定",
        str(SRC_ST),
        '        stable=(distinct == 1),',
        '        stable=True,',
        f"{D_ST}::test_different_decision_breaks_stability",
    ),
    # C：把 params 顺序算进指纹（同决策换 key 顺序误报抖动）
    (
        "C2 参数 key 顺序影响指纹",
        str(SRC_ST),
        'canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"))',
        'canonical = json.dumps(params, ensure_ascii=False, separators=(",", ":"))',
        f"{D_ST}::test_param_key_order_does_not_matter",
    ),
    # C：把返回体/status 算进指纹（mock 故障变化误报模型抖动）
    (
        "C3 返回体算进决策指纹",
        str(SRC_ST),
        '        seq.append([call["name"], canonical])',
        '        seq.append([call["name"], canonical, call.get("status"), str(call.get("body"))])',
        f"{D_ST}::test_status_and_body_are_excluded",
    ),
    # D：对账结论污染分数
    (
        "D1 对账写进 score",
        str(SRC_RR),
        '    report["reconciliation"] = verdict',
        '    report["score"]["reconciliation"] = verdict\n    report["reconciliation"] = verdict',
        f"{D_RR}::test_attach_does_not_touch_score_or_process_fact",
    ),
    # D：学生可见 notes 写回指控性字眼
    (
        "D2 mismatch 的 note 写回『疑似编造』",
        str(SRC_RR),
        '"mismatch": "过程对账：有数值暂未在操作记录中找到对应出处，已交人工复核。"',
        '"mismatch": "过程对账：疑似编造，已交人工复核。"',
        f"{D_RR}::test_student_notes_never_accuse",
    ),
]


def main() -> int:
    return run_mutations(MUTATIONS, default_path=SRC_RR, temp_prefix="pt-decisions-")


if __name__ == "__main__":
    raise SystemExit(main())
