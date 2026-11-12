"""真实 Git 合入测试：cherry-pick candidate，检查失败回滚。"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch
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
        (self.base / "wt" / "src" / "earlier.py").write_text("earlier = True\n", encoding="utf-8")
        self.c.commit_workspace(self.ws["workspace_id"], "earlier change")
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
        self.assertEqual(git(self.repo, "rev-parse", "main"), self.base_sha)
        merged = self.c.finish_merge(self.attempt["attempt_id"], "succeeded", result["result_sha"])
        self.assertEqual(git(self.repo, "rev-parse", "main"), result["result_sha"])
        self.assertIn("value = 2", (self.repo / "src" / "a.py").read_text())
        self.assertTrue((self.repo / "src" / "earlier.py").exists())
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
        failed = self.c.list_merge_attempts(self.cand_id)[0]
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.c.get_task(self.t["id"])["lifecycle"], "needs_fix")

    def test_manual_success_without_git_receipt_is_rejected(self):
        with self.assertRaises(CoordinatorError):
            self.c.finish_merge(self.attempt["attempt_id"], "succeeded", self.head)
        self.assertEqual(self.c.get_candidate(self.cand_id)["status"], "approved")

    def test_target_advanced_since_candidate_base_is_blocked(self):
        (self.repo / "src" / "a.py").write_text("value = 99\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-m", "concurrent conflicting change")
        newer = git(self.repo, "rev-parse", "HEAD")
        with self.assertRaises(CoordinatorError):
            self.c.merge_candidate_git(self.cand_id, str(self.repo), str(self.base / "integration"))
        self.assertEqual(git(self.repo, "rev-parse", "main"), newer)
        self.assertIn("value = 99", (self.repo / "src" / "a.py").read_text())
        self.assertEqual(self.c.list_merge_attempts(self.cand_id)[0]["status"], "failed")

    def test_dirty_target_is_preserved_and_merge_fails(self):
        (self.repo / "src" / "a.py").write_text("unsaved user edit\n")
        with self.assertRaises(CoordinatorError):
            self.c.merge_candidate_git(self.cand_id, str(self.repo), str(self.base / "integration"))
        self.assertEqual((self.repo / "src" / "a.py").read_text(), "unsaved user edit\n")
        self.assertEqual(git(self.repo, "rev-parse", "main"), self.base_sha)

    def test_target_changes_while_checks_run_are_not_overwritten(self):
        script = (
            "import pathlib,subprocess,sys; p=pathlib.Path(sys.argv[1]); "
            "(p/'other.txt').write_text('concurrent'); "
            "subprocess.run(['git','-C',str(p),'add','other.txt'],check=True); "
            "subprocess.run(['git','-C',str(p),'commit','-m','concurrent'],check=True)"
        )
        with self.assertRaises(CoordinatorError):
            self.c.merge_candidate_git(
                self.cand_id, str(self.repo), str(self.base / "integration"),
                check_command=[sys.executable, "-c", script, str(self.repo)],
            )
        self.assertTrue((self.repo / "other.txt").exists())
        self.assertIn("value = 1", (self.repo / "src" / "a.py").read_text())
        self.assertNotEqual(git(self.repo, "rev-parse", "main"), self.base_sha)
        self.assertEqual(self.c.list_merge_attempts(self.cand_id)[0]["status"], "failed")

    def test_finish_rejects_wrong_sha_and_changed_target(self):
        result = self.c.merge_candidate_git(self.cand_id, str(self.repo), str(self.base / "integration"))
        with self.assertRaises(CoordinatorError):
            self.c.finish_merge(self.attempt["attempt_id"], "succeeded", self.head)
        (self.repo / "other.txt").write_text("later")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-m", "later target")
        with self.assertRaises(CoordinatorError):
            self.c.finish_merge(self.attempt["attempt_id"], "succeeded", result["result_sha"])
        self.assertNotEqual(self.c.get_task(self.t["id"])["lifecycle"], "accepted")

    def test_checks_cannot_change_the_reviewed_tree(self):
        with self.assertRaises(CoordinatorError):
            self.c.merge_candidate_git(
                self.cand_id, str(self.repo), str(self.base / "integration"),
                check_command=[sys.executable, "-c", "from pathlib import Path; Path('src/a.py').write_text('tampered')"],
            )
        self.assertEqual(git(self.repo, "rev-parse", "main"), self.base_sha)

    def test_publication_receipt_survives_restart(self):
        result = self.c.merge_candidate_git(self.cand_id, str(self.repo), str(self.base / "integration"))
        reopened = Coordinator(self.base / "db.sqlite")
        receipt = reopened.list_merge_attempts(self.cand_id)[0]
        self.assertEqual(receipt["candidate_sha"], self.head)
        self.assertEqual(receipt["target_before_sha"], self.base_sha)
        self.assertEqual(receipt["git_result_sha"], result["result_sha"])
        self.assertEqual(receipt["target_ref"], "refs/heads/main")
        reopened.finish_merge(self.attempt["attempt_id"], "succeeded", result["result_sha"])
        self.assertEqual(reopened.get_task(self.t["id"])["lifecycle"], "accepted")
        self.assertEqual(git(self.repo, "config", "--get", "core.bare"), "false")
        proc = subprocess.run(["git", "-C", str(self.repo), "config", "--local", "--get", "receive.denyCurrentBranch"], capture_output=True)
        self.assertEqual(proc.returncode, 1)  # 没有持久改写用户配置

    def test_published_but_recording_failed_is_unknown_and_can_be_reconciled(self):
        result = self.c.merge_candidate_git(self.cand_id, str(self.repo), str(self.base / "integration"))
        original = self.c._event

        def fail_published_event(conn, kind, *args, **kwargs):
            if kind == "merge_succeeded":
                raise RuntimeError("injected event failure after Git publication")
            return original(conn, kind, *args, **kwargs)

        with patch.object(self.c, "_event", side_effect=fail_published_event):
            with self.assertRaisesRegex(RuntimeError, "injected event failure"):
                self.c.finish_merge(self.attempt["attempt_id"], "succeeded", result["result_sha"])
        attempt = self.c.list_merge_attempts(self.cand_id)[0]
        self.assertEqual(attempt["status"], "unknown")
        self.assertNotEqual(git(self.repo, "rev-parse", "main"), self.base_sha)
        self.assertNotEqual(self.c.get_task(self.t["id"])["lifecycle"], "accepted")
        reopened = Coordinator(self.base / "db.sqlite")
        reopened.finish_merge(attempt["attempt_id"], "succeeded", attempt["git_result_sha"])
        self.assertEqual(reopened.get_task(self.t["id"])["lifecycle"], "accepted")

    def test_failure_cannot_hide_a_published_result(self):
        result = self.c.merge_candidate_git(self.cand_id, str(self.repo), str(self.base / "integration"))
        self.c.finish_merge(self.attempt["attempt_id"], "succeeded", result["result_sha"])
        with self.assertRaises(CoordinatorError):
            self.c.finish_merge(self.attempt["attempt_id"], "failed", error="pretend it did not happen")

    def test_cli_failure_preserves_original_reason_and_finishes_attempt(self):
        import worker_cli
        args = worker_cli.build_parser().parse_args([
            "--db", str(self.base / "db.sqlite"), "merge-candidate-git",
            "--candidate", self.cand_id, "--repo-path", str(self.repo),
            "--worktree", str(self.base / "integration"), "--finish",
            "--check", "definitely-not-a-real-merge-check-12345",
        ])
        with self.assertRaises(FileNotFoundError):
            worker_cli.run(args)
        self.assertEqual(self.c.list_merge_attempts(self.cand_id)[0]["status"], "failed")
        self.assertEqual(git(self.repo, "rev-parse", "main"), self.base_sha)

    def test_cli_target_mismatch_does_not_touch_existing_attempt(self):
        import worker_cli
        git(self.repo, "branch", "wrong", self.base_sha)
        args = worker_cli.build_parser().parse_args([
            "--db", str(self.base / "db.sqlite"), "merge-candidate-git",
            "--candidate", self.cand_id, "--repo-path", str(self.repo),
            "--worktree", str(self.base / "integration"), "--target-branch", "wrong",
        ])
        with self.assertRaisesRegex(CoordinatorError, "target branch"):
            worker_cli.run(args)
        self.assertEqual(self.c.list_merge_attempts(self.cand_id)[0]["status"], "pending")

    def test_explicit_unchecked_out_target_does_not_change_main(self):
        git(self.repo, "branch", "release", self.base_sha)
        self.c.release_file_reservations(task_id=self.t["id"], reason="fixture switches task")
        project = self.c.create_project("release target")
        task = self.c.create_task(project["project_id"], "release", ["src"], repo_id="repo1")
        execution = self.c.start_execution(task["id"], "fixture")
        self.c.finish_execution(execution["execution_id"], "succeeded")
        candidate = self.c.create_candidate(task["id"], "repo1", self.base_sha, self.head,
                                            author_execution_id=execution["execution_id"])
        cid = candidate["candidate_id"]
        self.c.freeze_candidate(cid)
        for check_id, kind in (("platform-test", "test"), ("agent-review", "review")):
            self.c.record_evidence(cid, kind, exit_code=0, sha=self.head, check_id=check_id)
        self.c.mark_candidate_ready(cid)
        self.c.approve_candidate(cid, "fixture")
        attempt = self.c.start_merge(cid, self.base_sha, repo_path=str(self.repo), target_branch="release")
        result = self.c.merge_candidate_git(cid, str(self.repo), str(self.base / "integration"))
        self.c.finish_merge(attempt["attempt_id"], "succeeded", result["result_sha"])
        self.assertEqual(git(self.repo, "rev-parse", "main"), self.base_sha)
        self.assertEqual(git(self.repo, "rev-parse", "release"), result["result_sha"])
        self.assertIn("value = 1", (self.repo / "src" / "a.py").read_text())

    def test_old_merge_table_migration_preserves_history_and_cannot_fake_success(self):
        with sqlite3.connect(self.base / "db.sqlite") as conn:
            for name in ("target_ref", "repo_path", "candidate_sha", "target_before_sha", "git_result_sha", "git_started"):
                conn.execute(f"ALTER TABLE merge_attempts DROP COLUMN {name}")
        for _ in range(2):
            reopened = Coordinator(self.base / "db.sqlite")
            attempts = reopened.list_merge_attempts(self.cand_id)
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["attempt_id"], self.attempt["attempt_id"])
            self.assertEqual(attempts[0]["git_result_sha"], "")
            with self.assertRaises(CoordinatorError):
                reopened.finish_merge(self.attempt["attempt_id"], "succeeded", self.head)

    def test_revoked_evidence_still_blocks_merge_after_approval(self):
        evidence = self.c.list_evidence(self.cand_id)[0]
        self.c.void_evidence(evidence["evidence_id"], "revoked after approval")
        with self.assertRaises(CoordinatorError):
            self.c.merge_candidate_git(self.cand_id, str(self.repo), str(self.base / "integration"))
        self.assertEqual(git(self.repo, "rev-parse", "main"), self.base_sha)

    def test_same_attempt_cannot_execute_concurrently(self):
        original = self.c._git
        invoked = False

        def interleave(cwd, *args, **kwargs):
            nonlocal invoked
            if args[:2] == ("worktree", "add") and not invoked:
                invoked = True
                with self.assertRaisesRegex(CoordinatorError, "already started"):
                    self.c.merge_candidate_git(self.cand_id, str(self.repo), str(self.base / "second"))
            return original(cwd, *args, **kwargs)

        with patch.object(self.c, "_git", side_effect=interleave):
            result = self.c.merge_candidate_git(self.cand_id, str(self.repo), str(self.base / "integration"))
        self.assertTrue(invoked)
        self.assertFalse((self.base / "second").exists())
        self.c.finish_merge(self.attempt["attempt_id"], "succeeded", result["result_sha"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
