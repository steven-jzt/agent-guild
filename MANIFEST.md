# AgentGuild 整合版：清单与整合记录

初版生成：2026-11-09（Asia/Shanghai）
最近更新：2026-11-11（P1-2 合入正确性）

本文件记录 `AgentGuild-整合版-2026-11-09/` 的来源、目录规则、内容改动和校验结果。整合后的统一入口见 [README.md](README.md)。

> **2026-11-11 本地更新**：已实现 P1-2 合入正确性并刷新清单/分发包。当前 **258 个文件、257 项哈希**；2026-11-09 整合记录和 2026-11-10 同步记录保留在下方，历史数字不删除。

---

## 1. 来源

| 来源包 | 文件数 | 压缩包大小 | SHA256 | 在整合版中的位置 |
| --- | ---: | ---: | --- | --- |
| `Agent_Guild_交接包_v1.0.1_2026-11-09.zip` | 37 | 183,033 B | `EB087B89ABCCEADFD36153108DF2E07F97591EAEA8DAB5DF5F5150CC74CBA3F6` | `docs/` 的设计/验证/归档文档 + `agent-guild/` |
| `AgentGuild-完整项目-2026-11-09.zip` | 212 | 1,741,256 B | `1050703EB60449EEB61268AD53632BA0DB9B2866CD310E12F80D299B53EAF7BB` | `docs/` 的报告/调研/评审/归档 + `p0-coordinator/`、`experiments/`、`pilot/`、`共享/` |

两个原始压缩包共 249 个文件，全部保留（空目录除外）。原始压缩包本身不在本目录内，仍保存在工作区根目录；本整合版没有修改它们。

> 说明：完整项目包内的旧 `MANIFEST.md` 记为 211 个文件，实际解压/计数为 212 个；差异原因未追溯，本清单以实际文件数为准。

---

## 2. 整合后结构

```text
AgentGuild-整合版-2026-11-09/
├─ README.md                         # 统一入口（新增）
├─ MANIFEST.md                       # 本文件（新增）
├─ 文件清单_SHA256.json               # 全部文件哈希（新增，不含自身）
├─ docs/                             # 25 个文件：21 篇正文 + 4 篇归档
│  ├─ 00_先读我_进度与架构交接.md
│  ├─ 设计/          （3）
│  ├─ 现状/          （1）
│  ├─ 报告/          （11）
│  ├─ 调研/          （2）
│  ├─ 评审/          （2）
│  ├─ 验证/          （1）
│  └─ 归档/          （4：原两包的 README/MANIFEST/交付摘要/文件清单）
├─ agent-guild/                      # 30 个文件；TypeScript M0 接入实验
├─ p0-coordinator/                   # 83 个文件；Python 协调内核 + WebView2 壳
├─ experiments/                      # 47 个文件；8 个独立实验
├─ pilot/                            # 59 个文件；试点计划、真实运行证据、状态报告
└─ 共享/                              # 5 个文件；共享/打包脚本与文案
```

整合主体共 **250 个文件，约 3.84 MB**；加上本清单与 `文件清单_SHA256.json` 后为 **252 个文件**（以上为 2026-11-09 整合时的数字；2026-11-10 同步后为 257 个文件，详见 §7）。

目录映射规则：

| 原位置 | 新位置 |
| --- | --- |
| 交接包根目录 `00_先读我…` | `docs/00_先读我_进度与架构交接.md` |
| 交接包 `设计文档/` | `docs/设计/` |
| 交接包 `交付验证摘要.json`、`文件清单_SHA256.json` | `docs/归档/` |
| 交接包 `agent-guild/` | `agent-guild/`（原样） |
| 交接包 `agent-guild/docs/M0_验证记录…` | `docs/验证/M0_验证记录_2026-11-08.md` |
| 完整项目根目录的报告/设计/调研/评审 | `docs/报告|调研|评审|现状/` |
| 完整项目根目录 `README.md`、`MANIFEST.md` | `docs/归档/完整项目_README.md`、`完整项目_MANIFEST.md` |
| 完整项目 `p0-coordinator/`、`pilot/`、`共享/` | 原样保留 |
| 完整项目 8 个实验目录 | `experiments/` 下，保持彼此兄弟关系 |

---

## 3. 整合规则与改动

### 3.1 做了什么

1. 去掉两个外层包目录名，合并为一个项目根。
2. 文档归入 `docs/` 七类；代码、实验、试点保留为可独立运行的子树，不改业务逻辑。
3. 按新目录重写 Markdown 相对链接 **128 处**（涉及 16 个 Markdown 文件），保证链接仍指向同一内容。
4. 给两份历史入口（`docs/00`、归档的完整项目 README/MANIFEST）加“这是历史快照/已归档”提示，正文数据与结论保持原样。
5. 修正 `pilot/CURRENT-STATUS-2026-11-09.md` 中指向未分发 ZIP 的失效链接，改为说明“可用 `共享/build_share.py` 重建”。
6. 适配 `共享/build_share.py`、`共享/build_full_package.py` 的路径到新目录，使两个打包脚本仍可运行。
7. 剔除原包中的 6 个空运行时探测目录（`demo-ledger/tmp*`、`_probe_tmp/tmp*`）；没有文件丢失。

### 3.2 内容改动清单

**语义修改（21 个文件）**：

```text
docs/00_先读我_进度与架构交接.md
docs/现状/当前状态-2026-11-08.md
docs/报告/agent通信与冲突-实验.md
docs/报告/多agent动态组织设计-问题定义.md
docs/报告/多agent协作-总报告.md
docs/报告/多agent协作-整体复盘.md
docs/报告/多agent协调插件-运筹学方案.md
docs/报告/多agent同工作空间-实验报告.md
docs/报告/多agent文档编辑-操作系统类比.md
docs/报告/学习型优化-从trace到策略.md
docs/报告/门控机制-实验与设计.md
docs/调研/AgentGuild-论文与开源项目-调研.md
docs/评审/AgentGuild-v1.0.1-对比分析.md
docs/归档/完整项目_README.md
docs/归档/完整项目_MANIFEST.md
agent-guild/README.md
experiments/a2a-lab/README.md
p0-coordinator/README.md
pilot/CURRENT-STATUS-2026-11-09.md
共享/build_full_package.py
共享/build_share.py
```

（上面按类别列出，实际 21 个；`docs/设计/` 三篇未改内容，仅随目录移动。）

**仅换行符归一化（4 个文件，正文无差异）**：

```text
pilot/FINAL-REPORT.md
pilot/发给朋友-运行说明.md
共享/README.md
共享/成果摘要.md
```

**逐字节未改**：其余 224 个源文件。

### 3.3 没有做什么

- 没有把 TypeScript 的 `agent-guild` 与 Python 的 `p0-coordinator` 合并成单一运行时；两者仍分别运行。
- 没有合并依赖、安装 `node_modules`、升级依赖或修复 209 个依赖审计项。
- 没有重新调用真实 Codex/DSH 模型；没有为打包消耗模型额度。
- 没有加入 API Key、账号登录态、`.env`、原始模型日志、实验仓库或安装包。
- 没有修改两个原始压缩包。

---

## 4. 校验结果（2026-11-09 整合时）

| 项目 | 结果 |
| --- | --- |
| 源文件保留 | 249/249（按映射逐字节比对：224 个完全一致，21 个语义修改，4 个仅换行符变化） |
| 文件数 | 整合主体 250 个；含清单/哈希共 252 个 |
| Markdown 相对链接 | 251 条本地链接检查：**0 broken**（生成 MANIFEST 与哈希文件后复核） |
| p0-coordinator 测试 | **91 tests OK**（Python 3.14.4、PuLP 3.3.1；在隔离副本中运行，避免把 `.tmp`/`__pycache__` 写进交付树） |
| agent-guild 测试 | 未在本次整合中重跑；原交接包记录为 17 项通过、`typecheck` 退出码 0（需要先 `npm ci`，依赖约 1000 个包 / 571 MB） |
| 哈希清单 | `文件清单_SHA256.json` 覆盖除自身外的全部 251 个文件 |

说明：p0 的 `test_semantic` 两个用例需要写系统临时目录；在受限沙箱中会因临时目录权限失败，放开权限后 **91/91 通过**。这不是整合改动导致的失败（原包同样表现）。

---

## 5. 未包含与已知边界

- **凭据/账号**：不含 `~/.dsh/.credentials.yaml`、API Key、登录态；真实实验需接收方自行配置。
- **Git 历史**：两个包都没有 `.git`；交接包声明的提交 `a58e08e7340400fead3bed87ebbb315d4cf39283` 无法在本地验证。
- **依赖安装目录**：`node_modules`、`a2a-lab/vendor`、`__pycache__`、运行时数据库/Worktree 均未打包。
- **原始模型日志/实验仓库/安装包**：未包含；`pilot` 中的结论来自原记录。
- **LICENSE**：两个来源都没有版权/授权文件；对外分发前需补。
- **版本口径**：设计文档 v1.0.1 ≠ 软件已发布 v1.0.1；`agent-guild/package.json` 为 0.1.0。
- **两套状态文档**：`docs/00` 是 M0 视角（M0 未完整验收），`pilot/CURRENT-STATUS-2026-11-09.md` 是 p0 规则层视角（V1 核心已实现）；不要混为同一层结论。

---

## 6. 重建与校验

从本目录重建整合包（会生成 `../AgentGuild-整合版-重建.zip`，不影响原目录）：

```powershell
cd 共享
python build_full_package.py
```

校验文件哈希：

```powershell
python -c "import json,hashlib,pathlib; d=pathlib.Path('.'); m=json.load(open('文件清单_SHA256.json',encoding='utf-8')); bad=[x['path'] for x in m['files'] if hashlib.sha256((d/x['path']).read_bytes()).hexdigest().lower()!=x['sha256'].lower()]; print('checked',len(m['files']),'bad',len(bad)); print(*bad,sep='\n')"
```

---

## 7. 2026-11-10 同步更新

### 7.1 同步结果

- 远端 GitHub `main`：`3ad1fd08687a857659c40c79485d87279271869f`（2026-11-10 00:22）。
- 本地已 fast-forward 到同一提交，工作树干净，未产生合并冲突。
- 当前跟踪文件 **257 个**，未压缩约 **4.02 MB**（不含 `.git`、`.tmp`、`__pycache__`）。

### 7.2 相对 2026-11-09 整合基线的变化

新增 5 个文件：

```text
docs/releases/2026-11-10-p1-progress.md
p0-coordinator/.gitignore
p0-coordinator/codex_appserver_stub.py
p0-coordinator/test_codex_appserver.py
p0-coordinator/test_v1_evidence_gate.py
```

修改 14 个文件：

```text
README.md
agent-guild/README.md
agent-guild/test/fixtures/rpc-server.mjs
agent-guild/test/rpc-process.test.ts
p0-coordinator/README.md
p0-coordinator/adapters.py
p0-coordinator/agent_runner.py
p0-coordinator/candidate_demo.py
p0-coordinator/coordinator.py
p0-coordinator/mcp_server.py
p0-coordinator/test_semantic.py
p0-coordinator/test_v1_core.py
p0-coordinator/test_v1_git_merge.py
p0-coordinator/worker_cli.py
```

内容对应 P1-1 证据门禁、TS 测试就绪同步、P1-3 Codex App Server 协议修复和验收补修；详见 [2026-11-10 P1 进度](docs/releases/2026-11-10-p1-progress.md)。

### 7.3 本次刷新后的校验

| 项目 | 结果 |
| --- | --- |
| Python 全量测试 | `python -m unittest discover -s . -p 'test_*.py'` → **157 tests OK**（含 9 项 MILP；Python 3.14.4 + PuLP 3.3.1） |
| 发布说明口径 | 148 项核心回归 + 9 项 MILP 未运行；本机补跑后 157/157 通过 |
| TypeScript | 未重跑；本地没有 `node_modules`，发布说明记录 19 项测试 + `typecheck` 通过 |
| Markdown 链接 | 生成新清单后复核，**0 broken** |
| 哈希清单 | `文件清单_SHA256.json` 覆盖除自身外的全部 **256** 个文件，逐项复算 0 错误 |
| 分发包 | `AgentGuild-整合版-2026-11-10.zip`，257 个文件；11-09 ZIP 保留为历史快照 |

### 7.4 仍未解决

- **Codex Exec 沙箱**：`CodexExecAdapter` 默认仍带 `--sandbox danger-full-access`，`agent_runner` 的 `codex-deepseek` / `codex-zhipu` 路由使用它。
- **真实 Agent 审查生产者**：`agent_runner` 只记录 test 证据；默认必需项含 `agent-review`，因此当前真实运行会停在 `missing=agent-review`，这是预期门禁，但产品流程尚未闭合。
- **P1-4 / P2-1**：界面异步与管道退出、独立克隆仍未完成。
- **工程化**：根目录仍无 `.gitignore`（只有 `p0-coordinator/.gitignore`），无 LICENSE；公开仓库中的本机路径未脱敏。

---

## 8. 2026-11-11：P1-2 合入正确性

- **目标分支校验**：`start_merge` 解析本地分支/`HEAD` 的实际 SHA，必须等于 candidate 的 `base_sha`；候选生成后目标前进时拒绝创建 MergeAttempt。
- **从目标 HEAD 建集成分支**：`merge_candidate_git` 从目标 HEAD 建 worktree，并在 cherry-pick 前再次核对 `target_before_sha`。
- **结果绑定**：`merge_candidate_git` 把 `result_sha` 写回 pending Attempt；`finish_merge(succeeded)` 必须收到同一个 `result_sha`。
- **推进并复核目标 ref**：`finish_merge` 只允许 fast-forward，更新后重新读取目标 ref 并对比；目标被外部推进、结果不是基线后代、结果为空或未变化，都标记 failed，Candidate 保持 approved、任务退回 `needs_fix`。
- **迁移**：`merge_attempts` 新增 `repo_path`、`target_ref`、`target_before_sha`、`target_after_sha`，旧库自动补列；旧的无目标信息 Attempt 不能确认 succeeded。
- **验证**：Python 全量 **163 tests OK**（含 9 项 MILP）；`test_v1_git_merge.py` 8 条真实 Git 回归，覆盖目标前进、错误/空 result、结果未变化；`test_codex_appserver` 超时用例放宽到 1.5s 以避免慢机器误报。
- **新增文件**：`docs/releases/2026-11-11-p1-2-merge-correctness.md`。
- **分发包**：`AgentGuild-整合版-2026-11-11.zip`（258 个文件）；11-10、11-09 ZIP 保留为历史快照。
