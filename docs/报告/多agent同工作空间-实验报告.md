# 同一工作空间里的多 Agent 协作：一次可复现的可行性实验

> **历史版本**：这是第一份实验报告，后续的 A2A、通信/原子性、文档编辑、学习/门控等已并入 [多agent协作-总报告.md](多agent协作-总报告.md)，最新实现状态见 [当前状态-2026-11-08.md](../现状/当前状态-2026-11-08.md)。保留本文件作为过程记录。
>
> 实验时间：2026-11-06 22:06–22:14（Asia/Shanghai）
> 环境：DeepSeek Harness（Windows，workspace-write 沙箱），所有 agent 共享同一工作目录
> 产物：`demo-ledger/`（可运行的个人记账 CLI）、`contention/`（并发写实验脚本）、本报告

## 0. 一句话结论

**可行，但有明确边界。**

在「接口先冻结、文件所有权清晰、验收自动化」的条件下，3 个 agent 在同一目录并行约 6 分钟，交付 485 行实现 + 856 行测试 + 195 行验收测试，**83 个测试全绿，零手工冲突解决**。

但只要两个 agent 对**同一个文件**做「读—改—写」，问题立刻出现：

- 绕过文件工具、用脚本直接写：**62% 的更新静默丢失**，还会读到写了一半的文件；
- 走文件工具：冲突会变成显式报错（`file changed since it was read`），**不重试的 agent 更新就丢了**；
- 加上「重读 → 合并 → 重试」：3 个 agent 全部写入成功。

浓缩成一句话：**同工作空间适合「按文件并行」，不适合「按行并行」。**

---

## 1. 要回答的问题

把多个 agent 放进同一个工作空间做同一个项目，实际会遇到什么？

1. 并行写文件安全吗？会不会互相覆盖？
2. 没有人工中间层，接口能对齐吗？集成会不会返工？
3. agent 之间怎么协调？能互相发消息吗？
4. 质量怎么保证？谁来验收、谁来提交？
5. 这种模式什么时候值得用，什么时候是坑？

## 2. 实验设计

### 2.1 项目与角色

项目是一个纯标准库的 Python 记账 CLI（`add` / `list` / `report`），刻意切成三个低耦合模块：

| 角色 | 职责 | 拥有的文件 |
| --- | --- | --- |
| 编排者（我） | 写契约、写验收测试、调度、集成、提交 | `CONTRACT.md`、`test_acceptance.py`、`README.md` |
| agent-storage | 账本读写、校验、JSONL 持久化 | `storage.py`、`test_storage.py` |
| agent-report | 汇总与报表格式化 | `report.py`、`test_report.py` |
| agent-cli | 命令行入口、参数与错误处理 | `cli.py`、`test_cli.py` |

### 2.2 四条防冲突规则

1. **契约先行**：`CONTRACT.md` 写死函数签名、数据结构、错误语义、边界行为，实现期间冻结。
2. **验收先行**：`test_acceptance.py`（11 个黑盒用例）在实现开始前写好，是唯一的「完成」标准。
3. **文件所有权**：每个 agent 只能改自己名下的文件；发现别人的问题只能上报，不能动手。
4. **编排者统一提交**：agent 禁止任何 git 写操作；集成通过后由编排者 review + commit。

### 2.3 时间线

| 时间 | 事件 |
| --- | --- |
| 22:06:22 | 契约写入 |
| 22:06:31 | 验收测试写入 |
| 22:06:38 | baseline commit（只有契约和测试） |
| 22:07:05 | 三个实现 agent 同时启动 |
| 22:07:34 | `report.py` 落盘（最快） |
| 22:08:03 | `storage.py` 落盘 |
| 22:08:32 | `cli.py` 落盘 |
| 22:11:59 前后 | storage 完成（17 tests OK） |
| 22:12:14 | 验收测试临时目录适配沙箱 ACL（断言未改） |
| 22:13 前后 | cli 完成（22 tests OK）；验收 11/11 通过 |
| 22:13:42 | 集成 commit（8 files, +1360/−4） |

从启动到全部完成约 **6 分钟**；从契约到集成提交 **7 分 20 秒**。

## 3. 结果一：按文件并行，跑通了

### 3.1 产出

| 文件 | 所有者 | 行数 | 测试数 |
| --- | --- | ---: | ---: |
| `storage.py` | agent-storage | 158 | 17 |
| `report.py` | agent-report | 119 | 33 |
| `cli.py` | agent-cli | 208 | 22 |
| `test_acceptance.py` | 编排者 | 195 | 11 |
| 实现者测试合计 | — | 856 | 72 |

验收命令与结果：

```text
test_storage     Ran 17 tests  OK
test_report      Ran 33 tests  OK
test_cli         Ran 22 tests  OK
test_acceptance  Ran 11 tests  OK
```

### 3.2 范围纪律

集成后 `git diff --numstat` 只出现预期的 8 个文件，**没有 agent 改到别人的文件**。唯一越界是诊断残留：cli agent 在项目根留下了 `pyprobe_workspace.txt` 和 `_probe_tmp/`，由编排者清理。

这说明「文件所有权」这条规则是**可执行、可检查**的：不需要信任 agent，只需要 diff。

### 3.3 零集成返工，但有一次「假故障」

排除环境问题后，第一次集成验收就 11/11 通过。不过第一次运行验收时出现 7 个 failure + 1 个 error，全部是：

```text
PermissionError: [Errno 13] Permission denied: '...\.tmp\acceptance-xxxx\ledger.jsonl'
```

不是实现问题，而是本机 DSH Windows 沙箱下 `tempfile.TemporaryDirectory`（0o700 ACL）创建的子目录不可写。把验收测试的临时目录改成工作区内、默认权限 `mkdir` 后，11/11 直接通过。

两个实现 agent 都独立踩到了同一个坑，并各自写了不同的绕行方案：

- storage agent：`tempfile.mktemp` + 默认权限 `mkdir`；
- cli agent：`tempfile.gettempdir()` + 默认权限 `mkdir`。

**教训**：共享工作空间也共享环境缺陷。N 个 agent 会重复排查同一个坑，环境注意事项应该写进契约/README，只踩一次。

### 3.4 agent 自报的契约歧义

三个 agent 都主动报告了契约未写死、需要自己取舍的地方：

| agent | 歧义点 | 取舍 |
| --- | --- | --- |
| storage | `load` 是否要求 `note` 必填；显式 `None` 如何处理；额外字段 | 五键齐全；`None` 视为缺省；保留额外字段 |
| report | `by_category` 顺序；非法输入行为；`-0.0` | 首次出现顺序；抛 `ValueError`；归一为 `0.0` |
| cli | 正数是否加 `+`；`--limit 0`；帮助输出到哪 | 不加 `+`；输出 0 条返回 0；stdout |

这些取舍这次没有冲突，是因为验收测试恰好覆盖了关键格式；**没有被验收覆盖的歧义，就会变成集成阶段的意外**。契约写得越接近「可执行的规格」，返工越少。

## 4. 结果二：同一文件并发写，会发生什么

### 4.1 进程级：朴素「读—改—写」会静默丢更新

三个独立进程，各自循环 60 次「读 `counter.json` → 等 1ms → 整文件写回 count+1」，期望 180：

| 轮次 | 期望 | 实际 | 丢失 | 撕裂读（每进程） |
| --- | ---: | ---: | ---: | --- |
| 1 | 180 | 67 | 113 | 7 / 8 / 8 |
| 2 | 180 | 67 | 113 | 4 / 4 / 5 |
| 3 | 180 | 68 | 112 | 6 / 10 / 5 |

**所有进程退出码都是 0。** 没有报错、没有异常，数据就是少了约 62%。这是最危险的一类失败：静默、不可见、看起来成功。

### 4.2 进程级：加锁后完全正确

同样的三个进程、同样的 60 次，用原子 `mkdir` 做互斥锁：

| 轮次 | 期望 | 实际 | 丢失 |
| --- | ---: | ---: | ---: |
| 1 | 180 | 180 | 0 |
| 2 | 180 | 180 | 0 |
| 3 | 180 | 180 | 0 |

顺带一个真实细节：这个「显然正确」的 mkdir 锁在 Windows 上第一次实现时仍然崩了——目录处于 delete-pending 时 `mkdir` 返回 `WinError 5` 而不是 `FileExistsError`。**临时自制的锁本身就是 bug 来源**，生产环境应使用成熟锁库或数据库事务。

### 4.3 Agent 级第一轮：工具层把冲突变成了报错

3 个 agent 同时执行：读 `team.json` → `Start-Sleep 6` → 整文件覆盖写回自己的名字（明确要求「不重读、不重试」）。

结果：

| 参与者 | 结果 |
| --- | --- |
| 甲 | 写入成功 |
| 乙 | 被拒绝：`cannot write ...: file changed since it was read — re-read the file, then retry` |
| 丙 | 同样被拒绝 |

最终文件里只有「甲」，**1/3 的更新存活**。

关键区别：这次不是静默丢失，而是**显式拒绝**。本会话的文件工具在写入前会校验「你读到的快照是否仍是当前内容」（乐观并发控制），过期快照的覆盖会被挡住。**安全性和可用性在这里做了交换**：不会写坏，但不重试就等于放弃。

### 4.4 Agent 级第二轮：重读—合并—重试，全部收敛

同样的并发，规则改成「遇到 `file changed` 就重读、把别人名字合并进来、重试（最多 5 次，间隔 1s）」：

| 参与者 | 重试次数 | 结果 |
| --- | ---: | --- |
| 丁 | 0 | 首次成功 |
| 戊 | 1 | 第二次成功，合并了丁 |
| 己 | 2 | 第三次成功，合并了丁、戊 |

最终 `members = ["丁","戊","己"]`，JSON 合法，3/3 存活。

### 4.5 三条结论

1. **工具路径受保护，shell 路径裸奔。** 用 `write`/`edit` 改文件有快照校验；用 PowerShell/Python 直接写就退回 4.1 的静默丢更新。
2. **保护不等于正确。** 拒绝过期写只保证「不覆盖别人」，要保证「不丢自己」，必须重读 + 合并 + 重试，而且合并逻辑要是「并集」而不是「覆盖」。
3. **同一文件的并行度是有上限的。** 冲突概率随「同时写同一文件的人数 × 持有快照的时长」上升。这次只是 3 个 agent、6 秒窗口，就已经 2/3 冲突。

## 5. 结果三：agent 之间怎么协调

### 5.1 没有直接的 agent 间消息通道

让一个 agent 尝试给同级 agent 发消息，返回原文：

```text
Error: subagent "1e7c37f4-..." belongs to another parent session
```

`send_message` 工具存在，但只能对「自己的直接子 agent」或「自己的父 agent」使用；**同级 agent 之间不能直接对话**。

> 补记（2026-11-07）：这条限制可以在协议层绕过——用 A2A 把 agent 服务化之后，同级 agent 可以直接互调。实测（两个 server、两跳调用、任务失败回传）见 [A2A-可行性-附记.md](A2A-可行性-附记.md)。

### 5.2 文件系统是唯一共享黑板

- 好处：天然可审计（git diff 就是变更记录）、无需额外基础设施、跨 agent 可见；
- 坏处：没有通知、没有锁、没有事务、没有「谁在读」的可见性；
- 编排者是唯一的路由器：agent 的发现、冲突、求助都汇总到编排者，再由编排者决定派给谁。

### 5.3 git 是审计层，不是协调层

agent 不做 git 操作，编排者统一提交。这样：

- 每个文件的改动范围一目了然（`git diff --numstat`）；
- 出问题可以整体回滚；
- 但 git 本身不解决并发写——它只是事后记录，不是运行时锁。

## 6. 可行性矩阵

| 场景 | 判断 | 关键条件 |
| --- | --- | --- |
| 独立文件/模块并行开发 | ✅ 很适合 | 契约 + 文件所有权 + 验收测试 |
| 同一文件不同区域并行编辑 | ⚠️ 有条件 | `edit` 精确匹配 + 冲突重读；文件越大冲突越多 |
| 同一文件整文件覆盖 | ❌ 不要 | 必然丢更新或被拒 |
| 共享 JSON/状态文件 | ⚠️→❌ | 单写者、锁、队列或数据库事务；禁止裸 RMW |
| 强依赖链（A→B→C） | ⚠️ 可以 | 接口先行、按契约编码；或改成阶段化串行 |
| 需要频繁讨论的设计决策 | ⚠️ 低效 | agent 间不能对话，靠编排者中转 |
| 10+ agent 大规模并行 | ❓ 未验证 | 协调成本、冲突概率、上下文开销都会上升 |

## 7. 可复用 Playbook

### 7.1 开工前

1. **按文件切分，不按行切分。** 每个 agent 的名下文件互不重叠；共享文件指定唯一写者。
2. **写契约，写到「可执行」。** 签名、数据结构、错误消息格式、边界（空值、负数、舍入、排序）都要写死；留下 `TODO` 就等于留下集成意外。
3. **先写黑盒验收测试。** 它是唯一完成标准，也是编排者不读实现就能验收的依据。
4. **写环境注意事项。** 本次的临时目录 ACL 坑，如果提前写进 README，能省掉两个 agent 的重复排查。

### 7.2 运行中

5. **按接口编程，不要互相等。** 依赖方先按契约写完，等依赖文件出现再跑测试；不要在别人的文件里放 stub。
6. **冲突处理统一为「重读 → 合并 → 重试」。** 有界重试 + 退避；合并是并集，不是覆盖。
7. **禁止 agent 直接写共享状态。** 需要并发写时用锁/队列/数据库；不要相信「就改一行」。
8. **禁止 agent 做 git 写操作。** 提交权收归编排者。

### 7.3 收尾

9. **编排者跑全量验收 + 查看 diff 范围。** 检查两件事：测试是否全绿、有没有人改到别人的文件。
10. **清理诊断残留。** 本次有 agent 留下探测文件和临时目录；所有权规则要覆盖「临时文件放哪」。
11. **统一提交，记录实验数据。** 提交信息里写清「多 agent 并行产出」，方便回溯。

### 7.4 反模式

- ❌ 多个 agent 同时改同一个大文件
- ❌ 没有验收测试就并行开工
- ❌ 用 shell/Python 直接写共享文件（绕过并发保护）
- ❌ 把「代码写完」当「任务完成」
- ❌ 让 agent 自己 commit / merge / 改契约

## 8. 成本与局限

- **墙钟时间**：并行实现约 6 分钟；串行做同样三个模块，保守估计 10–15 分钟（未实测）。
- **token 成本**：未采集精确数据；3 个并行上下文，量级上高于单 agent。
- **样本很小**：3 个 agent、1 个项目、1 台机器、同一模型；结论是「机制上可行」，不是「规模上已验证」。
- **未验证**：git worktree 隔离后合并、10+ agent、异构模型、跨天长期项目。
- **环境相关**：Windows 沙箱的 ACL 行为不代表 Linux/macOS；文件工具的快照校验是本会话观察到的行为，不同 harness 可能不同。
- **「零集成返工」有前提**：任务被切得很干净、契约写得足够细。任务耦合度越高，这个结论越不成立。

## 9. 复现

目录结构：

```text
探索区/
├─ demo-ledger/                 # 3 个 agent 并行实现的项目
│  ├─ CONTRACT.md               # 接口契约（冻结）
│  ├─ test_acceptance.py        # 编排者的 11 个黑盒验收用例
│  ├─ storage.py / report.py / cli.py
│  ├─ test_storage.py / test_report.py / test_cli.py
│  └─ README.md
├─ contention/                  # 并发写实验
│  ├─ naive_increment.py        # 无锁读—改—写
│  ├─ locked_increment.py       # mkdir 互斥锁
│  ├─ run_experiment.py         # 3 进程 × 60 次对照实验
│  └─ team.json                 # agent 级并发写的目标文件
└─ 多agent同工作空间-实验报告.md
```

复现命令：

```powershell
# 1) 项目测试
cd demo-ledger
python -m unittest test_storage test_report test_cli test_acceptance -v

# 2) CLI 演示
$env:LEDGER_DATA = "$PWD\.tmp\demo.jsonl"
python cli.py add --amount 28.5 --category 餐饮 --note 晚饭
python cli.py add --amount -8000 --category 工资
python cli.py list
python cli.py report

# 3) 并发写实验（期望 180）
cd ..\contention
python run_experiment.py
```

> 说明：`demo-ledger/` 根目录残留 5 个空目录（`tmp2f2_y6lc` 等），是 agent 探测沙箱时由 `tempfile`（0o700 ACL）创建、当前沙箱权限删不掉的。它们不进 git，可在**非 DSH 沙箱**的普通 PowerShell 里用 `rmdir /s /q` 清理。

## 10. 后续值得做的实验

1. **git worktree 隔离**：每个 agent 一个 worktree，编排者做 merge——对比「共享目录」的冲突率和返工率。
2. **共享文件的单写者队列**：所有写请求进队列，一个 writer 顺序落盘，验证吞吐与正确性。
3. **规模测试**：10 个 agent、20+ 文件，观察协调开销和冲突概率是否非线性上升。
4. **异构模型**：不同模型对同一契约的理解偏差，是否比同模型更大。
5. **语义级冲突检测**：不只看 mtime，而是看「两个改动是否触及同一语义单元」。

> 补记（2026-11-07）：第 5 条之外，已实测「A2A 协议能不能补上 agent 间通信」——结论是能补通信层，不能补文件并发。见 [A2A-可行性-附记.md](A2A-可行性-附记.md)。

---

## 附录：原始数据

**进程级并发写（3 进程 × 60 次，期望 180）**

```text
naive (no lock)      trial 1: final= 67  lost=113  corrupt_reads=7/8/8
naive (no lock)      trial 2: final= 67  lost=113  corrupt_reads=4/4/5
naive (no lock)      trial 3: final= 68  lost=112  corrupt_reads=6/10/5
locked (mkdir lock)  trial 1: final=180  lost=  0
locked (mkdir lock)  trial 2: final=180  lost=  0
locked (mkdir lock)  trial 3: final=180  lost=  0
```

**Agent 级并发写**

```text
第一轮（不重试）：甲 成功；乙、丙 被拒 "file changed since it was read"
                  最终 members = ["甲"]（1/3 存活）

第二轮（重读+合并+重试）：丁 0 次重试；戊 1 次；己 2 次
                  最终 members = ["丁","戊","己"]（3/3 存活）
```

**测试与提交**

```text
test_storage     17 tests  OK
test_report      33 tests  OK
test_cli         22 tests  OK
test_acceptance  11 tests  OK
commit 4dc917f   8 files changed, 1360 insertions(+), 4 deletions(-)
```
