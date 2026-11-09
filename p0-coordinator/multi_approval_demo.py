"""多级审批演示：2-of-2，两个不同 approver 都签才 done。"""

from __future__ import annotations

import json
from pathlib import Path

from coordinator import Coordinator

ROOT = Path(__file__).resolve().parent
DB = ROOT / ".tmp" / "multi-approval.db"


def show(label: str, data) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    c = Coordinator(DB)
    p = c.create_project("multi-approval", require_approval=True, approvals_required=2)
    t = c.create_task(p["project_id"], "写 intro", ["doc#intro"])
    r = c.claim_task("W1", task_id=t["id"])
    submitted = c.commit_task(t["id"], "W1", r["lease"]["fencing_token"])
    show("commit -> review, 2 pending", {
        "status": submitted["status"],
        "pending": len(c.list_approvals("pending")),
    })

    first = c.approve_task(t["id"], "alice", "lgtm")
    show("alice approves -> still review", {
        "status": first["status"],
        "pending": len(c.list_approvals("pending")),
    })

    try:
        c.approve_task(t["id"], "alice", "again")
    except Exception as exc:
        show("same approver again -> rejected", str(exc))

    second = c.approve_task(t["id"], "bob", "lgtm")
    show("bob approves -> done", second["status"])
    show("metrics", c.metrics())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
