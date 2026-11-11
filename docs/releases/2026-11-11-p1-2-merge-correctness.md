# P1-2 合入正确性：2026-11-11

这是本地开发阶段的合入正确性补丁，基线为 `3ad1fd0`。本次只改合入路径、CLI/MCP/WebView2 参数和回归测试，没有改证据门禁、Agent 适配器或界面布局。

## 问题

旧实现的 `accepted` 只证明“临时集成分支 cherry-pick 成功”：

- `start_merge` 只比较调用方传入的 `expected_base_sha` 与 candidate base，不读目标分支实际 HEAD；
- `merge_candidate_git` 从 candidate base 建临时 worktree，不读目标 HEAD；
- `finish_merge` 信任调用方传入的 `succeeded` / `result_sha`，不检查目标 ref 是否真的前进；
- 因此候选生成后目标分支前进，仍可能被记录为 merged/accepted。

## 实现

1. **MergeAttempt 记录目标信息**：`merge_attempts` 新增 `repo_path`、`target_ref`、`target_before_sha`、`target_after_sha`；旧库启动时自动 `ALTER TABLE` 补列。
2. **start_merge 校验目标 HEAD**：解析本地分支（或 `HEAD`）的实际 SHA，必须等于 candidate `base_sha`；不等于时报 `candidate must be revalidated`，不创建 Attempt。候选无显式仓库时从最近 workspace 解析 `repo_path`。
3. **merge_candidate_git 从目标 HEAD 建集成分支**：创建 worktree 前再次核对目标 HEAD 与 `target_before_sha`；start 之后目标前进会报 `moved after start_merge`。成功后把 `result_sha` 写回 pending Attempt。
4. **finish_merge 校验并推进目标 ref**：
   - `succeeded` 必须带非空 `result_sha`，且与 Attempt 记录一致；
   - 目标 ref 只能从 `target_before_sha` fast-forward 到 `result_sha`；
   - 更新后重新读取目标 ref，必须等于 `result_sha`；目标被外部推进、结果不是基线后代、结果为空或未变化，都标记 failed；
   - 失败时 Candidate 保持 approved、任务退回 `needs_fix`，不会记录 accepted。
5. **CLI / MCP / WebView2**：`start-merge`、`merge-candidate-git` 增加 `--repo-path` / `--target-ref`（默认 `HEAD`）；MCP 的 `start_merge` 暴露同名字段；WebView2 合入默认目标为当前 HEAD，可通过 payload `targetRef` 覆盖。

## 回归测试

`test_v1_git_merge.py` 使用真实临时 Git 仓库，覆盖 8 条路径：

- 正常合入：目标 ref 从 base 前进到 result，Candidate merged、任务 accepted；
- 检查失败：worktree/branch 回滚，目标 ref 不动；
- 候选生成后目标分支前进：`start_merge` 拒绝；
- start 之后目标分支前进：`merge_candidate_git` 拒绝；
- 集成完成、finish 前目标分支再次前进：`finish_merge` 返回 failed；
- `finish_merge` 收到错误 `result_sha`：返回 failed；
- `finish_merge` 收到空 `result_sha`：返回 failed；
- 合并结果等于目标基线（无变化）：返回 failed。

`test_v1_core.py` 的合成合入用例已改为只验证到 `ready_to_merge`，真实合入统一由 `test_v1_git_merge.py` 覆盖；另保留 `start_merge` 无目标仓库时拒绝的测试。

## 验证

- Python 全量测试：**163 tests OK**（含 9 项 MILP；Python 3.14.4 + PuLP 3.3.1）。
- `test_codex_appserver` 的超时用例从 0.2s 放宽到 1.5s：慢机器上 Python 子进程可能还没开始读 stdin，原值会误报；“超时后不重发”的断言不变。
- `git diff --check`：通过。
- 未调用真实付费模型、pilot 或 GUI。

## 仍未完成

- P1-4 界面响应（Python 调用异步化、管道退出/取消）；
- P2-1 独立克隆（避免直接操作用户源仓库的 Git 元数据）；
- 真实 Agent 审查生产者；
- 统一运行时与正式 React/Electron 界面。
