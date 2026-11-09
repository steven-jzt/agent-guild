"""V1 核心切片演示：execution（Run）+ repo/path 文件预约。"""

from __future__ import annotations

import json
from pathlib import Path

from coordinator import Coordinator, CoordinatorError

ROOT = Path(__file__).resolve().parent
DB = ROOT / ".tmp" / "v1-core-demo.db"


def show(label: str, data) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    c = Coordinator(DB)
    p = c.create_project("v1-core-demo")
    t1 = c.create_task(p["project_id"], "t1", ["src"])
    t2 = c.create_task(p["project_id"], "t2", ["src/a.py"])

    e1 = c.start_execution(t1["id"], "agent-a", workspace="/workspaces/agent-a")
    show("start execution", e1)

    show("reserve src/ for t1", c.reserve_files(t1["id"], "repo1", ["src"], kind="dir"))
    try:
        c.reserve_files(t2["id"], "repo1", ["src/a.py"])
    except CoordinatorError as exc:
        show("t2 tries src/a.py -> conflict", str(exc))

    c.release_file_reservations(task_id=t1["id"], reason="handoff")
    show("release t1, reserve t2", c.reserve_files(t2["id"], "repo1", ["src/a.py"]))

    show("finish execution", c.finish_execution(e1["execution_id"], "succeeded", "done"))
    show("executions", c.list_executions(t1["id"]))
    show("file reservations", c.list_file_reservations(repo_id="repo1"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
