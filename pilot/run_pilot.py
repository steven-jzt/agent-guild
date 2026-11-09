"""B 内部试点 dry-run：真实 worktree + Candidate + Evidence + Merge 规则闭环。

注意：这里的 finish_merge 是规则层合入，不会修改原仓库；真实 Git 合入尚未实现。
"""

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

from adapters import CommandAdapter  # noqa: E402
from coordinator import Coordinator  # noqa: E402

BASE = P0 / ".tmp" / "pilot"
REPO = BASE / "repo"
DB = BASE / "db.sqlite"
WT = BASE / "wt"
AGENT = ROOT / "agent_edit.py"


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
    (REPO / "src" / "app.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    git(REPO, "init", "-b", "main")
    git(REPO, "config", "user.name", "pilot")
    git(REPO, "config", "user.email", "pilot@local")
    git(REPO, "add", "-A")
    git(REPO, "commit", "-m", "base")
    base_sha = git(REPO, "rev-parse", "HEAD")

    c = Coordinator(DB)
    p = c.create_project("pilot-dry-run")
    t = c.create_task(
        p["project_id"], "add mul", ["src/app.py"], repo_id="repo1", kind="code"
    )
    c.reserve_files(t["id"], "repo1", ["src/app.py"])

    e = c.start_execution(t["id"], "generic-cli")
    ws = c.create_workspace(
        t["id"], "repo1", str(REPO), execution_id=e["execution_id"], path=str(WT)
    )

    adapter = CommandAdapter("generic-cli", [sys.executable, str(AGENT), "src/app.py"])
    run = adapter.start(t["id"], str(WT))
    exited = adapter.wait(run["run_id"], timeout=60)
    c.finish_execution(e["execution_id"], "succeeded", f"agent exit={exited['exit_code']}")

    scope = c.check_workspace_scope(ws["workspace_id"])
    if not scope["ok"]:
        raise RuntimeError(f"scope violation: {scope['violations']}")

    head = c.commit_workspace(ws["workspace_id"], "add mul")
    cand = c.create_candidate_from_workspace(ws["workspace_id"])
    c.freeze_candidate(cand["candidate_id"])

    check_code = (
        "import sys; sys.path.insert(0, 'src'); import app; "
        "assert app.mul(2, 3) == 6"
    )
    proc = subprocess.run(
        [sys.executable, "-c", check_code],
        cwd=str(WT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    evidence = c.record_evidence(
        cand["candidate_id"], "test", command=check_code, cwd=str(WT),
        exit_code=proc.returncode, output_ref="inline", sha=head,
        execution_id=e["execution_id"],
    )
    if proc.returncode != 0:
        raise RuntimeError(f"evidence failed: {proc.stderr}")

    c.mark_candidate_ready(cand["candidate_id"])
    c.approve_candidate(cand["candidate_id"], "reviewer", "dry-run lgtm")
    attempt = c.start_merge(cand["candidate_id"], base_sha, "reviewer")
    c.finish_merge(attempt["attempt_id"], "succeeded", head)

    task = c.get_task(t["id"])
    candidate = c.get_candidate(cand["candidate_id"])
    out = {
        "dry_run": True,
        "note": "rule-layer merge only; original repo is NOT modified",
        "project_id": p["project_id"],
        "task_id": t["id"],
        "execution_id": e["execution_id"],
        "workspace_id": ws["workspace_id"],
        "base_sha": base_sha,
        "candidate_sha": head,
        "changed": scope["changed"],
        "evidence_exit": proc.returncode,
        "candidate_status": candidate["status"],
        "task_status": task["status"],
        "task_lifecycle": task["lifecycle"],
        "evidence_id": evidence["evidence_id"],
    }
    c.remove_workspace(ws["workspace_id"])
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if candidate["status"] == "merged" and task["lifecycle"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
