"""worker_cli 冒烟测试：只用 shell 命令跑完 create → claim → commit → approve。"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / ".tmp" / f"cli-{uuid.uuid4().hex}.db"


def run(*args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "worker_cli.py", "--db", str(DB), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"command failed: {args}\n{proc.stderr}")
    return json.loads(proc.stdout)


def main() -> int:
    DB.parent.mkdir(exist_ok=True)
    project = run("create-project", "--name", "cli-demo", "--require-approval")
    task = run("create-task", "--project", project["project_id"], "--title", "t",
               "--write-set", "doc#a", "--kind", "code")
    claimed = run("claim", "--worker", "W1", "--task", task["id"], "--ttl", "30")
    assert claimed["claimed"] is True
    committed = run(
        "commit", "--task", task["id"], "--worker", "W1",
        "--token", str(claimed["lease"]["fencing_token"]),
        "--run", '{"cost_usd":0.02,"quality":0.9,"duration_s":5}',
    )
    assert committed["status"] == "review", committed
    approved = run("approve", "--task", task["id"], "--approver", "human", "--note", "ok")
    assert approved["status"] == "done", approved
    status = run("status", "--project", project["project_id"])
    print("CLI OK:", json.dumps(status["metrics"], ensure_ascii=False))
    try:
        DB.unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
