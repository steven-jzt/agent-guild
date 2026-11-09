"""审批超时演示：TTL 到期 -> expired -> 重新发起 -> 2-of-2 通过。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from coordinator import Coordinator

ROOT = Path(__file__).resolve().parent
DB = ROOT / ".tmp" / "approval-timeout.db"


def show(label: str, data) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    c = Coordinator(DB)
    p = c.create_project(
        "approval-timeout", require_approval=True,
        approvals_required=2, approval_ttl_seconds=0.5,
    )
    t = c.create_task(p["project_id"], "写 intro", ["doc#intro"])
    r = c.claim_task("W1", task_id=t["id"])
    c.commit_task(t["id"], "W1", r["lease"]["fencing_token"])
    show("commit -> review", {
        "pending": len(c.list_approvals("pending")),
        "expires_in": [a["expires_in"] for a in c.list_approvals("pending")],
    })

    time.sleep(0.6)
    c.reap_expired()
    show("TTL expires -> approvals expired, task still review", {
        "task": c.get_task(t["id"])["status"],
        "pending": len(c.list_approvals("pending")),
        "expired": len(c.list_approvals("expired")),
    })

    requested = c.request_approvals(t["id"], "human", count=2, ttl_seconds=10)
    show("re-request approvals", {
        "approval_ids": requested["approval_ids"],
        "pending": len(c.list_approvals("pending")),
    })
    c.approve_task(t["id"], "alice", "lgtm")
    show("after alice", c.get_task(t["id"])["status"])
    show("after bob", c.approve_task(t["id"], "bob", "lgtm")["status"])
    show("metrics", c.metrics())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
