"""Governance live demo：verification + 2-of-2 approval。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))

from coordinator import Coordinator  # noqa: E402

DB = ROOT / "governance.db"


def main() -> int:
    if DB.exists():
        DB.unlink()
    c = Coordinator(DB)
    p = c.create_project(
        "live-demo-governance",
        require_verification=True,
        require_approval=True,
        approvals_required=2,
    )
    tasks = []
    for letter in ("A", "B", "C"):
        path = f"live-demo/governance/part-{letter.lower()}.md"
        t = c.create_task(
            p["project_id"],
            f"Part {letter}",
            write_set=[path],
            kind="docs",
            dod=f"{path} contains '# Part {letter}' and one sentence with the word coordination",
            checks=[
                {"type": "file_exists", "path": path},
                {"type": "file_contains", "path": path, "text": "coordination"},
                {"type": "file_regex", "path": path, "pattern": f"^# Part {letter}$"},
            ],
        )
        tasks.append({"letter": letter, "task_id": t["id"], "file": path})
    print(json.dumps({"db": str(DB), "project_id": p["project_id"], "tasks": tasks},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
