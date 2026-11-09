# B 内部试点（pilot）

> 发给朋友运行：见 [发给朋友-运行说明.md](发给朋友-运行说明.md)。
> 组织协调与分工：[org-visual.html](org-visual.html) / [org-visual.png](org-visual.png)。
> 新版 WebView2 仪表盘：[ui-dashboard.png](ui-dashboard.png)。
> V2 路线图：[ROADMAP-V2.md](ROADMAP-V2.md)；真实仓库试点：[REAL-REPO-V2.md](REAL-REPO-V2.md)。
> OR 路由：[OR-ROUTER.md](OR-ROUTER.md)。
> 审批角色：[ROLES.md](ROLES.md)。
> 当前状态报告：[CURRENT-STATUS-2026-11-09.md](CURRENT-STATUS-2026-11-09.md) / [CURRENT-STATUS-2026-11-09.docx](CURRENT-STATUS-2026-11-09.docx) / [CURRENT-STATUS-2026-11-09.pdf](CURRENT-STATUS-2026-11-09.pdf)。
> Marvis 拆解：[MARVIS-TEARDOWN.md](MARVIS-TEARDOWN.md)。
> 语义/组织 MVP：[SEMANTIC-ORG-DESIGN.md](SEMANTIC-ORG-DESIGN.md) / [SEMANTIC-ORG-VERIFY.md](SEMANTIC-ORG-VERIFY.md)。

目标：用 **一个真实小仓库、Codex + DSH API Key、5–10 个任务**，验证 V1 的“派遣 → worktree → 候选 → 验收 → 合入”闭环。

当前状态：

- ✅ `PLAN.md`：三周计划、Gate、指标、风险；
- ✅ `RUNBOOK.md`：操作员步骤、失败路径、记录模板；
- ✅ `run_pilot.py`：V1 规则层 dry-run（真实 git worktree + Candidate/Evidence/Merge）；
- ✅ `run_real_dsh.py`：真实 DSH V1 端到端（成功，usage 已记录）；
- ✅ `run_dsh_batch.py`：真实 DSH 批量（3 成功 / 1 越界 / 1 无改动）；
- ✅ `run_real_codex.py`：真实 Codex（DeepSeek provider）V1 端到端；
- ✅ `run_real_appserver.py`：Codex App Server adapter 的 V1 端到端；
- ✅ `run_multi_agent.py`：3 个真实 agent 并发（DSH + Codex-DeepSeek + Codex-智谱）；
- ✅ `run_multi_repo.py`：多模块 `sample-repo` 上 3 个 agent 并发；
- ✅ `COST.md`：按 DeepSeek 官方价目表的 cost 估算；
- ✅ `FINAL-REPORT.md`：试点终版报告；
- ✅ WebView2 桌面壳（Electron 替代）：[../p0-coordinator/webview2/README.md](../p0-coordinator/webview2/README.md)
- 🟡 Electron：45/28 都已装，窗口崩溃。

## 快速开始

```powershell
cd p0-coordinator
python ..\pilot\run_pilot.py
```

dry-run 会：

1. 在 `.tmp/pilot/repo` 建小 Git 仓库；
2. 创建项目/任务/文件预约；
3. 启动 execution + worktree；
4. 用通用 CLI adapter 改 `src/app.py`；
5. scope check → commit → Candidate；
6. 记录绑定 SHA 的测试证据；
7. 人工批准 → MergeAttempt → `accepted`；
8. 删除 worktree。

**注意**：这是规则层 dry-run，`finish_merge` 不会修改原仓库；真实 Git 合入与真实 adapter 尚未验收。

## 文件

- [PLAN.md](PLAN.md)
- [RUNBOOK.md](RUNBOOK.md)
- [M0.md](M0.md)（DSH 401 实测、Codex 登录、Electron 崩溃记录）
- [CODEX-ACCESS.md](CODEX-ACCESS.md)（Codex 国内访问：DeepSeek / 智谱 / 本地）
- [REAL-DSH.md](REAL-DSH.md)（真实 DSH V1 端到端 + usage）
- [BATCH-DSH.md](BATCH-DSH.md)（5 个真实 DSH 任务汇总：3 成功 / 2 拦截）
- [REAL-CODEX.md](REAL-CODEX.md)（Codex + DeepSeek V1 端到端）
- [CODEX-APPSERVER.md](CODEX-APPSERVER.md)（Codex App Server JSON-RPC smoke）
- [REAL-APPSERVER.md](REAL-APPSERVER.md)（App Server adapter + V1 端到端 + 真实 cherry-pick）
- [ELECTRON-BLOCKED.md](ELECTRON-BLOCKED.md)（17/22/28/45 崩溃证据）
- [REAL-MULTI.md](REAL-MULTI.md)（3 agent 并发：DSH + Codex-DeepSeek + Codex-智谱）
- [REAL-REPO.md](REAL-REPO.md)（多模块 sample-repo 3 agent 并发）
- [INTEGRATION.md](INTEGRATION.md)（三个 Candidate 的真实 Git cherry-pick）
- [COST.md](COST.md)（官方价目表 cost 估算）
- [FINAL-REPORT.md](FINAL-REPORT.md)（终版试点报告）
- `run_pilot.py`
- `run_real_dsh.py`
- `run_dsh_batch.py`
- `run_real_codex.py`
- `run_real_appserver.py`
- `run_multi_agent.py`
- `run_multi_repo.py`
- `sample-repo/`（多模块测试仓库）
- `agent_edit.py`（脚本化 agent，仅 dry-run）
