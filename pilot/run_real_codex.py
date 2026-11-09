"""真实 Codex（DeepSeek provider）V1 端到端。

不需要 OpenAI 账号：CODEX_HOME 指向 DeepSeek 官方 setup 生成的隔离配置，
模型走 deepseek-flash。
"""

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

from adapters import CodexExecAdapter, extract_usage  # noqa: E402
from coordinator import Coordinator  # noqa: E402

BASE = P0 / ".tmp" / "pilot-codex"
REPO = BASE / "repo"
DB = BASE / "db.sqlite"
WT = BASE / "wt"
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
        print("DEEPSEEK_API_KEY is required", file=sys.stderr)
        return 2
    if not (CODEX_HOME / "config.toml").exists():
        print(f"missing Codex DeepSeek config: {CODEX_HOME}", file=sys.stderr)
        return 2
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
    p = c.create_project("pilot-codex")
    t = c.create_task(
        p["project_id"], "add mul via Codex", ["src/app.py"], repo_id="repo1", kind="code"
    )
    c.reserve_files(t["id"], "repo1", ["src/app.py"])
    e = c.start_execution(t["id"], "codex", workspace=str(WT))
    ws = c.create_workspace(
        t["id"], "repo1", str(REPO), execution_id=e["execution_id"], path=str(WT)
    )

    prompt = (
        "You are working in a git worktree. Modify ONLY src/app.py: "
        "add a function `def mul(a, b): return a * b`. "
        "Do not modify any other file. Do not commit. Reply DONE when finished."
    )
    adapter = CodexExecAdapter(check_login=False)
    run = adapter.start(
        t["id"], str(WT), prompt=prompt,
        env={"CODEX_HOME": str(CODEX_HOME), "DEEPSEEK_API_KEY": os.environ["DEEPSEEK_API_KEY"]},
    )
    started = time.time()
    try:
        exited = adapter.wait(run["run_id"], timeout=300)
    except Exception as exc:  # noqa: BLE001
        events = adapter.events(run["run_id"])
        c.record_run(
            t["id"], "codex", duration_s=round(time.time() - started, 1),
            input_tokens=extract_usage(events).get("input_tokens", 0),
            output_tokens=extract_usage(events).get("output_tokens", 0),
            status="failed", meta={"error": str(exc)}, execution_id=e["execution_id"],
        )
        c.finish_execution(e["execution_id"], "failed", str(exc))
        print(json.dumps({"error": str(exc), "events": events[-5:]},
                         ensure_ascii=False, indent=2)[-4000:])
        return 1

    events = adapter.events(run["run_id"])
    usage = extract_usage(events)
    scope = c.check_workspace_scope(ws["workspace_id"])
    c.record_run(
        t["id"], "codex", duration_s=round(time.time() - started, 1),
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        status="ok" if exited["exit_code"] == 0 else "failed",
        meta={"events": len(events), "usage": usage, "scope": scope},
        execution_id=e["execution_id"],
    )
    if exited["exit_code"] != 0 or not scope["ok"] or not scope["changed"]:
        c.finish_execution(
            e["execution_id"], "failed",
            f"exit={exited['exit_code']} violations={scope['violations']} "
            f"changed={scope['changed']}",
        )
        print(json.dumps({
            "exit_code": exited["exit_code"], "scope": scope,
            "events": events[-12:],
        }, ensure_ascii=False, indent=2)[-6000:])
        return 1

    c.finish_execution(e["execution_id"], "succeeded", "codex exec done")
    head = c.commit_workspace(ws["workspace_id"], "add mul via Codex")
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
    c.record_evidence(
        cand["candidate_id"], "test", command=check_code, cwd=str(WT),
        exit_code=proc.returncode, output_ref="inline", sha=head,
        execution_id=e["execution_id"],
    )
    if proc.returncode != 0:
        print(json.dumps({"error": "evidence failed", "stderr": proc.stderr},
                         ensure_ascii=False), file=sys.stderr)
        return 1

    c.mark_candidate_ready(cand["candidate_id"])
    c.approve_candidate(cand["candidate_id"], "reviewer", "real Codex pilot")
    attempt = c.start_merge(cand["candidate_id"], base_sha, "reviewer")
    c.finish_merge(attempt["attempt_id"], "succeeded", head)

    task = c.get_task(t["id"])
    candidate = c.get_candidate(cand["candidate_id"])
    out = {
        "real_codex": True,
        "provider": "deepseek",
        "codex_home": str(CODEX_HOME),
        "base_sha": base_sha,
        "candidate_sha": head,
        "changed": scope["changed"],
        "usage": usage,
        "events": len(events),
        "candidate_status": candidate["status"],
        "task_status": task["status"],
        "task_lifecycle": task["lifecycle"],
        "final_text": next((
            (x.get("item") or {}).get("text")
            for x in reversed(events)
            if x.get("type") == "item.completed"
            and (x.get("item") or {}).get("type") == "agent_message"
        ), None),
    }
    c.remove_workspace(ws["workspace_id"])
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if candidate["status"] == "merged" and task["lifecycle"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
