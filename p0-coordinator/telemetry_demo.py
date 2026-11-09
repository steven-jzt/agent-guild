"""遥测演示：模拟 3 个 worker 完成任务并记录 runs，再输出估计器摘要。"""

from __future__ import annotations

from pathlib import Path

from coordinator import Coordinator
from estimate import print_summary, summarize
from plan import plan_project, print_plan

ROOT = Path(__file__).resolve().parent
DB = ROOT / ".tmp" / "telemetry.db"


def main() -> int:
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    c = Coordinator(DB)
    p = c.create_project("telemetry-demo")
    kinds = ["design", "code", "test", "code", "test", "design"]
    tasks = [
        c.create_task(p["project_id"], f"task-{i}", [f"doc#{i}"], kind=kinds[i])
        for i in range(6)
    ]

    # worker -> (cost, quality, duration, input_tokens, output_tokens, diff_lines)
    profiles = {
        "W1-cheap": (0.02, 0.74, 5.0, 900, 400, 30),
        "W2-balanced": (0.09, 0.87, 8.0, 1400, 700, 55),
        "W3-premium": (0.25, 0.95, 12.0, 2200, 1100, 80),
    }
    workers = list(profiles)
    for i, task in enumerate(tasks):
        worker = workers[i % len(workers)]
        cost, quality, duration, inp, out, diff = profiles[worker]
        r = c.claim_task(worker, task_id=task["id"])
        assert r["claimed"], r
        c.commit_task(
            task["id"], worker, r["lease"]["fencing_token"],
            run={
                "duration_s": duration,
                "input_tokens": inp,
                "output_tokens": out,
                "cost_usd": cost,
                "quality": quality,
                "diff_lines": diff,
                "files_changed": 1,
            },
        )

    summary = summarize(DB)
    print_summary(summary)
    print("\n=== metrics ===")
    print(c.metrics())

    # 用学到的先验给一个新项目做分配
    p2 = c.create_project("plan-demo")
    for i in range(6):
        c.create_task(p2["project_id"], f"new-{i}", [f"doc#n{i}"], kind=kinds[i])
    print("\n=== plan from learned priors ===")
    print_plan(plan_project(c, summary, p2["project_id"], quality_floor=0.8))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
