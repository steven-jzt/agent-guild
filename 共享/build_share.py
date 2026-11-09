"""构建对外共享包：docs + p0 code + 其他实验代码，排除 vendor/db/tmp。"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARE = ROOT / "共享"
STAGE = SHARE / "AgentGuild-成果共享-2026-11-08"
ZIP_BASE = ROOT / "AgentGuild-成果共享-2026-11-08"

DOCS = [
    "agent通信与冲突-实验.md",
    "A2A-可行性-附记.md",
    "多agent文档编辑-操作系统类比.md",
    "多agent协调插件-运筹学方案.md",
    "多agent动态组织设计-问题定义.md",
    "学习型优化-从trace到策略.md",
    "门控机制-实验与设计.md",
    "AgentGuild-论文与开源项目-调研.md",
    "AgentGuild-缺口与GitHub参考.md",
]
CODE_DIRS = ["p0-coordinator", "demo-ledger", "contention", "coordination",
             "or-planner", "org-sim", "doc-sim", "learning-optimizer", "a2a-lab"]


def ignore(_dir: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        if name in {".git", ".tmp", "data", "__pycache__", ".pytest_cache", ".venv", "vendor", "node_modules"}:
            ignored.add(name)
        elif name.startswith("tmp"):
            ignored.add(name)
        elif name.endswith((".pyc", ".db", ".db-journal", ".zip")):
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


def main() -> int:
    if STAGE.exists():
        _rmtree(STAGE)
    STAGE.mkdir(parents=True)

    shutil.copy2(SHARE / "README.md", STAGE / "README.md")
    shutil.copy2(SHARE / "发送文案.md", STAGE / "00-发送文案.md")
    shutil.copy2(SHARE / "成果摘要.md", STAGE / "01-成果摘要.md")
    shutil.copy2(ROOT / "docs" / "报告" / "多agent协作-总报告.md", STAGE / "02-总报告.md")
    shutil.copy2(ROOT / "docs" / "现状" / "当前状态-2026-11-08.md", STAGE / "03-当前状态.md")
    shutil.copy2(ROOT / "docs" / "评审" / "AgentGuild-v1.0.1-对比分析.md", STAGE / "07-与AgentGuild-V1对比.md")

    design = STAGE / "04-实验与设计"
    design.mkdir()
    for name in DOCS:
        src = ROOT / "docs" / "报告" / name
        if not src.exists():
            src = ROOT / "docs" / "调研" / name
        shutil.copy2(src, design / name)

    code = STAGE / "05-代码"
    code.mkdir()
    for name in ["p0-coordinator"]:
        shutil.copytree(ROOT / name, code / name, ignore=ignore)

    other = STAGE / "06-其他实验代码"
    other.mkdir()
    for name in CODE_DIRS[1:]:
        shutil.copytree(ROOT / "experiments" / name, other / name, ignore=ignore)

    pilot = STAGE / "08-试点"
    shutil.copytree(ROOT / "pilot", pilot, ignore=ignore)

    files = [p for p in STAGE.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    manifest = [
        "# MANIFEST",
        "",
        "构建时间：2026-11-08",
        "",
        f"- 文件数：{len(files)}",
        f"- 未压缩大小：{total / 1024 / 1024:.2f} MB",
        "- 测试：42 个单元测试全绿（test_coordinator / test_estimate / test_plan_milp / test_verifier）",
        "- 真实 agent live demo：baseline / verified / governance",
        "",
        "## 排除项",
        "",
        "- `a2a-lab/vendor/`（46MB pip 产物）",
        "- `*.db`、`.tmp/`、`__pycache__/`、`data/` 运行时产物",
        "",
        "## 重建",
        "",
        "```powershell",
        "cd 05-代码/p0-coordinator",
        "python -m unittest test_coordinator test_estimate test_plan_milp test_verifier -v",
        "python smoke_cli.py",
        "python smoke_mcp.py",
        "```",
    ]
    (STAGE / "MANIFEST.md").write_text("\n".join(manifest), encoding="utf-8")

    if ZIP_BASE.with_suffix(".zip").exists():
        ZIP_BASE.with_suffix(".zip").unlink()
    archive = shutil.make_archive(str(ZIP_BASE), "zip", root_dir=STAGE.parent, base_dir=STAGE.name)
    size = Path(archive).stat().st_size
    print(f"staged: {STAGE}")
    print(f"files: {len(files)}, raw: {total/1024/1024:.2f} MB")
    print(f"zip: {archive} ({size/1024/1024:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
