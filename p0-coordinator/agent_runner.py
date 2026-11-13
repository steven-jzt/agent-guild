"""UI/CLI 共用：真正启动一个 agent，跑完 scope/commit/candidate/evidence。"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from adapters import CodexAppServerAdapter, CodexExecAdapter, DshAdapter, extract_usage
from coordinator import Coordinator, CoordinatorError
from recovery import execution_owner

P0 = Path(__file__).resolve().parent
DEEPSEEK_HOME = P0 / ".tmp" / "codex-deepseek-home"
ZHIPU_HOME = P0 / ".tmp" / "codex-zhipu-home"
DSH_UI_HOME = P0 / ".tmp" / "dsh-ui-home"


def _ensure_dsh_home() -> Path:
    """给 UI 子进程一个独立 DSH_HOME，避开桌面端对 ~/.dsh/profile 的锁。"""
    dst = DSH_UI_HOME / "profiles" / "headless"
    if not dst.exists():
        src = Path.home() / ".dsh" / "profiles" / "headless"
        if src.is_dir():
            shutil.copytree(src, dst)
    return DSH_UI_HOME


def _adapter(agent: str):
    if agent == "dsh":
        return DshAdapter(), "dsh"
    if agent in ("codex", "codex-deepseek"):
        return CodexExecAdapter(check_login=False), "codex-deepseek"
    if agent in ("zhipu", "codex-zhipu"):
        return CodexExecAdapter(check_login=False), "codex-zhipu"
    if agent in ("appserver", "codex-appserver"):
        return CodexAppServerAdapter(), "codex-appserver"
    raise CoordinatorError(f"unknown agent: {agent}")


def _adapter_env(agent: str) -> dict:
    if agent == "dsh":
        return {"DSH_HOME": str(_ensure_dsh_home())}
    if agent in ("codex", "codex-deepseek", "appserver", "codex-appserver"):
        return {"CODEX_HOME": str(DEEPSEEK_HOME)}
    if agent in ("zhipu", "codex-zhipu"):
        return {"CODEX_HOME": str(ZHIPU_HOME)}
    return {}


def run_agent(
    coord: Coordinator,
    task_id: str,
    agent: str,
    prompt: str | None = None,
    check: str | None = None,
    timeout: float = 300,
) -> dict:
    task = coord.get_task(task_id)
    workspaces = coord.list_workspaces(task_id, status="active")
    if not workspaces:
        raise CoordinatorError("task has no active workspace; create one first")
    ws = workspaces[0]
    if not task.get("write_set"):
        raise CoordinatorError("task has no write_set; refuse to run agent")

    check_source = "arg" if check else "none"
    if not check:
        for item in (task.get("checks") or []):
            if isinstance(item, dict) and item.get("type") == "command" and item.get("command"):
                check = item["command"]
                check_source = "task"
                break

    # 平台测试证据必须绑定明确的必需检查项 ID，且类别与 kind 一致（test）。
    platform_check_id = "platform-test"
    platform_check_kind = "test"
    try:
        for item in coord.required_checks_for_task(task_id):
            if item.get("kind") == "test":
                platform_check_id = item["id"]
                platform_check_kind = item["kind"]
                break
    except CoordinatorError:
        pass

    if not prompt:
        prompt = (
            f"Task: {task['title']}. "
            f"Modify only these files: {', '.join(task['write_set'])}. "
            "Do not modify other files. Do not commit. Reply DONE."
        )

    adapter, canonical = _adapter(agent)
    execution = coord.start_execution(task_id, canonical, workspace=ws["path"], meta=execution_owner())
    run = adapter.start(
        task_id, ws["path"], prompt=prompt,
        env=_adapter_env(agent),
    )
    started = time.time()
    try:
        exited = adapter.wait(run["run_id"], timeout=timeout)
        error = str(exited.get("error", "") or "")
    except Exception as exc:  # noqa: BLE001
        exited = {"status": "failed", "exit_code": -1}
        error = str(exc)
    events = adapter.events(run["run_id"])
    usage = extract_usage(events)
    estimated_cost = round(
        float(usage.get("input_tokens", 0)) * 1e-6
        + float(usage.get("output_tokens", 0)) * 4e-6,
        6,
    )
    scope = coord.check_workspace_scope(ws["workspace_id"])
    duration = round(time.time() - started, 1)
    ok = exited.get("status") in ("completed", "exited") and exited.get("exit_code", 1) == 0
    coord.record_run(
        task_id, canonical, duration_s=duration,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cost_usd=estimated_cost,
        status="ok" if ok and scope["ok"] and scope["changed"] else "failed",
        meta={"events": len(events), "usage": usage, "scope": scope, "error": error,
              "estimated_cost_usd": estimated_cost},
        execution_id=execution["execution_id"],
    )
    if not ok or not scope["ok"] or not scope["changed"]:
        coord.finish_execution(
            execution["execution_id"], "failed",
            error or f"exit={exited.get('exit_code')} scope={scope['violations']}",
        )
        return {
            "ok": False, "agent": canonical, "execution_id": execution["execution_id"],
            "seconds": duration, "events": len(events), "usage": usage,
            "changed": scope["changed"], "violations": scope["violations"],
            "error": error or "agent failed or made no change",
            "event_tail": events[-8:],
        }

    coord.finish_execution(execution["execution_id"], "succeeded", "agent done")
    wt = Path(ws["path"])
    for cache in list(wt.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)
    for pyc in list(wt.rglob("*.pyc")):
        try:
            pyc.unlink()
        except OSError:
            pass
    head = coord.commit_workspace(ws["workspace_id"], f"{canonical}: {task['title']}")
    cand = coord.create_candidate_from_workspace(ws["workspace_id"])
    coord.freeze_candidate(cand["candidate_id"])
    out = {
        "ok": True, "agent": canonical, "execution_id": execution["execution_id"],
        "workspace_id": ws["workspace_id"], "candidate_id": cand["candidate_id"],
        "candidate_sha": head, "changed": scope["changed"],
        "seconds": duration, "events": len(events), "usage": usage,
        "estimated_cost_usd": estimated_cost,
        "candidate_status": "building",
        "semantic_changes": len(coord.list_semantic_changes(candidate_id=cand["candidate_id"])),
        "check_source": check_source,
    }

    if check:
        try:
            proc = subprocess.run(
                shlex.split(check), cwd=ws["path"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
            )
            check_exit = proc.returncode
            check_output = ((proc.stdout or "") + (proc.stderr or ""))[-2000:]
            check_error = ""
        except Exception as exc:  # noqa: BLE001
            check_exit = -1
            check_output = f"{type(exc).__name__}: {exc}"
            check_error = check_output
        coord.record_evidence(
            cand["candidate_id"], platform_check_kind, command=check, cwd=ws["path"],
            exit_code=check_exit, output_ref="inline", sha=head,
            execution_id=execution["execution_id"],
            check_id=platform_check_id,
        )
        out["check_exit"] = check_exit
        out["check_output"] = check_output
        out["check_error"] = check_error
        if check_exit == 0:
            coord.mark_candidate_ready(cand["candidate_id"])
            out["candidate_status"] = coord.get_candidate(cand["candidate_id"])["status"]
        else:
            out["candidate_status"] = "validating"
    return out
