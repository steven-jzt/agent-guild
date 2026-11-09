# 协作契约 v1

> 本文件由编排者（orchestrator）维护。实现 agent **不得修改**本文件。
> 项目：`ledger` 个人记账 CLI，纯 Python 标准库（Python 3.14），无第三方依赖。
> 工作目录：本文件所在目录（下文称“项目根目录”）。所有命令都在项目根目录执行。

## 1. 文件所有权

| 文件 | 所有者 |
| --- | --- |
| `storage.py`、`test_storage.py` | agent-storage |
| `report.py`、`test_report.py` | agent-report |
| `cli.py`、`test_cli.py` | agent-cli |
| `CONTRACT.md`、`test_acceptance.py`、`README.md`、`.gitignore` | 编排者 |

- 只允许创建/修改**自己拥有**的文件。
- 发现别人的文件有问题：写进回报交给编排者，**不要直接改**。
- 不要执行任何 `git` 写操作（add/commit/checkout 等），不要安装依赖。

## 2. 数据模型

一条记录（Entry）是 JSON 对象：

```json
{"id": "8f3a1c2b9d4e", "date": "2026-11-06", "amount": 12.34, "category": "餐饮", "note": "午饭"}
```

- `id`：12 位小写十六进制字符串（`uuid.uuid4().hex[:12]`）。
- `date`：`YYYY-MM-DD`。
- `amount`：浮点数，**正数=支出，负数=收入**；允许 0；不接受 bool / NaN / infinity。
- `category`：非空字符串。
- `note`：字符串，可为空。
- 存储格式：JSON Lines（UTF-8，每行一个对象，行尾 `\n`，键顺序不限）。

## 3. `storage.py` 公开接口

```python
PROJECT_ROOT: pathlib.Path   # 等于本文件所在目录
def data_path() -> pathlib.Path
def load_entries() -> list[dict]
def append_entry(entry: dict) -> dict
```

- `data_path()`：**每次调用时**读取环境变量 `LEDGER_DATA`；有值则使用（相对路径按 `PROJECT_ROOT` 解析），否则返回 `PROJECT_ROOT / "data" / "ledger.jsonl"`。该函数只计算路径，**不创建目录**。
- `load_entries()`：文件不存在返回 `[]`；跳过空白行；逐行 `json.loads`，失败抛 `ValueError`，消息中必须含出错行号（从 1 开始）；对每条记录做最小校验（必需键齐全、`date` 匹配 `YYYY-MM-DD`、`amount` 是数字），不合法抛 `ValueError`。
- `append_entry(entry)`：不修改入参；复制后校验；缺 `id` 时生成；缺 `date` 时用本地时区今天；缺 `note` 时用 `""`；自动创建父目录；以一行 JSON（`ensure_ascii=False`）追加写入；返回实际写入的完整记录。校验失败抛 `ValueError`。

## 4. `report.py` 公开接口

```python
def summarize(entries: list[dict]) -> dict
def format_report(summary: dict) -> str
```

- `summarize`：返回 `{"count": int, "total": float, "by_category": dict[str, float]}`。
  - `count` = 条数；`total` = 所有 `amount` 之和，`round(x, 2)`；`by_category` 按 `category` 汇总，同样 `round(x, 2)`。
  - 空列表返回 `{"count": 0, "total": 0.0, "by_category": {}}`。
  - **不得修改入参**。
- `format_report`：多行字符串，以 `\n` 连接，**末尾无换行**。必须包含：
  - 一行含 `共 N 笔`；
  - 一行含 `合计 X.XX`；
  - 每个分类一行，形如 `  - 餐饮: 123.45`（两个空格 + `- ` + 分类 + `: ` + 两位小数）。

## 5. `cli.py` 公开接口

```python
def main(argv: list[str] | None = None) -> int
```

- 可直接被 import 调用；文件末尾：

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

- 子命令：
  - `add --amount FLOAT --category STR [--note STR] [--date YYYY-MM-DD]`
    调用 `storage.append_entry`，成功时向 stdout 打印 `已记录 <id>`，返回 0。
  - `list [--limit N]`
    按文件顺序打印，每行形如 `YYYY-MM-DD  分类  ±金额  note`（日期与分类、分类与金额之间至少一个空格；金额两位小数）。`--limit N` 只打印**最后 N 条**。空账本打印 `暂无记录`，返回 0。
  - `report`
    打印 `report.format_report(report.summarize(storage.load_entries()))`，返回 0。
  - 无子命令：打印 argparse 帮助，返回 2。
- 所有错误（参数错误、`ValueError` 等）向 stderr 打印 `错误: <消息>`，返回 1。**包括 argparse 的类型转换错误**（需自定义 `ArgumentParser.error` 或自行校验）。

## 6. 测试要求

- 每人用 `unittest` 写自己的测试文件，至少覆盖正常路径 + 2 个错误路径。
- 测试必须能在项目根目录直接运行：`python -m unittest test_storage -v`。
- 测试不得依赖网络，不得写项目内 `data/` 目录；用 `tempfile` + 环境变量 `LEDGER_DATA` 隔离。
- 测试要能由编排者在 agent 结束后重复运行。

## 7. 验收

编排者的 `test_acceptance.py` 会端到端执行：空账本 `list` → `add`（支出/收入/非法金额/指定日期）→ `list` / `list --limit` → `report` → 损坏文件 → 直接 import 调用模块 API。实现必须严格满足本契约。
