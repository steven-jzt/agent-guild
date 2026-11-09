# 真实 token / cost 估算（2026-11-08）

## 1. 官方价目表

来源：[DeepSeek 模型 & 价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)

`deepseek-flash`，单位：元 / 百万 tokens：

| 项目 | 空闲时段 | 高峰时段 |
| --- | --- | --- |
| 输入（缓存命中） | 0.02 | 0.04 |
| 输入（缓存未命中） | 1.00 | 2.00 |
| 输出 | 4.00 | 8.00 |

高峰：北京时间周一至周五 9:00–12:00、14:00–18:00；其余为空闲。  
本次任务在 12:00 后，按**空闲时段**计算。

## 2. 公式

```text
uncached = max(input_tokens - cached_input_tokens, 0)
cost_cny = uncached / 1e6 * 1.00
         + cached_input_tokens / 1e6 * 0.02
         + output_tokens / 1e6 * 4.00
```

说明：DSH 的 `inputTokens` 与 `cachedInputTokens` 存在口径不一致（cached 可能大于 input），这里用保守公式；真实账单可能略有差异。

## 3. 5 个 DSH 任务

| 任务 | input | cached | output | cost_cny |
| --- | ---: | ---: | ---: | ---: |
| add mul | 553 | 6656 | 134 | 0.000669 |
| add sub | 547 | 6400 | 139 | 0.000684 |
| scope other | 543 | 6272 | 88 | 0.000477 |
| no change | 527 | 5760 | 3 | 0.000127 |
| add mul #2 | 261 | 6784 | 134 | 0.000672 |
| **合计** | 2431 | 31872 | 498 | **0.002629** |

DSH 合计：**约 ¥0.0026**。

## 4. 1 个 Codex 任务

| input | cached | output | cost_cny |
| ---: | ---: | ---: | ---: |
| 53408 | 51200 | 1634 | **0.009768** |

Codex 合计：**约 ¥0.0098**。

## 4.1 三 agent 并发

| agent | input | cached | output | cost_cny |
| --- | ---: | ---: | ---: | ---: |
| DSH | 545 | 6528 | 106 | 0.000555 |
| Codex-DeepSeek | 53436 | 51840 | 1680 | 0.009353 |
| Codex-智谱 | 16026 | 15488 | 168 | 套餐计（不按 token） |

DSH + Codex-DeepSeek 合计：**约 ¥0.0099**。  
智谱走 GLM Coding Plan，按套餐计费，不能按上表 token 单价直接算。

## 4.2 多模块真实仓库（sample-shop）

| agent | input | cached | output | cost_cny |
| --- | ---: | ---: | ---: | ---: |
| DSH | 561 | 6784 | 186 | 0.000880 |
| Codex-DeepSeek | 50869 | 49408 | 1215 | 0.007309 |
| Codex-智谱 | 16035 | 15552 | 150 | 套餐计 |

DSH + Codex-DeepSeek 合计：**约 ¥0.0082**。

## 5. 总计

```text
DSH 5 tasks:             ¥0.0026
Codex 1 task:            ¥0.0098
3-agent concurrent:      ¥0.0099（DeepSeek 两家；智谱套餐另计）
multi-module repo:       ¥0.0082（DeepSeek 两家；智谱套餐另计）
total DeepSeek:          ¥0.0305  （约 3 分人民币）
```

如果全部落在高峰时段，按官方表约为 2 倍：**约 ¥0.061**（不含智谱套餐）。

## 6. 边界

- 这是**按官方价目表 + usage 的估算**，不是 DeepSeek 账单；
- DSH 的 cache token 口径不明确，实际可能有偏差；
- 未计算 Search/Fetch（本轮未用）；
- `runs.cost_usd` 仍写 0；真实人民币成本记录在本报告与 `runs.meta.usage` 中；
- 若要写进 DB，建议加 `cost_cny` 列，而不是把人民币塞进 `cost_usd`。
