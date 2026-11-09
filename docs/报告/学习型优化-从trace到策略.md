# 从 trace 学习优化策略：能不能借机器学习的训练思路？

> 回应“没有历史 trace，优化器就是拍脑袋”——能不能用 ML/DL 的训练方式，把“拍脑袋”变成“可学习、可验证的策略”。
> 结论：**能，但不要一上来端到端 RL。** 先监督估计，再上下文 bandit，最后才考虑离线 RL。

---

## 0. 一句话结论

**优化器的两个部分要分开训练：**

1. **估计器**（cost / duration / quality / over-design）→ 监督学习；
2. **策略**（任务分配给谁、选哪个组织、何时重组）→ 上下文 bandit / 离线 RL / 模仿学习。

大多数收益来自第 1 步；第 2 步只有在动作空间大、序列决策强、数据足够时才值得上 RL。

---

## 1. 三种“训练”范式，对应三个层次

| 层次 | 学什么 | 方法 | 数据需求 | 适合 |
| --- | --- | --- | --- | --- |
| **估计** | `cost(task, agent, model)`、`quality(...)`、`duration(...)` | 回归、分位数回归、GBDT、随机森林、贝叶斯层次模型 | 中 | 所有项目，第一步 |
| **决策** | 给定估计，选分配/组织/协议 | 上下文 bandit、ε-greedy、Thompson、UCB | 中 | 动作空间离散、反馈快 |
| **序列** | 多步分配、重组、交接 | 离线 RL、Decision Transformer、模仿学习 | 大 | 长任务、动作序列、状态转移明显 |

**推荐顺序**：估计 → bandit → 离线 RL。不要跳级。

---

## 2. 训练循环长什么样

```text
┌─────────────┐
│ 运行 agent  │
└──────┬──────┘
       │ 记录 state / action / outcome / reward / cost / quality
┌──────▼──────┐
│ Trace Store │  ← Postgres / 数据湖
└──────┬──────┘
       │ 特征工程 + 标签
┌──────▼──────┐
│ Estimator   │  ← 监督学习（cost/quality/duration）
└──────┬──────┘
       │ 预测参数
┌──────▼──────┐
│ Optimizer   │  ← CP-SAT / 枚举 / bandit
└──────┬──────┘
       │ 策略
┌──────▼──────┐
│ Shadow / A-B│  ← 只建议、不自动执行
└──────┬──────┘
       │ 反馈
       └────→ 回到 Trace Store
```

关键：**先 shadow mode**（优化器只建议，人/规则决定是否执行），再逐步放权。

在策略外面再包一层**门控**：置信度/稳定性不足时回退到规则/人工。门控实验与设计见 [门控机制-实验与设计.md](门控机制-实验与设计.md)。

---

## 3. 数据：记什么、标签从哪来

每条 trace 至少记：

```text
state:   任务特征（size/kind/依赖/耦合/写集）、agent 特征（模型/价格/速度/上下文）、
         项目特征（语言/仓库/历史质量）、系统状态（并发/队列/预算）
action:  分配给了谁、用了什么模型/上下文策略、是否加锁/租约
outcome: token、成本、工时、是否完成、测试结果、返工、人工验收
reward:  quality - λc·cost - λt·time - λr·risk（或 Pareto 标签）
meta:    policy version、propensity（选择该动作的概率）、时间戳
```

标签来源：

- **自动测试/验收**：强标签，但覆盖有限；
- **LLM-as-Judge**：弱标签，要校准（与人工验收对齐）；
- **人工验收**：强标签，但贵、稀疏；
- **失败/返工**：负标签，容易采集。

**Propensity 很重要**：日志是“策略选择后”的结果，不是随机实验。没有 propensity，离线评估会有偏。

---

## 4. 冷启动：没有 trace 怎么办

1. **先验**：厂商 benchmark、公开 leaderboard、历史项目；只作为先验，不要当结论。
2. **层次贝叶斯**：任务类型/agent/模型做随机效应，新任务从同类借力。
3. **LLM 嵌入 + kNN**：用任务描述/代码 diff 的 embedding 找相似历史任务。
4. **小规模因子实验**：不要随机分配；用 2×2×2 设计，少量样本估计主效应。
5. **模拟器校准**：用 `org-sim`/`or-planner` 生成合成数据，再用真实 trace 做 system identification（校准参数）。
6. **探索预算**：ε-greedy/Thompson，上限 5–10%，避免拿生产项目冒险。

---

## 5. 评估：别只看训练 loss

- **离线策略评估（OPE）**：IPS / doubly robust / direct method；需要 propensity。
- **Shadow mode**：优化器给建议，不执行；比较“建议 vs 实际”。
- **A/B**：小流量、按项目/任务分层；看质量、成本、周期、返工。
- **Regret**：与 oracle（事后最优）比；也要与 best fixed/heuristic 比。
- **校准**：预测质量 vs 实际验收；用 conformal prediction 给区间。
- **稳定性**：策略切换频率、重跑一致性、对噪声的敏感度。

---

## 6. 非平稳：模型/价格/任务会变

- 价格、模型版本、工具权限都会变；昨天的策略今天可能失效。
- 监控 drift（Page-Hinkley / ADWIN / 滑窗统计）；
- 定期重训 + 滑窗 bandit；
- 保留 fallback：模型不确定/分布外时回到规则/人工。

---

## 7. 小实验：从 trace 学分配策略

代码：[`learning-optimizer/train_optimizer.py`](../../experiments/learning-optimizer/train_optimizer.py)。

**设置**：复用 `or-planner` 的 6 任务 × 3 agent 环境；216 种可行分配；随机采样 N 条 trace（带 0.02 噪声），用模型预测 utility，枚举选最优；20 个种子。

**结果（mean regret，越低越好）**：

| N traces | linear | random forest | GBR | 贪心基线 |
| ---: | ---: | ---: | ---: | ---: |
| 5 | 0.0226 | 0.0651 | 0.0503 | 0.009 |
| 10 | 0.0193 | 0.0429 | 0.0459 | 0.009 |
| 20 | 0.0140 | 0.0344 | 0.0240 | 0.009 |
| 50 | 0.0118 | 0.0200 | 0.0184 | 0.009 |
| 100 | 0.0104 | **0.0086** | **0.0086** | 0.009 |
| 200 | 0.0094 | 0.0097 | 0.0112 | 0.009 |
| 400 | 0.0092 | 0.0096 | 0.0110 | 0.009 |
| 全量 216 | 0.0092 | 0.0096 | 0.0110 | 0.009 |

**怎么读**：

- **小数据（N≤20）**：线性模型最好；树模型过拟合。经典 bias-variance。
- **N≈100**：RF/GBR 追上来，略优于贪心（0.0086 vs 0.009），但仍远未到 oracle（0）。
- **N≥200**：模型开始过拟合/饱和；更多数据没有继续改善。
- **贪心基线很强**：它已经利用了一部分结构（按技能/价格选），简单 ML 很难大幅超过。
- **结论**：ML 训练思路可行，但在小、离散、低异质性的问题上，收益有限；它的价值在**大规模、异构、非平稳**的场景。

> 注意：这是合成环境、确定性收益、无策略偏差；真实 trace 有选择偏差、分布漂移、标签稀疏，难度高得多。

---

## 8. 落地路线（和 P0–P3 对齐）

### P0：先记数据

- 任务/agent/action/outcome/reward 全量记录；
- 记录 policy version、propensity；
- 先不做模型，只做规则 + 人工。

### P1：估计器

- 监督学习预测 cost/duration/quality；
- 分位数回归/GBDT/RF；用 conformal 给区间；
- 用估计器替换手填参数，跑 CP-SAT/枚举；
- 评估：预测误差、regret、与贪心对比。

### P2：上下文 bandit

- 分配/组织选择作为 arms；
- LinUCB/Thompson；ε 探索上限；
- OPE + shadow + A/B；
- 只在 headroom 大的场景启用。

### P3：离线 RL / 模仿学习

- 当动作是序列（分配→执行→交接→重组）、状态转移明显时；
- 用 Decision Transformer / IQL / CQL；
- 先模仿人类调度员，再 RL 微调；
- 安全：约束 RL、fallback、人工闸门。

---

## 9. 坑

1. **日志有偏**：只记录被选中的动作，不能当随机实验；要 propensity/探索。
2. **奖励黑客**：优化 quality 指标会让 agent 刷分；过度设计指标会让它少写代码。必须留人工验收/held-out。
3. **分布漂移**：模型/价格/任务变，策略失效；要 drift 监控和重训。
4. **小数据上深模型**：过拟合；先线性/树模型。
5. **端到端 RL 的样本效率**：LLM 调用贵，在线探索成本高；优先离线/bandit。
6. **把相关性当因果**：某 agent 成功率高可能只是它接的任务简单；要控制任务难度。
7. **忽略约束**：优化器可能选出“质量高但超预算/超时”的解；约束要硬。
8. **过度自动化**：shadow 期不够就放权，容易翻车。
9. **门控阈值拍脑袋**：门控太松会“半开半关”，可能比不开更差；必须用验证集校准，见 [门控机制-实验与设计.md](门控机制-实验与设计.md)。

---

## 10. 参考

- Contextual bandits：Li et al., *A Contextual-Bandit Approach to Personalized News Article Recommendation*（2010）；Lattimore & Szepesvári, *Bandit Algorithms*（2020）
- Offline RL：Levine et al., *Offline Reinforcement Learning: Tutorial, Review, and Perspectives*（2020）
- Decision Transformer：Chen et al., *Decision Transformer: Reinforcement Learning via Sequence Modeling*（2021）
- Predict-then-optimize：Elmachtoub & Grigas, *Smart “Predict, then Optimize”*（2022）
- Conformal prediction：Angelopoulos & Bates, *A Gentle Introduction to Conformal Prediction*（2021）
- Off-policy evaluation：Thomas & Brunskill, *Data-Efficient Off-Policy Policy Evaluation for Reinforcement Learning*（2016）
- 调度学习：RL for scheduling 的综述（如 *Deep Reinforcement Learning for Scheduling* 系列）
