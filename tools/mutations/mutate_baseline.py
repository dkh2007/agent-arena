"""B-04f 全库基线 + B-04g「补不上那批」分档的变异检验。

判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）：

    DETECTED  变异后 pytest 退出码 **1** —— 真被抓住
    MISSED    变异后仍全绿 —— 这条测试没在管这事
    INVALID   锚点没命中 / 收集失败 —— **不算抓住**

## 这 14 组各自防的是什么

基线的失效方式和单份重评不同：单份错了会算错分，批次错了**看起来全对**——
数报得漂亮、报告齐全，只是漏了一批人或把补不上的算成了补上了。所以：

- M1 打"跑完不重算范围"（用跑之前的范围冒充跑之后，等于永远不承认自己干过活）；
- M2 打"只跑 stale 不跑 unfingerprinted"——那正好是基线要治的那批人；
- M3 打"补不上的算成补上了"（缺产物被计进 reappraised）；
- M4 打"演练也落库"（预判变成既成事实，且再也不可回退）；
- M5 打"同一个任务传两次悄悄去重"（两个版本被当成一个，事后无从发现）；
- M6 打"没理由也照跑"（整批改分没有任何线索）；
- M7 打"结果不带 user_id"（跑完了没人知道自己被重算过）；
- M8 打"报表说已经全部打平"（把补不上的从剩余里抹掉，报告立刻变漂亮）。

分档（B-04g）那一段，失效方式又是一种：**它给的是处置名单**，多一个人、少一个人
都会被拿去当"要不要向学生解释"的依据，而报告永远说得通。所以：

- M9 打"所有补不上的都要交代"（让本来没分的人替我们的数据缺失背一次解释）；
- M10 打"0 分有证据的也归没分可解释"（漏掉掌握度被抬过的那批，与 B-07b 同批人）；
- M11 打"读落库时那份而不是生效报告"（报出来的分和接口给的数不一致，对账时说不清）；
- M12 打"缺 score.ratio 默认 0"（从"要交代"掉进"没分可解释"，且不报错）；
- M13 打"坏 JSON 当空报告"（坏数据被静默降级成"当年没分"）；
- M14 打"total 用补不上的那些冒充全库"（占比永远接近 100%，人以为自己面对巨大包袱）。
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

from _runner import run_mutations  # 同目录；判定只有一处实现

BL = Path("src/arena/baseline.py")
ST = Path("src/arena/store.py")
RAP = Path("src/arena/reappraisal.py")
T = "tests/test_baseline.py"

MUTATIONS = [
    ("M1 跑完不重算范围（用跑之前的范围冒充跑之后）",
     BL,
     "    after = {tid: rap.scope_for_task(conn, task) for tid, task in by_id.items()}",
     "    after = before  # 变异：不重算，等于永远不承认自己干过活",
     f"{T}::test_baseline_backfills_fingerprints_for_history_that_can_be_replayed"),

    ("M2 只跑 stale、不跑 unfingerprinted（正好漏掉基线要治的那批人）",
     BL,
     "        for sid in before[tid].all_ids:",
     "        for sid in before[tid].stale:  # 变异：历史那批不处理",
     f"{T}::test_baseline_backfills_fingerprints_for_history_that_can_be_replayed"),

    ("M3 补不上的算成补上了（缺产物被计进 reappraised）",
     BL,
     '            "missing_artifacts": by_state.get("missing_artifacts", 0),',
     '            "missing_artifacts": 0,  # 变异：把它并进 reappraised',
     f"{T}::test_baseline_reports_what_it_could_not_repair_instead_of_calling_it_done"),

    ("M4 演练也落库（预判变成既成事实）",
     BL,
     "                               apply=apply, created_at=created_at)",
     "                               created_at=created_at)  # 变异：无视 apply",
     f"{T}::test_dry_run_writes_nothing_and_shows_it_by_leaving_the_scope_unchanged"),

    ("M5 同一个任务传两次悄悄去重（两个版本被当成一个）",
     BL,
     "        if tid in out:",
     "        if False:  # 变异：不查重，后一个覆盖前一个",
     f"{T}::test_duplicate_tasks_are_rejected"),

    ("M6 没理由也照跑（整批改分没有任何线索）",
     BL,
     '    if not isinstance(reason, str) or not reason.strip():\n'
     '        raise BaselineError("reason 必须写：这是「为什么整批重算」的唯一线索")',
     "    if False:  # 变异：理由交给每一份提交去验（范围为空时等于没验）\n"
     "        pass",
     f"{T}::test_baseline_requires_a_reason_before_doing_any_work"),

    ("M7 结果不带 user_id（跑完了没人知道自己被重算过）",
     RAP,
     '    user_id = store.load_submission(conn, submission_id)["user_id"]',
     "    user_id = None  # 变异：只给 submission_id",
     f"{T}::test_baseline_names_who_lost_points"),

    ("M8 报表说「已全部打平」（把补不上的从剩余里抹掉）",
     BL,
     '            "still_unfingerprinted": sum(len(s.unfingerprinted) for s in self.after.values()),',
     '            "still_unfingerprinted": 0,  # 变异：报告变漂亮了，那批人再也没人提',
     f"{T}::test_blocked_rows_stay_in_scope_forever_and_are_retried_but_never_rewritten"),

    # ── 分档（B-04g 取数层）：这六组打的是"怎么跟学生交代"这条链 ──────────────
    ("M9 所有补不上的都算「要交代」（让本来没分的人也来背一次解释）",
     BL,
     "        if ratio > 0:",
     "        if True:  # 变异：不区分当年有没有分",
     f"{T}::test_only_the_people_who_had_a_score_need_an_explanation"),

    ("M10 0 分有证据的也归「没分可解释」（漏掉掌握度被抬过的那批）",
     BL,
     "        elif positive:",
     "        elif False:  # 变异：不看过程证据",
     f"{T}::test_zero_score_with_evidence_and_zero_score_without_are_different_buckets"),

    ("M11 读落库时那份而不是生效报告（报出来的分和接口给的数不一致）",
     ST,
     '        raw = row["latest_report"] if from_reappraisal else row["diagnosis"]',
     '        raw = row["diagnosis"]  # 变异：永远读冻结的那份',
     f"{T}::test_tier_split_reads_the_effective_report_not_the_frozen_one"),

    ("M12 缺 score.ratio 默认成 0 分（从「要交代」掉进「没分可解释」）",
     ST,
     '            "ratio": float(_require(report, "score", "ratio")),',
     '            "ratio": float((report.get("score") or {}).get("ratio", 0.0)),'
     '  # 变异：缺分数当 0',
     f"{T}::test_corrupt_history_is_loud_instead_of_looking_like_zero_points"),

    ("M13 坏 JSON 当成空报告（坏数据被静默降级）",
     ST,
     '        except ValueError as exc:\n'
     '            raise StoreError(\n'
     '                f"{row[\'submission_id\']} 的{\'重评\' if from_reappraisal else \'\'}报告不是合法 JSON"\n'
     '                f"（{exc}）——坏数据不该被当成「当年没分」算进「不需要交代」那一档"\n'
     '            ) from exc',
     "        except ValueError:  # 变异：坏数据不响，当成空报告往下走\n"
     "            report = {}",
     f"{T}::test_corrupt_history_is_loud_instead_of_looking_like_zero_points"),

    ("M14 total 用「补不上的那些」冒充全库（占比永远是 100%）",
     BL,
     "        total=store.count_submissions(conn),",
     "        total=len(store.blocked_submissions(conn)),  # 变异：分母用错",
     f"{T}::test_tier_split_is_read_only_and_its_total_covers_the_whole_library"),
]

def _run(node: str) -> tuple[int, str]:
    env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-base-")}
    r = subprocess.run([PYEXE, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                       capture_output=True, text=True, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()

def main() -> int:
    """判定与还原全交给 `_runner`：本文件只负责"变异长什么样"。

    以前这里有一份自己的判定代码，窄看只是重复；实际是两套规则会分叉——旧那套
    把 rc=2/3/4 也当 `DETECTED`，于是节点名写错时会报"抓住了"（round-89 M14 实测）。
    """
    return run_mutations(MUTATIONS, temp_prefix="pt-baseline-")

if __name__ == "__main__":
    raise SystemExit(main())
