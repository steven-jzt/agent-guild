# 语义级 + 组织级 MVP 设计与计划

日期：2026-11-09  
目标：把“语义变更协调”和“Agent 能力标签”从讨论推进到可运行的第一版。

---

## 1. 范围

### 做

**语义级**

- 从 Git diff 提取 Python 顶层符号变化；
- 识别 added / removed / signature_changed / body_changed；
- 标记 compatibility：compatible / breaking / unknown；
- 写入数据库，形成可查询的变更事实；
- 影响面：同 write_set 的其他任务 + 显式语义依赖；
- 定向 ACK；
- breaking change 必须 admin 批准。

**组织级**

- Agent 能力标签表；
- 人工/来源/置信度；
- `recommend-agent --require-tag` 过滤；
- OR 推荐时按标签过滤。

### 不做（本轮）

- LLM 语义审查；
- 全量跨语言 AST；
- 自动修复语义冲突；
- 合并队列；
- A2A 消息总线；
- 动态组队/自动重排；
- 分布式数据库；
- 把“主领导 Agent”作为真相源。

---

## 2. 语义级设计

### 2.1 事实优先

```text
Git diff
  → 确定性 AST 提取
  → semantic_changes（事实）
  → impact（受影响任务）
  → 定向通知 / ACK
  → breaking 门禁
```

主 Agent 不参与事实提取；后续最多做解释和仲裁。

### 2.2 变更类型

```text
added               新增函数/类
removed             删除函数/类
signature_changed   参数/返回值/装饰器变化
body_changed        函数体变化
parse_error         无法解析
```

### 2.3 兼容性

```text
removed             breaking
signature_changed   breaking
added               compatible
body_changed        unknown
parse_error         unknown
```

### 2.4 影响面

MVP 两种来源：

1. 同一 repo 内，其他任务的 `write_set` 包含该文件；
2. 显式 `semantic_dependencies(task_id, object)`。

输出：

```json
{
  "change_id": "SC-...",
  "object": "ops.div",
  "compatibility": "breaking",
  "affected_tasks": [
    {"task_id": "T-...", "reason": "write_set:src/calc/ops.py", "pending_ack": true}
  ]
}
```

### 2.5 ACK

依赖方必须显式 ACK：

```text
semantic_ack(change_id, task_id, agent)
```

没有 ACK 不影响候选本身，但会出现在影响面里；  
breaking change 的审批由 admin 角色兜底，避免“没人 ACK 也能合”。

### 2.6 breaking 门禁

`approve_candidate` 增加：

```text
if candidate has breaking semantic change:
    require role == admin
```

这是最小语义门禁；后续再接 merge queue/rebase 重检。

---

## 3. 组织级设计

### 3.1 能力标签表

```sql
agent_capabilities (
  agent_id,
  dimension,
  value,
  confidence,
  source,
  sample_size,
  updated_at,
  PRIMARY KEY(agent_id, dimension, value)
)
```

维度示例：

```text
language: python / typescript
task_type: bugfix / feature / test / review
tool: pytest / git / browser
strength: api-design / refactor / perf
```

### 3.2 标签原则

- 自报只是先验；
- 历史 runs 是证据；
- 人工可修正；
- 标签带 confidence/sample_size/source；
- 能力 ≠ 权限；
- 允许过期/衰减（后续）。

### 3.3 OR 使用

```powershell
python worker_cli.py ... recommend-agent --task T-xxxx --require-tag language:python
python worker_cli.py ... start-agent --task T-xxxx --agent auto --require-tag task_type:bugfix
```

MVP 只做硬过滤：没有标签的 agent 不进入候选；  
后续再加置信度加权和探索。

---

## 4. CLI / API

```text
semantic-changes --candidate C-xxxx
semantic-impact --change SC-xxxx
semantic-ack --change SC-xxxx --task T-xxxx --agent dsh
add-semantic-dep --task T-xxxx --object ops.div
tag-agent --agent codex-deepseek --dimension language --value python --confidence 0.9 --source manual
agent-profile --agent codex-deepseek
recommend-agent --task T-xxxx --require-tag language:python
```

---

## 5. 验收标准

- [x] AST 提取覆盖 added/removed/signature/body；
- [x] Git diff 能生成 semantic_changes；
- [x] breaking change 必须 admin 批准；
- [x] 影响面能返回 write_set 相关任务；
- [x] ACK 可记录、可查询；
- [x] 能力标签可写入、可查询；
- [x] `recommend-agent --require-tag` 能过滤；
- [x] 全量测试通过；
- [x] 文档和共享包更新。

验收记录：[SEMANTIC-ORG-VERIFY.md](SEMANTIC-ORG-VERIFY.md)

---

## 6. 后续

1. merge queue + rebase 后语义重检；
2. 跨语言 AST / tree-sitter；
3. 接口/schema/config 契约；
4. 置信度加权 + 探索；
5. 动态组队 / A2A；
6. 把语义事实暴露给 UI。
