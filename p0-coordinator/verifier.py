"""执行任务的 checks，并把结论写进 verifications。

支持：
- file_exists: {path}
- file_contains: {path, text}
- file_regex: {path, pattern}
- command: {cmd, cwd?, timeout?}（默认禁用，需 allow_command=True）

用法：
    python verifier.py --db data/coordinator.db --task T-xxxx
    python verifier.py --db data/coordinator.db --task T-xxxx --allow-command
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from coordinator import Coordinator, CoordinatorError

DEFAULT_ALLOWED_COMMANDS = {"python", "python3", "py", "pytest", "node", "npm", "git"}
ALLOWED_ENV = {
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
    "HOME", "USERPROFILE", "PYTHONPATH", "PYTHONHOME", "LANG", "LC_ALL",
}


def _safe_path(root: Path, raw: str) -> Path:
    path = (root / raw).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        raise CoordinatorError(f"path escapes root: {raw}") from None
    return path


def run_checks(
    task: dict,
    root: str = ".",
    allow_command: bool = False,
    allowed_commands: Iterable[str] | None = None,
) -> list[dict]:
    base = Path(root).resolve()
    allowed = {
        Path(x).name.lower().removesuffix(".exe")
        for x in (allowed_commands if allowed_commands is not None else DEFAULT_ALLOWED_COMMANDS)
    }
    results: list[dict] = []
    for index, check in enumerate(task.get("checks") or []):
        kind = check.get("type")
        result: dict[str, Any] = {"index": index, "type": kind, "passed": False, "detail": ""}
        try:
            if kind == "file_exists":
                path = _safe_path(base, check["path"])
                result["passed"] = path.is_file()
                result["detail"] = str(path)
            elif kind == "file_contains":
                path = _safe_path(base, check["path"])
                text = path.read_text(encoding="utf-8") if path.is_file() else ""
                needle = check["text"]
                result["passed"] = needle in text
                result["detail"] = f"{path} contains {needle!r}" if result["passed"] else f"missing {needle!r}"
            elif kind == "file_regex":
                path = _safe_path(base, check["path"])
                text = path.read_text(encoding="utf-8") if path.is_file() else ""
                result["passed"] = re.search(check["pattern"], text, re.MULTILINE) is not None
                result["detail"] = f"{path} =~ {check['pattern']!r}"
            elif kind == "command":
                if not allow_command:
                    result["detail"] = "command checks disabled (use --allow-command)"
                else:
                    argv = shlex.split(check["cmd"])
                    executable = Path(argv[0]).name.lower().removesuffix(".exe") if argv else ""
                    if not executable or executable not in allowed:
                        result["detail"] = f"command not allowed: {executable!r} (allowed={sorted(allowed)})"
                    else:
                        cwd = _safe_path(base, check.get("cwd", "."))
                        env = {
                            key: value
                            for key, value in os.environ.items()
                            if key.upper() in ALLOWED_ENV
                        }
                        proc = subprocess.run(
                            argv,
                            shell=False,
                            cwd=str(cwd),
                            capture_output=True,
                            text=True,
                            timeout=float(check.get("timeout", 60)),
                            env=env,
                        )
                        result["passed"] = proc.returncode == 0
                        output = (proc.stdout or proc.stderr or "").strip()
                        limit = int(check.get("max_output_chars", 4000))
                        if len(output) > limit:
                            output = output[:limit] + f"...[truncated {len(output) - limit} chars]"
                        result["detail"] = output
            else:
                result["detail"] = f"unknown check type: {kind}"
        except Exception as exc:  # noqa: BLE001
            result["detail"] = f"{type(exc).__name__}: {exc}"
        results.append(result)
    return results


def auto_verify(
    coord: Coordinator,
    task_id: str,
    verifier: str = "auto",
    root: str = ".",
    allow_command: bool = False,
    allowed_commands: Iterable[str] | None = None,
) -> dict:
    task = coord.get_task(task_id)
    if task["status"] != "verification":
        raise CoordinatorError(f"task is not awaiting verification: {task['status']}")
    checks = task.get("checks") or []
    if not checks:
        raise CoordinatorError("task has no checks; define checks at create_task")
    results = run_checks(task, root, allow_command, allowed_commands)
    passed = all(r["passed"] for r in results)
    score = round(sum(1 for r in results if r["passed"]) / len(results), 3)
    notes = "; ".join(f"{r['type']}={'ok' if r['passed'] else 'fail'}" for r in results)
    updated = coord.record_verification(task_id, verifier, passed, score, notes)
    return {
        "task_id": task_id,
        "passed": passed,
        "score": score,
        "checks": results,
        "status": updated["status"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/coordinator.db")
    ap.add_argument("--task", required=True)
    ap.add_argument("--verifier", default="auto")
    ap.add_argument("--root", default=".")
    ap.add_argument("--allow-command", action="store_true")
    ap.add_argument("--allowed-commands", default=",".join(sorted(DEFAULT_ALLOWED_COMMANDS)))
    args = ap.parse_args()

    allowed = [x.strip() for x in args.allowed_commands.split(",") if x.strip()]
    try:
        result = auto_verify(
            Coordinator(args.db), args.task, args.verifier, args.root,
            args.allow_command, allowed,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["passed"] else 1
    except CoordinatorError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
