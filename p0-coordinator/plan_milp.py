"""P2 计划器：用 runs 先验 + MILP（PuLP/CBC）做带约束的调度。

约束：
- 每个任务一个 worker；
- 依赖 precedence；
- 一个 worker 同时只做一个任务；
- 写集重叠的任务不能并行（即使在不同 worker 上）；
- 每个任务的预测质量 >= quality_floor。

目标：
- mode=cost：先最小化成本，再在最优成本下最小化工期；
- mode=time：先最小化工期，再在最优工期下最小化成本；
- mode=balanced：加权和（w_cost * cost + w_time * makespan）。

用法：
    python plan_milp.py --db .tmp/telemetry.db --project-id P-xxxx
    python plan_milp.py --db .tmp/telemetry.db --project-id P-xxxx --pareto
"""

from __future__ import annotations

import argparse
import json

import pulp

from coordinator import Coordinator
from estimate import summarize
from plan import predict, worker_catalog


def solve_plan(
    coord: Coordinator,
    summary: dict,
    project_id: str,
    quality_floor: float = 0.8,
    mode: str = "cost",
    time_limit: int = 30,
    w_cost: float = 1.0,
    w_time: float = 0.02,
    deadline_s: float | None = None,
) -> dict:
    tasks = coord.list_tasks(project_id=project_id, status="ready")
    if not tasks:
        return {"feasible": False, "reason": "no_ready_tasks", "project_id": project_id}

    workers = worker_catalog(summary)
    if not workers:
        return {"feasible": False, "reason": "no_worker_estimates", "project_id": project_id}

    pred: dict[tuple[int, str], tuple[float, float, float]] = {}
    allowed: dict[int, list[str]] = {}
    for i, task in enumerate(tasks):
        allowed[i] = []
        for w in workers:
            c, q, d = predict(summary, w, task["kind"])
            pred[(i, w)] = (c, q, d)
            if q + 1e-9 >= quality_floor:
                allowed[i].append(w)
        if not allowed[i]:
            return {
                "feasible": False,
                "reason": f"no_worker_meets_quality:{task['id']}",
                "project_id": project_id,
            }

    n = len(tasks)
    idx = {task["id"]: i for i, task in enumerate(tasks)}
    horizon = sum(max(pred[(i, w)][2] for w in allowed[i]) for i in range(n)) + 1.0
    big_m = horizon + 1.0

    prob = pulp.LpProblem("plan", pulp.LpMinimize)
    x = {
        (i, w): prob.add_variable(
            f"x_{i}_{workers.index(w)}", lowBound=0, upBound=1, cat=pulp.LpBinary
        )
        for i in range(n)
        for w in allowed[i]
    }
    start = [prob.add_variable(f"s_{i}", lowBound=0) for i in range(n)]
    end = [prob.add_variable(f"e_{i}", lowBound=0) for i in range(n)]
    makespan = prob.add_variable("T", lowBound=0)

    for i in range(n):
        prob += pulp.lpSum(x[(i, w)] for w in allowed[i]) == 1
        prob += end[i] == start[i] + pulp.lpSum(
            pred[(i, w)][2] * x[(i, w)] for w in allowed[i]
        )
        prob += makespan >= end[i]

    if deadline_s is not None:
        prob += makespan <= float(deadline_s)

    # precedence（只处理项目内任务）
    for i, task in enumerate(tasks):
        for dep in task["deps"]:
            if dep in idx:
                prob += start[i] >= end[idx[dep]]

    # worker 容量：同一 worker 上的任务两两排序
    for i in range(n):
        for k in range(i + 1, n):
            for w in workers:
                if (i, w) not in x or (k, w) not in x:
                    continue
                y = prob.add_variable(
                    f"y_{i}_{k}_{workers.index(w)}", lowBound=0, upBound=1, cat=pulp.LpBinary
                )
                prob += start[k] >= end[i] - big_m * (3 - x[(i, w)] - x[(k, w)] - y)
                prob += start[i] >= end[k] - big_m * (2 - x[(i, w)] - x[(k, w)] + y)

    # 写集互斥：重叠任务不能并行（跨 worker 也生效）
    for i in range(n):
        for k in range(i + 1, n):
            if set(tasks[i]["write_set"]) & set(tasks[k]["write_set"]):
                z = prob.add_variable(f"z_{i}_{k}", lowBound=0, upBound=1, cat=pulp.LpBinary)
                prob += start[k] >= end[i] - big_m * (1 - z)
                prob += start[i] >= end[k] - big_m * z

    total_cost = pulp.lpSum(pred[(i, w)][0] * x[(i, w)] for i in range(n) for w in allowed[i])
    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit)

    def optimize(objective) -> bool:
        prob.setObjective(objective)
        status = prob.solve(solver)
        return pulp.LpStatus[status] == "Optimal"

    if mode == "time":
        if not optimize(makespan):
            return {"feasible": False, "reason": "solver_infeasible", "project_id": project_id}
        best_t = pulp.value(makespan)
        prob += makespan <= best_t + 1e-6
        if not optimize(total_cost):
            return {"feasible": False, "reason": "solver_infeasible", "project_id": project_id}
    elif mode == "balanced":
        if not optimize(w_cost * total_cost + w_time * makespan):
            return {"feasible": False, "reason": "solver_infeasible", "project_id": project_id}
    else:  # cost
        if not optimize(total_cost):
            return {"feasible": False, "reason": "solver_infeasible", "project_id": project_id}
        best_cost = pulp.value(total_cost)
        prob += total_cost <= best_cost + 1e-6
        if not optimize(makespan):
            return {"feasible": False, "reason": "solver_infeasible", "project_id": project_id}

    assignment = []
    min_q = 1.0
    for i, task in enumerate(tasks):
        chosen = next(w for w in allowed[i] if pulp.value(x[(i, w)]) > 0.5)
        c, q, _ = pred[(i, chosen)]
        min_q = min(min_q, q)
        assignment.append(
            {
                "task_id": task["id"],
                "title": task["title"],
                "kind": task["kind"],
                "worker": chosen,
                "start_s": round(pulp.value(start[i]), 2),
                "end_s": round(pulp.value(end[i]), 2),
                "pred_cost_usd": round(c, 4),
                "pred_quality": round(q, 3),
            }
        )
    return {
        "feasible": True,
        "project_id": project_id,
        "mode": mode,
        "quality_floor": quality_floor,
        "tasks": n,
        "workers": workers,
        "assignment": sorted(assignment, key=lambda a: a["start_s"]),
        "pred_total_cost_usd": round(pulp.value(total_cost), 4),
        "pred_makespan_s": round(pulp.value(makespan), 2),
        "pred_min_quality": round(min_q, 3),
    }


def pareto_front(
    coord: Coordinator,
    summary: dict,
    project_id: str,
    quality_floor: float = 0.8,
    points: int = 8,
    time_limit: int = 30,
) -> list[dict]:
    fastest = solve_plan(
        coord, summary, project_id, quality_floor, mode="time", time_limit=time_limit
    )
    cheapest = solve_plan(
        coord, summary, project_id, quality_floor, mode="cost", time_limit=time_limit
    )
    if not fastest.get("feasible") or not cheapest.get("feasible"):
        return []

    t_min = float(fastest["pred_makespan_s"])
    t_max = float(cheapest["pred_makespan_s"])
    if t_max < t_min:
        t_min, t_max = t_max, t_min
    if abs(t_max - t_min) < 1e-6 or points <= 1:
        deadlines = [t_min]
    else:
        deadlines = [
            t_min + (t_max - t_min) * i / (points - 1) for i in range(points)
        ]

    results = []
    for deadline in deadlines:
        plan = solve_plan(
            coord, summary, project_id, quality_floor,
            mode="cost", time_limit=time_limit, deadline_s=deadline + 1e-6,
        )
        if plan.get("feasible"):
            results.append(plan)

    unique = []
    seen = set()
    for plan in results:
        key = (
            round(plan["pred_total_cost_usd"], 4),
            round(plan["pred_makespan_s"], 2),
            round(plan["pred_min_quality"], 3),
        )
        if key not in seen:
            seen.add(key)
            unique.append(plan)

    def dominated(a: dict, b: dict) -> bool:
        no_worse = (
            a["pred_total_cost_usd"] <= b["pred_total_cost_usd"] + 1e-9
            and a["pred_makespan_s"] <= b["pred_makespan_s"] + 1e-6
            and a["pred_min_quality"] >= b["pred_min_quality"] - 1e-9
        )
        strictly = (
            a["pred_total_cost_usd"] < b["pred_total_cost_usd"] - 1e-9
            or a["pred_makespan_s"] < b["pred_makespan_s"] - 1e-6
            or a["pred_min_quality"] > b["pred_min_quality"] + 1e-9
        )
        return no_worse and strictly

    front = [p for p in unique if not any(dominated(q, p) for q in unique if q is not p)]
    return sorted(front, key=lambda p: p["pred_makespan_s"])


def print_front(front: list[dict]) -> None:
    if not front:
        print("empty Pareto front")
        return
    print(f"{'makespan':>9} {'cost$':>8} {'min_q':>6} {'feasible':>8}")
    for p in front:
        print(
            f"{p['pred_makespan_s']:9.1f} {p['pred_total_cost_usd']:8.3f} "
            f"{p['pred_min_quality']:6.3f} {p.get('mode', '-'):>8}"
        )


def pareto_front_3d(
    coord: Coordinator,
    summary: dict,
    project_id: str,
    floors: tuple[float, ...] = (0.70, 0.80, 0.90),
    points: int = 5,
    time_limit: int = 30,
) -> list[dict]:
    candidates: list[dict] = []
    for floor in floors:
        candidates.extend(
            pareto_front(coord, summary, project_id, quality_floor=floor, points=points,
                         time_limit=time_limit)
        )

    unique = []
    seen = set()
    for plan in candidates:
        key = (
            round(plan["pred_total_cost_usd"], 4),
            round(plan["pred_makespan_s"], 2),
            round(plan["pred_min_quality"], 3),
        )
        if key not in seen:
            seen.add(key)
            unique.append(plan)

    def dominated(a: dict, b: dict) -> bool:
        no_worse = (
            a["pred_total_cost_usd"] <= b["pred_total_cost_usd"] + 1e-9
            and a["pred_makespan_s"] <= b["pred_makespan_s"] + 1e-6
            and a["pred_min_quality"] >= b["pred_min_quality"] - 1e-9
        )
        strictly = (
            a["pred_total_cost_usd"] < b["pred_total_cost_usd"] - 1e-9
            or a["pred_makespan_s"] < b["pred_makespan_s"] - 1e-6
            or a["pred_min_quality"] > b["pred_min_quality"] + 1e-9
        )
        return no_worse and strictly

    front = [p for p in unique if not any(dominated(q, p) for q in unique if q is not p)]
    return sorted(front, key=lambda p: (p["pred_makespan_s"], p["pred_total_cost_usd"]))


def print_front_3d(front: list[dict]) -> None:
    if not front:
        print("empty 3D Pareto front")
        return
    print(f"{'makespan':>9} {'cost$':>8} {'min_q':>6}")
    for p in front:
        print(
            f"{p['pred_makespan_s']:9.1f} {p['pred_total_cost_usd']:8.3f} "
            f"{p['pred_min_quality']:6.3f}"
        )


def print_plan(plan: dict) -> None:
    if not plan.get("feasible"):
        print(f"infeasible: {plan.get('reason')}")
        return
    print(
        f"project={plan['project_id']} mode={plan['mode']} floor={plan['quality_floor']} "
        f"tasks={plan['tasks']}"
    )
    print(f"{'task':12} {'kind':7} {'worker':14} {'start':>6} {'end':>6} {'cost':>8} {'q':>5}")
    for a in plan["assignment"]:
        print(
            f"{a['task_id']:12} {a['kind']:7} {a['worker']:14} "
            f"{a['start_s']:6.1f} {a['end_s']:6.1f} {a['pred_cost_usd']:8.4f} {a['pred_quality']:5.3f}"
        )
    print(
        f"  total: cost=${plan['pred_total_cost_usd']:.4f} "
        f"makespan={plan['pred_makespan_s']:.1f}s min_q={plan['pred_min_quality']:.3f}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/coordinator.db")
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--quality-floor", type=float, default=0.8)
    ap.add_argument("--mode", choices=["cost", "time", "balanced"], default="cost")
    ap.add_argument("--time-limit", type=int, default=30)
    ap.add_argument("--pareto", action="store_true")
    ap.add_argument("--front", action="store_true")
    ap.add_argument("--front3d", action="store_true")
    ap.add_argument("--floors", default="0.70,0.80,0.90")
    ap.add_argument("--points", type=int, default=8)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    coord = Coordinator(args.db)
    summary = summarize(args.db)

    if args.front3d:
        floors = tuple(float(x) for x in args.floors.split(",") if x.strip())
        front = pareto_front_3d(
            coord, summary, args.project_id, floors, args.points, args.time_limit
        )
        if args.json:
            print(json.dumps(front, ensure_ascii=False, indent=2))
        else:
            print_front_3d(front)
        return 0

    if args.front:
        front = pareto_front(
            coord, summary, args.project_id, args.quality_floor, args.points, args.time_limit
        )
        if args.json:
            print(json.dumps(front, ensure_ascii=False, indent=2))
        else:
            print_front(front)
        return 0

    if args.pareto:
        rows = []
        for floor in (0.70, 0.80, 0.90):
            for mode in ("cost", "time"):
                plan = solve_plan(
                    coord, summary, args.project_id, floor, mode, args.time_limit
                )
                rows.append(
                    {
                        "floor": floor,
                        "mode": mode,
                        "feasible": plan.get("feasible"),
                        "cost": plan.get("pred_total_cost_usd"),
                        "makespan": plan.get("pred_makespan_s"),
                        "min_q": plan.get("pred_min_quality"),
                    }
                )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    plan = solve_plan(
        coord, summary, args.project_id, args.quality_floor, args.mode, args.time_limit
    )
    if args.apply:
        if not plan.get("feasible"):
            print("cannot apply infeasible plan:", plan.get("reason"))
            return 1
        plan["applied"] = coord.apply_plan(plan)
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print_plan(plan)
        if plan.get("applied"):
            print(f"  applied: {plan['applied']['plan_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
