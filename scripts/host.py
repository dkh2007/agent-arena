"""托管会话的运维入口（B-51）。

    python scripts/host.py mint   --db arena.db --task dbg-001-order-report --count 30 \
                                  --base-url http://10.1.2.3:8080 --out 名单.csv
    python scripts/host.py roster --db arena.db --out 结果.csv

零配置入口，跟 `scripts/lab.py` 一样替你挂好 `src/`；实现与判据在 `src/arena/hosting.py`
（含"为什么 mint 不等于循环 create"与"名单里为什么不放完整 token"）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena.hosting import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
