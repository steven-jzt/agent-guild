"""Python semantic diff: 从 Git 变更中提取顶层符号/类方法变化。

MVP 只做确定性事实，不做 LLM 判断。
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path


def _signature(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = ast.unparse(node.args)
        returns = ast.unparse(node.returns) if node.returns is not None else ""
        decorators = ",".join(ast.unparse(d) for d in node.decorator_list)
        suffix = f"->{returns}" if returns else ""
        return f"({args}){suffix}" + (f"@{decorators}" if decorators else "")
    if isinstance(node, ast.ClassDef):
        bases = ",".join(ast.unparse(b) for b in node.bases)
        return f"class({bases})"
    return ""


def _collect(tree: ast.Module, path: str) -> dict[str, dict]:
    result: dict[str, dict] = {}

    def add(obj: str, kind: str, node: ast.AST):
        body_node = ast.Module(body=getattr(node, "body", []), type_ignores=[])
        result[obj] = {
            "object": obj,
            "kind": kind,
            "signature": _signature(node),
            "body": ast.dump(body_node, include_attributes=False),
            "path": path,
        }

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node.name, "function", node)
        elif isinstance(node, ast.ClassDef):
            add(node.name, "class", node)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(f"{node.name}.{child.name}", "method", child)
    return result


def _compat(change_type: str) -> str:
    if change_type in ("removed", "signature_changed"):
        return "breaking"
    if change_type == "added":
        return "compatible"
    return "unknown"


def extract_changes(path: str, before: str, after: str) -> list[dict]:
    try:
        old_tree = ast.parse(before)
        new_tree = ast.parse(after)
    except SyntaxError as exc:
        return [{
            "path": path,
            "object": "<module>",
            "change_type": "parse_error",
            "compatibility": "unknown",
            "before_value": "",
            "after_value": f"{type(exc).__name__}: {exc}",
        }]

    old = _collect(old_tree, path)
    new = _collect(new_tree, path)
    changes: list[dict] = []
    for obj in sorted(set(old) | set(new)):
        if obj not in old:
            changes.append({
                "path": path, "object": obj, "change_type": "added",
                "compatibility": _compat("added"),
                "before_value": "", "after_value": new[obj]["signature"],
            })
        elif obj not in new:
            changes.append({
                "path": path, "object": obj, "change_type": "removed",
                "compatibility": _compat("removed"),
                "before_value": old[obj]["signature"], "after_value": "",
            })
        elif old[obj]["signature"] != new[obj]["signature"]:
            changes.append({
                "path": path, "object": obj, "change_type": "signature_changed",
                "compatibility": _compat("signature_changed"),
                "before_value": old[obj]["signature"],
                "after_value": new[obj]["signature"],
            })
        elif old[obj]["body"] != new[obj]["body"]:
            changes.append({
                "path": path, "object": obj, "change_type": "body_changed",
                "compatibility": _compat("body_changed"),
                "before_value": old[obj]["signature"],
                "after_value": new[obj]["signature"],
            })
    return changes


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return proc.stdout


def git_changes(repo_path: str, base_sha: str, candidate_sha: str) -> list[dict]:
    repo = Path(repo_path).resolve()
    status = _git(repo, "diff", "--name-status", base_sha, candidate_sha, "--", "*.py")
    changes: list[dict] = []
    for line in status.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code, path = parts[0], parts[-1]
        if not path.endswith(".py"):
            continue
        before = ""
        after = ""
        if not code.startswith("A"):
            try:
                before = _git(repo, "show", f"{base_sha}:{path}")
            except RuntimeError:
                before = ""
        if not code.startswith("D"):
            try:
                after = _git(repo, "show", f"{candidate_sha}:{path}")
            except RuntimeError:
                after = ""
        changes.extend(extract_changes(path, before, after))
    return changes
