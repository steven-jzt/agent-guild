"""初始化真实仓库试点：源码 + 测试 + 检查脚本 + 任务/预约/workspace。"""

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

TEMPLATE = ROOT / "real-repo-template"
BASE = P0 / ".tmp" / "real-repo"
REPO = BASE / "repo"
DB = BASE / "db.sqlite"
WT = BASE / "wt"
CHECK = "python check.py"
PROMPT = (
    "Implement mul(a, b) and div(a, b) in src/calc/ops.py. "
    "Add unit tests named test_mul and test_div in tests/test_ops.py. "
    "Keep add and sub unchanged. Do not modify check.py. "
    "Do not commit. Reply DONE."
)


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
    shutil.copytree(TEMPLATE, REPO)
    git(REPO, "init", "-b", "main")
    git(REPO, "config", "user.name", "pilot")
    git(REPO, "config", "user.email", "pilot@local")
    git(REPO, "add", "-A")
    git(REPO, "commit", "-m", "calc base")

    base_check = subprocess.run(
        ["python", "check.py"], cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )

    c = Coordinator(DB)
    p = c.create_project("real-repo")
    t = c.create_task(
        p["project_id"],
        "Add mul and div with tests",
        ["src/calc/ops.py", "tests/test_ops.py"],
        repo_id="repo1",
        checks=[{"type": "command", "command": CHECK}],
    )
    c.reserve_files(t["id"], "repo1", ["src/calc/ops.py", "tests/test_ops.py"])
    ws = c.create_workspace(t["id"], "repo1", str(REPO), path=str(WT))
    print(json.dumps({
        "db": str(DB),
        "project_id": p["project_id"],
        "task_id": t["id"],
        "workspace_id": ws["workspace_id"],
        "repo": str(REPO),
        "worktree_for_merge": str(BASE / "integration"),
        "check": CHECK,
        "prompt": PROMPT,
        "base_check_exit": base_check.returncode,
        "base_check_output": ((base_check.stdout or "") + (base_check.stderr or ""))[-500:],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
