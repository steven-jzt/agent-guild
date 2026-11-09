# 真实多模块仓库多 agent（2026-11-08）

## 1. 仓库

`pilot/sample-repo`：一个真实 Git 仓库（不是 `.tmp` 临时目录）：

```text
sample-repo/
├─ src/shop/pricing.py
├─ src/shop/cart.py
├─ src/shop/formatting.py
├─ tests/test_base.py
├─ pyproject.toml
└─ README.md
```

base tests：3 个 unittest 全过。

## 2. 三 agent 并发结果

`pilot/run_multi_repo.py`：

| agent | 改的模块 | 耗时 | 结果 | usage（in/cached/out） |
| --- | --- | ---: | --- | --- |
| DSH | `src/shop/pricing.py` | 10.9s | ✅ accepted | 561 / 6784 / 186 |
| Codex-DeepSeek | `src/shop/cart.py` | 21.6s | ✅ accepted | 50869 / 49408 / 1215 |
| Codex-智谱 | `src/shop/formatting.py` | 22.4s | ✅ accepted | 16035 / 15552 / 150 |

总墙钟：**24.8 秒**。  
三个候选：

```text
dsh:            7ea7481b021362a6d036dbe5f6b579493f5371af
codex-deepseek: 0443aa242b106012c9b2f6ad67e2fb9d27ea9e86
codex-zhipu:    94b7bd94e3e792aadd6853f969fe9716e71afcbc
```

全部 `candidate=merged`、`lifecycle=accepted`、`status=done`；无越界。

## 3. 每个 agent 做了什么

- DSH：`pricing.py` 增加 `bulk_discount(total, percent)`；
- Codex-DeepSeek：`cart.py` 增加 `cart_total(items)`；
- Codex-智谱：`formatting.py` 增加 `format_price(cents)`。

每个任务：

```text
文件预约（只允许自己的模块）
→ 独立 worktree
→ agent 修改
→ scope check（只改预约文件）
→ commit → Candidate
→ 模块级 evidence（import + assert）
→ approve → MergeAttempt → accepted
```

## 4. 这次证明了什么

- 多 agent 可以在**多模块仓库**上并发，不是只在一个空 demo 里；
- 文件级预约 + 独立 worktree 能防止互相覆盖；
- 三个 agent 的改动各自独立绑定 SHA、独立 evidence、独立合入；
- 并发墙钟 24.8s，接近最慢的 22.4s。

## 5. 没证明什么

- `finish_merge` 仍是规则层；真实 Git 合入是额外脚本完成的（见 [INTEGRATION.md](INTEGRATION.md)）；
- 任务之间没有依赖/交叉修改；
- 没有真实人类 reviewer（脚本调用）；
- 智谱按 Coding Plan 套餐计费，不是 token 账单；
- Codex 用 `codex exec`，不是 App Server JSON-RPC。

## 6. 成本

- DSH + Codex-DeepSeek：约 **¥0.0082**（官方价目表估算）；
- Codex-智谱：套餐计。

详见 [COST.md](COST.md)。

## 7. 真实 Git 合入

三个候选已在 `.tmp/pilot-sample/integration` 集成分支真实 cherry-pick：

```text
base:        7d722e9
integration: d036486
3 files changed, 12 insertions(+)
tests:       3 OK
```

见 [INTEGRATION.md](INTEGRATION.md)。
