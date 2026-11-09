"""把 vendor/ 和 demo-ledger/ 放进 sys.path，供 a2a-lab 下的脚本 import。"""

import sys
from pathlib import Path

LAB = Path(__file__).resolve().parent
for _p in (LAB / "vendor", LAB.parent / "demo-ledger"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
