# Electron 阻塞证据（2026-11-08）

## 结论

**当前这台 Windows 环境无法启动 Electron 窗口。**  
不是我们的 main/preload/IPC 代码问题，而是 `electron.exe` 本体在创建窗口时崩溃。

## 1. 版本矩阵

| 版本 | 结果 |
| --- | --- |
| Electron 17.4.11 | `0xC0000005` |
| Electron 22.3.27 | `0xC0000005` |
| Electron 28.3.3 | `0xC0000005` |
| Electron 45.2.3 | `0xC0000005` |
| Electron 46.0.0-alpha.7 | `0xC0000005` |

`electron.exe --version` 都能输出版本；`node electron/smoke_cli.js` 也通过。  
只有 BrowserWindow 创建时崩。

## 2. Windows 事件日志

Application Error 1000：

```text
出错应用程序名称: electron.exe
出错模块名称:   electron.exe
异常代码:       0xc0000005
```

45.2.3 错误偏移 `0x7f1b4ab`；28.3.3 错误偏移 `0x21c3c6b`。  
不是某个第三方 DLL，是 electron.exe 本体。

## 3. 已排除

- `--no-sandbox --disable-gpu`：仍崩；
- `--headless` / `--headless=new`：仍崩；
- `__COMPAT_LAYER=WIN8RTM / WIN7RTM`：仍崩；
- 最小 `BrowserWindow({show:false})`：仍崩；
- **计划任务 `/IT` 绕开 DSH Job Object**：仍崩（Last Result `-1073741819`）；
- 当前进程在 Session 1 Console，不是 Session 0。

## 4. 判断

不是 DSH 的 Job Object，也不是 Electron 单版本 bug。  
17/22/28/45 四个大版本都在创建最小窗口时崩 `0xC0000005`，更像是 **Windows build 26200 / 本机安全策略（ACG/CFG/Exploit Protection）/ 图形环境** 导致。

“换一台 Windows”不是指换许可证，而是需要一个 **Electron 能创建窗口的已知良好环境**：另一台机器、Windows VM、Windows Sandbox 或云桌面都可以。  
也可以先尝试在本机修安全策略/换用户配置，不一定要换机器。

## 4.1 进一步定位（round 12）

- 连 `app.whenReady()`、**不创建 BrowserWindow** 也崩 `0xC0000005`；
- `electron.exe --version` 正常；
- `ELECTRON_RUN_AS_NODE=1`（Node 模式）正常；
- **Edge Chromium `--headless=new` 正常**；
- `--js-flags=--jitless`、`--disable-features=V8Sandbox` 仍崩；
- `app.disableHardwareAcceleration()` 仍崩；
- 进程在 `WinSta0\Default`、Session 1，不是 Session 0；
- `Get-ProcessMitigation -System` 所有项为 0（没有 ACG/CFG/DynamicCode 限制）；
- VBS 运行中，SecurityServicesRunning = {2, 7}（HVCI 等）；
- GPU：Intel Arc + **ToDesk Virtual Display Adapter**；
- Windows：11 家庭版 10.0.26200；
- WER 加载模块里没有第三方 DLL，故障模块就是 `electron.exe`。

结论：**不是我们的代码，也不是窗口创建逻辑；是 Electron 运行时在本机启动阶段就崩。**
精确到指令级需要 Electron 符号 + 调试器；当前没装。

### 重启后复测（2026-11-09 02:01）

- 电脑重启后，Electron 45.2.3 仍然崩；
- 最小窗口：`0xC0000005`；
- 不加 `--no-sandbox` 时退出码是 `0x80000003`（breakpoint），加 `--no-sandbox` 后是 `0xC0000005`（access violation）；
- Electron 46.0.0-alpha.7 也崩；
- Edge Chromium 仍然正常。

**重启无效，排除“临时进程状态”这类原因。**

最可能的方向：

1. Electron runtime 与 Windows 11 26200 / HVCI 的兼容问题；
2. ToDesk 虚拟显示/远程会话对 Electron 启动的干扰；
3. 本机 Windows 组件/策略问题（Edge 不受影响，所以不是“Chromium 全坏”）。

最快验证：换一台机器/VM/Windows Sandbox 跑同一份 `electron`。如果那边正常，就是本机环境问题。

## 5. 需要用户做什么

在**另一台普通 Windows 机器/桌面会话**跑：

```powershell
cd p0-coordinator/electron
$env:ELECTRON_RUN_AS_NODE=$null
npm start
```

或者给出本机 Windows 版本/安全策略信息，我继续查。

## 6. 对试点的意义

- 代码侧 Electron 壳已完成（main/utilityProcess/IPC/preload/renderer）；
- Node CLI 桥冒烟通过；
- 但“真实窗口”这一 Gate 在本机无法通过；
- 不能把 Electron 标为“已验证”。
