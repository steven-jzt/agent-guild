"""从 trace 学习分配策略：最小演示。

环境复用 or-planner/toy_optimizer.py 的 6 任务 × 3 agent 模型。
流程：
1. 随机采样 N 条历史分配（trace），带噪声观测 utility；
2. 用线性模型（ridge）从分配特征预测 utility；
3. 枚举所有分配，选预测最优；
4. 看真实 regret 随 N 的变化，对比贪心启发式。

这是“predict-then-optimize”的最小版本，不是端到端 RL。
"""

from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent
TOY_PATH = ROOT.parent / "or-planner" / "toy_optimizer.py"
spec = importlib.util.spec_from_file_location("toy", TOY_PATH)
toy = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(toy)

WC, WT = 0.3, 0.3
NOISE = 0.02


def all_assignments():
    return list(itertools.product(range(len(toy.AGENTS)), repeat=len(toy.TASKS)))


def features(assign):
    total_cost_proxy = 0.0
    loads = [0.0, 0.0, 0.0]
    counts = [0, 0, 0]
    skills = [0.0, 0.0, 0.0]
    for task, ai in zip(toy.TASKS, assign):
        agent = toy.AGENTS[ai]
        loads[ai] += task["size"] / agent["speed"]
        counts[ai] += 1
        skills[ai] += task["size"] * agent["quality"][task["kind"]]
        total_cost_proxy += task["size"] * toy.TOKENS_PER_SIZE / 1_000_000 * agent["price"]
    handoffs = 0
    for task in toy.TASKS:
        for dep in task["deps"]:
            if assign[toy.ORDER.index(task["id"])] != assign[toy.ORDER.index(dep)]:
                handoffs += 1
    return np.array(
        [1.0, total_cost_proxy, handoffs, max(loads), *loads, *counts, *skills],
        dtype=float,
    )


def model_factories():
    return {
        "linear": lambda: make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "rf": lambda: RandomForestRegressor(
            n_estimators=300, min_samples_leaf=2, random_state=0
        ),
        "gbr": lambda: GradientBoostingRegressor(random_state=0),
    }


def main() -> int:
    assignments = all_assignments()
    results = {}
    for a in assignments:
        r = toy.evaluate(a)
        if r is not None:
            results[a] = r
    feasible = list(results)

    costs = [r["cost"] for r in results.values()]
    times = [r["makespan"] for r in results.values()]
    c0, c1 = min(costs), max(costs)
    t0, t1 = min(times), max(times)

    def util(a):
        r = results[a]
        return (
            r["quality"]
            - WC * (r["cost"] - c0) / (c1 - c0)
            - WT * (r["makespan"] - t0) / (t1 - t0)
        )

    X_all = np.array([features(a) for a in feasible])
    y_all = np.array([util(a) for a in feasible])
    oracle = float(y_all.max())

    # 贪心基线
    heur = {}
    for name in ("cheapest", "quality", "fastest", "greedy"):
        a = toy.heuristic(name)
        heur[name] = oracle - util(a) if a in results else float("nan")

    rng = np.random.default_rng(7)
    sizes = [5, 10, 20, 50, 100, 200, 400, len(feasible)]
    print(f"feasible assignments: {len(feasible)}; oracle utility={oracle:.3f}")
    print("\nheuristic regret:")
    for name, reg in heur.items():
        print(f"  {name:10} {reg:+.3f}")

    print(f"\n{'N traces':>9} " + " ".join(f"{name:>10}" for name in model_factories()))
    for n in sizes:
        row = []
        for name, factory in model_factories().items():
            regrets = []
            for seed in range(20):
                r = np.random.default_rng(seed)
                idx = r.choice(len(feasible), size=min(n, len(feasible)), replace=False)
                X = X_all[idx]
                y = y_all[idx] + r.normal(0, NOISE, size=len(idx))
                model = factory()
                model.fit(X, y)
                pred = model.predict(X_all)
                best = int(np.argmax(pred))
                regrets.append(oracle - y_all[best])
            row.append(float(np.mean(regrets)))
        print(f"{n:9d} " + " ".join(f"{v:10.4f}" for v in row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
