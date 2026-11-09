"""为 live demo 建项目/任务，输出 project_id 和 task ids。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from coordinator import Coordinator  # noqa: E402

DB = ROOT / "live.db"


def main() -> int:
    if DB.exists():
        DB.unlink()
    c = Coordinator(DB)
    p = c.create_project("live-demo")
    tasks = []
    for letter in ("A", "B", "C"):
        t = c.create_task(
            p["project_id"],
            f"Part {letter}",
            write_set=[f"live-demo/part-{letter.lower()}.md"],
            kind="docs",
            dod=f"live-demo/part-{letter.lower()}.md contains '# Part {letter}' and one sentence with the word coordination",
        )
        tasks.append({"letter": letter, "task_id": t["id"], "file": f"live-demo/part-{letter.lower()}.md"})
    print(json.dumps({"db": str(DB), "project_id": p["project_id"], "tasks": tasks}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
