"""V1 核心切片测试：execution（Run）与 repo/path 文件预约。"""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from coordinator import Coordinator, CoordinatorError

ROOT = Path(__file__).resolve().parent
TMP = ROOT / ".tmp"


class V1CoreTest(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)
        self.db = TMP / f"v1-{uuid.uuid4().hex}.db"
        self.c = Coordinator(self.db)
        self.p = self.c.create_project("v1")
        self.t1 = self.c.create_task(self.p["project_id"], "t1", ["a"])
        self.t2 = self.c.create_task(self.p["project_id"], "t2", ["b"])

    def tearDown(self):
        try:
            self.db.unlink()
        except OSError:
            pass

    def test_file_reservation_blocks_claim(self):
        t1 = self.c.create_task(
            self.p["project_id"], "a", ["src/a.py"], repo_id="repo1"
        )
        t2 = self.c.create_task(self.p["project_id"], "b", [], repo_id="repo1")
        self.c.reserve_files(t2["id"], "repo1", ["src"], kind="dir")

        blocked = self.c.claim_task("W1", task_id=t1["id"])
        self.assertFalse(blocked["claimed"])
        self.assertTrue(blocked["reason"].startswith("file_reserved_by:"), blocked)

        self.c.release_file_reservations(task_id=t2["id"])
        claimed = self.c.claim_task("W1", task_id=t1["id"])
        self.assertTrue(claimed["claimed"])

    def test_file_reservation_blocks_execution(self):
        t1 = self.c.create_task(
            self.p["project_id"], "a", ["src/a.py"], repo_id="repo1"
        )
        t2 = self.c.create_task(self.p["project_id"], "b", [], repo_id="repo1")
        self.c.reserve_files(t2["id"], "repo1", ["src"], kind="dir")
        with self.assertRaises(CoordinatorError):
            self.c.start_execution(t1["id"], "agent-a")

    def test_file_reservation_repo_isolation_claim(self):
        t1 = self.c.create_task(
            self.p["project_id"], "a", ["src/a.py"], repo_id="repo1"
        )
        t2 = self.c.create_task(self.p["project_id"], "b", [], repo_id="repo2")
        self.c.reserve_files(t2["id"], "repo2", ["src"], kind="dir")
        claimed = self.c.claim_task("W1", task_id=t1["id"])
        self.assertTrue(claimed["claimed"])

    def test_candidate_flow(self):
        e = self.c.start_execution(self.t1["id"], "agent-a")
        self.c.finish_execution(e["execution_id"], "succeeded", "done")
        c = self.c.create_candidate(
            self.t1["id"], "repo1", "base-1", "cand-1",
            author_execution_id=e["execution_id"],
        )
        self.assertEqual(c["status"], "building")
        self.c.freeze_candidate(c["candidate_id"])
        with self.assertRaises(CoordinatorError):
            self.c.mark_candidate_ready(c["candidate_id"])

        self.c.record_evidence(
            c["candidate_id"], "test", command="pytest", exit_code=0, sha="cand-1"
        )
        ready = self.c.mark_candidate_ready(c["candidate_id"])
        self.assertEqual(ready["status"], "awaiting_human")
        approved = self.c.approve_candidate(c["candidate_id"], "alice", role="admin")
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["approver_role"], "admin")

        attempt = self.c.start_merge(c["candidate_id"], "base-1", "alice")
        self.assertEqual(attempt["status"], "pending")
        done = self.c.finish_merge(attempt["attempt_id"], "succeeded", "cand-1")
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(self.c.get_candidate(c["candidate_id"])["status"], "merged")
        self.assertEqual(len(self.c.list_evidence(c["candidate_id"])), 1)

    def test_approve_requires_evidence(self):
        e = self.c.start_execution(self.t1["id"], "agent-a")
        self.c.finish_execution(e["execution_id"], "succeeded", "done")
        c = self.c.create_candidate(
            self.t1["id"], "repo1", "base-1", "cand-1",
            author_execution_id=e["execution_id"],
        )
        self.c.freeze_candidate(c["candidate_id"])
        self.c.record_evidence(
            c["candidate_id"], "test", command="pytest", exit_code=0, sha="cand-1"
        )
        self.c.mark_candidate_ready(c["candidate_id"])
        with self.c._conn() as conn:
            conn.execute("DELETE FROM evidence WHERE candidate_id=?", (c["candidate_id"],))
        with self.assertRaises(CoordinatorError):
            self.c.approve_candidate(c["candidate_id"], "alice")

    def test_multilevel_approval_chain(self):
        p = self.c.create_project("chain", approval_chain="reviewer,admin")
        t = self.c.create_task(p["project_id"], "chain task", ["src/a.py"], repo_id="repo1")
        e = self.c.start_execution(t["id"], "agent-a")
        self.c.finish_execution(e["execution_id"], "succeeded", "done")
        c = self.c.create_candidate(
            t["id"], "repo1", "base-1", "cand-1",
            author_execution_id=e["execution_id"],
        )
        self.c.freeze_candidate(c["candidate_id"])
        self.c.record_evidence(
            c["candidate_id"], "test", command="pytest", exit_code=0, sha="cand-1"
        )
        self.c.mark_candidate_ready(c["candidate_id"])

        with self.assertRaises(CoordinatorError):
            self.c.approve_candidate(c["candidate_id"], "admin", role="admin")

        first = self.c.approve_candidate(c["candidate_id"], "alice", role="reviewer")
        self.assertEqual(first["status"], "awaiting_human")
        self.assertEqual(first["pending_roles"], ["admin"])
        self.assertEqual(len(self.c.list_candidate_approvals(c["candidate_id"])), 1)

        second = self.c.approve_candidate(c["candidate_id"], "bob", role="admin")
        self.assertEqual(second["status"], "approved")
        self.assertEqual(second["approver_role"], "admin")
        self.assertEqual(len(self.c.list_candidate_approvals(c["candidate_id"])), 2)

    def test_project_requires_admin_approval(self):
        p = self.c.create_project("admin-only", required_approver_role="admin")
        t = self.c.create_task(p["project_id"], "admin task", ["src/a.py"], repo_id="repo1")
        e = self.c.start_execution(t["id"], "agent-a")
        self.c.finish_execution(e["execution_id"], "succeeded", "done")
        c = self.c.create_candidate(
            t["id"], "repo1", "base-1", "cand-1",
            author_execution_id=e["execution_id"],
        )
        self.c.freeze_candidate(c["candidate_id"])
        self.c.record_evidence(
            c["candidate_id"], "test", command="pytest", exit_code=0, sha="cand-1"
        )
        self.c.mark_candidate_ready(c["candidate_id"])

        with self.assertRaises(CoordinatorError):
            self.c.approve_candidate(c["candidate_id"], "alice", role="reviewer")

        approved = self.c.approve_candidate(
            c["candidate_id"], "alice", role="admin"
        )
        self.assertEqual(approved["approver_role"], "admin")
        attempt = self.c.start_merge(c["candidate_id"], "base-1", "alice")
        self.assertEqual(attempt["status"], "pending")

    def test_approve_rejects_unknown_role(self):
        with self.assertRaises(CoordinatorError):
            self.c.approve_candidate("C-unknown", "alice", role="owner")

    def test_candidate_requires_finished_execution(self):
        e = self.c.start_execution(self.t1["id"], "agent-a")
        with self.assertRaises(CoordinatorError):
            self.c.create_candidate(
                self.t1["id"], "repo1", "base-1", "cand-1",
                author_execution_id=e["execution_id"],
            )

    def test_merge_base_mismatch(self):
        e = self.c.start_execution(self.t1["id"], "agent-a")
        self.c.finish_execution(e["execution_id"], "succeeded", "done")
        c = self.c.create_candidate(self.t1["id"], "repo1", "base-1", "cand-1")
        self.c.freeze_candidate(c["candidate_id"])
        self.c.record_evidence(c["candidate_id"], "test", exit_code=0, sha="cand-1")
        self.c.mark_candidate_ready(c["candidate_id"])
        self.c.approve_candidate(c["candidate_id"], "alice")
        with self.assertRaises(CoordinatorError):
            self.c.start_merge(c["candidate_id"], "base-2")

    def test_execution_lifecycle(self):
        e1 = self.c.start_execution(self.t1["id"], "agent-a", workspace="/w/a")
        self.assertEqual(e1["generation"], 1)
        self.assertEqual(e1["status"], "running")
        with self.assertRaises(CoordinatorError):
            self.c.start_execution(self.t1["id"], "agent-b")

        done = self.c.finish_execution(e1["execution_id"], "succeeded", "ok")
        self.assertEqual(done["status"], "succeeded")
        self.assertIsNotNone(done["ended_at"])

        e2 = self.c.start_execution(self.t1["id"], "agent-b")
        self.assertEqual(e2["generation"], 2)
        self.assertEqual(len(self.c.list_executions(self.t1["id"])), 2)
        with self.assertRaises(CoordinatorError):
            self.c.finish_execution(e1["execution_id"], "failed")

    def test_run_links_execution(self):
        e = self.c.start_execution(self.t1["id"], "agent-a")
        run = self.c.record_run(self.t1["id"], "agent-a", execution_id=e["execution_id"],
                                cost_usd=0.01, quality=0.9)
        self.assertEqual(run["execution_id"], e["execution_id"])
        self.assertEqual(self.c.list_runs(task_id=self.t1["id"])[0]["execution_id"], e["execution_id"])

    def test_file_reservation_parent_child_and_case(self):
        self.c.reserve_files(self.t1["id"], "repo1", ["src"], kind="dir")
        with self.assertRaises(CoordinatorError):
            self.c.reserve_files(self.t2["id"], "repo1", ["src/a.py"])
        with self.assertRaises(CoordinatorError):
            self.c.reserve_files(self.t2["id"], "repo1", ["SRC/B.PY"])

        self.c.release_file_reservations(task_id=self.t1["id"], reason="done")
        rows = self.c.reserve_files(self.t2["id"], "repo1", ["src/a.py"])
        self.assertEqual(rows[0]["path"], "src/a.py")

    def test_file_reservation_all_or_nothing(self):
        self.c.reserve_files(self.t1["id"], "repo1", ["src"], kind="dir")
        with self.assertRaises(CoordinatorError):
            self.c.reserve_files(self.t2["id"], "repo1", ["ok.py", "src/a.py"])
        self.assertEqual(self.c.list_file_reservations(task_id=self.t2["id"]), [])

    def test_file_reservation_repo_isolation(self):
        self.c.reserve_files(self.t1["id"], "repo1", ["src/a.py"])
        rows = self.c.reserve_files(self.t2["id"], "repo2", ["src/a.py"])
        self.assertEqual(len(rows), 1)

    def test_path_escape_rejected(self):
        for path in ("../x", "/abs/x", "C:/x", "a//b"):
            with self.assertRaises(CoordinatorError, msg=path):
                self.c.reserve_files(self.t1["id"], "repo1", [path])


if __name__ == "__main__":
    unittest.main(verbosity=2)
