"""自动验证演示：checks 由 verifier.py 真正执行，而不是 worker 自报。"""

from __future__ import annotations

import json
from pathlib import Path

from coordinator import Coordinator
from verifier import auto_verify

ROOT = Path(__file__).resolve().parent
BASE = ROOT / ".tmp" / "auto-verify-demo"
DB = BASE / "demo.db"


def main() -> int:
    BASE.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    c = Coordinator(DB)
    p = c.create_project("auto-verify-demo", require_verification=True)

    task = c.create_task(
        p["project_id"], "write part", ["out.md"],
        checks=[
            {"type": "file_exists", "path": "out.md"},
            {"type": "file_contains", "path": "out.md", "text": "coordination"},
            {"type": "file_regex", "path": "out.md", "pattern": r"^# Part$"},
        ],
    )
    (BASE / "out.md").write_text("# Part\neffective coordination works\n", encoding="utf-8")
    r = c.claim_task("W1", task_id=task["id"])
    c.commit_task(task["id"], "W1", r["lease"]["fencing_token"])
    result = auto_verify(c, task["id"], root=str(BASE))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    bad = c.create_task(
        p["project_id"], "missing file", ["missing.md"],
        checks=[{"type": "file_exists", "path": "missing.md"}],
    )
    r = c.claim_task("W2", task_id=bad["id"])
    c.commit_task(bad["id"], "W2", r["lease"]["fencing_token"])
    failed = auto_verify(c, bad["id"], root=str(BASE))
    print(json.dumps(failed, ensure_ascii=False, indent=2, default=str))

    cmd_task = c.create_task(
        p["project_id"], "run command", ["cmd"],
        checks=[{"type": "command", "cmd": "python -c \"print('ok')\""}],
    )
    r = c.claim_task("W3", task_id=cmd_task["id"])
    c.commit_task(cmd_task["id"], "W3", r["lease"]["fencing_token"])
    cmd_result = auto_verify(c, cmd_task["id"], root=str(BASE), allow_command=True)
    print(json.dumps(cmd_result, ensure_ascii=False, indent=2, default=str))

    print("metrics:", json.dumps(c.metrics(), ensure_ascii=False))
    return 0 if result["passed"] and not failed["passed"] and cmd_result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
