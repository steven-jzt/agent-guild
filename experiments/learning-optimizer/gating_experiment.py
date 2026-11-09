"""门控实验：学习策略什么时候该“闭嘴”，回退到规则？

做法：
- 用随机森林的 200 棵树分别对 216 个分配打分；
- 每棵树有自己的 argmax；
- stability = 多少比例的树与集成 argmax 一致；
- 若 stability < 阈值，回退到贪心；否则用学习策略。

比较：always-learned / greedy / gated（不同阈值）的 mean regret、p90、coverage。
"""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import RandomForestRegressor

import train_optimizer as base

WC, WT = 0.3, 0.3
NOISE = 0.02


def setup():
    assignments = base.all_assignments()
    results = {}
    for a in assignments:
        r = base.toy.evaluate(a)
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

    X = np.array([base.features(a) for a in feasible])
    y = np.array([util(a) for a in feasible])
    greedy = base.toy.heuristic("greedy")
    greedy_idx = feasible.index(greedy)
    return feasible, X, y, greedy_idx


def main() -> int:
    feasible, X_all, y_all, greedy_idx = setup()
    oracle = float(y_all.max())
    greedy_regret = oracle - y_all[greedy_idx]
    sizes = [10, 20, 50, 100, 200]
    thresholds = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50]
    seeds = 20

    print(f"feasible={len(feasible)} oracle={oracle:.3f} greedy_regret={greedy_regret:+.4f}")
    print(f"\n{'N':>4} {'thr':>5} {'coverage':>9} {'mean_reg':>9} {'p90_reg':>8}")
    for n in sizes:
        stats = {th: {"regs": [], "cover": 0} for th in thresholds}
        for seed in range(seeds):
            rng = np.random.default_rng(seed)
            idx = rng.choice(len(feasible), size=min(n, len(feasible)), replace=False)
            y = y_all[idx] + rng.normal(0, NOISE, size=len(idx))
            rf = RandomForestRegressor(
                n_estimators=200, min_samples_leaf=2, random_state=seed
            )
            rf.fit(X_all[idx], y)
            tree_pred = np.array([t.predict(X_all) for t in rf.estimators_])
            mean = tree_pred.mean(axis=0)
            top = int(np.argmax(mean))
            stability = float(np.mean(tree_pred.argmax(axis=1) == top))
            for th in thresholds:
                use_learned = stability >= th
                chosen = top if use_learned else greedy_idx
                stats[th]["regs"].append(oracle - y_all[chosen])
                stats[th]["cover"] += int(use_learned)
        for th in thresholds:
            regs = np.array(stats[th]["regs"])
            print(
                f"{n:4d} {th:5.2f} {stats[th]['cover']/seeds:9.2f} "
                f"{regs.mean():9.4f} {np.percentile(regs, 90):8.4f}"
            )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
