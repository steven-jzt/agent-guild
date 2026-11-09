# Agent Guild V2 路线图

> 目标：把当前“能跑通的协调内核 + 合入闭环”，推进到“真实仓库可重复试点，再往上长 OR、A2A、动态组织、文档与学习”。
> 时间按 1 人 + AI 助手估算，不是对外承诺；每阶段以验收标准为准，不以日期为准。

## 0. 当前基线（2026-11-09）

已完成：

- P0 协调器：SQLite + MCP + CLI；
- 任务 / 写集 / 依赖 / 原子 claim / TTL 租约 / fencing / 事件；
- runs 遥测 / 成本估计 / MILP / 2D+3D Pareto / 预算；
- 自动验证 / N-of-M 审批 / 计划回写 / 预留；
- V1 Core 六块：Run、文件预约、Candidate/Merge、Worktree/越界 diff、Task lifecycle、双重门；
- 真实 Git cherry-pick + 回滚；
- Adapter：DSH / Codex-DeepSeek / Codex-智谱 / Codex App Server；
- WebView2 控制台：启动 Run / 批准 / 合入 / 可视化；
- 73 个单元测试；多次真实 agent 执行。

当前主要缺口：

- 试点仓库还是 `.tmp` 小 demo；
- 合并前有检查，**合并后检查还没接到 UI**；
- 任务 checks 没有成为主流程的一等公民；
- OR 还是离线分析，没有进入实时调度；
- A2A 只是“Coordinator 中介的间接协作”；
- 动态组织只有分工展示，没有自动组队；
- 文档内核、学习闭环未落地。

---

## 1. 推进顺序

```text
Phase 1  真实仓库 + 合并后检查 + 可重复报告
   ↓
Phase 2  任务级 checks + 自动验收 + 轻量身份/角色
   ↓
Phase 3  OR 进主循环：按成本/质量/时间选 agent
   ↓
Phase 4  A2A 消息总线 + 动态组织
   ↓
Phase 5  文档内核 + 学习/质量预测
```

顺序理由：

1. 先把真实代码 + 真实验收跑通；
2. 再把验收变成任务定义的一部分；
3. 有稳定数据后，OR 才有意义；
4. 有稳定调度后，A2A/动态组织才有价值；
5. 文档与学习是上层增强，不应先于地基。

---

## 2. Phase 1：真实仓库试点（已完成）

> 完成记录：[REAL-REPO-V2.md](REAL-REPO-V2.md)

### 目标

在一个**多文件、带测试、非 demo** 的仓库上跑完整链路：

```text
建任务 → 预约两个文件 → worktree
→ 真实 agent 改代码 + 测试
→ scope 检查
→ Candidate
→ 检查通过 → Evidence
→ 人工批准
→ 合入 integration
→ 合并后检查
→ integration 测试通过
→ accepted
```

### 交付物

- `pilot/real-repo-template/`：真实小仓库模板（源码 + 测试 + 检查脚本）；
- `pilot/seed_real_repo.py`：初始化真实试点仓库/任务/workspace；
- WebView2 合入时带 `--check`，合并后检查失败会回滚；
- `pilot/REAL-REPO-V2.md`：完整证据、命令、SHA、结果、失败记录；
- 共享包更新。

### 验收标准

- [x] 仓库至少 3 个源文件 + 1 个测试文件；
- [x] 任务一次修改 **两个预约文件**（源码 + 测试）；
- [x] 任务开始前，`check.py` 必须失败；
- [x] agent 完成后，`check.py` 通过；
- [x] scope 无越界；
- [x] Candidate 绑定 SHA + evidence + author execution；
- [x] 批准后合入 integration；
- [x] 合并后检查通过；
- [x] integration 里独立跑测试通过；
- [x] 至少一次“检查失败 → 不合入”的负向验证。

### 风险

- agent 只改源码不改测试 → check 失败，候选停在 validating；
- 检查脚本本身写错 → 需要人工 review；
- 真实仓库比 demo 更容易出现跨文件依赖。

---

## 3. Phase 2：任务级 checks + 自动验收（基本完成）

### 目标

把“检查命令”从 UI 输入框，升级成**任务定义的一部分**。

### 已完成

- `start-agent` 在未显式传 `--check` 时，自动读取任务 checks 的第一条 command；
- WebView2 的「启动 Run」会优先用任务 checks，输入框只作覆盖；
- UI 不再强制每次手填检查命令；
- 无 passing evidence 不允许批准（`approve_candidate` 显式检查）；
- 合入前后都使用检查命令；
- 审批角色落库：`reviewer` / `admin`，写入 `approver_role`；
- 项目级策略：`required_approver_role=admin` 时，reviewer 不能批准，合入前再校验；
- 多级审批链：`approval_chain="reviewer,admin"`，reviewer 批准后等待 admin；
- 语义级 MVP：AST 变更事实、影响面、ACK、breaking 必须 admin；
- 组织级 MVP：Agent 能力标签落库，OR 支持 `--require-tag`；
- 旧库自动迁移；
- 证据：[ROLES.md](ROLES.md)、[SEMANTIC-ORG-VERIFY.md](SEMANTIC-ORG-VERIFY.md)。

### 待完成

- UI 展示审批链进度；
- 真实身份认证（本地账号/SSO），当前只是审计标签。

---

## 4. Phase 3：OR 进主循环（进行中）

### 目标

让 OR 从“离线算例”变成“运行时选 agent / 排任务”的依据。

### 已完成的第一步

- `or_router.py`：按 `cost` / `time` / `quality` 推荐 agent；
- 历史 runs 覆盖默认画像，预算约束过滤不可行项；
- CLI `recommend-agent`；
- `start-agent --agent auto` 自动使用推荐；
- WebView2 Agent 下拉新增 `Auto（OR 推荐）`；
- runs 开始按 token 估算 `cost_usd`，后续预算/推荐可用；
- 批量分配：`recommend-batch`，每个 agent 同时只接一个任务，预算逐项扣减。

证据：[OR-ROUTER.md](OR-ROUTER.md)

### 待完成

- 带依赖/文件冲突的全局排期；
- 预算约束下的组合优化；
- 从历史学习成功率/时延；
- UI 展示推荐对比和原因。

### 交付物

- 基于历史 runs 的 agent 成本/质量/时延画像；
- 任务分配建议：成本优先 / 时间优先 / 质量优先；
- 预算约束下的调度；
- UI 展示“为什么选这个 agent”。

### 验收标准

- 同一批任务，在预算约束下给出不同 agent 分配；
- 建议可解释（成本、历史成功率、时延）；
- 超预算时拒绝启动，而不是事后才发现。

---

## 5. Phase 4：A2A + 动态组织

### 目标

从“Coordinator 中介”升级到“agent 之间可协商”，但仍受协调器审计与边界约束。

### 交付物

- Agent 消息总线（请求帮助 / 交接 / 评审）；
- 任务可拆分、可转交；
- 动态角色：author / reviewer / fixer；
- 组织视图反映真实任务关系，而不是静态图。

### 验收标准

- 两个 agent 可以围绕同一任务协商，不绕过文件预约；
- 转交后责任链可追溯；
- 动态组队不破坏 scope / 审批 / 合入门禁。

---

## 6. Phase 5：文档内核 + 学习

### 目标

让文档成为一等公民，并让历史运行产生可复用的质量判断。

### 交付物

- 文档/决策记录与任务、Candidate、Merge 关联；
- 失败模式库；
- 基于 runs 的质量/风险预测；
- 审批辅助：哪些候选值得重点看。

### 验收标准

- 每个 accepted 任务都有可追溯的决策记录；
- 失败模式能回流到下一次任务提示/检查；
- 质量预测至少能区分“高风险候选”，不追求全自动。

---

## 7. 现在这一轮只做 Phase 1

本轮明确不做：

- 多用户/远程；
- A2A 消息总线；
- 动态组织；
- 文档内核；
- 学习模型；
- OR 在线调度。

本轮只回答一个问题：

> **在一个真实多文件仓库上，这套“预约 → worktree → agent → scope → evidence → 人审 → 真合入 → 合并后检查”能不能稳定跑通？**

跑通后再决定 Phase 2 的具体优先级。
