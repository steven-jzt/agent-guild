"""真实小仓库多 agent：sample-shop，三个 agent 各改一个模块。"""

from __future__ import annotations

import concurrent.futures
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

from adapters import CodexExecAdapter, DshAdapter, extract_usage  # noqa: E402
from coordinator import Coordinator  # noqa: E402

BASE = P0 / ".tmp" / "pilot-sample"
REPO = BASE / "repo"
DB = BASE / "db.sqlite"
SOURCE = ROOT / "sample-repo"
DEEPSEEK_HOME = P0 / ".tmp" / "codex-deepseek-home"
ZHIPU_HOME = P0 / ".tmp" / "codex-zhipu-home"


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


SPECS = [
    {
        "name": "dsh",
        "file": "src/shop/pricing.py",
        "prompt": (
            "Modify ONLY src/shop/pricing.py: add "
            "`def bulk_discount(total, percent): return round(total * (1 - percent / 100), 2)`. "
            "Do not modify tests or any other file. Do not commit. Reply DONE."
        ),
        "evidence": (
            "import sys; sys.path.insert(0, 'src'); "
            "from shop.pricing import bulk_discount; assert bulk_discount(100, 10) == 90.0"
        ),
        "adapter": "dsh",
    },
    {
        "name": "codex-deepseek",
        "file": "src/shop/cart.py",
        "prompt": (
            "Modify ONLY src/shop/cart.py: add "
            "`def cart_total(items): return sum(i['price'] * i['qty'] for i in items)`. "
            "Do not modify tests or any other file. Do not commit. Reply DONE."
        ),
        "evidence": (
            "import sys; sys.path.insert(0, 'src'); "
            "from shop.cart import cart_total; "
            "assert cart_total([{'price': 2, 'qty': 3}, {'price': 5, 'qty': 1}]) == 11"
        ),
        "adapter": "codex-deepseek",
    },
    {
        "name": "codex-zhipu",
        "file": "src/shop/formatting.py",
        "prompt": (
            "Modify ONLY src/shop/formatting.py: add "
            "`def format_price(cents): return f'${cents / 100:,.2f}'`. "
            "Do not modify tests or any other file. Do not commit. Reply DONE."
        ),
        "evidence": (
            "import sys; sys.path.insert(0, 'src'); "
            "from shop.formatting import format_price; assert format_price(123456) == '$1,234.56'"
        ),
        "adapter": "codex-zhipu",
    },
]


def prepare_repo() -> str:
    if BASE.exists():
        _rmtree(BASE)
    shutil.copytree(SOURCE, REPO, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if not (REPO / ".git").exists():
        git(REPO, "init", "-b", "main")
        git(REPO, "config", "user.name", "pilot")
        git(REPO, "config", "user.email", "pilot@local")
        git(REPO, "add", "-A")
        git(REPO, "commit", "-m", "base")
    git(REPO, "config", "user.name", "pilot")
    git(REPO, "config", "user.email", "pilot@local")
    return git(REPO, "rev-parse", "HEAD")


def make_adapter(kind: str):
    if kind == "dsh":
        return DshAdapter()
    return CodexExecAdapter(check_login=False)


def main() -> int:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY required", file=sys.stderr)
        return 2
    if not (ZHIPU_HOME / "config.toml").exists():
        print(f"missing Zhipu CODEX_HOME: {ZHIPU_HOME}", file=sys.stderr)
        return 2
    base_sha = prepare_repo()
    c = Coordinator(DB)
    p = c.create_project("pilot-sample")
    tasks = {}
    for spec in SPECS:
        t = c.create_task(
            p["project_id"], f"{spec['name']} {spec['file']}",
            [spec["file"]], repo_id="shop", kind="code",
        )
        c.reserve_files(t["id"], "shop", [spec["file"]])
        e = c.start_execution(t["id"], spec["name"], workspace=str(BASE / f"wt-{spec['name']}"))
        ws = c.create_workspace(
            t["id"], "shop", str(REPO), execution_id=e["execution_id"],
            path=str(BASE / f"wt-{spec['name']}"),
        )
        tasks[spec["name"]] = {"task": t, "execution": e, "workspace": ws, "spec": spec}

    started = time.time()
    runs = {}
    for name, item in tasks.items():
        spec = item["spec"]
        adapter = make_adapter(spec["adapter"])
        env = {}
        if spec["adapter"] == "codex-deepseek":
            env = {"CODEX_HOME": str(DEEPSEEK_HOME)}
        elif spec["adapter"] == "codex-zhipu":
            env = {"CODEX_HOME": str(ZHIPU_HOME)}
        run = adapter.start(
            item["task"]["id"], str(BASE / f"wt-{name}"),
            prompt=spec["prompt"], env=env,
        )
        runs[name] = {"adapter": adapter, "run": run, "started": time.time()}

    def wait_one(name: str) -> dict:
        item = runs[name]
        try:
            exited = item["adapter"].wait(item["run"]["run_id"], timeout=300)
            error = None
        except Exception as exc:  # noqa: BLE001
            exited = {"exit_code": -1}
            error = str(exc)
        events = item["adapter"].events(item["run"]["run_id"])
        return {
            "name": name, "exit": exited, "error": error, "events": events,
            "usage": extract_usage(events),
            "seconds": round(time.time() - item["started"], 1),
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(wait_one, [s["name"] for s in SPECS]))

    out = []
    for r in results:
        item = tasks[r["name"]]
        task, e, ws = item["task"], item["execution"], item["workspace"]
        scope = c.check_workspace_scope(ws["workspace_id"])
        c.record_run(
            task["id"], r["name"], duration_s=r["seconds"],
            input_tokens=r["usage"].get("input_tokens", 0),
            output_tokens=r["usage"].get("output_tokens", 0),
            status="ok" if r["exit"]["exit_code"] == 0 else "failed",
            meta={"events": len(r["events"]), "usage": r["usage"], "scope": scope},
            execution_id=e["execution_id"],
        )
        row = {
            "agent": r["name"], "seconds": r["seconds"],
            "exit_code": r["exit"]["exit_code"], "error": r["error"],
            "changed": scope["changed"], "violations": scope["violations"],
            "usage": r["usage"],
        }
        if r["exit"]["exit_code"] != 0 or not scope["ok"] or not scope["changed"]:
            c.finish_execution(e["execution_id"], "failed", f"exit/scope: {scope}")
            row["result"] = "failed"
            row["lifecycle"] = c.get_task(task["id"])["lifecycle"]
            out.append(row)
            continue
        c.finish_execution(e["execution_id"], "succeeded", f"{r['name']} done")
        head = c.commit_workspace(ws["workspace_id"], f"{r['name']} change")
        cand = c.create_candidate_from_workspace(ws["workspace_id"])
        c.freeze_candidate(cand["candidate_id"])
        proc = subprocess.run(
            [sys.executable, "-c", item["spec"]["evidence"]],
            cwd=str(BASE / f"wt-{r['name']}"), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        c.record_evidence(
            cand["candidate_id"], "test", command=item["spec"]["evidence"],
            cwd=str(BASE / f"wt-{r['name']}"), exit_code=proc.returncode,
            output_ref="inline", sha=head, execution_id=e["execution_id"],
        )
        if proc.returncode != 0:
            row.update({"result": "evidence_failed", "stderr": proc.stderr[-500:]})
            out.append(row)
            continue
        c.mark_candidate_ready(cand["candidate_id"])
        c.approve_candidate(cand["candidate_id"], "reviewer", r["name"])
        attempt = c.start_merge(cand["candidate_id"], base_sha, "reviewer")
        c.finish_merge(attempt["attempt_id"], "succeeded", head)
        t = c.get_task(task["id"])
        row.update({
            "result": "accepted" if t["lifecycle"] == "accepted" else "not_accepted",
            "candidate_sha": head, "lifecycle": t["lifecycle"], "status": t["status"],
        })
        out.append(row)

    print(json.dumps({
        "repo": str(SOURCE),
        "agents": len(SPECS),
        "wall_seconds": round(time.time() - started, 1),
        "results": out,
    }, ensure_ascii=False, indent=2))
    return 0 if all(r.get("result") == "accepted" for r in out) else 1


if __name__ == "__main__":
    raise SystemExit(main())
