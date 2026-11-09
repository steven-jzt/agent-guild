"""Codex App Server adapter 的 V1 端到端：JSON-RPC → 改文件 → 真实 cherry-pick。"""

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

from adapters import CodexAppServerAdapter, extract_usage  # noqa: E402
from coordinator import Coordinator  # noqa: E402

BASE = P0 / ".tmp" / "pilot-appserver"
REPO = BASE / "repo"
DB = BASE / "db.sqlite"
WT = BASE / "wt"
INTEGRATION = BASE / "integration"
CODEX_HOME = P0 / ".tmp" / "codex-deepseek-home"


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
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY required", file=sys.stderr)
        return 2
    if BASE.exists():
        _rmtree(BASE)
    (REPO / "src").mkdir(parents=True)
    (REPO / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    git(REPO, "init", "-b", "main")
    git(REPO, "config", "user.name", "pilot")
    git(REPO, "config", "user.email", "pilot@local")
    git(REPO, "add", "-A")
    git(REPO, "commit", "-m", "base")
    base_sha = git(REPO, "rev-parse", "HEAD")

    c = Coordinator(DB)
    p = c.create_project("pilot-appserver")
    t = c.create_task(p["project_id"], "add mul via app-server", ["src/app.py"], repo_id="repo1")
    c.reserve_files(t["id"], "repo1", ["src/app.py"])
    e = c.start_execution(t["id"], "codex-appserver", workspace=str(WT))
    ws = c.create_workspace(t["id"], "repo1", str(REPO), execution_id=e["execution_id"], path=str(WT))

    prompt = (
        "Modify ONLY src/app.py: add `def mul(a, b): return a * b`. "
        "Do not modify any other file. Do not commit. Reply DONE."
    )
    adapter = CodexAppServerAdapter()
    run = adapter.start(
        t["id"], str(WT), prompt=prompt,
        env={"CODEX_HOME": str(CODEX_HOME), "DEEPSEEK_API_KEY": os.environ["DEEPSEEK_API_KEY"]},
    )
    exited = adapter.wait(run["run_id"], timeout=300)
    events = adapter.events(run["run_id"])
    usage = extract_usage(events)
    scope = c.check_workspace_scope(ws["workspace_id"])
    c.record_run(
        t["id"], "codex-appserver", duration_s=0,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        status="ok" if exited["status"] == "completed" else "failed",
        meta={"events": len(events), "usage": usage, "scope": scope},
        execution_id=e["execution_id"],
    )
    if exited["status"] != "completed" or not scope["ok"] or not scope["changed"]:
        c.finish_execution(e["execution_id"], "failed", f"appserver {exited} scope {scope}")
        print(json.dumps({"error": "appserver failed", "exit": exited, "scope": scope,
                          "events": [x.get("method") for x in events][-15:]},
                         ensure_ascii=False, indent=2))
        return 1

    c.finish_execution(e["execution_id"], "succeeded", "appserver done")
    head = c.commit_workspace(ws["workspace_id"], "appserver change")
    cand = c.create_candidate_from_workspace(ws["workspace_id"])
    c.freeze_candidate(cand["candidate_id"])
    check = [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'src'); import app; assert app.mul(2, 3) == 6"]
    c.record_evidence(cand["candidate_id"], "test", command=" ".join(check[:3]),
                      cwd=str(WT), exit_code=0, output_ref="inline", sha=head,
                      execution_id=e["execution_id"])
    c.mark_candidate_ready(cand["candidate_id"])
    c.approve_candidate(cand["candidate_id"], "reviewer", "appserver pilot")
    attempt = c.start_merge(cand["candidate_id"], base_sha, "reviewer")
    merged = c.merge_candidate_git(cand["candidate_id"], str(REPO), str(INTEGRATION),
                                   check_command=check)
    final = c.finish_merge(attempt["attempt_id"], "succeeded", merged["result_sha"])

    task = c.get_task(t["id"])
    candidate = c.get_candidate(cand["candidate_id"])
    out = {
        "real_appserver": True,
        "candidate_sha": head,
        "integration_sha": merged["result_sha"],
        "usage": usage,
        "events": len(events),
        "candidate_status": candidate["status"],
        "merge_status": final["status"],
        "task_lifecycle": task["lifecycle"],
        "task_status": task["status"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if task["lifecycle"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
