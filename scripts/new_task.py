"""加题工具入口：`python scripts/new_task.py --list|--check|--new`（B-30，2026-11-22）。

`python -m arena.authoring` 也能跑，但要求 `PYTHONPATH=src`；加题的人不该先学这个。
真正的实现在 `src/arena/authoring.py`，判据与 `tests/` 同源（每条 finding 都标了对应测试）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena.authoring import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
