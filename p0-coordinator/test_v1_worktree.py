"""V1 Worktree / 越界 diff 测试（真实 git worktree）。"""

from __future__ import annotations

import shutil
import subprocess
import unittest
import uuid
from pathlib import Path

from coordinator import Coordinator, CoordinatorError

ROOT = Path(__file__).resolve().parent
TMP = ROOT / ".tmp"


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


class WorktreeTest(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)
        self.base = TMP / f"v1-wt-{uuid.uuid4().hex}"
        self.repo = self.base / "repo"
        (self.repo / "src").mkdir(parents=True)
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "user.email", "test@local")
        (self.repo / "README.md").write_text("# demo\n", encoding="utf-8")
        (self.repo / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-m", "base")
        self.base_sha = git(self.repo, "rev-parse", "HEAD")

        self.db = self.base / "db.sqlite"
        self.c = Coordinator(self.db)
        self.p = self.c.create_project("wt")
        self.t = self.c.create_task(self.p["project_id"], "t", ["src"])

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_scope_and_candidate(self):
        self.c.reserve_files(self.t["id"], "repo1", ["src"], kind="dir")
        ws = self.c.create_workspace(
            self.t["id"], "repo1", str(self.repo), path=str(self.base / "wt")
        )
        (self.base / "wt" / "src" / "a.py").write_text("print('a2')\n", encoding="utf-8")
        (self.base / "wt" / "src" / "b.py").write_text("print('b')\n", encoding="utf-8")
        (self.base / "wt" / "other.txt").write_text("out of scope\n", encoding="utf-8")

        scope = self.c.check_workspace_scope(ws["workspace_id"])
        self.assertFalse(scope["ok"])
        self.assertIn("other.txt", scope["violations"])

        (self.base / "wt" / "other.txt").unlink()
        scope = self.c.check_workspace_scope(ws["workspace_id"])
        self.assertTrue(scope["ok"], scope)

        head = self.c.commit_workspace(ws["workspace_id"], "change")
        self.assertNotEqual(head, self.base_sha)
        candidate = self.c.create_candidate_from_workspace(ws["workspace_id"])
        self.assertEqual(candidate["base_sha"], self.base_sha)
        self.assertEqual(candidate["candidate_sha"], head)

        removed = self.c.remove_workspace(ws["workspace_id"])
        self.assertEqual(removed["status"], "removed")
        listing = git(self.repo, "worktree", "list", "--porcelain")
        self.assertNotIn(str(self.base / "wt"), listing)

    def test_candidate_falls_back_to_latest_execution(self):
        self.c.reserve_files(self.t["id"], "repo1", ["src"], kind="dir")
        ws = self.c.create_workspace(
            self.t["id"], "repo1", str(self.repo), path=str(self.base / "wt")
        )
        (self.base / "wt" / "src" / "a.py").write_text("print('a3')\n", encoding="utf-8")
        self.c.commit_workspace(ws["workspace_id"], "change")
        ex = self.c.start_execution(self.t["id"], "codex", workspace=str(self.base / "wt"))
        self.c.finish_execution(ex["execution_id"], "succeeded")
        candidate = self.c.create_candidate_from_workspace(ws["workspace_id"])
        self.assertEqual(candidate["author_execution_id"], ex["execution_id"])

    def test_scope_ignores_python_cache(self):
        self.c.reserve_files(self.t["id"], "repo1", ["src"], kind="dir")
        ws = self.c.create_workspace(
            self.t["id"], "repo1", str(self.repo), path=str(self.base / "wt")
        )
        (self.base / "wt" / "src" / "a.py").write_text("print('a2')\n", encoding="utf-8")
        (self.base / "wt" / "src" / "__pycache__").mkdir()
        (self.base / "wt" / "src" / "__pycache__" / "a.pyc").write_bytes(b"x")
        scope = self.c.check_workspace_scope(ws["workspace_id"])
        self.assertTrue(scope["ok"], scope)
        self.assertEqual(scope["changed"], ["src/a.py"])

    def test_scope_without_reservation(self):
        ws = self.c.create_workspace(
            self.t["id"], "repo1", str(self.repo), path=str(self.base / "wt")
        )
        (self.base / "wt" / "other.txt").write_text("x", encoding="utf-8")
        scope = self.c.check_workspace_scope(ws["workspace_id"])
        self.assertFalse(scope["ok"])
        self.assertEqual(scope["violations"], ["other.txt"])

    def test_bad_repo_path(self):
        with self.assertRaises(CoordinatorError):
            self.c.create_workspace(self.t["id"], "repo1", str(self.base / "missing"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
