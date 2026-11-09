"""P0 协调器单元测试。运行：python -m unittest test_coordinator -v"""

from __future__ import annotations

import time
import threading
import unittest
import uuid
from pathlib import Path

from coordinator import Coordinator, CoordinatorError, LeaseError

ROOT = Path(__file__).resolve().parent
TMP = ROOT / ".tmp"


class CoordinatorTest(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)
        self.db = TMP / f"test-{uuid.uuid4().hex}.db"
        self.c = Coordinator(self.db)
        self.p = self.c.create_project("test")

    def tearDown(self):
        try:
            self.db.unlink()
        except OSError:
            pass

    def test_claim_and_commit(self):
        t = self.c.create_task(self.p["project_id"], "t", ["a"])
        r = self.c.claim_task("W1", project_id=self.p["project_id"])
        self.assertTrue(r["claimed"])
        self.assertEqual(r["task"]["id"], t["id"])
        self.assertEqual(r["lease"]["fencing_token"], 1)
        done = self.c.commit_task(t["id"], "W1", r["lease"]["fencing_token"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(self.c.metrics()["tasks_by_status"], {"done": 1})

    def test_write_conflict(self):
        t1 = self.c.create_task(self.p["project_id"], "t1", ["doc#a"])
        t2 = self.c.create_task(self.p["project_id"], "t2", ["doc#a"])
        r1 = self.c.claim_task("W1", project_id=self.p["project_id"])
        self.assertTrue(r1["claimed"])
        r2 = self.c.claim_task("W2", project_id=self.p["project_id"])
        self.assertFalse(r2["claimed"])
        self.assertIn("write_conflict", r2["reason"])
        self.c.commit_task(t1["id"], "W1", r1["lease"]["fencing_token"])
        r3 = self.c.claim_task("W2", project_id=self.p["project_id"])
        self.assertTrue(r3["claimed"])
        self.assertEqual(r3["task"]["id"], t2["id"])

    def test_dependency_blocks(self):
        t1 = self.c.create_task(self.p["project_id"], "t1", ["a"])
        t2 = self.c.create_task(self.p["project_id"], "t2", ["b"], deps=[t1["id"]])
        blocked = self.c.claim_task("W1", task_id=t2["id"])
        self.assertFalse(blocked["claimed"])
        self.assertEqual(blocked["reason"], "deps_blocked")
        r = self.c.claim_task("W1", task_id=t1["id"])
        self.c.commit_task(t1["id"], "W1", r["lease"]["fencing_token"])
        unblocked = self.c.claim_task("W2", task_id=t2["id"])
        self.assertTrue(unblocked["claimed"])

    def test_expiry_and_fencing(self):
        t = self.c.create_task(self.p["project_id"], "t", ["a"])
        r1 = self.c.claim_task("W1", task_id=t["id"], ttl_seconds=0.2)
        self.assertTrue(r1["claimed"])
        time.sleep(0.3)
        r2 = self.c.claim_task("W2", task_id=t["id"], ttl_seconds=30)
        self.assertTrue(r2["claimed"])
        self.assertGreater(r2["lease"]["fencing_token"], r1["lease"]["fencing_token"])
        with self.assertRaises(LeaseError):
            self.c.commit_task(t["id"], "W1", r1["lease"]["fencing_token"])
        done = self.c.commit_task(t["id"], "W2", r2["lease"]["fencing_token"])
        self.assertEqual(done["status"], "done")

    def test_renew(self):
        t = self.c.create_task(self.p["project_id"], "t", ["a"])
        r = self.c.claim_task("W1", task_id=t["id"], ttl_seconds=10)
        renewed = self.c.renew_lease(t["id"], "W1", r["lease"]["fencing_token"], ttl_seconds=5)
        self.assertEqual(renewed["status"], "active")
        self.assertLessEqual(renewed["expires_in"], 5.1)
        with self.assertRaises(LeaseError):
            self.c.renew_lease(t["id"], "W1", r["lease"]["fencing_token"] + 1)

    def test_atomic_claim_threads(self):
        t = self.c.create_task(self.p["project_id"], "t", ["a"])
        results = []
        lock = threading.Lock()

        def worker(i: int) -> None:
            r = self.c.claim_task(f"W{i}", task_id=t["id"])
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        claimed = [r for r in results if r["claimed"]]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(len(results), 5)

    def test_run_recorded_on_commit(self):
        t = self.c.create_task(self.p["project_id"], "t", ["a"], kind="code")
        r = self.c.claim_task("W1", task_id=t["id"])
        self.c.commit_task(
            t["id"], "W1", r["lease"]["fencing_token"],
            run={
                "duration_s": 12.5,
                "input_tokens": 1000,
                "output_tokens": 500,
                "cost_usd": 0.12,
                "quality": 0.9,
                "diff_lines": 40,
                "files_changed": 2,
            },
        )
        runs = self.c.list_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["task_kind"], "code")
        self.assertAlmostEqual(runs[0]["cost_usd"], 0.12)
        m = self.c.metrics()
        self.assertEqual(m["runs"], 1)
        self.assertAlmostEqual(m["total_cost_usd"], 0.12)
        self.assertAlmostEqual(m["avg_quality"], 0.9)

    def test_record_run_manual(self):
        t = self.c.create_task(self.p["project_id"], "t", ["a"], kind="test")
        run = self.c.record_run(
            t["id"], "W2", input_tokens=10, output_tokens=20, cost_usd=0.01, quality=0.8
        )
        self.assertGreater(run["run_id"], 0)
        self.assertEqual(self.c.list_runs(worker="W2")[0]["task_kind"], "test")

    def test_approval_flow(self):
        p = self.c.create_project("appr", require_approval=True)
        t = self.c.create_task(p["project_id"], "t", ["a"])
        r = self.c.claim_task("W1", task_id=t["id"])
        submitted = self.c.commit_task(t["id"], "W1", r["lease"]["fencing_token"])
        self.assertEqual(submitted["status"], "review")
        self.assertEqual(self.c.metrics()["pending_approvals"], 1)
        pending = self.c.list_approvals("pending")
        self.assertEqual(len(pending), 1)
        approved = self.c.approve_task(t["id"], "human", "ok")
        self.assertEqual(approved["status"], "done")
        self.assertEqual(self.c.metrics()["pending_approvals"], 0)

    def test_rejection_reopens_task(self):
        p = self.c.create_project("appr2", require_approval=True)
        t = self.c.create_task(p["project_id"], "t", ["a"])
        r = self.c.claim_task("W1", task_id=t["id"])
        self.c.commit_task(t["id"], "W1", r["lease"]["fencing_token"])
        rejected = self.c.reject_task(t["id"], "human", "needs fix")
        self.assertEqual(rejected["status"], "ready")
        again = self.c.claim_task("W2", task_id=t["id"])
        self.assertTrue(again["claimed"])

    def test_explicit_approval_override(self):
        t = self.c.create_task(self.p["project_id"], "t", ["a"])
        r = self.c.claim_task("W1", task_id=t["id"])
        submitted = self.c.commit_task(
            t["id"], "W1", r["lease"]["fencing_token"], require_approval=True
        )
        self.assertEqual(submitted["status"], "review")

    def test_verification_passed(self):
        p = self.c.create_project("ver", require_verification=True)
        t = self.c.create_task(p["project_id"], "t", ["a"])
        r = self.c.claim_task("W1", task_id=t["id"])
        submitted = self.c.commit_task(t["id"], "W1", r["lease"]["fencing_token"])
        self.assertEqual(submitted["status"], "verification")
        self.assertEqual(self.c.metrics()["pending_verifications"], 1)
        done = self.c.record_verification(t["id"], "verifier", True, 0.95, "ok")
        self.assertEqual(done["status"], "done")
        self.assertEqual(len(self.c.list_verifications(t["id"])), 1)
        self.assertEqual(self.c.metrics()["pending_verifications"], 0)

    def test_verification_failed_reopens(self):
        p = self.c.create_project("ver2", require_verification=True)
        t = self.c.create_task(p["project_id"], "t", ["a"])
        r = self.c.claim_task("W1", task_id=t["id"])
        self.c.commit_task(t["id"], "W1", r["lease"]["fencing_token"])
        reopened = self.c.record_verification(t["id"], "verifier", False, 0.2, "missing")
        self.assertEqual(reopened["status"], "ready")
        self.assertTrue(self.c.claim_task("W2", task_id=t["id"])["claimed"])

    def test_verification_then_approval(self):
        p = self.c.create_project("ver3", require_verification=True, require_approval=True)
        t = self.c.create_task(p["project_id"], "t", ["a"])
        r = self.c.claim_task("W1", task_id=t["id"])
        self.c.commit_task(t["id"], "W1", r["lease"]["fencing_token"])
        review = self.c.record_verification(t["id"], "verifier", True, 1.0, "ok")
        self.assertEqual(review["status"], "review")
        self.assertEqual(self.c.metrics()["pending_approvals"], 1)
        done = self.c.approve_task(t["id"], "human", "ok")
        self.assertEqual(done["status"], "done")

    def test_multi_approval(self):
        p = self.c.create_project("multi", require_approval=True, approvals_required=2)
        t = self.c.create_task(p["project_id"], "t", ["a"])
        r = self.c.claim_task("W1", task_id=t["id"])
        submitted = self.c.commit_task(t["id"], "W1", r["lease"]["fencing_token"])
        self.assertEqual(submitted["status"], "review")
        self.assertEqual(len(self.c.list_approvals("pending")), 2)

        first = self.c.approve_task(t["id"], "alice", "ok")
        self.assertEqual(first["status"], "review")
        self.assertEqual(len(self.c.list_approvals("pending")), 1)
        with self.assertRaises(Exception):
            self.c.approve_task(t["id"], "alice", "again")

        second = self.c.approve_task(t["id"], "bob", "ok")
        self.assertEqual(second["status"], "done")
        self.assertEqual(len(self.c.list_approvals("pending")), 0)

    def test_reject_supersedes_partial_approvals(self):
        p = self.c.create_project("multi2", require_approval=True, approvals_required=2)
        t = self.c.create_task(p["project_id"], "t", ["a"])
        r = self.c.claim_task("W1", task_id=t["id"])
        self.c.commit_task(t["id"], "W1", r["lease"]["fencing_token"])
        self.c.approve_task(t["id"], "alice", "ok")
        reopened = self.c.reject_task(t["id"], "bob", "needs fix")
        self.assertEqual(reopened["status"], "ready")

        r2 = self.c.claim_task("W2", task_id=t["id"])
        self.c.commit_task(t["id"], "W2", r2["lease"]["fencing_token"])
        again = self.c.approve_task(t["id"], "alice", "ok")
        self.assertEqual(again["status"], "review")
        done = self.c.approve_task(t["id"], "bob", "ok")
        self.assertEqual(done["status"], "done")

    def test_approval_timeout_and_rerequest(self):
        p = self.c.create_project(
            "ttl", require_approval=True, approvals_required=2,
            approval_ttl_seconds=0.2,
        )
        t = self.c.create_task(p["project_id"], "t", ["a"])
        r = self.c.claim_task("W1", task_id=t["id"])
        self.c.commit_task(t["id"], "W1", r["lease"]["fencing_token"])
        self.assertEqual(len(self.c.list_approvals("pending")), 2)

        time.sleep(0.3)
        self.c.reap_expired()
        self.assertEqual(len(self.c.list_approvals("pending")), 0)
        self.assertEqual(len(self.c.list_approvals("expired")), 2)
        self.assertEqual(self.c.get_task(t["id"])["status"], "review")

        requested = self.c.request_approvals(t["id"], "alice", count=2, ttl_seconds=10)
        self.assertEqual(len(requested["approval_ids"]), 2)
        self.assertEqual(self.c.approve_task(t["id"], "alice")["status"], "review")
        self.assertEqual(self.c.approve_task(t["id"], "bob")["status"], "done")

    def test_reject_after_approval_expiry(self):
        p = self.c.create_project("ttl2", require_approval=True, approval_ttl_seconds=0.2)
        t = self.c.create_task(p["project_id"], "t", ["a"])
        r = self.c.claim_task("W1", task_id=t["id"])
        self.c.commit_task(t["id"], "W1", r["lease"]["fencing_token"])
        time.sleep(0.3)
        self.c.reap_expired()
        self.assertEqual(self.c.reject_task(t["id"], "alice", "stale")["status"], "ready")

    def test_budget_blocks_claim(self):
        p = self.c.create_project("budget", budget_usd=0.10)
        t1 = self.c.create_task(p["project_id"], "t1", ["a"])
        t2 = self.c.create_task(p["project_id"], "t2", ["b"])
        r = self.c.claim_task("W1", task_id=t1["id"])
        self.c.commit_task(
            t1["id"], "W1", r["lease"]["fencing_token"],
            run={"cost_usd": 0.12, "quality": 0.9},
        )
        budget = self.c.project_budget(p["project_id"])
        self.assertTrue(budget["exceeded"])
        self.assertAlmostEqual(budget["spent_usd"], 0.12)
        blocked = self.c.claim_task("W2", task_id=t2["id"])
        self.assertFalse(blocked["claimed"])
        self.assertEqual(blocked["reason"], "budget_exceeded")

    def test_budget_unlimited(self):
        p = self.c.create_project("budget2")
        t = self.c.create_task(p["project_id"], "t", ["a"])
        r = self.c.claim_task("W1", task_id=t["id"])
        self.c.commit_task(t["id"], "W1", r["lease"]["fencing_token"], run={"cost_usd": 9.0})
        self.assertFalse(self.c.project_budget(p["project_id"])["exceeded"])

    def test_events_and_metrics(self):
        t = self.c.create_task(self.p["project_id"], "t", ["a"])
        r = self.c.claim_task("W1", task_id=t["id"])
        self.c.commit_task(t["id"], "W1", r["lease"]["fencing_token"])
        types = [e["type"] for e in self.c.events(limit=20)]
        for expected in ("project_created", "task_created", "task_claimed", "task_committed"):
            self.assertIn(expected, types)
        m = self.c.metrics()
        self.assertEqual(m["projects"], 1)
        self.assertEqual(m["tasks_by_status"].get("done"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
