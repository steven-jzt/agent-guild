"""从 runs 表聚合成本/质量/工时先验。

用法：
    python estimate.py --db data/coordinator.db
    python estimate.py --db data/coordinator.db --json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from coordinator import Coordinator


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    data = sorted(values)
    if len(data) == 1:
        return data[0]
    pos = (len(data) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(data) - 1)
    frac = pos - lo
    return data[lo] + (data[hi] - data[lo]) * frac


def group(runs: Iterable[dict]) -> dict:
    rows = list(runs)
    costs = [float(r["cost_usd"]) for r in rows]
    durations = [float(r["duration_s"]) for r in rows]
    qualities = [float(r["quality"]) for r in rows if r["quality"] is not None]
    diffs = [int(r["diff_lines"]) for r in rows]
    return {
        "runs": len(rows),
        "avg_cost_usd": round(sum(costs) / len(costs), 4) if costs else None,
        "p90_cost_usd": round(percentile(costs, 0.9), 4) if costs else None,
        "avg_quality": round(sum(qualities) / len(qualities), 3) if qualities else None,
        "avg_duration_s": round(sum(durations) / len(durations), 1) if durations else None,
        "avg_diff_lines": round(sum(diffs) / len(diffs), 1) if diffs else None,
    }


def summarize(db_path: str | Path, limit: int = 10000) -> dict:
    coord = Coordinator(db_path)
    runs = coord.list_runs(limit=limit)
    by_worker: dict[str, list[dict]] = defaultdict(list)
    by_kind: dict[str, list[dict]] = defaultdict(list)
    by_worker_kind: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        by_worker[run["worker"]].append(run)
        by_kind[run["task_kind"]].append(run)
        by_worker_kind[f'{run["worker"]}::{run["task_kind"]}'].append(run)
    return {
        "overall": group(runs),
        "by_worker": {k: group(v) for k, v in sorted(by_worker.items())},
        "by_kind": {k: group(v) for k, v in sorted(by_kind.items())},
        "by_worker_kind": {k: group(v) for k, v in sorted(by_worker_kind.items())},
    }


def print_summary(summary: dict) -> None:
    print("=== overall ===")
    print(json.dumps(summary["overall"], ensure_ascii=False))
    for title, key in (("by worker", "by_worker"), ("by kind", "by_kind")):
        print(f"\n=== {title} ===")
        header = f"{'key':22} {'runs':>4} {'avg_cost':>9} {'p90_cost':>9} {'quality':>7} {'duration':>9}"
        print(header)
        print("-" * len(header))
        for name, stats in summary[key].items():
            print(
                f"{name:22} {stats['runs']:4d} {stats['avg_cost_usd']:9.4f} "
                f"{stats['p90_cost_usd']:9.4f} {stats['avg_quality'] if stats['avg_quality'] is not None else 0:7.3f} "
                f"{stats['avg_duration_s']:9.1f}"
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/coordinator.db")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=10000)
    args = ap.parse_args()

    summary = summarize(args.db, args.limit)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
