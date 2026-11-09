# 审批角色（Phase 2 / 多用户第一层）

日期：2026-11-09  
目标：先让“谁批的、以什么角色批的”可审计，再谈真实认证与权限。

## 1. 角色

| 角色 | 含义 | 当前权限 |
|---|---|---|
| `reviewer` | 普通审批人 | 可批准候选 |
| `admin` | 管理员 | 可批准候选；后续用于高风险门禁 |

当前两种角色都能批准，区别在审计记录；还没有“admin 才能合入”的强策略。

## 2. 数据

`candidates` 新增：

```text
approver_role TEXT NOT NULL DEFAULT 'reviewer'
```

旧库自动迁移：

```python
if "approver_role" not in ccols:
    ALTER TABLE candidates ADD COLUMN approver_role TEXT NOT NULL DEFAULT 'reviewer'
```

## 3. CLI

```powershell
python worker_cli.py --db ... approve-candidate `
  --candidate C-xxxx `
  --approver alice `
  --role admin `
  --note "reviewed"
```

非法角色直接拒绝：

```text
invalid approver role: owner
```

## 4. API

```python
coord.approve_candidate(candidate_id, approver, note="", role="reviewer")
```

返回：

```json
{
  "candidate_id": "C-...",
  "status": "approved",
  "approved_by": "alice",
  "approver_role": "admin"
}
```

事件 `candidate_approved` 也带 `role`。

## 5. UI

Agent 下拉旁边新增：

```text
审批角色：Reviewer / Admin
```

批准后候选卡片显示：

```text
批准：webview2-reviewer · admin
```

## 5.1 项目级策略

项目可以要求更高的审批角色：

```powershell
python worker_cli.py --db ... create-project `
  --name high-risk `
  --required-approver-role admin
```

策略生效点：

- `approve_candidate`：reviewer 批准 admin-only 项目会被拒绝；
- `start_merge`：检查候选的 `approver_role` 是否满足项目要求；
- `merge_candidate_git`：真实 cherry-pick 前再检查一次。

错误示例：

```text
project requires admin approval; got reviewer
candidate was approved by reviewer; project requires admin
```

默认项目仍是：

```text
required_approver_role = reviewer
```

所以旧流程不受影响，admin 也可以批 reviewer 项目。

## 5.2 多级审批链

项目可以要求多级：

```powershell
python worker_cli.py --db ... create-project `
  --name chain `
  --approval-chain "reviewer,admin"
```

行为：

```text
reviewer 批准
→ 候选仍是 awaiting_human
→ pending_roles: ["admin"]

admin 批准
→ 候选 approved
```

顺序必须正确：

```text
next approval role is reviewer; got admin
```

每次批准写入 `candidate_approvals`，可按候选查询：

```python
coord.list_candidate_approvals(candidate_id)
```

实测：

```text
reviewer=blocked project requires admin approval; got reviewer
admin=approved admin
merge_attempt pending
```

## 6. 验证

```powershell
python -m unittest test_v1_core -v
```

覆盖：

- 批准时记录角色；
- 非法角色拒绝；
- 无 evidence 不允许批准；
- 多级审批链 `reviewer → admin`；
- 旧库打开后自动补列。

实测旧库 `.tmp/real-repo/db.sqlite` 打开后：

```text
candidates columns include approver_role
```

## 7. 限制

- 不是认证：名字和角色都是调用方传入；
- 已有多级审批链（如 `reviewer,admin`），但没有真实身份；
- 没有用户表/登录；
- 角色策略只有 `reviewer` / `admin` 两档。

## 8. 下一步

- 与审批 TTL / N-of-M 合并；
- 高风险操作（merge/push）独立角色；
- 再接真实身份（本地账号或 SSO）；
- UI 展示审批链进度。
