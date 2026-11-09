"""一个极简的消息 broker，用来模拟 agent 之间的通信。

支持三种消息：
- hello：注册名字；
- announce：广播“我要写了”的意图（纯信息共享）；
- acquire / release：申请/释放一把互斥锁（通信 + 原子裁决）。

它故意做得很小：单进程、内存状态、TCP 明文 JSON 行。
"""

from __future__ import annotations

import json
import socketserver
import threading


class BrokerState:
    def __init__(self) -> None:
        self.mu = threading.Lock()
        self.owner: str | None = None
        self.waiters: list[tuple[str, "Handler"]] = []
        self.clients: dict[str, "Handler"] = {}
        self.messages = 0
        self.grants = 0

    def register(self, name: str, handler: "Handler") -> None:
        with self.mu:
            self.clients[name] = handler

    def unregister(self, name: str, handler: "Handler") -> None:
        with self.mu:
            if self.clients.get(name) is handler:
                self.clients.pop(name, None)

    def broadcast(self, payload: dict) -> None:
        with self.mu:
            self.messages += 1
            handlers = list(self.clients.values())
        for handler in handlers:
            handler.safe_send(payload)

    def acquire(self, name: str, handler: "Handler") -> None:
        with self.mu:
            self.messages += 1
            if self.owner is None:
                self.owner = name
                grant = True
            else:
                self.waiters.append((name, handler))
                grant = False
        if grant:
            self.grants += 1
            handler.safe_send({"type": "grant"})

    def release(self, name: str) -> None:
        nxt = None
        with self.mu:
            self.messages += 1
            if self.owner != name:
                return
            self.owner = None
            if self.waiters:
                nxt = self.waiters.pop(0)
                self.owner = nxt[0]
        if nxt is not None:
            self.grants += 1
            nxt[1].safe_send({"type": "grant"})


STATE = BrokerState()


class Handler(socketserver.StreamRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.wlock = threading.Lock()
        self.name: str | None = None

    def safe_send(self, payload: dict) -> None:
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        with self.wlock:
            try:
                self.wfile.write(data)
                self.wfile.flush()
            except OSError:
                pass

    def handle(self) -> None:
        try:
            for raw in self.rfile:
                if not raw.strip():
                    continue
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "hello":
                    self.name = str(msg["name"])
                    STATE.register(self.name, self)
                    self.safe_send({"type": "welcome"})
                elif kind == "announce":
                    STATE.broadcast(
                        {"type": "intent", "name": self.name, "seq": msg.get("seq")}
                    )
                    self.safe_send({"type": "ack"})
                elif kind == "acquire":
                    STATE.acquire(self.name or "?", self)
                elif kind == "release":
                    STATE.release(self.name or "?")
                    self.safe_send({"type": "released"})
                else:
                    self.safe_send({"type": "error", "detail": f"unknown {kind!r}"})
        except (ConnectionError, json.JSONDecodeError):
            pass
        finally:
            if self.name:
                STATE.unregister(self.name, self)


class BrokerServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_broker() -> tuple[BrokerServer, int]:
    server = BrokerServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]
