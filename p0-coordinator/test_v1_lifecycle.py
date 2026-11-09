"""V1 Task lifecycle 测试。"""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from coordinator import Coordinator, CoordinatorError

ROOT = Path(__file__).resolve().parent
TMP = ROOT / ".tmp"


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)
        self.db = TMP / f"life-{uuid.uuid4().hex}.db"
        self.c = Coordinator(self.db)
        self.p = self.c.create_project("life")
        self.t = self.c.create_task(self.p["project_id"], "t", ["a"])

    def tearDown(self):
        try:
            self.db.unlink()
        except OSError:
            pass

    def test_manual_transitions(self):
        path = ["assigned", "in_progress", "auto_review", "human_review",
                "ready_to_merge", "accepted"]
        for state in path:
            task = self.c.transition_task(self.t["id"], state, "test")
            self.assertEqual(task["lifecycle"], state)

    def test_invalid_transition(self):
        with self.assertRaises(CoordinatorError):
            self.c.transition_task(self.t["id"], "accepted")
        self.c.transition_task(self.t["id"], "assigned")
        self.c.transition_task(self.t["id"], "in_progress")
        self.c.transition_task(self.t["id"], "auto_review")
        self.c.transition_task(self.t["id"], "human_review")
        self.c.transition_task(self.t["id"], "ready_to_merge")
        self.c.transition_task(self.t["id"], "accepted")
        with self.assertRaises(CoordinatorError):
            self.c.transition_task(self.t["id"], "in_progress")

    def test_failed_execution_needs_fix(self):
        e = self.c.start_execution(self.t["id"], "agent-a")
        self.assertEqual(self.c.get_task(self.t["id"])["lifecycle"], "in_progress")
        self.c.finish_execution(e["execution_id"], "failed", "boom")
        self.assertEqual(self.c.get_task(self.t["id"])["lifecycle"], "needs_fix")
        e2 = self.c.start_execution(self.t["id"], "agent-b")
        self.assertEqual(self.c.get_task(self.t["id"])["lifecycle"], "in_progress")
        self.assertEqual(e2["generation"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
