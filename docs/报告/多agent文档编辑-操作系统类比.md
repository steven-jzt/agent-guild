# 多 Agent 文档编辑：把操作系统进程模型搬过来

> 针对“多个 agent 编辑同一份文档”的冲突问题，讨论 OS 进程/内存/调度/保护机制的迁移。
> 结论：**能迁移，但要“OS + 数据库”双借鉴**；纯 OS 锁不够，纯数据库事务也不够。

---

## 0. 一句话结论

**OS 的进程模型解决“隔离、调度、权限、故障恢复”；数据库的事务模型解决“共享文档的一致性”。**

- 每个 agent 像一个进程：私有地址空间（文档快照 + 上下文）、PID、文件描述符、信号、配额、权限；
- 文档像一块共享内存/文件：分页（章节/段落/AST 节点）、页表（版本映射）、COW、锁/租约、MVCC、原子提交；
- 但 agent 的“事务”长达几分钟、读集不透明、状态不能 fork/缓冲——所以**不能直接套 2PL**，要用“租约 + 乐观并发 + 代理裁决 + 可撤销写”。

---

## 1. 为什么这个类比成立

OS 几十年就在解决“多个进程并发访问共享资源”：

| OS 问题 | 多 agent 文档编辑的对应问题 |
| --- | --- |
| 进程隔离 | 每个 agent 不能随便改别人的工作副本 |
| 虚拟地址空间 | agent 的上下文 + 它看到的文档版本 |
| 页/页表 | 文档的章节/段落/AST 节点 + 版本映射 |
| 缺页/换页 | 上下文按需加载/摘要/淘汰 |
| 互斥锁/读写锁 | 章节的写租约/读租约 |
| 信号 | 取消、暂停、超时 |
| 调度器 | 哪个 agent 先跑、并发多少 |
| 优先级/优先级反转 | 人工评审等低优先级 agent 释放锁 |
| cgroups/配额 | token、时间、并发数限制 |
| 用户态/内核态 | agent 权限 vs 平台权限 |
| 系统调用 | MCP 工具（读/写/提交/加锁） |
| 能力（capability） | 细粒度文档权限 |
| 管道/共享内存 | A2A/MCP/黑板 |
| 日志/崩溃恢复 | 事件日志、快照、回滚 |
| 僵尸/孤儿进程 | 断连 agent 的清理与接管 |
| 死锁检测 | 等待图：A 等 B 的评审，B 等 A 的修改 |

这张表说明：**你直觉里的“进程方式”，在资源管理和隔离层面几乎逐项对得上。**

---

## 2. 三个可以直接搬的机制

### 2.1 进程隔离 + COW（写时复制）

OS 的 `fork()` 不复制整个地址空间，而是 COW：父子共享只读页，谁写谁复制。

对应到文档：

- agent 启动时拿到文档的**只读快照**（version V）；
- 它写某个章节时，只复制该章节（region-level COW），形成私有副本；
- 提交时做**版本校验 + 原子替换**；冲突则走合并/重试；
- 好处：agent 可以“大胆改”，不会影响别人；失败可丢弃。

这就是 git worktree/branch 的语义，但粒度可以细到章节/段落，而不是整个仓库。

### 2.2 虚拟内存 + 缺页（上下文分页）

MemGPT 已经把“LLM 上下文 = 内存”讲得很清楚：上下文窗口是 RAM，外部存储是磁盘，缺页时把需要的内容换入。

对应到文档编辑：

- agent 不需要一次读整份文档；它持有“页表”（哪些章节在上下文里、版本是多少）；
- 需要某章节时触发“缺页”，从文档存储/检索里加载；
- 上下文满了就“换页”：摘要、压缩、淘汰；
- 页表记录**它读过的版本**，提交时用来做 read-set validation。

这直接解决“上下文窗口不够 + 读集不透明”的问题：不是让 agent 读全部，而是让它知道自己读了哪些页、版本是多少。

### 2.3 调度器 + 配额 + 信号

OS 调度器决定谁跑、跑多久；cgroups 限制资源；信号可以取消/暂停。

对应到 agent：

- 并发上限（不要让 20 个 agent 同时改一份文档）；
- 优先级（人工验收 > 紧急修复 > 普通任务）；
- 配额（token/时间/调用次数）；
- 取消/超时（`SIGCANCEL`），防止一个 agent 占着章节写租约几小时；
- 看门狗（watchdog）回收僵尸 agent 的租约。

这些都是“组织设计”章节里 Dynamics 的运行时基础。

---

## 3. 三个不能直接搬的地方

### 3.1 锁的持有时间：微秒 vs 分钟

OS 的 mutex 通常持有微秒级；agent 的一次“读—想—写”可能几分钟到几十分钟。直接上悲观锁：

- 其他 agent 长时间阻塞；
- 锁超时/崩溃导致死锁；
- 吞吐骤降。

所以要用**租约（lease）**而不是锁：

- 有 TTL，持有者必须心跳续约；
- 过期自动回收；
- 配 **fencing token**（单调递增 epoch），提交时校验，防止过期持有者写入；
- 长任务拆成“读快照 → 离线推理 → 提交时校验”，而不是全程持锁。

### 3.2 冲突是语义的，不是字节的

OS 的锁保护的是字节/页；文档冲突是**语义冲突**：

- A 改了接口名，B 还在用旧名；
- A 删了章节，B 在改该章节；
- A 和 B 对同一段话做了不同的重写。

锁只能保证“不同时写”，不能保证“写完合起来是对的”。所以还需要：

- 三路合并（diff3）；
- CRDT/OT（如果是结构化、可交换的编辑）；
- LLM 语义合并 + 人工验收；
- 变更通知 + 重读（Passes Alone 的 82% 恢复率）；
- 提交时 read-set validation（你读的版本变了吗？）。

### 3.3 读集不透明：OS 知道页，agent 不一定知道

OS 的 MMU 精确记录每个进程访问了哪些页；agent 的“读了什么”往往是一大段上下文，无法静态推断。

解法：

- **足迹声明（footprint declaration）**：工具调用时声明读了哪些 region/version；
- **读集跟踪**：在工具层记录 read set，而不是靠 agent 自报；
- **能力化工具**：只给 agent 能读/能写的 region，越权直接拒绝；
- **提交校验**：read set 里任何一个 region 版本变了，就要求重读/重判。

CoAgent 的做法更进一步：让 agent 自己判断“这个冲突写是否使我的计划失效”，只修复依赖它的操作，而不是全部重来。

---

## 4. 已有工作：别人已经走到哪

- **[AIOS: LLM Agent Operating System](https://arxiv.org/abs/2403.16971)**（COLM 2025）
  把调度、上下文管理、内存、存储、访问控制收进一个 AIOS kernel；agent 应用通过 SDK 调用。报告最高 2.1x 加速。→ 证明“agent OS”不是比喻，是可以工程化的。

- **[Agent Operating Systems (AOS)](https://arxiv.org/abs/2606.01508)**（2026）
  明确定义 AOS：把 agentic control plane 集成进传统 OS 或独立存在；职责拆成调度器、上下文/内存、工具/能力注册、策略/信任、可观测/审计；并分析经典 OS 抽象对 agent 负载的局限。→ 这是“OS 类比”的系统化版本。

- **[CoAgent: Concurrency Control for Multi-Agent Systems](https://arxiv.org/abs/2606.15376)**（2026）
  直接研究“多个 agent 同时改同一个 git tree / 文档”的并发控制。结论很关键：**2PL 和 OCC 都不适配**——agent 事务长达数分钟、读集宽且不透明、live state 不能 fork/缓冲，写会立刻生效。它提出 MTPO：启动时固定序列化顺序，读时返回按序过滤的值，写先原地投机执行；发现冲突就单向通知受影响的 reader，让 agent 自己判断并修补计划；框架用 saga 式逆操作撤销/重排。结果：10 个争用负载上，在串行正确性 5% 以内，1.4x 加速、接近串行的 token 成本；bash-only 场景在线长出 25 个工具，通过率 45/71 → 63/71。
  → **这是“OS/DB 并发控制 + LLM 语义修复”最直接的参考。**

- **[MemGPT](https://arxiv.org/abs/2310.08560)**：把上下文当虚拟内存，分页/换入换出。→ 对应“上下文分页”。

- **CRDT/OT**：协同编辑的经典路线（Shapiro et al. 的 CRDT 综述；Sun et al. 的 OT）。→ 对应“语义合并”里的可交换编辑。

- **分布式锁/租约**：Gray & Cheriton 的 Leases（1989）；Kleppmann 的 fencing token。→ 对应“长租约 + 过期保护”。

---

## 5. 建议的架构：Document Kernel

```text
┌──────────────────────────────────────────────┐
│ Agent 进程（每个 agent 一个）                 │
│  PID / capabilities / quota / signal handler │
│  private view (COW) + page table (versions)  │
└──────────────────┬───────────────────────────┘
                   │ syscalls（MCP tools）
┌──────────────────▼───────────────────────────┐
│ Document Kernel                              │
│  Scheduler │ Lease Mgr │ Version Store       │
│  Merge Engine │ Capability/Policy │ Monitor  │
└──────────────────┬───────────────────────────┘
                   │
┌──────────────────▼───────────────────────────┐
│ Document Store（region tree + MVCC + WAL）   │
└──────────────────────────────────────────────┘
```

### 5.1 文档模型

- 文档 = region tree（标题/章节/段落/表格/代码块），每个 region 有 `id, version, owner, lock`；
- 版本存储用 MVCC：每次提交生成新版本，不覆盖旧版本；
- 提交是原子的：多 region 一起提交，要么全成，要么全失败；
- 结构编辑（移动/删除章节）用**意向锁**（intention lock），避免和子树编辑冲突。

### 5.2 进程模型

```text
AgentProcess = {
  pid, parent_pid,
  capabilities: {read: [region...], write: [region...], tools: [...]},
  quota: {tokens, seconds, max_concurrency},
  page_table: {region_id: version},
  leases: [(region_id, mode, expiry, fencing_token)],
  state: running | waiting | canceled | zombie
}
```

### 5.3 系统调用（MCP tools）

```text
open(doc) -> handle
map(region, RO|RW) -> private COW view
lock(region, SHARED|EXCLUSIVE, ttl) -> lease_id + fencing_token
read(region) -> {content, version}
write(region, patch) -> staged
commit(handle) -> 校验 read-set/lease/version，原子提交
abort(handle) -> 丢弃私有 view
fork(handle) -> 分支（what-if）
signal(pid, CANCEL|PAUSE|RESUME)
wait(pid) -> join
stat(doc) -> {regions, versions, owners, waiters}
```

### 5.4 冲突处理策略（按 region 类型）

| 场景 | 策略 |
| --- | --- |
| 不同章节并行编辑 | 乐观并发：各自 COW，提交时版本校验；无冲突直接合 |
| 同一段落并行重写 | 单写者租约，或 MVCC + 三路合并 + agent/人工裁决 |
| 跨章节一致性（术语/接口） | 版本向量 + 变更通知 + read-set 校验；必要时全局读租约 |
| 结构编辑（移动/删除章节） | 意向锁 + 子树锁；提交时检查树结构版本 |
| 多文件/多仓库 | worktree/branch + 合并队列（更粗粒度） |
| 长任务 | 不持锁推理：读快照 → 离线 → 提交时校验；租约只保护“提交窗口” |
| 死锁（A 等 B、B 等 A） | 等待图 + 超时 + 升级人工；资源排序预防 |
| 优先级反转（人工评审等低优 agent） | 优先级继承/提升 |

### 5.5 调度与配额

- 并发上限：按文档/项目设 WIP（Little's Law）；
- 优先级：人工验收 > 修复 > 新功能；
- 配额：token/时间/工具调用，按 agent 记账；
- 抢占：超时/超预算发 `SIGCANCEL`，回滚未提交写；
- 看门狗：心跳丢失 → 回收租约 → 标记 orphan。

---

## 6. 和“纯 OS 锁”方案的对比

| 方案 | 隔离 | 吞吐 | 冲突处理 | 适合 |
| --- | --- | --- | --- | --- |
| 全局互斥锁 | 强 | 低 | 不冲突 | 小团队、强一致 |
| 章节写租约（悲观） | 强 | 中 | 阻塞等待 | 高冲突、短任务 |
| COW + MVCC（乐观） | 强 | 高 | 提交时合并/重试 | 低冲突、长任务 |
| CoAgent 式 advisory + 修复 | 强 | 高 | agent 判断 + saga 撤销 | 读集不透明、语义冲突 |
| worktree/branch | 最强 | 高 | 合并队列 | 代码/多文件 |

**建议默认走“COW + MVCC + 章节租约”的混合**：读多写少用乐观，高冲突区用租约，结构编辑用意向锁，长任务不在推理期间持锁。

---

## 6.5 首轮实验结果（2026-11-07）

按第 7 节的设计先跑了一版机制实验（[`doc-sim/doc_sim.py`](../../experiments/doc-sim/doc_sim.py)）：3 个 agent、6 个章节、30 个种子、4 种策略。

### 追加场景（12 个任务各追加一行）

| 策略 | 保留 / 12 | 丢失 | 重试 | 耗时 |
| --- | ---: | ---: | ---: | ---: |
| `none` | 8.5 | 3.5 | 0 | 25.9ms |
| `global` | 12.0 | 0 | 0 | 69.6ms |
| `section` | 12.0 | 0 | 0 | 31.8ms |
| `mvcc` | 12.0 | 0 | 3.4 | 25.7ms |

- 不协调丢约 29% 的追加；
- 全局锁正确但串行；
- 章节锁正确且快（不同章节并行）；
- MVCC 也正确、最快，但付 3.4 次重试/合并。

### 改同一行场景（3 个 agent 抢改 `Status`）

| 策略 | 检测到冲突 | 静默覆盖 | 最终 S1 | 最终 S2 |
| --- | ---: | ---: | --- | --- |
| `none` | 0 | 4 | v2 | v5 |
| `global` | 0 | 4 | v2 | v5 |
| `section` | 0 | 4 | v2 | v5 |
| `mvcc` | 4 | 0 | v0 | v3 |

- **锁保证“不同时写”，但不保证“知道别人也想改”**：全局锁/章节锁下，三个 agent 依次覆盖，前两个意图静默消失；
- **MVCC 检测到 4 次冲突**，但选择 first-writer-wins，后写者白做；
- 还缺第三层：**语义合并/裁决**（三路合并、agent 修复、人工验收、先 claim 再改）。

这正好验证了第 3 节的判断：**OS 锁解决原子性，数据库 MVCC 解决检测，语义合并要单独建。**

## 7. 一个可继续验证的实验

**问题**：在同一份 Markdown 规格文档上，3 个 agent 并行编辑，哪种并发控制最好？

**四种策略**：

1. 全局锁；
2. 章节写租约（悲观）；
3. COW + MVCC + 提交校验；
4. CoAgent 式 advisory + 修复（简化版：冲突通知 + read-set 失效 + 重读修补）。

**任务**：3 类编辑——不同章节、同一章节、跨章节术语统一；每类 20 次，3 个 agent。

**指标**：

- 冲突率、丢失更新、陈旧写；
- 完成时间、token 成本；
- 合并质量（人工/LLM 评审）；
- 人工干预次数；
- 死锁/超时次数。

**预期**：

- 不同章节：COW/MVCC 最快；
- 同一章节：租约或 advisory 更稳；
- 跨章节一致性：read-set 校验 + 通知显著减少返工；
- 全局锁：正确但最慢。

这能把“OS 类比”从概念变成一张可比较的表。

---

## 8. 风险与反模式

1. **把锁当时钟**：租约没有 fencing token，过期持有者仍能写 → 双写。
2. **锁粒度太细**：每个段落一把锁，管理开销超过收益；按章节/语义单元。
3. **锁粒度太粗**：整篇文档一把锁，退化成串行。
4. **让 agent 自觉遵守**：必须在工具层强制，不能靠 prompt。
5. **只锁不合并**：锁只能防同时写，不能防语义冲突；合并/验收必须独立。
6. **忽略优先级反转**：人工评审等一个低优先级 agent 的锁，体验会很差。
7. **忽略孤儿**：agent 断连后租约不回收，文档永远锁着。
8. **过度设计**：3 个 agent、低冲突的项目，直接 worktree + 合并队列就够，不需要 Document Kernel。

---

## 9. 参考

- AIOS：<https://arxiv.org/abs/2403.16971>
- AOS：<https://arxiv.org/abs/2606.01508>
- CoAgent：<https://arxiv.org/abs/2606.15376>
- MemGPT：<https://arxiv.org/abs/2310.08560>
- CRDT 综述：Shapiro et al., *A comprehensive study of Convergent and Commutative Replicated Data Types*（2011）
- OT：Sun & Ellis, *Operational Transformation in Real-Time Group Editors*（1998）
- Leases：Gray & Cheriton, *Leases: An Efficient Fault-Tolerant Mechanism for Distributed File Cache Consistency*（1989）
- Fencing：Kleppmann, *How to do distributed locking*（2016）
