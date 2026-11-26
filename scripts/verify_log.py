"""重放一份 `calls.jsonl`，验证它没被改过：`python scripts/verify_log.py --log calls.jsonl --task dbg-001-order-report`

零配置入口，跟 `scripts/lab.py` 一样替你挂好 `src/`；
实现与判据在 `src/arena/log_verify.py`（含"为什么不猜未声明的工具"）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena.log_verify import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
