# P2-3：手动 Agent 审查与离线验收

交付日期：2026-11-13（Asia/Shanghai）。仓库：https://github.com/steven-jzt/agent-guild

分支：`codex/p2-3-agent-review`，基线为已合并 P2-2 的 main `4788e8673bc7e1849765363630382c0b40bc8dd3`。本轮提交独立 PR，等待人工验收后再合并。

## 从功能上理解

作者写完代码、测试通过后，候选停在“等待审查”。管理员点击“开始审查”，审查员在另一份候选副本中只读检查，再报告“通过”或“请求修改”，列出问题的等级、文件、行号和原因。通过后才进入待人工验收；批准和合入仍各自需要操作。

审查发现缺陷时，表示审查任务正常完成、候选没有通过，不能说作者执行失败。P1、P2 是阻塞缺陷，P3 是不单独阻塞的建议。重新审查会立即阻止沿用此前通过结果；重复点击不会创建第二个运行。

本轮验收全部使用明确标记的离线模拟 Agent，没有调用真实模型。模拟通过只对登记的临时隔离测试项目生效，普通项目拒绝使用模拟通道。登记入口不暴露给 CLI、MCP 或界面。

## 当前架构

沿用 Python + SQLite 协调内核、现有 Codex App Server 适配器和 WebView2 桌面壳。TypeScript 接入实验保持独立；本轮没有重写或统一运行时。

- `review_store.py`：原子创建 reviewer Execution、绑定候选 SHA/批次/检查项，插入 pending 证据，保存报告和结束状态。门禁检查最新审查的来源；中断收尾不会改变作者结果或任务生命周期。
- `agent_review.py`：独立克隆冻结候选，提供任务要求、完整差异和当前批次测试证据；实例参数强制 `read-only`，不改作者或全局配置。等待结束后检查副本内容、HEAD、refs 和本地配置，再校验结构化报告。
- `adapters.py`：复用现有 thread/turn 身份关联、正常终态判断和默认拒绝审批请求的通道；不自动重试。
- `review_privacy.py`：报告和原始协议事件保存前脱敏已识别的凭据字段、Bearer、常见密钥和 JWT。输入上下文也按相同规则脱敏。
- SQLite `review_runs` 与 Execution/Evidence 关联；磁盘产物位于数据库旁 `reviews/EXECUTION/report.json`、`events.json` 和独立 `workspace/`。错误报告同样保存绑定信息。界面显示最新报告、证据检查状态、审查进度和离线标识。
- 只有正常完成、报告有效且副本未变，才写通过证据；P1/P2 报告写失败门禁证据，但审查 Execution 为 succeeded。超时、启动失败、非法输出、身份错误、批次变化等均阻止放行。

差异超过 2 MiB 会拒绝本次审查，不静默截断。问题位置必须属于基线或候选树中的文件，行号为正整数；本轮未校验行号是否超出文件实际行数，也未限制必须落在变更行。

## 接口与操作

在 `p0-coordinator` 下执行，`DB` 和 `CANDIDATE` 替换为实际值：

```powershell
python -X utf8 worker_cli.py --db DB review-candidate --candidate CANDIDATE --check-id agent-review --timeout 180
python -X utf8 worker_cli.py --db DB reviews --candidate CANDIDATE
```

超时为大于 0、最多 1800 秒。批次由启动事务读取，不允许调用者指定其他批次。返回审查 Execution、报告位置、结论、候选状态及最新门禁。

MCP：`review_candidate(candidate_id, check_id, timeout)`、`list_reviews(candidate_id)`。桌面 RPC：`review_candidate`、`reviews`，采用 camelCase 参数。每个必需 review 检查项都有手动按钮；不在页面加载时启动审查。只有 frozen/validating 或待人工验收的候选允许开始/重新审查。

取消本地请求后，数据库可能仍为 running。刷新恢复面板，确认本地所属进程已退出，填写说明并通过既有 `reconcile_execution` 结束 reviewer；此时门禁继续阻止，然后才可显式再次审查。审查副本和报告保留供核对，不自动删除或回滚。超时不等于远端撤销成功。

## 验证结果

本轮分组验证覆盖 Python 225 项用例（原有核心/Git/进程/恢复 200 项、审查 19 项、真实窗口/native CLI 6 项），均取得通过结果；TS 19 项、桥接/界面逻辑 10 项及类型检查通过。先跑完整核心回归，再对后续修正补跑相关专项，窗口测试使用正常桌面权限运行。验收范围包含：

- 无缺陷通过后待人工验收；缺陷成功报告但禁止批准；P3 建议可通过。
- 无效 JSON、异常结束、错误 SHA/批次/turn、矛盾结论、副本修改、迟到报告不能放行。
- 重复点击、重审时阻止旧结果、取消/超时不自动重试、reviewer 显式恢复不改变作者结果。
- 作者执行与测试完成而缺审查时，返回等待审查。
- 普通项目拒绝模拟审查，已有模拟证据在测试登记被移除后失效；输出脱敏。
- 真实隐藏 WebView2 操作按钮，展示通过/缺陷报告，并取消运行后显示恢复记录。
- 原有工作区隔离、证据门禁、Git 合入、进程树和恢复回归；TS 接入回归和类型检查。

```powershell
# p0-coordinator 下；Windows 指定可用 Python 可执行文件，或使用 python
python -X utf8 -m unittest test_coordinator test_estimate test_verifier test_v1_core test_v1_worktree test_v1_lifecycle test_v1_git_merge test_adapters test_or_router test_semantic test_v1_evidence_gate test_codex_appserver test_webview2_process test_managed_workspace test_recovery test_agent_review -q
python -X utf8 -m unittest test_webview2_ui -q
node --test webview2/test-bridge.cjs
# agent-guild 下
npm.cmd test
npm.cmd run typecheck
```

保护有效性验证：临时移除 reviewer 独立收尾分支，对应回归失败（任务错误变为 needs_fix）；恢复原始字节后回归通过。另先复现并修复了旧批次磁盘报告仍显示 pass、二进制文件内容边界导致副本指纹混淆，以及 Git 默认转义中文路径导致报告被误拒绝的问题。最终桥接回归覆盖本地已停止时显示“待核对收尾”，不继续冒充审查正在运行。

## 未完成事项和限制

未运行真实付费模型，没有证明实际模型的审查质量或生产账户可用性；当前只支持 Codex App Server reviewer，未扩展 DSH。正式产品 UI、React/运行时统一、自动派单、自动批准/合入、自动重试、审批 TTL 和执行端签名均不在本轮。

缺少可选 PuLP，9 项 MILP 测试未运行。Electron 窗口未验收，本轮桌面验收使用 WebView2。没有安装或升级依赖。

脱敏是模式匹配，不能保证识别任意自定义秘密。报告、协议日志和副本暂无保留期限清理；磁盘写入失败会留下待核对的运行，门禁保持阻止。仅凭本机数据库不是防本机管理员篡改的签名证据系统。旧手工 review 证据兼容保留；一旦某检查项有本轮生产者记录，必须匹配该批次的最新有效审查。

## 协作者同步提示词

```text
项目：https://github.com/steven-jzt/agent-guild
本轮分支 codex/p2-3-agent-review，基于 main 4788e86，交付说明在 docs/releases/2026-11-13-p2-3-agent-review.md。
请先自己阅读项目 README、本轮说明和 PR 差异，再用几句话复述你理解的背景、已实现内容、边界和验收重点，暂不改代码或推 main。
本轮在 Python 内核 + WebView2 上接入手动 Codex App Server 审查：独立只读副本，SHA/批次/检查项/Execution 绑定报告，P1/P2 阻塞、P3 建议，人工批准和合入分开。审查发现缺陷是运行成功但门禁未通过；重审/中断不得沿用旧通过或改变作者结果。
重点检查重复点击与作者并发保护、异常/迟到输出、副本修改、模拟证据隔离、恢复后显式重试，以及按钮和报告展示。本轮全部离线模拟，不调用真实模型；MILP 未跑。请先给出你的任务复述与风险，再等待后续分工。
```

下一步最值得做：人工验收本 PR 的离线流程，再在明确授权后用小型测试仓库验证真实只读审查质量；不要直接拿模拟通过批准业务项目。
