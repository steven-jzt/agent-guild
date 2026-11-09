"""OR routing: 用历史 runs + 默认画像，为任务选 agent。

这是 Phase 3 的运行时入口，不替代离线 MILP/Pareto；先做一个可解释、
可测试的在线推荐器。
"""

from __future__ import annotations

from statistics import mean

DEFAULTS = {
    "dsh": {"seconds": 18.0, "cost_usd": 0.0010, "success": 1.00},
    "codex-deepseek": {"seconds": 32.0, "cost_usd": 0.0060, "success": 0.95},
    "codex-zhipu": {"seconds": 28.0, "cost_usd": 0.0040, "success": 0.90},
    "codex-appserver": {"seconds": 45.0, "cost_usd": 0.0080, "success": 0.95},
}
STRATEGIES = ("cost", "time", "quality")


def _token_cost(run: dict) -> float:
    if float(run.get("cost_usd") or 0) > 0:
        return float(run["cost_usd"])
    return float(run.get("input_tokens") or 0) * 1e-6 + float(run.get("output_tokens") or 0) * 4e-6


def _profile(agent: str, runs: list[dict]) -> dict:
    base = DEFAULTS[agent]
    rows = [r for r in runs if r.get("worker") == agent]
    if not rows:
        return {
            "agent": agent, "runs": 0, "success_rate": base["success"],
            "avg_seconds": base["seconds"], "estimated_cost_usd": base["cost_usd"],
            "source": "default",
        }
    ok = [r for r in rows if r.get("status") == "ok"]
    return {
        "agent": agent,
        "runs": len(rows),
        "success_rate": len(ok) / len(rows),
        "avg_seconds": round(mean(float(r.get("duration_s") or 0) for r in rows), 1),
        "estimated_cost_usd": round(mean(_token_cost(r) for r in rows), 6),
        "source": "history",
    }


def rank_agents(
    runs: list[dict],
    project_id: str,
    budget_usd: float | None = None,
    strategy: str = "quality",
    agents: list[str] | None = None,
) -> dict:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy}")

    agent_names = list(agents) if agents else list(DEFAULTS)
    unknown = [a for a in agent_names if a not in DEFAULTS]
    if unknown:
        raise ValueError(f"unknown agents: {unknown}")
    scoped = [r for r in runs if r.get("project_id") in (None, project_id)]
    candidates = []
    for agent in agent_names:
        item = _profile(agent, scoped)
        item["feasible"] = budget_usd is None or item["estimated_cost_usd"] <= budget_usd
        if budget_usd is not None and not item["feasible"]:
            item["reason"] = "超出剩余预算"
        elif item["source"] == "history":
            item["reason"] = "基于历史 runs"
        else:
            item["reason"] = "无历史，使用默认画像"
        candidates.append(item)

    if strategy == "cost":
        candidates.sort(key=lambda x: (not x["feasible"], x["estimated_cost_usd"], -x["success_rate"]))
    elif strategy == "time":
        candidates.sort(key=lambda x: (not x["feasible"], x["avg_seconds"], -x["success_rate"]))
    else:
        candidates.sort(key=lambda x: (not x["feasible"], -x["success_rate"], x["estimated_cost_usd"]))

    feasible = [c for c in candidates if c["feasible"]]
    recommended = feasible[0] if feasible else candidates[0]
    if not feasible and budget_usd is not None:
        recommended["reason"] = "全部超出预算；返回最便宜方案"
    return {
        "project_id": project_id,
        "strategy": strategy,
        "budget_usd": budget_usd,
        "recommended": recommended,
        "candidates": candidates,
    }


def recommend_agent(
    coord, task_id: str, strategy: str = "quality", required_tags: list[str] | None = None,
) -> dict:
    task = coord.get_task(task_id)
    budget = coord.project_budget(task["project_id"])
    runs = coord.list_runs(limit=1000)

    required_tags = [t for t in (required_tags or []) if t]
    agents = None
    if required_tags:
        capabilities = coord.list_agent_capabilities()
        tags_by_agent: dict[str, set[str]] = {}
        for cap in capabilities:
            tags_by_agent.setdefault(cap["agent_id"], set()).add(
                f"{cap['dimension']}:{cap['value']}"
            )
            tags_by_agent[cap["agent_id"]].add(cap["value"])
        agents = [
            agent for agent, tags in tags_by_agent.items()
            if all(tag in tags for tag in required_tags)
        ]
        if not agents:
            raise ValueError(
                "no agent has required tags: " + ", ".join(required_tags)
            )

    result = rank_agents(
        runs, task["project_id"],
        budget_usd=budget["remaining_usd"], strategy=strategy, agents=agents,
    )
    result["task_id"] = task_id
    result["task_title"] = task["title"]
    result["project_budget"] = budget
    result["required_tags"] = required_tags
    return result


def assign_batch(
    tasks: list[dict],
    runs: list[dict],
    project_id: str,
    budget_usd: float | None = None,
    strategy: str = "quality",
    max_parallel: int = 4,
) -> dict:
    """为一批 ready 任务分配不同 agent；一个 agent 同时只接一个任务。"""
    max_parallel = max(1, min(int(max_parallel), len(DEFAULTS)))
    available = list(DEFAULTS)[:max_parallel]
    remaining = budget_usd
    assignments = []
    unassigned = []

    ordered = sorted(tasks, key=lambda t: (-int(t.get("priority") or 0), t.get("created_at") or ""))
    for task in ordered:
        ranking = rank_agents(runs, project_id, budget_usd=remaining, strategy=strategy)
        picked = None
        for candidate in ranking["candidates"]:
            if candidate["agent"] in available and candidate["feasible"]:
                picked = candidate
                break
        if picked is None:
            reason = "没有空闲 agent" if not available else "剩余预算不足"
            unassigned.append({
                "task_id": task["id"], "title": task.get("title", ""),
                "reason": reason,
            })
            continue
        assignments.append({
            "task_id": task["id"],
            "title": task.get("title", ""),
            "agent": picked["agent"],
            "estimated_cost_usd": picked["estimated_cost_usd"],
            "estimated_seconds": picked["avg_seconds"],
            "reason": picked["reason"],
        })
        available.remove(picked["agent"])
        if remaining is not None:
            remaining = round(max(0.0, remaining - picked["estimated_cost_usd"]), 6)

    return {
        "project_id": project_id,
        "strategy": strategy,
        "max_parallel": max_parallel,
        "budget_usd": budget_usd,
        "remaining_budget_usd": remaining,
        "assignments": assignments,
        "unassigned": unassigned,
    }


def recommend_batch(
    coord, project_id: str, strategy: str = "quality", max_parallel: int = 4,
) -> dict:
    tasks = [
        t for t in coord.list_tasks(project_id=project_id)
        if t.get("lifecycle") in ("ready", "assigned", "needs_fix")
    ]
    budget = coord.project_budget(project_id)
    runs = coord.list_runs(limit=1000)
    result = assign_batch(
        tasks, runs, project_id,
        budget_usd=budget["remaining_usd"], strategy=strategy,
        max_parallel=max_parallel,
    )
    result["candidate_tasks"] = len(tasks)
    return result
