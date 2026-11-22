"""零配置启动入口：`python scripts/lab.py --task <任务>`（B-32 第 0 层）。

`python -m arena.lab` 也能跑，但要求 `PYTHONPATH=src`——学习者第一步就撞这个，
等于把「本地跑起来」又推远一格。所以给一个替用户挂好路径的壳。

用法：

    python scripts/lab.py --list                        # 看有哪些任务
    python scripts/lab.py --task dbg-001-order-report   # 起服务并打印端点
    python scripts/lab.py --task tasks/dbg-001-order-report.yaml --port 8766

真正的实现在 `src/arena/lab.py`（含"不泄露判分信息"的约束与对应测试）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena.lab import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
