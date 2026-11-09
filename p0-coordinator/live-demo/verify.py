"""验证 live demo：任务完成、文件内容、runs 记录。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from coordinator import Coordinator  # noqa: E402

DB = ROOT / "live.db"


def main() -> int:
    c = Coordinator(DB)
    projects = [p for p in c.list_projects() if p["name"] == "live-demo"]
    if not projects:
        print("no live-demo project")
        return 1
    pid = projects[0]["project_id"]
    tasks = c.list_tasks(project_id=pid)
    checks = []
    for task in sorted(tasks, key=lambda t: t["title"]):
        letter = task["title"].split()[-1]
        path = ROOT / f"part-{letter.lower()}.md"
        exists = path.exists()
        content = path.read_text(encoding="utf-8").strip().splitlines() if exists else []
        heading_ok = bool(content) and content[0].strip() == f"# Part {letter}"
        sentence_ok = len(content) >= 2 and "coordination" in content[1].lower()
        checks.append(
            {
                "task": task["id"],
                "title": task["title"],
                "status": task["status"],
                "file": str(path),
                "heading_ok": heading_ok,
                "sentence_ok": sentence_ok,
            }
        )
    runs = c.list_runs(limit=20)
    out = {
        "project_id": pid,
        "checks": checks,
        "all_done": all(t["status"] == "done" for t in tasks),
        "all_files_ok": all(c["heading_ok"] and c["sentence_ok"] for c in checks),
        "runs": len(runs),
        "run_quality": [r["quality"] for r in runs],
        "metrics": c.metrics(),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["all_done"] and out["all_files_ok"] and out["runs"] == 3 else 2


if __name__ == "__main__":
    raise SystemExit(main())
