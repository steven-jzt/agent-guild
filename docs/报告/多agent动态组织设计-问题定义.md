# 多 Agent 动态组织设计：把你说的东西命名

> 你说“分工架构等因素也要考虑，感觉是个多 agent 的动态组织分工规划，但说不上来”。
> 这个感觉是对的，而且它有名字：**多 Agent 组织设计（Multi-Agent Organization Design）**，
> 动态版本叫 **动态组织重构（Dynamic Reorganization）** 或 **组织自适应（Adaptive Organization）**。
> 任务分配只是它下面的一层；你真正在说的是“设计一个会随任务变化而重组的公司”。

---

## 0. 一句话定义

**在任务互依性、agent 能力/成本、治理约束下，选择并持续调整“谁组成团队、如何分工、谁向谁汇报/通信、用什么协调与问责机制”，使质量、成本、周期、风险达到可接受的 Pareto 最优。**

更短一点：

> 不是给每个 agent 写更好的 prompt，而是设计一个**会重组的组织**。

---

## 1. 三个名字，对应三种语境

| 语境 | 说法 |
| --- | --- |
| 学术 | Organizational Science of Multi-Agent LLM Systems；Multi-Agent Organization Design |
| 工程 | Org-as-Code；Adaptive Orchestration；Dynamic Team/Topology Design |
| 管理 | 组织设计（Organization Design）；组织重构（Reorganization） |

你之前提的“插件 + 运筹学”是**给定组织下的任务分配/调度**；现在这一层是**选择组织本身**。两层合起来才是完整问题。

---

## 2. 六个维度：把“说不上来”拆开

| 维度 | 要回答的问题 | 典型选项 | 对应 Agent Guild |
| --- | --- | --- | --- |
| **Who（组成）** | 团队里有哪些角色/agent？ | 固定名册、按任务临时组队、外部 API agent | Agent 名册、能力注册表 |
| **How（协调）** | 他们怎么对齐？ | 计划、评审 gate、拍卖、升级、互调 | 任务板、评审、告警 |
| **Which（协议）** | 多人意见怎么融合？ | supervisor-worker、debate、vote、pipeline、blackboard | 执行拓扑 |
| **Structure（结构）** | 谁向谁汇报/通信？ | 扁平、层级、矩阵、星型、DAG、市场 | 控制台组织图 |
| **Governance（治理）** | 谁有权改什么？ | 权限/特权、租约、分权制衡、审计 | 资源锁、验收、日志 |
| **Dynamics（动态）** | 什么时候重组？ | 任务变化、冲突率、成本超支、质量漂移、agent 故障 | 重规划触发器 |

**关键洞察**：大多数多 agent 系统只设计了 Who + Which（角色 prompt + 对话拓扑），把 How / Structure / Governance / Dynamics 留给了临时决定。而这四项才是“组织”之所以是组织。

---

## 3. 为什么它是双层优化（bilevel optimization）

上一轮的运筹学方案，是**给定组织**做分配/排期：

```text
下层（tactical）：给定组织 O，求任务分配 x、开工时间 s
min  成本 + 工期 + 复杂度
s.t. 依赖、容量、上下文、质量、截止期
```

现在多了一层：

```text
上层（strategic）：选择组织 O = (角色, 结构, 协调机制, 问责, 权限)
min  E[ 下层最优成本 + 协调开销 + 重组成本 + 风险 ]
s.t. 治理约束、预算、人力、合规
```

这是**双层规划**：上层选组织，下层在组织里做调度。它一般 NP-hard，而且下层是 MILP/CP-SAT，不能直接把两层塞进一个求解器。

**可操作的解法**：

1. **离散化组织空间**：先只做 3~5 个 archetype（扁平、层级、流水线、市场/拍卖），不要连续调参；
2. **仿真评估**：每个 archetype 在下层跑历史任务 trace，得到质量/成本/周期；
3. **上下文 bandit**：把“选哪个组织/协议”当成 arms，按任务特征选，在线更新；
4. **重组放在批次边界**：一个 sprint/一批任务结束后才允许换组织，避免每 token 都重组；
5. **加重组成本/滞后**：`reorg_cost` + 稳定性惩罚，防止抖动。

这和第 6 轮说的“Adaptive Org Routing”是同一类思路：不是硬编码组织，而是**学一个选择组织的策略**。

---

## 4. 管理科学的理论地图

| 理论 | 核心 | 对多 agent 组织的直接含义 |
| --- | --- | --- |
| **Thompson（1967）互依性** | pooled / sequential / reciprocal | 池化任务→标准化+共享队列；顺序任务→计划+交接；互惠任务→高频互调/共同调试 |
| **Mintzberg（1979）协调机制** | 互调、直接监督、工作/产出/技能标准化 | 对应 peer chat、supervisor、契约/测试/角色规范 |
| **Galbraith（1974）信息处理** | 组织是信息处理器；不确定性越高，越需要横向协调 | 任务越不确定，越该加评审/互调，而不是加层级 |
| **Ouchi（1980）市场/层级/宗族** | 三种控制方式 | 明确任务用市场（拍卖/计件），模糊任务用层级（主管），长期团队用宗族（共享规范） |
| **权变理论（Lawrence & Lorsch）** | 没有唯一最优组织，只有“拟合” | 不同项目类型（Web/FPGA/研究）应配不同组织 archetype |
| **交易成本经济学（Williamson）** | 协调成本 vs 生产成本 | 什么时候新开一个 agent，什么时候复用/外包，取决于协调成本 |
| **团队组建/联盟形成** | team formation、hedonic games、coalition formation | “临时组队”可以形式化为匹配/联盟博弈，而不是拍脑袋点名 |
| **有限理性（March & Simon）** | 满意解，不是全局最优 | 组织设计目标是“够好 + 可解释 + 可回滚”，不是数学最优 |

这张表的价值：它把“分工架构”从直觉变成了**可配置、可比较、可验证**的设计变量。

---

## 5. 形式化：把组织当成一等对象

```text
OrgConfig O = (R, S, C, A, P)

R = 角色/agent 集合：{id, skills, model, cost, context, reliability}
S = 结构：汇报边/通信边的图，层级深度，管理幅度
C = 协调机制：plan / review gate / auction / escalation / mutual adjustment
A = 分配策略：任务类型 → 角色；固定 / 轮询 / 拍卖 / 学习
P = 治理：权限、租约、审批、审计、分权（Operation / Review / Supervision）
```

状态与动作：

```text
state s_t = (任务 DAG, 互依类型, 负载, 质量历史, 冲突率, 预算, 模型绑定)
action a_t = 保持 | 加角色 | 删角色 | 拆/并角色 | 改通信边 | 换协议 | 升级人工
reward = quality - λc·cost - λt·cycle - λr·risk - λo·reorg_cost - λs·instability
```

三个时间尺度（不要混在一起优化）：

| 尺度 | 决策 | 频率 |
| --- | --- | --- |
| 战略 | 组织 archetype、角色体系、治理规则 | 项目/季度 |
| 战术 | 团队组建、任务分配、排期 | 批次/sprint |
| 操作 | 派发、锁、重试、升级 | 分钟级 |

**组织设计的关键不是“最优”，而是“在正确的尺度上做正确的决定”。** 每分钟重组的组织没有记忆，也无法问责。

---

## 6. 2026 年的直接证据

- **[OrgAgent: Organize Your Multi-Agent System like a Company](https://arxiv.org/abs/2604.01020)**（2026）
  公司式三层：governance（规划/资源分配）、execution（执行/评审）、compliance（最终答案控制）。结论：层级式组织在多数设置下优于扁平；GPT-OSS-120B 在 SQuAD 2.0 上比扁平提升 **102.73%**、token 降低 **74.52%**。层级在“稳定技能分配、受控信息流、分层验证”时收益最大。
  → 注意：这是推理/QA 基准，不是编码；但“结构影响效果与成本”这一点可直接迁移。

- **[Toward an Organizational Science of Multi-Agent LLM Systems](https://arxiv.org/abs/2607.25446)**（2026）
  把 **Who（组织）/ How（协调）/ Which（协作算法）** 解耦成三层可互换配置；把 Belbin 角色、Mintzberg 协调、RACI 问责变成可执行配置；用 contextual bandit 做 **Adaptive Org Routing**，按任务在质量-成本间选协议，优于任何固定协议。关键发现：**问责位置是否有效，取决于协议是否把交付物路由到问责 agent；而最优问责位置会随模型家族翻转——组织不能硬编码，必须按模型绑定重新验证/学习。**

- **[Fluid Structure, Rigid Record](https://arxiv.org/abs/2608.08516)**（2026）
  把组织分成持久层与动态层：持久层是四类记录 + 常驻专家 agent 池；协调层用 **Permission（能接触什么）/ Privilege（能改什么组织状态）** 编译出任务专属的“操作世界”；运行时用 Workflow Protocol 动态组 **Task Group**；人类通过 Control Plane + 非决策的 Translation Agent 介入。角色分成 **Operation / Review / Supervision** 三组：执行者拿窄租约，评审者按需获得更高权限，监督者只看不改。结论：**执行面可以流动，记录/权限/分权必须刚性。**

- **[ARG-Designer: Assemble Your Crew](https://arxiv.org/abs/2507.18224)**（AAAI 2026 oral）
  把 MAS 设计重构为“条件自回归图生成”：给一个任务 query，模型从零决定 **agent 数量、角色、通信边**，联合设计组成与拓扑；六个基准上 SOTA 且更省 token。
  → 这是“组织结构生成器”的技术路线；但注意它是为单次任务生成拓扑，不等于长期组织的治理。

- **反面对照**：[CooperBench](https://arxiv.org/abs/2601.13295)（协作成功率平均低 30%）、[MAST](https://arxiv.org/abs/2503.13657)（14 种失败模式，含 inter-agent misalignment 与 task verification）。
  → 组织设计不是锦上添花：没有协调与验证结构，多 agent 天然是负收益。

---

## 7. 对 Agent Guild 的设计含义

1. **把 OrgConfig 做成一等对象**，和任务、agent 一样进数据库、可版本化、可回滚。
2. **先做 3 个 archetype 就够**：扁平 peer、supervisor-worker、pipeline；不要一上来做“任意图生成”。
3. **持久治理 vs 流动执行**：记录、权限、分权（Operation/Review/Supervision）刚性；任务组、通信边、视图可以按任务重组（Fluid Structure 的核心）。
4. **重组放在批次边界**，带 `reorg_cost` 和稳定性惩罚；否则 agent 一直在“适应组织”，没有产出。
5. **按互依性选协调机制**（Thompson）：顺序任务用计划和交接包；互惠任务用高频互调/共同调试；池化任务用队列+标准化。
6. **不要硬编码组织**：IMACS 已经证明最优问责位置会随模型家族翻转；至少要把“模型绑定”作为组织配置的一部分，定期重验。
7. **人类控制面**：审批、升级、翻译（把人的意图翻译成组织动作），而不是让人直接改每个 agent 的 prompt。
8. **组织指标**：除了质量/成本/周期，还要测协调开销（消息/token per task）、冲突率、负载均衡、重组频率、人工升级率。

---

## 8. 一个可验证的实验计划

**假设**：没有单一组织 archetype 在所有任务上占优；按任务特征自适应选组织，能接近 oracle，并优于最佳固定组织。

**设置**：

- 3 个 archetype：flat peer / supervisor-worker / pipeline；
- 3 类任务：pooled（可并行、低互依）、sequential（强依赖）、reciprocal（需要来回调试）；
- 3 个模型绑定（便宜/均衡/贵），观察最优问责位置是否翻转；
- 指标：质量、成本、周期、冲突率、协调 token、人工升级率。

**步骤**：

1. 固定 archetype，各跑一遍，画“组织 × 任务类型”的收益矩阵；
2. 用 contextual bandit 按任务特征选 archetype，对比 best-fixed 和 oracle；
3. 消融：去掉重组成本 → 是否抖动；去掉问责路由 → 是否退化；换模型绑定 → 最优组织是否翻转。

**预期**：会出现“没有免费午餐”的矩阵；自适应的价值来自**匹配**，不是某个组织绝对更好。

### 8.1 首轮仿真结果（2026-11-07）

代码：[`org-sim/org_sim.py`](../../experiments/org-sim/org_sim.py)。3 种组织 × 3 类互依性 × 9 个任务 × 60 个种子。

**（1）组织选择确实随目标权重变化。**

| 成本/时间权重 | pooled | sequential | reciprocal |
| --- | --- | --- | --- |
| 0.3 / 0.3 | flat | flat | flat |
| 0.1 / 0.1 | flat | hierarchy | hierarchy |
| 0.02 / 0.02 | pipeline | hierarchy | hierarchy |
| 0 / 0 | pipeline | hierarchy | hierarchy |

**（2）但动态重组的收益上限接近 0。** 质量优先（0.1/0.1）稳态下：oracle = 0.804，best fixed = hierarchy 0.804，adaptive（bandit）= 0.800，**headroom = +0.000**。层级是一个很强的折中组织，按任务换组织没有提升空间，反而付探索成本。

**（3）任务组合突变时，固定组织退化，但自适应也没赢。** 阶段 1 pooled 为主、阶段 2 sequential 为主；固定 hierarchy 在阶段 2 为 0.781，自适应为 0.773（delta −0.008）。

**结论**：这轮仿真没有证明“动态组织一定更好”；它证明了**组织选择是权变的**，而动态重组的价值要先看 `headroom = oracle − best fixed`。headroom 小，稳健的折中组织更划算；headroom 大，才值得做自适应。仿真是风格化模型，参数人为设定，真实结论要用运行 trace 校准。

---

## 9. 术语表（方便你下次把它说清楚）

| 中文 | English | 一句话 |
| --- | --- | --- |
| 组织设计 | Organization Design | 选择角色、结构、协调、问责 |
| 分工 | Division of Labor | 谁负责哪类任务 |
| 角色 | Role | 一组职责+技能+权限 |
| 问责 | Accountability（RACI） | 谁对结果负责 |
| 决策权 | Decision Rights | 谁能拍板 |
| 管理幅度 | Span of Control | 一个主管带多少 |
| 协调机制 | Coordination Mechanism | 计划/评审/互调/市场 |
| 互依性 | Interdependence | pooled / sequential / reciprocal |
| 团队组建 | Team Formation | 为任务临时组队 |
| 联盟形成 | Coalition Formation | 多个自利 agent 组队 |
| 重构 | Reorganization | 改结构/角色/协议 |
| 重组成本 | Reconfiguration Cost | 换组织付出的代价 |
| 组织惯性 | Organizational Inertia | 组织不愿意变 |
| 权变 | Contingency | 没有唯一最优，只有拟合 |
| 治理 | Governance | 权限、审计、分权 |
| 组织科学 | Organizational Science | 把上述当研究对象 |

---

## 10. 参考

**直接相关**
- OrgAgent：<https://arxiv.org/abs/2604.01020>
- Toward an Organizational Science of MAS：<https://arxiv.org/abs/2607.25446>
- Fluid Structure, Rigid Record：<https://arxiv.org/abs/2608.08516>
- ARG-Designer：<https://arxiv.org/abs/2507.18224>
- CooperBench：<https://arxiv.org/abs/2601.13295>
- MAST：<https://arxiv.org/abs/2503.13657>
- SASE：<https://arxiv.org/abs/2509.06216>

**经典**
- Thompson, *Organizations in Action*（1967）——互依性
- Mintzberg, *The Structuring of Organizations*（1979）——协调机制
- Galbraith, *Organization Design*（1977）——信息处理观
- Ouchi, “Markets, Bureaucracies, and Clans”（1980）
- Lawrence & Lorsch, *Organization and Environment*（1967）——权变
- Williamson, *The Economic Institutions of Capitalism*（1985）——交易成本
- Lappas et al., “Finding a Team of Experts in Social Networks”（KDD 2009）——团队组建
- Shehory & Kraus, “Methods for Task Allocation via Agent Coalition Formation”（1998）
