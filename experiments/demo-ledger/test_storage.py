"""storage.py 的单元测试（unittest，仅标准库）。

隔离方式：每个测试使用独立的临时目录，并通过 ``LEDGER_DATA`` 指向其中的
``ledger.jsonl``，绝不写入项目内的 ``data/`` 目录。

运行：python -m unittest test_storage -v
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

import storage

ID_PATTERN = r"^[0-9a-f]{12}$"


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_ledger_data = os.environ.get("LEDGER_DATA")
        # 不用 TemporaryDirectory/mkdtemp：它们以 0o700 创建目录，在部分受限
        # Windows 环境中会导致后续文件写入被拒绝。这里仍由 tempfile 生成唯一
        # 名称，只是以默认权限创建目录。
        self._tmp_path = Path(tempfile.mktemp(prefix="ledger-storage-test-"))
        self._tmp_path.mkdir()
        self.addCleanup(self._cleanup)
        self.data = self._tmp_path / "ledger.jsonl"
        os.environ["LEDGER_DATA"] = str(self.data)

    def _cleanup(self) -> None:
        if self._old_ledger_data is None:
            os.environ.pop("LEDGER_DATA", None)
        else:
            os.environ["LEDGER_DATA"] = self._old_ledger_data
        shutil.rmtree(self._tmp_path, ignore_errors=True)

    def _write_lines(self, *lines: str) -> None:
        self.data.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------- PROJECT_ROOT / data_path ----------

    def test_project_root_is_storage_directory(self) -> None:
        self.assertEqual(storage.PROJECT_ROOT, Path(storage.__file__).resolve().parent)
        self.assertTrue(storage.PROJECT_ROOT.is_dir())

    def test_data_path_uses_ledger_data_on_every_call(self) -> None:
        self.assertEqual(storage.data_path(), self.data)
        other = Path(self._tmp_path) / "other.jsonl"
        os.environ["LEDGER_DATA"] = str(other)
        self.assertEqual(storage.data_path(), other)
        self.assertFalse(other.exists(), "data_path() 不应创建文件")

    def test_data_path_relative_is_resolved_against_project_root(self) -> None:
        relative = Path("some") / "nested" / "ledger.jsonl"
        os.environ["LEDGER_DATA"] = str(relative)
        self.assertEqual(storage.data_path(), storage.PROJECT_ROOT / relative)

    def test_data_path_default_when_env_missing_or_empty(self) -> None:
        old = os.environ.pop("LEDGER_DATA", None)
        try:
            expected = storage.PROJECT_ROOT / "data" / "ledger.jsonl"
            self.assertEqual(storage.data_path(), expected)
            os.environ["LEDGER_DATA"] = ""
            self.assertEqual(storage.data_path(), expected)
        finally:
            if old is not None:
                os.environ["LEDGER_DATA"] = old

    # ---------- append_entry：正常路径 ----------

    def test_append_entry_returns_and_writes_complete_record(self) -> None:
        entry = {
            "amount": 12.34,
            "category": "餐饮",
            "note": "午饭",
            "date": "2026-02-03",
        }
        snapshot = copy.deepcopy(entry)

        record = storage.append_entry(entry)

        self.assertEqual(entry, snapshot, "append_entry 不得修改入参")
        self.assertRegex(record["id"], ID_PATTERN)
        self.assertEqual(record["date"], "2026-02-03")
        self.assertEqual(record["amount"], 12.34)
        self.assertEqual(record["category"], "餐饮")
        self.assertEqual(record["note"], "午饭")
        self.assertEqual(
            set(record), {"id", "date", "amount", "category", "note"}
        )

        raw = self.data.read_bytes()
        self.assertTrue(raw.endswith(b"\n"), "每行应以 \\n 结尾")
        self.assertNotIn(b"\r\n", raw, "行尾应是 \\n 而不是 \\r\\n")
        self.assertIn("餐饮".encode("utf-8"), raw, "应使用 ensure_ascii=False")

        lines = self.data.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), record)

    def test_append_entry_fills_defaults_without_touching_input(self) -> None:
        entry = {"amount": -20, "category": "工资"}
        before = date.today()
        record = storage.append_entry(entry)
        after = date.today()

        self.assertEqual(set(entry), {"amount", "category"}, "不得向入参添加键")
        self.assertRegex(record["id"], ID_PATTERN)
        self.assertIn(
            record["date"],
            {before.isoformat(), after.isoformat()},
            "默认日期应为本地时区今天",
        )
        self.assertEqual(record["amount"], -20.0)
        self.assertEqual(record["note"], "")

    def test_append_entry_creates_parent_directories(self) -> None:
        nested = Path(self._tmp_path) / "sub" / "dir" / "ledger.jsonl"
        os.environ["LEDGER_DATA"] = str(nested)

        record = storage.append_entry({"amount": 1, "category": "测试"})

        self.assertTrue(nested.is_file())
        self.assertEqual(storage.load_entries(), [record])

    def test_append_entry_keeps_explicit_id_and_date(self) -> None:
        entry = {
            "id": "0123456789ab",
            "date": "2026-02-03",
            "amount": 0,
            "category": "交通",
            "note": "地铁",
        }
        record = storage.append_entry(entry)
        self.assertEqual(record["id"], "0123456789ab")
        self.assertEqual(record["date"], "2026-02-03")
        self.assertEqual(record["amount"], 0.0)

    # ---------- load_entries：正常路径 ----------

    def test_load_entries_missing_file_returns_empty_list(self) -> None:
        self.assertFalse(self.data.exists())
        self.assertEqual(storage.load_entries(), [])
        self.assertFalse(self.data.exists(), "load_entries 不应创建文件")

    def test_load_entries_skips_blank_lines_and_keeps_order(self) -> None:
        first = storage.append_entry(
            {"amount": 12.34, "category": "餐饮", "note": "午饭"}
        )
        second = storage.append_entry({"amount": -20, "category": "工资"})
        self._write_lines(
            "",
            json.dumps(first, ensure_ascii=False),
            "   ",
            "",
            json.dumps(second, ensure_ascii=False),
            "",
        )

        self.assertEqual(storage.load_entries(), [first, second])

    # ---------- load_entries：错误路径 ----------

    def test_load_entries_corrupt_json_reports_physical_line_number(self) -> None:
        valid = {
            "id": "0123456789ab",
            "date": "2026-01-01",
            "amount": 1,
            "category": "餐饮",
            "note": "",
        }
        self._write_lines(json.dumps(valid, ensure_ascii=False), "", "not-json")

        with self.assertRaises(ValueError) as caught:
            storage.load_entries()

        message = str(caught.exception)
        self.assertIn("第 3 行", message)
        self.assertIn("3", message)

    def test_load_entries_rejects_non_object_line(self) -> None:
        self._write_lines("[1, 2, 3]")
        with self.assertRaises(ValueError) as caught:
            storage.load_entries()
        self.assertIn("第 1 行", str(caught.exception))

    def test_load_entries_rejects_invalid_amount(self) -> None:
        template = (
            '{"id": "0123456789ab", "date": "2026-01-01", '
            '"amount": %s, "category": "餐饮", "note": ""}'
        )
        for literal in ("\"12.34\"", "true", "null", "NaN", "Infinity", "[]", "{}"):
            with self.subTest(amount=literal):
                self._write_lines(template % literal)
                with self.assertRaises(ValueError) as caught:
                    storage.load_entries()
                self.assertIn("第 1 行", str(caught.exception))

    def test_load_entries_rejects_bad_date_or_missing_key(self) -> None:
        cases = {
            "斜杠日期": (
                '{"id": "0123456789ab", "date": "2026/01/01", '
                '"amount": 1, "category": "餐饮", "note": ""}'
            ),
            "不存在的日期": (
                '{"id": "0123456789ab", "date": "2026-13-01", '
                '"amount": 1, "category": "餐饮", "note": ""}'
            ),
            "缺少 note": (
                '{"id": "0123456789ab", "date": "2026-01-01", '
                '"amount": 1, "category": "餐饮"}'
            ),
        }
        for label, line in cases.items():
            with self.subTest(case=label):
                self._write_lines(line)
                with self.assertRaises(ValueError) as caught:
                    storage.load_entries()
                self.assertIn("第 1 行", str(caught.exception))

    # ---------- append_entry：错误路径 ----------

    def test_append_entry_rejects_invalid_amount_without_writing(self) -> None:
        for amount in ("12.34", True, None, float("nan"), float("inf")):
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError):
                    storage.append_entry({"amount": amount, "category": "餐饮"})
                self.assertFalse(self.data.exists(), "校验失败时不应写入文件")

    def test_append_entry_rejects_invalid_fields_without_writing(self) -> None:
        cases = {
            "缺少 amount": {"category": "餐饮"},
            "缺少 category": {"amount": 1},
            "空 category": {"amount": 1, "category": ""},
            "category 非字符串": {"amount": 1, "category": 123},
            "date 格式错误": {"amount": 1, "category": "餐饮", "date": "2026/01/01"},
            "date 不是合法日期": {
                "amount": 1,
                "category": "餐饮",
                "date": "2026-13-01",
            },
            "note 非字符串": {"amount": 1, "category": "餐饮", "note": 123},
            "id 格式错误": {"amount": 1, "category": "餐饮", "id": "XYZ"},
        }
        for label, entry in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    storage.append_entry(entry)
                self.assertFalse(self.data.exists(), "校验失败时不应写入文件")

    def test_append_entry_rejects_non_dict(self) -> None:
        for value in (None, [], "entry", 42):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    storage.append_entry(value)  # type: ignore[arg-type]
                self.assertFalse(self.data.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
