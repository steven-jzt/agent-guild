"""Rebuild the integrated project archive: docs + source + evidence, no tmp/vendor/node_modules.

Output: AgentGuild-整合版-重建.zip
"""

from __future__ import annotations

import os
import shutil
import stat
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARE = ROOT / "共享"
STAGE = SHARE / "AgentGuild-整合版-重建"
ZIP_BASE = ROOT / "AgentGuild-整合版-重建"

EXCLUDE_NAMES = {
    ".git", ".tmp", "data", "__pycache__", ".pytest_cache", ".venv",
    "node_modules", "vendor", ".nuget",
}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".db", ".db-journal", ".zip", ".log")


def ignore(_dir: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        if name in EXCLUDE_NAMES:
            ignored.add(name)
        elif name.endswith(EXCLUDE_SUFFIXES):
            ignored.add(name)
    return ignored


def _rmtree(path: Path) -> None:
    def onexc(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onexc=onexc)


def copy_tree(src: Path, dst: Path) -> None:
    if src.exists():
        shutil.copytree(src, dst, ignore=ignore)


def main() -> int:
    if STAGE.exists():
        _rmtree(STAGE)
    STAGE.mkdir(parents=True)

    # Unified documentation tree.
    copy_tree(ROOT / "docs", STAGE / "docs")

    # Core code and experiments.
    for name in [
        "agent-guild",
        "p0-coordinator",
        "experiments",
        "pilot",
    ]:
        copy_tree(ROOT / name, STAGE / name)

    # Shared docs/scripts, but not the old staged package or zip.
    shared_dst = STAGE / "共享"
    shared_dst.mkdir()
    for path in sorted(SHARE.iterdir()):
        if path.is_file() and path.suffix in (".md", ".py"):
            shutil.copy2(path, shared_dst / path.name)

    # Remove nested old packages if any slipped through.
    for nested in STAGE.rglob("*.zip"):
        nested.unlink()

    files = [p for p in STAGE.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    manifest = [
        "# AgentGuild 整合版（重建）",
        "",
        f"构建时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"- 文件数：{len(files)}",
        f"- 未压缩大小：{total / 1024 / 1024:.2f} MB",
        "- 测试基线：p0-coordinator 91 tests；agent-guild 17 tests（本脚本不自动运行）",
        "- 包含：docs/ 统一文档、M0 接入工程（agent-guild）、协调内核（p0-coordinator）、实验（experiments）、试点（pilot）、共享脚本",
        "- 包含：webview2/bin 预编译控制台（Windows）",
        "",
        "## 排除项",
        "",
        "- `.tmp/`：运行数据库、worktree、demo 仓库",
        "- `p0-coordinator/electron/node_modules/`：前端依赖，按 package.json 安装",
        "- `p0-coordinator/.tmp/`：演示 DB 与 worktree",
        "- `a2a-lab/vendor/`：pip vendor 产物",
        "- `AgentGuild_交接包/`：旧的 572MB 交接包，避免重复",
        "- `*.db`、`*.zip`、`*.log`、`__pycache__`、`vendor`",
        "",
        "## 快速开始",
        "",
        "```powershell",
        "cd p0-coordinator",
        "python -m unittest test_coordinator test_estimate test_plan_milp test_verifier test_v1_core test_v1_worktree test_v1_lifecycle test_v1_git_merge test_adapters test_or_router test_semantic -q",
        "python smoke_cli.py",
        "python smoke_mcp.py",
        "```",
        "",
        "WebView2 控制台：`p0-coordinator/webview2/bin/AgentGuildWebView2.exe`（需要 .NET Framework 4.8 + Edge WebView2 Runtime）。",
        "",
        "## 关键文档",
        "",
        "- `pilot/CURRENT-STATUS-2026-11-09.docx` / `.pdf`",
        "- `pilot/ROADMAP-V2.md`",
        "- `pilot/SEMANTIC-ORG-DESIGN.md` / `SEMANTIC-ORG-VERIFY.md`",
        "- `pilot/MARVIS-TEARDOWN.md`",
        "- `pilot/REAL-REPO-V2.md`",
        "- `pilot/OR-ROUTER.md` / `ROLES.md`",
        "",
    ]
    (STAGE / "MANIFEST.md").write_text("\n".join(manifest), encoding="utf-8")

    if ZIP_BASE.with_suffix(".zip").exists():
        ZIP_BASE.with_suffix(".zip").unlink()
    archive = shutil.make_archive(str(ZIP_BASE), "zip", root_dir=STAGE.parent, base_dir=STAGE.name)
    size = Path(archive).stat().st_size
    print(f"staged: {STAGE}")
    print(f"files: {len(files)}, raw: {total / 1024 / 1024:.2f} MB")
    print(f"zip: {archive} ({size / 1024 / 1024:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
