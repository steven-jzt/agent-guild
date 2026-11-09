"""机制仿真：三种组织 archetype × 三类任务互依性。

目的：验证“没有单一组织在所有任务上占优；按任务特征自适应选组织，
能接近 oracle、优于最佳固定组织”这个假设。

这是**风格化模型**，不是实证：参数是人为设定的，用来验证机制逻辑，
不能当成真实 LLM agent 的性能数据。真实结论要用 trace 校准。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

DT = 0.05
BASE_HOURS_PER_SIZE = 2.0
TOKENS_PER_SIZE = 50_000
HANDOFF_HOURS = 0.15
HANDOFF_TOKENS = 3_000

ORGS = ["flat", "hierarchy", "pipeline"]
TASK_TYPES = ["pooled", "sequential", "reciprocal"]
COUPLING_SCALE = 1.0


@dataclass
class Agent:
    id: str
    speed: float
    price: float
    skill: dict[str, float]


@dataclass
class Task:
    id: str
    kind: str
    size: float
    deps: list[str]
    write: set[int]
    coupling: float
    priority: float = 0.0


AGENTS = {
    "W1": Agent("W1", 1.5, 0.4, {"design": 0.70, "code": 0.75, "test": 0.65}),
    "W2": Agent("W2", 1.0, 2.0, {"design": 0.85, "code": 0.88, "test": 0.84}),
    "W3": Agent("W3", 0.7, 8.0, {"design": 0.95, "code": 0.96, "test": 0.94}),
}


def make_episode(task_type: str, rng: random.Random) -> list[Task]:
    tasks: list[Task] = []
    if task_type == "pooled":
        for i in range(9):
            tasks.append(
                Task(
                    id=f"T{i}",
                    kind=rng.choice(["design", "code", "test"]),
                    size=rng.uniform(0.8, 1.8),
                    deps=[],
                    write=set(rng.sample(range(8), rng.randint(1, 2))),
                    coupling=0.15,
                )
            )
    elif task_type == "sequential":
        for i in range(9):
            tasks.append(
                Task(
                    id=f"T{i}",
                    kind=["design", "code", "test"][i % 3],
                    size=rng.uniform(0.8, 1.6),
                    deps=[f"T{i-1}"] if i > 0 else [],
                    write={i % 4, (i + 1) % 4},
                    coupling=0.6,
                )
            )
    else:  # reciprocal：3 组，每组 3 个任务强耦合
        for g in range(3):
            base = g * 3
            shared = {g, g + 3}
            for j, deps in enumerate(([], [f"T{base}"], [f"T{base}", f"T{base+1}"])):
                tasks.append(
                    Task(
                        id=f"T{base+j}",
                        kind=["design", "code", "test"][j],
                        size=rng.uniform(1.0, 1.7),
                        deps=deps,
                        write=set(shared),
                        coupling=0.8,
                    )
                )
    # 关键路径优先级
    by_id = {t.id: t for t in tasks}
    memo: dict[str, float] = {}

    def cp(tid: str) -> float:
        if tid in memo:
            return memo[tid]
        t = by_id[tid]
        memo[tid] = t.size + max((cp(d) for d in t.deps), default=0.0)
        return memo[tid]

    for t in tasks:
        t.priority = cp(t.id)
        t.coupling *= COUPLING_SCALE
    return tasks


@dataclass
class Result:
    quality: float
    cost: float
    hours: float
    conflicts: int
    tokens: int


def _weighted_quality(tasks: list[Task], qualities: dict[str, float]) -> float:
    total = sum(t.size for t in tasks)
    return sum(t.size * qualities[t.id] for t in tasks) / total


def simulate_flat(tasks: list[Task], rng: random.Random) -> Result:
    agents = list(AGENTS.values())
    busy = {a.id: 0.0 for a in agents}
    started: dict[str, float] = {}
    ends: dict[str, float] = {}
    agent_of: dict[str, str] = {}
    qualities: dict[str, float] = {}
    tokens = 0
    cost = 0.0
    conflicts = 0
    t = 0.0

    while len(ends) < len(tasks):
        done = {tid for tid, end in ends.items() if end <= t}
        for a in agents:
            if busy[a.id] <= t:
                ready = [
                    task for task in tasks
                    if task.id not in started
                    and all(d in done for d in task.deps)
                ]
                if not ready:
                    continue
                task = max(ready, key=lambda x: x.priority)
                started[task.id] = t
                agent_of[task.id] = a.id
                handoffs = sum(1 for d in task.deps if agent_of.get(d) != a.id)
                duration = task.size * BASE_HOURS_PER_SIZE / a.speed + handoffs * HANDOFF_HOURS
                # 冲突：与正在运行的任务写集重叠
                running = [
                    other for other in tasks
                    if other.id in started and other.id not in done and other.id != task.id
                ]
                conflict = any(
                    other.write & task.write and agent_of.get(other.id) != a.id
                    for other in running
                )
                penalty = 0.0
                if conflict and rng.random() < task.coupling * 0.35:
                    duration *= 1.3
                    penalty = 0.06
                    conflicts += 1
                qualities[task.id] = max(
                    0.0,
                    a.skill[task.kind]
                    - handoffs * task.coupling * 0.04
                    - penalty,
                )
                task_tokens = int(task.size * TOKENS_PER_SIZE + handoffs * HANDOFF_TOKENS)
                tokens += task_tokens
                cost += task_tokens / 1_000_000 * a.price
                ends[task.id] = t + duration
                busy[a.id] = ends[task.id]
        t += DT
        if t > 500:
            raise RuntimeError("flat simulation did not converge")

    return Result(
        quality=_weighted_quality(tasks, qualities),
        cost=cost,
        hours=max(ends.values()),
        conflicts=conflicts,
        tokens=tokens,
    )


def simulate_hierarchy(tasks: list[Task], rng: random.Random) -> Result:
    workers = [AGENTS["W1"], AGENTS["W3"]]
    supervisor = AGENTS["W2"]
    busy = {a.id: 0.0 for a in workers}
    supervisor_free = 0.0
    started: dict[str, float] = {}
    ends: dict[str, float] = {}
    agent_of: dict[str, str] = {}
    qualities: dict[str, float] = {}
    review_backlog: list[str] = []
    reviewed: set[str] = set()
    tokens = 0
    cost = 0.0
    t = 0.0
    task_by_id = {task.id: task for task in tasks}

    while len(ends) < len(tasks):
        done = {tid for tid, end in ends.items() if end <= t}
        for tid, end in ends.items():
            if end <= t and tid not in reviewed:
                reviewed.add(tid)
                review_backlog.append(tid)
        # 主管先处理评审，再分配
        if review_backlog and supervisor_free <= t:
            review_backlog.pop(0)
            supervisor_free = t + 0.2
        if supervisor_free <= t:
            running = [tid for tid in started if tid not in done]
            idle = [a for a in workers if busy[a.id] <= t]
            if len(running) < 2 and idle:
                candidates = [
                    task for task in tasks
                    if task.id not in started and all(d in done for d in task.deps)
                    and not any(task_by_id[x].write & task.write for x in running)
                ]
                if candidates:
                    task = max(candidates, key=lambda x: x.priority)
                    worker = max(idle, key=lambda a: a.skill[task.kind])
                    started[task.id] = t
                    agent_of[task.id] = worker.id
                    handoffs = sum(1 for d in task.deps if agent_of.get(d) != worker.id)
                    duration = task.size * BASE_HOURS_PER_SIZE / worker.speed + handoffs * 0.10
                    qualities[task.id] = min(
                        1.0, worker.skill[task.kind] + 0.03 - handoffs * task.coupling * 0.02
                    )
                    task_tokens = int(task.size * TOKENS_PER_SIZE + 5_000)
                    tokens += task_tokens + 3_000
                    cost += task_tokens / 1_000_000 * worker.price + 3_000 / 1_000_000 * supervisor.price
                    ends[task.id] = t + duration
                    busy[worker.id] = ends[task.id]
                    supervisor_free = t + 0.05
        t += DT
        if t > 500:
            raise RuntimeError("hierarchy simulation did not converge")

    return Result(
        quality=_weighted_quality(tasks, qualities),
        cost=cost,
        hours=max(ends.values()),
        conflicts=0,
        tokens=tokens,
    )


def simulate_pipeline(tasks: list[Task], rng: random.Random) -> Result:
    stages = [("design", AGENTS["W1"]), ("code", AGENTS["W2"]), ("test", AGENTS["W3"])]
    queues: list[list[Task]] = [[], [], []]
    busy = [0.0, 0.0, 0.0]
    stage_of: dict[str, int] = {}
    stage_end: dict[str, float] = {}
    started: set[str] = set()
    finished: set[str] = set()
    max_end = 0.0
    t = 0.0
    task_by_id = {task.id: task for task in tasks}

    while len(finished) < len(tasks):
        # 完成当前阶段：推进到下一阶段，或标记完成
        for tid, end in list(stage_end.items()):
            if end <= t:
                idx = stage_of[tid]
                del stage_end[tid]
                if idx < 2:
                    queues[idx + 1].append(task_by_id[tid])
                    stage_of[tid] = idx + 1
                else:
                    finished.add(tid)
                    max_end = max(max_end, end)
        # 入口：依赖已全部完成的任务
        for task in tasks:
            if task.id not in started and all(d in finished for d in task.deps):
                started.add(task.id)
                queues[0].append(task)
                stage_of[task.id] = 0
        # 各阶段开工
        for idx, (kind, agent) in enumerate(stages):
            if busy[idx] <= t and queues[idx]:
                task = queues[idx].pop(0)
                duration = task.size * 0.7 * BASE_HOURS_PER_SIZE / agent.speed
                if idx > 0:
                    duration += 0.2
                stage_end[task.id] = t + duration
                busy[idx] = stage_end[task.id]
        t += DT
        if t > 500:
            raise RuntimeError("pipeline simulation did not converge")

    qualities: dict[str, float] = {}
    tokens = 0
    cost = 0.0
    for task in tasks:
        handoffs = 2
        qualities[task.id] = min(
            1.0,
            sum(agent.skill[kind] for kind, agent in stages) / 3
            + 0.02
            - handoffs * task.coupling * 0.02,
        )
        task_tokens = int(task.size * TOKENS_PER_SIZE + handoffs * 4_000)
        tokens += task_tokens
        cost += sum(
            task.size * TOKENS_PER_SIZE / 1_000_000 * agent.price for _, agent in stages
        ) + handoffs * 4_000 / 1_000_000 * 2.0

    return Result(
        quality=_weighted_quality(tasks, qualities),
        cost=cost,
        hours=max_end,
        conflicts=0,
        tokens=tokens,
    )


SIMULATORS = {
    "flat": simulate_flat,
    "hierarchy": simulate_hierarchy,
    "pipeline": simulate_pipeline,
}


def run_matrix(seeds: int = 60):
    rows = []
    for task_type in TASK_TYPES:
        for org in ORGS:
            results = [
                SIMULATORS[org](make_episode(task_type, random.Random(seed)), random.Random(seed + 1))
                for seed in range(seeds)
            ]
            rows.append(
                {
                    "type": task_type,
                    "org": org,
                    "quality": sum(r.quality for r in results) / len(results),
                    "cost": sum(r.cost for r in results) / len(results),
                    "hours": sum(r.hours for r in results) / len(results),
                    "conflicts": sum(r.conflicts for r in results) / len(results),
                }
            )
    return rows


def print_matrix(rows, w_cost=0.3, w_time=0.3):
    costs = [r["cost"] for r in rows]
    times = [r["hours"] for r in rows]
    c0, c1 = min(costs), max(costs)
    t0, t1 = min(times), max(times)
    print(f"\nweights: cost={w_cost}, time={w_time}")
    print(f"{'type':11} {'org':10} {'quality':>7} {'cost$':>6} {'hours':>6} {'conflicts':>9} {'utility':>7}")
    for r in rows:
        u = r["quality"] - w_cost * (r["cost"] - c0) / (c1 - c0) - w_time * (r["hours"] - t0) / (t1 - t0)
        print(
            f"{r['type']:11} {r['org']:10} {r['quality']:7.3f} {r['cost']:6.2f} "
            f"{r['hours']:6.2f} {r['conflicts']:9.2f} {u:7.3f}"
        )
    return c0, c1, t0, t1


def best_per_type(rows, w_cost, w_time, c0, c1, t0, t1):
    best = {}
    for task_type in TASK_TYPES:
        subset = [r for r in rows if r["type"] == task_type]
        scored = [
            (
                r["org"],
                r["quality"]
                - w_cost * (r["cost"] - c0) / (c1 - c0)
                - w_time * (r["hours"] - t0) / (t1 - t0),
            )
            for r in subset
        ]
        best[task_type] = max(scored, key=lambda x: x[1])
    return best


def _util_row(row, w_cost, w_time, c0, c1, t0, t1):
    return (
        row["quality"]
        - w_cost * (row["cost"] - c0) / (c1 - c0)
        - w_time * (row["hours"] - t0) / (t1 - t0)
    )


def run_egreedy(c0, c1, t0, t1, episodes=900, w_cost=0.1, w_time=0.1, eps=0.08, seed=11):
    rng = random.Random(seed)
    counts = {tt: {org: 0 for org in ORGS} for tt in TASK_TYPES}
    sums = {tt: {org: 0.0 for org in ORGS} for tt in TASK_TYPES}
    rewards = []
    for ep in range(episodes):
        task_type = rng.choice(TASK_TYPES)
        total = sum(counts[task_type].values())
        if total < 9:
            arm = ORGS[total % 3]
        elif rng.random() < eps:
            arm = rng.choice(ORGS)
        else:
            arm = max(
                ORGS,
                key=lambda o: (
                    sums[task_type][o] / counts[task_type][o]
                    if counts[task_type][o] else 1e9
                ),
            )
        result = SIMULATORS[arm](
            make_episode(task_type, random.Random(10_000 + ep)),
            random.Random(20_000 + ep),
        )
        reward = (
            result.quality
            - w_cost * (result.cost - c0) / (c1 - c0)
            - w_time * (result.hours - t0) / (t1 - t0)
        )
        counts[task_type][arm] += 1
        sums[task_type][arm] += reward
        rewards.append(reward)
    return sum(rewards[-200:]) / 200


def shift_experiment(w_cost=0.15, w_time=0.15):
    """阶段 1 以 pooled 为主，阶段 2 切到以 sequential 为主。"""
    rng = random.Random(42)
    types = []
    for _ in range(300):
        types.append(rng.choices(TASK_TYPES, [0.8, 0.1, 0.1])[0])
    for _ in range(300):
        types.append(rng.choices(TASK_TYPES, [0.1, 0.8, 0.1])[0])

    rows = run_matrix(seeds=40)
    costs = [r["cost"] for r in rows]
    times = [r["hours"] for r in rows]
    c0, c1, t0, t1 = min(costs), max(costs), min(times), max(times)

    def util(result):
        return (
            result.quality
            - w_cost * (result.cost - c0) / (c1 - c0)
            - w_time * (result.hours - t0) / (t1 - t0)
        )

    def sim(org, task_type, i):
        return SIMULATORS[org](
            make_episode(task_type, random.Random(1000 + i)),
            random.Random(2000 + i),
        )

    phase1 = {org: [] for org in ORGS}
    for i, task_type in enumerate(types[:300]):
        for org in ORGS:
            phase1[org].append(util(sim(org, task_type, i)))
    fixed = max(ORGS, key=lambda o: sum(phase1[o]) / len(phase1[o]))
    phase2_fixed = [
        util(sim(fixed, task_type, i))
        for i, task_type in enumerate(types[300:], start=300)
    ]

    counts = {tt: {org: 0 for org in ORGS} for tt in TASK_TYPES}
    sums = {tt: {org: 0.0 for org in ORGS} for tt in TASK_TYPES}
    rng2 = random.Random(3)
    phase2_adaptive = []
    for i, task_type in enumerate(types):
        total = sum(counts[task_type].values())
        if total < 9:
            arm = ORGS[total % 3]
        elif rng2.random() < 0.08:
            arm = rng2.choice(ORGS)
        else:
            arm = max(
                ORGS,
                key=lambda o: (
                    sums[task_type][o] / counts[task_type][o]
                    if counts[task_type][o] else 1e9
                ),
            )
        value = util(sim(arm, task_type, i))
        counts[task_type][arm] += 1
        sums[task_type][arm] += value
        if i >= 300:
            phase2_adaptive.append(value)

    return (
        fixed,
        sum(phase1[fixed]) / len(phase1[fixed]),
        sum(phase2_fixed) / len(phase2_fixed),
        sum(phase2_adaptive) / len(phase2_adaptive),
    )


def main() -> int:
    rows = run_matrix(seeds=60)
    c0, c1, t0, t1 = print_matrix(rows)

    print("\nphase: objective weights -> best org per task type")
    for wc, wt in ((0.3, 0.3), (0.1, 0.1), (0.02, 0.02), (0.0, 0.0)):
        best = best_per_type(rows, wc, wt, c0, c1, t0, t1)
        line = ", ".join(f"{k}={v[0]}" for k, v in best.items())
        print(f"  cost={wc:<4} time={wt:<4} {line}")

    wc = wt = 0.1
    best = best_per_type(rows, wc, wt, c0, c1, t0, t1)
    oracle = sum(v for _, v in best.values()) / len(best)
    fixed_scores = {
        org: sum(_util_row(r, wc, wt, c0, c1, t0, t1) for r in rows if r["org"] == org)
        / len(TASK_TYPES)
        for org in ORGS
    }
    best_fixed = max(fixed_scores.items(), key=lambda x: x[1])
    bandit = run_egreedy(c0, c1, t0, t1, w_cost=wc, w_time=wt)
    print(f"\nstationary (quality-heavy w={wc}/{wt}):")
    print(f"  oracle={oracle:.3f}  best_fixed={best_fixed[0]}:{best_fixed[1]:.3f}  adaptive={bandit:.3f}")
    print(f"  headroom (oracle - best_fixed) = {oracle - best_fixed[1]:+.3f}")

    fixed, p1, p2_fixed, p2_adaptive = shift_experiment()
    print("\nnon-stationary: phase1 pooled-heavy -> phase2 sequential-heavy")
    print(f"  fixed chosen on phase1 = {fixed}  phase1={p1:.3f}  phase2={p2_fixed:.3f}")
    print(f"  adaptive phase2={p2_adaptive:.3f}  delta={p2_adaptive - p2_fixed:+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
