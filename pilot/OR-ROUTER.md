# OR Router：运行时 agent 推荐（Phase 3 第一步）

日期：2026-11-09  
目标：把 OR 从“离线 MILP/Pareto 算例”推进到“每次 Run 前可解释地选 agent”。

## 1. 输入

- 历史 `runs`：worker、duration、tokens、cost、status；
- agent 默认画像（无历史时）；
- 项目剩余预算（`project_budget().remaining_usd`）。

默认画像：

| agent | 默认耗时 | 默认成本 | 默认成功率 |
|---|---:|---:|---:|
| dsh | 18s | $0.0010 | 1.00 |
| codex-deepseek | 32s | $0.0060 | 0.95 |
| codex-zhipu | 28s | $0.0040 | 0.90 |
| codex-appserver | 45s | $0.0080 | 0.95 |

有历史时，用历史均值覆盖默认值。

## 2. 策略

- `cost`：成本优先；
- `time`：耗时优先；
- `quality`：成功率优先，其次成本。

预算：

- 估算成本 > 剩余预算 → `feasible=false`；
- 全部超预算 → 返回最便宜方案，并标记“全部超出预算”。

## 3. CLI

```powershell
python worker_cli.py --db ... recommend-agent --task T-xxxx --strategy time
```

输出：

```json
{
  "strategy": "time",
  "recommended": {
    "agent": "dsh",
    "avg_seconds": 18.0,
    "estimated_cost_usd": 0.001,
    "source": "default",
    "reason": "无历史，使用默认画像"
  },
  "candidates": [...]
}
```

自动选：

```powershell
python worker_cli.py --db ... start-agent --task T-xxxx --agent auto --strategy time
```

结果会多一个 `or` 字段，记录为什么选这个 agent。

批量分配：

```powershell
python worker_cli.py --db ... recommend-batch --project P-xxxx --strategy quality --max-parallel 4
```

规则：

- 每个 agent 同时只接一个任务；
- 预算按剩余额度逐项扣减；
- 没有空闲 agent 或剩余预算不足的任务进入 `unassigned`。

## 4. UI

Agent 下拉新增：

```text
Auto（OR 推荐）
```

选 Auto 后，Run 完成 notice 会显示：

```text
OR 选择 dsh
```

## 5. 实测

在 `agent-demo` 新任务上：

```text
task: T-e282ee16
strategy: time
recommended: dsh
reason: 无历史，使用默认画像
executed: dsh
duration: 16.6s
candidate: C-4aa9f055
candidate_status: awaiting_human
estimated_cost_usd: 0.000766
check_exit: 0
```

说明：

- 推荐器选中 DSH；
- 实际执行也是 DSH；
- Run 的成本已按 token 估算写回 `runs.cost_usd`，预算和后续推荐可用。

批量实测（4 个任务，预算 $0.01）：

```text
assignments:
  batch task 1 → dsh            $0.001
  batch task 2 → codex-deepseek $0.006
unassigned:
  batch task 3 → 剩余预算不足
  batch task 4 → 剩余预算不足
remaining_budget_usd: 0.003
```

## 6. 测试

```powershell
python -m unittest test_or_router -v
```

覆盖：

- 无历史按成本选 DSH；
- 有历史按耗时选更快的 agent；
- 按成功率选质量更高的 agent；
- 预算排除昂贵 agent；
- 批量分配不同 agent；
- 批量受 `max_parallel` 限制；
- 批量受预算限制。

```text
Ran 7 tests
OK
```

## 7. 限制

- 历史样本还很少，默认画像权重大；
- 成本是 token 估算，不是最终账单；
- 只做“单任务 + 简单批量”，还没有依赖/冲突约束下的全局排期；
- 预算只覆盖 `cost_usd`，尚未把时间/质量折算成预算。

## 8. 下一步

- 带依赖/文件冲突的批量排期；
- 更复杂的预算约束；
- 从 runs 学习成功率/时延，而不是依赖默认值；
- UI 展示候选对比和“为什么没选 Codex”。
