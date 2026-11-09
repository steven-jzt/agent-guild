# Agent Guild 整合版（2026-11-09）

> 本目录把两份互补的 Agent Guild 材料合并成一个项目树：
>
> - `Agent_Guild_交接包_v1.0.1_2026-11-09.zip`：v1.0.1 设计文档 + M0 接入实验（TypeScript）
> - `AgentGuild-完整项目-2026-11-09.zip`：研究/实验 + p0-coordinator 协调内核（Python）
>
> 两个原始压缩包保持不动；这里是整合后的唯一入口。

Agent Guild（也写作 AgentGuild）的目标是一个**本机多 Agent 项目控制台**：管理员手动把任务派给 Codex 或 DeepSeek Harness（DSH），平台记录过程、隔离工作区、协调文件范围、组织自动测试/Agent 审查/人工验收，并在管理员单独确认后合入项目。

**当前结论：接入层和规则内核都有真实原型，但还没有统一的可操作桌面产品。** 不是一个已发布的 v1.0.1 软件，也不是生产系统。

---

## 1. 现在到底有什么

| 层 | 现状 | 主要证据 |
| --- | --- | --- |
| M0 接入实验（TypeScript） | Codex、DSH 官方账号、DSH API Key 三条路线的基本任务均真实验证通过；交互/中断只覆盖部分场景，**M0 尚未完整验收** | `agent-guild/`、[M0 验证记录](docs/验证/M0_验证记录_2026-11-08.md)、[交接说明](docs/00_先读我_进度与架构交接.md) |
| 协调内核（Python） | SQLite 任务/租约/文件预约/Execution/Candidate/Evidence/MergeAttempt/Worktree/lifecycle；**91 个测试通过**（本次整合后重跑） | `p0-coordinator/`、[当前状态](pilot/CURRENT-STATUS-2026-11-09.md) |
| 真实 Agent 试点 | DSH 5 个任务、Codex-DeepSeek 1 个任务、3-agent 并发、Codex App Server V1 端到端、真实 cherry-pick | `pilot/REAL-*.md`、`pilot/BATCH-DSH.md`、`pilot/INTEGRATION.md` |
| 桌面壳 | Electron 多个版本窗口崩溃（`0xC0000005`）；WebView2 替代壳已跑通窗口、renderer、Python RPC、Start Run/Approve/Merge | [WebView2 README](p0-coordinator/webview2/README.md)、[Electron 阻塞记录](pilot/ELECTRON-BLOCKED.md) |
| 产品界面 | **没有**目标架构中的 React 页面、受限 Electron IPC、utilityProcess 或 HTTP API；WebView2 壳不等同于正式界面 | [交接说明 §4–§5](docs/00_先读我_进度与架构交接.md) |

一句话：**“启动真实 Agent → 在隔离工作区改文件 → 采集结果 → 候选/证据/审批/合入”这条链在原型里能跑；“安装一个桌面软件，建任务、看进度、回答问题、点验收”还没有实现。**

---

## 2. 两份来源是什么关系

它们不是一份工作的两个版本，而是同一目标的两条互补路线：

| 能力 | 交接包（`agent-guild/`） | 完整项目（`p0-coordinator/`） |
| --- | --- | --- |
| Codex / DSH 协议级适配 | ✅ app-server + JSON-RPC、提问/审批桥接 | 🟡 子进程 + argv 级适配 |
| Windows 进程树治理 | ✅ 手写 P/Invoke Job Object，含子孙进程清理测试 | 🟡 `subprocess` + 超时 |
| SQLite / 任务 / 租约 / 事件 | ❌ 内存 + JSON | ✅ 91 个测试 |
| Run / Candidate / Worktree / Merge 门禁 | ❌ 只有目标架构 | ✅ V1 规则层已实现 |
| 人工验收 / N-of-M 审批 | ❌ | ✅ 有 |
| 桌面 | ❌ | 🟡 WebView2 壳（Electron 崩） |

[交接包审阅意见](docs/评审/AgentGuild_交接包_审阅意见.md)的结论是：**以 p0 为骨架，把交接包的 `src/transport/`、`src/adapters/`、`JobHost.cs` 移植进 p0 的适配器层，再在 UI 层做新工作**；两边各做一遍是重复投入。这份整合版保留了两边的完整材料，方便按这个路线继续，但**没有替使用者完成代码合并**。

---

## 3. 目录地图

```text
AgentGuild-整合版-2026-11-09/
├─ README.md                     # 本文件：统一入口
├─ MANIFEST.md                   # 整合记录、来源、校验与边界
├─ 文件清单_SHA256.json           # 全部文件哈希（不含自身）
├─ docs/                         # 统一文档树
│  ├─ 00_先读我_进度与架构交接.md   # 交接包原文（M0 视角）
│  ├─ 设计/                       # v1.0.1 架构总结 + 两份 11-07 草案
│  ├─ 现状/                       # 2026-11-08 状态汇总
│  ├─ 报告/                       # 总报告、复盘、各专题实验/设计（11 篇）
│  ├─ 调研/                       # 论文/开源项目、缺口与 GitHub 参考
│  ├─ 评审/                       # V1 对比分析、交接包审阅意见
│  ├─ 验证/                       # M0 验证记录
│  └─ 归档/                       # 原两个包的 README/MANIFEST/交付摘要/文件清单
├─ agent-guild/                  # TypeScript：M0 接入实验（Codex / DSH）
├─ p0-coordinator/               # Python：协调内核 + CLI/MCP + Electron/WebView2 壳
├─ experiments/                  # 独立实验：a2a-lab、contention、coordination、
│                                #   demo-ledger、doc-sim、learning-optimizer、
│                                #   or-planner、org-sim
├─ pilot/                        # B 内部试点：计划、runbook、真实运行证据、状态报告
└─ 共享/                          # 对外共享/打包脚本与文案
```

目录关系说明：

- `docs/` 是唯一文档入口；原包根目录散落的报告都已归类，代码子项目自己的 README 仍留在原目录。
- `experiments/` 下各目录仍保持原有的兄弟关系（例如 `a2a-lab` 仍能在上一级找到 `demo-ledger`，`learning-optimizer` 仍能找到 `or-planner`），代码导入路径不受影响。
- `pilot/` 与 `p0-coordinator/` 仍在同一级，`pilot` 脚本里的 `../p0-coordinator` 路径保持有效。

---

## 4. 推荐阅读顺序

1. **本文件**：先建立整体地图。
2. [交接说明：已经做了什么、架构是什么、还缺什么](docs/00_先读我_进度与架构交接.md)（约 15 分钟）：M0 的真实进度、目标架构、明确未做的部分。这是**最诚实的一份边界说明**。
3. [当前状态报告（2026-11-09）](pilot/CURRENT-STATUS-2026-11-09.md)（约 10 分钟）：p0 原型侧的能力、真实运行证据、风险。
4. [交接包审阅意见](docs/评审/AgentGuild_交接包_审阅意见.md) + [V1.0.1 对比分析](docs/评审/AgentGuild-v1.0.1-对比分析.md)：两份来源怎么拼、哪些能复用、哪些是缺口。
5. [v1.0.1 计划与架构总结](docs/设计/Agent_Guild_v1.0.1_计划与架构总结_2026-11-08.md)：完整目标架构与规则。
6. 按兴趣读 [多 Agent 协作总报告](docs/报告/多agent协作-总报告.md) 及 `docs/报告/` 下的专题；真实运行细节在 `pilot/`。

> 注意：`docs/00` 是交接包的 **M0 视角**（M0 未完整验收，M1/M2 未实现）；`pilot/CURRENT-STATUS-2026-11-09.md` 是 p0 原型的 **规则层视角**（91 个测试、V1 核心已实现）。两者说的是不同层，不矛盾，也不要把“设计版本 v1.0.1”当成“软件已发布 v1.0.1”。

---

## 5. 快速开始

### 5.1 Python 协调内核（推荐先跑）

要求：Windows x64、Python 3.x、Git 2.30+；MILP 相关测试需要 PuLP/CBC（没有安装时可跳过 `test_plan_milp`）。

```powershell
cd p0-coordinator
python -m unittest test_coordinator test_estimate test_plan_milp test_verifier `
  test_v1_core test_v1_worktree test_v1_lifecycle test_v1_git_merge `
  test_adapters test_codex_appserver test_or_router test_semantic -q
python smoke_cli.py
python smoke_mcp.py
```

本次整合后在 Windows + Python 3.14.4 + PuLP 3.3.1 上重跑：**91 tests OK**。

### 5.2 TypeScript M0 接入实验

要求：Node.js ≥ 24.9、npm、Git。依赖树较大（审阅记录约 1000 个包 / 571 MB），首次安装需要时间。

```powershell
cd agent-guild
npm ci
npm test          # 交接包记录：17 项通过
npm run typecheck # 交接包记录：退出码 0
```

真实模型实验需要在本机自行配置 Codex / DSH 官方认证，可能消耗额度；本包不含任何凭据、账号登录态或原始模型日志。详见 [agent-guild README](agent-guild/README.md)。

### 5.3 WebView2 控制台

```powershell
$env:GUILD_DB="<本目录>\p0-coordinator\.tmp\agent-demo\db.sqlite"
cd p0-coordinator\webview2
.\bin\AgentGuildWebView2.exe
```

需要 .NET Framework 4.8 + Edge WebView2 Runtime。Electron 版本在本机崩溃，原因与排除项见 [pilot/ELECTRON-BLOCKED.md](pilot/ELECTRON-BLOCKED.md)。

---

## 6. 版本、口径与边界

- **设计版本 vs 代码版本**：设计文档是 v1.0.1，`agent-guild/package.json` 是 0.1.0；两者不代表软件已发布 v1.0.1。
- **提交号不可验证**：交接包声明源码快照为 `a58e08e7340400fead3bed87ebbb315d4cf39283`，但包内没有 `.git`，接收方无法复算。
- **依赖审计**：审阅记录为 209 个受影响依赖项（high 198 / moderate 11），本次整合**没有升级依赖或重跑审计**；进入正式分发前需要单独处理，不要直接 `npm audit fix --force`。
- **凭据与隐私**：整合包不含 API Key、账号登录态、`.env`、原始模型事件日志、实验仓库或安装包。历史文档中的本机路径只用于追溯。
- **没有 LICENSE**：两份来源都没有版权/授权声明；对外分发前应补。
- **没有合并运行时**：`agent-guild/`（TypeScript）与 `p0-coordinator/`（Python）仍是两套可独立运行的原型。整合的是项目树、文档与构建脚本，不是把两种语言合成一个进程。
- **未重跑真实模型**：本次整合只重跑了 p0 的 91 个自动化测试；真实 Codex/DSH 实验的结论来自原包记录，没有为打包再次消耗模型额度。
- **M0 剩余项**：Codex 原生提问/审批、DSH 审批允许分支、API 路线停止/接续、鉴权失效矩阵、重启后认证、双并行隔离、能力矩阵等，详见 [交接说明 §7](docs/00_先读我_进度与架构交接.md)。

---

## 7. 已知缺口与下一步

按“成本最低、解锁最多”排序：

1. **先定契约，再写界面**：产出“适配器接口 + 事件类型 + 交互请求类型”的 TypeScript 类型文件 + MockAdapter，让前端在 M1 不存在时并行开发（审阅意见 §7）。
2. **以 p0 为骨架移植接入层**：把 `agent-guild/src/transport/`、`src/adapters/`、`JobHost.cs` 接进 `p0-coordinator` 的 adapter 层，替换当前的子进程适配；这样才同时具备协议级会话、进程树治理和 Run/Candidate/Merge 规则。
3. **修 3 个可用性坑**：`m0:cancel` 读取 `AGENT_GUILD_CODEX_JS`；RPC 超时后明确提示“执行状态未知，需人工核对”；`LOCALAPPDATA` 缺失时明确报错。
4. **补 CI 与依赖治理**：把 `npm test` + `typecheck` 变成自动门禁；给出 198 个 high 的处置清单。
5. **真实小仓库 5–10 个任务 + cost 对账 + 人工验收**：当前真实仓库试点仍是模板仓库，cost 是估算不是账单。
6. **桌面与 UI**：Electron 真窗口仍崩溃（WebView2 是替代壳）；目标 React/Electron IPC/utilityProcess 尚未开始。

---

## 8. 整合记录（摘要）

- 来源：交接包 37 个文件 + 完整项目 212 个文件，共 **249 个源文件**；整合后新增本 README、`MANIFEST.md`、`文件清单_SHA256.json`。
- 文档按 `docs/设计|现状|报告|调研|评审|验证|归档` 归类；代码、实验、试点保持可运行子树。
- 相对链接按新目录重写 **128 处**（涉及 16 个 Markdown 文件）；归档的旧 README/MANIFEST 加了“历史入口”提示。
- `共享/build_share.py`、`共享/build_full_package.py` 的路径已适配新目录；业务代码逻辑未改。
- 原包中的 6 个空运行时探测目录（`demo-ledger/tmp*`）未保留；其余内容按文件哈希核对保留。
- 详细来源、哈希、逐项修改与校验结果见 [MANIFEST.md](MANIFEST.md) 和 [文件清单_SHA256.json](文件清单_SHA256.json)。

---

**最短结论**：这是一份“可继续开发的整合快照”，不是成品。用它看全貌、跑 91 个规则测试、读真实证据；下一步最值得做的是**定接口契约 + 把交接包的接入层移植进 p0**，而不是先做 UI 皮肤。
