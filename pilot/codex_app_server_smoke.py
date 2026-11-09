"""Codex App Server 最小 JSON-RPC 冒烟：initialize → thread/start → turn/start。"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

CODEX_HOME = Path(__file__).resolve().parent.parent / "p0-coordinator" / ".tmp" / "codex-deepseek-home"
WORKSPACE = Path(__file__).resolve().parent.parent / "p0-coordinator" / ".tmp" / "appserver-ws"


class Client:
    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self.q: queue.Queue[dict | None] = queue.Queue()
        self.notifications: list[dict] = []
        self._id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.q.put(json.loads(line))
            except json.JSONDecodeError:
                self.q.put({"raw": line})
        self.q.put(None)

    def request(self, method: str, params: dict, timeout: float = 30) -> dict:
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.q.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            if msg is None:
                raise RuntimeError("app-server stdout closed")
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(f"{method} failed: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.notifications.append(msg)
        raise TimeoutError(f"{method} timed out")

    def wait_turn(self, timeout: float = 180) -> list[dict]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.q.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            if msg is None:
                raise RuntimeError("app-server stdout closed")
            if "method" in msg:
                self.notifications.append(msg)
                if msg["method"] in ("turn/completed", "turn/failed"):
                    return self.notifications
        return self.notifications


def main() -> int:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY required", file=sys.stderr)
        return 2
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    codex = shutil.which("codex")
    if not codex:
        print("codex executable not found", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env["CODEX_HOME"] = str(CODEX_HOME)
    proc = subprocess.Popen(
        [codex, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(WORKSPACE),
    )
    client = Client(proc)
    try:
        init = client.request("initialize", {
            "clientInfo": {"name": "agent-guild-pilot", "version": "0.1.0"},
        }, timeout=20)
        print("INIT", json.dumps(init, ensure_ascii=False)[:500], flush=True)

        thread = client.request("thread/start", {
            "cwd": str(WORKSPACE),
            "model": "deepseek-flash",
            "modelProvider": "deepseek",
            "sandbox": "danger-full-access",
            "approvalPolicy": "never",
        }, timeout=30)
        thread_id = thread["thread"]["id"]
        print("THREAD", thread_id, "provider", thread.get("modelProvider"), flush=True)

        turn = client.request("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": (
                "Create a file named appserver.txt containing exactly OK "
                "in the current working directory. Do not commit. Reply DONE."
            )}],
            "cwd": str(WORKSPACE),
        }, timeout=30)
        print("TURN", json.dumps(turn, ensure_ascii=False)[:500], flush=True)

        events = client.wait_turn(120)
        texts = []
        usage = None
        for e in events:
            method = e.get("method")
            params = e.get("params") or {}
            if method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agent_message":
                    texts.append(item.get("text"))
            if method == "thread/tokenUsage/updated":
                usage = params
        print("EVENTS", len(events), [e.get("method") for e in events], flush=True)
        print(json.dumps({
            "thread_id": thread_id,
            "texts": texts,
            "usage": usage,
            "completed": any(e.get("method") == "turn/completed" for e in events),
        }, ensure_ascii=False, indent=2)[:4000], flush=True)
        return 0
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
