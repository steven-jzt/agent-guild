# 真实多 agent 并发（2026-11-08）

## 1. 结果

`pilot/run_multi_agent.py`：3 个真实 agent 并发，同一项目、同一 base、各自独立 worktree + 文件预约。

| agent | 模型/路径 | 改的文件 | 耗时 | 结果 | usage（in/cached/out） |
| --- | --- | --- | ---: | --- | --- |
| DSH | deepseek-flash（DSH headless） | `src/a.py` | 12.7s | ✅ accepted | 545 / 6528 / 106 |
| Codex-DeepSeek | Codex CLI + deepseek-flash | `src/b.py` | 24.4s | ✅ accepted | 53436 / 51840 / 1680 |
| Codex-智谱 | Codex CLI + glm-5.3 | `src/c.py` | 23.9s | ✅ accepted | 16026 / 15488 / 168 |

总墙钟：**26.8 秒**（并发，不是 12.7+24.4+23.9 的串行和）。

三个候选：

```text
dsh:            9775436c8e075c6ee84d4b52008a246a9e2ce043
codex-deepseek: c7d8223b63e27903a1879644a43bc1a8dba2b8dc
codex-zhipu:    519df6c208d5ddfc69bc03f4b8f917b7ee375ab3
```

三个任务全部 `candidate=merged`、`lifecycle=accepted`、`status=done`。

## 2. 并发结构

```text
同一 repo / 同一 base SHA
├─ worktree dsh            → 只改 src/a.py
├─ worktree codex-deepseek → 只改 src/b.py
└─ worktree codex-zhipu    → 只改 src/c.py

每个 agent 一个 execution + 一个文件预约
→ scope check：无越界
→ commit → Candidate → evidence（exit 0）
→ approve → MergeAttempt → accepted
```

关键点：

- 不是三个 agent 抢同一个文件，而是**文件级隔离 + 预约**；
- 三个 agent 同时跑，不是串行排队；
- 每个候选独立绑定自己的 SHA；
- 没有手工改 DB。

## 3. 这次证明了什么

- DSH、Codex-DeepSeek、Codex-智谱可以在**同一项目**里并发执行；
- 文件预约 + worktree 让三方互不覆盖；
- 三个 agent 的产物都能独立走 Candidate/Evidence/Merge；
- 多 agent 的“墙钟时间”接近最慢的那个，而不是三者之和。

## 4. 没证明什么

- `finish_merge` 仍是规则层合入，不会真的把三个 commit 合进原仓库；
- demo repo 很小，不是真实项目；
- 三个任务都是“加一个函数”，没有跨文件/依赖/冲突；
- 智谱走 GLM Coding Plan，cost 不是按 token 账单；
- Codex 走 `codex exec`，不是 App Server JSON-RPC（App Server 冒烟本轮卡住，未完成）。

## 5. 复现

```powershell
cd p0-coordinator
$env:DEEPSEEK_API_KEY="sk-..."
python ..\pilot\run_multi_agent.py
```

智谱配置在隔离的 `CODEX_HOME`：

```text
p0-coordinator/.tmp/codex-zhipu-home/
```

## 6. 成本

- DSH + Codex-DeepSeek：约 **¥0.0099**（按 DeepSeek 官方价目表）；
- Codex-智谱：按 GLM Coding Plan 套餐计，不按 token 账单；
- 明细见 [COST.md](COST.md)。
