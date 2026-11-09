# Codex App Server 真实冒烟（2026-11-08）

## 1. 结果

`pilot/codex_app_server_smoke.py` 打通：

```text
initialize      -> userAgent / codexHome / platform
thread/start    -> thread id, provider=deepseek
turn/start      -> turn id
turn/completed  -> completed=true
```

第二次 turn 让 Codex 在 workspace 里创建文件：

```text
prompt: Create appserver.txt containing exactly OK
file:   p0-coordinator/.tmp/appserver-ws/appserver.txt
content: OK
```

## 2. 协议

```text
JSON-RPC over stdio:
  1. initialize
  2. thread/start（cwd, model=deepseek-flash, modelProvider=deepseek,
                  sandbox=danger-full-access, approvalPolicy=never）
  3. turn/start（threadId, input=[{type:"text", text:...}]）
  4. 等 turn/completed
```

事件：

```text
thread/started
turn/started
item/started
item/completed
item/agentMessage/delta
thread/tokenUsage/updated
turn/completed
```

## 3. usage

第二次（改文件）：

```json
{
  "inputTokens": 18867,
  "cachedInputTokens": 18432,
  "outputTokens": 350,
  "reasoningOutputTokens": 290,
  "totalTokens": 19217
}
```

第一次（只回复 OK）：

```json
{
  "inputTokens": 9245,
  "cachedInputTokens": 9088,
  "outputTokens": 9,
  "totalTokens": 9254
}
```

## 4. 这次证明了什么

- Codex App Server 不只是能启动，能完成 initialize/thread/turn；
- 真实模型（DeepSeek provider）能通过 App Server 执行工具并改文件；
- usage 能从 `thread/tokenUsage/updated` 读到；
- 全程 JSON-RPC，不需要 OpenAI 账号。

## 5. 没证明什么

- **没有接进 Coordinator/V1**：这是独立 smoke，不是 `start_execution → candidate → merge`；
- 没有 `CodexAppServerAdapter`；
- 没有中断/取消/审批请求处理；
- 没有多轮/多 thread 并发；
- 文本提取还依赖事件结构，当前 smoke 主要靠文件内容验证。

## 6. 复现

```powershell
cd p0-coordinator
$env:DEEPSEEK_API_KEY="sk-..."
$env:CODEX_HOME="...\.tmp\codex-deepseek-home"
python ..\pilot\codex_app_server_smoke.py
```

## 7. 下一步

- 把 smoke 抽成 `CodexAppServerAdapter`；
- 在 V1 里用 App Server 跑一次 candidate/merge；
- 补 turn/interrupt、审批请求、进程清理。
