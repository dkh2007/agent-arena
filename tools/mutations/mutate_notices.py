"""B-04g「每条记录说什么」的变异检验（round-100）。

判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）：

    DETECTED  变异后 pytest 退出码 **1** —— 真被抓住
    MISSED    变异后仍全绿 —— 这条测试没在管这事
    INVALID   锚点没命中 / 收集失败 —— **不算抓住**

## 这一层的失效方式

算错会在报表里显形，**说错不会**——文案看着通顺，只是少了一件该说的事，
或者把"待定"说成了"不用管"。所以这里打的全是"读起来没毛病"的错：

- M1 少一件"能跟什么比"（学生看完就去跟别人对数）；
- M2 少一件"这不是你的问题"（人会把系统的改动归因到自己身上）；
- M3 把"掌握度被冻住"那批并进"当年没分、不用管"（等于替人拍了"不用管"）；
- M4 把没分的那批也推上页面（通知变成噪音，真正要紧的被淹没）；
- M5 内部术语检查放行（学生读到"平台出故障"，读不到"标准改了"）；
- M6 加一句"以后不会再变"（标准还会修，这句话迟早变成假话）；
- M7 缺参数时静默填空（把 `{changed_on}` 原样端给学生，或者悄悄当今天）；
- M8 分数变了当没变（该说的那句整条丢掉，界面静默换成新分）。
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

from _runner import run_mutations  # 同目录；判定只有一处实现

NT = Path("src/arena/notices.py")
T = "tests/test_notices.py"

MUTATIONS = [
    ("M1 少一件「能跟什么比」（学生看完就去跟别人对数）",
     NT,
     '''        ("comparable",
         "新分只和同一天之后、按新判据判的记录比；不能和更早的作业比（那些也是旧判据），"
         "也不能和同学比。"),''',
     "        # 变异：不比边界",
     f"{T}::test_recalculated_record_names_what_changed_and_the_new_score"),

    ("M2 少一件「这不是你的问题」（人会把系统的改动归因到自己）",
     NT,
     '''        ("not_your_fault",
         "这不是你的问题：是判据改了、而当时的作答内容没有留到今天。"),''',
     "        # 变异：不说这句",
     f"{T}::test_scored_but_unrepairable_gets_all_four_elements_on_the_page"),

    ("M3 把「掌握度被冻住」并进「当年没分、不用管」",
     NT,
     '            elif tier == "T3":',
     '            elif False:  # 变异：T3 落到下面的 legacy_unscored',
     f"{T}::test_evidence_only_record_is_pending_not_folded_into_unscored"),

    # round-101 给这批记录加了 `retired` 标记位，锚点里必须带上它——不然命中 0 次，
    # 变异会以 INVALID 形式"看起来做过"，实际一个字符都没改。
    ("M4 把没分的那批也推上页面（通知变成噪音）",
     NT,
     '            else:\n'
     '                notice = Notice(sid, row["user_id"], tid, "legacy_unscored", "none", (),\n'
     '                                retired=retired)',
     '            else:\n'
     '                notice = Notice(sid, row["user_id"], tid, "legacy_unscored", "page", (),\n'
     '                                retired=retired)  # 变异',
     f"{T}::test_zero_score_unrepairable_keeps_its_diagnosis_and_is_not_announced"),

    ("M5 内部术语检查放行（学生读到「平台出故障」）",
     NT,
     "        if word.lower() in lowered:",
     "        if False:  # 变异：不查内部术语",
     f"{T}::test_internal_jargon_is_rejected_before_it_reaches_a_student"),

    ("M6 加一句「以后不会再变」（标准还会修，这句迟早变假话）",
     NT,
     '''        ("next_step", "你不需要做什么，也没有任何扣分后果；复习计划照常。"),''',
     '''        ("next_step", "你不需要做什么，也没有任何扣分后果；以后不会再变了。"),  # 变异''',
     f"{T}::test_no_notice_text_contains_jargon_or_promises"),

    ("M7 缺参数时静默填空（把 {changed_on} 原样端给学生）",
     NT,
     '            raise NoticeError(f"{name} 必须写：学生的页面上要靠它解释「为什么变了」")',
     "            value = value or ''  # 变异：不抛，让空值一路往下",
     f"{T}::test_missing_facts_are_rejected_instead_of_invented"),

    ("M8 分数变了当没变（该说的那句整条丢掉）",
     NT,
     "        changed = abs(effective - original) > 1e-12",
     "        changed = False  # 变异：不认分数变化",
     f"{T}::test_recalculated_record_names_what_changed_and_the_new_score"),
]

def _run(node: str) -> tuple[int, str]:
    env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-nt-")}
    r = subprocess.run([PYEXE, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                       capture_output=True, text=True, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()

def main() -> int:
    """判定与还原全交给 `_runner`：本文件只负责"变异长什么样"。

    以前这里有一份自己的判定代码，窄看只是重复；实际是两套规则会分叉——旧那套
    把 rc=2/3/4 也当 `DETECTED`，于是节点名写错时会报"抓住了"（round-89 M14 实测）。
    """
    return run_mutations(MUTATIONS, temp_prefix="pt-notices-")

if __name__ == "__main__":
    raise SystemExit(main())
