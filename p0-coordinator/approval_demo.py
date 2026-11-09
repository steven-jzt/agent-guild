"""审批门演示：commit -> review -> approve/reject。"""

from __future__ import annotations

import json
from pathlib import Path

from coordinator import Coordinator

ROOT = Path(__file__).resolve().parent
DB = ROOT / ".tmp" / "approval.db"


def show(label: str, data) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    c = Coordinator(DB)
    p = c.create_project("approval-demo", require_approval=True)
    t1 = c.create_task(p["project_id"], "写 intro", ["doc#intro"])
    t2 = c.create_task(p["project_id"], "写 api", ["doc#api"])

    r1 = c.claim_task("W1", task_id=t1["id"])
    submitted = c.commit_task(
        t1["id"], "W1", r1["lease"]["fencing_token"],
        run={"cost_usd": 0.05, "quality": 0.88, "duration_s": 6},
    )
    show("W1 commits -> review", submitted)
    show("pending approvals", c.list_approvals("pending"))

    show("human approves", c.approve_task(t1["id"], "human", "looks good"))

    r2 = c.claim_task("W2", task_id=t2["id"])
    c.commit_task(t2["id"], "W2", r2["lease"]["fencing_token"])
    show("W2 commits -> review", c.get_task(t2["id"]))
    show("human rejects", c.reject_task(t2["id"], "human", "missing edge cases"))
    show("rejected task can be claimed again", c.claim_task("W3", task_id=t2["id"]))

    show("metrics", c.metrics())
    show("events", c.events(limit=10))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
