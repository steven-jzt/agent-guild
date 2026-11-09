"""自动 verifier 测试。运行：python -m unittest test_verifier -v"""

from __future__ import annotations

import os
import shutil
import unittest
import uuid
from pathlib import Path

from coordinator import Coordinator, CoordinatorError
from verifier import auto_verify

ROOT = Path(__file__).resolve().parent
TMP = ROOT / ".tmp"


class VerifierTest(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)
        base = TMP / f"verify-{uuid.uuid4().hex}"
        base.mkdir()
        self.root = base
        self.db = base / "db.sqlite"
        self.c = Coordinator(self.db)
        self.p = self.c.create_project("ver", require_verification=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _commit(self, task_id: str) -> None:
        r = self.c.claim_task("W1", task_id=task_id)
        self.c.commit_task(task_id, "W1", r["lease"]["fencing_token"])

    def test_pass(self):
        (self.root / "out.md").write_text("# Part\ncoordination works\n", encoding="utf-8")
        t = self.c.create_task(
            self.p["project_id"], "t", ["out.md"],
            checks=[
                {"type": "file_exists", "path": "out.md"},
                {"type": "file_contains", "path": "out.md", "text": "coordination"},
                {"type": "file_regex", "path": "out.md", "pattern": r"^# Part$"},
            ],
        )
        self._commit(t["id"])
        result = auto_verify(self.c, t["id"], root=str(self.root))
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["score"], 1.0)
        self.assertEqual(result["status"], "done")

    def test_fail_reopens(self):
        t = self.c.create_task(
            self.p["project_id"], "t", ["missing.md"],
            checks=[{"type": "file_exists", "path": "missing.md"}],
        )
        self._commit(t["id"])
        result = auto_verify(self.c, t["id"], root=str(self.root))
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "ready")

    def test_no_checks_errors(self):
        t = self.c.create_task(self.p["project_id"], "t", ["a"])
        self._commit(t["id"])
        with self.assertRaises(CoordinatorError):
            auto_verify(self.c, t["id"], root=str(self.root))

    def test_path_escape_fails_safely(self):
        t = self.c.create_task(
            self.p["project_id"], "t", ["a"],
            checks=[{"type": "file_exists", "path": "../escape"}],
        )
        self._commit(t["id"])
        result = auto_verify(self.c, t["id"], root=str(self.root))
        self.assertFalse(result["passed"])
        self.assertIn("escapes root", result["checks"][0]["detail"])

    def test_command_disabled(self):
        t = self.c.create_task(
            self.p["project_id"], "t", ["a"],
            checks=[{"type": "command", "cmd": "python -c \"raise SystemExit(0)\""}],
        )
        self._commit(t["id"])
        result = auto_verify(self.c, t["id"], root=str(self.root))
        self.assertFalse(result["passed"])
        self.assertIn("disabled", result["checks"][0]["detail"])

    def test_command_allowlist_blocks_shell(self):
        t = self.c.create_task(
            self.p["project_id"], "t", ["a"],
            checks=[{"type": "command", "cmd": "cmd /c echo hi"}],
        )
        self._commit(t["id"])
        result = auto_verify(self.c, t["id"], root=str(self.root), allow_command=True)
        self.assertFalse(result["passed"])
        self.assertIn("not allowed", result["checks"][0]["detail"])

    def test_command_allowed_python(self):
        t = self.c.create_task(
            self.p["project_id"], "t", ["a"],
            checks=[{"type": "command", "cmd": "python -c \"print('ok')\""}],
        )
        self._commit(t["id"])
        result = auto_verify(self.c, t["id"], root=str(self.root), allow_command=True)
        self.assertTrue(result["passed"], result)

    def test_command_timeout(self):
        t = self.c.create_task(
            self.p["project_id"], "t", ["a"],
            checks=[{
                "type": "command",
                "cmd": "python -c \"import time; time.sleep(2)\"",
                "timeout": 0.2,
            }],
        )
        self._commit(t["id"])
        result = auto_verify(self.c, t["id"], root=str(self.root), allow_command=True)
        self.assertFalse(result["passed"])
        self.assertIn("TimeoutExpired", result["checks"][0]["detail"])

    def test_command_output_truncated(self):
        t = self.c.create_task(
            self.p["project_id"], "t", ["a"],
            checks=[{
                "type": "command",
                "cmd": "python -c \"print('x' * 5000)\"",
                "max_output_chars": 100,
            }],
        )
        self._commit(t["id"])
        result = auto_verify(self.c, t["id"], root=str(self.root), allow_command=True)
        self.assertTrue(result["passed"], result)
        detail = result["checks"][0]["detail"]
        self.assertLess(len(detail), 200)
        self.assertIn("truncated", detail)

    def test_command_env_whitelist(self):
        os.environ["P0_SECRET"] = "leak"
        try:
            t = self.c.create_task(
                self.p["project_id"], "t", ["a"],
                checks=[{
                    "type": "command",
                    "cmd": "python -c \"import os; print(os.getenv('P0_SECRET', 'missing'))\"",
                }],
            )
            self._commit(t["id"])
            result = auto_verify(self.c, t["id"], root=str(self.root), allow_command=True)
            self.assertTrue(result["passed"], result)
            self.assertEqual(result["checks"][0]["detail"], "missing")
        finally:
            os.environ.pop("P0_SECRET", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
