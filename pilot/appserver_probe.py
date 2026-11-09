"""最小 Codex App Server 探测：只做 initialize，打印原始消息。"""

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

ROOT = Path(__file__).resolve().parent
P0 = ROOT.parent / "p0-coordinator"
HOME = P0 / ".tmp" / "codex-deepseek-home"
WS = P0 / ".tmp" / "appserver-probe"
WS.mkdir(parents=True, exist_ok=True)


def main() -> int:
    codex = shutil.which("codex")
    if not codex:
        print("codex not found", flush=True)
        return 2
    env = os.environ.copy()
    env["CODEX_HOME"] = str(HOME)
    proc = subprocess.Popen(
        [codex, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=env, cwd=str(WS),
    )
    q: queue.Queue[str] = queue.Queue()

    def read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            q.put(line.rstrip("\n"))
        q.put("__EOF__")

    threading.Thread(target=read, daemon=True).start()
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
           "params": {"clientInfo": {"name": "probe", "version": "0.1.0"}}}
    print("SEND", json.dumps(req), flush=True)
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            line = q.get(timeout=1)
        except queue.Empty:
            print("WAIT", round(deadline - time.time(), 1), flush=True)
            continue
        print("RECV", line[:2000], flush=True)
        if line == "__EOF__":
            break
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == 1:
            break
    err = (proc.stderr.read() if proc.stderr else "")[-2000:]
    print("STDERR", err, flush=True)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
