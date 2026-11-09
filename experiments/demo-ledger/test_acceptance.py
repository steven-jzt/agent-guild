#!/usr/bin/env python3
"""端到端验收测试（编排者所有，实现 agent 不得修改）。

运行：python -m unittest test_acceptance -v
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def make_temp_dir() -> Path:
    """在项目内 .tmp/ 下建临时目录。

    本机 DSH Windows 沙箱下，由 tempfile 创建的子目录（0o700 ACL）不可写
    （PermissionError），因此这里用带默认权限的 mkdir 建目录。
    """
    root = ROOT / ".tmp"
    root.mkdir(exist_ok=True)
    path = root / f"acceptance-{uuid.uuid4().hex[:8]}"
    path.mkdir()
    return path


def run_cli(args, env):
    return subprocess.run(
        [sys.executable, str(ROOT / "cli.py"), *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


class AcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = make_temp_dir()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.data = self.tmpdir / "ledger.jsonl"
        self.env = os.environ.copy()
        self.env["LEDGER_DATA"] = str(self.data)
        self.env["PYTHONIOENCODING"] = "utf-8"
        self.env["PYTHONUTF8"] = "1"

    # ---------- helpers ----------
    def entries(self):
        if not self.data.exists():
            return []
        return [
            json.loads(line)
            for line in self.data.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def add_ok(self, *args):
        r = run_cli(["add", *args], self.env)
        self.assertEqual(
            r.returncode, 0, msg=f"add 失败: {args} stdout={r.stdout!r} stderr={r.stderr!r}"
        )
        return r

    # ---------- tests ----------
    def test_00_empty_list(self):
        r = run_cli(["list"], self.env)
        self.assertEqual(r.returncode, 0, msg=f"stderr={r.stderr!r}")
        self.assertIn("暂无记录", r.stdout)
        self.assertFalse(self.data.exists())

    def test_01_add_expense(self):
        r = self.add_ok("--amount", "12.34", "--category", "餐饮", "--note", "午饭")
        self.assertRegex(r.stdout.strip(), r"^已记录 [0-9a-f]{12}$")
        rows = self.entries()
        self.assertEqual(len(rows), 1, msg=f"文件内容={rows!r}")
        e = rows[0]
        for key in ("id", "date", "amount", "category", "note"):
            self.assertIn(key, e)
        self.assertAlmostEqual(e["amount"], 12.34, places=2)
        self.assertEqual(e["category"], "餐饮")
        self.assertEqual(e["note"], "午饭")
        self.assertRegex(e["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(e["id"], r"^[0-9a-f]{12}$")

    def test_02_add_income_and_unique_ids(self):
        self.add_ok("--amount", "12.34", "--category", "餐饮", "--note", "午饭")
        self.add_ok("--amount", "-20", "--category", "工资")
        rows = self.entries()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({e["id"] for e in rows}), 2, msg="id 应唯一")
        self.assertAlmostEqual(rows[1]["amount"], -20.0, places=2)
        self.assertEqual(rows[1]["note"], "")

    def test_03_invalid_amount_exit_1(self):
        self.add_ok("--amount", "12.34", "--category", "餐饮")
        r = run_cli(["add", "--amount", "abc", "--category", "餐饮"], self.env)
        self.assertEqual(r.returncode, 1, msg=f"stdout={r.stdout!r} stderr={r.stderr!r}")
        self.assertTrue(r.stderr.strip().startswith("错误"), msg=f"stderr={r.stderr!r}")
        self.assertEqual(len(self.entries()), 1, msg="失败时不应写入记录")

    def test_04_add_with_explicit_date(self):
        self.add_ok(
            "--amount", "5", "--category", "交通", "--date", "2026-02-03", "--note", "地铁"
        )
        self.assertEqual(self.entries()[-1]["date"], "2026-02-03")

    def test_05_list_format(self):
        self.add_ok("--amount", "12.34", "--category", "餐饮", "--note", "午饭")
        self.add_ok("--amount", "-20", "--category", "工资")
        r = run_cli(["list"], self.env)
        self.assertEqual(r.returncode, 0, msg=f"stderr={r.stderr!r}")
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2, msg=f"stdout={r.stdout!r}")
        self.assertRegex(lines[0], r"^\d{4}-\d{2}-\d{2}\s+餐饮\s+12\.34")
        self.assertRegex(lines[1], r"^\d{4}-\d{2}-\d{2}\s+工资\s+-20\.00")

    def test_06_list_limit(self):
        self.add_ok("--amount", "12.34", "--category", "餐饮")
        self.add_ok("--amount", "-20", "--category", "工资")
        r = run_cli(["list", "--limit", "1"], self.env)
        self.assertEqual(r.returncode, 0, msg=f"stderr={r.stderr!r}")
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, msg=f"stdout={r.stdout!r}")
        self.assertIn("工资", lines[0])

    def test_07_report_output(self):
        self.add_ok("--amount", "12.34", "--category", "餐饮", "--note", "午饭")
        self.add_ok("--amount", "-20", "--category", "工资")
        r = run_cli(["report"], self.env)
        self.assertEqual(r.returncode, 0, msg=f"stderr={r.stderr!r}")
        out = r.stdout
        self.assertIn("共 2 笔", out)
        self.assertIn("合计 -7.66", out)
        self.assertIn("- 餐饮: 12.34", out)
        self.assertIn("- 工资: -20.00", out)

    def test_08_no_command_exit_2(self):
        r = run_cli([], self.env)
        self.assertEqual(r.returncode, 2, msg=f"stderr={r.stderr!r}")

    def test_09_corrupt_file(self):
        self.data.write_text("not-json\n", encoding="utf-8")
        r = run_cli(["list"], self.env)
        self.assertEqual(r.returncode, 1, msg=f"stdout={r.stdout!r}")
        self.assertIn("错误", r.stderr)
        self.assertIn("1", r.stderr, msg="错误消息应包含行号")

    def test_10_module_api(self):
        import report  # noqa: PLC0415
        import storage  # noqa: PLC0415

        empty = report.summarize([])
        self.assertEqual(empty, {"count": 0, "total": 0.0, "by_category": {}})

        entries = [
            {"id": "a", "date": "2026-01-01", "amount": 0.1, "category": "x", "note": ""},
            {"id": "b", "date": "2026-01-02", "amount": 0.2, "category": "x", "note": ""},
            {"id": "c", "date": "2026-01-03", "amount": -1.05, "category": "y", "note": ""},
        ]
        snapshot = json.dumps(entries, sort_keys=True)
        summary = report.summarize(entries)
        self.assertEqual(json.dumps(entries, sort_keys=True), snapshot, msg="summarize 不得修改入参")
        self.assertEqual(summary["count"], 3)
        self.assertAlmostEqual(summary["total"], -0.75, places=2)
        self.assertAlmostEqual(summary["by_category"]["x"], 0.3, places=2)
        self.assertAlmostEqual(summary["by_category"]["y"], -1.05, places=2)

        text = report.format_report(summary)
        self.assertIn("共 3 笔", text)
        self.assertIn("合计 -0.75", text)
        self.assertIn("- x: 0.30", text)
        self.assertIn("- y: -1.05", text)
        self.assertFalse(text.endswith("\n"), msg="format_report 末尾不应有换行")

        old = os.environ.get("LEDGER_DATA")
        try:
            os.environ["LEDGER_DATA"] = str(self.data)
            self.assertEqual(Path(storage.data_path()).resolve(), self.data.resolve())
        finally:
            if old is None:
                os.environ.pop("LEDGER_DATA", None)
            else:
                os.environ["LEDGER_DATA"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
