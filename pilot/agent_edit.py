"""dry-run 用的脚本化 agent：只在 workspace 里改 src/app.py。"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    if "def mul" not in text:
        text += "\n\ndef mul(a, b):\n    return a * b\n"
        path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
