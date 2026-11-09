# Verified Live Demo：3 个真实 agent + 独立 verifier

> 项目开启 `require_verification`。agent commit 后不是 done，而是 `verification`；独立 verifier 跑 checks 通过后才 done。

## 1. 结果

| 任务 | worker | commit 后 | verifier | 最终 |
| --- | --- | --- | --- | --- |
| Part A | agent-a | verification | 3/3 checks，score 1.0 | done |
| Part B | agent-b | verification | 3/3 checks，score 1.0 | done |
| Part C | agent-c | verification | 3/3 checks，score 1.0 | done |

最终 metrics：

```json
{
  "tasks_by_status": {"done": 3},
  "runs": 3,
  "avg_quality": 1.0,
  "verifications": 3,
  "avg_verification_score": 1.0,
  "pending_verifications": 0
}
```

注意两个 quality 不是一回事：

- `avg_quality = 1.0`：worker 自报；
- `avg_verification_score = 1.0`：独立 verifier 实际跑 checks 的结果。

## 2. 第一次验证失败：root 配错

第一次跑 `verify_run.py` 时，`root` 被设成了 `live-demo`，而 checks 里的路径是 `live-demo/verified/part-a.md`，拼出来变成：

```text
p0-coordinator/live-demo/live-demo/verified/part-a.md
```

结果 3 个任务全部 `file_exists=fail`，`score=0.0`，任务从 `verification` 退回 `ready`。

这是**正确行为**：

- verifier 没有因为“文件其实存在”就放水；
- 配置错误被验证门挡住，而不是让错误的任务进入 done；
- 失败可恢复：任务回到 ready，可以重新提交。

修正 `root` 为 `p0-coordinator` 后，重新提交并验证，3/3 通过。

## 3. 这次证明了什么

- 真实 agent 的 commit 可以被强制进入 `verification`；
- 独立 verifier 实际执行 checks（file_exists / file_contains / file_regex），不是自报；
- 验证失败会自动打回 `ready`，不会污染 done；
- verifier 的 root/路径配置是契约的一部分，配错会安全失败。

## 4. 没证明什么

- token/cost 仍是 0：没有接真实计量；
- 第一次失败后的重新提交是编排脚本做的，不是 3 个 agent 自己重试（文件本身已经正确）；
- `command` check 没有在这轮启用；
- 审批门没在这轮开启（`require_approval=False`）。

## 5. 复现

```powershell
cd p0-coordinator
python live-demo/verified/setup.py
# 3 个 worker 按 live-demo/verified/brief.md 执行 claim/write/commit
python live-demo/verified/verify_run.py
```
