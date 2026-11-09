"""模拟一个 agent：对共享计数器做 N 次读-改-写。

六种协议，区别只在“写之前做了什么”：
- silent   不说话，直接读改写；
- paced    不说话，但先等 20ms（与 announce 同样的时间节奏，用来隔离“错峰”效应）；
- announce 每次写前广播“我要写了”，但自己不等待、别人也不避让（纯信息共享）；
- polite   广播前先看别人的意图，发现有人最近要写就退避重试（信息 + 礼貌）；
- lease    向 broker 申请互斥锁，拿到才写（通信 + 原子裁决）；
- cas      不通信，写前校验版本号，变了就重试（注意：check-then-act 本身不是原子 CAS）。
"""

from __future__ import annotations

import argparse
import json
import random
import socket
import threading
import time


class Conn:
    def __init__(self, port: int, name: str) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=30)
        self.name = name
        self.lock = threading.Lock()
        self.last_intent: dict[str, float] = {}
        self.grant = threading.Event()
        self._send({"type": "hello", "name": name})
        threading.Thread(target=self._read, daemon=True).start()

    def _send(self, payload: dict) -> None:
        self.sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))

    def _read(self) -> None:
        try:
            stream = self.sock.makefile("r", encoding="utf-8")
            for line in stream:
                msg = json.loads(line)
                if msg.get("type") == "intent" and msg.get("name") != self.name:
                    with self.lock:
                        self.last_intent[msg["name"]] = time.monotonic()
                elif msg.get("type") == "grant":
                    self.grant.set()
        except (OSError, ValueError):
            pass

    def announce(self, seq: int) -> None:
        self._send({"type": "announce", "seq": seq})

    def acquire(self) -> None:
        self.grant.clear()
        self._send({"type": "acquire"})
        if not self.grant.wait(30):
            raise TimeoutError("lease timeout")

    def release(self) -> None:
        self._send({"type": "release"})

    def recent_peer_intent(self, window: float) -> bool:
        now = time.monotonic()
        with self.lock:
            return any(now - ts < window for ts in self.last_intent.values())

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def read_counter(path: str, attempts: int = 300) -> dict:
    for _ in range(attempts):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.001)
    raise RuntimeError(f"could not read {path}")


def write_counter(path: str, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def rmw(path: str, delay: float) -> None:
    state = read_counter(path)
    time.sleep(delay)
    state["count"] += 1
    state["version"] += 1
    write_counter(path, state)


def cas_rmw(path: str, delay: float) -> int:
    for attempt in range(500):
        state = read_counter(path)
        version = state["version"]
        time.sleep(delay)
        current = read_counter(path)
        if current["version"] == version:
            current["count"] += 1
            current["version"] = version + 1
            write_counter(path, current)
            return attempt
        time.sleep(random.uniform(0.0, 0.002))
    raise RuntimeError("cas retries exhausted")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", required=True, choices=["silent", "paced", "announce", "polite", "lease", "cas"])
    ap.add_argument("--name", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--delay", type=float, default=0.001)
    args = ap.parse_args()

    conn = Conn(args.port, args.name) if args.protocol not in ("cas", "paced") else None
    retries = 0
    started = time.perf_counter()

    try:
        for seq in range(args.n):
            if args.protocol == "silent":
                rmw(args.data, args.delay)

            elif args.protocol == "paced":
                time.sleep(0.02)  # 与 announce 相同的时间节奏，但不发消息
                rmw(args.data, args.delay)

            elif args.protocol == "announce":
                conn.announce(seq)
                time.sleep(0.02)  # 让消息飞一会儿
                rmw(args.data, args.delay)

            elif args.protocol == "lease":
                conn.acquire()
                try:
                    rmw(args.data, args.delay)
                finally:
                    conn.release()

            elif args.protocol == "cas":
                retries += cas_rmw(args.data, args.delay)

            elif args.protocol == "polite":
                for _ in range(12):
                    if not conn.recent_peer_intent(window=0.15):
                        conn.announce(seq)
                        time.sleep(0.02)
                        rmw(args.data, args.delay)
                        break
                    retries += 1
                    time.sleep(random.uniform(0.02, 0.06))
                else:
                    retries += 1
                    rmw(args.data, args.delay)
    finally:
        if conn is not None:
            conn.close()

    print(
        json.dumps(
            {
                "name": args.name,
                "protocol": args.protocol,
                "ops": args.n,
                "retries": retries,
                "elapsed": round(time.perf_counter() - started, 3),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
