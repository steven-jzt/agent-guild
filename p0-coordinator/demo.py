"""P0 协调器演示：冲突、过期、fencing、依赖。

运行：python demo.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from coordinator import Coordinator, LeaseError

ROOT = Path(__file__).resolve().parent
DB = ROOT / ".tmp" / "demo.db"


def show(label: str, data) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    c = Coordinator(DB, default_ttl=120)

    p = c.create_project("doc-demo")
    t1 = c.create_task(p["project_id"], "写 intro", ["doc.md#intro"], priority=1)
    t2 = c.create_task(p["project_id"], "改 intro", ["doc.md#intro"])
    t3 = c.create_task(p["project_id"], "写 api", ["doc.md#api"], deps=[t1["id"]], priority=5)
    show("created", [t1, t2, t3])

    w1 = c.claim_task("W1", project_id=p["project_id"], ttl_seconds=2)
    show("W1 claims", w1)

    w2 = c.claim_task("W2", project_id=p["project_id"], ttl_seconds=2)
    show("W2 claims while W1 holds intro", w2)

    print("\n... W1 的租约过期（2 秒）...")
    time.sleep(2.2)

    w2b = c.claim_task("W2", project_id=p["project_id"], ttl_seconds=30)
    show("W2 claims after expiry", w2b)

    try:
        c.commit_task(w1["task"]["id"], "W1", w1["lease"]["fencing_token"])
    except LeaseError as exc:
        show("W1 stale commit rejected", {"error": str(exc)})

    show("W2 commits", c.commit_task(
        w2b["task"]["id"], "W2", w2b["lease"]["fencing_token"], {"note": "intro done"}
    ))

    w3 = c.claim_task("W3", project_id=p["project_id"])
    show("W3 claims next (api unblocked)", w3)

    show("metrics", c.metrics())
    show("events", c.events(limit=12))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
