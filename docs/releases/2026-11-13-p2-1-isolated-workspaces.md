# 第一轮交付：独立工作区与合入适配

日期：2026-11-13（Asia/Shanghai）。基线 main `746d3a7`；分支 `codex/p2-1-isolated-workspaces`。

## 行为与架构

新工作区使用 `clone --no-local --no-hardlinks --no-checkout`，再检出任务分支；移除 origin，不共享对象库或 alternates。默认位置是 `%LOCALAPPDATA%/AgentGuild/workspaces/W-ID`，仍支持显式 `path`，但拒绝与源仓库嵌套。只复制提交内容；源仓库有未提交修改时返回 warnings。含 submodule 的基线明确拒绝，暂不静默交付不完整工作区。

`repo_path` 继续代表源仓库；新增 `storage_kind`、`managed_repo_path`。数据库以追加列方式升级，旧行标为 `legacy_worktree`，不自动修改其目录。新候选在 meta 中绑定 `workspace_id`，语义分析与合入跟随该绑定；没有绑定的旧候选只在工作区唯一时推导，歧义时拒绝。

执行、快照和合入准备在独立副本内完成。MergeAttempt 新增 `result_repo_path` 保存结果所在仓库；`finish_merge` 从该仓库向显式本地源路径发布结果，继续校验旧目标 SHA、候选、审批和证据。未确认前源仓库的 refs/index/config/worktree 列表和结果对象不变；确认后才更新指定分支。不推送 GitHub，不修改源仓库持久配置。

所有权凭据保存在克隆旁；创建失败仅清理有匹配凭据的规范化目录。删除时拒绝运行中执行、未验收候选和 pending/unknown 合入；缺失或错误凭据不删除，不把失败清理记为 removed。`force=False` 保留未提交修改。旧 worktree 保留原删除接口，也受业务记录保护。

## 验证

核心、真实 Git、独立工作区、进程与真实窗口回归共 185 项；TS 19 项、桥接 4 项及 typecheck。独立工作区新增 8 项，真实 Git 增加准备阶段源元数据/对象不变的测试。具体运行结果以 PR 验证记录为准。

```powershell
# p0-coordinator 下；原核心模块保持执行，另外加入以下模块
py -3.13 -X utf8 -m unittest test_managed_workspace test_v1_git_merge test_v1_worktree test_semantic -v
# agent-guild 下
npm.cmd test
npm.cmd run typecheck
```

未调用真实模型，未改用户业务仓库；PuLP 不可用，9 项 MILP 未运行。仓库隔离不等于 OS 沙箱；旧 worktree 仍是旧模式。硬退出可能留下有所有权凭据但未入库的目录，本轮不自动删除这类目录。第二轮继续补停止后的核对与恢复。

## 协作者同步提示词

```text
项目：https://github.com/steven-jzt/agent-guild
请先阅读 main 与 codex/p2-1-isolated-workspaces 的差异，以及本说明，再向我简要复述你的理解，暂不改代码。
本轮把新工作区改为独立克隆，保留 repo_path 为源路径；候选绑定具体工作区，合入准备留在副本，finish_merge 才发布到源分支。检查源 Git 元数据不变、完整候选合入、并发目标变化、旧 worktree 兼容，以及有未决记录时拒绝删除。
不要自动迁移旧目录、补假审查证据、调用付费模型或直接推 main。真实 Agent 审查和运行时统一尚未实现；停止后的恢复是下一轮任务。先汇报差异和理解，再等待分工。
```
