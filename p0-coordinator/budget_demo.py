"""预算治理演示：项目预算 $0.10，第一单花 $0.12，第二单被拒。"""

from __future__ import annotations

import json
from pathlib import Path

from coordinator import Coordinator

ROOT = Path(__file__).resolve().parent
DB = ROOT / ".tmp" / "budget.db"


def show(label: str, data) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    c = Coordinator(DB)
    p = c.create_project("budget-demo", budget_usd=0.10)
    t1 = c.create_task(p["project_id"], "t1", ["a"])
    t2 = c.create_task(p["project_id"], "t2", ["b"])
    show("initial budget", c.project_budget(p["project_id"]))

    r = c.claim_task("W1", task_id=t1["id"])
    c.commit_task(
        t1["id"], "W1", r["lease"]["fencing_token"],
        run={"cost_usd": 0.12, "quality": 0.9, "duration_s": 5},
    )
    show("after $0.12 run", c.project_budget(p["project_id"]))

    blocked = c.claim_task("W2", task_id=t2["id"])
    show("claim next task -> blocked", blocked)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
