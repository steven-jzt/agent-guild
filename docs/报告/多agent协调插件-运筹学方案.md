# 多 Agent 协调插件 × 运筹学：可行性方案

> 回答两个问题：
> 1. 能不能做一个“协调多 agent”的插件？
> 2. 能不能用管理科学/运筹学做任务拆分，并在价格、上下文、过度设计、质量等多指标下求最优？
>
> 结论：**都能，但“插件”应该是“协调服务 + 薄适配层”，“最优”应该是“约束下的 Pareto 最优 + 滚动重规划”，而不是一个静态全局最优解。**

---

## 0. 一句话结论

- **插件可行**：核心是一个带状态、带锁、带调度器的**协调服务**（MCP server / A2A agent / sidecar），UI 插件只是它的前端。纯编辑器插件做不了跨进程租约和调度。
- **运筹学可用**：任务拆分 → 图划分/WBS/DSM；分配 → 广义分配/最小费用流；排期 → RCPSP/DAG 调度；多指标 → Pareto/ε-约束/MCDA；不确定性 → 随机规划/鲁棒优化/bandit。
- **“最优”要重新定义**：LLM 的工时、质量、token 都是估计值，真正能做的是**在约束下求可解释的 Pareto 前沿，执行后用观测数据滚动重规划**。

---

## 1. 插件形态：为什么不是“一个 UI 插件”

协调需要四样东西，都不是 UI 插件擅长的：

1. **跨进程状态**：任务、租约、agent 心跳、历史成本；
2. **原子操作**：claim/lease/fencing token，防止两个 agent 同时写；
3. **全局视图**：依赖图、资源池、预算、优先级；
4. **后台执行**：超时回收、重试、重规划。

所以建议分层：

```text
┌─────────────────────────────────────────────┐
│ UI 插件（VS Code / DSH / Agent Guild 桌面） │  ← 看板、Pareto 曲线、审批
└──────────────────┬──────────────────────────┘
                   │ MCP tools / HTTP / A2A
┌──────────────────▼──────────────────────────┐
│ 协调服务（core）                             │
│  Task Graph │ Registry │ Estimator │ Solver │
│  Lease Mgr  │ Monitor  │ Replanner │ Trace  │
└──────────────────┬──────────────────────────┘
                   │ CLI / SDK / ACP / A2A
┌──────────────────▼──────────────────────────┐
│ Codex / DSH / Claude Code / 自研 Runtime     │
└─────────────────────────────────────────────┘
```

**对 DSH 的具体建议**：client plugin 做 UI（任务卡、Pareto 选择、审批按钮），真正的协调器做成 **MCP server + 本地 SQLite**；需要跨机时再加 A2A 接口。这样插件可替换，协调逻辑不绑定某个宿主。

**对 Agent Guild 的具体建议**：协调器就是文档里的“任务管理器 + 资源协调器 + 验收中心”的合体，先做成独立服务，桌面端只消费它的 API。

---

## 2. 运筹学 / 管理科学能用在哪些地方

| 问题 | 运筹学/管理学工具 | 在插件里的位置 |
| --- | --- | --- |
| 任务怎么拆 | WBS、DSM（设计结构矩阵）、图划分/社区检测、模块化 | Task Graph Builder |
| 谁来做 | 广义分配问题（GAP）、匈牙利算法、最小费用流、拍卖 | Allocator |
| 什么时候做 | CPM/PERT、RCPSP（资源受限项目调度）、DAG 列表调度 | Scheduler |
| 多目标权衡 | Pareto、ε-约束、加权和、AHP/SWING 定权、目标规划 | Solver / UI |
| 不确定性 | 随机规划、鲁棒优化、CVaR、多臂 bandit | Estimator / Replanner |
| 上下文/预算 | 背包问题、预算分配、信息价值 | Context Planner |
| 吞吐/瓶颈 | 排队论、Little's Law、约束理论（TOC）、看板 WIP | Monitor / 控制台 |
| 质量 | 统计过程控制、验收抽样、质量成本 | Acceptance |
| 风险 | 投资组合/风险调整收益、情景分析 | Portfolio（多项目） |

几个容易忽略但很值钱的点：

- **TOC（约束理论）**：系统的产出由瓶颈决定。在 Agent Guild 里，瓶颈通常不是 agent 写代码，而是**人工验收**和**合并/集成测试**。优化 agent 速度不如扩大验收吞吐。
- **Little's Law**：`WIP = 吞吐 × 周期`。并行 agent 越多，WIP 越大，评审队列越长；需要 WIP 上限，而不是无限并行。
- **关键链/缓冲**：给依赖链末端留时间缓冲，而不是给每个任务加安全时间。
- **质量成本**：预防（契约/测试）> 检查（review）> 失败（返工）。把预算花在验收前。

---

## 3. 数学形式化（可直接落成 CP-SAT / MILP）

### 3.1 集合与参数

```text
任务 i ∈ T，依赖边 (i,k) ∈ E，规模 s_i，类型 kind_i
agent j ∈ A，模型 m ∈ M_j（同一 agent 可切模型/上下文策略）
决策变量 x[i,j,m] ∈ {0,1}：任务 i 是否由 agent j 用模式 m 执行
开始时间 st_i，完成时间 ft_i
```

参数（都来自历史 trace 的估计）：

```text
cost[i,j,m]      预期 token 成本（输入/输出/缓存分别计价）
dur[i,j,m]       预期工时（含上下文加载、冷启动）
ctx[i,j,m]       需要的上下文 token
qual[i,j,m]      预期质量（0~1，或通过概率）
cplx[i,j,m]       过度设计代理指标（diff 规模、新增依赖、抽象数量）
```

### 3.2 目标与约束

单目标（成本最小，带质量/工期约束）：

```text
min  Σ cost[i,j,m] · x[i,j,m]
s.t. Σ_{j,m} x[i,j,m] = 1                    每个任务恰好一个执行者
     st_i ≥ ft_k                             ∀ (k,i) ∈ E（ precedence）
     ft_i = st_i + Σ dur[i,j,m] · x[i,j,m]
     Σ_{i 在 j 上并行} ... ≤ 1                agent 同一时刻只做一个任务
     Σ ctx[i,j,m] · x[i,j,m] ≤ context_j      上下文窗口
     P(quality ≥ q0) ≥ 1-α                    质量机会约束
     makespan ≤ D                             工期
```

多目标（更推荐）：

```text
min  ( Σcost, makespan, Σcplx, -Σquality )
```

用 **ε-约束** 求 Pareto 前沿：固定质量下限 q0、工期上限 D、复杂度上限 C，最小化成本；扫 q0/D/C 得到前沿。比“拍权重”更可解释。

过度设计可以进目标，也可以进约束：

```text
cplx[i,j,m] = α·diff_size + β·new_deps + γ·new_abstractions − δ·reuse
Σ cplx ≤ C_budget        # 变更预算，防止“顺手重构”
```

### 3.3 不确定性

LLM 的工时和质量是重尾分布，不要用点估计硬优化：

- **场景法**：给每个 (task, agent) 采 3~5 个历史分位数（P50/P90），求期望或 CVaR；
- **鲁棒**：最坏场景下仍满足质量/工期；
- **bandit**：新模型/新 agent 先小流量探索，再逐步利用；
- **滚动重规划**：每完成一个任务，用真实 token/质量更新后验，重解一次（receding horizon），而不是一次算到底。

---

## 4. 四个指标的度量方式

| 指标 | 怎么算 | 坑 |
| --- | --- | --- |
| **模型价格** | 输入/输出/缓存 token × 单价 + 重试 + 评审人力 | 价格会变；缓存命中率难估；批处理折扣 |
| **上下文** | 峰值 context token、handoff 摘要长度、缓存复用率 | 窗口是硬约束，但“塞满”不等于“用好” |
| **输出质量** | 验收通过率、返工率、缺陷逃逸、人工评审发现数 | 自报质量不可信；要独立 verifier |
| **过度设计** | diff 行数/文件数 vs 估算必要量、新增依赖、抽象/配置项数量、评审 rubric | 容易被优化器 game（少写代码≠好）；必须配质量下限 |

补充：

- **过度设计**更像“约束/惩罚项”，不适合当唯一目标；建议 `变更预算 + 人工 rubric` 双保险。
- **上下文**可以建模成背包：给定 token 预算，选哪些文件/历史/文档进 context，最大化信息价值（可用检索分数/消融估计）。
- **质量**最好用“通过概率 + 置信区间”，而不是一个点；验收用独立 verifier + 人工抽查。

---

## 5. 求解策略：别一上来就上 MILP

| 规模 | 方法 | 说明 |
| --- | --- | --- |
| ≤ 20 任务、≤ 5 agent | 枚举 / 动态规划 | 玩具验证够用 |
| ≤ 100 任务 | **CP-SAT**（OR-Tools）或 MILP | RCPSP 是 NP-hard，CP-SAT 工程上好用 |
| 100~1000 | 列表调度 + 局部搜索 / 遗传算法 / LNS | 先构造可行解，再邻域改进 |
| 超大/多项目 | 分层规划 + 滚动窗口 | 项目→里程碑→任务；只优化近 2 周 |
| 在线 | bandit / 规则 + 重规划 | 不确定性高时，简单策略 + 快速反馈更稳 |

**人力定权**：AHP / SWING 问用户几个偏好问题，得到权重；或者直接给 Pareto 前沿让用户点选（推荐）。

---

## 6. 分阶段落地（建议）

### P0（1~2 周）：先别优化

- 手动派遣 + 明确写集 + 原子 claim + 验收 gate；
- 记录每次运行的 token、工时、质量、diff 规模；
- 目标：**拿到可用的参数估计**。没有数据，优化就是拍脑袋。

### P1（2~4 周）：约束下的贪心

- 估计器：按 (task kind, agent, model) 聚合历史均值/分位数；
- 调度器：拓扑序 + 最便宜可行 agent + 工期/质量硬约束；
- UI：显示“为什么选它”（约束、估计、备选）。

### P2（1~2 月）：CP-SAT 精确求解

- 任务 ≤ 50、agent ≤ 10 时用 CP-SAT；
- 输出 Pareto 前沿 + 推荐点；
- A/B：优化器 vs 贪心，指标是成本、质量、周期、返工率。

### P3（长期）：在线学习 + 多项目组合

- bandit 探索新模型；滚动重规划；
- 多项目共享验收/测试资源，做组合调度；
- 用 CVaR 控制尾部风险。

---

## 7. 玩具算例：优化器到底比贪心强多少

代码：[`or-planner/toy_optimizer.py`](../../experiments/or-planner/toy_optimizer.py)

设置：6 个有依赖的任务、3 个 agent（便宜快但上下文小 / 均衡 / 贵慢但质量高），枚举 3^6 = 729 种分配，216 种可行，得到 41 个 Pareto 点。

在业务约束 **质量 ≥ 0.80、15.5 小时内完成** 下：

| 策略 | 成本 | 工期 | 质量 | 是否可行 |
| --- | ---: | ---: | ---: | --- |
| 最便宜 | $0.598 | 13.00h | 0.72 | ❌ 质量不够 |
| 最高质量 | $3.160 | 21.07h | 0.95 | ❌ 超时 |
| 最快 | $0.598 | 13.00h | 0.72 | ❌ 质量不够 |
| 逐任务贪心 | $0.790 | 15.80h | 0.87 | ❌ 超时 0.3h |
| **约束最优（优化器）** | **$0.710** | **15.05h** | **0.807** | ✅ |

结论：**四个常用启发式全部违反约束；优化器找到一个可行解，成本比逐任务贪心低 10%（$0.710 vs $0.790），而贪心本身超出工期 0.3h。** 这不是说优化器永远更好——参数是合成的，真实收益取决于估计质量；但它说明“约束下的可行解”本身就有价值，贪心很容易顾此失彼。

另一个观察：Pareto 前沿有 41 个点，成本从 $0.598 到 $3.16、质量从 0.72 到 0.95。**多目标问题没有唯一最优**，产品上应该让用户在前沿上选，而不是偷偷替用户定权重。

再补一个约束耦合的例子：把复杂度上限从可行解的 7.60 压到 7.50，问题**直接无解**。这说明“过度设计阈值”不能单独拍脑袋，必须和工期/质量一起标定；这也是为什么建议把它做成约束/惩罚项，而不是唯一目标。

---

## 8. 风险与反模式

1. **参数垃圾进、垃圾出**：没有历史数据时，优化器的精度不如“按文件所有权手动分”。
2. **Goodhart**：优化“过度设计”指标会让 agent 少写代码、少写测试；必须配质量下限和人工 rubric。
3. **优化过度**：3 个任务、1 个 agent 的项目不需要求解器；设一个“值得优化”的阈值（任务数/金额/工期）。
4. **集中式单点**：协调器挂了，所有 agent 停摆；要有本地降级（直接手动派遣）。
5. **计划僵化**：LLM 执行偏差大，必须滚动重规划；不要一次算 3 个月。
6. **基准污染**：用自己项目的回归集，不要只刷 SWE-bench。
7. **忽略瓶颈**：只优化 agent 速度，不扩验收吞吐，整体周期不会降（TOC/Little's Law）。

---

## 9. 最小可行插件（MVP）长什么样

**核心服务**（Python + SQLite + FastAPI/MCP）：

```text
POST /plan        {project, tasks, agents, constraints} -> {assignments, schedule, pareto}
POST /claim       {task_id, agent_id, ttl} -> {lease_id, fencing_token}
POST /release     {lease_id}
POST /events      {task_id, tokens, cost, duration, outcome, diff_stats}
GET  /metrics     -> cost/quality/cycle/conflict
```

**MCP tools**：`plan_project`、`claim_task`、`release_task`、`report_progress`、`get_pareto`。

**数据表**：`tasks`、`agents`、`runs`、`leases`、`traces`。

**第一阶段只做三件事**：

1. 任务图 + 写集 + 依赖边；
2. 原子 claim/lease（带 TTL + fencing token）；
3. 每次运行记录成本/质量/diff 规模，用于下一轮估计。

优化器可以后置；**没有这三样，优化器没有输入，也没有执行保障。**

---

## 10. 参考

- Co-Coder（任务切分/图划分）：<https://arxiv.org/abs/2606.00953>
- MAST（失败分类，验收相关）：<https://arxiv.org/abs/2503.13657>
- SASE（ACE/AEE、MRP/CRP）：<https://arxiv.org/abs/2509.06216>
- CooperBench（协作负收益）：<https://arxiv.org/abs/2601.13295>
- Agentless（反过度设计）：<https://arxiv.org/abs/2407.01489>
- RCPSP / 资源受限项目调度：Kolisch & Padman, *An overview of models and methods for the RCPSP*
- Little's Law / 约束理论：Goldratt, *The Goal*；Hopp & Spearman, *Factory Physics*
- 多目标优化：Deb et al., NSGA-II；Miettinen, *Nonlinear Multiobjective Optimization*
- CP-SAT：Google OR-Tools <https://developers.google.com/optimization/cp>
