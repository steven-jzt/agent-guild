# Agent Guild：M0 接入实验

目前处于第一阶段（M0）：先证明能稳定调用 Codex 和 DSH，再开发协作功能与桌面界面。这里已经有可运行的接入实验和测试，尚未成为桌面应用。

**当前状态：M0 未通过全部准入条件。** Codex、DSH 账号、DSH API Key 三条路线的基本任务均已真实验证通过。2026-11-08 用户更新本机密钥后，API Key 路线复测通过，原 401 阻塞解除；仍需补齐审批、中断及鉴权错误等验证，暂不进入 M1、M2。

## 用功能理解目前的成果

- 可以让真实 Agent 在独立的实验工作区里修改一个小功能，再由平台单独运行测试、检查修改范围。
- 可以接收 DSH 的提问和审批请求。本轮用固定测试答案验证通道，并拒绝测试审批；尚未接入真人操作界面。
- 可以停止正在执行的进程，再启动新的执行，从保留下来的工作区继续任务。
- 会保存运行结果和经过脱敏的事件记录。模型说“完成”或系统显示“空闲”，都不能直接当作任务成功。

任务列表、文件占用、候选版本、Agent 审查、人工验收、合并确认和桌面界面属于后续阶段，尚未实现。

## 本机运行

在 PowerShell 中进入项目目录：

```powershell
Set-Location -LiteralPath 'C:\Users\LENOVO\OneDrive\文档\ChatGPT\Github\agent-guild'
npm ci
npm test
npm run typecheck
```

当前机器已经安装依赖，不需要重复安装。要求 Node.js ≥24.9、Git；本轮验证使用 Windows x64。Windows 进程管理助手首次运行时使用系统 .NET Framework 的 `csc.exe` 编译，缓存到本机 AppData 的 `AgentGuild/bin`。

测试夹具 `test/fixtures/rpc-server.mjs` 会先发 `fixture/ready` 通知；`test/rpc-process.test.ts` 等到就绪后才开始请求计时。启动等待（默认 5s）与单个请求期限（畸形响应 500ms、`stall` 100ms）分开，测试包含延迟启动与就绪超时用例。`npm run typecheck` 需要 `typescript`/`@types/node` 开发依赖；未安装时只能运行 `node --experimental-strip-types --test`，不能把去掉类型的运行当成类型检查。

真实模型实验可能消耗账号额度，运行一次会创建一个新的实验目录：

| 命令 | 验证内容 | 当前结果 |
| --- | --- | --- |
| `npm run m0` | Codex 完成限定文件的任务 | 已通过 |
| `npm run m0:dsh -- account` | DSH 账号完成同一任务 | 已通过 |
| `npm run m0:dsh -- api-key` | DSH API Key 完成同一任务 | 更新密钥后已通过，原 401 阻塞解除 |
| `npm run m0:dsh:interaction` | DSH 原生提问、拒绝审批 | 已通过，固定测试响应 |
| `npm run m0:cancel -- codex` | Codex 执行中停止、新执行接续 | 已通过 |
| `npm run m0:cancel -- dsh-account` | DSH 等待提问时停止、新执行接续 | 已通过 |

Codex 使用本机已登录的 CLI，默认查找 `%APPDATA%/npm/node_modules/@openai/codex/bin/codex.js`；位置不同时可设置 `AGENT_GUILD_CODEX_JS`。DSH 使用项目锁定的官方 npm Runtime，不调用桌面安装目录里的封装程序。

DSH 实验引用本机官方凭据文件 `~/.dsh/.credentials.yaml`，运行记录不复制凭据内容；API 与账号路线分别选择，失败后不会自动切换。密钥只在 DSH 官方本地设置中维护，不要发到聊天或写进仓库。本次复测使用用户在本机更新后的凭据，未修改任何凭据配置。

## 文件与证据

```text
src/transport/       JSONL、JSON-RPC、Windows 执行进程树管理
src/adapters/        Codex 与 DSH 的实验适配器
src/experiments/     独立 Git 克隆、真实模型任务、交互与停止实验
test/                自动化测试及受控协议服务
docs/                验证结论、失败原因与后续准入条件
```

每次实验会打印实际目录，并生成 `report.json`、`events.json`。默认在 `%LOCALAPPDATA%/AgentGuild/experiments` 下；打包桌面环境可能重定向该目录，程序会解析真实路径后再交给 Agent。原始日志保留在本机，未加入 Git。

实验使用专门生成的源仓库、独立裸克隆及 Worktree，没有拿用户真实项目做修改或合并。实验不包含任何自动合并功能，也不提供操作系统级的文件隔离承诺。

详细结果见 [M0 验证记录](../docs/验证/M0_验证记录_2026-11-08.md)。当前上游 DSH 依赖存在审计告警，发布客户端也有类型声明兼容问题，均已记录；此目录只用于 M0 开发验证。

## 下一步

补齐 M0 的原生审批、API 路线停止/接续及鉴权失效等验证。三条接入路线满足阶段准入条件后，再进入 M1 协作核心。
