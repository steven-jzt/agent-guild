# B 内部试点：三周计划

> 目标：**1 台 Windows、1 个操作员、1 个真实小仓库、2 个真实 agent（Codex + DSH API Key）、5–10 个任务**，跑通“派遣 → worktree → 候选 → 人工验收 → 合入”的治理闭环。
>
> 当前起点：66 个单元测试全绿；V1 规则层六块里五块有代码 + 测试；Adapter/Electron 有合同/壳，未验收。

---

## 1. 边界

### 做

- Codex App Server 真接入；
- DSH API Key 真接入（DSH 账户并行验证，未过不宣称双认证达标）；
- 真实 git worktree；
- Candidate / Evidence / Merge；
- Electron 最小可用：项目/任务板、启动/停止、候选/验收/合入、事件日志；
- 真实 token/cost；
- 一页 Runbook + 试点报告。

### 不做

- 远程多人、账号体系、ACL；
- 自动选人/自动排期；
- MSI 安装包；
- Postgres / 跨机；
- Document Kernel / A2A；
- 继续加 MILP/Pareto/门控。

---

## 2. 三周排期

### 第 1 周：M0 + 一条真实端到端

| 天 | 任务 | 验收 |
| --- | --- | --- |
| D1 | Codex App Server 启动命令、版本、认证 | `probe --name codex` available=true |
| D2 | Codex start/events/stop + 进程清理 | 一个真实任务能启动、收事件、停干净 |
| D3 | DSH API Key probe/start/stop | 同上，API Key 模式 |
| D4 | 真实 worktree + 越界检查 + candidate | 改一个未预约文件会被拒 |
| D5 | 一条完整 V1：execution → candidate → evidence → approve → merge | 不手工改 DB，SHA 全对得上 |

**Gate 1**：

```text
[ ] Codex 真跑通
[ ] DSH API Key 真跑通
[ ] 一条 V1 端到端跑通
[ ] 越界/测试失败路径能挡住
```

### 第 2 周：Electron + 真实成本 + 试点任务

| 天 | 任务 | 验收 |
| --- | --- | --- |
| D6 | `npm install` + 窗口打开 | 能看到项目/任务 |
| D7 | IPC：启动 Run / 停止 | renderer 不能直接执行命令 |
| D8 | IPC：候选/批准/合入 | 一次完整操作从 UI 完成 |
| D9 | 真实 token/cost + 预算 | 一次 run 的 cost 与厂商日志对上 |
| D10 | 跑 3 个试点任务 | 成功/失败/越界各一条 |

**Gate 2**：

```text
[ ] Electron 真窗口可用
[ ] 一个写操作从 UI 完成
[ ] 真实 cost 不为 0
[ ] 3 个试点任务有审计记录
```

### 第 3 周：试点 + 加固 + 复盘

- 跑 5–10 个真实任务（小仓库，非关键代码）；
- 记录：成功率、派到合入时间、越界拦截、人工干预、恢复时间；
- 修 3 个最高频问题，不扩散范围；
- 写试点报告。

**Gate 3**：

```text
[ ] ≥5 个真实任务完成
[ ] 0 次越界合入
[ ] 100% candidate 绑定 SHA
[ ] 100% 人工验收 + 单独合入确认
[ ] 至少一次进程被杀后的恢复
[ ] 操作员能从 UI 解释“现在卡在哪”
```

---

## 3. 角色

- **Operator**：建项目/任务、启动 Run、处理提问；
- **Reviewer**：验收候选、确认合入；
- 可以是同一人，但两个动作分开记录；
- 每天 15 分钟站会：昨天完成、卡点、是否触发 Gate 失败。

---

## 4. 成功指标

| 指标 | 目标 |
| --- | --- |
| 真实任务完成率 | ≥ 70% |
| 越界合入 | 0 |
| Candidate SHA 绑定率 | 100% |
| 人工验收 + 合入确认 | 100% |
| 进程/核心崩溃恢复 | 至少 1 次成功 |
| 操作员理解成本 | 能独立解释任务状态 |
| 单任务人工干预 | ≤ 2 次 |

---

## 5. 风险与应对

| 风险 | 应对 |
| --- | --- |
| Codex 版本/命令不稳定 | 固定版本；probe 记录版本；失败不降级 |
| DSH SDK 没有 headless 入口 | 只用官方 SDK；不拿交互式 CLI 冒充 |
| DSH 账户认证阻塞 | API Key 先跑；账户并行验；不宣称双认证达标 |
| Electron 装不上 | 换镜像/离线包；装不上退回 CLI 试点，不假装桌面通过 |
| 进程清理不干净 | Job Object + 进程树检查；杀不干净就冻结资源 |
| 范围蔓延 | 第 1–3 周冻结新功能，只修试点阻塞 |

---

## 6. 交付物

- `pilot/PLAN.md`（本文件）；
- `pilot/RUNBOOK.md`；
- `pilot/run_pilot.py` dry-run 脚本（规则层验证，不是 M0）；
- 真实 M0 证据：Codex / DSH API Key 的 probe/start/stop 日志；
- 一次真实 V1 端到端记录；
- Electron 截图/日志；
- 试点报告：指标、问题、是否进入产品化。
