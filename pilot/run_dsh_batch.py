"""真实 DSH 批量试点：成功 / 越界 / 无改动，验证 V1 门禁。"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
P0 = ROOT.parent / "p0-coordinator"
if not (P0 / "coordinator.py").exists():
    P0 = ROOT.parent / "05-代码" / "p0-coordinator"
sys.path.insert(0, str(P0))

from adapters import DshAdapter, extract_usage  # noqa: E402
from coordinator import Coordinator  # noqa: E402

BASE = P0 / ".tmp" / "pilot-batch"


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


def prepare_repo(repo: Path) -> str:
    if repo.exists():
        _rmtree(repo)
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "pilot")
    git(repo, "config", "user.email", "pilot@local")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")
    return git(repo, "rev-parse", "HEAD")


def run_case(name: str, prompt: str, evidence: str | None, expect: str) -> dict:
    case = BASE / name
    repo = case / "repo"
    wt = case / "wt"
    base_sha = prepare_repo(repo)

    c = Coordinator(case / "db.sqlite")
    p = c.create_project(name)
    t = c.create_task(p["project_id"], name, ["src/app.py"], repo_id="repo1", kind="code")
    c.reserve_files(t["id"], "repo1", ["src/app.py"])
    e = c.start_execution(t["id"], "dsh", workspace=str(wt))
    ws = c.create_workspace(
        t["id"], "repo1", str(repo), execution_id=e["execution_id"], path=str(wt)
    )

    adapter = DshAdapter()
    run = adapter.start(t["id"], str(wt), prompt=prompt)
    started = time.time()
    try:
        exited = adapter.wait(run["run_id"], timeout=240)
    except Exception as exc:  # noqa: BLE001
        events = adapter.events(run["run_id"])
        c.record_run(
            t["id"], "dsh", duration_s=round(time.time() - started, 1),
            input_tokens=extract_usage(events).get("input_tokens", 0),
            output_tokens=extract_usage(events).get("output_tokens", 0),
            status="failed", meta={"error": str(exc)}, execution_id=e["execution_id"],
        )
        c.finish_execution(e["execution_id"], "failed", str(exc))
        return {"case": name, "result": "timeout", "error": str(exc)}

    events = adapter.events(run["run_id"])
    usage = extract_usage(events)
    scope = c.check_workspace_scope(ws["workspace_id"])
    c.record_run(
        t["id"], "dsh", duration_s=round(time.time() - started, 1),
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        status="ok" if exited["exit_code"] == 0 else "failed",
        meta={"events": len(events), "usage": usage, "scope": scope},
        execution_id=e["execution_id"],
    )

    out = {
        "case": name,
        "expect": expect,
        "exit_code": exited["exit_code"],
        "changed": scope["changed"],
        "violations": scope["violations"],
        "usage": usage,
        "final_text": next((x.get("text") for x in reversed(events)
                            if x.get("type") == "final"), None),
    }

    if exited["exit_code"] != 0:
        c.finish_execution(e["execution_id"], "failed", "dsh exit != 0")
        out.update({"result": "failed", "lifecycle": c.get_task(t["id"])["lifecycle"]})
        return out

    if expect == "scope":
        c.finish_execution(e["execution_id"], "failed", "scope violation expected")
        out["result"] = "blocked" if not scope["ok"] else "false_negative"
        out["lifecycle"] = c.get_task(t["id"])["lifecycle"]
        return out

    if expect == "nochange":
        c.finish_execution(e["execution_id"], "failed", "expected no change")
        out["result"] = "blocked" if not scope["changed"] else "unexpected_change"
        out["lifecycle"] = c.get_task(t["id"])["lifecycle"]
        return out

    if not scope["ok"]:
        c.finish_execution(e["execution_id"], "failed", f"scope: {scope['violations']}")
        out.update({"result": "scope_violation", "lifecycle": c.get_task(t["id"])["lifecycle"]})
        return out

    c.finish_execution(e["execution_id"], "succeeded", "dsh done")
    head = c.commit_workspace(ws["workspace_id"], name)
    cand = c.create_candidate_from_workspace(ws["workspace_id"])
    c.freeze_candidate(cand["candidate_id"])
    proc = subprocess.run(
        [sys.executable, "-c", evidence or "pass"],
        cwd=str(wt), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    c.record_evidence(
        cand["candidate_id"], "test", command=evidence or "pass", cwd=str(wt),
        exit_code=proc.returncode, output_ref="inline", sha=head,
        execution_id=e["execution_id"],
    )
    if proc.returncode != 0:
        out.update({"result": "evidence_failed", "stderr": proc.stderr[-800:]})
        return out
    c.mark_candidate_ready(cand["candidate_id"])
    c.approve_candidate(cand["candidate_id"], "reviewer", name)
    attempt = c.start_merge(cand["candidate_id"], base_sha, "reviewer")
    c.finish_merge(attempt["attempt_id"], "succeeded", head)
    task = c.get_task(t["id"])
    out.update({
        "result": "accepted" if task["lifecycle"] == "accepted" else "not_accepted",
        "candidate_sha": head,
        "lifecycle": task["lifecycle"],
        "status": task["status"],
    })
    return out


CASES = [
    ("success_sub", (
        "Modify ONLY src/app.py: add `def sub(a, b): return a - b`. "
        "Do not modify any other file. Do not commit. Reply DONE."
    ), "import sys; sys.path.insert(0, 'src'); import app; assert app.sub(5, 2) == 3", "success"),
    ("scope_other", (
        "Create a new file `other.txt` containing the word oops. "
        "Do not modify src/app.py. Do not commit. Reply DONE."
    ), None, "scope"),
    ("no_change", (
        "Do not modify any file. Do not run commands. Reply DONE."
    ), None, "nochange"),
]


def main() -> int:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY is required", file=sys.stderr)
        return 2
    if BASE.exists():
        _rmtree(BASE)
    results = [run_case(*case) for case in CASES]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(r["result"] in {"accepted", "blocked"} for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
