"""任务数据版本的一句提示——**只提示，不改判定、不新增存储**（B 口径，2026-11-19 人拍）。

## 为什么单独成一个函数，而不是塞进报告

`repo_head()` 把 HEAD 短哈希写进 `task_git_hash` 当版本锚点。缺口是：工作区里
`tasks/*.yaml` 若有未提交改动，报告仍锚在 HEAD 上，于是"这份分是按哪版任务算的"
会指向一个**并不包含那次改动**的提交。这是失真，不是崩溃，所以不能改分数、不能抛错。

人拍板的处置只有一句：**加一句"有修改"提示**。三种落点里选了最轻的：

| 落点 | 为什么没选 |
|---|---|
| 改 `task_git_hash` 的值 | schema 限定 `^[0-9a-f]{7,40}$`，拼后缀直接违规；且它进重评指纹，改值会触发全库无谓重评 |
| 自动进报告 `notes` | `notes` 对学生可见，且 `test_submission_api` 有 `body == _internal_report(raw)` 逐字段锁；按工作区状态漂移会让大量用例 flaky |
| **独立纯函数，按需调用** | 不写库、不进报告、不改哈希；谁要核对版本就查一句，默认零影响 |

所以它回答的是"要不要提醒核对版本"，**不**回答"这份分算得对不对"。后者是判定层的事。

## 检测范围：只看 `tasks/`

整个仓库脏（改了源码、改了文档）不影响任务数据版本。只检测 `tasks/` 下的已跟踪文件
有没有未提交改动（含未暂存、已暂存未提交、新建未跟踪）。看别的目录只会制造噪音——
"我改了 README 也要提示任务版本有修改"是错误归因。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = ROOT / "tasks"

HINT = "任务文件有未提交改动：task_git_hash 只锚定 HEAD，未包含工作区里的任务改动；核对版本前请先提交。"


def workspace_dirty_hint(tasks_dir: Path | None = None,
                         *, run: object | None = None) -> str | None:
    """`tasks/` 有未提交改动则返回**一句**提示，否则 `None`。

    `run` 是给测试注入的钩子（签名同 `subprocess.run`）；生产路径不传。
    检测失败（git 不可用、目录不存在）返回 `None`——这里没有资格因为"查不了版本"
    就把一次正常评测判成失败，那是把工程问题算到学习者头上（决策 7 的同一条理由）。
    """
    directory = Path(tasks_dir) if tasks_dir is not None else TASKS_DIR
    runner = run if run is not None else subprocess.run
    try:
        out = runner(  # type: ignore[operator]
            ["git", "-C", str(directory.parent), "status", "--porcelain", "--", str(directory)],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return HINT
    except (OSError, subprocess.SubprocessError):
        pass
    return None
