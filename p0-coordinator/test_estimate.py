"""估计器 + 计划器的单元测试。运行：python -m unittest test_estimate -v"""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from coordinator import Coordinator
from estimate import summarize
from plan import plan_project

ROOT = Path(__file__).resolve().parent
TMP = ROOT / ".tmp"


class EstimateTest(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)
        self.db = TMP / f"est-{uuid.uuid4().hex}.db"
        self.c = Coordinator(self.db)
        self.p = self.c.create_project("est")

    def tearDown(self):
        try:
            self.db.unlink()
        except OSError:
            pass

    def test_summary_and_plan(self):
        t1 = self.c.create_task(self.p["project_id"], "t1", ["a"], kind="code")
        t2 = self.c.create_task(self.p["project_id"], "t2", ["b"], kind="code")
        self.c.record_run(t1["id"], "W1", cost_usd=0.02, quality=0.70, duration_s=5)
        self.c.record_run(t1["id"], "W2", cost_usd=0.10, quality=0.90, duration_s=8)
        self.c.record_run(t2["id"], "W2", cost_usd=0.10, quality=0.90, duration_s=8)

        summary = summarize(self.db)
        self.assertEqual(summary["by_worker"]["W1"]["avg_quality"], 0.7)
        self.assertEqual(summary["by_worker"]["W2"]["runs"], 2)

        p2 = self.c.create_project("plan")
        for i in range(2):
            self.c.create_task(p2["project_id"], f"n{i}", [f"n{i}"], kind="code")
        plan = plan_project(self.c, summary, p2["project_id"], quality_floor=0.8)
        self.assertEqual(len(plan["assignment"]), 2)
        self.assertTrue(all(a["worker"] == "W2" for a in plan["assignment"]))
        self.assertGreater(plan["feasible_assignments"], 0)

    def test_plan_infeasible(self):
        t = self.c.create_task(self.p["project_id"], "t", ["a"], kind="code")
        self.c.record_run(t["id"], "W1", cost_usd=0.02, quality=0.70, duration_s=5)
        p2 = self.c.create_project("plan2")
        self.c.create_task(p2["project_id"], "n", ["n"], kind="code")
        summary = summarize(self.db)
        plan = plan_project(self.c, summary, p2["project_id"], quality_floor=0.99)
        self.assertEqual(plan["reason"], "no_feasible_assignment")


if __name__ == "__main__":
    unittest.main(verbosity=2)
