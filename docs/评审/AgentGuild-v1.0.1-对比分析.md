# Agent Guild V1.0.1 vs 我们的成果：对齐、冲突与改造建议

> 输入：
> - Agent Guild V1.0.1《计划与架构总结》（2026-11-08，394 行）
> - 我们的 [p0-coordinator](../../p0-coordinator/README.md)、[当前状态](../现状/当前状态-2026-11-08.md)、[总报告](../报告/多agent协作-总报告.md)
>
> 结论先行：**我们做的是“协调内核 + 验证/审批原型”，V1 要的是“桌面产品 + Run/Candidate/Git 合入门禁”。核心规则约一半可复用，产品层几乎从零。**
>
> 注：§1–§9 保留 2026-11-08 的初始分析；§10 是最新进度，已补齐 Run/预约/Candidate/Merge/Worktree/lifecycle/双重门。

---

## 1. 执行摘要

四句话：

1. **对得上的**：本地 SQLite、任务/租约/fencing/事件、人工验收、自动验证、本机单用户——这些我们有可运行实现和测试。
2. **关键错位**：我们的 `runs` 是**遥测记录**，V1 的 Run 是**一次执行实体**；我们没有 Candidate/SHA/Worktree/Merge，也没有 Codex/DSH 适配器和 Electron 界面。
3. **我们的实验反而验证了 V1 的几个决定**：广播无效 → 必须中心化状态；锁不解决语义合并 → 必须 Candidate + 人工验收；动态组织 headroom≈0 → V1 先做人工派遣是对的。
4. **建议**：保留 p0 作为“协调内核原型”，按 V1 领域模型补 Run/Candidate/Reservation/Merge；MCP/CLI 只做开发接口，不替代桌面产品；预算/Pareto 后置。

---

## 2. 一张图看差异

```text
V1：
  管理员 → React UI → Electron 主进程 → 协作核心（utilityProcess）
    → Agent Adapter → Codex / DSH
    → 每 Run 独立 Worktree → Candidate(SHA) → 自动验证/审查
    → 人工验收 → 管理员确认合入 → Accepted

我们：
  CLI / MCP → Coordinator(SQLite) → 外部 worker（自己 claim）
    → 同一工作空间/写集 → verifier checks → N-of-M 审批 → done
```

差异不在“有没有任务板”，而在**执行实体、文件边界、候选/合入**这三层。

---

## 3. 逐项对齐表

| V1 能力 | V1 要求 | 我们现状 | 判定 | 建议 |
| --- | --- | --- | --- | --- |
| 桌面与界面 | Electron + React + TS | 无 UI，MCP + CLI | ❌ 缺 | 核心嵌入 utilityProcess，UI 另做 |
| 本地数据 | SQLite 结构化状态 | SQLite | ✅ 可复用 | 加迁移/备份 |
| Task | 作者、审查者、依赖、范围、DoD、状态、current Run、代次 | 有任务/owner/write_set/deps/checks/status | 🟡 部分 | 补 author/reviewer/current_run/generation/状态机 |
| Run | 一次执行；重试/换 Agent/接续新建 Run；旧 Run 迟到事件无效 | `runs` 是遥测（cost/quality/duration） | ❌ 语义冲突 | **拆成 executions + run_metrics**，最高优先级 |
| 文件预约 | 仓库标识 + Git 相对路径；父子目录；持有到合入/撤回 | `write_set` 字符串 + TTL lease + 计划 reservation | 🟡 部分 | 加 repo/path 预约、父子冲突、不随验收释放 |
| Worktree | 每作者 Run 独立分支/Worktree；管理副本 | 同工作空间，无 Git 管理 | ❌ 缺 | 按 V1 做，不要用同工作空间替代 |
| Candidate | SHA 绑定、冻结、测试/审查/人验同 SHA | 无候选、无 SHA | ❌ 缺 | 新增 Candidate + Evidence |
| 合入 | 人工验收后管理员单独确认；基线变化重验 | 审批通过即 done | ❌ 缺 | 新增 MergeAttempt + 合入门禁 |
| 自动验证 | 平台测试 + Agent 审查 | verifier checks（file/command） | 🟡 可复用 | 绑定候选 SHA；补 diff 范围检查 |
| 人工验收 | 所有任务都要 | N-of-M 审批，可配置 | 🟡 可复用 | 默认全任务；与“合入确认”分开 |
| 证据/事件 | 自报/平台/人工分离；证据文件 + 引用 | events + runs + verifications | 🟡 部分 | 加 evidence 表、SHA/命令/退出码引用 |
| 恢复/代次 | 旧 Run 事件不能改新状态；崩溃恢复 | fencing token 防旧 lease | 🟡 部分 | 加 execution generation，不只 lease |
| Agent Adapter | Codex、DSH 双认证、事件/审批/停止 | 无适配器，只有 CLI | ❌ 缺 | M0 先做，别先做 UI |
| 并发上限 | 默认 2 | 无上限 | 🟡 缺 | 项目级并发配额 |
| 派遣模型 | 管理员指定并启动 | worker 自己 claim | 🟡 冲突 | 核心提供 runs.start；claim 可留作执行门 |
| 预算/估计器/MILP | **V1 未要求** | 已做 | 🟡 超前 | 后置到 V1 之后，不要挤占 M0/M1 |
| A2A | **V1 未要求** | 有实验 | 🟡 超前 | 后置到跨边界场景 |

---

## 4. 四个关键架构冲突

### 4.1 `runs` 的语义冲突（最高优先级）

我们的 `runs` 表：

```text
task_id, worker, cost_usd, quality, duration_s, tokens, diff_lines...
```

这是**运行结果遥测**，一个任务可以有多次，但它不是“执行实体”：没有 workspace、没有 session、没有 process、没有 generation、没有开始/结束原因。

V1 的 Run 是：

```text
所属任务、角色、Agent 配置、工作区、厂商会话、状态、开始/结束时间、退出原因
```

**建议**：

- 新表 `executions`（V1 Run）：一次执行一条，带 workspace/session/generation/status；
- 现有 `runs` 改名 `run_metrics`（或保留为 metrics 子表），通过 `execution_id` 关联；
- 旧 Run 的迟到事件用 `generation` 拒绝，fencing token 只解决 lease，不解决 execution。

这是“改个表名”解决不了的，是**领域模型错位**。

### 4.2 文件协调粒度

我们的 `write_set` 是字符串列表，只做**完全相等**的重叠检查；没有父子目录、没有仓库相对路径、没有 worktree、没有越界 diff 检查。

V1 要求：

- 仓库标识 + Git 相对路径；
- 文件/目录范围，父子目录冲突；
- Windows 大小写不敏感；
- 重命名检查旧/新路径；
- 预约属于任务，持有到合入/撤回，**人工验收期间不释放**。

我们的 lease TTL 会过期、plan reservation 也会过期——这和 V1 的“预约持有到合入”直接冲突。**建议把“执行租约”和“文件预约”拆成两个概念**：lease 管执行占用，reservation 管文件范围。

### 4.3 “完成”的定义

我们的链路：

```text
commit → verification → review/approve → done
```

V1 的链路：

```text
Run 结束 → Candidate(SHA) → 自动验证/审查 → 人工验收 → ReadyToMerge → 管理员确认 → Accepted
```

在 V1 里，**Agent 自报成功、测试通过、人工验收通过，都不等于 Accepted**；只有实际合入成功才 Accepted。我们目前没有 Candidate、没有 SHA、没有 MergeAttempt，`done` 太早。

### 4.4 派遣模型

我们是 **worker-pull**：worker 自己 `claim_task`。
V1 是 **admin-push**：管理员指定 Agent，核心 `runs.start` 校验前置条件、预约范围、启动执行。

两者不是简单二选一：V1 的 `runs.start` 应该成为唯一入口；`claim` 可以保留为内部执行门的实现细节，但不能让 worker 绕过管理员派遣。

---

## 5. 可直接复用 / 需要改造 / 必须新建

### 可直接复用

- SQLite `BEGIN IMMEDIATE` 原子 claim、leases、fencing token、事件日志；
- `worker_cli.py` / MCP 的调用形态（作为开发接口，不是产品 API）；
- `verifier.py` 的 file/regex/command checks、allowlist、timeout、输出/环境限制；
- N-of-M 审批 + TTL + `request_approvals`（改成 V1 的人工验收/合入确认）；
- 91 个测试作为核心规则回归基线。

### 需要改造

- Task 状态机：`ready/claimed/review/verification/done` → V1 的 `Ready/Assigned/InProgress/AutoReview/HumanReview/ReadyToMerge/Accepted`；
- 审批：从“任务级 N-of-M”改成“候选级人工验收 + 单独合入确认”；
- 预留：从“plan reservation”改成“repo/path 文件预约”，不随 TTL 释放；
- 证据：events 里补 SHA、命令、退出码、输出引用；
- 预算：保留，但后置。

### 必须新建

- `executions`（V1 Run）；
- `workspaces` / Worktree 管理；
- `candidates` / `merge_attempts`；
- `file_reservations`（repo+path）；
- `evidence`；
- Codex / DSH 适配器；
- Electron utilityProcess + IPC；
- Windows Job Object 生命周期；
- 单实例、凭据保护、双认证。

---

## 6. 我们的实验对 V1 的启示

这部分是“我们的证据”能直接帮 V1 做判断的地方：

1. **广播无效（52.2% vs 52.4%）** → V1 把状态放中心、不靠 agent 聊天，是对的。
2. **锁不解决语义合并（锁检测 0 / 静默覆盖 4）** → V1 坚持 Candidate + 人工验收 + 单独合入，是对的。
3. **动态组织 headroom≈0** → V1 先做管理员手动派遣、不做自动选人，是对的。
4. **门控实验** → 数据少时不要相信学习策略；V1 不把自动选人放进 V1，是对的。
5. **真实 verifier root 配错** → 证据必须绑定 SHA/路径/命令；V1 要求“证据对应候选 SHA”是必要的。
6. **同工作空间并行可行** → 说明 V1 的 Worktree 不是唯一解；但 V1 要的是“越界不能合入 + 合入可审计”，Worktree + Candidate 更稳。**建议 V1 保留 Worktree，同时把我们的写集/租约作为任务级互斥的补充，而不是替代。**

---

## 7. 建议的整合路线

### 第 0 步：不要先改 UI

V1 的 M0 是三条真实接入链路（Codex、DSH API Key、DSH 账户）。我们目前没有适配器，先做 M0，别把时间花在 Electron 皮肤上。

### 第 1 步：把 p0 改造成 V1 核心的“规则层”

- 保留 `tasks/leases/events`；
- 新增 `executions`、`file_reservations`、`candidates`、`merge_attempts`、`evidence`；
- Task 状态机对齐 V1；
- 测试从 74 个扩到“迟到事件/基线变化/脏仓库”等 V1 必测场景。

### 第 2 步：核心嵌入 Electron

- 协作核心放 `utilityProcess`；
- UI 不直接改 SQLite、不启动任意命令；
- MCP/CLI 只保留为开发/自动化接口，不作为产品对外 API。

### 第 3 步：双 Agent 验收

- 用我们已有的 live-demo 编排方式做 V1 场景；
- 每个场景绑定 Candidate SHA；
- 通过后再做安装包。

### 第 4 步：Post-V1

- 预算、估计器、MILP/3D Pareto、A2A；
- 这些是我们的增量，不是 V1 的门槛。

---

## 8. 差距清单（按优先级）

**第一优先：不做就不能叫 V1 Core**

1. `executions` 与 `run_metrics` 分离；
2. repo/path 文件预约（父子目录、大小写、重命名）；
3. `candidates` + `merge_attempts` + SHA 绑定；
4. `evidence` 表；
5. Task 状态机对齐；
6. 越界 diff 检查。

**第二优先：产品闭环**

7. Codex / DSH 适配器（M0）；
8. Electron utilityProcess + IPC；
9. Job Object / 进程清理；
10. 并发上限 2；
11. 单实例与凭据保护。

**第三优先：Post-V1**

12. 预算/估计器/MILP/Pareto；
13. A2A 跨边界；
14. Document Kernel。

---

## 9. 结论

- 我们的成果**不是 V1 的替代品**，而是 V1 的**协调内核原型 + 验证/审批原型**；
- 最强的资产是：lease/fencing/events、verifier、N-of-M 审批、真实 agent live demo；
- 最大的缺口是：**Run 执行实体、Candidate/SHA、Worktree/合入、适配器、桌面**；
- 最危险的误区是：把 `runs` 当 Run、把 `done` 当 Accepted、把 `write_set` 当文件预约、把 MCP/CLI 当产品接口。

一句话：**我们证明“协调 + 验收 + 问责”可以跑；V1 要把它变成“执行 + 候选 + 合入”的产品。先补 Run/Candidate/Merge，再谈 UI 和优化。**

---

## 10. 进度更新（2026-11-08）

写完本文后，已落地 V1 Core 的前两块：

- ✅ `executions`：一次 Run 一条，agent/role/workspace/session/generation/status；同一任务同时只允许一个 author Run；
- ✅ `runs.execution_id`：遥测关联到具体 Run；
- ✅ `file_reservations`：repo + Git 相对路径；父子目录、大小写、全有或全无；持有到显式释放；
- ✅ `candidates` / `evidence` / `merge_attempts`：Candidate 冻结、证据绑定 `candidate_sha`、人工批准、base SHA 校验、合入成功才 merged；
- ✅ `workspaces`：真实 `git worktree`、改动文件对比 base、越界检查、commit、从 worktree 生成 Candidate；
- ✅ `tasks.lifecycle`：Ready→Assigned→InProgress→AutoReview→HumanReview→ReadyToMerge→Accepted，非法迁移被拒绝；
- ✅ `write_set` 与 `file_reservations` 双重门：`claim_task` / `start_execution` 同时检查；
- ✅ Adapter：DSH headless 已跑 5 个任务；Codex 0.167.0 + DeepSeek 官方 Responses 跑了 1 个任务；**DSH + Codex-DeepSeek + Codex-智谱 3-agent 并发全部合入**；`CodexAppServerAdapter` 已跑通 V1 端到端（JSON-RPC + 真实 cherry-pick）；
- ✅ Electron 17.4.11 / 22.3.27 / 28.3.3 / 45.2.3 都已安装；`--no-sandbox --disable-gpu --version` 可用；但 BrowserWindow 在本机崩溃（0xC0000005），**Electron 窗口未通过**；**WebView2 替代壳已通过 `--smoke`**；
- ✅ 36 个 V1 切片测试；总计 91 个测试；`merge_candidate_git()` 真实 cherry-pick + 回滚。

仍未做 / 未验证：

- ❌ 真实小仓库的 5–10 个任务（目前 DSH 5 个 + Codex 1 个，都是 demo repo）；
- ❌ Electron 真实窗口/IPC/utilityProcess；
- ❌ cost 账单级核对（usage 真，已按官方价目表估算 ¥0.0124）、审批角色/顺序。

验证：`python -m unittest test_coordinator test_estimate test_plan_milp test_verifier test_v1_core test_v1_worktree test_v1_lifecycle test_v1_git_merge test_adapters test_or_router test_semantic -v` → 91 tests OK。
