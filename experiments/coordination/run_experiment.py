"""对照实验：agent 之间“通信/共享信息”到底能不能减少写冲突。

六种协议，三种都涉及通信，区别在于是否把信息变成协调动作：
  silent   无通信、无原子性（基线）
  paced    无通信，但节奏与 announce 相同（隔离“错峰”效应）
  announce 有通信（广播意图），无避让、无原子性
  polite   有通信 + 收到意图后退避重试，无原子性
  lease    有通信 + 中心化互斥锁（原子裁决）
  cas      无通信 + 版本号校验重试（check-then-act，非原子 CAS）

用法：python run_experiment.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from broker import STATE, start_broker

ROOT = Path(__file__).resolve().parent
N = 30
PROCS = 3
EXPECTED = N * PROCS
TRIALS = 5
PROTOCOLS = ["silent", "paced", "announce", "polite", "lease", "cas"]


def run_trial(protocol: str, port: int) -> dict:
    data = ROOT / f"counter_{protocol}.json"
    data.write_text(json.dumps({"count": 0, "version": 0}), encoding="utf-8")
    STATE.messages = 0
    STATE.grants = 0

    procs = [
        subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "worker.py"),
                "--protocol", protocol,
                "--name", f"{protocol}-{i}",
                "--port", str(port),
                "--data", str(data),
                "--n", str(N),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        for i in range(PROCS)
    ]
    outputs = [p.communicate(timeout=180)[0] for p in procs]

    final = json.loads(data.read_text(encoding="utf-8"))["count"]
    retries = 0
    elapsed = 0.0
    for out in outputs:
        lines = [line for line in out.splitlines() if line.strip()]
        if lines:
            try:
                payload = json.loads(lines[-1])
            except json.JSONDecodeError:
                continue
            retries += int(payload.get("retries", 0))
            elapsed = max(elapsed, float(payload.get("elapsed", 0)))
    return {
        "final": final,
        "lost": EXPECTED - final,
        "retries": retries,
        "elapsed": round(elapsed, 2),
        "messages": STATE.messages,
        "grants": STATE.grants,
    }


def main() -> int:
    server, port = start_broker()
    print(f"broker on 127.0.0.1:{port}; {PROCS} agents x {N} ops = {EXPECTED} expected\n")
    header = f"{'protocol':10s} {'trial':>5s} {'final':>6s} {'lost':>5s} {'retries':>7s} {'msgs':>5s} {'grants':>6s} {'sec':>5s}"
    print(header)
    print("-" * len(header))

    summary: dict[str, list[dict]] = {}
    try:
        for protocol in PROTOCOLS:
            rows = []
            for trial in range(1, TRIALS + 1):
                row = run_trial(protocol, port)
                rows.append(row)
                print(
                    f"{protocol:10s} {trial:5d} {row['final']:6d} {row['lost']:5d} "
                    f"{row['retries']:7d} {row['messages']:5d} {row['grants']:6d} {row['elapsed']:5.2f}"
                )
            summary[protocol] = rows
            print()
    finally:
        server.shutdown()
        server.server_close()

    print(f"=== 汇总（{TRIALS} 次平均） ===")
    for protocol in PROTOCOLS:
        rows = summary[protocol]
        avg_final = sum(r["final"] for r in rows) / len(rows)
        avg_lost = sum(r["lost"] for r in rows) / len(rows)
        avg_retries = sum(r["retries"] for r in rows) / len(rows)
        avg_sec = sum(r["elapsed"] for r in rows) / len(rows)
        print(
            f"{protocol:10s} final={avg_final:6.1f}/{EXPECTED}  lost={avg_lost:5.1f}  "
            f"retries={avg_retries:6.1f}  elapsed={avg_sec:5.2f}s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
