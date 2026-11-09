# Agent Guild Desktop（Electron 壳）

这是 V1 的桌面壳原型：Electron 主进程 + `utilityProcess` 核心 + IPC + 渲染页。

## 结构

```text
electron/
├─ main.js          # BrowserWindow + utilityProcess + ipcMain
├─ core.js          # utilityProcess 内：接收 RPC，调用 Python CLI
├─ cli_client.js    # spawnSync worker_cli.py，JSON 解析
├─ preload.js       # contextBridge，只暴露 guild.rpc
├─ renderer/        # 项目选择 + 任务表（只读）
└─ package.json
```

## 运行

```powershell
cd p0-coordinator/electron
npm install
$env:ELECTRON_RUN_AS_NODE=$null   # 本机 DSH 环境默认设为 1
npm start
```

Electron 45.2.3 已通过 npmmirror 安装成功；`electron.exe --no-sandbox --disable-gpu --version` 可输出 `v45.2.3`。但当前环境的 BrowserWindow 启动崩溃（`0xC0000005`），最小窗口 `smoke_min.js` 也一样，**所以还没有真实窗口运行验证**。

## 边界

- 渲染层不能直接访问 Node/文件系统，只能 `guild.rpc`；
- 核心 RPC 只暴露 `projects` / `status` / `adapters`，不暴露任意 SQL/命令；
- 这是**只读壳**：创建任务、启动 Run、审批尚未接到 UI；
- 正式版需要把 `worker_cli.py` 的调用换成嵌入式 core（或 sidecar），并加认证/单实例/Job Object。

## 冒烟

```powershell
node electron/smoke_cli.js
```
