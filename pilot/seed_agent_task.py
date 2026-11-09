"""给 WebView2 UI 造一个 ready task + active workspace，供 Start Run 真跑 agent。"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
P0 = ROOT.parent / "p0-coordinator"
if not (P0 / "coordinator.py").exists():
    P0 = ROOT.parent / "05-代码" / "p0-coordinator"
sys.path.insert(0, str(P0))

from coordinator import Coordinator  # noqa: E402

BASE = P0 / ".tmp" / "agent-demo"
REPO = BASE / "repo"
DB = BASE / "db.sqlite"
WT = BASE / "wt"


def _rmtree(path: Path) -> None:
    def onexc(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onexc=onexc)


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def main() -> int:
    if BASE.exists():
        _rmtree(BASE)
    (REPO / "src").mkdir(parents=True)
    (REPO / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    git(REPO, "init", "-b", "main")
    git(REPO, "config", "user.name", "pilot")
    git(REPO, "config", "user.email", "pilot@local")
    git(REPO, "add", "-A")
    git(REPO, "commit", "-m", "base")

    c = Coordinator(DB)
    p = c.create_project("agent-demo")
    t = c.create_task(
        p["project_id"], "Add mul to src/app.py", ["src/app.py"], repo_id="repo1"
    )
    c.reserve_files(t["id"], "repo1", ["src/app.py"])
    ws = c.create_workspace(t["id"], "repo1", str(REPO), path=str(WT))
    print(json.dumps({
        "db": str(DB),
        "task_id": t["id"],
        "workspace_id": ws["workspace_id"],
        "repo": str(REPO),
        "worktree_for_merge": str(BASE / "integration"),
        "check": "python -m py_compile src/app.py",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
