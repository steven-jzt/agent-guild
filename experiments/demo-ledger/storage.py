"""账本存储层：JSON Lines 文件读写。

严格遵循项目根目录 CONTRACT.md 第 3 节：

- ``PROJECT_ROOT`` 为本文件所在目录；
- ``data_path()`` 每次调用都重新读取环境变量 ``LEDGER_DATA``；
- ``load_entries()`` 读取并做最小校验；
- ``append_entry()`` 补齐字段后追加写入，不修改入参。
"""

from __future__ import annotations

import json
import math
import os
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT: Path = Path(__file__).resolve().parent

_DEFAULT_RELATIVE = Path("data") / "ledger.jsonl"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_REQUIRED_KEYS = ("id", "date", "amount", "category", "note")


def data_path() -> Path:
    """返回当前数据文件路径；只计算路径，不创建目录。

    环境变量 ``LEDGER_DATA`` 每次调用时读取；相对路径按 ``PROJECT_ROOT`` 解析。
    """

    raw = os.environ.get("LEDGER_DATA")
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path
    return PROJECT_ROOT / _DEFAULT_RELATIVE


def load_entries() -> list[dict]:
    """读取全部记录；文件不存在返回空列表。

    空白行跳过；JSON 解析失败或记录不合法时抛 ``ValueError``，
    消息中包含从 1 开始的物理行号。
    """

    path = data_path()
    if not path.exists():
        return []

    entries: list[dict] = []
    with path.open("r", encoding="utf-8") as stream:
        for lineno, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"第 {lineno} 行 JSON 解析失败: {exc.msg}（第 {exc.colno} 列）"
                ) from exc
            _validate_loaded_record(record, lineno)
            entries.append(record)
    return entries


def append_entry(entry: dict) -> dict:
    """校验并追加一条记录，返回实际写入的完整记录。

    不修改入参；缺少 ``id`` / ``date`` / ``note`` 时分别生成 12 位十六进制
    id、使用本地时区今天、使用空字符串；自动创建数据文件的父目录。
    """

    if not isinstance(entry, dict):
        raise ValueError("entry 必须是 dict")

    record = dict(entry)

    if record.get("id") is None:
        record["id"] = uuid.uuid4().hex[:12]
    else:
        _validate_id(record["id"])

    if record.get("date") is None:
        record["date"] = date.today().isoformat()
    else:
        _validate_date(record["date"], "记录")

    if "amount" not in record:
        raise ValueError("缺少必需字段 amount")
    record["amount"] = _validate_amount(record["amount"], "记录")

    if "category" not in record:
        raise ValueError("缺少必需字段 category")
    category = record["category"]
    if not isinstance(category, str) or category == "":
        raise ValueError("category 必须是非空字符串")

    note = record.get("note")
    if note is None:
        record["note"] = ""
    elif not isinstance(note, str):
        raise ValueError("note 必须是字符串")

    path = data_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line + "\n")
    return record


def _validate_loaded_record(record: Any, lineno: int) -> None:
    where = f"第 {lineno} 行"
    if not isinstance(record, dict):
        raise ValueError(f"{where}记录必须是 JSON 对象")

    missing = [key for key in _REQUIRED_KEYS if key not in record]
    if missing:
        raise ValueError(f"{where}记录缺少必需字段: {', '.join(missing)}")

    _validate_date(record["date"], where)
    _validate_amount(record["amount"], where)


def _validate_date(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ValueError(f"{where}的 date 必须匹配 YYYY-MM-DD，实际为 {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{where}的 date 不是合法日期: {value!r}") from None
    return value


def _validate_id(value: Any) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError("id 必须是 12 位小写十六进制字符串")
    return value


def _validate_amount(value: Any, where: str) -> float:
    """校验金额并返回浮点值；拒绝 bool、非数字、NaN 和无穷大。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}的 amount 必须是数字，实际为 {value!r}")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{where}的 amount 超出浮点数范围: {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{where}的 amount 不能是 NaN 或无穷大")
    return number
