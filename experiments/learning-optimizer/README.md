# learning-optimizer：从 trace 学分配策略

最小演示：用历史分配 trace 训练一个模型，预测 utility，再枚举选最优；看 regret 随 trace 数量的变化。

环境复用 `../or-planner/toy_optimizer.py`（6 任务 × 3 agent，216 种可行分配）。

## 运行

```powershell
python train_optimizer.py
```

依赖：numpy、scikit-learn（本机已装）。

## 输出

- 贪心基线的 regret；
- 线性 / 随机森林 / GBR 在 N=5…216 条 trace 下的平均 regret。

## 结论（20 seeds，噪声 0.02）

| N | linear | rf | gbr |
| ---: | ---: | ---: | ---: |
| 5 | 0.0226 | 0.0651 | 0.0503 |
| 20 | 0.0140 | 0.0344 | 0.0240 |
| 100 | 0.0104 | 0.0086 | 0.0086 |
| 216 | 0.0092 | 0.0096 | 0.0110 |

贪心基线 regret = 0.009；oracle = 0。

**读法**：小数据线性模型稳；N≈100 树模型略胜贪心；再多开始饱和/过拟合。ML 训练思路可行，但在小、离散、低异质性问题上的收益有限；真实价值在大规模、异构、非平稳场景。

> 合成环境、确定性收益、无策略偏差；真实 trace 有选择偏差/漂移/稀疏标签，难度高得多。
