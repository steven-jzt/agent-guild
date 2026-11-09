#!/usr/bin/env python3
"""带文件锁的读-改-写：用原子的 mkdir 作为互斥锁。

和 naive_increment.py 做同样的 N 次自增，但每次 RMW 都在锁内完成，
因此并发进程不会互相覆盖。
"""
import argparse
import json
import os
import time


def acquire(lock_dir: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            os.mkdir(lock_dir)
            return
        except (FileExistsError, PermissionError):
            # Windows 下目录处于 delete-pending 时 mkdir 会报 WinError 5，
            # 和 FileExistsError 一样按“锁被占用”处理并重试。
            try:
                if time.time() - os.path.getmtime(lock_dir) > 15:
                    os.rmdir(lock_dir)  # 清理明显陈旧的锁
                    continue
            except OSError:
                pass
            if time.time() > deadline:
                raise TimeoutError(f"lock timeout: {lock_dir}")
            time.sleep(0.001)


def release(lock_dir: str) -> None:
    try:
        os.rmdir(lock_dir)
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--delay", type=float, default=0.001)
    args = ap.parse_args()
    lock = args.data + ".lock"

    for _ in range(args.n):
        acquire(lock)
        try:
            try:
                with open(args.data, encoding="utf-8") as f:
                    state = json.load(f)
            except FileNotFoundError:
                state = {"count": 0}
            time.sleep(args.delay)
            state["count"] += 1
            with open(args.data, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
        finally:
            release(lock)

    print(f"pid={os.getpid()} done n={args.n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
