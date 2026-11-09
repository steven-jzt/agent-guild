"""report.py — 账目汇总与报表格式化（所有者：agent-report）。

实现 CONTRACT.md 第 4 节的两个公开接口：

    summarize(entries) -> {"count": int, "total": float, "by_category": dict[str, float]}
    format_report(summary) -> str

契约未规定处的实现约定：

- ``by_category`` 的顺序 = 分类在遍历中首次出现的顺序（不排序，避免依赖语言/区域排序规则）；
- 金额累加使用 ``math.fsum`` 以减少浮点误差，最终仍严格按契约 ``round(x, 2)`` 取整；
- ``-0.0`` 统一归一为 ``0.0``，避免报表出现 ``-0.00``；
- 对明显非法的输入抛 ``ValueError``（中文消息，可由 cli 捕获为 ``错误: ...``）；
- 只读入参，绝不修改调用方传入的 ``entries`` 或 ``summary``。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

__all__ = ["summarize", "format_report"]

_PLACES = 2


def _as_amount(value: Any, label: str) -> float:
    """把金额转成 float；bool / 非数字 / NaN / infinity 一律拒绝。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必须是数字，实际为 {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} 必须是有限数字，实际为 {value!r}")
    return number


def _round_money(value: float) -> float:
    """按契约 round(x, 2)，并把 -0.0 归一为 0.0。"""
    rounded = round(value, _PLACES)
    return 0.0 if rounded == 0 else rounded


def _format_money(value: float) -> str:
    """格式化为两位小数，且不输出 ``-0.00``。"""
    rounded = round(float(value), _PLACES)
    if rounded == 0:
        rounded = 0.0
    return f"{rounded:.2f}"


def summarize(entries: Iterable[dict]) -> dict:
    """汇总记录，返回 ``{"count", "total", "by_category"}``；不修改入参。

    空列表返回 ``{"count": 0, "total": 0.0, "by_category": {}}``。
    """
    if entries is None:
        raise ValueError("entries 不能为 None")
    if isinstance(entries, (str, bytes, bytearray, Mapping)):
        raise ValueError("entries 必须是记录列表")

    count = 0
    amounts: list[float] = []
    grouped: dict[str, list[float]] = {}

    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, Mapping):
            raise ValueError(
                f"第 {index} 条记录必须是对象，实际为 {type(entry).__name__}"
            )
        if "amount" not in entry:
            raise ValueError(f"第 {index} 条记录缺少 amount")
        amount = _as_amount(entry["amount"], f"第 {index} 条记录的 amount")
        category = entry.get("category")
        if not isinstance(category, str) or category == "":
            raise ValueError(f"第 {index} 条记录的 category 必须是非空字符串")

        count += 1
        amounts.append(amount)
        grouped.setdefault(category, []).append(amount)

    return {
        "count": count,
        "total": _round_money(math.fsum(amounts)),
        "by_category": {
            category: _round_money(math.fsum(values))
            for category, values in grouped.items()
        },
    }


def format_report(summary: Mapping) -> str:
    """把 summarize 的结果格式化成多行报表，行间用 ``\\n`` 连接，末尾无换行。"""
    if not isinstance(summary, Mapping):
        raise ValueError(f"summary 必须是对象，实际为 {type(summary).__name__}")

    missing = [key for key in ("count", "total", "by_category") if key not in summary]
    if missing:
        raise ValueError("summary 缺少字段: " + ", ".join(missing))

    count = summary["count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError(f"count 必须是非负整数，实际为 {count!r}")

    total = _as_amount(summary["total"], "total")

    by_category = summary["by_category"]
    if not isinstance(by_category, Mapping):
        raise ValueError("by_category 必须是 {分类: 金额} 对象")

    lines = [f"共 {count} 笔", f"合计 {_format_money(total)}"]
    for category, amount in by_category.items():
        if not isinstance(category, str):
            raise ValueError(
                f"by_category 的分类名必须是字符串，实际为 {type(category).__name__}"
            )
        value = _as_amount(amount, f"by_category[{category!r}]")
        lines.append(f"  - {category}: {_format_money(value)}")
    return "\n".join(lines)
