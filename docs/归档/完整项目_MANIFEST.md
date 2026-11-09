# AgentGuild 完整项目包（整合前清单，归档）

> **整合归档说明**：本文件描述整合前的完整项目包；当前清单见根目录 `MANIFEST.md`。

构建时间：2026-11-09 14:59:28

- 文件数：211
- 未压缩大小：2.98 MB
- 测试基线：91 tests OK
- 包含：p0-coordinator、pilot、A2A/contention/OR/org/doc/learning 实验代码、根目录设计文档、共享文档
- 包含：webview2/bin 预编译控制台（Windows）

## 排除项

- `.tmp/`：运行数据库、worktree、demo 仓库
- `p0-coordinator/electron/node_modules/`：前端依赖，按 package.json 安装
- `p0-coordinator/.tmp/`：演示 DB 与 worktree
- `a2a-lab/vendor/`：pip vendor 产物
- `AgentGuild_交接包/`：旧的 572MB 交接包，避免重复
- `*.db`、`*.zip`、`*.log`、`__pycache__`、`vendor`

## 快速开始

```powershell
cd p0-coordinator
python -m unittest test_coordinator test_estimate test_plan_milp test_verifier test_v1_core test_v1_worktree test_v1_lifecycle test_v1_git_merge test_adapters test_or_router test_semantic -q
python smoke_cli.py
python smoke_mcp.py
```

WebView2 控制台：`p0-coordinator/webview2/bin/AgentGuildWebView2.exe`（需要 .NET Framework 4.8 + Edge WebView2 Runtime）。

## 关键文档

- `pilot/CURRENT-STATUS-2026-11-09.docx` / `.pdf`
- `pilot/ROADMAP-V2.md`
- `pilot/SEMANTIC-ORG-DESIGN.md` / `SEMANTIC-ORG-VERIFY.md`
- `pilot/MARVIS-TEARDOWN.md`
- `pilot/REAL-REPO-V2.md`
- `pilot/OR-ROUTER.md` / `ROLES.md`
