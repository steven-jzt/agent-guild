"""独立 verifier 跑 verified live demo：对每个 verification 任务执行 checks。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))

from coordinator import Coordinator  # noqa: E402
from verifier import auto_verify  # noqa: E402

DB = ROOT / "verified.db"
BASE = ROOT.parent.parent  # p0-coordinator，checks 的相对路径基准


def main() -> int:
    c = Coordinator(DB)
    projects = [p for p in c.list_projects() if p["name"] == "live-demo-verified"]
    if not projects:
        print("no project")
        return 1
    pid = projects[0]["project_id"]
    results = []
    for task in c.list_tasks(project_id=pid):
        if task["status"] == "verification":
            results.append(auto_verify(c, task["id"], verifier="verifier", root=str(BASE)))
        else:
            results.append({"task_id": task["id"], "status": task["status"], "skipped": True})
    tasks = c.list_tasks(project_id=pid)
    out = {
        "project_id": pid,
        "results": results,
        "tasks": [{ "id": t["id"], "title": t["title"], "status": t["status"]} for t in tasks],
        "all_done": all(t["status"] == "done" for t in tasks),
        "verifications": c.list_verifications(limit=20),
        "metrics": c.metrics(),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out["all_done"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
