"""玩具级多 agent 任务分配 / 调度优化器。

目的：验证“在成本、质量、上下文、过度设计、工期多指标下求 Pareto 解”
这件事能不能落地，以及和贪心策略差多少。

不依赖任何第三方库；任务/agent 规模很小，直接枚举 3^6 种分配。
真实系统应换成 CP-SAT / MILP / 列表调度启发式。
"""

from __future__ import annotations

import itertools
import json

# ---------------- 模型参数 ----------------
TOKENS_PER_SIZE = 50_000       # 每单位任务规模消耗的 token
CONTEXT_PER_SIZE = 30_000      # 每单位任务规模需要的上下文 token
HOURS_PER_SIZE = 2.0           # 每单位任务规模的基础工时
HANDOFF_PENALTY = 0.02         # 相邻任务换 agent 的质量惩罚

TASKS = [
    {"id": "T1", "deps": [], "size": 1.0, "kind": "design"},
    {"id": "T2", "deps": ["T1"], "size": 2.0, "kind": "code"},
    {"id": "T3", "deps": ["T1"], "size": 2.0, "kind": "code"},
    {"id": "T4", "deps": ["T2"], "size": 1.5, "kind": "test"},
    {"id": "T5", "deps": ["T2", "T3"], "size": 0.8, "kind": "docs"},
    {"id": "T6", "deps": ["T4", "T5"], "size": 0.6, "kind": "review"},
]

AGENTS = [
    {
        "id": "A-cheap",
        "price": 0.4,       # $ / 1M tokens
        "speed": 1.6,       # 相对速度
        "context": 32_000,
        "verbosity": 0.7,   # 过度设计代理指标：同样任务产出更多代码/配置
        "quality": {"design": 0.72, "code": 0.70, "test": 0.68, "docs": 0.75, "review": 0.70},
    },
    {
        "id": "B-balanced",
        "price": 2.0,
        "speed": 1.0,
        "context": 128_000,
        "verbosity": 1.0,
        "quality": {"design": 0.88, "code": 0.87, "test": 0.85, "docs": 0.86, "review": 0.88},
    },
    {
        "id": "C-premium",
        "price": 8.0,
        "speed": 0.75,
        "context": 256_000,
        "verbosity": 1.5,
        "quality": {"design": 0.96, "code": 0.95, "test": 0.93, "docs": 0.94, "review": 0.96},
    },
]

TASK_BY_ID = {t["id"]: t for t in TASKS}
ORDER = [t["id"] for t in TASKS]  # 已是拓扑序


def schedule(assign: tuple[int, ...]) -> tuple[float, dict[str, float]] | None:
    """给定每个任务的 agent 下标，返回 (makespan, 每个任务完成时间)。"""
    free = {a["id"]: 0.0 for a in AGENTS}
    finish: dict[str, float] = {}
    for task_id, agent_idx in zip(ORDER, assign):
        task = TASK_BY_ID[task_id]
        agent = AGENTS[agent_idx]
        if task["size"] * CONTEXT_PER_SIZE > agent["context"]:
            return None
        ready = max((finish[d] for d in task["deps"]), default=0.0)
        start = max(ready, free[agent["id"]])
        end = start + task["size"] * HOURS_PER_SIZE / agent["speed"]
        finish[task_id] = end
        free[agent["id"]] = end
    return max(finish.values()), finish


def evaluate(assign: tuple[int, ...]) -> dict | None:
    sched = schedule(assign)
    if sched is None:
        return None
    makespan, _ = sched

    cost = 0.0
    quality = 0.0
    complexity = 0.0
    weight_sum = 0.0
    for task_id, agent_idx in zip(ORDER, assign):
        task = TASK_BY_ID[task_id]
        agent = AGENTS[agent_idx]
        cost += task["size"] * TOKENS_PER_SIZE / 1_000_000 * agent["price"]
        quality += task["size"] * agent["quality"][task["kind"]]
        weight_sum += task["size"]
        complexity += task["size"] * agent["verbosity"]

    quality /= weight_sum
    for task in TASKS:
        for dep in task["deps"]:
            if assign[ORDER.index(task["id"])] != assign[ORDER.index(dep)]:
                quality -= HANDOFF_PENALTY
    quality = max(0.0, min(1.0, quality))

    return {
        "assign": assign,
        "cost": round(cost, 3),
        "makespan": round(makespan, 2),
        "quality": round(quality, 3),
        "complexity": round(complexity, 2),
    }


def dominates(a: dict, b: dict) -> bool:
    """a 是否支配 b：成本/工期/复杂度不更高，质量不更低，且至少一项严格更好。"""
    no_worse = (
        a["cost"] <= b["cost"]
        and a["makespan"] <= b["makespan"]
        and a["complexity"] <= b["complexity"]
        and a["quality"] >= b["quality"]
    )
    strictly = (
        a["cost"] < b["cost"]
        or a["makespan"] < b["makespan"]
        or a["complexity"] < b["complexity"]
        or a["quality"] > b["quality"]
    )
    return no_worse and strictly


def pareto_front(solutions: list[dict]) -> list[dict]:
    front = []
    for sol in solutions:
        if not any(dominates(other, sol) for other in solutions if other is not sol):
            front.append(sol)
    return sorted(front, key=lambda s: (s["cost"], s["makespan"]))


def heuristic(name: str) -> tuple[int, ...] | None:
    assign = []
    for task in TASKS:
        candidates = [
            i for i, agent in enumerate(AGENTS)
            if task["size"] * CONTEXT_PER_SIZE <= agent["context"]
        ]
        if not candidates:
            return None
        if name == "cheapest":
            best = min(candidates, key=lambda i: AGENTS[i]["price"])
        elif name == "quality":
            best = max(candidates, key=lambda i: AGENTS[i]["quality"][task["kind"]])
        elif name == "fastest":
            best = max(candidates, key=lambda i: AGENTS[i]["speed"])
        elif name == "greedy":
            # 逐任务贪心：只看单任务得分，不考虑依赖、交接和全局工期
            best = max(
                candidates,
                key=lambda i: (
                    AGENTS[i]["quality"][task["kind"]]
                    - 0.02 * AGENTS[i]["price"]
                    - 0.03 * AGENTS[i]["verbosity"]
                ),
            )
        else:
            raise ValueError(name)
        assign.append(best)
    return tuple(assign)


def fmt_assign(assign: tuple[int, ...]) -> str:
    return " ".join(f"{t}:{AGENTS[i]['id'].split('-')[0]}" for t, i in zip(ORDER, assign))


def main() -> int:
    all_solutions = []
    for assign in itertools.product(range(len(AGENTS)), repeat=len(TASKS)):
        result = evaluate(assign)
        if result is not None:
            all_solutions.append(result)

    front = pareto_front(all_solutions)
    print(f"feasible assignments: {len(all_solutions)} / {len(AGENTS) ** len(TASKS)}")
    print(f"Pareto front size: {len(front)}\n")

    print("=== Pareto front (sample) ===")
    print(f"{'cost$':>7} {'hours':>6} {'qual':>5} {'cplx':>5}  assignment")
    for sol in front[:12]:
        print(
            f"{sol['cost']:7.3f} {sol['makespan']:6.2f} {sol['quality']:5.2f} "
            f"{sol['complexity']:5.2f}  {fmt_assign(sol['assign'])}"
        )

    print("\n=== heuristics vs Pareto ===")
    print(f"{'policy':10} {'cost$':>7} {'hours':>6} {'qual':>5} {'cplx':>5} {'dominated?':>11}")
    for name in ("cheapest", "quality", "fastest", "greedy"):
        assign = heuristic(name)
        sol = evaluate(assign) if assign else None
        if sol is None:
            print(f"{name:10} infeasible")
            continue
        dom = any(dominates(p, sol) for p in front)
        print(
            f"{name:10} {sol['cost']:7.3f} {sol['makespan']:6.2f} "
            f"{sol['quality']:5.2f} {sol['complexity']:5.2f} {str(dom):>11}  {fmt_assign(sol['assign'])}"
        )

    # 一个“均衡”策略：在 Pareto 前沿里按归一化权重选
    def norm(values, v):
        lo, hi = min(values), max(values)
        return 0.0 if hi == lo else (v - lo) / (hi - lo)

    costs = [s["cost"] for s in front]
    hours = [s["makespan"] for s in front]
    cplx = [s["complexity"] for s in front]
    quals = [s["quality"] for s in front]
    best = min(
        front,
        key=lambda s: (
            0.35 * norm(costs, s["cost"])
            + 0.25 * norm(hours, s["makespan"])
            + 0.20 * norm(cplx, s["complexity"])
            + 0.20 * (1 - norm(quals, s["quality"]))
        ),
    )
    print("\n=== balanced pick from Pareto (w=0.35/0.25/0.20/0.20) ===")
    print(json.dumps(
        {
            "assign": fmt_assign(best["assign"]),
            "cost": best["cost"],
            "makespan": best["makespan"],
            "quality": best["quality"],
            "complexity": best["complexity"],
        },
        ensure_ascii=False,
        indent=2,
    ))

    # 带业务约束的最省方案：质量 >= 0.80 且 15.5 小时内完成
    quality_floor, deadline = 0.80, 15.5
    constrained = [
        s for s in all_solutions
        if s["quality"] >= quality_floor and s["makespan"] <= deadline
    ]
    print(f"\n=== constrained optimum: quality >= {quality_floor}, hours <= {deadline} ===")
    if not constrained:
        print("infeasible")
    else:
        pick = min(constrained, key=lambda s: s["cost"])
        print(json.dumps(
            {
                "assign": fmt_assign(pick["assign"]),
                "cost": pick["cost"],
                "makespan": pick["makespan"],
                "quality": pick["quality"],
                "complexity": pick["complexity"],
                "feasible_solutions": len(constrained),
            },
            ensure_ascii=False,
            indent=2,
        ))
        print("\nheuristics under the same constraints:")
        for name in ("cheapest", "quality", "fastest", "greedy"):
            assign = heuristic(name)
            sol = evaluate(assign)
            ok = sol is not None and sol["quality"] >= quality_floor and sol["makespan"] <= deadline
            print(f"  {name:10} feasible={ok}  cost={sol['cost'] if sol else '-'}")

    print("\n=== complexity budget sensitivity (quality>=0.80, hours<=15.5) ===")
    for budget in (7.50, 7.55, 7.60, 8.00):
        pool = [
            s for s in all_solutions
            if s["quality"] >= quality_floor
            and s["makespan"] <= deadline
            and s["complexity"] <= budget
        ]
        if not pool:
            print(f"  cplx<={budget:.2f}: infeasible")
            continue
        pick = min(pool, key=lambda s: s["cost"])
        print(
            f"  cplx<={budget:.2f}: cost={pick['cost']:.3f} "
            f"hours={pick['makespan']:.2f} qual={pick['quality']:.3f} "
            f"cplx={pick['complexity']:.2f} {fmt_assign(pick['assign'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
