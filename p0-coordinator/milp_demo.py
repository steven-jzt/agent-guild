"""MILP 计划器演示：学习先验 + 依赖 + worker 容量 + 写集互斥。"""

from __future__ import annotations

from pathlib import Path

from coordinator import Coordinator
from estimate import summarize
from plan_milp import (
    pareto_front,
    pareto_front_3d,
    print_front,
    print_front_3d,
    print_plan,
    solve_plan,
)

ROOT = Path(__file__).resolve().parent
DB = ROOT / ".tmp" / "milp-demo.db"


def main() -> int:
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    c = Coordinator(DB)

    seed = c.create_project("seed")
    seed_tasks = {
        kind: c.create_task(seed["project_id"], f"seed-{kind}", [f"s#{kind}"], kind=kind)
        for kind in ("design", "code", "test")
    }
    profiles = {
        "W1-cheap": (0.02, 0.70, 5.0),
        "W2-balanced": (0.10, 0.87, 8.0),
        "W3-premium": (0.25, 0.95, 4.0),
    }
    for worker, (cost, quality, duration) in profiles.items():
        for kind, task in seed_tasks.items():
            c.record_run(task["id"], worker, cost_usd=cost, quality=quality, duration_s=duration)

    p = c.create_project("milp-plan")
    t1 = c.create_task(p["project_id"], "design", ["a"], kind="design")
    t2 = c.create_task(p["project_id"], "code-1", ["b"], deps=[t1["id"]], kind="code")
    t3 = c.create_task(p["project_id"], "code-2", ["b"], deps=[t1["id"]], kind="code")
    t4 = c.create_task(
        p["project_id"], "test", ["c"], deps=[t2["id"], t3["id"]], kind="test"
    )

    summary = summarize(DB)
    for mode in ("cost", "time"):
        print(f"\n### mode={mode} ###")
        print_plan(solve_plan(c, summary, p["project_id"], quality_floor=0.8, mode=mode))

    print("\n### Pareto（quality floor × mode）###")
    for floor in (0.70, 0.80, 0.90):
        for mode in ("cost", "time"):
            plan = solve_plan(c, summary, p["project_id"], quality_floor=floor, mode=mode)
            if plan.get("feasible"):
                print(
                    f"floor={floor:.2f} mode={mode:4} cost=${plan['pred_total_cost_usd']:.3f} "
                    f"makespan={plan['pred_makespan_s']:.1f}s min_q={plan['pred_min_quality']:.3f}"
                )
            else:
                print(f"floor={floor:.2f} mode={mode:4} infeasible: {plan.get('reason')}")

    print("\n### Pareto front（deadline sweep）###")
    print_front(pareto_front(c, summary, p["project_id"], quality_floor=0.8, points=5))

    print("\n### 3D Pareto front（floor × deadline）###")
    print_front_3d(
        pareto_front_3d(c, summary, p["project_id"], floors=(0.70, 0.80, 0.90), points=3)
    )

    print("\n### 回写计划 + 按计划领取 ###")
    plan = solve_plan(c, summary, p["project_id"], quality_floor=0.8, mode="cost")
    applied = c.apply_plan(plan)
    print("applied:", applied)
    stored = c.get_plan(p["project_id"])
    print("stored plan:", stored["plan_id"], "items:", len(stored["plan"]["assignment"]))
    first_task = plan["assignment"][0]["task_id"]
    blocked = c.claim_task("W-other", task_id=first_task)
    print("other worker blocked:", blocked["claimed"], blocked["reason"])
    first_worker = plan["assignment"][0]["worker"]
    claimed = c.claim_planned(first_worker, project_id=p["project_id"])
    print(f"claim_planned({first_worker}) ->", claimed["claimed"], claimed.get("task", {}).get("id"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
