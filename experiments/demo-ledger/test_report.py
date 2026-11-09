"""test_report.py — ``report`` 模块单元测试（所有者：agent-report）。

运行：``python -m unittest test_report -v``

只依赖标准库与 ``report`` 模块，不 import ``storage`` / ``cli``，也不读写任何数据文件；
覆盖正常路径、空列表、入参不被修改、舍入、负金额/收入，以及多组错误与边界输入。
"""

from __future__ import annotations

import copy
import json
import math
import unittest

import report


def make_entry(
    amount: float,
    category: str,
    note: str = "",
    *,
    entry_id: str = "0123456789ab",
    date: str = "2026-11-06",
) -> dict:
    """构造一条符合契约数据模型的记录。"""
    return {
        "id": entry_id,
        "date": date,
        "amount": amount,
        "category": category,
        "note": note,
    }


class SummarizeHappyPathTest(unittest.TestCase):
    """summarize 的正常路径。"""

    def test_mixed_expense_and_income(self):
        entries = [
            make_entry(12.34, "餐饮", "午饭", entry_id="aaaaaaaaaaaa"),
            make_entry(-20.0, "工资", entry_id="bbbbbbbbbbbb"),
            make_entry(5.66, "餐饮", "晚饭", entry_id="cccccccccccc"),
            make_entry(0.0, "交通", entry_id="dddddddddddd"),
        ]
        summary = report.summarize(entries)

        self.assertEqual(summary["count"], 4)
        self.assertEqual(summary["total"], round(12.34 - 20.0 + 5.66 + 0.0, 2))
        self.assertEqual(summary["by_category"]["餐饮"], 18.0)
        self.assertEqual(summary["by_category"]["工资"], -20.0)
        self.assertEqual(summary["by_category"]["交通"], 0.0)
        self.assertEqual(list(summary["by_category"]), ["餐饮", "工资", "交通"])

    def test_single_entry(self):
        summary = report.summarize([make_entry(0.01, "餐饮")])
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["total"], 0.01)
        self.assertEqual(summary["by_category"], {"餐饮": 0.01})

    def test_returns_only_contract_keys(self):
        summary = report.summarize([make_entry(1.5, "交通")])
        self.assertEqual(set(summary), {"count", "total", "by_category"})
        self.assertIsInstance(summary["count"], int)
        self.assertIsInstance(summary["total"], float)
        self.assertIsInstance(summary["by_category"], dict)

    def test_accepts_int_amounts(self):
        summary = report.summarize([make_entry(3, "交通"), make_entry(-1, "交通")])
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["total"], 2.0)
        self.assertEqual(summary["by_category"], {"交通": 2.0})

    def test_does_not_require_storage_fields(self):
        """只依赖 amount / category，便于独立于 storage 使用。"""
        summary = report.summarize([{"amount": 2.5, "category": "餐饮"}])
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["total"], 2.5)


class SummarizeEdgeCaseTest(unittest.TestCase):
    """空列表、入参保护、舍入、负金额等边界。"""

    def test_empty_list_exact_value(self):
        summary = report.summarize([])
        self.assertEqual(summary, {"count": 0, "total": 0.0, "by_category": {}})
        self.assertIsInstance(summary["total"], float)

    def test_empty_list_format(self):
        text = report.format_report(report.summarize([]))
        self.assertEqual(text, "共 0 笔\n合计 0.00")
        self.assertFalse(text.endswith("\n"))

    def test_entries_are_not_modified(self):
        entries = [
            make_entry(12.34, "餐饮", "午饭", entry_id="aaaaaaaaaaaa"),
            make_entry(-20.0, "工资", entry_id="bbbbbbbbbbbb"),
            make_entry(0.0, "交通", note="步行", entry_id="cccccccccccc"),
        ]
        snapshot = copy.deepcopy(entries)
        summary = report.summarize(entries)

        self.assertEqual(entries, snapshot, "summarize 不得修改入参")
        self.assertEqual(
            json.dumps(entries, ensure_ascii=False, sort_keys=True),
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
        )
        # 输出中的金额也不应是入参里某个 float 对象的别名式篡改（数量与值都保持）
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["amount"], 12.34)
        self.assertEqual(summary["total"], round(12.34 - 20.0, 2))

    def test_rounds_total_and_categories_to_two_places(self):
        raw = 1.2345
        summary = report.summarize([make_entry(raw, "餐饮"), make_entry(0.005, "交通"), make_entry(0.005, "交通")])
        self.assertEqual(summary["total"], round(summary["total"], 2))
        self.assertEqual(summary["total"], round(math.fsum([raw, 0.005, 0.005]), 2))
        self.assertEqual(summary["by_category"]["餐饮"], round(raw, 2))
        self.assertEqual(summary["by_category"]["交通"], 0.01)
        for value in [summary["total"], *summary["by_category"].values()]:
            self.assertEqual(value, round(value, 2))

    def test_float_accumulation_artifacts_are_rounded_away(self):
        summary = report.summarize([make_entry(0.1, "餐饮"), make_entry(0.2, "餐饮")])
        self.assertEqual(summary["total"], 0.3)
        self.assertEqual(summary["by_category"]["餐饮"], 0.3)
        text = report.format_report(summary)
        self.assertIn("合计 0.30", text)
        self.assertIn("  - 餐饮: 0.30", text)
        self.assertNotIn("0.30000000000000004", text)

    def test_negative_amounts_and_negative_total(self):
        entries = [make_entry(-20.0, "工资"), make_entry(-0.5, "退款")]
        summary = report.summarize(entries)
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["total"], -20.5)
        self.assertEqual(summary["by_category"], {"工资": -20.0, "退款": -0.5})

        text = report.format_report(summary)
        self.assertIn("共 2 笔", text)
        self.assertIn("合计 -20.50", text)
        self.assertIn("  - 工资: -20.00", text)
        self.assertIn("  - 退款: -0.50", text)

    def test_negative_zero_is_normalized(self):
        summary = report.summarize([make_entry(-0.0, "交通"), make_entry(0.0, "交通")])
        self.assertEqual(summary["total"], 0.0)
        self.assertEqual(math.copysign(1.0, summary["total"]), 1.0, "total 不应是 -0.0")
        text = report.format_report(summary)
        self.assertIn("合计 0.00", text)
        self.assertIn("  - 交通: 0.00", text)
        self.assertNotIn("-0.00", text)


class FormatReportTest(unittest.TestCase):
    """format_report 的格式要求。"""

    def test_exact_multiline_output(self):
        entries = [
            make_entry(12.34, "餐饮", "午饭"),
            make_entry(-20.0, "工资"),
        ]
        text = report.format_report(report.summarize(entries))
        expected = "共 2 笔\n合计 -7.66\n  - 餐饮: 12.34\n  - 工资: -20.00"
        self.assertEqual(text, expected)
        self.assertFalse(text.endswith("\n"), "末尾不应有换行")

    def test_category_line_shape(self):
        text = report.format_report(
            {"count": 1, "total": 12.34, "by_category": {"餐饮": 12.34}}
        )
        self.assertRegex(text, r"(?m)^  - 餐饮: 12\.34$")
        self.assertEqual(len(text.split("\n")), 3)

    def test_two_decimal_places_even_for_round_numbers(self):
        text = report.format_report(
            {"count": 3, "total": 7.0, "by_category": {"餐饮": 5.0, "交通": 2}}
        )
        self.assertIn("合计 7.00", text)
        self.assertIn("  - 餐饮: 5.00", text)
        self.assertIn("  - 交通: 2.00", text)

    def test_empty_summary(self):
        text = report.format_report({"count": 0, "total": 0.0, "by_category": {}})
        self.assertEqual(text, "共 0 笔\n合计 0.00")
        self.assertFalse(text.endswith("\n"))

    def test_manually_built_summary_is_supported(self):
        text = report.format_report(
            {"count": 2, "total": -0.75, "by_category": {"x": 0.3, "y": -1.05}}
        )
        self.assertIn("共 2 笔", text)
        self.assertIn("合计 -0.75", text)
        self.assertIn("  - x: 0.30", text)
        self.assertIn("  - y: -1.05", text)
        self.assertFalse(text.endswith("\n"))


class SummarizeErrorTest(unittest.TestCase):
    """summarize 的错误输入。"""

    def assert_invalid(self, entries):
        with self.assertRaises(ValueError):
            report.summarize(entries)

    def test_none(self):
        self.assert_invalid(None)

    def test_not_a_collection_of_entries(self):
        for bad in ("abc", b"abc", {"amount": 1.0, "category": "x"}):
            with self.subTest(bad=bad):
                self.assert_invalid(bad)

    def test_entry_not_a_mapping(self):
        self.assert_invalid(["not a dict"])
        self.assert_invalid([None])

    def test_missing_amount(self):
        self.assert_invalid([{"category": "餐饮"}])

    def test_missing_category(self):
        self.assert_invalid([{"amount": 1.0}])

    def test_invalid_category(self):
        for bad in ("", 123, None, ["餐饮"]):
            with self.subTest(bad=bad):
                self.assert_invalid([{"amount": 1.0, "category": bad}])

    def test_amount_must_be_number(self):
        for bad in (True, False, "12.34", None, [1.0], {}, object()):
            with self.subTest(bad=bad):
                self.assert_invalid([{"amount": bad, "category": "餐饮"}])

    def test_amount_must_be_finite(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                self.assert_invalid([{"amount": bad, "category": "餐饮"}])

    def test_error_in_later_entry_reports_position(self):
        entries = [make_entry(1.0, "餐饮"), {"category": "交通"}]
        with self.assertRaisesRegex(ValueError, "第 2 条"):
            report.summarize(entries)


class FormatReportErrorTest(unittest.TestCase):
    """format_report 的错误输入。"""

    def test_not_a_mapping(self):
        for bad in (None, [], "summary", 1.0):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    report.format_report(bad)

    def test_missing_fields(self):
        cases = [
            {},
            {"count": 0, "total": 0.0},
            {"count": 0, "by_category": {}},
            {"total": 0.0, "by_category": {}},
        ]
        for summary in cases:
            with self.subTest(summary=summary):
                with self.assertRaises(ValueError):
                    report.format_report(summary)

    def test_invalid_count(self):
        for bad in ("1", 1.0, -1, True, None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    report.format_report({"count": bad, "total": 1.0, "by_category": {}})

    def test_invalid_total(self):
        for bad in ("1.0", float("nan"), float("inf"), True, None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    report.format_report({"count": 1, "total": bad, "by_category": {}})

    def test_invalid_by_category(self):
        for bad in ([], "餐饮", 1.0):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    report.format_report({"count": 1, "total": 1.0, "by_category": bad})

    def test_invalid_category_amount_or_name(self):
        for bad_category in ({"餐饮": "1.0"}, {"餐饮": float("nan")}, {123: 1.0}):
            with self.subTest(bad_category=bad_category):
                with self.assertRaises(ValueError):
                    report.format_report(
                        {"count": 1, "total": 1.0, "by_category": bad_category}
                    )

    def test_empty_summary_does_not_raise(self):
        report.format_report({"count": 0, "total": 0.0, "by_category": {}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
