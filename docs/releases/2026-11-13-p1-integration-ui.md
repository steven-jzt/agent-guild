# 2026-11-13：P1-2 统一与 P1-4 界面响应

这是开发阶段更新，不是完整 V1 产品发布。

基于远端 `e81b511`，整合 Codex 本地 `3e7fccc` 的已验证机制；未覆盖同学的目标仓库/分支接口。提交分为 P1-2 与 P1-4 两个可独立审查的变更，保留已有历史。

## P1-2：统一后的合入契约

1. `start_merge` 解析真实仓库与目标 HEAD。显式 `repo_path` 优先，否则从候选工作区推导；目标支持 `HEAD` 或本地分支，拒绝 detached HEAD、tag 和远程跟踪分支。当前 HEAD 必须等于冻结候选的 base。
2. `merge_candidate_git` 只准备完整 `base..candidate` 快照及检查结果，**不会推进目标分支**；覆盖候选全部提交，不只取最后一个提交。单个 attempt 原子认领，阻止重复执行；检查不能改变被审查的跟踪文件、提交或快照。
3. `finish_merge(..., succeeded, SHA)` 才确认合入：校验保存的结果、候选与证据，再以显式旧 SHA 租约更新本地目标；同时更新已检出的干净工作区。之后重新读取真实 ref，才记 merged/accepted。CLI `--finish` 仍一并完成准备和确认。
4. 保留 `--repo-path/--target-ref`、MCP 参数与 WebView2 `targetRef`，并兼容 CLI `--target-branch` 别名。`target_after_sha` 仍记录已确认的目标结果；pending 的 `result_sha` 是准备结果，不表示已合入。
5. 追加候选 SHA、Git 结果 SHA 和启动认领标记；旧字段/历史保留，不为旧记录伪造新凭据。手填 SHA 无法放行。目标已变化、检查失败、脏工作区或错误结果均不记成功。

更新只指向显式的本地仓库，不递归推送子模块，不跟随 tags，不修改仓库持久 Git 配置。Git 和 SQLite 不在同一事务；目标已更新、后续记录失败时保存 unknown，可重启后核对真实目标与准备凭据恢复。无法确认时不接受，也不盲目回滚目标。

## P1-4：界面和进程生命周期

- WebView2 消息异步处理；请求进程和 JSON 解析不占用 UI 等待。慢请求期间窗口计时器仍能运行，其他请求可独立返回。
- stdout/stderr 同时读取；单路输出设上限，输出超限、畸形 JSON、非零退出和响应序列化超限都返回错误，避免管道死锁或 Promise 一直悬挂。
- 每个请求由现有 Windows JobHost 拥有进程树；取消、超时、窗口关闭或宿主退出会终止本地进程树。进程/管道关闭无法确认时报告 unknown。
- UI 运行中保留“停止当前请求”；刷新不会解除重复提交保护；任务/候选分别由前端和 native 层阻止并发重复操作。请求 ID 与取消请求 ID 分离，不会把“收到停止请求”当成原业务完成。
- 页面变更取消旧请求，旧页面结果不投递给新页面。只有 confirmed merge_attempt=succeeded 才显示“已合入”。
- GUI、CLI、JobHost 二进制已按新源码编译，继续沿用仓库预编译分发方式；SDK DLL 未升级。默认 Python 为 `py.exe -3`，亦支持 `PYTHON` 可执行文件覆盖，子进程管道统一 UTF-8。

## 合并前补充修复

合并前重跑发现：取消恰好发生在 JobHost 创建暂停子进程与单独绑定 Job Object 之间，会留下暂停的孤儿进程。新增确定性回归在原实现上失败；现改为宿主先加入自有 Job Object，再创建子进程，使子进程从创建时就继承归属。Job 句柄不传给子进程，由 Windows 在宿主退出时关闭，确保正常退出码和子孙清理。已更新分发二进制。

## 验证

| 范围 | 结果 |
| --- | --- |
| Python 核心 13 模块，含真实 Git 与 C# 进程回归 | 173 项通过，0 失败 |
| 其中真实 Git 合入回归 | 17 项 |
| 其中 C# 进程/管道回归 | 10 项：双管道大输出、上限、错误退出、参数/Unicode、并发、取消、超时、宿主退出、子孙持管道退出、创建子进程瞬间杀宿主 |
| 新源码构建的真实 WebView2 隐藏窗口 | 3 项通过：慢 worker 期间计时器继续、取消往返、序列化超限明确拒绝 |
| JavaScript 桥接与界面逻辑 | 4 项通过 |
| 原 TypeScript 套件及类型检查 | 19 项通过；typecheck 通过 |
| 原 P1-1 独立门禁/迁移回归 | 8 项通过 |
| 回归有效性 | 旧二进制的 stderr 洪流死锁已复现；新二进制相同探针返回成功；临时反转合入成功判断使回归失败，恢复原始字节后通过 |

所有场景使用临时仓库、临时数据库或受控离线 worker。未调用真实付费模型，未操作用户业务仓库。本机 Python 3.13 无 PuLP，9 项 MILP 未运行；11 月 11 日其他环境的“163 项含 MILP”与本轮计数不可直接比较。

```powershell
# p0-coordinator/ 下
py -3.13 -X utf8 -m unittest test_coordinator test_estimate test_verifier test_v1_core test_v1_worktree test_v1_lifecycle test_v1_git_merge test_adapters test_or_router test_semantic test_v1_evidence_gate test_codex_appserver test_webview2_process -q
py -3.13 -X utf8 -m unittest test_webview2_ui -q
node --test webview2/test-bridge.cjs
# agent-guild/ 下
npm.cmd test
npm.cmd run typecheck
```

## 停止和恢复的实际边界

停止当前请求只确认本地进程树生命周期；不代表远端模型任务已撤销，也不代表已修改的文件或 Git 更新已回滚。不会自动把业务任务标成成功、取消或可重跑。数据库可能仍保留 running Execution 或 pending MergeAttempt。

操作员先确认本地进程退出，再查看任务工作区、候选、Execution 和目标 ref。仅对仍为 running、且确认属于这次中断的 Execution 显式收尾；已经完成的记录不改写：

```powershell
python worker_cli.py --db DB executions --task TASK
python worker_cli.py --db DB finish-execution --execution EXECUTION --status interrupted --reason "本地请求已停止，工作区已核对"
```

合入若已准备但未更新目标，可以核对后结束失败并重新验证；若目标已更新、记录 pending/unknown，使用保存的 `git_result_sha` 对原 attempt 调用 `finish-merge --status succeeded --result-sha SHA`，只有真实目标和凭据一致才会完成。目标又变化则继续阻止，需人工核对，禁止自动再次执行。

P2-1 独立克隆、真实 Agent 审查生产者、完整业务取消/恢复界面、统一运行时和真实模型全流程验收仍未完成。下一步建议先做独立克隆，保护用户源仓库的 Git 元数据。

## 协作者同步提示词

```text
项目：https://github.com/steven-jzt/agent-guild
本轮分支：codex/p1-2-p1-4-integration，基于 main 的 e81b511。
P1-2 提交 fa2af5a；P1-4 提交 c643b0a；后续文档提交包含验证与清单。

合并前另补 JobHost 子进程创建瞬间取消竞态，避免暂停孤儿；请同步最新提交，不只取前三个提交。
本轮由 Codex 完成，请先拉取本分支、阅读本说明，再向我简要复述理解，不要重复开发。重点核对：保留 repo_path/target_ref 与准备/确认两阶段接口；候选包含全部提交；真实目标更新、凭据、并发保护和 unknown 恢复；WebView2 异步双管道、停止本地请求与重复提交保护。

停止请求不等于业务自动取消，数据库可能需核对收尾；不要自动重跑或补假审查证据。验证记录为核心 173、真实窗口 3、TS 19、桥接逻辑 4，类型检查及原门禁独立回归通过；MILP 本轮未跑。保留原工作区，先汇报差异和理解，不擅自合并、推送或开展下一批改动。
```
