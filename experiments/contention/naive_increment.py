#!/usr/bin/env python3
"""朴素读-改-写：模拟 agent 直接覆盖写共享 JSON 的行为。

每个进程循环 N 次：读 counter.json -> sleep -> 整个文件写回 count+1。
没有锁、没有版本检查，因此并发时会丢失更新，甚至读到写了一半的文件。
"""
import argparse
import json
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--delay", type=float, default=0.001)
    args = ap.parse_args()

    reads = 0
    corrupt = 0
    for _ in range(args.n):
        for attempt in range(50):
            try:
                with open(args.data, encoding="utf-8") as f:
                    state = json.load(f)
                break
            except (json.JSONDecodeError, UnicodeDecodeError):
                corrupt += 1
                time.sleep(0.001)
        else:
            raise SystemExit("too many corrupt reads")

        reads += 1
        time.sleep(args.delay)
        state["count"] += 1
        with open(args.data, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)

    print(f"pid={__import__('os').getpid()} done n={args.n} corrupt_reads={corrupt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
