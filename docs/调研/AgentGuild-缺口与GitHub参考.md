# Agent Guild 缺口清单 × GitHub 可复用项目

> 回答：我们现在缺的东西，能不能在 GitHub 上找项目参考、组合或直接解决？
> 结论：**大约七成可以“ adopt/combine ”，剩下三成是必须自建的“协作语义”。**
> 截至 2026-11-08；许可证、维护状态以仓库当时页面为准。

---

## 0. 一句话结论

**基础设施（持久化执行、状态/锁、沙箱、可观测、策略、UI、求解器）几乎都能找到成熟项目；真正缺的是“agent 协作语义”——任务/写集模型、lease+fencing 生命周期、组织配置、文档 region/MVCC/合并队列、验收工作流。**

所以策略不是“全部自己写”，也不是“拼一堆开源就完事”，而是：

```text
Adopt（直接用）：沙箱、可观测、策略、求解器、CRDT 库、看板 UI
Combine（粘合）：durable execution + Postgres 状态 + MCP/ACP/A2A + 合并工具
Build（自建）：任务/写集、租约/围栏、组织运行时、文档合并队列、验收流
```

---

## 1. 缺口矩阵

| # | 缺口 | 候选 GitHub 项目 | 建议 |
| --- | --- | --- | --- |
| 1 | 长任务持久化/崩溃恢复/定时器/审批等待 | [Temporal](https://github.com/temporalio/temporal)、[Restate](https://github.com/restatedev/restate)、[DBOS](https://github.com/dbos-inc/dbos-transact-py)、[Hatchet](https://github.com/hatchet-dev/hatchet) | **Combine**：先用 Postgres + asyncio；需要跨小时/崩溃恢复时接 Temporal/Restate |
| 2 | 任务/租约/事件状态 | [PostgreSQL](https://github.com/postgres/postgres) 行锁/`SKIP LOCKED`、[etcd](https://github.com/etcd-io/etcd)、[pgmq](https://github.com/tembo-io/pgmq) | **Adopt**：Postgres 做事实源；etcD 只在多机强租约时上 |
| 3 | 文档并发/合并 | [Yjs](https://github.com/yjs/yjs)、[Automerge](https://github.com/automerge/automerge)、[Loro](https://github.com/loro-dev/loro)、[ShareDB](https://github.com/share/sharedb)、[mergiraf](https://github.com/mergiraf/mergiraf)、[difftastic](https://github.com/Wilfred/difftastic) | **Combine**：实时协作用 CRDT；agent 批量编辑用 MVCC + diff3/mergiraf |
| 4 | 工作区/沙箱隔离 | [E2B](https://github.com/e2b-dev/E2B)、[Daytona](https://github.com/daytonaio/daytona)、[microsandbox](https://github.com/microsandbox/microsandbox)、[Docker](https://github.com/moby/moby) | **Adopt**：worktree 做 Git 隔离；容器/微 VM 做安全边界 |
| 5 | Agent 接入/协议 | [FastMCP](https://github.com/jlowin/fastmcp)、[mcp-agent](https://github.com/lastmile-ai/mcp-agent)、[ACP](https://github.com/agentclientprotocol/agent-client-protocol)、[A2A](https://github.com/a2aproject/A2A) | **Adopt + 薄适配**：MCP 工具、ACP 编辑器、A2A 跨进程 |
| 6 | 人工审批/验收 | [HumanLayer](https://github.com/humanlayer/humanlayer)、LangGraph interrupts、Temporal signals | **Combine**：审批 API + 验收队列；不要自己造通知/回调 |
| 7 | 可观测/评测 | [OpenTelemetry](https://github.com/open-telemetry/opentelemetry-python)、[Langfuse](https://github.com/langfuse/langfuse)、[Phoenix](https://github.com/Arize-ai/phoenix)、[Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai)、[promptfoo](https://github.com/promptfoo/promptfoo) | **Adopt**：OTel 埋点 → Langfuse/Phoenix；Inspect/promptfoo 做回归 |
| 8 | 权限/策略 | [OPA](https://github.com/open-policy-agent/opa)、[Cedar](https://github.com/cedar-policy/cedar)、[Casbin](https://github.com/casbin/casbin) | **Adopt**：先表驱动 RBAC；规则复杂再上 OPA/Cedar |
| 9 | 调度/优化 | [OR-Tools CP-SAT](https://github.com/google/or-tools)、[PuLP](https://github.com/coin-or/pulp) | **Adopt**：CP-SAT 解 RCPSP/分配；不要自己写求解器 |
| 10 | 看板/UI | [Plane](https://github.com/makeplane/plane)、[Focalboard](https://github.com/mattermost/focalboard)、[Vikunja](https://github.com/go-vikunja/vikunja)、[Vibe Kanban](https://github.com/BloopAI/vibe-kanban)、[Agent Kanban](https://github.com/graywrk/agent-kanban) | **Combine**：Plane/Focalboard 做通用看板；agent 专用看板做参考 |
| 11 | 事件日志/审计 | Postgres outbox、[NATS JetStream](https://github.com/nats-io/nats-server) | **Adopt**：先 Postgres 事件表 + outbox；量大再上 NATS |
| 12 | 密钥/凭证 | [Vault](https://github.com/hashicorp/vault)、[SOPS](https://github.com/getsops/sops) | **Adopt**：按任务注入短期凭证；不要写进 prompt |
| 13 | 合并队列/CI | GitHub merge queue、Mergify、Bors | **Adopt**：在 GitHub 上直接用；自建成本高 |
| 14 | Agent 编排图 | [LangGraph](https://github.com/langchain-ai/langgraph)、[Microsoft Agent Framework](https://github.com/microsoft/agent-framework) | **Combine**：LangGraph 做状态图/checkpoint/HITL；只选一个，不要叠床架屋 |

> 注意：AutoGen 已并入 Microsoft Agent Framework；Vibe Kanban 在 Bloop 停运后转社区维护；Crystal 已弃用改 Nimbalyst。采用前确认维护状态。

---

## 2. 逐项：怎么用、坑在哪

### 2.1 持久化执行：Temporal / Restate / DBOS / Hatchet

**为什么需要**：agent 一次任务可能跑几分钟到几小时；要跨崩溃恢复、定时器、审批等待、重试。自己写状态机很容易漏。

- **Temporal**：最成熟，workflow/activity、signal、timer、重试；Python/TS SDK。适合“长任务 + 人工审批 + 定时租约续期”。坑：运维重（Cassandra/Postgres + 服务），概念多。
- **Restate**：轻量 durable execution，virtual object 天然适合“每个文档/任务一个单写者 actor”。坑：生态较新。
- **DBOS**：Postgres 原生 durable workflows，部署简单。坑：相对年轻。
- **Hatchet**：任务队列/DAG，偏批处理；durable workflow 语义不如前三个。

**建议**：P0 不用上；用 Postgres + asyncio 足够。等出现“跑 30 分钟以上、崩溃要恢复、要等人工审批”时再接 Temporal/Restate。

### 2.2 状态与租约：Postgres 是默认答案

- 任务表、租约表、事件表、fencing 计数器都放 Postgres；
- `SELECT ... FOR UPDATE SKIP LOCKED` 做 claim；
- 租约 = `(resource, owner, expiry, fencing_token)`；续约更新 expiry；过期回收；
- 提交时校验 `fencing_token`，拒绝旧持有者；
- 事件表 + outbox，异步发通知。

**不建议**：Redis Redlock 做唯一事实源（争议大）；文件锁做跨机锁。etcD 只在多机强一致租约时用。

### 2.3 文档并发：CRDT 还是 MVCC？

两条路线：

- **实时协作**：Yjs / Automerge / Loro / ShareDB。适合人 + agent 同时看同一份文档、光标/增量同步。坑：CRDT 的“合并”是字节/结构级，不保证语义正确；Markdown/富文本要用对应 binding。
- **Agent 批量编辑**：MVCC + 提交时版本校验 + diff3/mergiraf。适合“读快照 → 离线改 → 提交”。坑：冲突需要合并/裁决；结构编辑要树锁。

**建议**：Document Kernel 用 MVCC 做事实源；实时预览层可以接 Yjs；代码/结构化文档用 mergiraf 做结构化合并；文本用 diff3。

### 2.4 沙箱：worktree 不是安全边界

- **Git worktree**：便宜、适合分支隔离；不是安全边界，仍可访问同一文件系统/端口。
- **Docker/devcontainer**：本地隔离，成熟；端口/资源要自己管。
- **E2B / Daytona / microsandbox**：托管/本地微 VM，适合不可信代码；注意成本和网络。
- **Firecracker/gVisor**：强隔离，运维重。

**建议**：V1 用 worktree + Docker；需要跑不可信代码/多租户再上 E2B/Daytona。

### 2.5 协议：MCP、ACP、A2A 各管一段

- **MCP**：agent ↔ 工具/上下文；FastMCP 快速写 server；mcp-agent 提供 pattern。
- **ACP**：编辑器/客户端 ↔ agent；Zed 生态；适合“在 IDE 里派活”。
- **A2A**：agent ↔ agent；跨进程/跨厂商；我们已实测两跳。

**建议**：工具用 MCP，编辑器用 ACP，跨机/外部 agent 用 A2A；不要用一个协议硬套所有层。

### 2.6 人工审批：HumanLayer + 看板

- HumanLayer 提供 approval API、Slack/Email 通知、webhook；适合“agent 停下来等人批准”。
- LangGraph interrupt / Temporal signal 适合工作流内等待。
- 验收队列可以放在 Plane/Agent Kanban，或自建轻量页面。

**建议**：审批用 HumanLayer，验收队列先用 Plane/自建表；不要自己造通知系统。

### 2.7 可观测/评测：OTel + Langfuse/Phoenix + Inspect

- OpenTelemetry GenAI semantic conventions 做统一埋点；
- Langfuse / Phoenix 做 trace、成本、评估；
- Inspect AI / promptfoo 做回归评测（CI）。

**建议**：从第一天就埋 OTel；否则后面无法算 cost/quality/headroom。

### 2.8 策略/权限：先表，后 OPA/Cedar

- V1：表驱动（agent × resource × action），代码里 enforce；
- 复杂后：OPA（Rego）或 Cedar（策略语言）；Casbin 适合传统 RBAC/ABAC。
- 权限必须在工具层强制执行，不能靠 prompt。

### 2.9 调度/优化：OR-Tools CP-SAT

- 任务 ≤100、agent ≤10 时，CP-SAT 足够；
- 目标/约束按第 7 节的形式化写；
- 输出 Pareto 前沿，不要只给一个解。

### 2.10 UI：Plane / Focalboard / Vikunja / Agent Kanban

- Plane：完整项目管理（issue/cycle/module），适合做“公会控制台”；
- Focalboard：轻量看板；
- Vikunja：任务/列表；
- Vibe Kanban / Agent Kanban：agent 专用看板，参考它们的 worktree/claim 设计。

**建议**：V1 用 Plane 或 Focalboard 做前台，协调器通过 API 同步任务；不要从零写看板。

---

## 3. 组合架构（推荐）

```text
┌─────────────────────────────────────────────┐
│ UI：Plane / Focalboard / 自建轻量看板        │
└──────────────────┬──────────────────────────┘
                   │ REST/MCP
┌──────────────────▼──────────────────────────┐
│ Coordinator（FastAPI + MCP）                 │
│  Task/Lease/Event │ Scheduler │ Policy       │
└───────┬──────────────────────┬──────────────┘
        │                      │
┌───────▼────────┐   ┌─────────▼──────────────┐
│ Postgres        │   │ Document Kernel         │
│ tasks/leases/   │   │ region tree + MVCC +    │
│ events/outbox   │   │ merge queue             │
└───────┬────────┘   └─────────┬──────────────┘
        │                      │
┌───────▼────────┐   ┌─────────▼──────────────┐
│ Temporal/Restate│   │ Sandbox：worktree/E2B   │
│ （按需）        │   │ /Daytona/Docker         │
└───────┬────────┘   └─────────┬──────────────┘
        │                      │
┌───────▼──────────────────────▼──────────────┐
│ Agents：Codex / DSH / Claude Code            │
│ 接入：MCP / ACP / A2A / CLI                  │
└─────────────────────────────────────────────┘
        │
┌───────▼─────────────────────────────────────┐
│ Observability：OTel → Langfuse/Phoenix       │
│ Approval：HumanLayer                         │
│ Policy：表驱动 → OPA/Cedar                   │
└─────────────────────────────────────────────┘
```

---

## 4. 哪些不要自建，哪些必须自建

### 不要自建（直接用/薄封装）

- workflow/durable execution 引擎；
- 沙箱/容器/微 VM；
- CRDT/OT 库；
- diff/merge 算法（diff3、mergiraf、difftastic）；
- 策略引擎；
- 求解器（CP-SAT）；
- 指标/trace 后端；
- 看板 UI；
- 通知/审批通道。

### 必须自建（差异化）

1. **任务/写集模型**：任务卡带 write set、依赖边、DoD；这是 Agent Guild 的核心数据模型。
2. **lease + fencing 生命周期**：谁持有、TTL、续约、回收、提交校验；要跟 agent 任务状态机绑定。
3. **组织配置/运行时**：archetype、角色、问责、重组策略；没有现成产品。
4. **Document Kernel 的 region/MVCC/merge queue**：CRDT 库不提供“章节级 MVCC + 合并队列”。
5. **验收工作流**：自动验证 + 人工 checklist + 退回/复验；HumanLayer 只提供审批通道，不提供验收语义。
6. **headroom 度量**：oracle − best fixed；这是决定要不要做动态组织的依据。

---

## 5. 两周 PoC 组合清单

**目标**：3 个 agent 改同一份 Markdown，能 claim、能租约、能提交、能合并、能审批、能看 trace。

- **Day 1–2**：Postgres schema（tasks/leases/events/outbox）+ FastAPI + MCP server；
- **Day 3–4**：claim/lease/fencing + 3 个 worker 压测（复用 `coordination` 的思路）；
- **Day 5–6**：Document Kernel：region tree + MVCC 提交 + diff3/mergiraf 合并；
- **Day 7–8**：HumanLayer 审批 + Plane/Focalboard 任务同步；
- **Day 9–10**：OTel → Langfuse/Phoenix；跑 `doc-sim` 的四种策略对比；
- **交付**：一个能演示“claim → 编辑 → 提交 → 冲突 → 审批 → trace”的闭环。

**先不要上**：Temporal、Kubernetes、多机 etcD、完整 OPA、复杂 CRDT。等 P0 数据出来再说。

---

## 6. 许可证/维护风险清单

- **AGPL**：Plane、Vikunja、Claude Squad、Retinue——嵌入/托管前要审。
- **ELv2**：部分 Agent Kanban 类项目——不能当托管服务卖。
- **GPL**：mergiraf（以仓库为准）——链接/分发要注意。
- **开源核心 + 商业版**：Temporal、Langfuse、E2B、Daytona——注意功能边界。
- **维护状态**：Vibe Kanban 社区维护、Crystal 已弃用、AutoGen 迁到 Microsoft Agent Framework。
- **数据合规**：把代码/文档发给托管 sandbox/LLM 前，确认数据边界。

---

## 7. 参考链接

**Durable execution**
- Temporal：<https://github.com/temporalio/temporal>
- Restate：<https://github.com/restatedev/restate>
- DBOS：<https://github.com/dbos-inc/dbos-transact-py>
- Hatchet：<https://github.com/hatchet-dev/hatchet>

**状态/队列**
- PostgreSQL：<https://github.com/postgres/postgres>
- etcd：<https://github.com/etcd-io/etcd>
- pgmq：<https://github.com/tembo-io/pgmq>

**文档/合并**
- Yjs：<https://github.com/yjs/yjs>
- Automerge：<https://github.com/automerge/automerge>
- Loro：<https://github.com/loro-dev/loro>
- ShareDB：<https://github.com/share/sharedb>
- Mergiraf：<https://github.com/mergiraf/mergiraf>
- Difftastic：<https://github.com/Wilfred/difftastic>

**沙箱/工作区**
- E2B：<https://github.com/e2b-dev/E2B>
- Daytona：<https://github.com/daytonaio/daytona>
- microsandbox：<https://github.com/microsandbox/microsandbox>
- Docker：<https://github.com/moby/moby>

**协议/编排**
- FastMCP：<https://github.com/jlowin/fastmcp>
- mcp-agent：<https://github.com/lastmile-ai/mcp-agent>
- ACP：<https://github.com/agentclientprotocol/agent-client-protocol>
- A2A：<https://github.com/a2aproject/A2A>
- LangGraph：<https://github.com/langchain-ai/langgraph>
- Microsoft Agent Framework：<https://github.com/microsoft/agent-framework>

**审批/可观测/策略**
- HumanLayer：<https://github.com/humanlayer/humanlayer>
- OpenTelemetry：<https://github.com/open-telemetry/opentelemetry-python>
- Langfuse：<https://github.com/langfuse/langfuse>
- Phoenix：<https://github.com/Arize-ai/phoenix>
- Inspect AI：<https://github.com/UKGovernmentBEIS/inspect_ai>
- promptfoo：<https://github.com/promptfoo/promptfoo>
- OPA：<https://github.com/open-policy-agent/opa>
- Cedar：<https://github.com/cedar-policy/cedar>
- Casbin：<https://github.com/casbin/casbin>

**UI/优化/密钥**
- Plane：<https://github.com/makeplane/plane>
- Focalboard：<https://github.com/mattermost/focalboard>
- Vikunja：<https://github.com/go-vikunja/vikunja>
- Vibe Kanban：<https://github.com/BloopAI/vibe-kanban>
- Agent Kanban：<https://github.com/graywrk/agent-kanban>
- OR-Tools：<https://github.com/google/or-tools>
- Vault：<https://github.com/hashicorp/vault>
- SOPS：<https://github.com/getsops/sops>
