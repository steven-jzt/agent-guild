"""MCP stdio 冒烟测试：启动 server，走 initialize → tools/list → 创建/领取/提交。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / ".tmp" / f"mcp-{uuid.uuid4().hex}.db"


def main() -> int:
    DB.parent.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["COORDINATOR_DB"] = str(DB)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "mcp_server.py"],
        cwd=str(ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert proc.stdin is not None and proc.stdout is not None

    def send(obj):
        proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def recv():
        line = proc.stdout.readline()
        if not line:
            err = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(f"server closed: {err}")
        return json.loads(line)

    def call(msg_id, name, args):
        send({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
              "params": {"name": name, "arguments": args}})
        return recv()

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        init = recv()
        assert init["result"]["serverInfo"]["name"] == "p0-coordinator"

        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = recv()["result"]["tools"]
        names = {t["name"] for t in tools}
        for expected in ("create_project", "create_task", "claim_task", "commit_task"):
            assert expected in names, expected

        p = json.loads(call(3, "create_project", {"name": "mcp-demo"})["result"]["content"][0]["text"])
        t = json.loads(call(4, "create_task", {
            "project_id": p["project_id"], "title": "t", "write_set": ["doc#a"]
        })["result"]["content"][0]["text"])
        claimed = json.loads(call(5, "claim_task", {
            "worker": "W1", "project_id": p["project_id"]
        })["result"]["content"][0]["text"])
        assert claimed["claimed"] is True
        done = json.loads(call(6, "commit_task", {
            "task_id": t["id"], "worker": "W1", "fencing_token": claimed["lease"]["fencing_token"],
            "run": {"duration_s": 3, "input_tokens": 100, "output_tokens": 50,
                    "cost_usd": 0.01, "quality": 0.9, "diff_lines": 5}
        })["result"]["content"][0]["text"])
        assert done["status"] == "done"
        metrics = json.loads(call(7, "get_metrics", {})["result"]["content"][0]["text"])
        estimates = json.loads(call(8, "get_estimates", {})["result"]["content"][0]["text"])
        assert estimates["overall"]["runs"] == 1
        print("MCP OK:", json.dumps(metrics, ensure_ascii=False))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            DB.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
