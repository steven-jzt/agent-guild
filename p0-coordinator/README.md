# p0-coordinator：多 agent 协调内核（P0 + P1/P2 初步）

SQLite + MCP + CLI 的协调器：任务卡、写集、依赖、原子 claim、TTL 租约、fencing token、事件日志；外加 runs 遥测、估计器、MILP 2D/3D Pareto、独立验证、N-of-M 审批（TTL）、预算止损、计划回写/预留。

协调器纯 Python 标准库；MILP 计划器用 PuLP/CBC（本机已装）；Worktree 功能需要 Git 2.30+。不依赖网络。

> 最新状态汇总：[../docs/现状/当前状态-2026-11-08.md](../docs/现状/当前状态-2026-11-08.md)。
> 内部试点计划：[../pilot/PLAN.md](../pilot/PLAN.md)。

---

## 1. 它保证什么

- **原子 claim**：`BEGIN IMMEDIATE`，同一任务不会被两个 worker 同时领走。
- **写集互斥**：两个任务的 `write_set` 有交集时，后一个 claim 会被拒绝，直到前一个租约释放/过期。
- **依赖门控**：`deps` 里只要有一个任务不是 `done`，就不能 claim。
- **TTL 租约**：过期自动回收（MCP reaper 或 claim 时惰性回收），任务回到 `ready`。
- **Fencing token**：每次 claim 生成单调递增 token；旧持有者提交会被拒绝。
- **事件日志**：`project_created / task_created / task_claimed / lease_* / task_committed / run_recorded / verification_* / approval_* / plan_applied / reservation_expired`。
- **运行遥测**：`runs` 表记录 token/成本/质量/diff；`estimate.py` 聚合 worker/kind 先验。
- **人工审批门**：项目可设 `require_approval`；commit 后进入 `review`，`approve` 通过、`reject` 退回 `ready`。`approvals_required=N` 时是 N-of-M：N 个不同 approver 各签一次才 done。审批有 TTL，过期自动 `expired`，可用 `request_approvals` 重新发起。
- **独立验证门**：项目可设 `require_verification`；commit 后进入 `verification`，`record_verification(passed=True)` 才继续（可再进 `review`），失败退回 `ready`。
- **自报 vs 验证**：`runs.quality` 是 worker 自报；`verifications.score` 是独立 verifier 打分，`metrics.avg_verification_score` 单独统计。
- **后台回收**：MCP server 内 daemon 线程按 `REAPER_INTERVAL`（默认 15s）回收过期租约。
- **计划回写 + 预留**：MILP 的 assignment 可写入任务板（planned_worker/start/end），并为每个任务建 reservation；别的 worker 抢会被拒（`reserved_for`），`claim_planned` 消费自己的预留。
- **成本预算**：项目可设 `budget_usd`；`claim_task` 发现已花费（runs.cost_usd 累计）≥ 预算时返回 `budget_exceeded`。
- **V1 执行实体（Run）**：`executions` 表记录 agent/role/workspace/session/generation/status；同一任务同时只允许一个 author Run；`runs` 通过 `execution_id` 关联遥测。
- **V1 Task lifecycle**：`tasks.lifecycle` 走 Ready→Assigned→InProgress→AutoReview→HumanReview→ReadyToMerge→Accepted，另有 NeedsFix/Blocked/Cancelled；非法迁移会被拒绝。
- **repo/path 文件预约**：`file_reservations` 按 repo + Git 相对路径预约；父子目录冲突、Windows 大小写不敏感、全有或全无；可持有到显式释放。
- **write_set 与预约双重门**：`claim_task` / `start_execution` 同时检查 `write_set` 与 `file_reservations`；repo/path 预约会以 `file_reserved_by:<task>` 拒绝。
- **真实 Git 合入**：`merge_candidate_git()` 从 base 建集成分支、cherry-pick candidate、可选跑检查；失败自动 abort + 删除 worktree/branch；CLI：`merge-candidate-git --candidate ... --repo-path ... --worktree ... [--check ...] [--finish]`；测试见 `test_v1_git_merge.py`。
- **Adapter 合同**：`adapters.py` 提供 probe/start/stop；Codex App Server 用 `CODEX_APP_SERVER_CMD`；Codex exec 用 `codex exec --json --skip-git-repo-check`（已用 DeepSeek provider 真实跑通）；DSH 用 `dsh --profile headless --json`（需要 `DEEPSEEK_API_KEY`；已真实跑 5 个任务）；另有 Mock/通用命令适配器；`extract_usage` 可从 NDJSON 事件提取 token/cost。
- **桌面壳**：`electron/` 有 main + utilityProcess + IPC + 任务表；本机 Electron 17/22/28/45/46a 都崩 `0xC0000005`。`webview2/` 是 WebView2 + WinForms 替代壳，已跑通窗口 + 现有 renderer + Python RPC + Start Run/Approve/Merge。
- **可观测**：`metrics()` 返回任务状态、活跃租约、过期租约、事件数、runs、总成本、平均质量、待审批数、待验证数、验证数/平均验证分、活跃预留。

---

## 2. 文件

```text
p0-coordinator/
├─ coordinator.py       # 核心：schema + Coordinator 类
├─ semantic.py          # Python AST 语义变更提取（added/removed/signature/body）
├─ mcp_server.py        # MCP stdio server（JSON-RPC，按行分隔）
├─ demo.py              # 冲突 / 过期 / fencing / 依赖 的演示
├─ telemetry_demo.py    # 模拟运行并记录 runs，输出估计器 + 计划器
├─ estimate.py          # 从 runs 聚合 cost/quality/duration 先验
├─ plan.py              # 用先验做 predict-then-optimize（小规模枚举）
├─ plan_milp.py         # MILP 调度：依赖/容量/写集互斥/质量下限
├─ milp_demo.py         # MILP 演示（含 Pareto 表）
├─ approval_demo.py      # 审批门演示：review -> approve/reject
├─ multi_approval_demo.py # 2-of-2 多级审批演示
├─ approval_timeout_demo.py # 审批 TTL 过期 + 重新发起
├─ budget_demo.py        # 项目预算超支 -> claim 被拒
├─ v1_core_demo.py       # V1 切片：execution + 文件预约冲突
├─ candidate_demo.py     # V1 切片：Candidate/Evidence/MergeAttempt
├─ worktree_demo.py      # V1 切片：git worktree + 越界 diff + candidate
├─ verification_demo.py  # 验证门演示：verification -> failed/passed -> review -> done
├─ verifier.py           # 执行 checks：file_exists/file_contains/file_regex/command
├─ adapters.py           # Adapter 合同：Codex/DSH/通用命令/Mock
├─ adapter_cli.py        # adapter 探测 CLI
├─ electron/             # Electron 壳：main + utilityProcess + IPC + 只读任务表
├─ webview2/             # WebView2 壳：WinForms + 现有 renderer + Python RPC
├─ auto_verify_demo.py   # 自动验证演示：pass/fail 两条路径
├─ worker_cli.py         # Shell/CLI 入口：create/claim/commit/approve/verify/...
├─ smoke_cli.py          # CLI 冒烟测试
├─ smoke_mcp.py          # MCP 冒烟测试（启动 server 走一遍）
├─ test_coordinator.py   # 21 个协调器测试（含审批/预算/验证/并发）
├─ test_estimate.py      # 2 个估计器/计划器测试
├─ test_plan_milp.py     # 9 个 MILP/计划回写/Pareto（2D/3D）/预留测试
├─ test_verifier.py      # 10 个自动验证测试（command allowlist/timeout/output/env）
├─ test_v1_core.py       # 16 个 V1 切片测试（execution/预约/candidate/merge/claim 门）
├─ test_v1_worktree.py   # 5 个 Worktree 测试（真实 git + 越界 diff + pycache 忽略）
├─ test_v1_lifecycle.py  # 3 个 Task lifecycle 测试
├─ test_v1_git_merge.py  # 2 个真实 Git cherry-pick 合并测试
├─ test_adapters.py      # 10 个 Adapter 测试（Codex exec/App Server/DSH/usage）
├─ test_or_router.py     # 7 个 OR 路由测试（cost/time/quality/批量预算）
├─ test_semantic.py      # 6 个语义变更/能力标签测试
├─ live-demo/            # 3 个真实 agent 的端到端 demo（setup/verify/REPORT）
├─ live-demo/verified/   # 同上 + require_verification（独立 verifier 跑 checks）
├─ live-demo/governance/ # 同上 + 2-of-2 审批（alice + bob）
└─ data/                # 默认 SQLite 位置（运行时创建）
```

---

## 3. 运行

```powershell
cd p0-coordinator

# 单元测试
python -m unittest test_coordinator test_estimate test_plan_milp test_verifier test_v1_core test_v1_worktree test_v1_lifecycle test_v1_git_merge test_adapters test_codex_appserver test_or_router test_semantic -v

# 演示：W1 领 intro -> W2 冲突 -> 租约过期 -> W2 接管 -> W1 旧 token 被拒 -> 提交
python demo.py

# MCP 冒烟：initialize -> tools/list -> create -> claim -> commit -> estimates
python smoke_mcp.py

# 遥测 + 估计器：模拟 3 个 worker 完成 6 个任务，输出 cost/quality 先验
python telemetry_demo.py

# MILP 调度：依赖 + worker 容量 + 写集互斥 + 质量下限，含 Pareto 表
python milp_demo.py

# 审批门：commit -> review -> approve / reject
python approval_demo.py

# 多级审批：2-of-2，alice + bob 都签才 done
python multi_approval_demo.py

# 审批 TTL：过期 -> 重新发起 -> 2-of-2
python approval_timeout_demo.py

# 预算：$0.10 预算，花 $0.12 后下一单被拒
python budget_demo.py

# V1 切片：execution + repo/path 文件预约冲突
python v1_core_demo.py

# V1 切片：Candidate -> Evidence -> 人工批准 -> MergeAttempt
python candidate_demo.py

# V1 切片：git worktree + 越界 diff + candidate
python worktree_demo.py

# 验证门：commit -> verification -> failed/passed -> review -> done
python verification_demo.py

# 自动验证：真正执行 checks（file_exists/contains/regex）
python auto_verify_demo.py

# CLI 冒烟：只用 shell 命令跑完 create -> claim -> commit -> approve
python smoke_cli.py
```

期望：91 tests OK；demo 最后显示 `lease_expired` / `task_committed` 事件；`MCP OK: {...}`；`CLI OK: {...}`；telemetry 输出按 worker/kind 的均值与一个 plan；milp_demo 输出 cost/time 两种排期、Pareto 表、2D/3D Pareto front、回写计划、预留拦截与 claim_planned；approval_demo 显示 review → done / ready；multi_approval_demo 显示 2-of-2；approval_timeout_demo 显示 expired + 重新发起；budget_demo 显示 budget_exceeded；v1_core_demo 显示 execution + 文件预约冲突；candidate_demo 显示 candidate → merged + lifecycle accepted；worktree_demo 显示 worktree + 越界 diff；verification_demo 显示 verification → ready/review → done；auto_verify_demo 显示 file/command pass/fail；`adapter_cli.py probe` 显示 codex available、codex-exec/codex-appserver 在 DeepSeek CODEX_HOME 下 available、dsh 需 `DEEPSEEK_API_KEY`（已真跑）、mock available。

---

## 4. MCP 配置

`mcp_server.py` 是 stdio MCP server。通用配置示例：

```json
{
  "mcpServers": {
    "p0-coordinator": {
      "command": "python",
      "args": ["C:\\Users\\steve\\Desktop\\探索区\\p0-coordinator\\mcp_server.py"],
      "env": {
        "COORDINATOR_DB": "C:\\Users\\steve\\Desktop\\探索区\\p0-coordinator\\data\\coordinator.db"
      }
    }
  }
}
```

工具：

| 工具 | 作用 |
| --- | --- |
| `create_project` | 建项目 |
| `create_task` | 建任务（标题、write_set、deps、DoD、priority） |
| `list_tasks` | 列出任务 |
| `claim_task` | 原子领取，返回 task + lease + fencing_token |
| `renew_lease` | 续约 |
| `release_lease` | 主动释放 |
| `commit_task` | 提交（校验租约/token；可附 run 遥测） |
| `record_run` | 单独记录一次运行遥测 |
| `list_runs` | 查看运行记录 |
| `get_estimates` | 聚合 runs，输出 worker/kind 先验 |
| `plan_milp` | 用先验 + MILP 做带约束的调度 |
| `plan_front` | 扫 deadline，输出 cost–makespan Pareto 前沿 |
| `plan_front_3d` | 扫 floor + deadline，输出 cost–time–quality 3D 前沿 |
| `apply_plan` | 把 plan 回写任务板 |
| `get_plan` / `clear_plan` | 查看/清除计划 |
| `claim_planned` | 按计划领取 |
| `list_reservations` | 查看计划预留 |
| `renew_reservation` | 续期计划预留 |
| `record_verification` | 独立验证：passed → done/review，failed → ready |
| `list_verifications` | 查看验证记录 |
| `auto_verify` | 执行任务 checks 并自动记录结论（command 走 allowlist） |
| `start_execution` / `finish_execution` | 开始/结束一次 Run（V1 执行实体） |
| `reserve_files` / `release_files` | 预约/释放 repo/path 文件范围 |
| `list_executions` / `list_file_reservations` | 查看 Run / 文件预约 |
| `create_candidate` / `freeze_candidate` | 创建/冻结 Candidate |
| `record_evidence` / `mark_candidate_ready` | 证据绑定 candidate_sha；通过后进 awaiting_human |
| `approve_candidate` / `start_merge` / `finish_merge` | 人工批准 / 创建 MergeAttempt / 完成合入 |
| `create_workspace` / `list_workspaces` | 创建/查看 git worktree |
| `workspace_changes` / `workspace_scope` | 查看改动 / 检查是否越过 file_reservations |
| `commit_workspace` / `candidate_from_workspace` / `remove_workspace` | 提交 worktree / 生成 Candidate / 删除 worktree |
| `transition_task` | V1 Task lifecycle 迁移 |
| `approve_task` | 审批通过 review → done |
| `reject_task` | 审批退回 review → ready |
| `list_approvals` | 查看审批队列 |
| `request_approvals` | 重新发起审批（超时/作废后） |
| `get_budget` | 查看项目预算/已花费/剩余 |
| `get_events` | 事件流 |
| `get_metrics` | 统计 |
| `reap_expired` | 回收过期租约 |

## 4.1 Shell CLI（给不支持 MCP 的 agent）

```powershell
python worker_cli.py --db data/coordinator.db create-project --name demo --require-verification --approvals-required 2 --approval-ttl 600 --budget 1.0
python worker_cli.py --db data/coordinator.db create-task --project P-xxxx --title t --write-set doc#a --kind code \
    --checks '[{"type":"file_exists","path":"doc/a.md"},{"type":"file_contains","path":"doc/a.md","text":"coordination"}]'
python worker_cli.py --db data/coordinator.db claim --worker W1 --project P-xxxx --ttl 120
python worker_cli.py --db data/coordinator.db commit --task T-xxxx --worker W1 --token 1 \
    --cost 0.05 --quality 0.9 --duration 30 --diff-lines 10 --files-changed 1
python worker_cli.py --db data/coordinator.db auto-verify --task T-xxxx --root . --allow-command --allowed-commands python,pytest
python worker_cli.py --db data/coordinator.db approve --task T-xxxx --approver human
python worker_cli.py --db data/coordinator.db request-approvals --task T-xxxx --count 2 --ttl 600
```

命令：`create-project / projects / create-task / claim / renew / commit / release / approve / reject / request-approvals / renew-reservation / record-verification / list-verifications / auto-verify / start-execution / start-agent / recommend-agent / recommend-batch / semantic-changes / semantic-impact / semantic-ack / add-semantic-dep / tag-agent / agent-profile / finish-execution / reserve-files / release-files / executions / file-reservations / create-candidate / freeze-candidate / record-evidence / mark-candidate-ready / approve-candidate / reject-candidate / start-merge / finish-merge / merge-candidate-git / transition-task / candidates / evidence / merge-attempts / create-workspace / workspaces / workspace-changes / workspace-scope / commit-workspace / candidate-from-workspace / remove-workspace / adapters / status / budget / runs / events`。所有命令输出 JSON；失败 stderr + 退出码 1。

> PowerShell 5.1 会吞掉 `--run '{"..."}'` 里的双引号，优先用 `--cost/--quality/--duration/--diff-lines/--files-changed`。

---

## 5. 数据模型

```text
projects(id, name, require_approval, require_verification, approvals_required, approval_ttl_seconds, budget_usd, created_at)
tasks(id, project_id, title, description, kind, status, lifecycle, owner,
      write_set JSON, deps JSON, dod, checks JSON, priority,
      plan_id, planned_worker, planned_start_s, planned_end_s,
      created_at, updated_at)
leases(id, task_id, owner, fencing_token, status, expires_at, created_at, renewed_at)
runs(id, task_id, execution_id, worker, fencing_token, duration_s, input_tokens, output_tokens,
     cost_usd, quality, diff_lines, files_changed, status, meta, created_at)
executions(id, task_id, agent, role, workspace, session, status, generation,
           started_at, ended_at, exit_reason, meta)
file_reservations(id, project_id, repo_id, task_id, execution_id, path, path_key,
                  kind, status, created_at, released_at, release_reason)
candidates(id, task_id, repo_id, author_execution_id, base_sha, candidate_sha,
           status, created_at, frozen_at, approved_by, approved_at, merged_at, meta)
evidence(id, candidate_id, execution_id, type, command, cwd, exit_code,
         output_ref, sha, created_at)
merge_attempts(id, candidate_id, expected_base_sha, requested_by, status,
               result_sha, error, created_at, finished_at)
workspaces(id, task_id, execution_id, repo_id, repo_path, path, branch,
           base_sha, status, created_at, removed_at)
approvals(id, task_id, requested_by, requested_at, status, decided_by, decided_at, note, expires_at)
plans(id, project_id, created_at, mode, quality_floor, makespan_s, total_cost_usd, payload JSON)
reservations(id, task_id, worker, plan_id, status, expires_at, created_at)
verifications(id, task_id, verifier, passed, score, notes, created_at)
events(id, ts, type, task_id, actor, payload JSON)
counters(name, value)   -- fencing 全局单调计数
```

任务状态：`ready → claimed → done`；需要验证时 `claimed → verification → done/review`；需要审批时 `claimed → review → done/ready`；异常时 `claimed → ready`（释放/过期）。

---

## 6. 已知边界

- **单机 SQLite**：不是分布式锁；跨机要换 Postgres/etcd。
- **回收**：MCP server 有后台 reaper（默认 15s），但进程崩溃时仍靠 claim 时惰性回收。
- **审批是计数制**：N 个不同 approver + TTL 过期；没有角色权限、审批链顺序、自动升级；生产要接 HumanLayer。
- **无鉴权**：worker 名字只是标识，不是身份；生产要接 token/ACL。
- **验证器是外部的**：`auto_verify` 支持 `file_exists/file_contains/file_regex/command`；`command` 默认禁用，启用后只跑 allowlist 内的可执行文件，`shell=False`，有 timeout、输出上限、环境变量白名单，路径限制在 root 内。
- **无内容合并**：写集只保证互斥，不保证语义合并；`commit` 只记 result，不校验文档内容。
- **预算是硬止损**：只按已记录的 `runs.cost_usd` 累计，不预测未来花费；`budget_usd=None` 表示不限。
- **无优先级抢占**：高优先级任务不能抢走低优先级租约；P0 先不做。

---

## 7. 下一步

1. 审批升级：角色权限、审批链顺序、自动升级，接 HumanLayer。
2. 把 `write_set` 从字符串升级为 region/文件级（如 `doc.md#intro` 已够用）。
3. 真实 trace + 质量：verified live demo 已跑通；command check 已有 allowlist/timeout/output/env 限制；下一步接真实 token/cost。
4. 跨机时把 SQLite 换 Postgres，lease 用 `SELECT ... FOR UPDATE SKIP LOCKED`。
5. 计划器：2D/3D Pareto 已支持；下一步 CP-SAT / 更大规模实例，以及预留心跳与自动预分配。
6. 预算：已支持项目级硬止损；下一步做按 task 预算、花费预测与超支预检。
7. V1 Core：Run/预约/Candidate/Merge/Worktree/lifecycle/双重门已实现；DSH 已真实跑 5 个任务，Codex（DeepSeek provider）已真实跑 1 个任务；Electron 崩但 WebView2 壳已跑通。

---

## 8. Live demo（真实 agent）

`live-demo/` 记录了一次真实的 3 agent 端到端运行：

- 3 个 DSH subagent 各自 `claim` 一个任务，写自己的 Markdown 文件，再 `commit --duration 5 --quality 1.0 ...`；
- `verify.py` 检查 3 个文件格式、3 个任务 done、3 条 runs；
- 结果：[`live-demo/REPORT.md`](live-demo/REPORT.md)。

真实发现：Windows PowerShell 5.1 会吞掉 `--run '{"..."}'` 里的双引号，所以 CLI 加了简单 flags（`--duration/--quality/--diff-lines/--files-changed`）。**接口要按 agent 的实际 shell 设计。**

`live-demo/verified/` 是同一套流程 + `require_verification`：3 个真实 agent commit 后停在 `verification`，独立 verifier 跑 3 个 checks 全过才 done。第一次跑时 `root` 配错，3 个任务被正确判失败退回 `ready`；修正后 3/3 通过。记录：[`live-demo/verified/REPORT.md`](live-demo/verified/REPORT.md)。

`live-demo/governance/` 再把审批接上：verification 通过后进入 `review`，alice + bob 各签一次（2-of-2）才 done。记录：[`live-demo/governance/REPORT.md`](live-demo/governance/REPORT.md)。

```powershell
cd p0-coordinator
python live-demo\setup.py
# 让 3 个 worker 按 live-demo/brief.md 执行
python live-demo\verify.py
```

---

## 9. V1 Core Slice（2026-11-08）

按 V1.0.1 计划补的两块核心：

- `executions`：一次 Run 一条，带 agent/role/workspace/session/generation/status；同一任务同时只允许一个 author Run；结束状态区分 succeeded/failed/cancelled/interrupted；
- `runs.execution_id`：遥测记录关联到具体 Run；
- `file_reservations`：repo + Git 相对路径；父子目录冲突、大小写不敏感、全有或全无；持有到显式释放；
- `candidates` / `evidence` / `merge_attempts`：Candidate 冻结后进入验证批次；证据必须绑定 `candidate_sha` 并明确 `check_id`，当前批次内全部必需项（至少一项 test + 一项 review）有效通过才允许进入人工验收/批准；`start_merge` 校验 base SHA，`finish_merge` 成功才 merged（目标基线校验属于 P1-2，尚未修）；
- `workspaces`：真实 `git worktree add`，记录 repo/path/branch/base_sha；`workspace_changed_files` 对比 base + 未跟踪文件；`check_workspace_scope` 检查是否越过 `file_reservations`；`commit_workspace` 提交并返回 SHA；`candidate_from_workspace` 直接生成 Candidate；
- `tasks.lifecycle`：V1 状态机；`start_execution` → in_progress，`finish_execution(succeeded)` → auto_review，候选 ready → human_review，approve → ready_to_merge，merge succeeded → accepted；失败 → needs_fix。
- `write_set` 与 `file_reservations` 双重门：`claim_task` / `start_execution` 都检查 repo/path 预约；冲突返回 `file_reserved_by:<task>`。

还没验收：Electron 真实窗口（二进制已装，BrowserWindow 崩溃）；WebView2 壳已通过 `--smoke`；Codex App Server adapter 已接并跑通 V1 端到端。

### 9.1 Candidate 证据门禁（P1-1，2026-11-09）

- **必需项**：项目级 `required_checks`，默认 `platform-test(test)` + `agent-review(review)`，项目配置必须至少含一项 `test` 和一项 `review`；任务的 `required_checks` 只作为**追加项**，项目必需项始终保留，项目后续新增项也自动生效（任务不能删项或改 kind）。
- **类别规范**：证据的 `type` 必须与检查项 `kind` 一致（test/review/check）；普通测试记录不能冒充 Agent 审查。写入校验与门禁都会检查；`agent_runner` 给平台测试写 `type=test`。
- **验证批次**：`freeze_candidate` 自动开启批次，固化 candidate SHA、base SHA、必需项快照与 config hash；`begin_validation_batch` 可重开，旧批次标记 superseded，其证据与人工批准都不再参与新批次判定。
- **证据**：门禁证据必须带 `check_id`、绑定当前 candidate SHA、落在打开的批次内；`seq` 为批次内递增序号，判定取每个必需项最新一条（同一时间戳也按 `seq` 排序，不依赖时间）。
- **作废语义**：`void_evidence` 只作废单条记录；如果被作废的是该检查项的最新结果，则该项进入等待重新验证状态，**不会**回退到更早的成功记录，必须记录新的有效结果才能恢复。
- **判定**：缺失、失败（`exit_code != 0`）、错误 SHA、类别不匹配、最新结果作废、旧批次、必需项配置变化、批次基线变化，全部阻止；`mark_candidate_ready` 与 `approve_candidate` 共用同一门禁；进入 `awaiting_human` 之后新增有效失败结果会让后续批准再次被拒绝。
- **审批批次**：`candidate_approvals` 绑定验证批次；新批次必须重新完成整条审批链，旧批次的部分批准只保留审计历史、不再计数。
- **迁移**：旧库只新增列/表并把 `candidate_approvals` 重建为带 `batch_id` 的结构；重建全过程在显式事务中执行，任一步失败都会回滚并抛出错误，不会留下“新表已建、旧行留在 legacy 表”的半迁移状态。启动时若发现 `candidate_approvals_legacy` 残留，会先补齐缺失历史行，再逐字段核对（id、candidate_id、approver、role、note、created_at，且目标行 `batch_id` 必须为 NULL）；同 ID 内容不一致时明确报错、回滚并保留两表，不自动覆盖或选边。无法安全判定（legacy 与旧结构目标表并存）时明确报错阻塞。历史证据与批准不删除，但旧证据（无 batch/check_id）和旧批准（batch_id 为 NULL）都不能满足新批次，旧候选需 `begin_validation_batch` 后重新记录与审批。
- **CLI**：`create-project` / `create-task` 的 `--required-checks`，`record-evidence --check-id [--batch]`，另有 `begin-validation-batch`、`validation-batches`、`candidate-gate`；MCP 暴露同名工具。
- **尚未支持**：真实 Agent 审查生产者。现有 demo/pilot 没有真实 review 证据时，会在 `mark_candidate_ready` 停止并报 `missing=agent-review`；这是预期行为，不要补假 review。

```powershell
python v1_core_demo.py
python candidate_demo.py
python worktree_demo.py
python -m unittest test_v1_core test_v1_worktree test_v1_lifecycle test_v1_evidence_gate -v
```

---

## 10. Adapters 与桌面壳（2026-11-08）

- `adapters.py`：probe/start/stop 合同；
  - Codex App Server：`CODEX_APP_SERVER_CMD`；`CodexAppServerAdapter` 已跑通 V1 端到端；
  - Codex exec：`codex exec --json --skip-git-repo-check --sandbox danger-full-access`；已用 DeepSeek 官方 Responses 接入跑通；`CODEX_HOME` 指向 DeepSeek 配置时 probe 显示 `custom provider: deepseek`；
  - DSH：默认 `dsh --profile headless --json "<task>"`；需要 `DEEPSEEK_API_KEY`；已真实跑 5 个任务；
  - Mock：测试用；通用 `CommandAdapter` 可接任意 CLI。
- `adapter_cli.py probe`：codex available；codex-exec 在 DeepSeek CODEX_HOME 下 available；dsh 需 key（已真跑）；mock available。
- `electron/`：main + utilityProcess + IPC + 只读任务表；Electron **17/22/28/45/46a 都装过**；但本机 BrowserWindow 都崩溃（0xC0000005），**Electron 窗口未通过**。
- `webview2/`：WinForms + WebView2 + 现有 renderer + Python RPC；任务表 + Start Run + Candidates + Approve + Merge；`.\webview2\run.ps1 --smoke` 输出 `WEBVIEW2_SMOKE`。
- `electron/smoke_cli.js`：Python CLI 桥冒烟通过；`smoke_min.js` 最小窗口在当前环境崩溃。
- 真实 DSH e2e：`python ..\pilot\run_real_dsh.py`（需 key）；结果见 `pilot/REAL-DSH.md`。

### 10.1 Codex App Server 协议与审批（P1-3）

- 字段依据：本机 `codex-cli 0.164.0` 的 `app-server generate-json-schema`。`turn/completed` 为 `{threadId, turn{id,status,error,items}}`，`TurnStatus` 仅 `completed / interrupted / failed / inProgress`；`turn/start` 响应为 `{turn}`；`turn/interrupt` 参数为 `{threadId, turnId}`。
- 状态：只有 `completed` 算成功；`failed` / `interrupted` 明确失败；`inProgress`、缺失或无法识别的状态记为 `unknown`；超时也是 `unknown`，不重发、不判成功。
- 时序：通知先于 `turn/start` 应答到达也不会丢失；只接受当前 `threadId + turnId` 的完成事件，其他线程、旧轮次、迟到事件不改变结果。
- 取消与退出：`stop()` 发送 `turn/interrupt` 并尽量确认终态；进程提前退出记为 `closed`，不会当作成功。
- 取消确认：`confirmed=true` 仅表示已观察到当前 thread/turn 的终态；中断请求的成功应答不算终态，等待结束仍为 `unknown` 时保持 `confirmed=false`，即使随后已清理本地进程。
- 响应校验：请求 ID 只接受字符串或 int64 整数，拒绝布尔值、浮点数等；响应必须恰含 `result/error` 之一。本客户端使用的方法要求对象结果；错误必须含整数 `code` 和字符串 `message`。畸形响应明确报协议错误，不能默认为空结果。
- 服务端请求：带 id 的 method 与通知分开处理；审批请求默认拒绝（command/fileChange `decline`、legacy `denied`、permissions 空授权、MCP `decline`），未知请求返回 JSON-RPC `-32601`；可用 `approval_handler` 显式决定，但适配器绝不默认批准。
- 配置：默认 `sandbox=workspace-write`、`approvalPolicy=on-request`、`approvalsReviewer=user`；旧版写死的 `danger-full-access` / `never` 已移除。`CODEX_APP_SERVER_SANDBOX` 可显式覆盖 sandbox（`read-only/workspace-write/danger-full-access`），审批策略不可关闭。兼容影响：需要工作区外写入或网络的任务现在会触发审批；没有 handler 时默认拒绝，任务会失败而不是静默绕过。
- 测试：`test_codex_appserver.py` 配合离线桩 `codex_appserver_stub.py`，覆盖终态、早到、错配、迟到、超时、取消、进程退出、审批与未知请求；`test_adapters.py` 保留。

```powershell
python adapter_cli.py probe
node electron/smoke_cli.js
# WebView2 壳（替代 Electron）
.\webview2\run.ps1 --smoke
# Electron 版本（需去掉 ELECTRON_RUN_AS_NODE，且加 no-sandbox）
$env:ELECTRON_RUN_AS_NODE=$null
.\node_modules\electron\dist\electron.exe --no-sandbox --disable-gpu --version
```
