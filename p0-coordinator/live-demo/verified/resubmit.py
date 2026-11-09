"""修好 verifier root 后，让 3 个任务重新提交并再验证一次。

第一次验证因为 root 指错（live-demo 而不是 p0-coordinator），路径重复，
3 个任务都被判失败并退回 ready。这里模拟 worker 重试：
re-claim -> commit -> auto_verify（root 已修正）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))

from coordinator import Coordinator  # noqa: E402
from verifier import auto_verify  # noqa: E402

DB = ROOT / "verified.db"
BASE = ROOT.parent.parent


def main() -> int:
    c = Coordinator(DB)
    projects = [p for p in c.list_projects() if p["name"] == "live-demo-verified"]
    pid = projects[0]["project_id"]
    workers = {"Part A": "agent-a", "Part B": "agent-b", "Part C": "agent-c"}
    results = []
    for task in c.list_tasks(project_id=pid):
        worker = workers[task["title"]]
        if task["status"] == "ready":
            claimed = c.claim_task(worker, task_id=task["id"])
            if not claimed.get("claimed"):
                results.append({"task": task["id"], "error": claimed})
                continue
            c.commit_task(task["id"], worker, claimed["lease"]["fencing_token"])
        current = c.get_task(task["id"])
        if current["status"] == "verification":
            results.append(auto_verify(c, task["id"], verifier="verifier", root=str(BASE)))
        else:
            results.append({"task": task["id"], "status": current["status"], "skipped": True})
    tasks = c.list_tasks(project_id=pid)
    out = {
        "project_id": pid,
        "results": results,
        "tasks": [{"title": t["title"], "status": t["status"]} for t in tasks],
        "all_done": all(t["status"] == "done" for t in tasks),
        "metrics": c.metrics(),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out["all_done"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
