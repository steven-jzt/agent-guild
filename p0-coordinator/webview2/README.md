# Agent Guild WebView2 壳

![控制台](../../pilot/ui-dashboard.png)

这是 Electron 的替代桌面壳。  
本机 Electron 17/22/28/45/46-alpha 都在启动阶段崩 `0xC0000005`，但 Edge/WebView2 正常，所以改用 WebView2。

## 2026-11-13：异步请求与停止

消息处理异步等待 Python；stdout/stderr 同时排空，单路最多 2M 字符，响应序列化超限也返回错误。每个请求由现有 Windows JobHost 管理进程树，复用 `agent-guild/src/transport/JobHost.cs`。同一任务或候选不能并发重复提交，刷新不会清除运行中的操作。当前本地请求可点击“停止当前请求”；关闭窗口或更换文档也会停止本页请求。超时和停止均不冒充业务成功。

“停止请求”表示终止本地 Python/Agent 进程树；不承诺远端模型服务撤销，也不自动回滚工作区或数据库。若进程被终止时正在改文件或写记录，结果为 unknown，数据库可能仍显示执行中。先核对工作区、Execution 和 MergeAttempt；确认本地进程已退出后，使用既有 `finish-execution --status interrupted` 等明确操作收尾，不能直接自动重跑。合入的 prepared/unknown 结果使用持久凭据与真实目标核对完成。详见[版本说明与恢复步骤](../../docs/releases/2026-11-13-p1-integration-ui.md)。

默认通过 `py.exe -3` 运行 Python，也可将 `PYTHON` 设为具体 Python **可执行文件路径**（不附加命令行参数）。单个请求最长 30 分钟；进程停止和管道退出分别有 5 秒确认上限，无法确认时明确报 unknown。预编译 GUI/CLI 与 `AgentGuildJobHost.exe` 已同步；`build.ps1` 可复用仓库已有 WebView2 DLL 离线构建。

验证命令（在 `p0-coordinator/`）：

```powershell
py -3.13 -X utf8 -m unittest test_webview2_process test_webview2_ui -q
node --test webview2/test-bridge.cjs
```

这些测试离线构建真实 C# 代码：10 项进程/管道测试（含创建子进程瞬间杀宿主）、3 项隐藏窗口测试及 4 项 JavaScript 桥接/界面逻辑测试；不会调用真实模型。`--smoke` 和 `--smoke-cancel` 是测试模式，窗口隐藏，实际交互启动不加这些参数。

## 直接运行

```powershell
cd p0-coordinator\webview2
.\bin\AgentGuildWebView2.exe
```

如果 PowerShell 执行策略拦截脚本，用上面的 exe 直接启动即可。  
也可以：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

需要 Microsoft Edge WebView2 Runtime（Windows 11 自带；Windows 10 通常随 Edge 安装）。

## 验证

```powershell
.\bin\AgentGuildWebView2Cli.exe --smoke
```

期望：

```text
WEBVIEW2_SMOKE {"smoke":true,"data":[...projects...]}
```

说明：

- 窗口创建成功；
- WebView2 初始化成功；
- 现有 `electron/renderer` 页面加载成功；
- 注入的 `window.guild.rpc` 桥成功；
- Python `worker_cli.py projects` 返回真实数据。

## 功能

- 项目下拉；
- 任务表 + **启动 Run**（真正启动选中 agent）；
- 候选表；
- **批准**（awaiting_human）；
- **合入**（approved，准备完整候选快照 + finish_merge 确认目标更新）；
- 刷新；
- RPC：`projects` / `status` / `candidates` / `start_run` / `approve` / `merge` / `adapters`。

`start_run` 现在会调用 `worker_cli.py start-agent`：

```text
start execution
→ 启动 adapter（Codex-DeepSeek / 智谱 / App Server / DSH）
→ 等完成
→ scope check
→ 清理 __pycache__
→ commit → Candidate
→ 可选 check → evidence → awaiting_human
```

边界：

- 需要任务**已有 active workspace**；
- `check` 为空时，Candidate 停在 `building`，不会自动进人工审批；
- DSH 用独立 `DSH_HOME`（`.tmp/dsh-ui-home`），已验证 UI 启动；
- Merge 走 `merge_candidate_git()` 准备完整候选，再由 `finish_merge()` 核对并更新目标；基线变化、用户脏工作区和未确认结果不会显示合入成功。

## 真实启动 agent

```powershell
cd p0-coordinator
python ..\pilot\seed_agent_task.py
```

记下 `db` / `task_id` / `repo` / `worktree_for_merge`，然后：

```powershell
$env:GUILD_DB="...\agent-demo\db.sqlite"
$env:DEEPSEEK_API_KEY="sk-..."
cd webview2
.\bin\AgentGuildWebView2.exe
```

在 UI 里：

1. 选任务；
2. agent 选 **Codex-DeepSeek** 或 **DSH**；
3. check 填 `python -m py_compile src/app.py`；
4. 点 **启动 Run**；
5. 等候选变成 `awaiting_human`；
6. 点 **批准**；
7. 填 repo / worktree；
8. 点 **合入**。

## 用种子数据试

```powershell
cd p0-coordinator
python ..\pilot\seed_webview2.py
```

记下输出的 `db`、`candidate_id`、`repo`、`worktree_for_merge`，然后：

```powershell
$env:GUILD_DB="...\webview2-demo\db.sqlite"
cd webview2
.\bin\AgentGuildWebView2.exe
```

在 UI 里：

1. 点候选的 **批准**；
2. 填 repo / worktree；
3. 点 **合入**；
4. 看到 `已合入: <sha>`。

## 命令行自测

```powershell
$env:GUILD_DB="...\webview2-demo\db.sqlite"
.\bin\AgentGuildWebView2Cli.exe --call candidates
.\bin\AgentGuildWebView2Cli.exe --call approve --candidate C-xxxx
.\bin\AgentGuildWebView2Cli.exe --call merge --candidate C-xxxx --repo-path ... --worktree ...
```

## 结构

```text
webview2/
├─ Program.cs        # WinForms + WebView2 + JSON-RPC 桥
├─ build.ps1         # 下载 WebView2 SDK + csc 编译
├─ run.ps1
├─ bin/              # 预编译 exe + WebView2 DLL
└─ README.md
```

## 为什么不用 Electron

本机 Electron 崩溃证据：

- [../../pilot/ELECTRON-BLOCKED.md](../../pilot/ELECTRON-BLOCKED.md)

WebView2 用 Edge 的 Chromium，绕开 Electron 运行时。

## 与 Electron 的关系

- `electron/` 保留，供能跑 Electron 的机器使用；
- `webview2/` 是本机可用的桌面壳；
- 两者共用 `electron/renderer` 的 HTML/CSS/JS 和 Python CLI 桥思路。
