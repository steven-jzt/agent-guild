# 多 Agent 协作总报告：从同工作空间到文档内核

> 五组实验、四份设计，加一个可运行的 P0/P1/P2 协调器。
> 截至 2026-11-08。这是目前的总报告；单篇实验/设计作为附录链接。
> 最新状态汇总见 [当前状态-2026-11-08.md](../现状/当前状态-2026-11-08.md)，完整文件索引见 [README.md](../../README.md)。

---

## 摘要

如果只记三句话：

1. **按文件并行的多 agent 开发可行**：契约 + 所有权 + 验收 + git 审计，3 个 agent 可以零冲突交付。
2. **通信不能替代原子性，锁不能替代语义合并**：广播意图约等于无效；原子租约才能让丢失归零；锁能防同时写，但防不了“两个人都想改同一行”。
3. **先测 headroom，再谈动态组织；先记 trace，再谈优化**：组织选择是权变的，但动态重组的收益上限可能接近 0；运筹学优化没有参数就是拍脑袋。

五组实验：

| # | 实验 | 核心结论 |
| --- | --- | --- |
| 1 | 3 agent 同工作空间开发 | 可行；6 分钟、91 个测试、零手工冲突 |
| 2 | 同一文件并发写 | 裸写丢 62%；锁 180/180；工具快照校验只能把冲突变报错 |
| 3 | A2A 两跳调用 | 通信层可行；不解决文件一致性 |
| 4 | 通信 vs 原子性 | 纯广播≈无效；退避 2.4%；租约 0% |
| 5 | 同一 Markdown 文档编辑 | 锁保“不丢追加”，但静默覆盖“改同一行”；MVCC 能检测冲突 |

四份设计：

- **A. 协调插件 + 运筹学**：给定组织下的分配/排期，Pareto 优化；
- **B. 动态组织设计**：双层优化，组织 archetype 的选择与重组；
- **C. Document Kernel**：把 OS 进程/内存/调度/保护模型迁移到文档编辑；
- **D. 学习与门控**：从 trace 学估计器/策略，置信度不足时回退规则/人工。

**实现状态（2026-11-08）**：P0 协调器已可用（SQLite + MCP + CLI，91 个测试）；runs/估计器、MILP 2D/3D Pareto、独立验证、N-of-M 审批（TTL）、预算止损、计划回写/预留都已落地；V1 Core 六块（Run、文件预约、Candidate/Evidence/Merge、Worktree、lifecycle、双重门）已实现；**DSH 5 个任务、Codex（DeepSeek）1 个任务、3-agent 并发全部合入；Codex App Server adapter 已跑通 V1 端到端；`merge_candidate_git()` 真实 cherry-pick + 回滚**；Electron 17/22/28/45 已装但窗口崩溃；WebView2 替代壳已跑通 Start Run/Approve/Merge；3 个真实 DSH subagent 跑过三次 live demo。缺口是真实小仓库、Electron 可用会话、cost 对账、审批角色/顺序、跨机与权限。详见 [当前状态-2026-11-08.md](../现状/当前状态-2026-11-08.md)。

---

## 1. 起点：朋友那份 Agent Guild 草案

那份文档提出：用户当“公会管理员”，手动把任务派遣给 Codex、DSH、Claude Code 等 agent；每个 agent 在独立 worktree 里干活；平台统一管资源锁、日志、交接、自动测试和人工验收。

它把问题定义对了，而且几个判断很准：

- Worktree 只是目录隔离，不是文件锁；
- 验收必须分“自动测试”和“人工验收”；
- 日志要“摘要 + 证据引用”，不能复制长 diff；
- 不同厂商的 agent 要统一到同一套任务/事件模型。

但它没有回答最硬的部分：**锁/租约的具体协议、跨 worktree 的合并门禁、以及“派遣”如何真正送达一个已经关闭的客户端。** 后面的实验，基本就是围绕这三件事展开的。

---

## 2. 实验一：三个 agent 在同一工作空间做一个项目

### 2.1 设置

先写冻结的 `CONTRACT.md`（函数签名、数据结构、错误语义），再写 11 个黑盒验收用例，然后 3 个 agent 并行实现：

- `agent-storage`：`storage.py` + `test_storage.py`
- `agent-report`：`report.py` + `test_report.py`
- `agent-cli`：`cli.py` + `test_cli.py`
- 编排者：契约、验收、提交，不写实现

规则：契约先行、验收先行、文件所有权、agent 禁止 git 写操作。

### 2.2 结果

6 分钟，485 行实现、856 行测试、195 行验收测试，合计 1360 行新增。四套测试全绿：

```text
test_storage     17 tests
test_report      33 tests
test_cli         22 tests
test_acceptance  11 tests
```

`git diff --numstat` 只出现预期的 8 个文件，没有 agent 改到别人的文件；集成时没有手工冲突，也没有返工。

### 2.3 两个真实的坑

**环境缺陷会被重复踩。** 本机 DSH 沙箱下，`tempfile.TemporaryDirectory` 创建的 0o700 目录不可写；两个 agent 各自踩了一遍，各自写了不同的绕行方案。共享工作空间也共享环境缺陷。

**环境噪声会伪装成集成失败。** 第一次跑验收出现 7 个 failure + 1 个 error，全部是临时目录权限，不是实现问题。编排者要先排除环境，再判断代码。

### 2.4 证明了什么

在“任务可切分、接口可冻结、验收可自动”的前提下，**按文件并行的多 agent 开发是工程上可行的**，不需要神奇框架：契约 + 所有权 + 验收 + git 审计就够了。

没证明的是：任务耦合度高、需要频繁讨论、或者多人同时改同一文件时，这套还成不成立——后面三组实验就是在补这些。

---

## 3. 实验二：同一文件并发写

### 3.1 进程级：裸读改写会静默丢更新

3 个进程对同一个 JSON 计数器各做 60 次「读 → 等 1ms → 整文件写回」，期望 180。结果：

| 轮次 | 实际 | 丢失 | 撕裂读 |
| --- | ---: | ---: | --- |
| 1 | 67 | 113 | 7/8/8 |
| 2 | 67 | 113 | 4/4/5 |
| 3 | 68 | 112 | 6/10/5 |

所有进程退出码都是 0。没有报错、没有异常，62% 的更新就是没了。加上 mkdir 互斥锁后，三轮都是 180/180。

### 3.2 Agent 级：工具会拒绝过期快照

3 个 agent 同时读 `team.json`、等 6 秒、整文件覆盖写回自己的名字：

- 甲成功；
- 乙、丙被拒绝：`cannot write ...: file changed since it was read — re-read the file, then retry`。

最终只有甲的名字，**1/3 存活**。第二轮改成「遇到冲突就重读、合并、重试」：丁 0 次重试、戊 1 次、己 2 次，最终 3/3 全部写入。

### 3.3 证明了什么

- **工具路径受保护，shell 路径裸奔。** 用 `write`/`edit` 有快照校验；用 PowerShell/Python 直接写就退回静默丢更新。
- **保护不等于正确。** 拒绝过期写保证“不覆盖别人”，但要保证“不丢自己”，必须重读 + 合并 + 重试。
- **原子性必须在写入那一刻。** “读版本 → 复查 → 写”不是原子 CAS。

---

## 4. 实验三：A2A 能不能补上 agent 间的通信

上一轮发现：同级 subagent 不能直接发消息，会报 `belongs to another parent session`。A2A 正好补这个缺口。

用官方 `a2a-sdk 1.3.0`（A2A v1.0）起了两个 server：

- **Ledger Agent**（:8765）：记账；
- **Audit Agent**（:8766）：收到审计任务后，**自己作为 A2A 客户端**去调 Ledger Agent 取报表，再核对。

客户端只调了 Audit 一次，但发生了两跳：`client → audit → ledger`。`audit.ok = True`，三项检查全过，**audit → ledger 这一跳没有任何中心编排者参与**。失败路径也验证了：未知 action → Task 状态 `failed`，错误消息通过协议回传。

### 4.1 解决了什么

- 同级 agent 直连；
- 标准发现（Agent Card）；
- Task 生命周期、artifact、流式、取消；
- 跨进程/跨厂商/跨语言。

### 4.2 没解决什么

- 文件锁、租约、工作区一致性；
- 统一调度、预算、权限策略；
- 单机同进程父子委派（起端口 + 序列化，比原生 subagent 重）。

### 4.3 接入 DSH 的三条路径

1. **旁路服务化**：每个 agent 独立进程 + A2A server，DSH 侧当 client。
2. **Sidecar 桥接**：对外 A2A server，内部转成 DSH 会话；DSH 目前没有原生 A2A endpoint，要自己写网关。
3. **只在跨边界时用**：同工作区继续原生 subagent，A2A 只接外部黑盒 agent。

---

## 5. 实验四：通信、共享信息，到底能不能解决“打架”

把“通信”和“原子性”拆成两个变量，6 种协议、每种 5 轮：

| 协议 | 通信 | 收到信息后行动 | 丢失率 |
| --- | --- | --- | ---: |
| `silent` | ❌ | — | 61.1% |
| `paced`（同节奏对照） | ❌ | — | 52.4% |
| `announce` 广播意图 | ✅ | ❌ 不避让 | 52.2% |
| `polite` 广播 + 退避 | ✅ | ✅ | 2.4% |
| `lease` 申请/授予锁 | ✅ | ✅ 持锁才写 | 0% |
| 读版本→复查→写 | ❌ | — | 38.4% |

四个发现：

1. **纯信息共享 ≈ 没有共享。** `announce` 52.2%，`paced` 52.4%，几乎一样。广播“我要写了”本身不产生协调效果；之前 `announce` 比 `silent` 好看，几乎全是“多等了 20ms”的错峰。
2. **信息 + 行动，能大幅改善，但不能归零。** `polite` 把丢失压到 2.4%，但 5 轮里仍有 4 轮丢 2–4 次。信息永远过期：两个 agent 可以同时看到“没人要写”，然后同时写。
3. **通信 + 原子租约，才归零。** `lease` 90/90，而且比退避重试更快（0.38s vs 2.44s）——排队比碰撞+重试便宜。工程上还要配 fencing token。
4. **“先检查再写”不是原子操作。** 读版本 → 复查 → 写仍丢 38.4%；原子性必须在写入那一刻完成。

一个 2×2 就能概括：

| | 无原子性 | 有原子性 |
| --- | --- | --- |
| **无通信** | 61% 丢失 | 本地锁 / 事务：0 丢失 |
| **有通信** | 广播 52%；退避 2.4% | 租约：0 丢失 |

**决定“会不会丢”的是原子性；通信决定的是协调成本和语义质量。**

---

## 6. 实验五：同一份 Markdown 文档的并发编辑

前四组实验都在讲“文件/计数器”。这一组换成真正的文档：3 个 agent 改同一份 Markdown，6 个章节，两种任务。

代码：[`doc-sim/doc_sim.py`](../../experiments/doc-sim/doc_sim.py)。

### 6.1 四种策略

- `none`：无协调，最后写赢；
- `global`：整篇文档一把互斥锁；
- `section`：章节级锁（每节一把）；
- `mvcc`：读快照 + 提交时版本校验 + 追加合并（COW 思路）。

### 6.2 场景 A：12 个“追加一行”的任务

3 个 agent、每个 4 个任务，30 个种子：

| 策略 | 保留 / 12 | 丢失 | 重试 | 耗时 |
| --- | ---: | ---: | ---: | ---: |
| `none` | 8.5 | 3.5 | 0 | 25.9ms |
| `global` | 12.0 | 0 | 0 | 69.6ms |
| `section` | 12.0 | 0 | 0 | 31.8ms |
| `mvcc` | 12.0 | 0 | 3.4 | 25.7ms |

结论很干净：

- 不协调会丢约 29% 的追加；
- 全局锁正确但最慢（串行）；
- 章节锁正确且快（不同章节并行）；
- MVCC 也正确、最快，但要付 3.4 次重试/合并。

### 6.3 场景 B：3 个 agent 抢改同一行 `Status`

| 策略 | 检测到冲突 | 静默覆盖 | 最终 S1 | 最终 S2 |
| --- | ---: | ---: | --- | --- |
| `none` | 0 | 4 | v2 | v5 |
| `global` | 0 | 4 | v2 | v5 |
| `section` | 0 | 4 | v2 | v5 |
| `mvcc` | 4 | 0 | v0 | v3 |

这一组是关键：

- **`global` 和 `section` 保证了“不同时写”，但没有保证“知道别人也想改”。** 三个 agent 依次覆盖，最后写赢，前两个意图静默消失，平台没有任何感知。
- **`mvcc` 检测到了 4 次冲突**，但它选择“后写者放弃”（first-writer-wins）；没有静默覆盖，但有 4 个任务白做。
- 要真正解决，需要第三个东西：**语义裁决/合并**——三路合并、agent 修复、人工验收，或者把“改同一行”变成“先 claim 再改”。

### 6.4 这三层，正好对应 OS 类比

| 层 | OS 对应 | 文档编辑对应 | 本实验的体现 |
| --- | --- | --- | --- |
| 隔离/原子性 | 进程、锁、事务 | 全局锁 / 章节锁 | 追加不丢，但慢/串行 |
| 冲突检测 | 版本号、MVCC、页表 | 提交时版本校验 | `mvcc` 检测到 4 次冲突 |
| 语义合并 | 无（OS 不管语义） | diff3/CRDT/agent 修复/人工 | 本实验没做，所以只能放弃后写者 |

**这就是“OS 进程模型能迁移，但不够”的实证：锁给你原子性，版本给你检测，语义合并仍然要单独建。**

---

## 7. 设计一：协调插件 + 运筹学

你问“能不能做成一个协调多 agent 的插件，用运筹学做任务拆分，在多指标下求最优”。答案是能，但要把两个词拆开：

- **插件**：不是一个 UI 插件，而是**协调服务 + 薄适配层**。协调需要跨进程状态、原子租约、全局视图、后台重规划。
- **最优**：LLM 的工时/质量/token 都是估计值，不存在静态全局最优；实际能做的是**约束下的 Pareto 最优 + 滚动重规划**。

### 7.1 运筹学接在哪

- 任务拆分：WBS、DSM、依赖图划分/社区检测；
- 谁来做：广义分配、匈牙利算法、最小费用流、拍卖；
- 什么时候做：CPM/PERT、RCPSP、DAG 列表调度；
- 多目标：Pareto、ε-约束、加权和、AHP/SWING；
- 不确定性：随机规划、鲁棒优化、CVaR、bandit；
- 上下文/预算：背包问题；
- 吞吐/瓶颈：排队论、Little's Law、TOC；
- 质量：统计过程控制、验收抽样、质量成本。

### 7.2 形式化

```text
min  Σ cost[i,j,m] · x[i,j,m]
s.t. 每个任务一个执行者、依赖、容量、上下文窗口
     P(quality ≥ q0) ≥ 1-α
     makespan ≤ D
     Σ cplx[i,j,m] · x ≤ C_budget     # 过度设计预算
```

多目标时用 ε-约束求 Pareto 前沿，让用户在前沿上选，而不是偷偷定权重。

### 7.3 玩具算例

6 个有依赖的任务、3 个 agent，枚举 729 种分配，216 种可行，41 个 Pareto 点。在“质量 ≥ 0.80、15.5 小时内完成”下：

| 策略 | 成本 | 工期 | 质量 | 可行 |
| --- | ---: | ---: | ---: | --- |
| 最便宜 | $0.598 | 13.00h | 0.72 | ❌ |
| 最高质量 | $3.160 | 21.07h | 0.95 | ❌ |
| 逐任务贪心 | $0.790 | 15.80h | 0.87 | ❌ 超时 0.3h |
| **约束最优** | **$0.710** | **15.05h** | **0.807** | ✅ |

四个常用启发式全部违反约束，优化器找到了可行解。再压复杂度上限：7.55 就无解，7.60 才有解——说明“过度设计阈值”不能单独拍，必须和工期/质量一起标定。

但这个算例也暴露了真正的瓶颈：**参数估计**。没有历史 trace，优化器就是拍脑袋。落地顺序必须是“先记数据，再上求解器”。

### 7.4 从 trace 学习与门控（补充实验）

**学习曲线**：6 任务 × 3 agent、216 种可行分配；随机采样 N 条 trace，模型预测 utility，枚举选最优（20 seeds，噪声 0.02）。regret 越低越好，oracle=0：

| N | 线性 | 随机森林 | GBR |
| ---: | ---: | ---: | ---: |
| 5 | 0.0226 | 0.0651 | 0.0503 |
| 100 | 0.0104 | 0.0086 | 0.0086 |
| 216 | 0.0092 | 0.0096 | 0.0110 |

贪心基线 0.0092。小数据线性最稳；N≈100 树模型才略胜贪心；再多开始饱和/过拟合。结论：**先监督估计 + predict-then-optimize，端到端 RL 最后**。

**门控**：200 棵树，`stability`（与集成 argmax 一致的树比例）低于阈值时回退贪心：

| N | always learned（mean / p90） | gated（mean / p90） |
| ---: | --- | --- |
| 10 | 0.0412 / 0.0899 | 0.0092 / 0.0092 |
| 200 | 0.0114 / 0.0454 | 0.0090 / 0.0121 |

门控主要削尾、提升可部署性，不是提升均值；阈值必须用验证集校准，太松会“半开半关”更差。详见 [学习型优化-从trace到策略.md](学习型优化-从trace到策略.md) 与 [门控机制-实验与设计.md](门控机制-实验与设计.md)。

---

## 8. 设计二：动态组织设计

你说“分工架构等因素也要考虑，感觉是个多 agent 的动态组织分工规划，但说不上来”。这个感觉是对的，它有名字：**多 Agent 组织设计**，动态版本叫**动态组织重构**。

### 8.1 六个维度

| 维度 | 要回答的问题 |
| --- | --- |
| Who 组成 | 团队里有哪些角色/agent？固定还是临时组队？ |
| How 协调 | 计划、评审 gate、拍卖、升级、互调？ |
| Which 协议 | supervisor-worker、debate、vote、pipeline？ |
| Structure 结构 | 扁平、层级、矩阵、星型、DAG、市场？ |
| Governance 治理 | 权限/特权、租约、分权、审计、谁负责？ |
| Dynamics 动态 | 什么触发重组？重组成本多大？多久允许变一次？ |

大多数多 agent 系统只设计了 Who + Which，把 How / Structure / Governance / Dynamics 留给临时决定——而这四项才是“组织”。

### 8.2 双层优化

```text
上层（战略）：选组织 O =（角色、结构、协调、问责、权限）
              ↓
下层（战术）：给定 O，做任务分配/排期（第 7 节的 MILP）
              ↓
操作层：派发、锁、重试、升级
```

上层不能和下层塞进同一个 MILP；可操作的做法是：离散成 3–5 个 archetype → 仿真评估 → 用 bandit 按任务特征选 → 只在批次边界重组，并加重组成本。

### 8.3 理论地图

- **Thompson 互依性**：pooled → 标准化+队列；sequential → 计划+交接；reciprocal → 高频互调。
- **Mintzberg 协调机制**：互调、直接监督、工作/产出/技能标准化。
- **Galbraith 信息处理观**：不确定性越高，越需要横向协调，而不是加层级。
- **Ouchi 市场/层级/宗族**：明确任务用市场，模糊任务用层级，长期团队用共享规范。
- **权变理论**：没有唯一最优组织，只有“拟合”。

### 8.4 仿真：一个反高潮的结果

3 种组织 × 3 类互依性 × 9 个任务 × 60 个种子。

**（1）组织选择确实随目标权重变化。**

| 成本/时间权重 | pooled | sequential | reciprocal |
| --- | --- | --- | --- |
| 0.3 / 0.3 | flat | flat | flat |
| 0.1 / 0.1 | flat | hierarchy | hierarchy |
| 0.02 / 0.02 | pipeline | hierarchy | hierarchy |
| 0 / 0 | pipeline | hierarchy | hierarchy |

成本/时间优先时，扁平 peer 全面胜出；质量优先时，层级和流水线才会赢。**这支持“权变”：没有唯一最优组织。**

**（2）但“动态重组”的收益上限接近 0。**

质量优先（0.1/0.1）稳态：

```text
oracle（每类任务选最优） = 0.804
best fixed（只选一个）    = 0.804（hierarchy）
adaptive（bandit 按任务选）= 0.800
headroom（oracle - best fixed） = +0.000
```

层级是一个很强的“折中组织”：pooled 上只比扁平差一点，sequential/reciprocal 上大幅领先。所以按任务换组织几乎没有提升空间，反而付探索成本。

**（3）任务组合突变时，固定组织退化，但自适应也没赢。**

阶段 1 pooled 为主、阶段 2 sequential 为主；固定 hierarchy 在阶段 2 为 0.781，自适应 0.773（delta −0.008）。

### 8.5 这个负结果怎么读

它不是说“动态组织没用”，而是说：**动态组织的价值上限 = oracle − best fixed，要先测这个 headroom，再决定要不要做。**

Headroom 小，说明存在一个稳健的折中组织，动态重组是过度设计；Headroom 大，才值得付出探索/重组成本。Headroom 什么时候会大？

- 不同任务类型的最优组织差异很大；
- 任务组合频繁、剧烈切换；
- 固定组织失效的代价很高；
- 重组本身很便宜。

我们的仿真里，hierarchy 把三类任务都“吃住了”，所以 headroom 接近 0。真实 LLM 场景里，模型绑定、失败率、上下文窗口、工具权限都可能让 headroom 变大——但这要用真实 trace 去测。

---

## 9. 设计三：Document Kernel（OS 类比）

你问“能不能参考操作系统进程模型，迁移到多 agent 文档编辑”。答案是能，而且 2026 年已经有人做成了系统；但要把“OS 进程”和“数据库事务”合起来看。

### 9.1 能直接搬的

| OS | 多 agent 文档 |
| --- | --- |
| 进程 / PID | 每个 agent 的会话 |
| 地址空间 | 私有文档快照 + 上下文 |
| 页 / 页表 | 章节/段落/AST 节点 + 版本映射 |
| COW / fork | 分支编辑，提交时合并 |
| 互斥锁 / 读写锁 | 章节写租约 / 读租约 |
| 信号 | 取消、暂停、超时 |
| 调度器 / cgroups | 并发上限、优先级、token 配额 |
| 用户态/内核态 | agent 权限 vs 平台权限 |
| 系统调用 | MCP 工具（读/写/加锁/提交） |
| 能力（capability） | 细粒度文档权限 |
| 管道/共享内存 | A2A / MCP / 黑板 |
| 等待图 / 死锁检测 | A 等 B 的评审、B 等 A 的修改 |
| 僵尸/孤儿进程 | 断连 agent 的租约回收与接管 |

### 9.2 不能直接搬的

1. **锁的持有时间差了几个数量级。** OS mutex 持有微秒；agent 一次“读—想—写”是几分钟。要用**租约（TTL + 心跳 + fencing token）**，长任务“读快照 → 离线推理 → 提交时校验”。
2. **冲突是语义的，不是字节的。** 锁只能防“同时写”，不能防“写完合起来是错的”。需要三路合并、CRDT/OT、LLM 语义修复、人工验收。
3. **读集不透明。** OS 的 MMU 精确知道进程读了哪些页；agent 读了什么往往是一大段上下文。要在工具层记录 read set、要求足迹声明、提交时校验版本。

### 9.3 已有的系统

- **[AIOS](https://arxiv.org/abs/2403.16971)**（COLM 2025）：把调度、上下文、内存、存储、访问控制收进一个 kernel，最高 2.1x 加速。
- **[AOS](https://arxiv.org/abs/2606.01508)**（2026）：系统化定义 Agent Operating System；职责拆成调度器、上下文/内存、工具/能力注册、策略/信任、可观测/审计。
- **[CoAgent](https://arxiv.org/abs/2606.15376)**（2026）：**和最贴的一篇。** 直接研究“多个 agent 同时改同一个 git tree / 文档”。结论：**2PL 和 OCC 都不适配**——agent 事务长达数分钟、读集宽且不透明、live state 不能 fork/缓冲，写会立刻生效。它提出 MTPO：启动时固定序列化顺序，读时返回按序过滤的值，写先原地投机执行；冲突时通知受影响的 reader，让 agent 自己判断并修补计划；框架用 saga 式逆操作撤销/重排。结果：10 个争用负载上，距串行正确性 5% 以内，1.4x 加速、接近串行的 token 成本；bash 场景在线长出 25 个工具，通过率 45/71 → 63/71。
- **[MemGPT](https://arxiv.org/abs/2310.08560)**：上下文当虚拟内存，分页/换入换出。

### 9.4 建议的 Document Kernel

```text
Agent 进程（PID / 权限 / 配额 / COW 视图 / 页表）
        │  syscalls（MCP tools）
Document Kernel
  Scheduler │ Lease Mgr │ Version Store
  Merge Engine │ Capability/Policy │ Monitor
        │
Document Store（region tree + MVCC + WAL）
```

核心系统调用：

```text
open(doc)
map(region, RO|RW)              # 私有 COW 视图
lock(region, SHARED|EXCLUSIVE, ttl) -> lease + fencing_token
read(region) -> {content, version}
write(region, patch)            # staged
commit(handle)                  # 校验 read-set/lease/version，原子提交
abort(handle)
fork(handle)                    # what-if 分支
signal(pid, CANCEL|PAUSE)
wait(pid)
stat(doc)                       # 版本、持有者、等待者
```

冲突策略按 region 类型分：

- 不同章节 → 乐观 MVCC，各自 COW，提交时版本校验；
- 同一段落 → 单写者租约，或 MVCC + 三路合并 + agent/人工裁决；
- 跨章节一致性 → 版本向量 + 变更通知 + read-set 校验；
- 结构编辑 → 意向锁 + 子树锁；
- 多文件/多仓库 → worktree/branch + 合并队列；
- 长任务 → 不持锁推理，租约只保护“提交窗口”；
- 死锁 → 等待图 + 超时 + 升级人工；优先级反转 → 优先级继承。

### 9.5 和实验五的对应

- 实验五的 `global`/`section` 对应“隔离/原子性”层：追加不丢，但改同一行会静默覆盖。
- `mvcc` 对应“冲突检测”层：检测到 4 次冲突，但选择放弃后写者。
- 还缺“语义合并”层：三路合并、agent 修复、人工验收——这就是 CoAgent 和 Document Kernel 要补的。

---

## 10. 文献校准

| 我们的结论 | 文献对应 |
| --- | --- |
| 纯广播“我要写了”≈ 无效 | CooperBench：通信通道堵塞、承诺不兑现；Informed but Still Colliding：awareness 不等于不冲突 |
| 信息 + 退避把丢失从 61% 降到 2.4%，但不归零 | Passes Alone：一条变更消息恢复 82%，仍有 18% 修不好 |
| 通信 + 原子租约才归零 | MAST 的 inter-agent misalignment / verification；SASE 的 MRP/CRP |
| 按文件/目录切分比按行切分稳 | Co-Coder：按依赖图/社区切分更好 |
| 层级/流水线在质量优先时更优 | OrgAgent：层级在多数设置下优于扁平；但基准是推理/QA |
| 组织不能硬编码 | IMACS：最优问责位置随模型家族翻转 |
| 执行可流动、治理要刚性 | Fluid Structure：execution surface 流动，records/permissions 刚性 |
| 锁不等于冲突检测 | CoAgent：2PL/OCC 不适配长事务、宽读集、live state |
| 复杂度不是默认答案 | Agentless：简单三段式在 SWE-bench Lite 上以 $0.70 超过复杂 agent |

一个重要的反面提醒：**文献里的“多 agent 协作”经常是负收益**。CooperBench 测到协作成功率平均低 30%；MAST 的 14 种失败模式里，inter-agent misalignment 和 task verification 占了两大类。所以“多 agent”不是默认更好，组织设计是必要的补救，不是锦上添花。

---

## 11. 总判断

### 已经比较确定的

1. **按文件并行的多 agent 开发可行**：契约 + 所有权 + 验收 + git 审计，3 个 agent 可以零冲突交付。
2. **同一文件并发写必须靠原子性**：锁/租约/事务/fencing；通信只能改善，不能解决；check-then-act 不是原子操作。
3. **锁不等于冲突检测**：全局锁/章节锁能保证“不丢追加”，但会静默覆盖“改同一行”的意图；MVCC 能检测，但需要合并/裁决。
4. **A2A 是通信层，不是协调层**：能补 agent 间直连、发现、任务生命周期，但不提供文件锁、验收、治理。
5. **运筹学能优化“给定组织下的分配/排期”**：约束下的 Pareto 解有价值；但参数估计是瓶颈。
6. **组织选择是权变的**：成本/时间优先选扁平，质量优先选层级/流水线；没有唯一最优。
7. **动态组织的收益不是自动的**：先测 headroom；headroom 小的时候，稳健的折中组织比动态重组更划算。

### 还没有答案的

- 10+ agent、跨天/跨周项目、异构模型混用；
- 真实 trace 校准后的组织仿真；
- 跨 worktree 的合并语义、集成测试归属、合并冲突的自动解决；
- 人工验收的吞吐瓶颈（TOC 说的“瓶颈通常在验收，不在 agent”）；
- 组织重构的成本模型；
- 安全与权限：agent 能改什么、不能改什么，如何审计；
- 语义合并的质量：diff3/CRDT/LLM 修复在真实文档上的效果。

---

## 12. 实现进度（2026-11-08）

P0 协调器已经从设计变成可运行代码。当前证据：

| 能力 | 状态 | 证据 |
| --- | --- | --- |
| 任务/写集/依赖/原子 claim/TTL/fencing | ✅ | 21 个协调器测试 |
| runs 遥测 + 估计器 | ✅ | 3 条真实 run；`estimate.py` |
| MILP 排期 + 2D/3D Pareto | 🟡 6 任务玩具 | `plan_milp.py`、CBC、5 个 3D 非支配点 |
| 独立验证 | ✅ | 3 个真实 agent 的 verified live demo |
| N-of-M 审批 + TTL | ✅ | 2-of-2 governance live demo |
| 预算止损 | ✅ | $0.10 预算 / $0.12 花费 → `budget_exceeded` |
| 计划回写 + reservation | ✅ | `apply_plan` + `claim_planned` |
| V1 Core 切片 | 🟡 第六块 | Run/预约/Candidate/Merge/Worktree/lifecycle/双重门 + DSH 5 + Codex 1 + 3-agent 并发；27 个测试 |
| 真实 token/cost | ❌ | live demo 里仍是 0 占位 |
| 跨机/权限 | ❌ | 单机 SQLite；无 ACL |

```text
91 个单元测试：
  test_coordinator.py  21
  test_estimate.py      2
  test_plan_milp.py     9
  test_verifier.py     10
  test_v1_core.py      16
  test_v1_worktree.py   5
  test_v1_lifecycle.py  3
  test_v1_git_merge.py  2
  test_adapters.py     10
  test_or_router.py     7
  test_semantic.py      6
```

三次真实 DSH subagent live demo：

- baseline：3 agents claim → write → commit；
- verified：commit 后停在 `verification`，独立 verifier 跑 checks；第一次 root 配错，3 个任务被全部打回 `ready`，修正后 3/3 通过；
- governance：verification 通过后进入 2-of-2 审批，alice + bob 各签一次才 done。

**读法**：P0 的“协调 + 验收 + 问责”闭环已经跑通；P1/P2 是玩具规模；真实成本、跨机、权限还没有。

---

## 13. 路线图

### P0：可观测 + 可协调（1–2 周）

**状态：基础版已实现**——[p0-coordinator/README.md](../../p0-coordinator/README.md)（SQLite + MCP + CLI；任务/写集/依赖/原子 claim/TTL 租约/fencing/事件/runs 遥测/估计器/MILP/2D+3D Pareto/自动验证/N-of-M 审批（TTL）/预算止损/后台回收/计划回写/预留/executions/file_reservations/candidates/merge_attempts/workspaces/lifecycle/双重门/真实 Git cherry-pick/Adapter/Electron/WebView2；91 个单元测试 + 真实 DSH/Codex/3-agent 并发 + App Server V1 + WebView2 写操作 + MCP/CLI 冒烟）。

- 任务卡：写集、依赖边、DoD、验收标准；
- 原子 claim/lease（TTL + fencing token）；
- 每次运行记录 cost/quality/duration/diff（token 字段已留，真实采集待接）；
- 人工验收队列 + N-of-M 审批 + TTL。

### P1：可配置组织（2–4 周）

**状态：估计器已实现，组织配置未产品化。**

- 已实现 `runs → estimate.py → worker/kind 先验`；真实 trace 仍未接；
- flat / supervisor-worker / pipeline 还没做成可切换配置；
- 仿真给出的 headroom ≈ 0，说明“动态组织”优先级应低于“先把 trace 做真”；
- 下一步：在真实任务上测“组织 × 任务类型”收益矩阵。

### P2：约束下的分配/排期（1–2 月）

**状态：6 任务玩具规模已跑通 2D/3D Pareto；规模化未做。**

- 已实现 `plan_milp.py`：依赖/容量/写集互斥/质量下限，CBC 求解；
- 已实现 2D（cost–makespan）与 3D（+quality）Pareto、计划回写、reservation；
- 任务 ≤50 的 CP-SAT / 时间索引模型还没做；
- 预算 + Pareto 联合优化、学习策略决策门还没产品化。

### P3：文档并发控制（1–2 月）

- 先做章节级 lease + MVCC 提交校验；
- 再做三路合并/冲突队列；
- 最后考虑 CoAgent 式 advisory + agent 修复。

### P4：跨边界才用 A2A

- 同工作区继续原生 subagent；
- 跨机/跨厂商/外部 agent 用 A2A；
- 工具层用 MCP，编辑器集成用 ACP。

---

## 14. 文件索引

**实验**
- V1 计划对比：[AgentGuild-v1.0.1-对比分析.md](../评审/AgentGuild-v1.0.1-对比分析.md)
- 当前状态汇总：[当前状态-2026-11-08.md](../现状/当前状态-2026-11-08.md)
- P0 协调器：[p0-coordinator/README.md](../../p0-coordinator/README.md)
- 真实 agent baseline：[p0-coordinator/live-demo/REPORT.md](../../p0-coordinator/live-demo/REPORT.md)
- 真实 agent + 验证：[p0-coordinator/live-demo/verified/REPORT.md](../../p0-coordinator/live-demo/verified/REPORT.md)
- 真实 agent + 验证 + 2-of-2：[p0-coordinator/live-demo/governance/REPORT.md](../../p0-coordinator/live-demo/governance/REPORT.md)
- 三 agent 并行开发：[demo-ledger/README.md](../../experiments/demo-ledger/README.md)
- 并发写实验：[contention/README.md](../../experiments/contention/README.md)
- 通信 vs 原子性：[agent通信与冲突-实验.md](agent通信与冲突-实验.md)
- A2A 实测：[A2A-可行性-附记.md](A2A-可行性-附记.md)
- 文档并发编辑：[doc-sim/doc_sim.py](../../experiments/doc-sim/doc_sim.py)

**设计**
- 协调插件 + 运筹学：[多agent协调插件-运筹学方案.md](多agent协调插件-运筹学方案.md)
- 从 trace 学习策略：[学习型优化-从trace到策略.md](学习型优化-从trace到策略.md)
- 门控机制：[门控机制-实验与设计.md](门控机制-实验与设计.md)
- 动态组织设计：[多agent动态组织设计-问题定义.md](多agent动态组织设计-问题定义.md)
- 组织仿真：[org-sim/org_sim.py](../../experiments/org-sim/org_sim.py)
- Document Kernel / OS 类比：[多agent文档编辑-操作系统类比.md](多agent文档编辑-操作系统类比.md)

**文献**
- Agent Guild 论文与项目：[AgentGuild-论文与开源项目-调研.md](../调研/AgentGuild-论文与开源项目-调研.md)
- 缺口与 GitHub 选型：[AgentGuild-缺口与GitHub参考.md](../调研/AgentGuild-缺口与GitHub参考.md)

**起点**
- 朋友的原始草案：`C:\Users\steve\Downloads\Agent_Guild_项目构想与架构草案_2026-11-07.md`
