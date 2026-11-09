# B 内部试点 Runbook

> 适用：Windows 本机、单操作员、单仓库、Codex + DSH API Key 试点。
> 当前状态：V1 规则层可 dry-run；真实 adapter 与 Electron 运行尚未验收。

---

## 0. 前置

- Python 3.14；
- Git 2.30+；
- `p0-coordinator` 依赖：标准库 + PuLP/CBC（MILP）；
- 一个**小、非关键、可回滚**的 Git 仓库；
- 真实 Codex App Server / DSH SDK 的启动命令与凭据（M0 才需要）。

---

## 1. 先跑 dry-run（不依赖真实 agent）

```powershell
cd p0-coordinator
python ..\pilot\run_pilot.py
```

它会：

1. 在 `.tmp/pilot/repo` 建一个小 Git 仓库；
2. 创建项目/任务/文件预约；
3. 启动 execution；
4. 建真实 worktree；
5. 用通用 CLI adapter 改 `src/app.py`；
6. scope check；
7. commit → Candidate；
8. 记录绑定 SHA 的证据；
9. 人工批准 → MergeAttempt → accepted；
10. 删除 worktree。

**这是规则层 dry-run，不是 M0。** 真实 agent 接入要继续下面的 M0。

---

## 2. 建项目与任务（CLI）

```powershell
python worker_cli.py --db data/coordinator.db create-project --name pilot
python worker_cli.py --db data/coordinator.db create-task `
  --project P-xxxx --title "pilot task" --repo-id repo1 `
  --write-set src/app.py --checks '[{"type":"file_exists","path":"src/app.py"}]'
```

记下 `project_id` / `task_id`。

---

## 3. 预约文件

```powershell
python worker_cli.py --db data/coordinator.db reserve-files `
  --task T-xxxx --repo repo1 --paths src/app.py --kind file
```

---

## 4. 启动 Run 与 worktree

```powershell
python worker_cli.py --db data/coordinator.db start-execution `
  --task T-xxxx --agent codex --workspace C:\path\to\workspace

python worker_cli.py --db data/coordinator.db create-workspace `
  --task T-xxxx --repo repo1 --repo-path C:\path\to\repo --execution E-xxxx
```

记录 `execution_id` / `workspace_id`。

---

## 5. 启动 agent（M0 后）

当前 CLI 还没有 `adapter start`，用 Python：

```python
from adapters import CodexAdapter
a = CodexAdapter()
print(a.probe())
run = a.start("T-xxxx", r"C:\path\to\worktree")
print(run)
# ... 等它改文件 ...
print(a.stop(run["run_id"]))
```

DSH API Key 用 `DshAdapter()`，并设置 `DSH_SDK_CMD`。

**未配置时 probe 必须返回 unavailable；不要用交互式 DSH CLI 冒充 headless。**

---

## 6. scope + commit + Candidate

```powershell
python worker_cli.py --db data/coordinator.db workspace-changes --workspace W-xxxx
python worker_cli.py --db data/coordinator.db workspace-scope --workspace W-xxxx
python worker_cli.py --db data/coordinator.db commit-workspace --workspace W-xxxx --message "pilot change"
python worker_cli.py --db data/coordinator.db candidate-from-workspace --workspace W-xxxx
```

如果 `workspace-scope` 返回 `violations`，**停止**：改回范围或调整预约，不要强行合入。

---

## 7. 证据 + 候选就绪

```powershell
python worker_cli.py --db data/coordinator.db freeze-candidate --candidate C-xxxx
python worker_cli.py --db data/coordinator.db record-evidence `
  --candidate C-xxxx --type test --command "python -m unittest" `
  --exit-code 0 --sha <candidate_sha> --execution E-xxxx
python worker_cli.py --db data/coordinator.db mark-candidate-ready --candidate C-xxxx
```

`mark-candidate-ready` 要求：至少一条 `sha == candidate_sha` 且 `exit_code == 0` 的证据。

---

## 8. 人工验收 + 合入

```powershell
python worker_cli.py --db data/coordinator.db approve-candidate `
  --candidate C-xxxx --approver reviewer --note "lgtm"

python worker_cli.py --db data/coordinator.db start-merge `
  --candidate C-xxxx --base-sha <base_sha> --requested-by reviewer

python worker_cli.py --db data/coordinator.db finish-merge `
  --attempt M-xxxx --status succeeded --result-sha <candidate_sha>
```

验收标准：

- `candidate.base_sha` == 预期基线；
- `candidate.candidate_sha` == `git rev-parse HEAD`；
- 人工批准后 task lifecycle 进 `ready_to_merge`；
- merge 成功后才 `accepted`。

---

## 9. 失败路径必须测

1. **越界文件**：改一个未预约文件 → `workspace-scope` violations；
2. **测试失败**：evidence `exit_code=1` → `mark-candidate-ready` 拒绝；
3. **base 变化**：`start-merge` 的 base 不匹配 → 拒绝；
4. **进程被杀**：停止 agent 后 worktree 仍在，execution 不能假装成功；
5. **迟到事件**：旧 execution 的提交不能改新任务状态。

---

## 10. 每次试点记录

```text
日期：
任务：
agent：
base_sha / candidate_sha：
是否越界：
证据命令 / exit_code：
人工验收：
merge 结果：
人工干预次数：
问题：
```

试点结束写 `pilot/REPORT.md`。
