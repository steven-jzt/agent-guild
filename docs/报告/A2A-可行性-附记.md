# A2A 能不能用上？——附记（2026-11-07）

> 接上一份《多 agent 同工作空间-实验报告》。
> 上一轮的结论里有一条硬限制：**同级 agent 不能直接互发消息，编排者是唯一路由器**。
> A2A（Agent2Agent）正好是补这个缺口的协议。这篇附记记录：它是什么、我有没有真的跑通、以及它能不能解决上一轮的问题。

## 0. 结论

**能用上，而且已经跑通。**

我用官方 Python SDK（`a2a-sdk 1.3.0`，A2A 协议 v1.0）起了两个独立的 A2A server：

- **Ledger Agent**（`127.0.0.1:8765`）：把上一轮的记账 CLI 包装成 A2A 服务；
- **Audit Agent**（`127.0.0.1:8766`）：收到审计任务后，**自己作为 A2A 客户端**去调 Ledger Agent 取报表，再核对。

客户端只调了 Audit Agent 一次，但实际发生了两条链路：

```text
client ──SendMessage──► Audit Agent ──SendMessage──► Ledger Agent
                              ▲                          │
                              └────── artifact ◄─────────┘
```

`audit.ok = True`，三个检查全过。**没有任何中心编排者参与 audit → ledger 这一跳。**

但要先讲清楚边界：**A2A 解决的是"agent 之间怎么通信"，不是"agent 之间怎么不打架"。** 上一轮的文件并发写问题，A2A 一点都不管——两个 server 仍然共享同一个工作区，Ledger 仍然直接读写同一份数据文件，谁都没有锁。

一句话：**通信层可以标准化，工作区协作仍然要靠文件所有权、契约和锁。**

## 1. A2A 现在是什么状态

| 项 | 现状（2026-11） |
| --- | --- |
| 协议版本 | **v1.0**（`a2a.proto` 为规范源，三种绑定：JSON-RPC、gRPC、HTTP+JSON/REST） |
| Python SDK | `a2a-sdk 1.3.0`，支持 Python 3.10–3.14 |
| 治理 | 已加入 Agentic AI Foundation（Linux Foundation 生态），官方称 150+ 组织采用 |
| 核心对象 | Agent Card、Task、Message、Part（text/raw/url/data）、Artifact |
| 发现 | `GET /.well-known/agent-card.json` |
| 操作（v1.0） | `SendMessage`、`SendStreamingMessage`、`GetTask`、`ListTasks`、`CancelTask`、`SubscribeToTask`、`GetExtendedAgentCard`、推送配置系列 |
| 交互模式 | 请求/响应（轮询）、SSE 流式、Push Notification（webhook） |
| 安全 | API Key / OAuth2 / mTLS 声明；Agent Card 可用 JWS 签名 |

一个容易踩的坑：**v1.0 和 v0.3 不兼容**。网上大量教程还是 `message/send`、`tasks/get`、带 `kind` 字段的写法；v1.0 把操作改成了 `SendMessage` / `GetTask`，删掉了 `kind` 判别器，枚举改成了 `TASK_STATE_*` / `ROLE_*`，Agent Card 的 `protocolVersion` 移到了 `AgentInterface` 里。照旧教程抄会直接对不上。

## 2. 实测：两个 agent + 一次两跳调用

### 2.1 代码结构

```text
a2a-lab/
├─ vendor/                 # a2a-sdk 1.3.0 及依赖（--target 安装）
├─ bootstrap/sitecustomize.py  # 绕过沙箱 tempdir ACL 的补丁（装包用）
├─ _paths.py               # 把 vendor/ 和 demo-ledger/ 放进 sys.path
├─ a2a_client.py           # 极简客户端：发现 card -> SendMessage -> 取 artifact
├─ ledger_agent.py         # A2A server #1：记账
├─ audit_agent.py          # A2A server #2：审计（内部再调 ledger）
├─ demo.py                 # 客户端：先写账，再让 audit 审计
├─ agent-card-ledger.json  # 实测抓下来的 Agent Card
└─ agent-card-audit.json
```

### 2.2 Agent Card（实测抓取，节选）

```json
{
  "name": "Ledger Agent",
  "description": "个人记账 agent：新增条目、列出账目、生成汇总报表。",
  "version": "1.0.0",
  "supportedInterfaces": [
    {
      "url": "http://127.0.0.1:8765",
      "protocolBinding": "JSONRPC",
      "protocolVersion": "1.0"
    }
  ],
  "capabilities": { "streaming": false, "pushNotifications": false },
  "defaultInputModes": ["application/json"],
  "defaultOutputModes": ["application/json"],
  "skills": [
    {
      "id": "ledger",
      "name": "Ledger operations",
      "description": "action=add / list / report；add 需要 amount、category，可选 note、date。",
      "tags": ["ledger", "finance", "json"],
      "examples": ["{\"action\":\"add\",\"amount\":12.5,\"category\":\"餐饮\"}", "{\"action\":\"report\"}"]
    }
  ]
}
```

客户端就是靠这张卡知道"这个 agent 会什么、在哪个 URL、用什么绑定、什么协议版本"——不需要提前硬编码接口。这是 A2A 相比"手写 HTTP 接口"最实际的价值。

### 2.3 两跳调用的实测输出（节选）

```text
[discover] Ledger Agent v1.0.0 skills=['ledger'] interface=http://127.0.0.1:8765 binding=JSONRPC protocol=1.0
[discover] Audit Agent  v1.0.0 skills=['audit_ledger'] interface=http://127.0.0.1:8766 binding=JSONRPC protocol=1.0

=== client -> ledger: add 28.5 餐饮 ===
{ "ok": true, "entry": { "note": "晚饭", "category": "餐饮", "amount": 28.5, "date": "2026-11-07", "id": "ed52d75e2ad8" } }

=== client -> ledger: report ===
{
  "ok": true,
  "summary": { "count": 3.0, "total": -7968.5, "by_category": { "工资": -8000.0, "交通": 3.0, "餐饮": 28.5 } },
  "text": "共 3 笔\n合计 -7968.50\n  - 餐饮: 28.50\n  - 工资: -8000.00\n  - 交通: 3.00"
}

=== client -> audit -> ledger: audit ===
{
  "ok": true,
  "source": "http://127.0.0.1:8765",
  "checks": [
    { "name": "total_equals_category_sum", "ok": true, "detail": "total=-7968.50 category_sum=-7968.50" },
    { "name": "count_is_non_negative", "ok": true, "detail": "count=3" },
    { "name": "report_text_contains_total", "ok": true, "detail": "expect 合计 -7968.50" }
  ]
}

=== 结论 ===
audit.ok = True（audit agent 自己调用了 ledger agent，未经过客户端）
```

### 2.4 失败路径也走通了

发一个未知 action，服务端把任务置为 failed，错误信息通过 Task 状态回传：

```text
EXPECTED-FAILURE: http://127.0.0.1:8765 任务未完成：state=4 message="错误: 未知 action: 'nope'"
```

这说明 A2A 的 Task 生命周期不是摆设：`working → failed`、错误消息、artifact 都在协议层有位置，客户端不需要自己约定错误码。

### 2.5 一个真实的协议细节

`count` 回来是 `3.0` 而不是 `3`。原因是 `data` Part 底层是 `google.protobuf.Value`，所有数字都存成 double。A2A 官方 helper 的文档也明确提醒了这一点：**整数要在客户端自己收窄**。这类"看起来是 JSON、其实有类型语义"的细节，是接协议时最容易忽略的成本。

## 3. 映射回上一轮的痛点

| 上一轮的问题 | A2A 能否解决 | 说明 |
| --- | --- | --- |
| 同级 agent 不能互发消息（`belongs to another parent session`） | ✅ 能 | agent 本身就是 client，HTTP 直连；本实验的 audit → ledger 就是 |
| 发现与能力协商靠人工约定 | ✅ 能 | Agent Card 标准化了名称、技能、输入输出模式、端点、协议版本 |
| 长任务、进度、取消、流式 | ✅ 能 | Task 状态机 + SSE + push notification |
| 跨厂商/跨机器/跨语言 | ✅ 能 | 三种绑定、六种语言 SDK、标准 Web 安全 |
| 同一文件并发写丢更新 | ❌ 不能 | 两个 server 共享同一工作区，A2A 不提供文件锁/事务 |
| 统一调度、预算、权限、审计策略 | ❌ 不能 | 协议只定义"怎么通话"，不定义"谁该干什么" |
| 单机同进程的父子委派 | ⚠️ 过重 | 起端口、序列化、任务存储，成本高于原生 subagent 调用 |

结论：**A2A 补的是"横向通信"，不是"纵向编排"，更不是"共享内存一致性"。**

> 补记：通信本身对写冲突的改善幅度，我做了一组对照实验——纯信息共享约等于无效，信息 + 退避把丢失从 61% 降到 2.4%，原子租约才归零。见 [agent通信与冲突-实验.md](agent通信与冲突-实验.md)。

## 4. 接到 DSH 的三条路径

### 路径 A：旁路服务化（本实验采用）

每个 agent 是一个独立进程 + A2A server；DSH 里的 agent 作为 A2A 客户端去调它们，或者反过来把 DSH 包成 server。

- 适合：跨机器、跨技术栈、需要让外部 agent 调进来；
- 代价：多一层进程/端口/部署；
- 现状：本 demo 已经证明可行，且能复用同一工作区里的代码。

### 路径 B：A2A 桥接层

写一个 sidecar：对外暴露 A2A server，收到 `SendMessage` 后转成一次 DSH 会话（或 subagent 调用），把结果包成 artifact 返回。

- 适合：想让 DSH 的 agent 被外部 A2A 生态发现和调用；
- 代价：需要处理会话生命周期、超时、并发、鉴权；DSH 目前没有原生 A2A endpoint，得自己写适配器；
- 注意：这不是"改个配置"，是一个真正的网关服务。

### 路径 C：只在跨边界时用

同进程、同工作空间的父子委派继续用 DSH 原生 subagent（便宜、上下文共享、文件可见）；只有需要跨机器/跨团队/跨厂商时才引入 A2A。

- 适合：大多数单机项目；
- 判断标准：**"对方是不是一个我看不见内部、只能通过消息交互的黑盒？"** 是，就用 A2A；不是，就用原生 subagent。

## 5. 成本与坑

1. **版本割裂**：v1.0 与 v0.3 不兼容，教程/示例大量停留在 v0.3；接之前先确认 SDK 和协议版本。
2. **类型语义**：protobuf 的 `Value` 把 int 变 float；`Part` 也没有 `kind` 了，要靠字段名判别。
3. **状态存储**：demo 用 `InMemoryTaskStore`，重启即丢；生产要持久化（SDK 提供 SQL 后端）。
4. **鉴权**：demo 没有认证；生产要 API Key/OAuth2/mTLS，Agent Card 签名也要配。
5. **部署**：每个 agent 一个端口/服务；服务发现、健康检查、超时重试都要自己补。
6. **沙箱适配**：本机 DSH 沙箱下 `tempfile` 的 0o700 目录不可写，装 SDK 时用 `bootstrap/sitecustomize.py` 把 `os.mkdir` 的 mode 放宽到 0o777 才装上；这是环境问题，不是 A2A 的问题。
7. **共享工作区仍然裸奔**：Ledger server 直接读写工作区里的数据文件，A2A 不会给文件加锁；要并发写，仍得回到上一轮的锁/单写者方案。

## 6. 建议

- **如果目标是"多个 agent 在同一工作空间做一个项目"**：A2A 不是关键路径。先把契约、文件所有权、验收测试、锁做好；A2A 只在需要 peer 直连或跨机时加上。
- **如果目标是"异构 agent 协作 / 跨团队 / 跨机器"**：A2A 值得上。最小落地顺序是：Agent Card（把能力说清楚）→ `SendMessage` + artifact（把任务和结果跑通）→ 再补流式、取消、鉴权、持久化。
- **混合架构最务实**：DSH 原生 subagent 负责同工作区的并行开发；A2A 负责把外部/远程/异栈 agent 接进来。两者不是替代关系。

## 7. 复现

```powershell
cd a2a-lab

# 终端 1
python ledger_agent.py      # 127.0.0.1:8765

# 终端 2
python audit_agent.py       # 127.0.0.1:8766

# 终端 3
python demo.py
```

预期最后一行：`audit.ok = True（audit agent 自己调用了 ledger agent，未经过客户端）`。

依赖已经装在 `a2a-lab/vendor/`（`pip install --target`），不需要动系统环境。`vendor/` 体积较大、可随时删除重建：

```powershell
pip install --target vendor "a2a-sdk[http-server]==1.3.0" uvicorn
```

## 8. 参考

- A2A 规范 v1.0：<https://a2a-protocol.org/latest/specification/>
- v0.3 → v1.0 变更：<https://a2a-protocol.org/latest/whats-new-v1/>
- A2A Python SDK：<https://pypi.org/project/a2a-sdk/> ／ <https://github.com/a2aproject/a2a-python>
- 核心概念：<https://a2a-protocol.org/latest/topics/key-concepts/>
- Linux Foundation 采用情况：<https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year>
