# 真实 Codex V1 端到端（2026-11-08）

## 1. 结果

`pilot/run_real_codex.py` 跑通：

```json
{
  "real_codex": true,
  "provider": "deepseek",
  "codex_home": "p0-coordinator/.tmp/codex-deepseek-home",
  "base_sha": "377e56c302c24e564df886eac38cd5aa8e2a0d29",
  "candidate_sha": "ab58828058f916e69b8c845fab900684bab2d967",
  "changed": ["src/app.py"],
  "usage": {
    "input_tokens": 53408,
    "output_tokens": 1634,
    "cached_input_tokens": 51200
  },
  "events": 22,
  "candidate_status": "merged",
  "task_status": "done",
  "task_lifecycle": "accepted",
  "final_text": "DONE"
}
```

## 2. 怎么接的

不使用 OpenAI 账号，走 DeepSeek 官方 Codex 接入：

- Codex CLI 0.167.0；
- 隔离 `CODEX_HOME = p0-coordinator/.tmp/codex-deepseek-home`；
- `model_provider = deepseek`；
- `wire_api = "responses"`；
- `model = deepseek-flash`；
- `DEEPSEEK_API_KEY` 由环境变量注入。

配置来源：[DeepSeek 官方 Codex 文档](https://api-docs.deepseek.com/quick_start/agent_integrations/codex)。

## 3. 链路

```text
create project / task（write_set=src/app.py）
→ reserve_files
→ start_execution
→ create_workspace（真实 git worktree）
→ CodexExecAdapter.start(prompt)
→ codex exec --json --sandbox danger-full-access "<prompt>"
→ 22 个 JSONL 事件
→ workspace-scope：只有 src/app.py
→ commit → Candidate → evidence（exit 0）
→ approve → MergeAttempt → accepted
```

## 4. 这次证明了什么

- Codex CLI 可以在**没有 OpenAI 账号**的情况下，用 DeepSeek provider 真实执行；
- Codex 只改预约文件 `src/app.py`，没有越界；
- Codex JSONL 的 `input_tokens/cached_input_tokens/output_tokens` 被提取并写入 runs；
- Candidate/Evidence/Merge/lifecycle 与 DSH 走同一套规则；
- `danger-full-access` 是必要的，否则 Codex 在默认 sandbox 下不会改文件（第一次跑就是 0 改动）。

## 5. 没证明什么

- 不是 OpenAI 官方模型/账号；
- 不是 Codex App Server 的 JSON-RPC 协议，而是 `codex exec`；
- 只有 1 个 Codex 任务；
- `finish_merge` 仍是规则层合入，不改原仓库；
- cost 需按 DeepSeek 官方价目表估算，不是账单。

## 6. 复现

```powershell
cd p0-coordinator
$env:DEEPSEEK_API_KEY="sk-..."
$env:CODEX_HOME="C:\Users\steve\Desktop\探索区\p0-coordinator\.tmp\codex-deepseek-home"
python ..\pilot\run_real_codex.py
```

## 7. 成本

见 [COST.md](COST.md)：按 DeepSeek 官方价目表，本次 Codex 约 **¥0.0098**（off-peak）。
