# Agent Guild 多 Agent 协作：成果共享包

> 2026-11-08。给 Agent Guild 团队/朋友的共享版本。
> 先读 `01-成果摘要.md`，再按兴趣看总报告和代码。

## 包内容

```text
AgentGuild-成果共享-2026-11-08/
├─ README.md                 # 本文件
├─ 00-发送文案.md             # 微信/邮件可直接复制
├─ 01-成果摘要.md             # 一页纸 + 关键结论
├─ 02-总报告.md               # 完整实验/设计/路线图
├─ 03-当前状态.md             # 91 个测试、能力、边界
├─ 04-实验与设计/             # 单篇实验与设计
├─ 05-代码/
│  └─ p0-coordinator/        # 协调器源码、测试、demo、live-demo 报告
├─ 06-其他实验代码/           # 并发/通信/A2A/运筹/组织/文档/学习
├─ 07-与AgentGuild-V1对比.md  # 对照 V1.0.1 计划的对齐/冲突/改造建议
└─ 08-试点/                   # B 内部试点：PLAN / RUNBOOK / REPORT / FINAL-REPORT / REAL-* / COST
```

## 先看什么

1. `01-成果摘要.md`（5–10 分钟）：我们验证了什么、做出了什么、还缺什么；
2. `02-总报告.md`（30–40 分钟）：完整论证；
3. `05-代码/p0-coordinator/README.md`：怎么跑协调器。

## 怎么跑

```powershell
cd 05-代码/p0-coordinator
python -m unittest test_coordinator test_estimate test_plan_milp test_verifier -v
python demo.py
python milp_demo.py
python approval_demo.py
python verification_demo.py
python smoke_cli.py
python smoke_mcp.py
```

依赖：Python 3.14；MILP 用 PuLP/CBC；其余纯标准库。

## 包里没有什么

- `a2a-lab/vendor/`（46MB 的 pip 产物）：没打进来；`06-其他实验代码/a2a-lab/` 里保留了源码和 README，需要时按说明重建；
- 各目录的 `.db`、`.tmp/`、`__pycache__`：运行时产物，没打进来；
- live demo 的 SQLite：可按 `live-demo/*/setup.py` 重建。

## 已验证

- 91 个单元测试全绿；
- 3 次真实 DSH subagent live demo：baseline / verified / governance（详见 `05-代码/p0-coordinator/live-demo/*/REPORT.md`）；
- Markdown 相对链接检查：0 broken。

## 反馈

欢迎拍砖，尤其是：

1. worktree 隔离 vs 同工作空间协调的边界；
2. 真实 token/cost 应该在哪一层采集；
3. 独立验证 + N-of-M 审批在小团队里是否过重。
