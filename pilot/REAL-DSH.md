# 真实 DSH V1 端到端（2026-11-08）

## 1. 结果

`pilot/run_real_dsh.py` 跑通：

```json
{
  "real_dsh": true,
  "base_sha": "87e1ea13ab862927fd41076abb42d19df2c3cdd4",
  "candidate_sha": "713b65aaf677186c59a383109fee148f5fcb5d27",
  "changed": ["src/app.py"],
  "usage": {
    "input_tokens": 553,
    "output_tokens": 134,
    "total_tokens": 6847,
    "cached_input_tokens": 6656,
    "cache_write_tokens": 0
  },
  "events": 24,
  "candidate_status": "merged",
  "task_status": "done",
  "task_lifecycle": "accepted",
  "final_text": "Only `src/app.py` was modified (function added), no commit made.\n\nDONE"
}
```

## 2. 链路

```text
create project / task（write_set=src/app.py）
→ reserve_files
→ start_execution
→ create_workspace（真实 git worktree）
→ DshAdapter.start(prompt=...)
→ dsh --profile headless --json "<prompt>"
→ 24 个 NDJSON 事件（含 usage）
→ workspace-scope：只有 src/app.py
→ commit_workspace
→ Candidate + freeze
→ evidence（candidate_sha + exit 0）
→ mark_candidate_ready
→ approve_candidate
→ start_merge / finish_merge
→ task accepted / status done
```

## 3. 这次真实验证了什么

- 真实 DSH headless 能在指定 worktree 里修改文件；
- DSH 只改了预约范围 `src/app.py`，没有越界；
- NDJSON usage 被 `extract_usage` 提取，写入 `runs`（input 553 / output 134 / total 6847 / cached 6656）；
- Candidate SHA 与 worktree HEAD 一致；
- evidence 绑定 candidate SHA 且 exit 0；
- 人工批准 → merge → `accepted`；
- `remove_workspace` 清理 worktree。

## 4. 这次没证明什么

- **cost_usd 仍是 0**：usage 是真的，但没有配置 DeepSeek 单价，所以没有算钱；
- 只跑了 1 个成功任务，没有跑 5–10 个试点任务；
- 没有跑失败/越界/迟到事件的真实 agent 路径；
- Codex 未登录、Electron 未真窗口；
- `finish_merge` 仍是规则层合入，不改原仓库。

## 5. 复现

```powershell
cd p0-coordinator
$env:DEEPSEEK_API_KEY="sk-..."
python ..\pilot\run_real_dsh.py
```

## 6. 下一步

1. 用同一脚本跑 5–10 个任务（成功/失败/越界）；
2. 配置单价，把 usage 换成真实 cost；
3. Codex 登录后跑 `CodexExecAdapter`；
4. Electron 真窗口；
5. 终版 `pilot/REPORT.md`。
