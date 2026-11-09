# Marvis（腾讯马维斯）拆解与借鉴

**日期**：2026-11-09  
**对象**：腾讯 Marvis（Windows 安装器 + 官方 Android APK）  
**方法**：公开产品文档 + 安装器/APK 静态拆包 + 字符串与资源分析  
**边界**：未执行安装器、未登录、未抓包、未绕过任何授权/支付/安全机制；不做代码复制，只做架构与产品分析。

---

## 0. 结论

Marvis 值得借鉴的不是“它也有 6 个 Agent”，而是它把下面几件事做成了产品：

1. **按资源域拆分 Agent**：File / Computer / App / Browser / Search，而不是按模型拆分；
2. **主 Agent 统筹 + 实时工作日志**：用户能看到“哪一步、谁在做、中间结果、最终产物”；
3. **风险分级与硬垂询**：高风险动作先给计划、再要求确认；
4. **端云/本地双模式**：效率模式端云协同，本地模式数据不出机；
5. **MCP / Skill 作为一等公民**：MCP 服务、OAuth、Skill Store、Skill Case；
6. **CodeAgent + 云端 relay**：代码 Agent 会话、权限、审批恢复、工作区、文件传输、WebSocket 隧道；
7. **预置团队 + 零配置**：开箱即用，同时保留 MCP/CLI/自定义 Skill 的扩展口。

对 Agent Guild 的启发：我们已经有“协调 + 门禁 + 真实合入”的硬内核，下一步最该补的是 **过程可见性、风险分级、MCP/Skill 扩展面、执行位置策略**，而不是继续堆 Agent 数量。

---

## 1. 产品事实（公开资料）

- 腾讯系统级 AI 助手，中文名“马维斯”，2026 年 5 月上线；
- Windows / macOS / Android / iOS / HarmonyOS 多端；
- 预置 6 个 Agent：主 Agent、File、Computer、App、Browser、Search；
- 主 Agent 拆解任务并调度，多个 Agent 可并行；
- 过程可见：步骤、执行 Agent、中间结果、最终产出；
- 高风险动作“硬垂询”：删除文件、修改系统核心配置等先确认；支付类必须本人完成；
- 效率模式：端云协同；本地/隐私模式：端侧模型，文件与对话不上云，可断网；
- 支持自定义 MCP / CLI、导入自定义 Skill；
- 每用户每天 1000 万 Token 免费额度；
- 2026 年 9 月定位从“AI 助手”升级为“AI 管家”，围绕电脑、文件、浏览器、软件四大管家能力。

来源：

- [Marvis 官网](https://marvis.qq.com)
- [产品认知与定位](https://marvis.qq.com/docs/product-description)
- [多 Agent 体系](https://marvis.qq.com/docs/agent-system)
- [隐私与数据安全](https://marvis.qq.com/docs/data-and-security)
- [新浪科技：系统级 AI 助手 Marvis](https://finance.sina.cn/2026-05-21/detail-inhysain5855797.d.html?vt=4)
- [CNMO：全端布局落地](https://ai.cnmo.com/news/820364.html)
- [腾讯云开发者：Marvis 保姆级教程](https://cloud.tencent.cn/developer/article/2693737)

---

## 2. 静态拆解证据

### 2.1 Windows 安装器

从官网 `https://marvis.qq.com/download/exe` 下载：

| 项 | 值 |
|---|---|
| 文件名 | `marvis_4100100002_1.0.0.44_x64_6373.exe` |
| 大小 | 8,669,224 bytes（约 8.3 MB） |
| SHA256 | `E57694CA6EB2DF00506337F3DB6C73817544E6E3ADEB8184FBB4C62A5B6B1080` |
| 文件版本 | 1.0.0.44 |
| 产品/公司 | Marvis / Tencent |
| 数字签名 | **Valid**，Tencent Technology (Shenzhen) Company Limited |
| 证书 | DigiCert Trusted G4 Code Signing RSA4096 SHA384 2021 CA1 |

安装器字符串里出现了：

```text
MarvisInstaller
androws://market/install?pkgname={}
androws://market/install?downloadUrl={}&source={}
androws://pcyybsdk/localInstall?path={}
androws://app/install?pkgname={}
androws://client/refreshInstalledAppList
not install marvis
already install fully yyb. no actual install
alreay install engine. target is engine. no actual install
F:\workplace\Androws\...\src\androws_install\src\main.cc
https://yybadaccess.3g.qq.com/v3/marvis_get_install_source
```

推断（不是官方声明）：

> Windows 端不是单一大体积 Electron 应用，而是一个 **Androws/应用宝系安装器**：先准备 Android-on-Windows 运行引擎，再通过 `androws://` 协议安装/拉起 Marvis。  
> 这解释了为什么官网 Windows 安装包只有 8 MB 左右。

### 2.2 Android APK

官方 Android 1.3.5 APK：

| 项 | 值 |
|---|---|
| 包名 | `com.tencent.android.marvis` |
| 大小 | 64,996,694 bytes（约 62 MB） |
| SHA256 | `3E2526C7EFF1461E28A32704B7E5808B5F09B7F99CB69AFC2948F8DC170AF18D` |
| 结构 | 4 个 `classes*.dex`，1843 个 zip 条目 |
| 主要原生库 | Bugly、Mars xlog、QIMEI、TPNS/HMS push、视频、安全风控、MMKV |
| UI 技术 | Android 原生 + Jetpack Compose + **Kuikly** 动态页面 |
| 动态页面 | `assets/kuikly/` 下 24 个页面 APK |
| 框架 | 未发现 Flutter / React Native / Electron |

Kuikly 页面名单很能说明产品面：

```text
AboutPage
AutoTaskPage
NewTaskPage
SessionOutputList
UserOutPutList
MySkill
SkillStore
SkillStoreSearchPage
SkillDetailPage
SkillCaseStore
SkillCaseDetailPage
SkillForConversationPager
McpServiceDetailPage
PersonaSecondaryPage
device_manager
NotificationPermissionPage
...
```

关键 Activity / 组件：

```text
MarvisMainActivity
MarvisSecondaryActivity
MarvisOfficeActivity
AgentBridgeActivity
AgentBridgeOutputsActivity
McpConnectionSheetActivity
McpOAuthBrowserActivity
ScheduleExecuteActivity
InstallResultReceiver
KuiklyActivity
```

关键字符串 / 类名：

```text
openclaw_*                          # 内部 Agent/工作流 UI 命名
CodeAgentProtocolClient
CodeAgentSessionPermissions
CodeAgentApprovalRecoveryEvidence
CodeAgentWorkspaceDiagnostics
CodeAgentDownloadManager
CodeAgentFileSink
CodeAgentProviderCatalog
GatewayCodeAgentTransport
CloudMarvisManager
switchToCloudMarvis
allocateCloudMarvisWithRetry
cloud_marvis_relay_poll_interval
CodeAgent Tunnel frame
WebSocketListener / RealWebSocket
/v3/marvis_get_mcp_service_detail
/v3/marvis_get_mcp_services_by_names
/v3/marvigo_allocate_cloud_marvis
/v3/marvigo_marvis_get_relay_info
/v3/marvigo_release_cloud_marvis
```

权限 / 声明（节选）：

```text
INTERNET
ACCESS_NETWORK_STATE / ACCESS_WIFI_STATE
RECORD_AUDIO
POST_NOTIFICATIONS
REQUEST_INSTALL_PACKAGES
DUMP
GET_TASKS
FOREGROUND_SERVICE
SCHEDULE_EXACT_ALARM
```

值得注意：manifest 中没有看到 `BIND_ACCESSIBILITY_SERVICE` 或 `CAPTURE_VIDEO_OUTPUT` / `MediaProjection` 的声明。  
这说明它的 GUI 操作更可能发生在 **PC 宿主/Android 运行引擎/云端 relay** 侧，而不是简单依赖 Android 无障碍服务；这一点是推断，未做运行时验证。

### 2.3 关键发现

1. **Windows 端是“Android 应用 + Androws 运行引擎”的组合**，不是 Electron；
2. **UI 是原生 Android + Compose + Kuikly 动态页面**，页面可按 APK 粒度更新；
3. **内部有 OpenClaw / CodeAgent / CloudMarvis 三层命名**：
   - OpenClaw：Agent/任务/技能相关 UI 和工作流；
   - CodeAgent：代码 Agent 会话、权限、审批恢复、工作区、文件传输；
   - CloudMarvis：云端 Marvis 分配、relay 信息、WebSocket 隧道；
4. **MCP 与 Skill 是产品级入口**：MCP 服务详情、OAuth 浏览器、Skill Store、Skill Case、MySkill；
5. **AutoTask / Schedule**：自动任务、定时执行、任务卡片；
6. **虚拟办公室**：MarvisOffice、worklog、token 消耗、Agent 工位；
7. **商业/配额层**：token 余量、订单、优惠券、支付/地址等。

---

## 3. 推断架构

```text
┌────────────────────────────────────────────┐
│ Android / Windows 客户端                    │
│  Native Android + Compose + Kuikly 页面     │
│  Marvis Office / Skill Store / MCP UI       │
└───────────────┬────────────────────────────┘
                │ WebSocket / relay / gateway
┌───────────────▼────────────────────────────┐
│ CloudMarvis / Gateway                       │
│  会话分配、relay、Tunnel、云端模型/工具       │
└───────────────┬────────────────────────────┘
                │
┌───────────────▼────────────────────────────┐
│ PC Host / Androws Engine / CodeAgent        │
│  文件 / 系统 / App / Browser / Code 工作区   │
│  Session Permissions / Approval Recovery    │
└────────────────────────────────────────────┘
```

几个设计点：

- **控制面与执行面分离**：客户端负责交互与可视化，执行在宿主/云端；
- **relay + WebSocket**：跨端/远程连接不是直连，而是云端 relay；
- **CodeAgent Tunnel**：代码 Agent 有独立传输帧、预算、网关校验；
- **Session Permissions**：权限是“会话级”的，不是全局一次授权；
- **Approval Recovery**：断线/重连后审批状态可恢复；
- **Cloud/Local 切换**：同一任务可在 cloud Marvis 与本地 Marvis 之间切换；
- **Kuikly 动态页面**：UI 可按页面热更新，降低发版成本。

---

## 4. 与 Agent Guild 的对比

| 维度 | Marvis | Agent Guild |
|---|---|---|
| 定位 | 消费级系统 AI 管家 | 开发团队的多 agent 协调/合入控制面 |
| 作用域 | 整台电脑/手机/跨端 | 代码仓库/任务/文件范围 |
| Agent 分工 | 按资源域：File/Computer/App/Browser/Search | 按 coding 角色/模型：DSH/Codex/… |
| 协调方式 | 主 Agent 拆解 + 调度 | Coordinator + 文件预约 + worktree |
| 过程可见 | 工作日志、步骤、中间结果、虚拟办公室 | 任务/候选/流水线，缺逐步执行时间线 |
| 风险控制 | L2 硬垂询、授权、支付本人 | scope/evidence/approval/admin/多级链 |
| 扩展 | MCP / CLI / Skill Store / 自定义 Skill | Adapter/CLI/MCP 有，但无 Skill/MCP 市场 |
| 执行位置 | 云端/本地双模式 | 单机；OR 按成本/时间/质量选 agent |
| 远程 | 手机接管电脑、relay | 单机 UI |
| 商业/配额 | token 配额、订单/支付 | 项目预算、成本估算 |
| 开源/可审计 | 闭源、云 relay | 本地代码、可审计 |

**关系**：不是直接竞品。  
Marvis 更像“宿主 + 执行面 + 消费级入口”；Agent Guild 更像“代码变更的协调与门禁层”。两者可以互补：

- Agent Guild 作为 **MCP server / CLI 工具** 接入 Marvis，让 Marvis 调“建任务/查候选/审批/合入”；
- 或 Agent Guild 把 Marvis 的 Browser/Search/File 能力作为 **外部工具**，补代码任务之外的调研与文档处理；
- 不建议直接逆向私有 API 去接；优先走官方 MCP/CLI。

---

## 5. 值得借鉴（按优先级）

### P0：过程可见性

Marvis 的“当前步骤 / 哪个 Agent / 中间结果 / 最终产物”四件事，正好补我们的 UI 短板。

Agent Guild 可加：

- 任务详情抽屉：计划步骤 + 每步状态 + 执行 Agent + 输入/输出；
- Run 时间线：开始、agent 事件、scope、check、candidate、approval；
- 中间产物：worktree diff、check 输出、evidence 链接；
- “为什么选这个 agent” 的 OR 解释。

### P0：风险分级 + 硬垂询

Marvis 的 L2 硬垂询不是“多一个弹窗”，而是按动作风险分级。

Agent Guild 可加：

- 动作风险级别：read / write / delete / network / merge；
- 高风险动作必须二次确认，并记录 `confirmed_by`；
- 命令执行前的 allowlist/denylist；
- merge 前展示 diff + check 结果 + 审批链；
- 删除/覆盖类操作单独策略。

### P0：Session Permissions

Marvis 的 `CodeAgentSessionPermissions` 很值得抄“概念”：

- 每次 Run 生成能力清单：可读路径、可写路径、可执行命令、网络权限、超时；
- 能力清单写入 Run/Execution，而不是散落在代码；
- 超出清单直接拒绝，不靠事后 scope 检查；
- 断线恢复后仍能恢复审批状态（Approval Recovery）。

### P1：MCP / Skill 扩展面

Marvis 把 MCP 服务、OAuth、Skill Store、Skill Case 做成了产品入口。  
Agent Guild 可以：

- 把 repo 工具（test、lint、git diff、search）注册成 MCP tools；
- 任务声明所需工具/权限；
- 预置 Skill 包：Python fix、test writer、dependency update、code review；
- 记录 skill 使用案例，形成团队可复用资产；
- 先做本地 registry，不做市场/支付。

### P1：执行位置策略

Marvis 的效率/隐私双模式，本质是“按任务选执行位置”。  
Agent Guild 的 OR 目前只有 cost/time/quality，可以加第四维：

```text
locality: local | cloud | hybrid
sensitivity: normal | sensitive
```

例如：

- 敏感仓库 → 强制本地 DSH/本地模型；
- 普通任务 → 云端 Codex；
- 预算优先 → 本地小模型；
- 质量优先 → 云端强模型。

### P1：定时/自动任务

Marvis 有 AutoTask、Schedule、任务卡片。Agent Guild 可先做：

- 每晚跑测试；
- 定时依赖检查；
- 定时抓取 issue/PR；
- 但所有代码修改仍走 Candidate + 人审。

### P2：预置团队 / 零配置

Marvis 的“开箱 6 个 Agent”降低上手门槛。Agent Guild 可做：

- `guild init`：识别仓库语言、生成默认 checks、创建 project/task 模板；
- 预置角色：Planner / Coder / Verifier / Reviewer；
- 默认安全策略、默认预算、默认审批链。

### P2：远程审批

Marvis 的手机接管电脑风险很高；Agent Guild 可以先做更低风险的：

- 手机/网页查看候选；
- 远程批准/拒绝；
- 不合入、不执行任意命令。

### P2：Token/预算配额

Marvis 每天 1000 万 Token。Agent Guild 已有项目预算，可补：

- 按用户/项目/agent 的日配额；
- 配额耗尽前预警；
- 预算与 OR 选择联动。

---

## 6. 不建议照搬

| 不建议 | 原因 |
|---|---|
| OS 级广泛权限、安装应用、改系统设置 | 爆炸半径太大，不是 Agent Guild 的域 |
| Android-on-Windows / VM 运行引擎 | 为 GUI 控制付出的复杂度极高，我们做代码协作不需要 |
| 虚拟办公室的强游戏化 | 容易让“好看”掩盖失败；我们需要审计日志优先 |
| 闭源云 relay | 企业/代码场景需要自托管、可审计、可断网 |
| 商业订单/优惠券/支付 | 与当前阶段无关 |
| 直接逆向私有 API 接入 | 法律/稳定性/封禁风险；优先 MCP/CLI 官方扩展 |
| 用 token 配额代替真实预算 | Token 不等于成本，也不等于质量 |

---

## 7. 给 Agent Guild 的具体落地建议

### 下一 sprint（1–2 周）

1. **Run 时间线 + 任务详情抽屉**
   - 复用现有 events/runs/evidence 数据；
   - UI 显示步骤、agent、耗时、token、check、candidate；
2. **动作风险分级**
   - 在 worker_cli 增加 `risk` 元数据；
   - 删除/覆盖/merge 前强制确认；
3. **Session Capability Manifest**
   - Run 记录 read/write paths、allowed commands、network；
   - scope 从事后检查变成事前约束 + 事后校验。

### 下一个 sprint（2–4 周）

4. **MCP tool registry**
   - 把 `run_tests`、`git_diff`、`search` 暴露为 MCP tools；
   - 任务声明工具权限；
5. **Skill 包**
   - 先做 3 个：Python fix、test writer、review；
   - 每个 skill 带 checks 和权限清单；
6. **Locality 策略**
   - OR 增加 local/cloud/hybrid；
   - 敏感仓库强制本地。

### 之后

7. `guild init` 零配置；
8. 远程只读/审批；
9. 预算配额；
10. 再考虑 A2A/动态组织。

---

## 8. 合法性与边界

- 本文只做**公开产品资料 + 安装器/APK 静态拆包**；
- 未执行安装器、未登录、未抓包、未绕过授权/支付/安全机制；
- 未复制 Marvis 代码、资源或提示词；
- 安装器/APK 仅存放在本地 `.tmp/marvis`，不进入共享包；
- 反逆向/破解可能违反腾讯服务条款；若要做深度互操作，优先走官方 MCP/CLI/开放接口。

---

## 9. 一句话总结

> Marvis 最强的地方不是“6 个 Agent”，而是 **把多 Agent 的执行过程、风险确认、端云位置、MCP/Skill 扩展做成了用户能看懂、敢授权的产品**。  
> Agent Guild 应该学它的“可见性 + 风险分级 + 扩展面”，而不是学它的 OS 权限和 Android VM。
