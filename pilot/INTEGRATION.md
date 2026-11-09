# 真实 Git 集成合入（2026-11-08）

## 1. 结果

把 `sample-repo` 三个 Candidate 真正 cherry-pick 到一个集成分支：

```text
base:        7d722e9877402517cd34bbdd8dd21be888ccb308
candidate 1: 7ea7481b... (pricing.py)
candidate 2: 0443aa24... (cart.py)
candidate 3: 94b7bd94... (formatting.py)
integration: d0364865ab148ece096417801dc26969935b113b
```

集成分支相对 base：

```text
src/shop/cart.py       | 4 ++++
src/shop/formatting.py | 4 ++++
src/shop/pricing.py    | 4 ++++
3 files changed, 12 insertions(+)
```

验证：

```text
python -m unittest discover -s tests -q -> 3 tests OK
bulk_discount(100,10) -> 90.0
cart_total([...]) -> 6
format_price(123456) -> $1,234.56
```

## 2. 命令

```powershell
$repo = "p0-coordinator/.tmp/pilot-sample/repo"
$int  = "p0-coordinator/.tmp/pilot-sample/integration"

git -C $repo worktree add -b integration $int <base_sha>
git -C $int cherry-pick <candidate1> <candidate2> <candidate3>
```

## 2.1 CLI 入口

现在也可以直接用 CLI：

```powershell
python worker_cli.py --db .tmp/cli-merge/db.sqlite merge-candidate-git `
  --candidate C-xxxx --repo-path .tmp/cli-merge/repo `
  --worktree .tmp/cli-merge/integration --requested-by reviewer --finish
```

输出包含：

```json
{
  "candidate_id": "C-xxxx",
  "result_sha": "...",
  "branch": "guild/merge-...",
  "worktree": "...",
  "merge_attempt": {"status": "succeeded", "result_sha": "..."}
}
```

CLI 冒烟实测：`result_sha=862dc4f...`，集成分支文件 `value = 2`，merge attempt `succeeded`。

## 3. 这次证明了什么

- Candidate 的 commit 不只是 DB 里的 SHA，可以真实 cherry-pick；
- 三个 agent 改不同文件时，顺序合入无冲突；
- 集成分支测试通过，三个新增 API 都可 import；
- “规则层 merged” 之外，真实 Git 合入可行。

## 4. 边界

- 集成 worktree 在 `.tmp` 下；`pilot/sample-repo` 的 `main` 仍是 base；
- `finish_merge` 仍只更新 DB；真实合入走 `merge_candidate_git()` 或 CLI `merge-candidate-git --finish`；
- 还没有把 Git 合入自动塞进 `finish_merge` 或 UI；
- 没有处理冲突（本次三个文件不重叠）；
- 没有回滚/失败恢复（方法内有 abort/cleanup，但流程级恢复未测）。

## 5. 下一步

- 把 `integration` 分支合回 `sample-repo` 的管理副本；
- 在 Coordinator 里加 `merge_candidate_repo()`：cherry-pick + 测试 + 成功才 `finish_merge`；
- 加冲突/回滚路径。
