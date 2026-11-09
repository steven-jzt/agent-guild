"""Independent verifier + 2-of-2 approval for governance live demo."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))

from coordinator import Coordinator  # noqa: E402
from verifier import auto_verify  # noqa: E402

DB = ROOT / "governance.db"
BASE = ROOT.parent.parent


def main() -> int:
    c = Coordinator(DB)
    projects = [p for p in c.list_projects() if p["name"] == "live-demo-governance"]
    pid = projects[0]["project_id"]
    results = []
    for task in c.list_tasks(project_id=pid):
        item = {"task_id": task["id"], "title": task["title"]}
        if task["status"] == "verification":
            verified = auto_verify(c, task["id"], verifier="verifier", root=str(BASE))
            item["verification"] = {"passed": verified["passed"], "score": verified["score"]}
            task = c.get_task(task["id"])
        if task["status"] == "review":
            first = c.approve_task(task["id"], "alice", "lgtm")
            second = c.approve_task(task["id"], "bob", "lgtm")
            item["approvals"] = {"after_alice": first["status"], "after_bob": second["status"]}
            task = second
        item["final"] = task["status"]
        results.append(item)

    tasks = c.list_tasks(project_id=pid)
    out = {
        "project_id": pid,
        "results": results,
        "tasks": [{"title": t["title"], "status": t["status"]} for t in tasks],
        "all_done": all(t["status"] == "done" for t in tasks),
        "approvals": c.list_approvals(limit=20),
        "metrics": c.metrics(),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out["all_done"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
