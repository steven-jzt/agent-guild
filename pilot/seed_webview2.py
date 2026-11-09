"""给 WebView2 UI 造一个 awaiting_human 的 Candidate。"""

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

BASE = P0 / ".tmp" / "webview2-demo"
REPO = BASE / "repo"
DB = BASE / "db.sqlite"
WT = BASE / "wt"
INTEGRATION = BASE / "integration"


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
    p = c.create_project("webview2-demo")
    t = c.create_task(p["project_id"], "UI approval demo", ["src/app.py"], repo_id="repo1")
    c.reserve_files(t["id"], "repo1", ["src/app.py"])
    e = c.start_execution(t["id"], "webview2-ui", workspace=str(WT))
    ws = c.create_workspace(t["id"], "repo1", str(REPO), execution_id=e["execution_id"], path=str(WT))
    (WT / "src" / "app.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    head = c.commit_workspace(ws["workspace_id"], "add mul")
    c.finish_execution(e["execution_id"], "succeeded", "ui demo")
    cand = c.create_candidate_from_workspace(ws["workspace_id"])
    c.freeze_candidate(cand["candidate_id"])
    code = "import sys; sys.path.insert(0, 'src'); import app; assert app.mul(2, 3) == 6"
    c.record_evidence(
        cand["candidate_id"], "test", command=code, cwd=str(WT),
        exit_code=0, output_ref="inline", sha=head, execution_id=e["execution_id"],
    )
    c.mark_candidate_ready(cand["candidate_id"])
    print(json.dumps({
        "db": str(DB),
        "candidate_id": cand["candidate_id"],
        "task_id": t["id"],
        "candidate_sha": head,
        "repo": str(REPO),
        "worktree_for_merge": str(INTEGRATION),
        "candidate_status": c.get_candidate(cand["candidate_id"])["status"],
        "task_lifecycle": c.get_task(t["id"])["lifecycle"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
