"""P1 计划器：用 runs 学的先验做 predict-then-optimize。

输入：Coordinator 里的 ready 任务 + estimate.summarize 的先验。
输出：满足质量下限的最省分配（小规模枚举；大规模退化为贪心）。
"""

from __future__ import annotations

import argparse
import itertools
import json
from typing import Any

from coordinator import Coordinator
from estimate import summarize


def _pred(stats: dict | None) -> tuple[float, float, float]:
    if not stats:
        return 0.0, 0.0, 0.0
    cost = float(stats.get("avg_cost_usd") or 0.0)
    quality = float(stats.get("avg_quality") if stats.get("avg_quality") is not None else 0.0)
    duration = float(stats.get("avg_duration_s") or 0.0)
    return cost, quality, duration


def worker_catalog(summary: dict) -> list[str]:
    return [
        w for w, s in summary["by_worker"].items()
        if s.get("avg_quality") is not None
    ]


def predict(summary: dict, worker: str, kind: str) -> tuple[float, float, float]:
    specific = summary["by_worker_kind"].get(f"{worker}::{kind}")
    if specific and specific.get("avg_quality") is not None:
        return _pred(specific)
    generic = summary["by_worker"].get(worker)
    if generic and generic.get("avg_quality") is not None:
        return _pred(generic)
    return _pred(summary["overall"])


def plan_project(
    coord: Coordinator,
    summary: dict,
    project_id: str,
    quality_floor: float = 0.8,
) -> dict:
    tasks = coord.list_tasks(project_id=project_id, status="ready")
    if not tasks:
        return {"project_id": project_id, "tasks": 0, "assignment": [], "reason": "no_ready_tasks"}
    workers = worker_catalog(summary)
    if not workers:
        return {"project_id": project_id, "tasks": len(tasks), "assignment": [], "reason": "no_worker_estimates"}

    combos = len(workers) ** len(tasks)
    if combos > 200_000:
        assignment = []
        for task in tasks:
            best = min(
                workers,
                key=lambda w: predict(summary, w, task["kind"])[0],
            )
            assignment.append({"task_id": task["id"], "worker": best})
        return {
            "project_id": project_id,
            "tasks": len(tasks),
            "assignment": assignment,
            "reason": "greedy_large_space",
        }

    best = None
    feasible_count = 0
    for combo in itertools.product(workers, repeat=len(tasks)):
        cost = 0.0
        duration = 0.0
        min_q = 1.0
        for task, worker in zip(tasks, combo):
            c, q, d = predict(summary, worker, task["kind"])
            cost += c
            duration += d
            min_q = min(min_q, q)
        if min_q + 1e-9 < quality_floor:
            continue
        feasible_count += 1
        score = (cost, duration)
        if best is None or score < best[0]:
            best = (score, combo, cost, duration, min_q)

    if best is None:
        return {
            "project_id": project_id,
            "tasks": len(tasks),
            "assignment": [],
            "reason": "no_feasible_assignment",
            "quality_floor": quality_floor,
        }

    _, combo, cost, duration, min_q = best
    assignment = [
        {
            "task_id": task["id"],
            "title": task["title"],
            "kind": task["kind"],
            "worker": worker,
            "pred_cost_usd": round(predict(summary, worker, task["kind"])[0], 4),
            "pred_quality": round(predict(summary, worker, task["kind"])[1], 3),
        }
        for task, worker in zip(tasks, combo)
    ]
    return {
        "project_id": project_id,
        "tasks": len(tasks),
        "workers": workers,
        "quality_floor": quality_floor,
        "feasible_assignments": feasible_count,
        "assignment": assignment,
        "pred_total_cost_usd": round(cost, 4),
        "pred_min_quality": round(min_q, 3),
        "pred_total_duration_s": round(duration, 1),
    }


def print_plan(plan: dict) -> None:
    print(f"project={plan['project_id']} tasks={plan['tasks']} floor={plan.get('quality_floor')}")
    if not plan.get("assignment"):
        print("no feasible plan:", plan.get("reason"))
        return
    for item in plan["assignment"]:
        print(
            f"  {item['task_id']} ({item['kind']:6}) -> {item['worker']:14} "
            f"cost=${item['pred_cost_usd']:.4f} q={item['pred_quality']:.3f}  {item['title']}"
        )
    print(
        f"  total: cost=${plan['pred_total_cost_usd']:.4f} "
        f"min_q={plan['pred_min_quality']:.3f} duration={plan['pred_total_duration_s']:.1f}s "
        f"feasible={plan['feasible_assignments']}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/coordinator.db")
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--quality-floor", type=float, default=0.8)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    coord = Coordinator(args.db)
    summary = summarize(args.db)
    plan = plan_project(coord, summary, args.project_id, args.quality_floor)
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print_plan(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
