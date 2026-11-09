# coordination —— 通信 vs 原子性对照实验

回答一个问题：agent 之间“通信、共享信息”能不能减少对同一文件的写冲突？

3 个 agent 进程对同一个 JSON 计数器各做 30 次读-改-写，期望 90；跑 6 种协议、每种 5 轮。

| 协议 | 通信 | 行动 | 原子性 |
| --- | --- | --- | --- |
| `silent` | ❌ | — | ❌ |
| `paced` | ❌ | — | ❌（与 announce 同节奏的对照组） |
| `announce` | ✅ 广播意图 | ❌ 不避让 | ❌ |
| `polite` | ✅ 广播 + 接收 | ✅ 退避重试 | ❌ |
| `lease` | ✅ 申请/授予 | ✅ 持锁才写 | ✅ 中心化互斥 |
| `cas` | ❌ | — | ❌（check-then-act） |

## 运行

```powershell
python run_experiment.py
```

输出每种协议 5 轮的 final / lost / retries / messages / 耗时，以及平均值。

## 结论（详见 ../agent通信与冲突-实验.md）

- 纯广播意图 ≈ 无效（和同节奏的 paced 组几乎一样）；
- 信息 + 退避：丢失从 61% 降到 2.4%，但不归零；
- 通信 + 原子租约：0 丢失；
- check-then-act 的版本校验：仍丢 38%——原子性必须在写入那一刻。
