# Governance Live Demo：3 个真实 agent + 独立验证 + 2-of-2 审批

> 完整链路：`claim → commit → verification → review → 2 approvals → done`

## 1. 设置

- 项目 `live-demo-governance`（`P-304fd676`）
- `require_verification=True`
- `require_approval=True`
- `approvals_required=2`
- 3 个任务，每个带 3 个 checks（file_exists / file_contains / file_regex）

## 2. 结果

| 任务 | worker | commit 后 | verifier | alice | bob | 最终 |
| --- | --- | --- | --- | --- | --- | --- |
| Part A | agent-a | verification | 3/3，1.0 | review | done | done |
| Part B | agent-b | verification | 3/3，1.0 | review | done | done |
| Part C | agent-c | verification | 3/3，1.0 | review | done | done |

metrics：

```json
{
  "tasks_by_status": {"done": 3},
  "runs": 3,
  "avg_quality": 1.0,
  "verifications": 3,
  "avg_verification_score": 1.0,
  "pending_approvals": 0,
  "pending_verifications": 0
}
```

approvals 表里每个任务 2 条 approved：一条 alice，一条 bob。同一个 approver 不能签两次。

## 3. 这条链路现在完整了

```text
worker claim
  → write files
  → commit(run)                      # 自报 quality
  → verification                     # 独立 verifier 跑 checks
  → review                           # 2 条 pending approvals
  → alice approve                    # 仍 review
  → bob approve                      # done
```

每一步都有 DB 记录和事件：

- `task_claimed`
- `verification_requested`
- `verification_passed`
- `approval_requested`（2 条）
- `approval_recorded`（alice）
- `approval_approved`（bob，最终）

## 4. 这次证明了什么

- 真实 agent 的提交可以被验证门拦住，不被直接标记 done；
- verifier 通过后不会自动 done，而是进入 2-of-2 审批；
- 两个人各签一次才 done；同一人重复签会被拒；
- 全流程的 runs / verifications / approvals / events 一致。

## 5. 没证明什么

- token/cost 仍是 0；
- alice/bob 是脚本调用，不是真人；
- `command` check 没启用；
- 没有测审批拒绝/超时路径（单元测试覆盖拒绝；超时未实现）。

## 6. 复现

```powershell
cd p0-coordinator
python live-demo/governance/setup.py
# 3 个 worker 按 live-demo/governance/brief.md 执行
python live-demo/governance/finish.py
```
