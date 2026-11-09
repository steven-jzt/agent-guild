# 真实 Codex App Server V1 端到端（2026-11-08）

## 1. 结果

`pilot/run_real_appserver.py` 跑通：

```json
{
  "real_appserver": true,
  "candidate_sha": "074cfc6d48bc16fe856f5483f58768a12e550ad7",
  "integration_sha": "193f165133cb2975743de05c28ee4571c34e0b0a",
  "usage": {
    "input_tokens": 50542,
    "output_tokens": 1185,
    "total_tokens": 51727,
    "cached_input_tokens": 49280,
    "cache_write_tokens": 0,
    "reasoning_tokens": 141
  },
  "events": 221,
  "candidate_status": "merged",
  "merge_status": "succeeded",
  "task_lifecycle": "accepted",
  "task_status": "done"
}
```

## 2. 链路

```text
start_execution
→ create_workspace（真实 git worktree）
→ CodexAppServerAdapter.start(prompt)
   → initialize
   → thread/start
   → turn/start
→ 等 turn/completed（221 个 JSON-RPC 事件）
→ scope check（只改 src/app.py）
→ commit → Candidate
→ evidence → approve
→ start_merge
→ merge_candidate_git() 真实 cherry-pick
→ finish_merge(result_sha)
→ accepted
```

## 3. 这次证明了什么

- **Codex App Server 有真实 adapter**：`CodexAppServerAdapter`；
- App Server 能完成 initialize/thread/turn，并真实改文件；
- usage 能从 `thread/tokenUsage/updated` 提取（input 50542 / cached 49280 / output 1185）；
- App Server 产物能走同一套 Candidate/Evidence/Merge；
- `merge_candidate_git()` 真实 cherry-pick 成功，integration SHA 与 candidate SHA 不同；
- 全程不需要 OpenAI 账号（DeepSeek provider）。

## 4. 没证明什么

- 没有 Electron UI；
- 没有真实人类 reviewer；
- 没有 turn/interrupt、审批请求处理；
- 没有多 thread 并发；
- 221 个事件里文本提取还没做完整（主要靠文件/scope 验证）。

## 5. 复现

```powershell
cd p0-coordinator
$env:DEEPSEEK_API_KEY="sk-..."
$env:CODEX_HOME="...\.tmp\codex-deepseek-home"
python ..\pilot\run_real_appserver.py
```
