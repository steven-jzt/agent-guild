# AgentGuild 整合版：清单与整合记录

生成时间：2026-11-09（Asia/Shanghai）

本文件记录 `AgentGuild-整合版-2026-11-09/` 的来源、目录规则、内容改动和校验结果。整合后的统一入口见 [README.md](README.md)。

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

整合主体共 **250 个文件，约 3.84 MB**；加上本清单与 `文件清单_SHA256.json` 后为 **252 个文件**。

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

## 4. 校验结果

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
python -c "import json,hashlib,pathlib; d=pathlib.Path('.'); m=json.load(open('文件清单_SHA256.json',encoding='utf-8')); bad=[x['path'] for x in m['files'] if hashlib.sha256((d/x['path']).read_bytes()).hexdigest().upper()!=x['sha256']]; print('checked',len(m['files']),'bad',len(bad)); print(*bad,sep='\n')"
```
