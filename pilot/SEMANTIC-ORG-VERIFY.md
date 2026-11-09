# 语义级 + 组织级 MVP 验收

日期：2026-11-09  
对应设计：[SEMANTIC-ORG-DESIGN.md](SEMANTIC-ORG-DESIGN.md)

## 结论

**第一版已落地并通过测试。**

- Python AST 语义变更提取；
- Git diff → semantic_changes；
- breaking change 必须 admin；
- 影响面 + 定向 ACK；
- Agent 能力标签；
- OR `--require-tag` 过滤；
- 全量 **91 tests OK**。

这是“事实层 + 门禁层”的 MVP，不包含 LLM 语义审查、合并队列、A2A、动态组队。

---

## 1. 语义级实现

### 1.1 提取器

文件：`p0-coordinator/semantic.py`

能力：

- 顶层函数；
- 顶层类；
- 类方法；
- 函数签名；
- 函数体变化；
- 语法错误降级为 `parse_error`。

变更类型：

```text
added
removed
signature_changed
body_changed
parse_error
```

兼容性：

```text
added              compatible
removed            breaking
signature_changed  breaking
body_changed       unknown
parse_error        unknown
```

### 1.2 数据表

```text
semantic_changes
semantic_acks
semantic_dependencies
agent_capabilities
```

### 1.3 自动捕获

`create_candidate_from_workspace()` 会自动：

```text
candidate 创建
→ 读取 workspace 的 repo/base/candidate
→ git diff
→ AST 提取
→ 写 semantic_changes
```

提取失败不会阻塞 Candidate，只记录 `semantic_capture_failed` 事件。

### 1.4 breaking 门禁

`approve_candidate()` 检查：

```text
如果 candidate 有 breaking semantic change：
  单级链：必须是 admin
  多级链：链中必须包含 admin
```

reviewer 会被拒绝：

```text
breaking semantic change requires admin approval
```

### 1.5 影响面与 ACK

```powershell
python worker_cli.py --db ... semantic-changes --candidate C-xxxx
python worker_cli.py --db ... semantic-impact --change SC-xxxx
python worker_cli.py --db ... semantic-ack --change SC-xxxx --task T-xxxx --agent dsh
python worker_cli.py --db ... add-semantic-dep --task T-xxxx --object ops.div
```

影响面来源：

- 同 repo 内其他任务的 `write_set` 包含该文件；
- 显式 `semantic_dependencies`。

---

## 2. 组织级实现

### 2.1 能力标签

```powershell
python worker_cli.py --db ... tag-agent `
  --agent dsh `
  --dimension language `
  --value python `
  --confidence 0.9 `
  --source manual

python worker_cli.py --db ... agent-profile --agent dsh
```

### 2.2 OR 过滤

```powershell
python worker_cli.py --db ... recommend-agent `
  --task T-xxxx `
  --strategy time `
  --require-tag language:python
```

结果只包含具备该标签的 Agent；没有匹配时明确报错。

---

## 3. 测试

```text
python -m unittest test_semantic -v
```

覆盖：

- 新增函数 → compatible；
- 删除函数 → breaking；
- 签名变化 → breaking；
- 函数体变化 → unknown；
- 自动捕获；
- 影响面包含同 write_set 任务；
- ACK 后 `acked=true`；
- reviewer 被 breaking 门禁拒绝；
- admin 批准成功；
- 能力标签写入/查询；
- `--require-tag` 过滤。

全量：

```text
91 tests OK
```

---

## 4. 限制

- 只支持 Python；
- 只提取顶层函数/类/类方法；
- 类 body 变化可能同时产生类级和方法级两条事实；
- `body_changed` 只标 unknown，不做行为分析；
- 不做接口 schema / DB migration / 配置键；
- 不做 merge queue / rebase 重检；
- 影响面依赖 `write_set` 和显式依赖，不是完整调用图；
- 能力标签是人工/静态写入，尚未从历史 runs 自动校准；
- 不做动态组队/A2A。

---

## 5. 下一步

1. tree-sitter 支持多语言；
2. 接口 schema / config key / migration 变更；
3. merge queue + 最新 base 重检；
4. 从 runs 自动更新能力置信度；
5. 能力标签 + 硬约束/软排序 + 探索；
6. 语义事实展示到 WebView2。
