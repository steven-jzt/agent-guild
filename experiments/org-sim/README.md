# org-sim：组织 archetype 的机制仿真

回答两个问题：

1. 是不是存在“放之四海而皆准”的多 agent 组织？
2. 按任务动态选组织，能比一个固定组织好多少？

## 运行

```powershell
python org_sim.py
```

无第三方依赖，纯标准库。

## 模型

- 3 种组织：`flat`（扁平 peer）、`hierarchy`（主管 + 2 worker + 评审）、`pipeline`（设计→编码→测试三阶段）；
- 3 类任务互依性：`pooled`（可并行）、`sequential`（链式依赖）、`reciprocal`（强耦合）；
- 9 个任务/局，60 个种子；
- agent 有速度、价格、按任务类型的技能；组织决定分配策略、协调开销、冲突概率、评审/测试加成。

## 输出

- 组织 × 任务类型的质量/成本/工期/冲突矩阵；
- 不同目标权重下“每类任务的最优组织”（phase 表）；
- 稳态下 oracle / best-fixed / adaptive 的对比，以及 **headroom = oracle − best fixed**；
- 任务组合突变时，固定组织与自适应组织的对比。

## 当前结论（2026-11-07）

- 组织选择对目标权重大幅敏感：成本/时间优先 → flat；质量优先 → hierarchy；极端质量优先 → pipeline。
- 但 hierarchy 是一个很强的折中组织：稳态下 `oracle = best fixed = 0.804`，**headroom ≈ 0**；自适应 bandit 只有 0.800（探索成本）。
- 任务组合从 pooled 切到 sequential 时，固定 hierarchy 仍是 0.781，自适应 0.773（略差）。

**这个仿真是风格化模型，不是实证。** 参数是人为设定的，用来验证机制逻辑；真实结论要用运行 trace 校准。它最有用的产出不是“哪个组织最好”，而是 **headroom 这个度量**：先算 oracle − best fixed，再决定要不要做动态组织。
