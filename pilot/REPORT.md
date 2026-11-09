# B 内部试点：启动报告（2026-11-08）

## 1. 状态

- 阶段：第 1 周 D0（规则层 dry-run + 真实 DSH 5 任务 + 真实 Codex 1 任务）
- 结论：**V1 规则闭环 dry-run 通过；DSH 5 个任务、Codex（DeepSeek provider）1 个任务均通过；Electron 窗口仍崩溃。**

## 2. Dry-run 证据

`pilot/run_pilot.py` 跑通：

```text
project_id:     P-036f74d6
task_id:        T-6c45d7db
execution_id:   E-23c414e4
workspace_id:   W-7c310822
base_sha:       10c7af3e669ab1af8f1a6fbe02181c3814e3dcf5
candidate_sha:  9a28df590a86f5ea771afa0c291458966396c5d7
changed:        ["src/app.py"]
evidence_exit:  0
candidate:      merged
task_status:    done
task_lifecycle: accepted
```

链路：

```text
project → task → reserve → start_execution
→ git worktree → generic CLI adapter 改 src/app.py
→ scope check → commit → Candidate
→ evidence（candidate_sha + exit 0）
→ candidate ready → approve → MergeAttempt → accepted
```

### 2.1 真实 DSH V1：5 个任务

```text
成功：add mul ×2、add sub ×1  → accepted / merged
拦截：越界写 other.txt ×1     → scope violation / needs_fix
拦截：无改动 ×1               → no change / needs_fix
usage 合计：input 2431 / output 498 / total 33155 / cached 31872
cost：¥0.0026（按官方价目表估算，usage 真）
```

细节：[REAL-DSH.md](REAL-DSH.md)、[BATCH-DSH.md](BATCH-DSH.md)。

### 2.2 真实 Codex（DeepSeek provider）V1 端到端

`pilot/run_real_codex.py`：

```text
provider:       deepseek（Codex CLI 0.167.0）
base_sha:       377e56c302c24e564df886eac38cd5aa8e2a0d29
candidate_sha:  ab58828058f916e69b8c845fab900684bab2d967
changed:        ["src/app.py"]
usage:          input 53408 / cached 51200 / output 1634
candidate:      merged
task_lifecycle: accepted
final_text:     "DONE"
cost:           ¥0.0098（估算）
```

不需要 OpenAI 账号：Codex CLI 走 DeepSeek 官方 Responses 接入。细节：[REAL-CODEX.md](REAL-CODEX.md)。

### 2.3 真实 3 agent 并发

`pilot/run_multi_agent.py`：

```text
DSH              → src/a.py → accepted（12.7s）
Codex-DeepSeek   → src/b.py → accepted（24.4s）
Codex-智谱       → src/c.py → accepted（23.9s）
总墙钟：26.8s（并发，不是三者之和）
```

三个 agent 同一项目、同一 base、各自 worktree + 文件预约；全部 `merged/accepted`。细节：[REAL-MULTI.md](REAL-MULTI.md)。

## 3. 这证明了什么

- V1 规则层能串起来：Run、文件预约、Worktree、Candidate、Evidence、Merge、lifecycle 都一致；
- candidate SHA 与 worktree HEAD 一致；
- scope check 只允许 `src/app.py`；
- 证据绑定 candidate SHA，exit 0 才能 ready；
- merge 成功才 `accepted`，`status` 同步为 `done`；
- `remove_workspace` 后 worktree 清理；
- **真实 DSH** 能在 worktree 里只改预约文件，usage 被真实记录；
- **真实 Codex（DeepSeek provider）** 也能走同一套 Candidate/Evidence/Merge 规则；
- **真实 3 agent 并发**：DSH + Codex-DeepSeek + Codex-智谱 同时跑，互不覆盖，全部 accepted。

## 4. 这没证明什么

- **cost 是估算，不是账单**：DSH ¥0.0026 + Codex ¥0.0098 + 3-agent ¥0.0099（智谱套餐另计）；
- **只有小代码任务**：没有真实小仓库的跨文件/依赖/冲突；
- **不是真实 Git 合入**：`finish_merge` 只更新规则层，没有改原仓库；
- **没有 Electron**：窗口/IPC/utilityProcess 未跑；
- **没有真实人类验收**：reviewer 是脚本调用；
- **Codex 不是 App Server JSON-RPC**：pilot 用 `codex exec`；App Server 冒烟本轮卡住，未完成。

## 5. 当前阻塞（需要外部输入）

DSH 5 个任务、Codex 1 个任务、3-agent 并发均已真跑；详见 [BATCH-DSH.md](BATCH-DSH.md)、[REAL-CODEX.md](REAL-CODEX.md)、[REAL-MULTI.md](REAL-MULTI.md)。

| 阻塞 | 需要什么 |
| --- | --- |
| DSH / Codex | ✅ 已跑通单任务与 3-agent 并发；下一步真实小仓库多样任务 |
| Electron | 28.3.3 与 45.2.3 都崩（0xC0000005）；需要可用桌面会话 |
| 真实 cost | 已按官方价目表估算；要账单级需核对 usage/cache 口径 |
| 试点仓库 | 默认用 `pilot` demo repo；真实小仓库路径可选 |

## 6. 下一步

1. 真实小仓库跑 5–10 个 DSH/Codex 任务；
2. Electron 真窗口/IPC；
3. cost 账单级核对；
4. 终版 `pilot/REPORT.md`。
