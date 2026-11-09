"""验证门演示：commit -> verification -> failed/passed -> review -> approve。"""

from __future__ import annotations

import json
from pathlib import Path

from coordinator import Coordinator

ROOT = Path(__file__).resolve().parent
DB = ROOT / ".tmp" / "verification.db"


def show(label: str, data) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    c = Coordinator(DB)
    p = c.create_project("verification-demo", require_verification=True, require_approval=True)
    t = c.create_task(p["project_id"], "写 intro", ["doc#intro"])

    r1 = c.claim_task("W1", task_id=t["id"])
    submitted = c.commit_task(
        t["id"], "W1", r1["lease"]["fencing_token"],
        run={"cost_usd": 0.05, "quality": 1.0, "duration_s": 6},
    )
    show("W1 commits (self quality=1.0) -> verification", submitted["status"])

    failed = c.record_verification(t["id"], "verifier", False, 0.3, "missing example")
    show("verifier fails -> ready", failed["status"])

    r2 = c.claim_task("W2", task_id=t["id"])
    c.commit_task(t["id"], "W2", r2["lease"]["fencing_token"])
    passed = c.record_verification(t["id"], "verifier", True, 0.95, "checked DoD")
    show("verifier passes -> review", passed["status"])

    done = c.approve_task(t["id"], "human", "looks good")
    show("human approves -> done", done["status"])
    show("verifications", c.list_verifications(t["id"]))
    show("metrics", c.metrics())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
