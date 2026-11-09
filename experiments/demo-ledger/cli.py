#!/usr/bin/env python3
"""ledger 个人记账 CLI。

契约见 CONTRACT.md 第 5 节：

    main(argv: list[str] | None = None) -> int

- add / list / report 三个子命令；
- 无子命令打印帮助并返回 2；
- 所有错误向 stderr 打印 ``错误: <消息>`` 并返回 1（含 argparse 的类型错误）。
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from datetime import date as _date

import report
import storage

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CliError(Exception):
    """命令行的参数错误或用户输入错误。"""


class LedgerArgumentParser(argparse.ArgumentParser):
    """把 argparse 的报错/退出统一转换成 CliError。

    这样 --amount abc 之类的类型错误不会被 argparse 直接 ``sys.exit(2)``，
    而是由 main() 打印 ``错误: ...`` 并返回 1。
    """

    def error(self, message: str) -> None:
        raise CliError(message)


def _amount(value: str) -> float:
    """把字符串转成有限浮点数（拒绝 NaN / Infinity）。"""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("金额必须是数字") from None
    if not math.isfinite(amount):
        raise argparse.ArgumentTypeError("金额必须是有限数字（不能是 NaN 或 Infinity）")
    # 把 -0.0 归一成 0.0，避免显示成 -0.00。
    return 0.0 if amount == 0.0 else amount


def _category(value: str) -> str:
    if value == "":
        raise argparse.ArgumentTypeError("分类不能为空")
    return value


def _date_value(value: str) -> str:
    if not _DATE_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("日期格式应为 YYYY-MM-DD")
    try:
        _date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"不是有效的日期: {value}") from None
    return value


def _limit(value: str) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("--limit 必须是整数") from None
    if limit < 0:
        raise argparse.ArgumentTypeError("--limit 不能为负数")
    return limit


def build_parser() -> argparse.ArgumentParser:
    parser = LedgerArgumentParser(
        prog="cli.py",
        description="个人记账 CLI（数据为 JSON Lines）",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="命令", required=False)

    add_parser = subparsers.add_parser("add", help="新增一条记录")
    add_parser.add_argument(
        "--amount",
        required=True,
        type=_amount,
        metavar="FLOAT",
        help="金额；正数=支出，负数=收入，允许 0",
    )
    add_parser.add_argument(
        "--category",
        required=True,
        type=_category,
        metavar="STR",
        help="分类（非空字符串）",
    )
    add_parser.add_argument("--note", default=None, metavar="STR", help="备注，可为空")
    add_parser.add_argument(
        "--date",
        default=None,
        type=_date_value,
        metavar="YYYY-MM-DD",
        help="日期，默认使用本地时区今天",
    )

    list_parser = subparsers.add_parser("list", help="按文件顺序列出记录")
    list_parser.add_argument(
        "--limit",
        default=None,
        type=_limit,
        metavar="N",
        help="只显示最后 N 条（N 为非负整数）",
    )

    subparsers.add_parser("report", help="输出汇总报告")
    return parser


def _cmd_add(args: argparse.Namespace) -> int:
    entry: dict[str, object] = {
        "amount": args.amount,
        "category": args.category,
    }
    if args.note is not None:
        entry["note"] = args.note
    if args.date is not None:
        entry["date"] = args.date

    saved = storage.append_entry(entry)
    print(f"已记录 {saved['id']}")
    return 0


def _format_entry(entry: dict) -> str:
    amount = entry["amount"]
    line = f"{entry['date']}  {entry['category']}  {amount:.2f}"
    note = entry.get("note", "")
    if note:
        line = f"{line}  {note}"
    return line


def _cmd_list(args: argparse.Namespace) -> int:
    entries = storage.load_entries()
    if not entries:
        print("暂无记录")
        return 0
    if args.limit is not None:
        if args.limit == 0:
            # “最后 0 条”即不打印任何记录。
            return 0
        entries = entries[-args.limit:]
    for entry in entries:
        print(_format_entry(entry))
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    entries = storage.load_entries()
    print(report.format_report(report.summarize(entries)))
    return 0


_COMMANDS = {
    "add": _cmd_add,
    "list": _cmd_list,
    "report": _cmd_report,
}


def main(argv: list[str] | None = None) -> int:
    """CLI 入口；返回进程退出码而不是调用 sys.exit()。"""
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    if not arguments:
        parser.print_help()
        return 2

    try:
        args = parser.parse_args(arguments)
    except CliError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    command = getattr(args, "command", None)
    if command is None:
        # 理论上走不到（空 argv 已提前处理），保留兜底以贴合契约。
        parser.print_help()
        return 2

    try:
        return _COMMANDS[command](args)
    except (CliError, ValueError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # 兜底：契约要求所有错误都返回 1
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
