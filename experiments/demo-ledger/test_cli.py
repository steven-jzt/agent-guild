#!/usr/bin/env python3
"""cli.py 的单元测试。

运行：python -m unittest test_cli -v

每个测试都使用独立的临时目录，并通过环境变量 LEDGER_DATA 隔离数据，
不会读写项目内的 data/ 目录。测试只依赖标准库。
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli  # noqa: E402  （必须在 sys.path 调整之后导入）


def make_temp_dir() -> Path:
    """在系统临时目录下创建本次测试专用的目录。

    这里不用 ``tempfile.TemporaryDirectory``：它内部以 mode 0o700 建目录，
    在某些受限的 Windows 沙箱里该目录拿不到写/删权限（WinError 5）。
    用 ``tempfile.gettempdir()`` + 默认权限 ``mkdir()`` 同样做到隔离，
    且能可靠清理。
    """
    directory = Path(tempfile.gettempdir()) / f"ledger_cli_test_{uuid.uuid4().hex}"
    directory.mkdir()
    return directory


def run_main(argv):
    """调用 cli.main(argv)，返回 (退出码, stdout, stderr)。"""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = make_temp_dir()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.data_file = self.tmpdir / "ledger.jsonl"
        self._previous_data = os.environ.get("LEDGER_DATA")
        os.environ["LEDGER_DATA"] = str(self.data_file)
        self.addCleanup(self._restore_ledger_data)

    def _restore_ledger_data(self):
        if self._previous_data is None:
            os.environ.pop("LEDGER_DATA", None)
        else:
            os.environ["LEDGER_DATA"] = self._previous_data

    # ---------- helpers ----------
    def add_ok(self, *args):
        code, out, err = run_main(["add", *args])
        self.assertEqual(code, 0, msg=f"add 失败: stdout={out!r} stderr={err!r}")
        return out

    def read_entries(self):
        if not self.data_file.exists():
            return []
        text = self.data_file.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    # ---------- add ----------
    def test_add_expense_writes_expected_entry(self):
        out = self.add_ok("--amount", "12.34", "--category", "餐饮", "--note", "午饭")
        self.assertRegex(out.strip(), r"^已记录 [0-9a-f]{12}$")

        entries = self.read_entries()
        self.assertEqual(len(entries), 1, msg=f"文件内容={entries!r}")
        entry = entries[0]
        for key in ("id", "date", "amount", "category", "note"):
            self.assertIn(key, entry)
        self.assertRegex(entry["id"], r"^[0-9a-f]{12}$")
        self.assertRegex(entry["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertAlmostEqual(entry["amount"], 12.34, places=2)
        self.assertEqual(entry["category"], "餐饮")
        self.assertEqual(entry["note"], "午饭")

    def test_add_income_without_note_defaults_to_empty(self):
        self.add_ok("--amount", "-20", "--category", "工资")
        entry = self.read_entries()[0]
        self.assertAlmostEqual(entry["amount"], -20.0, places=2)
        self.assertEqual(entry["note"], "")

    def test_add_with_explicit_date_and_zero_amount(self):
        self.add_ok("--amount", "0", "--category", "其他", "--date", "2026-02-03")
        entry = self.read_entries()[0]
        self.assertEqual(entry["date"], "2026-02-03")
        self.assertEqual(entry["amount"], 0)

        code, out, err = run_main(["list"])
        self.assertEqual(code, 0, msg=err)
        self.assertRegex(out, r"2026-02-03\s+其他\s+0\.00")

    def test_add_invalid_amount_returns_1_and_writes_nothing(self):
        code, out, err = run_main(["add", "--amount", "abc", "--category", "餐饮"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("错误: "), msg=f"stderr={err!r}")
        self.assertFalse(self.data_file.exists())

    def test_add_non_finite_amount_returns_1(self):
        for value in ("nan", "inf", "-inf", "infinity"):
            with self.subTest(amount=value):
                code, out, err = run_main(
                    ["add", "--amount", value, "--category", "餐饮"]
                )
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertTrue(err.startswith("错误: "), msg=f"stderr={err!r}")
        self.assertFalse(self.data_file.exists())

    def test_add_invalid_date_returns_1(self):
        for value in ("2026/02/03", "2026-2-3", "2026-13-01", "2026-02-31"):
            with self.subTest(date=value):
                code, out, err = run_main(
                    [
                        "add",
                        "--amount",
                        "1",
                        "--category",
                        "餐饮",
                        "--date",
                        value,
                    ]
                )
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertTrue(err.startswith("错误: "), msg=f"stderr={err!r}")
        self.assertFalse(self.data_file.exists())

    def test_add_empty_category_returns_1(self):
        code, out, err = run_main(["add", "--amount", "1", "--category", ""])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("错误: "), msg=f"stderr={err!r}")
        self.assertFalse(self.data_file.exists())

    def test_add_missing_required_argument_returns_1(self):
        code, out, err = run_main(["add", "--amount", "1"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("错误: "), msg=f"stderr={err!r}")

    def test_add_unknown_option_returns_1(self):
        code, out, err = run_main(
            ["add", "--amount", "1", "--category", "餐饮", "--bogus", "x"]
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("错误: "), msg=f"stderr={err!r}")

    # ---------- list ----------
    def test_list_empty_ledger(self):
        code, out, err = run_main(["list"])
        self.assertEqual(code, 0, msg=err)
        self.assertIn("暂无记录", out)
        self.assertFalse(self.data_file.exists())

    def test_list_format_and_file_order(self):
        self.add_ok(
            "--amount",
            "12.34",
            "--category",
            "餐饮",
            "--note",
            "午饭",
            "--date",
            "2026-02-03",
        )
        self.add_ok(
            "--amount", "-20", "--category", "工资", "--date", "2026-02-04"
        )
        code, out, err = run_main(["list"])
        self.assertEqual(code, 0, msg=err)
        lines = out.splitlines()
        self.assertEqual(len(lines), 2, msg=f"stdout={out!r}")
        self.assertEqual(lines[0], "2026-02-03  餐饮  12.34  午饭")
        self.assertEqual(lines[1], "2026-02-04  工资  -20.00")

    def test_list_limit_returns_last_entries(self):
        for index, (category, amount) in enumerate(
            (("餐饮", "1"), ("交通", "2"), ("购物", "3")), start=1
        ):
            self.add_ok(
                "--amount",
                amount,
                "--category",
                category,
                "--date",
                f"2026-02-0{index}",
            )
        code, out, err = run_main(["list", "--limit", "2"])
        self.assertEqual(code, 0, msg=err)
        lines = out.splitlines()
        self.assertEqual(len(lines), 2, msg=f"stdout={out!r}")
        self.assertIn("交通", lines[0])
        self.assertIn("购物", lines[1])
        self.assertNotIn("餐饮", out)

    def test_list_limit_larger_than_ledger(self):
        self.add_ok("--amount", "1", "--category", "餐饮")
        code, out, err = run_main(["list", "--limit", "100"])
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(len(out.splitlines()), 1, msg=f"stdout={out!r}")

    def test_list_limit_on_empty_ledger(self):
        code, out, err = run_main(["list", "--limit", "3"])
        self.assertEqual(code, 0, msg=err)
        self.assertIn("暂无记录", out)

    def test_list_limit_zero_prints_nothing(self):
        self.add_ok("--amount", "1", "--category", "餐饮")
        code, out, err = run_main(["list", "--limit", "0"])
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(out, "")

    def test_list_invalid_limit_returns_1(self):
        self.add_ok("--amount", "1", "--category", "餐饮")
        for value in ("abc", "1.5", "-1"):
            with self.subTest(limit=value):
                code, out, err = run_main(["list", "--limit", value])
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertTrue(err.startswith("错误: "), msg=f"stderr={err!r}")

    def test_list_corrupt_file_returns_1_with_line_number(self):
        self.data_file.write_text("not-json\n", encoding="utf-8")
        code, out, err = run_main(["list"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("错误", err)
        self.assertIn("1", err, msg="错误消息应包含出错行号")

    # ---------- report ----------
    def test_report_empty_ledger(self):
        code, out, err = run_main(["report"])
        self.assertEqual(code, 0, msg=err)
        self.assertIn("共 0 笔", out)
        self.assertIn("合计 0.00", out)

    def test_report_totals_and_categories(self):
        self.add_ok("--amount", "12.34", "--category", "餐饮", "--note", "午饭")
        self.add_ok("--amount", "-20", "--category", "工资")
        code, out, err = run_main(["report"])
        self.assertEqual(code, 0, msg=err)
        for fragment in (
            "共 2 笔",
            "合计 -7.66",
            "- 餐饮: 12.34",
            "- 工资: -20.00",
        ):
            self.assertIn(fragment, out)

    def test_report_corrupt_file_returns_1(self):
        self.data_file.write_text("{bad json}\n", encoding="utf-8")
        code, out, err = run_main(["report"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("错误: "), msg=f"stderr={err!r}")

    # ---------- 其他 ----------
    def test_no_subcommand_returns_2_and_prints_help(self):
        code, out, err = run_main([])
        self.assertEqual(code, 2)
        self.assertIn("usage", out.lower())
        self.assertEqual(err, "")

    def test_unknown_subcommand_returns_1(self):
        code, out, err = run_main(["frobnicate"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("错误: "), msg=f"stderr={err!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
