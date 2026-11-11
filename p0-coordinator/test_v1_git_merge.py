"""真实 Git 合入测试：目标 HEAD、cherry-pick、检查失败回滚、finish 校验。"""

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
            check_id="platform-test",
        )
        self.c.record_evidence(
            self.cand_id, "review", command="controlled-test:agent-review",
            cwd=str(self.base / "wt"), exit_code=0, output_ref="inline",
            sha=self.head, check_id="agent-review",
        )
        self.c.mark_candidate_ready(self.cand_id)
        self.c.approve_candidate(self.cand_id, "reviewer", "ok")

    def tearDown(self):
        for path in (self.base / "wt", self.base / "integration"):
            if path.exists() and self.repo.exists():
                git(self.repo, "worktree", "remove", "--force", str(path))
        shutil.rmtree(self.base, ignore_errors=True)

    def start_merge(self) -> dict:
        return self.c.start_merge(
            self.cand_id, self.base_sha, "reviewer",
            repo_path=str(self.repo), target_ref="main",
        )

    def merge_git(self, check_command: list[str] | None = None) -> dict:
        if check_command is None:
            check_command = [
                sys.executable, "-c",
                "import pathlib,sys; sys.exit(0 if 'value = 2' in "
                "pathlib.Path('src/a.py').read_text() else 1)",
            ]
        return self.c.merge_candidate_git(
            self.cand_id, str(self.repo), str(self.base / "integration"),
            check_command=check_command, target_ref="main",
        )

    def commit_target(self, name: str = "later.txt", content: str = "later\n") -> str:
        (self.repo / name).write_text(content, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-m", "target advanced")
        return git(self.repo, "rev-parse", "HEAD")

    def test_merge_success_advances_target_ref(self):
        attempt = self.start_merge()
        result = self.merge_git()
        self.assertEqual(result["target_ref"], "refs/heads/main")
        self.assertEqual(result["target_before_sha"], self.base_sha)
        self.assertNotEqual(result["result_sha"], self.base_sha)
        # merge_candidate_git 只产出集成分支，不能提前推进目标分支。
        self.assertEqual(git(self.repo, "rev-parse", "main"), self.base_sha)

        merged = self.c.finish_merge(attempt["attempt_id"], "succeeded", result["result_sha"])
        self.assertEqual(merged["status"], "succeeded")
        self.assertEqual(merged["target_after_sha"], result["result_sha"])
        self.assertEqual(git(self.repo, "rev-parse", "main"), result["result_sha"])
        self.assertEqual(self.c.get_candidate(self.cand_id)["status"], "merged")
        self.assertEqual(self.c.get_task(self.t["id"])["lifecycle"], "accepted")
        self.assertEqual(self.c.get_task(self.t["id"])["status"], "done")
        merged_file = (self.repo / "src" / "a.py").read_text(encoding="utf-8")
        self.assertIn("value = 2", merged_file)

    def test_merge_check_failure_rolls_back_and_target_unchanged(self):
        attempt = self.start_merge()
        with self.assertRaises(CoordinatorError):
            self.merge_git([sys.executable, "-c", "raise SystemExit(1)"])
        self.assertFalse((self.base / "integration").exists())
        self.assertEqual(self.c.get_candidate(self.cand_id)["status"], "approved")
        self.assertEqual(git(self.repo, "rev-parse", "main"), self.base_sha)
        branches = git(self.repo, "branch", "--list")
        self.assertNotIn("guild/merge-", branches)
        failed = self.c.finish_merge(attempt["attempt_id"], "failed", "", "check failed")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.c.get_task(self.t["id"])["lifecycle"], "needs_fix")

    def test_target_moved_after_candidate_blocks_start_merge(self):
        self.commit_target()
        with self.assertRaisesRegex(CoordinatorError, "must be revalidated"):
            self.start_merge()
        self.assertEqual(self.c.list_merge_attempts(self.cand_id), [])

    def test_target_moved_after_start_blocks_merge(self):
        attempt = self.start_merge()
        moved = self.commit_target()
        with self.assertRaisesRegex(CoordinatorError, "moved after start_merge"):
            self.merge_git()
        self.assertEqual(git(self.repo, "rev-parse", "main"), moved)
        self.assertEqual(self.c.get_candidate(self.cand_id)["status"], "approved")
        pending = [a for a in self.c.list_merge_attempts(self.cand_id) if a["status"] == "pending"]
        self.assertEqual(len(pending), 1)

        failed = self.c.finish_merge(attempt["attempt_id"], "succeeded", moved)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("record", failed["error"].lower())
        self.assertEqual(self.c.get_candidate(self.cand_id)["status"], "approved")
        self.assertEqual(self.c.get_task(self.t["id"])["lifecycle"], "needs_fix")

    def test_finish_rejects_target_moved_after_integration(self):
        attempt = self.start_merge()
        result = self.merge_git()
        moved = self.commit_target()
        failed = self.c.finish_merge(attempt["attempt_id"], "succeeded", result["result_sha"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("moved", failed["error"])
        self.assertEqual(git(self.repo, "rev-parse", "main"), moved)
        self.assertEqual(self.c.get_candidate(self.cand_id)["status"], "approved")
        self.assertEqual(self.c.get_task(self.t["id"])["lifecycle"], "needs_fix")

    def test_finish_rejects_wrong_result_sha(self):
        attempt = self.start_merge()
        result = self.merge_git()
        wrong = "0" * 40
        failed = self.c.finish_merge(attempt["attempt_id"], "succeeded", wrong)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("recorded", failed["error"].lower())
        self.assertEqual(git(self.repo, "rev-parse", "main"), self.base_sha)
        self.assertEqual(self.c.get_candidate(self.cand_id)["status"], "approved")
        self.assertEqual(self.c.get_task(self.t["id"])["lifecycle"], "needs_fix")
        self.assertNotEqual(result["result_sha"], wrong)

    def test_finish_rejects_empty_result_sha(self):
        attempt = self.start_merge()
        failed = self.c.finish_merge(attempt["attempt_id"], "succeeded", "")
        self.assertEqual(failed["status"], "failed")
        self.assertIn("result_sha", failed["error"])
        self.assertEqual(self.c.get_candidate(self.cand_id)["status"], "approved")

    def test_finish_rejects_unchanged_target(self):
        attempt = self.start_merge()
        # 模拟一个“合并结果等于目标基线”的尝试，确认 finish 不把无变化当成功。
        with self.c._conn() as conn:
            conn.execute(
                "UPDATE merge_attempts SET result_sha=? WHERE id=?",
                (self.base_sha, attempt["attempt_id"]),
            )
        failed = self.c.finish_merge(attempt["attempt_id"], "succeeded", self.base_sha)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("did not change", failed["error"])
        self.assertEqual(git(self.repo, "rev-parse", "main"), self.base_sha)
        self.assertEqual(self.c.get_candidate(self.cand_id)["status"], "approved")


if __name__ == "__main__":
    unittest.main(verbosity=2)
