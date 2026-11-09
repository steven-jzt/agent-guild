# 真实 DSH 批量试点（2026-11-08）

## 1. 汇总

已跑 **5 个真实 DSH 任务**：

| # | 任务 | 结果 | changed | usage（in/out/total/cached） |
| --- | --- | --- | --- | --- |
| 1 | add mul | ✅ accepted / merged | `src/app.py` | 553 / 134 / 6847 / 6656 |
| 2 | add sub | ✅ accepted / merged | `src/app.py` | 547 / 139 / 6628 / 6400 |
| 3 | 越界建 `other.txt` | ✅ blocked / needs_fix | `other.txt` | 543 / 88 / 6458 / 6272 |
| 4 | 无改动 | ✅ blocked / needs_fix | — | 527 / 3 / 6290 / 5760 |
| 5 | add mul（复跑） | ✅ accepted / merged | `src/app.py` | 261 / 134 / 6932 / 6784 |

**门禁结果：**

- 成功任务 3/3 进入 `accepted`；
- 越界任务 1/1 被 `workspace-scope` 拦截，`other.txt` 列为 violation，task → `needs_fix`；
- 无改动任务 1/1 被拦为“无改动”，task → `needs_fix`；
- 5 个任务都没有手工改 DB。

**usage 合计：**

```text
input:  2431
output:  498
total: 33155
cached: 31872
cost:   0（usage 真，单价未配置）
```

## 2. 证据位置

- 单任务成功（#1/#5）：`pilot/REAL-DSH.md`、`pilot/run_real_dsh.py`
- 批量（#2–#4）：`pilot/run_dsh_batch.py`，输出见下方
- 批量 DB：`p0-coordinator/.tmp/pilot-batch/*/db.sqlite`（本地，不入共享包）

## 3. 批量输出（#2–#4）

```json
[
  {
    "case": "success_sub",
    "result": "accepted",
    "changed": ["src/app.py"],
    "violations": [],
    "usage": {"input_tokens": 547, "output_tokens": 139, "total_tokens": 6628, "cached_input_tokens": 6400},
    "lifecycle": "accepted",
    "status": "done"
  },
  {
    "case": "scope_other",
    "result": "blocked",
    "changed": ["other.txt"],
    "violations": ["other.txt"],
    "usage": {"input_tokens": 543, "output_tokens": 88, "total_tokens": 6458, "cached_input_tokens": 6272},
    "lifecycle": "needs_fix"
  },
  {
    "case": "no_change",
    "result": "blocked",
    "changed": [],
    "violations": [],
    "usage": {"input_tokens": 527, "output_tokens": 3, "total_tokens": 6290, "cached_input_tokens": 5760},
    "lifecycle": "needs_fix"
  }
]
```

## 4. 这次证明了什么

- 真实 DSH 能稳定完成同一类小任务（2 次 add mul、1 次 add sub）；
- 越界写会被 scope check 挡住，不会进入 Candidate；
- 无改动不会被当成成功；
- 真实 usage 被写入 runs；
- 失败任务进入 `needs_fix`，不是 `accepted`。

## 5. 没证明什么

- **cost 仍是 0**：token 真，价格未配；
- 没有真实 Git 合入（`finish_merge` 只更新规则层）；
- 只有 1 类代码任务，没有跨文件/大仓库；
- Codex 未登录；Electron 未真窗口；
- 没有真实人类 reviewer（脚本调用）。

## 6. 下一步

1. 配置单价 → 真实 cost；
2. Codex 登录 → 第二条 agent；
3. Electron 真窗口；
4. 真实小仓库 5–10 个任务；
5. 终版 `pilot/REPORT.md`。
