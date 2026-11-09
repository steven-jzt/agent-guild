# Live Demo：3 个真实 agent 通过 Coordinator 协作

> 2026-11-08。不是模拟 worker，是 3 个真实 DSH subagent 通过 `worker_cli.py` 领取任务、写文件、提交 run。

## 1. 设置

- 项目：`live-demo`（`P-bcb70271`）
- 3 个任务，写集互不重叠：
  - `Part A` → `live-demo/part-a.md`
  - `Part B` → `live-demo/part-b.md`
  - `Part C` → `live-demo/part-c.md`
- 每个 worker 的流程：
  1. `worker_cli.py claim --worker agent-X --task <id> --ttl 300`
  2. 用 write 工具写自己的文件（两行：`# Part X` + 一句含 `coordination` 的英文）
  3. `worker_cli.py commit ... --duration 5 --quality 1.0 --diff-lines 3 --files-changed 1`
  4. 回报

## 2. 结果

| 任务 | worker | fencing token | 文件 | commit |
| --- | --- | ---: | --- | --- |
| Part A | agent-a | 2 | `part-a.md` | done |
| Part B | agent-b | 3 | `part-b.md` | done |
| Part C | agent-c | 1 | `part-c.md` | done |

`verify.py` 结果：

```json
{
  "all_done": true,
  "all_files_ok": true,
  "runs": 3,
  "run_quality": [1.0, 1.0, 1.0],
  "metrics": {
    "tasks_by_status": {"done": 3},
    "active_leases": 0,
    "events": 13,
    "runs": 3,
    "total_cost_usd": 0.0,
    "avg_quality": 1.0
  }
}
```

`estimate.py` 也读到了 3 条真实 run（worker/kind 都是 `docs`）。

## 3. 真实发现：PowerShell 会吞掉 JSON 参数

3 个 worker 都踩了同一个坑：Windows PowerShell 5.1 在把参数传给 native command 时，会剥掉 `--run '{"duration_s": 5, ...}'` 里的双引号，CLI 收到的是 `{duration_s: 5}`，`json.loads` 失败。

绕法：

- `--run '{\"duration_s\": 5, \"quality\": 1.0}'`（反斜杠转义）
- 或 `--%` 停止解析
- 或干脆别用 JSON

**修复**：`worker_cli.py commit` 增加简单 flags：

```powershell
--duration 5 --quality 1.0 --diff-lines 3 --files-changed 1
```

这比让每个 agent 自己转义 JSON 可靠得多。**工具接口要按 agent 的实际 shell 环境设计，不能按理想 shell 设计。**

## 4. 这次 live demo 证明了什么

- 真实 agent 能通过 CLI 完成 `claim → 执行 → commit`；
- 任务板、租约、fencing、runs、metrics 在真实调用下一致；
- 3 个 worker 并发执行，没有互相覆盖（写集不重叠）；
- 真实 agent 会踩环境坑，而且会自己写绕法——但这些绕法应该收敛到工具里。

## 5. 没证明什么

- **成本/token 是 0**：这次没有接真实 token 计量，`cost_usd/input_tokens/output_tokens` 都是占位；
- **quality 是自报 1.0**：`verify.py` 只检查了文件格式；真实质量需要独立 verifier。验证门现在已实现（`verification_demo.py`），但这次 live demo 没启用。
- **没有并发冲突**：3 个任务写集不重叠；冲突路径由 `demo.py`/`test_coordinator.py` 覆盖；
- **没有走审批**：项目没开 `require_approval`；审批由 `approval_demo.py` 覆盖。

## 6. 复现

```powershell
cd p0-coordinator
python live-demo/setup.py          # 重置 live.db 并创建 3 个任务
# 然后让 3 个 worker 按 brief.md 执行（或手动跑 CLI）
python live-demo/verify.py
```
