# 探索区：多 Agent 协作研究索引

> **整合归档说明**：这是整合前“完整项目包”的索引，正文按原样保留，只修正了相对链接。当前统一入口见根目录 `README.md`。

> 时间：2026-11-06 ~ 2026-11-08
> 起点：朋友的一份《Agent Guild 项目构想与架构草案》
> **先读这一份**：[多agent协作-总报告.md](../报告/多agent协作-总报告.md)

---

## 0. 一句话状态

**五组实验、四份设计、一个可运行的协调器。** P0 已可用；V1 Core 六块规则层（Run/预约/Candidate/Merge/Worktree/lifecycle/双重门）已实现；B 内部试点已启动，dry-run 通过。**真实 DSH/Codex/3-agent/App Server 已跑通；Electron 崩，但 WebView2 替代壳已通；剩下真实小仓库、cost 对账、真人验收。**

最新汇总：[当前状态-2026-11-08.md](../现状/当前状态-2026-11-08.md)。试点：[pilot/PLAN.md](../../pilot/PLAN.md)。

---

## 1. 推荐阅读顺序

1. **[当前状态-2026-11-08.md](../现状/当前状态-2026-11-08.md)**（5–10 分钟）——现在有什么、91 个测试、真实 DSH V1 端到端、边界与下一步。
2. **[多agent协作-总报告.md](../报告/多agent协作-总报告.md)**（30–40 分钟）——总纲，含全部实验、设计、判断、路线图。
3. 实验细节（按兴趣挑）：
   - [agent通信与冲突-实验.md](../报告/agent通信与冲突-实验.md)
   - [A2A-可行性-附记.md](../报告/A2A-可行性-附记.md)
   - [多agent文档编辑-操作系统类比.md](../报告/多agent文档编辑-操作系统类比.md)
4. 设计：
   - [多agent协调插件-运筹学方案.md](../报告/多agent协调插件-运筹学方案.md)
   - [多agent动态组织设计-问题定义.md](../报告/多agent动态组织设计-问题定义.md)
   - [学习型优化-从trace到策略.md](../报告/学习型优化-从trace到策略.md)
   - [门控机制-实验与设计.md](../报告/门控机制-实验与设计.md)
5. 文献与选型：
   - [AgentGuild-论文与开源项目-调研.md](../调研/AgentGuild-论文与开源项目-调研.md)
   - [AgentGuild-缺口与GitHub参考.md](../调研/AgentGuild-缺口与GitHub参考.md)

---

## 2. 关键数字

| 主题 | 数字 |
| --- | --- |
| 三 agent 同工作空间 | 6 分钟、485 行实现、856 行测试、91 个测试全绿、0 手工冲突 |
| 裸读改写 | 180 次期望，实际 67/67/68（丢约 62%），退出码全 0 |
| 加锁 | 180/180/180 |
| 通信实验 | 纯广播 52.2% vs 同节奏对照 52.4%（≈无效）；退避 2.4%；租约 0%；check-then-act 38.4% |
| 文档追加 | 无协调 8.5/12；全局锁 12/12（69.6ms）；章节锁 12/12（31.8ms）；MVCC 12/12（25.7ms，3.4 次重试） |
| 文档改同一行 | 锁：检测 0、静默覆盖 4；MVCC：检测 4、静默覆盖 0 |
| 运筹学算例 | 216/729 可行，41 个 Pareto 点；约束最优 $0.710 / 15.05h / 质量 0.807 |
| 动态组织仿真 | oracle 0.804 = best fixed 0.804；adaptive 0.800；**headroom ≈ 0** |
| 学习曲线 | N=100 时 RF/GBR regret 0.0086，贪心 0.0092，oracle 0 |
| 门控 | N=10 时 always-learned p90 0.0899 → 门控 0.0092；N=200 p90 0.0454 → 0.0121 |
| P0 协调器 | 91 个单元测试全绿；MCP + CLI；SQLite 单机；真实 DSH 5 任务 + Codex 1 任务 + 3-agent 并发 + App Server V1 + WebView2 写操作 |
| 真实 agent live demo | 3 个 DSH subagent × 3 次：baseline / verified / governance |
| 3D Pareto | 5 个非支配点，覆盖 16h/$1.00/q0.95 与 20h/$0.08/q0.70 |
| 预算止损 | 预算 $0.10，花费 $0.12 后下一单 `budget_exceeded` |

---

## 3. 文件地图

| 文件 | 类型 | 状态 | 内容 |
| --- | --- | --- | --- |
| [当前状态-2026-11-08.md](../现状/当前状态-2026-11-08.md) | 状态汇总 | **最新** | 现在有什么、91 个测试、真实 DSH V1 端到端、边界、下一步 |
| [pilot/PLAN.md](../../pilot/PLAN.md) | 试点计划 | **最新** | B 内部试点三周计划、Gate、指标、风险 |
| [pilot/发给朋友-运行说明.md](../../pilot/发给朋友-运行说明.md) | 运行说明 | **最新** | 解压、测试、dry-run、key、WebView2/Electron |
| [pilot/org-visual.png](../../pilot/org-visual.png) | 组织协调图 | **最新** | 人类/Coordinator/agent/审批/合入一页图 |
| [pilot/ui-dashboard.png](../../pilot/ui-dashboard.png) | 新版控制台 | **最新** | 任务流水线/候选环图/Agent 用量 |
| [pilot/ROADMAP-V2.md](../../pilot/ROADMAP-V2.md) | V2 路线图 | **最新** | 真实仓库 → checks → OR → A2A → 文档/学习 |
| [pilot/REAL-REPO-V2.md](../../pilot/REAL-REPO-V2.md) | 真实仓库试点 | **最新** | 两文件改动 + 合并后检查 + 4 测试 |
| [pilot/OR-ROUTER.md](../../pilot/OR-ROUTER.md) | OR 路由 | **最新** | 按成本/时间/质量选 agent |
| [pilot/ROLES.md](../../pilot/ROLES.md) | 审批角色 | **最新** | Reviewer/Admin + 旧库迁移 |
| [pilot/CURRENT-STATUS-2026-11-09.docx](../../pilot/CURRENT-STATUS-2026-11-09.docx) | 当前状态报告 | **最新** | 7 页 DOCX：结论/能力/证据/边界/路线图 |
| [pilot/CURRENT-STATUS-2026-11-09.pdf](../../pilot/CURRENT-STATUS-2026-11-09.pdf) | 当前状态报告 PDF | **最新** | 同 DOCX 的 PDF 版 |
| [pilot/MARVIS-TEARDOWN.md](../../pilot/MARVIS-TEARDOWN.md) | Marvis 拆解 | **最新** | 公开资料 + 安装器/APK 静态分析 + 借鉴建议 |
| [pilot/SEMANTIC-ORG-DESIGN.md](../../pilot/SEMANTIC-ORG-DESIGN.md) | 语义/组织设计 | **最新** | 变更事实/影响面/ACK + 能力标签 |
| [pilot/SEMANTIC-ORG-VERIFY.md](../../pilot/SEMANTIC-ORG-VERIFY.md) | 语义/组织验收 | **最新** | 91 tests + CLI 证据 |
| [pilot/RUNBOOK.md](../../pilot/RUNBOOK.md) | Runbook | **最新** | 操作员步骤、失败路径、记录模板 |
| [pilot/M0.md](../../pilot/M0.md) | M0 记录 | **最新** | DSH 401/成功实测、Codex 登录、Electron 崩溃 |
| [pilot/REAL-DSH.md](../../pilot/REAL-DSH.md) | 真实 DSH 证据 | **最新** | V1 端到端 + usage + 限制 |
| [pilot/BATCH-DSH.md](../../pilot/BATCH-DSH.md) | DSH 批量证据 | **最新** | 5 个任务：3 成功 / 2 拦截 |
| [pilot/REAL-CODEX.md](../../pilot/REAL-CODEX.md) | 真实 Codex 证据 | **最新** | Codex + DeepSeek V1 端到端 |
| [pilot/CODEX-APPSERVER.md](../../pilot/CODEX-APPSERVER.md) | App Server 证据 | **最新** | initialize/thread/turn + 改文件 |
| [pilot/REAL-APPSERVER.md](../../pilot/REAL-APPSERVER.md) | App Server V1 | **最新** | adapter + V1 + 真实 cherry-pick |
| [pilot/ELECTRON-BLOCKED.md](../../pilot/ELECTRON-BLOCKED.md) | Electron 阻塞 | **最新** | 17/22/28/45/46a + 重启/计划任务都崩 |
| [p0-coordinator/webview2/README.md](../../p0-coordinator/webview2/README.md) | WebView2 壳 | **最新** | 替代 Electron，smoke 通过 |
| [pilot/REAL-MULTI.md](../../pilot/REAL-MULTI.md) | 多 agent 证据 | **最新** | DSH + Codex-DeepSeek + Codex-智谱 并发 |
| [pilot/REAL-REPO.md](../../pilot/REAL-REPO.md) | 多模块仓库证据 | **最新** | sample-repo 三 agent 并发 |
| [pilot/INTEGRATION.md](../../pilot/INTEGRATION.md) | 真实 Git 合入 | **最新** | 3 Candidate cherry-pick + 集成测试 |
| [pilot/FINAL-REPORT.md](../../pilot/FINAL-REPORT.md) | **终版试点报告** | **最新** | 12 次真实执行、Gate 结果、建议 |
| [pilot/COST.md](../../pilot/COST.md) | 成本估算 | **最新** | 官方价目表 + usage → ¥0.0305 |
| [AgentGuild-v1.0.1-对比分析.md](../评审/AgentGuild-v1.0.1-对比分析.md) | 对比分析 | **最新** | V1 计划 vs 我们的成果：对齐、冲突、改造建议 |
| [多agent协作-总报告.md](../报告/多agent协作-总报告.md) | 总报告 | **最新** | 五组实验 + 四份设计 + 文献 + 路线图 + 实现进度 |
| [多agent同工作空间-实验报告.md](../报告/多agent同工作空间-实验报告.md) | 实验报告 | 历史 | 第一版：3 agent 并行 + 并发写 |
| [多agent协作-整体复盘.md](../报告/多agent协作-整体复盘.md) | 复盘 | 历史 | 中段复盘；被总报告取代 |
| [agent通信与冲突-实验.md](../报告/agent通信与冲突-实验.md) | 实验 | 最新 | 6 种协议、通信 vs 原子性 |
| [A2A-可行性-附记.md](../报告/A2A-可行性-附记.md) | 实验 | 最新 | 两个 A2A server 两跳调用 |
| [多agent文档编辑-操作系统类比.md](../报告/多agent文档编辑-操作系统类比.md) | 设计 + 实验 | 最新 | Document Kernel、OS 类比、首轮实验 |
| [多agent协调插件-运筹学方案.md](../报告/多agent协调插件-运筹学方案.md) | 设计 | 最新 | 插件形态、OR 映射、MILP/CP-SAT |
| [多agent动态组织设计-问题定义.md](../报告/多agent动态组织设计-问题定义.md) | 设计 | 最新 | 六维组织、双层优化、理论、仿真 |
| [学习型优化-从trace到策略.md](../报告/学习型优化-从trace到策略.md) | 设计 + 实验 | 最新 | 监督/bandit/离线 RL、学习曲线 |
| [门控机制-实验与设计.md](../报告/门控机制-实验与设计.md) | 设计 + 实验 | 最新 | 五种门控、回退、阈值校准 |
| [AgentGuild-论文与开源项目-调研.md](../调研/AgentGuild-论文与开源项目-调研.md) | 文献 | 最新 | 论文地图 + 开源项目地图 |
| [AgentGuild-缺口与GitHub参考.md](../调研/AgentGuild-缺口与GitHub参考.md) | 选型 | 最新 | 14 类缺口、adopt/combine/build |

> `AI获取视频内容-方案梳理-2026-11.md` 是此前无关文档，未纳入本索引。

---

## 4. 代码地图

| 目录 | 用途 | 运行 |
| --- | --- | --- |
| `p0-coordinator/` | **P0 协调器 + P1/P2 初步 + V1 Core 切片**：SQLite + MCP + CLI，任务/租约/fencing/runs/estimator/MILP/Pareto/验证/审批/预算 + V1 Run/预约/Candidate/Merge/Worktree/lifecycle/真实 Git cherry-pick/Adapter/Electron 壳/WebView2 壳（Start Run/Approve/Merge） | `cd p0-coordinator; python -m unittest test_coordinator test_estimate test_plan_milp test_verifier test_v1_core test_v1_worktree test_v1_lifecycle test_v1_git_merge test_adapters -v; python demo.py; python smoke_cli.py; python smoke_mcp.py; .\webview2\run.ps1 --smoke` |
| `pilot/` | **B 内部试点**：三周计划 + Runbook + V1 dry-run（真实 worktree/Candidate/Merge） | `cd p0-coordinator; python ..\pilot\run_pilot.py` |
| `demo-ledger/` | 3 agent 并行实现的记账 CLI | `cd demo-ledger; python -m unittest test_storage test_report test_cli test_acceptance -v` |
| `contention/` | 进程级并发写：裸写 vs 锁 | `cd contention; python run_experiment.py` |
| `coordination/` | 通信 vs 原子性：6 种协议 | `cd coordination; python run_experiment.py` |
| `a2a-lab/` | A2A 双 agent 两跳演示 | 见 `a2a-lab/README.md`（含 46MB vendor，可删重建） |
| `or-planner/` | 6 任务 × 3 agent 的 Pareto 优化 | `cd or-planner; python toy_optimizer.py` |
| `org-sim/` | 3 组织 × 3 互依性的机制仿真 | `cd org-sim; python org_sim.py` |
| `doc-sim/` | 同一 Markdown 的 4 种并发策略 | `cd doc-sim; python doc_sim.py` |
| `learning-optimizer/` | 从 trace 学策略 + 门控实验 | `cd learning-optimizer; python train_optimizer.py; python gating_experiment.py` |

依赖：Python 3.14；`p0-coordinator` 的 MILP 用 PuLP/CBC（协调器本体纯标准库）；`learning-optimizer` 用 numpy + scikit-learn；`a2a-lab` 用 vendor 里的 `a2a-sdk`。

---

## 5. 目录结构

```text
探索区/
├─ README.md                         # 本索引
├─ 当前状态-2026-11-08.md             # 最新状态汇总
├─ 多agent协作-总报告.md              # 总纲
├─ 多agent同工作空间-实验报告.md      # 历史
├─ 多agent协作-整体复盘.md            # 历史
├─ agent通信与冲突-实验.md
├─ A2A-可行性-附记.md
├─ 多agent文档编辑-操作系统类比.md
├─ 多agent协调插件-运筹学方案.md
├─ 多agent动态组织设计-问题定义.md
├─ 学习型优化-从trace到策略.md
├─ 门控机制-实验与设计.md
├─ AgentGuild-论文与开源项目-调研.md
├─ AgentGuild-缺口与GitHub参考.md
├─ demo-ledger/   contention/   coordination/   a2a-lab/
├─ or-planner/    org-sim/      doc-sim/        learning-optimizer/
├─ p0-coordinator/                    # 含 live-demo/{verified,governance}
├─ pilot/                              # B 内部试点：计划、Runbook、dry-run
└─ AI获取视频内容-方案梳理-2026-11.md   # 无关旧文档
```

---

## 6. 状态与清理说明

- **历史版本保留**：`多agent同工作空间-实验报告.md` 和 `多agent协作-整体复盘.md` 是过程记录，结论以总报告为准；保留是为了可追溯，不建议再改。
- **`.tmp/` 与 `demo-ledger/` 下的空目录**：早期沙箱 ACL 遗留（0o700 目录不可删），不影响 git/运行；可在**非 DSH 沙箱**的普通 PowerShell 里用 `rmdir /s /q` 清理。
- **`a2a-lab/vendor/`**：46MB，`pip install --target` 的产物；可删除，按 `a2a-lab/README.md` 重建。
- **`p0-coordinator/live-demo/**/*.db`**：三次 live demo 的 SQLite 产物；可删除，按各目录 `setup.py` 重建。
- **根目录的 `counter_*.json`**：早期并发实验的残留，已删除。

---

## 7. 下一步（按优先级）

1. **真实小仓库 5–10 个任务**：DSH 5 个 + Codex 1 个已跑；换真实小仓库扩大样本。
2. **Codex**：已用 DeepSeek 官方 Responses 接入跑通，无需 OpenAI 账号；智谱可备选。
3. **Electron**：17/22/28/45/46a 都已装，窗口都崩（0xC0000005）；**WebView2 替代壳已跑通**，Electron 需换机器验证。
4. **真实 cost**：usage 真，已按官方价目表估算（¥0.0124）；账单级需核对 cache 口径。
5. **审批角色/顺序/自动升级**：现在只有 N-of-M + TTL；下一步接 HumanLayer，补审批链与 timeout escalation。
6. **排期规模化**：CP-SAT / 时间索引模型支持 50 任务；预算 + 3D Pareto 联合优化。
7. **跨机与权限**：Postgres + `FOR UPDATE SKIP LOCKED`；worker token/ACL。
8. **P3 Document Kernel**：region + MVCC + 合并队列，先章节级 lease。
9. **P4 跨边界 A2A**：只在跨机/跨厂商时用。

一句话：**P0/V1 规则层、DSH 5 任务、Codex 1 任务、3-agent 并发、App Server V1、WebView2 壳已跑通；下一步是真实小仓库、cost 对账、真人验收。**
