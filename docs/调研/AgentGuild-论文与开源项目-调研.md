# Agent Guild 相关论文与开源项目调研

> 配套阅读清单，面向《Agent_Guild_项目构想与架构草案_2026-11-07》。
> 只收录能对应到 Agent Guild 某个模块的工作，并标注“读它解决哪个问题”。
> 说明：2026 年的不少工作是预印本；许可证、维护状态、star 数请以仓库当时页面为准。本清单截至 2026-11-07。

## 0. 先看结论：文献对 Agent Guild 的 6 个提醒

1. **多 agent 协作目前经常是负收益。** CooperBench 测了 600+ 个协作编码任务：两个 agent 一起做的成功率，平均比各自单独做低 **30%**。
2. **通信不是协调。** CooperBench 发现通信通道里充斥着含糊、不及时、不准确的消息，agent 还会违背自己的承诺；“Informed but Still Colliding”专门研究“知道彼此在做什么”是否减少合并冲突，标题本身就是答案的一部分；而“Passes Alone, Fails Together”发现一条“我改了什么”的消息能修复 **82%** 的语义干扰，但仍有 18% 修不好。
3. **任务切分要看耦合度，不是文件数量。** Co-Coder 用静态依赖图 + 社区发现做切分，依赖密集的项目收益最大；按文件平均切，会把跨 agent 依赖制造出来。
4. **复杂度不是默认答案。** Agentless 用“定位 → 修复 → 验证”三段式，在 SWE-bench Lite 上以 $0.70 的成本超过了当时一堆复杂 agent。
5. **验收是最薄弱的一环。** MAST 把 task verification 列为三大失败类之一；SWE-Marathon 里失败常来自自验证差、过早终止，13.8% 的 rollout 出现 reward hacking。
6. **长任务与交接仍未解决。** SWE-Marathon 平均 27.2M tokens，前沿 agent 解决不到 30%；TheAgentCompany 里最强 agent 也只能自主完成 30%。

**对 Agent Guild 的含义：** 差异化不在“再接一个多 agent 框架”，而在**协调协议 + 资源锁 + 可信验收 + 交接快照**这四件脏活。这也正是文档第 2、4、8 节的重点。

---

## 1. 论文地图

### A. 问题诊断：多 agent 为什么不行

- **[Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657)**（MAST，NeurIPS 2025）
  1600+ 条标注轨迹、7 个主流 MAS 框架，归纳出 **14 种失败模式、3 大类**：系统设计问题、agent 间失配（inter-agent misalignment）、任务验证（task verification）。附带 LLM-as-Judge 标注流水线。
  → **用途**：Agent Guild 的日志 Schema、验收项、异常状态设计可以直接对照这 14 种模式；尤其是“验证失败”和“失配”两类，应该在任务状态机里有对应出口。

- **[CooperBench: Why Coding Agents Cannot be Your Teammates Yet](https://arxiv.org/abs/2601.13295)**（2026）
  600+ 协作编码任务、12 个库、4 种语言；两个 agent 各做一个 feature，单独能过、合起来可能冲突。结果：协作成功率平均低 30%。三个原因：(1) 通信通道被含糊/不及时/不准确的消息堵塞；(2) 即使沟通有效，agent 也会偏离承诺；(3) agent 对别人的计划和沟通方式有错误预期。
  → **用途**：这是“公会”最该先读的一篇。它说明**光给 agent 开一个聊天频道没用**，必须有承诺机制、消息质量约束和可验证的交接。

- **[Passes Alone, Fails Together: Benchmarking Semantic Coordination in Parallel LLM-Agent Development](https://arxiv.org/abs/2609.25396)**（EXPRESS 2026）
  定义“语义干扰”：一个 agent 改了接口/规则，另一个还按旧假设写。417 对真实 Django PR 合并后只有 1 例干扰（修正评分后）；但在 12 个 Django helper 构造的任务上，**97% 的运行出现干扰**。一条“我刚完成了什么并发变更”的消息能恢复 **82%** 的运行。
  → **用途**：直接支撑 Agent Guild 的“变更通知 + 重读”设计；同时提醒：真实 PR 里干扰未必高频，别用构造实验的 97% 去吓自己，**但构造任务确实能稳定复现这类 bug**，适合做回归测试。

- **[Informed but Still Colliding: Workspace Awareness and Merge Conflicts between Parallel LLM Coding Agents](https://zenodo.org/records/23467694)**（2026，论文 + 复现包）
  在 CooperBench 上做两组随机实验（Qwen3.7-Plus 与 DeepSeek Flash，591 个 episode），检验“自动 workspace awareness”和“显式协调规则”能否减少两个并行 agent 的合并冲突；v3 还编码了“接收失败”（uptake failures）。
  → **用途**：和你的问题最贴的一篇——**“知道彼此在做什么”不等于“不撞车”**。具体效应量要读论文；复现包里有完整输入/输出，适合拿来做 Agent Guild 的评测基线。

- **[Understanding Issues, Causes and Solutions in Open-Source LLM-based Multi-Agent Systems](https://arxiv.org/abs/2610.00905)**（2026）
  从 21 个开源 MAS 的 22,848 个 closed issue 中筛出 944 个，做实证分析。最高频问题是 **Orchestration & Execution**；高频原因包括 Workflow、Tool Integration、Memory；最常见解法是“优化 workflow”。
  → **用途**：给 Agent Guild 的模块优先级提供经验依据：先做编排/执行/工具接入的可靠性，而不是花哨的自治。

- **[Agentless: Demystifying LLM-based Software Engineering Agents](https://arxiv.org/abs/2407.01489)**（2024）
  三阶段（localization → repair → patch validation），不做自主工具调用，在 SWE-bench Lite 上 32.00%、成本 $0.70，超过当时所有开源 agent。
  → **用途**：V1 的“反过度设计”证据。先把最小闭环跑通，再谈多 agent 拓扑。

### B. 架构与任务切分

- **[Agentic Software Engineering: Foundational Pillars and a Research Roadmap](https://arxiv.org/abs/2509.06216)**（SASE，arXiv v4 2026-11）
  提出 SE 3.0 的“双重性”：SE for Humans 与 SE for Agents；两个工作台——**ACE**（Agent Command Environment，人指挥/辅导 agent 团队）和 **AEE**（Agent Execution Environment，agent 执行、必要时回调人）；产出物叫 **Merge-Readiness Pack（MRP）** 和 **Consultation Request Pack（CRP）**。
  → **用途**：这是 Agent Guild 在学术上的“同名项目”。ACE ≈ 公会控制台，AEE ≈ 隔离工作区，MRP ≈ 提交合并包，CRP ≈ 求助/评审包。可以直接借用这套词汇和产物定义。

- **[When Parallelism Pays Off: Cohesion-Aware Task Partitioning for Multi-Agent Coding](https://arxiv.org/abs/2606.00953)**（Co-Coder，NeurIPS 2026 会议页面）
  把多 agent 编排形式化为图划分问题：任务分解能缩短关键路径，但跨 agent 依赖需要昂贵的上下文传递。做法：静态分析建依赖图 → 隔离结构性 hub 文件 → 社区检测切分 → 依赖感知调度。28 个真实任务上 pass rate +14%，墙钟 2.10x，API 成本 −35%，依赖越密集收益越大。
  → **用途**：直接回答“任务怎么切”。Agent Guild 的任务卡应该带**写集（write set）+ 依赖边**，而不是只写“改这几个文件”。

- **[Multi-Agent Collaboration Mechanisms: A Survey of LLMs](https://arxiv.org/abs/2501.06322)**（2025）
  用 actors / types（合作、竞争、竞合）/ structures（P2P、中心、分布）/ strategies / coordination protocols 五个维度梳理 MAS。
  → **用途**：设计 Agent Guild 的“公会拓扑”时的坐标系；V1 手动派遣属于 centralized + cooperation，未来自治再考虑 P2P/竞合。

### C. 基准与评测

- **[SWE-bench](https://arxiv.org/abs/2310.06770)**（ICLR 2024）：2,294 个真实 GitHub issue，12 个 Python 仓库。**注意**：已接近饱和且存在污染争议，别再把它当唯一指标。
- **[Multi-SWE-bench](https://arxiv.org/abs/2504.02605)**（2025）：1,632 个实例，覆盖 Java / TS / JS / Go / Rust / C / C++，用 Agentless、SWE-agent、OpenHands 做了基线。→ 多语言项目的回归基准。
- **[Terminal-Bench 2.0](https://arxiv.org/abs/2601.11868)**（2026）：89 个终端任务，前沿模型/agent 得分 <65%。→ 适合测“真实命令行工作流”，和 Agent Guild 的桌面/嵌入式场景接近。
- **[SWE-Marathon](https://arxiv.org/abs/2606.07682)**（2026）：20 个超长任务，平均 27.2M tokens，前沿 agent <30%；失败主因是自验证差、自报不可行、过早终止；**13.8% 的 rollout 有 reward hacking**。→ 长任务/交接/验收的试金石。
- **[SWE-Lancer](https://arxiv.org/abs/2502.12115)**（2025）：1,400+ 个 Upwork 任务、总价 $1M，把能力映射到钱。→ 做“值不值得派给 agent”的经济学参考。
- **[TheAgentCompany](https://arxiv.org/abs/2412.14161)**（NeurIPS 2025 D&B）：模拟一家小软件公司，最强 agent 自主完成 30%。→ 最接近“公会”场景的端到端基准。
- **[Agent-as-a-Judge](https://arxiv.org/abs/2410.10934)**（2024）：用 agent 评 agent，DevAI 55 个任务、365 条分层需求；比 LLM-as-Judge 可靠，接近人工基线。→ 自动验收/过程评审的设计参考。

### D. 基础系统论文

- **[OpenHands](https://arxiv.org/abs/2407.16741)**（ICLR 2025）：开源 agent 平台，沙箱执行、多 agent 协调、benchmark harness，MIT。
- **[SWE-agent](https://arxiv.org/abs/2405.15793)**（NeurIPS 2024）：提出 Agent-Computer Interface（ACI），用专用编辑/搜索/测试接口提升 agent 表现。
- **[AutoGen](https://arxiv.org/abs/2308.08155)**（2023）：可对话 agent 框架；**注意**：微软已把 AutoGen 与 Semantic Kernel 迁到 Microsoft Agent Framework，新项目别从 AutoGen 起步。
- **[MetaGPT](https://arxiv.org/abs/2308.00352)**（ICLR 2024）：把 SOP 编码进角色流水线，是“公会/流水线”思路的早期代表。
- **[ChatDev](https://arxiv.org/abs/2307.07924)**（ACL 2024）：chat chain + communicative dehallucination，研究多角色对话式开发。

### E. 组织设计：谁、怎么分、怎么协调

> 完整的问题定义与形式化见 [多agent动态组织设计-问题定义.md](../报告/多agent动态组织设计-问题定义.md)。

- **[OrgAgent: Organize Your Multi-Agent System like a Company](https://arxiv.org/abs/2604.01020)**（2026）
  公司式 governance / execution / compliance 三层；层级组织在多数设置下优于扁平，GPT-OSS-120B 在 SQuAD 2.0 上比扁平提升 102.73%、token 降低 74.52%。注意是推理/QA 基准，不是编码。
  → **用途**：给“公会”一个可对照的组织 archetype；也说明结构会同时影响效果和成本。

- **[Toward an Organizational Science of Multi-Agent LLM Systems](https://arxiv.org/abs/2607.25446)**（2026）
  把 Who（组织）/ How（协调）/ Which（算法）解耦；把 Belbin 角色、Mintzberg 协调、RACI 问责做成可执行配置；Adaptive Org Routing 用 bandit 选协议，优于固定协议；最优问责位置会随模型家族翻转。
  → **用途**：这是“动态组织设计”最直接的方法论：组织不能硬编码，要按模型绑定学习和重验。

- **[Fluid Structure, Rigid Record](https://arxiv.org/abs/2608.08516)**（2026）
  持久层（记录 + 常驻专家池）+ 动态层（Task Group + Workflow Protocol）；Permission/Privilege 定义权限边界；Operation/Review/Supervision 三权分立；执行面流动，记录/权限刚性。原型小样本，未声称通用性能。
  → **用途**：直接支撑“执行可重组，治理不能乱”的架构原则。

- **[ARG-Designer: Assemble Your Crew](https://arxiv.org/abs/2507.18224)**（AAAI 2026 oral）
  用条件自回归图生成，从任务 query 直接生成 agent 数量、角色和通信边。
  → **用途**：组织结构生成的技术路线；适合单次任务，不等于长期治理。

- **经典管理理论**：Thompson 的互依性（pooled/sequential/reciprocal）、Mintzberg 的协调机制、Galbraith 的信息处理观、Ouchi 的市场/层级/宗族、权变理论。
  → **用途**：给“分工架构”提供可配置的设计维度，而不是拍脑袋。

---

## 2. 开源项目地图

### 2.1 编码 agent 平台 / 运行时

| 项目 | 定位 | 可借鉴 | 注意 |
| --- | --- | --- | --- |
| [OpenHands](https://github.com/All-Hands-AI/OpenHands)（MIT） | 开源 agent 平台 | 沙箱 runtime、事件流、benchmark harness、多 agent 协调 | 体量大，别整包吞；先读 runtime/event 部分 |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent)（MIT） | 研究型编码 agent | ACI 的接口设计（编辑、搜索、测试） | 偏研究，工程化要自己补 |
| [Aider](https://github.com/Aider-AI/aider)（Apache-2.0） | 终端 pairing | repo map、git-native 编辑、上下文压缩 | 单 agent 为主，无多 agent 协调 |
| [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) | AutoGen/SK 的后继 | 工作流、agent 编排、可观测性 | 新生态，API 仍在演进 |

### 2.2 多 agent 框架

| 项目 | 定位 | 可借鉴 | 注意 |
| --- | --- | --- | --- |
| [LangGraph](https://github.com/langchain-ai/langgraph)（MIT） | 有状态图编排 | checkpoint、human-in-the-loop、恢复 | 适合做任务状态机，不是跨厂商 adapter |
| [CrewAI](https://github.com/crewAIInc/crewAI)（MIT） | 角色/crew 快速原型 | 角色分工、任务委派 | 生产级锁/审计要自己补 |
| [MetaGPT](https://github.com/FoundationAgents/MetaGPT)（MIT） | SOP 流水线 | 角色 SOP、结构化产物 | 重，偏研究 |
| [ChatDev](https://github.com/OpenBMB/ChatDev)（Apache-2.0） | 对话式开发 | chat chain、评审对话 | 研究 artifact，工程化弱 |
| [AgentScope](https://github.com/agentscope-ai/agentscope)（Apache-2.0） | 多 agent 平台 | 消息、分布式、容错 | 需要评估与桌面端集成成本 |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python)（MIT） | 轻量 agent 编排 | handoff、guardrails、tracing | 单进程为主，跨机器要自己搭 |
| [Google ADK](https://github.com/google/adk-python)（Apache-2.0） | 多 agent 开发套件 | 工具、评测、部署 | 与 Google Cloud 绑定较深 |

### 2.3 并行编排 / 看板 / Worktree

| 项目 | 定位 | 可借鉴 | 注意 |
| --- | --- | --- | --- |
| [Claude Squad](https://github.com/smtg-ai/claude-squad)（AGPL-3.0） | tmux + worktree 多会话 TUI | 最简并行会话管理 | AGPL；无 Windows 原生支持 |
| [Vibe Kanban](https://github.com/BloopAI/vibe-kanban)（Apache-2.0） | 看板 + worktree + MCP | 看板列、卡片拆解、浏览器预览 | 据 2026 年中的评测，Bloop 停运后转社区维护、云功能下线；采用前确认 |
| [Composio AO](https://github.com/ComposioHQ/agent-orchestrator)（MIT） | worktree + PR 生命周期 | CI 失败自动重试、PR 看板 | 偏 GitHub 工作流；空闲等待成本 |
| [Emdash](https://github.com/generalaction/emdash)（Apache-2.0） | Electron 多 CLI 桌面 | **每个任务注入 `$EMDASH_PORT`**，直接解决端口冲突 | 无 agent 间协作，各自为战 |
| [Bernstein](https://github.com/chernistry/bernstein)（Apache-2.0） | 确定性调度 + 合并前验证 | Planner→Task Graph→Janitor→Merge，"Janitor" 就是验收 gate | 文档薄；自动模型路由等说法需自验 |
| [Microsoft Conductor](https://github.com/microsoft/conductor)（MIT） | YAML 工作流 | 用 YAML 定义并行组和人工闸门 | v0.1.x，早期 |
| [Agent Kanban](https://github.com/graywrk/agent-kanban) | MCP 任务板 | `claim_task` / `post_progress` / `request_review`，hard assignment | 同名项目多（graywrk / saltbo / appsoftwareltd），别混淆 |
| [Retinue](https://github.com/jklthinking/retinue)（AGPL-3.0） | 任务所有权 + 审计 | 单持有人、仅持有人写入、追加式回执、accept/return | 许可证要审查（旧版 MIT，新版 AGPL） |
| [Nodes](https://github.com/scorp2006/Nodes) | 图数据库原子 ownership | 每个 agent 对代码段原子所有权、死锁检测、MCP hooks | 实验性，但思路与 Agent Guild 的资源锁最接近 |

> 文档第 7 节已经列过的 Friday、Retinue、Paperclip、Beads、Cyrene、Gas Town，这里不重复展开；本表补充的是文档没覆盖、或需要和它对照的项目。Retinue 仍保留在表中，因为它和“单持有人 + 审计”最直接相关。

### 2.4 协议 / 接口

| 协议 | 定位 | 对 Agent Guild |
| --- | --- | --- |
| [MCP](https://modelcontextprotocol.io/) | agent ↔ 工具/上下文 | 接 Codex/DSH 的工具层；本身不唤醒已关闭客户端 |
| [A2A](https://a2a-protocol.org/) | agent ↔ agent | 跨进程/跨厂商任务委派；我们已实测两跳调用（见 `A2A-可行性-附记.md`） |
| [ACP](https://agentclientprotocol.com/) | 编辑器/客户端 ↔ agent | 会话启动/恢复、`session/update`、权限请求；Zed 生态主推 |
| [AG-UI](https://ag-ui.com/) | agent ↔ UI 事件 | 桌面控制台的实时状态流可参考 |

### 2.5 可观测性 / 评估 / 沙箱

| 项目 | 用途 |
| --- | --- |
| [Langfuse](https://github.com/langfuse/langfuse) | trace、评估、成本；V1 可先用最小事件日志 |
| [AgentOps](https://github.com/AgentOps-AI/agentops) | agent 运行观测、回放 |
| [OpenTelemetry GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/) | 跨厂商 trace 语义约定 |
| [E2B](https://github.com/e2b-dev/E2B) / [Daytona](https://github.com/daytonaio/daytona) | 代码沙箱；worktree 之外的隔离选项 |

---

## 3. 映射到 Agent Guild 的模块

| Agent Guild 模块 | 先读什么 | 可直接借鉴 | 仍然缺什么 |
| --- | --- | --- | --- |
| 任务板 / 手动派遣 | CooperBench、Agent Kanban、Vibe Kanban | 任务卡字段、hard assignment、看板列 | 任务切分算法、依赖图 |
| 资源协调 / 文件锁 | Co-Coder、Passes Alone、Informed but Still Colliding、Nodes、Retinue | 写集、依赖边、单持有人、原子 claim | 跨 worktree 的锁协议、租约过期/fencing |
| 隔离工作区 | Claude Squad、Emdash、OpenHands | worktree + 端口注入 + 沙箱 | 合并门禁、集成测试归属 |
| 日志 / 交接 | SASE（MRP/CRP）、MAST、Beads | 摘要 + 证据引用、交接包、事件流 | 跨厂商 schema、uptake 记录 |
| 验收 | Agent-as-a-Judge、Bernstein Janitor、SWE-Marathon | 自动验证 + 独立评审 + 人工清单 | 防 reward hacking、复验排期 |
| 适配层 | ACP、MCP、A2A、OpenHands | 统一会话/任务/事件接口 | 各家 CLI 的唤醒与权限差异 |
| 评测 | Multi-SWE-bench、Terminal-Bench、SWE-Marathon、SWE-Lancer | 多语言/终端/长任务/经济价值 | 自己项目的回归集 |

---

## 4. 和我们已做实验的互相印证

| 我们的实验结论 | 文献里的对应 |
| --- | --- |
| 纯广播“我要写了”≈ 无效，必须有人据此行动 | CooperBench：通信通道堵塞、承诺不兑现；Informed but Still Colliding：awareness 不等于不冲突 |
| 信息 + 退避把丢失从 61% 降到 2.4%，但不归零 | Passes Alone：一条变更消息恢复 82%，仍有 18% 修不好 |
| 通信 + 原子租约才归零 | MAST 的 inter-agent misalignment / verification；SASE 的 MRP/CRP 把“协调”物化成可检查的包 |
| 按文件/目录切分比按行切分稳 | Co-Coder：按依赖图/社区切分，比按文件平均切更好 |
| 契约先行 + 验收先行，零集成返工 | Agentless：简单、可解释的流程往往打败复杂 agent |
| 人工验收不可省 | SWE-Marathon：自验证差、过早终止、13.8% reward hacking |

---

## 5. 建议阅读顺序

**第一天（问题与失败模式，约 3 小时）**
1. MAST（2503.13657）——先建立“失败 taxonomy”
2. CooperBench（2601.13295）——看协作为什么是负收益
3. Passes Alone（2609.25396）——看语义干扰与“变更消息”的边界
4. Informed but Still Colliding（Zenodo）——看 awareness 的局限

**第二天（架构与切分，约 3 小时）**
5. SASE（2509.06216）——ACE/AEE、MRP/CRP，直接对标 Agent Guild
6. Co-Coder（2606.00953）——任务切分算法
7. Agentless（2407.01489）——反过度设计

**第三天（评测与验收，约 2 小时）**
8. SWE-bench / Multi-SWE-bench / Terminal-Bench 2.0（挑两个）
9. SWE-Marathon（长任务 + reward hacking）
10. Agent-as-a-Judge（自动验收）

**第二周（代码，按需）**
- OpenHands runtime + event stream
- SWE-agent 的 ACI
- Claude Squad / Vibe Kanban / Bernstein 的 worktree 与 gate 实现
- ACP / A2A / MCP 的协议文档

---

## 6. 对 Agent Guild V1 的具体建议

1. **先做“单写者 + 原子 claim + 验收 gate”，不要先做自动分派。** 文献里协作失败的主因不是“派得不够快”，而是“派了之后互相踩/验收不可信”。
2. **任务卡带写集和依赖边。** 参考 Co-Coder：按依赖图/社区切分，而不是按文件数平均分；hub 文件单独处理。
3. **把“变更通知”做成协议，而不是聊天。** 每次提交产生一条结构化事件（改了哪些接口/规则、影响哪些任务），订阅方必须重读；参考 Passes Alone 的 82% 恢复率，但别指望 100%。
4. **验收三件套：自动测试 + 独立 verifier + 人工 checklist。** 参考 Agent-as-a-Judge 与 Bernstein 的 Janitor；验收记录要能防 reward hacking（SWE-Marathon 的 13.8%）。
5. **日志分两层：事件流（机器）+ 摘要（人和 agent）。** 参考 SASE 的 MRP/CRP、MAST 的失败分类；额外记录“消息是否被接收/采纳”（uptake），这是 Informed 论文专门编码的失败类型。
6. **评测别只用 SWE-bench。** 多语言用 Multi-SWE-bench，终端/桌面用 Terminal-Bench 2.0，长任务用 SWE-Marathon；自己的项目要有一小组固定回归任务。

---

## 7. 名字与竞品提醒

- **“Agent Guild”这个名字已经有人用**：
  - [AgentTanuki/agent-guild](https://github.com/AgentTanuki/agent-guild)：面向 agent 的信任/结算层（discover、vet、pay、reputation）；
  - [mathomhaus/guild](https://github.com/mathomhaus/guild)：给编码 agent 做共享上下文、记忆和任务协调（Go + SQLite）——和 Agent Guild 的“共享上下文 + 任务协调”最接近，值得直接读；
  - 另有一个 Steam 游戏《Agent Guild: World》和一个 PyPI 包 `agent-guild`。
  建议先查 GitHub org / 域名 / 商标，再决定是否改名或加限定词。
- **Agent Kanban 有多个同名项目**：文档链接的是 `graywrk/agent-kanban`；另有两个完全不同的 `saltbo/agent-kanban` 和 `appsoftwareltd/vscode-agent-kanban`，别混淆。
- **AutoGen 已进入迁移期**：微软把 AutoGen + Semantic Kernel 迁到 Microsoft Agent Framework；新项目直接看后者。
- **Crystal 已弃用**：2026 年 2 月归档，后继是 Nimbalyst；不要在新项目上用 Crystal。

---

## 8. 参考链接汇总

**论文**
- MAST：<https://arxiv.org/abs/2503.13657>
- CooperBench：<https://arxiv.org/abs/2601.13295>
- Passes Alone, Fails Together：<https://arxiv.org/abs/2609.25396>
- Informed but Still Colliding（复现包）：<https://zenodo.org/records/23467694>
- OSS MAS issues：<https://arxiv.org/abs/2610.00905>
- SASE：<https://arxiv.org/abs/2509.06216>
- Co-Coder：<https://arxiv.org/abs/2606.00953>
- Agentless：<https://arxiv.org/abs/2407.01489>
- MAS 协作综述：<https://arxiv.org/abs/2501.06322>
- SWE-bench：<https://arxiv.org/abs/2310.06770>
- Multi-SWE-bench：<https://arxiv.org/abs/2504.02605>
- Terminal-Bench 2.0：<https://arxiv.org/abs/2601.11868>
- SWE-Marathon：<https://arxiv.org/abs/2606.07682>
- SWE-Lancer：<https://arxiv.org/abs/2502.12115>
- TheAgentCompany：<https://arxiv.org/abs/2412.14161>
- Agent-as-a-Judge：<https://arxiv.org/abs/2410.10934>
- OpenHands：<https://arxiv.org/abs/2407.16741>
- SWE-agent：<https://arxiv.org/abs/2405.15793>
- AutoGen：<https://arxiv.org/abs/2308.08155>
- MetaGPT：<https://arxiv.org/abs/2308.00352>
- ChatDev：<https://arxiv.org/abs/2307.07924>

**开源项目**
- OpenHands：<https://github.com/All-Hands-AI/OpenHands>
- SWE-agent：<https://github.com/SWE-agent/SWE-agent>
- Aider：<https://github.com/Aider-AI/aider>
- LangGraph：<https://github.com/langchain-ai/langgraph>
- CrewAI：<https://github.com/crewAIInc/crewAI>
- MetaGPT：<https://github.com/FoundationAgents/MetaGPT>
- ChatDev：<https://github.com/OpenBMB/ChatDev>
- AgentScope：<https://github.com/agentscope-ai/agentscope>
- OpenAI Agents SDK：<https://github.com/openai/openai-agents-python>
- Google ADK：<https://github.com/google/adk-python>
- Microsoft Agent Framework：<https://github.com/microsoft/agent-framework>
- Claude Squad：<https://github.com/smtg-ai/claude-squad>
- Vibe Kanban：<https://github.com/BloopAI/vibe-kanban>
- Composio AO：<https://github.com/ComposioHQ/agent-orchestrator>
- Emdash：<https://github.com/generalaction/emdash>
- Bernstein：<https://github.com/chernistry/bernstein>
- Microsoft Conductor：<https://github.com/microsoft/conductor>
- Agent Kanban：<https://github.com/graywrk/agent-kanban>
- Retinue：<https://github.com/jklthinking/retinue>
- Nodes：<https://github.com/scorp2006/Nodes>
- mathomhaus/guild：<https://github.com/mathomhaus/guild>
- AgentTanuki/agent-guild：<https://github.com/AgentTanuki/agent-guild>
- MCP：<https://modelcontextprotocol.io/>
- A2A：<https://a2a-protocol.org/>
- ACP：<https://agentclientprotocol.com/>
- AG-UI：<https://ag-ui.com/>
- Langfuse：<https://github.com/langfuse/langfuse>
- AgentOps：<https://github.com/AgentOps-AI/agentops>
- OpenTelemetry GenAI：<https://opentelemetry.io/docs/specs/semconv/gen-ai/>
- E2B：<https://github.com/e2b-dev/E2B>
- Daytona：<https://github.com/daytonaio/daytona>
