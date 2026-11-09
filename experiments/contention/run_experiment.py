#!/usr/bin/env python3
"""并发写共享文件的对照实验：朴素覆盖写 vs 加锁。

用法：python run_experiment.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TRIALS = 3
PROCS = 3
N = 60
EXPECTED = PROCS * N


def reset(name: str) -> Path:
    path = ROOT / name
    path.write_text(json.dumps({"count": 0}), encoding="utf-8")
    return path


def trial(script: str, data_name: str) -> tuple[int, list[int]]:
    data = reset(data_name)
    procs = [
        subprocess.Popen(
            [sys.executable, str(ROOT / script), "--data", str(data), "--n", str(N), "--delay", "0.001"],
            cwd=str(ROOT),
        )
        for _ in range(PROCS)
    ]
    codes = [p.wait() for p in procs]
    return json.loads(data.read_text(encoding="utf-8"))["count"], codes


def main() -> int:
    print(f"expected = {PROCS} processes x {N} = {EXPECTED}\n")
    for label, script in (("naive (no lock)", "naive_increment.py"), ("locked (mkdir lock)", "locked_increment.py")):
        for i in range(1, TRIALS + 1):
            count, codes = trial(script, f"counter_{script.split('_')[0]}.json")
            lost = EXPECTED - count
            print(f"{label:22s} trial {i}: final={count:3d}  lost={lost:3d}  exit={codes}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
