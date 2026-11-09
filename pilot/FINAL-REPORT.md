# B 内部试点：终版报告（2026-11-08）

## 0. 结论

**治理内核：成功。**
**产品化：未完成。**

- 91 个单元测试全绿；
- 真实 DSH 5 个任务（3 成功 / 1 越界拦截 / 1 无改动拦截）；
- 真实 Codex（DeepSeek provider）1 个任务；
- 真实 3-agent 并发 2 组（demo 仓库 + 多模块 `sample-repo`），全部合入；
- 共 **15+ 次真实 agent 执行**，0 次越界合入；
- 真实 usage 已采集，cost 按 DeepSeek 官方价目表估算约 **¥0.0305**；
- **Electron 真窗口仍失败**：17/22/28/45/46-alpha 都崩 `0xC0000005`，重启后仍复现；**已用 WebView2 壳替代并跑通窗口/renderer/RPC**（见 [webview2/README.md](../p0-coordinator/webview2/README.md)）；
- **Codex App Server 已接成 adapter 并跑通 V1 端到端**（JSON-RPC → 改文件 → Candidate → 真实 cherry-pick → accepted）；
- **`finish_merge` 仍是规则层**；`merge_candidate_git()` 已能真实 cherry-pick + 检查 + 回滚，并已接 CLI（见 [INTEGRATION.md](INTEGRATION.md)），但还没接 UI。

**建议：B 试点判为“有条件成功”，可以进入受限内部试用，但不能对外宣称 V1 完成。**

---

## 1. 证据

| 证据 | 内容 |
| --- | --- |
| [pilot/REAL-DSH.md](REAL-DSH.md) | DSH 单任务 V1 端到端 + usage |
| [pilot/BATCH-DSH.md](BATCH-DSH.md) | DSH 5 任务：3 成功 / 2 拦截 |
| [pilot/REAL-CODEX.md](REAL-CODEX.md) | Codex + DeepSeek V1 端到端 |
| [pilot/CODEX-APPSERVER.md](CODEX-APPSERVER.md) | Codex App Server JSON-RPC smoke（真实改文件） |
| [pilot/REAL-APPSERVER.md](REAL-APPSERVER.md) | App Server adapter + V1 端到端 + 真实 cherry-pick |
| [pilot/org-visual.png](org-visual.png) | 组织协调与分工一页图 |
| [pilot/ui-dashboard.png](ui-dashboard.png) | 新版 WebView2 控制台截图 |
| [pilot/ROADMAP-V2.md](ROADMAP-V2.md) | V2 路线图 |
| [pilot/REAL-REPO-V2.md](REAL-REPO-V2.md) | 真实仓库试点证据 |
| [pilot/OR-ROUTER.md](OR-ROUTER.md) | OR 运行时 agent 推荐 |
| [pilot/ROLES.md](ROLES.md) | 审批角色与旧库迁移 |
| [pilot/ELECTRON-BLOCKED.md](ELECTRON-BLOCKED.md) | Electron 17/22/28/45/46a 崩溃证据与排除项 |
| [p0-coordinator/webview2/README.md](../p0-coordinator/webview2/README.md) | WebView2 壳：Start Run 真启 Codex + Approve/Merge |
| [pilot/REAL-MULTI.md](REAL-MULTI.md) | 3-agent 并发（demo） |
| [pilot/REAL-REPO.md](REAL-REPO.md) | 3-agent 并发（多模块 sample-repo） |
| [pilot/INTEGRATION.md](INTEGRATION.md) | 三个 Candidate 的真实 Git cherry-pick |
| [pilot/COST.md](COST.md) | 官方价目表 + usage 的 cost |

---

## 2. 真实执行（主要批次）

| 批次 | agent | 任务 | 结果 |
| --- | --- | --- | --- |
| DSH 单任务 | DSH | add mul | accepted |
| DSH 批量 | DSH | add sub | accepted |
| DSH 批量 | DSH | 越界 other.txt | blocked / needs_fix |
| DSH 批量 | DSH | 无改动 | blocked / needs_fix |
| DSH 复跑 | DSH | add mul | accepted |
| Codex 单任务 | Codex-DeepSeek | add mul | accepted |
| 3-agent demo | DSH | src/a.py | accepted |
| 3-agent demo | Codex-DeepSeek | src/b.py | accepted |
| 3-agent demo | Codex-智谱 | src/c.py | accepted |
| 多模块仓库 | DSH | pricing.py | accepted |
| 多模块仓库 | Codex-DeepSeek | cart.py | accepted |
| 多模块仓库 | Codex-智谱 | formatting.py | accepted |

**成功合入：10；正确拦截：2；越界合入：0。**

---

## 3. Gate 结果

### Gate 1：真实接入 + V1 端到端

```text
[✓] DSH 真跑通
[✓] Codex 真跑通（DeepSeek provider，不是 OpenAI 账号）
[✓] 一条 V1 端到端（execution → candidate → evidence → approve → merge → accepted）
[✓] 越界/无改动路径能挡住
[✓] sample-repo 三个 Candidate 真实 cherry-pick，集成测试 3 OK
[✓] Codex App Server initialize/thread/turn + 真实改文件 + V1 端到端
```

### Gate 2：Electron + 真实成本 + 3 个任务

```text
[✓] WebView2 桌面壳（任务表 + Start Run + Approve + Merge，`WEBVIEW2_SMOKE` / `--call` 通过）
[✗] Electron 真窗口（17/22/28/45/46-alpha 都崩 0xC0000005；重启后仍崩）
[✓] 真实 usage 采集
[✓] cost 估算（不是账单）
[✓] 3+ 个任务
```

### Gate 3：试点退出

```text
[✓] ≥5 个真实任务
[✓] 0 次越界合入
[✓] 100% candidate 绑定 SHA
[△] 人工验收：脚本 reviewer，不是真人
[✗] 进程被杀后的恢复：未系统验证
[✗] 操作员能从 UI 解释“卡在哪”：还没有 UI
```

---

## 4. 现在能做什么

- 三个 agent（DSH / Codex-DeepSeek / Codex-智谱）并发改不同文件；
- 文件预约 + worktree 防止互相覆盖；
- Candidate 绑定 SHA，evidence 绑定 candidate；
- 人工批准后才 merge；merge 成功才 accepted；
- 越界/无改动会被挡住；
- usage 真实记录，cost 可按官方价目表估算；
- 91 个单元测试 + CLI/MCP 入口。
- WebView2 桌面壳：任务表、Start Run、Approve、真实 Merge。

## 5. 现在不能做什么

- **DSH 已用隔离 `DSH_HOME` 在 UI 子进程启动**：Codex-DeepSeek 与 DSH 的 Start Run 都已实测；
- **Electron 没有，WebView2 有**：Electron 窗口起不来；WebView2 壳已通过 smoke，并已接 Start Run/Approve/Merge；
- **真实 Git 合入还没接 UI**：`merge_candidate_git()` + CLI 已有，`finish_merge` 仍只更新 DB；
- **没有真实人类审批台**：reviewer 是脚本；
- **App Server 的文本提取/中断/审批请求没有完整做**：目前靠文件/scope 验证；
- **没有真实小仓库的复杂任务**：`sample-repo` 仍是 3 个小模块；
- **没有跨机/多用户/ACL**；
- **没有账单级 cost**：cache 口径未核对。

---

## 6. 关键风险

1. **把 Git 合入接进主流程**
   - `merge_candidate_git()` 已有真实 cherry-pick/检查/回滚测试；
   - 还差 `finish_merge` 自动调用它，以及 UI 入口；CLI 已有 `merge-candidate-git`。

2. **Electron 不是代码问题，是环境问题**
   - 17/22/28/45/46-alpha 都崩，计划任务 `/IT` 也崩；
   - 事件日志显示 electron.exe 本体 `0xC0000005`；
   - 已用 WebView2 壳替代，桌面能力不再被 Electron 阻塞；
   - Electron 仍需换机器/桌面会话才能验证。

3. **App Server 的交互能力**
   - initialize/thread/turn/改文件/合入已通；
   - 还没做 turn/interrupt、审批请求、文本增量提取。

4. **成本是估算**
   - DSH cache 口径不一致；
   - 智谱是套餐，不是 token 账单。

5. **样本太小**
   - 这些执行都是“加一个函数”；
   - 没有依赖、冲突、回滚、失败重试。

---

## 7. 建议

### 可以做的

- 以 **DSH + Codex CLI（DeepSeek/智谱）** 进入受限内部试用；
- 用真实小仓库跑 5–10 个跨文件任务；
- 补真实 Git merge；
- 补真人 reviewer；
- 在普通桌面验证 Electron。

### 不要做的

- 不要对外说“V1 完成/生产可用”；
- 不要继续加 MILP/Pareto/A2A；
- 不要把规则层 merged 当成真实合入；
- 不要在 Electron 未验证前做 UI 功能。

---

## 8. 下一步（两周）

1. **自动 Git 合入**：把 `merge_candidate_git()` 接进 `finish_merge`（CLI 已有，cherry-pick + 检查 + 回滚测试已有）；
2. **桌面壳**：WebView2 已通；Electron 可选，需换机器验证；
3. **真人 reviewer**：至少 1 个真人签一次；
4. **真实小仓库**：5–10 个跨文件任务；
5. **cost 对账**：拿 DeepSeek 账单核对 cache 口径；
6. **App Server 多 thread / 中断 / 审批**：adapter 已有，补交互能力。

---

## 9. 安全提醒

DSH/智谱 key 已在对话中出现过，建议试点结束后轮换/吊销；`CODEX_HOME` 里的 key 不要提交到仓库或共享包。
