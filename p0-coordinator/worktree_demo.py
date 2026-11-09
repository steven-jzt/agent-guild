"""Worktree / 越界 diff 演示：真实 git worktree + scope check + candidate。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from coordinator import Coordinator

ROOT = Path(__file__).resolve().parent
BASE = ROOT / ".tmp" / "worktree-demo"


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def show(label: str, data) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    if BASE.exists():
        shutil.rmtree(BASE)
    repo = BASE / "repo"
    (repo / "src").mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "demo")
    git(repo, "config", "user.email", "demo@local")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    (repo / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")
    base_sha = git(repo, "rev-parse", "HEAD")

    c = Coordinator(BASE / "db.sqlite")
    p = c.create_project("worktree-demo")
    t = c.create_task(p["project_id"], "change src", ["src"])
    c.reserve_files(t["id"], "repo1", ["src"], kind="dir")

    ws = c.create_workspace(t["id"], "repo1", str(repo), path=str(BASE / "wt"))
    show("workspace", ws)
    show("base_sha", base_sha)

    (BASE / "wt" / "src" / "a.py").write_text("print('a2')\n", encoding="utf-8")
    (BASE / "wt" / "src" / "b.py").write_text("print('b')\n", encoding="utf-8")
    (BASE / "wt" / "other.txt").write_text("out of scope\n", encoding="utf-8")
    show("scope with other.txt", c.check_workspace_scope(ws["workspace_id"]))

    (BASE / "wt" / "other.txt").unlink()
    show("scope after removing other.txt", c.check_workspace_scope(ws["workspace_id"]))
    show("commit", {"sha": c.commit_workspace(ws["workspace_id"], "change")})
    show("candidate", c.create_candidate_from_workspace(ws["workspace_id"]))

    c.remove_workspace(ws["workspace_id"])
    show("workspaces", c.list_workspaces(task_id=t["id"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
