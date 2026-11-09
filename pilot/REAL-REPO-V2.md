# 真实仓库试点 V2（Phase 1）

日期：2026-11-09  
目标：在**多文件、带测试、非 demo** 的仓库上跑通：

```text
任务 → 预约 → worktree → agent → scope → Candidate
→ 检查 → Evidence → 人工批准 → 合入 integration
→ 合并后检查 → 独立测试 → accepted
```

## 结论

**核心链路通过。**

- 真实 agent（Codex-DeepSeek）一次修改 **2 个预约文件**；
- 检查脚本从失败变为通过；
- Candidate 绑定 SHA + evidence + author execution；
- 人工批准后真实 cherry-pick；
- 合并后检查通过；
- integration 里独立跑 4 个单元测试全绿；
- 负向路径（检查失败回滚）由单元测试覆盖。

未完成/限制：

- 审批人还是字符串，没有真实身份认证；
- 没有 push/PR；
- 检查脚本是试点自定义门禁，不是通用 CI；
- 真实仓库是试点模板，不是你的生产仓库。

## 1. 仓库

模板：`pilot/real-repo-template/`

```text
src/calc/__init__.py
src/calc/ops.py
tests/test_ops.py
check.py
pyproject.toml
README.md
```

初始化：

```powershell
python pilot/seed_real_repo.py
```

基线 commit：

```text
7204858f1641a51773d7c209bf8c908138e21282
```

基线检查**故意失败**：

```text
base_check_exit = 1
ImportError: cannot import name 'div' from 'src.calc.ops'
```

## 2. 任务与预约

```text
任务：T-42d29c0b
标题：Add mul and div with tests
写集：
  src/calc/ops.py
  tests/test_ops.py
检查：python check.py
workspace：W-c068ee49
```

任务声明 checks：

```json
[{"type": "command", "command": "python check.py"}]
```

## 3. Agent 执行

```text
agent: codex-deepseek
execution: E-d9a89250
duration: 30.8s
usage:
  input  71606
  cached 69120
  output 3021
changed:
  src/calc/ops.py
  tests/test_ops.py
candidate: C-2c0b3836
candidate_sha: 4bb45b7e384bf0bfac8eea6c09165a0bcaddf04f
author_execution_id: E-d9a89250
candidate_status: awaiting_human
check_exit: 0
check_output: REAL_REPO_CHECK_OK
```

diff：

```text
src/calc/ops.py   | 8 ++++++++
tests/test_ops.py | 8 ++++++-
2 files changed, 15 insertions(+), 1 deletion(-)
```

scoped changed 只有两个预约文件，没有越界。

## 4. 批准与合入

```text
approver: human
approved_at: 2026-11-09T11:42:11
merge_attempt: M-d23ebbaf
merge status: succeeded
result_sha: 96e5f737860a3068de7562bb9d01d766c21ef946
branch: guild/merge-c-2c0b3836-5fe4ff
```

合并后检查：

```text
check_exit: 0
check_output: REAL_REPO_CHECK_OK
```

## 5. integration 独立验证

```text
python check.py
REAL_REPO_CHECK_OK

python -m unittest discover -s tests -v
test_add ... ok
test_sub ... ok
test_mul ... ok
test_div ... ok
Ran 4 tests
OK
```

最终任务：

```text
status: done
lifecycle: accepted
candidate: merged
```

## 6. 负向验证

检查失败必须回滚，不允许半合入。

```powershell
python -m unittest test_v1_git_merge -v
```

结果：

```text
test_merge_check_failure_rolls_back ... ok
test_merge_success ... ok
Ran 2 tests
OK
```

## 7. UI 合并后检查

WebView2 的「合入」现在会把运行设置里的检查命令传给：

```text
worker_cli.py merge-candidate-git --check "<check>"
```

用 fresh candidate 验证：

```text
candidate: C-a47c2215
result_sha: 51358a57bb40a6757e3d0167fab98ba5a8695962
check_exit: 0
merge_attempt: succeeded
```

## 8. 复现命令

```powershell
cd C:\Users\steve\Desktop\探索区
python pilot\seed_real_repo.py

cd p0-coordinator
$env:DEEPSEEK_API_KEY="sk-..."
python worker_cli.py --db .tmp\real-repo\db.sqlite start-agent `
  --task T-42d29c0b `
  --agent codex-deepseek `
  --prompt "Implement mul(a, b) and div(a, b) in src/calc/ops.py. Add unit tests named test_mul and test_div in tests/test_ops.py. Keep add and sub unchanged. Do not modify check.py. Do not commit. Reply DONE." `
  --check "python check.py"

python worker_cli.py --db .tmp\real-repo\db.sqlite approve-candidate `
  --candidate C-2c0b3836 --approver human --note "real repo review"

python worker_cli.py --db .tmp\real-repo\db.sqlite merge-candidate-git `
  --candidate C-2c0b3836 `
  --repo-path .tmp\real-repo\repo `
  --worktree .tmp\real-repo\integration `
  --check "python check.py" `
  --requested-by human --finish
```

## 9. 下一步

Phase 1 核心完成，接下来进入 Phase 2：

1. 把任务 checks 变成 `start-agent` / UI 的默认来源；
2. 无 evidence 不允许批准；
3. 审批人从字符串升级为轻量身份；
4. 用同一个流程替换成你的真实仓库。
