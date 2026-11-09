"""真实 Git 合入测试：cherry-pick candidate，检查失败回滚。"""

from __future__ import annotations

import shutil
import subprocess
import sys
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


class GitMergeTest(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)
        self.base = TMP / f"v1-merge-{uuid.uuid4().hex}"
        self.repo = self.base / "repo"
        (self.repo / "src").mkdir(parents=True)
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "user.email", "test@local")
        (self.repo / "src" / "a.py").write_text("value = 1\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-m", "base")
        self.base_sha = git(self.repo, "rev-parse", "HEAD")

        self.c = Coordinator(self.base / "db.sqlite")
        p = self.c.create_project("merge")
        self.t = self.c.create_task(p["project_id"], "merge", ["src/a.py"], repo_id="repo1")
        self.c.reserve_files(self.t["id"], "repo1", ["src/a.py"])
        self.execution = self.c.start_execution(self.t["id"], "test")
        self.ws = self.c.create_workspace(
            self.t["id"], "repo1", str(self.repo), path=str(self.base / "wt"),
            execution_id=self.execution["execution_id"],
        )
        (self.base / "wt" / "src" / "a.py").write_text("value = 2\n", encoding="utf-8")
        self.head = self.c.commit_workspace(self.ws["workspace_id"], "change")
        self.c.finish_execution(self.execution["execution_id"], "succeeded", "done")
        cand = self.c.create_candidate_from_workspace(self.ws["workspace_id"])
        self.cand_id = cand["candidate_id"]
        self.c.freeze_candidate(self.cand_id)
        self.c.record_evidence(
            self.cand_id, "test", command="true", cwd=str(self.base / "wt"),
            exit_code=0, output_ref="inline", sha=self.head,
        )
        self.c.mark_candidate_ready(self.cand_id)
        self.c.approve_candidate(self.cand_id, "reviewer", "ok")
        self.attempt = self.c.start_merge(self.cand_id, self.base_sha, "reviewer")

    def tearDown(self):
        for path in (self.base / "wt", self.base / "integration"):
            if path.exists():
                git(self.repo, "worktree", "remove", "--force", str(path)) if self.repo.exists() else None
        shutil.rmtree(self.base, ignore_errors=True)

    def test_merge_success(self):
        result = self.c.merge_candidate_git(
            self.cand_id, str(self.repo), str(self.base / "integration"),
            check_command=[sys.executable, "-c",
                           "import pathlib,sys; sys.exit(0 if 'value = 2' in pathlib.Path('src/a.py').read_text() else 1)"],
        )
        self.assertNotEqual(result["result_sha"], self.head)
        merged = self.c.finish_merge(self.attempt["attempt_id"], "succeeded", result["result_sha"])
        self.assertEqual(merged["status"], "succeeded")
        task = self.c.get_task(self.t["id"])
        self.assertEqual(task["lifecycle"], "accepted")
        self.assertEqual(task["status"], "done")
        merged_file = (self.base / "integration" / "src" / "a.py").read_text(encoding="utf-8")
        self.assertIn("value = 2", merged_file)

    def test_merge_check_failure_rolls_back(self):
        with self.assertRaises(CoordinatorError):
            self.c.merge_candidate_git(
                self.cand_id, str(self.repo), str(self.base / "integration"),
                check_command=[sys.executable, "-c", "raise SystemExit(1)"],
            )
        self.assertFalse((self.base / "integration").exists())
        self.assertEqual(self.c.get_candidate(self.cand_id)["status"], "approved")
        branches = git(self.repo, "branch", "--list")
        self.assertNotIn("guild/merge-", branches)
        failed = self.c.finish_merge(self.attempt["attempt_id"], "failed", "", "check failed")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.c.get_task(self.t["id"])["lifecycle"], "needs_fix")


if __name__ == "__main__":
    unittest.main(verbosity=2)
