"""MILP 计划器测试。运行：python -m unittest test_plan_milp -v"""

from __future__ import annotations

import time
import unittest
import uuid
from pathlib import Path

from coordinator import Coordinator
from estimate import summarize
from plan_milp import pareto_front, pareto_front_3d, solve_plan

ROOT = Path(__file__).resolve().parent
TMP = ROOT / ".tmp"


class PlanMilpTest(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)
        self.db = TMP / f"milp-{uuid.uuid4().hex}.db"
        self.c = Coordinator(self.db)

        seed = self.c.create_project("seed")
        seeds = {
            kind: self.c.create_task(seed["project_id"], f"seed-{kind}", [f"s#{kind}"], kind=kind)
            for kind in ("design", "code", "test")
        }
        profiles = {
            "W1-cheap": (0.02, 0.70, 5.0),
            "W2-balanced": (0.10, 0.87, 8.0),
            "W3-premium": (0.25, 0.95, 12.0),
        }
        for worker, (cost, quality, duration) in profiles.items():
            for kind, task in seeds.items():
                self.c.record_run(
                    task["id"], worker,
                    cost_usd=cost, quality=quality, duration_s=duration,
                )

        self.p = self.c.create_project("plan")
        t1 = self.c.create_task(self.p["project_id"], "design", ["a"], kind="design")
        t2 = self.c.create_task(self.p["project_id"], "code-1", ["b"], deps=[t1["id"]], kind="code")
        t3 = self.c.create_task(self.p["project_id"], "code-2", ["b"], deps=[t1["id"]], kind="code")
        t4 = self.c.create_task(
            self.p["project_id"], "test", ["c"], deps=[t2["id"], t3["id"]], kind="test"
        )
        self.ids = {"t1": t1["id"], "t2": t2["id"], "t3": t3["id"], "t4": t4["id"]}
        self.summary = summarize(self.db)

    def tearDown(self):
        try:
            self.db.unlink()
        except OSError:
            pass

    def test_deps_and_write_conflict(self):
        plan = solve_plan(self.c, self.summary, self.p["project_id"], quality_floor=0.8, mode="cost")
        self.assertTrue(plan["feasible"], plan)
        by_id = {a["task_id"]: a for a in plan["assignment"]}
        self.assertGreaterEqual(by_id[self.ids["t2"]]["start_s"], by_id[self.ids["t1"]]["end_s"] - 1e-6)
        self.assertGreaterEqual(by_id[self.ids["t3"]]["start_s"], by_id[self.ids["t1"]]["end_s"] - 1e-6)
        # t2 和 t3 写集相同，不能并行
        a, b = by_id[self.ids["t2"]], by_id[self.ids["t3"]]
        self.assertTrue(
            a["end_s"] <= b["start_s"] + 1e-6 or b["end_s"] <= a["start_s"] + 1e-6,
            (a, b),
        )
        for a in plan["assignment"]:
            self.assertGreaterEqual(a["pred_quality"], 0.8 - 1e-9)

    def test_deadline_and_front(self):
        fastest = solve_plan(
            self.c, self.summary, self.p["project_id"], quality_floor=0.8, mode="time"
        )
        self.assertTrue(fastest["feasible"], fastest)
        deadline = fastest["pred_makespan_s"] + 0.5
        plan = solve_plan(
            self.c, self.summary, self.p["project_id"],
            quality_floor=0.8, mode="cost", deadline_s=deadline,
        )
        self.assertTrue(plan["feasible"], plan)
        self.assertLessEqual(plan["pred_makespan_s"], deadline + 1e-6)

        front = pareto_front(
            self.c, self.summary, self.p["project_id"], quality_floor=0.8, points=5
        )
        self.assertGreaterEqual(len(front), 1)
        for a, b in zip(front, front[1:]):
            self.assertLessEqual(a["pred_makespan_s"], b["pred_makespan_s"] + 1e-6)

    def test_front_3d(self):
        front = pareto_front_3d(
            self.c, self.summary, self.p["project_id"],
            floors=(0.70, 0.80, 0.90), points=3,
        )
        self.assertGreaterEqual(len(front), 1)

        def dominates(a, b):
            return (
                a["pred_total_cost_usd"] <= b["pred_total_cost_usd"] + 1e-9
                and a["pred_makespan_s"] <= b["pred_makespan_s"] + 1e-6
                and a["pred_min_quality"] >= b["pred_min_quality"] - 1e-9
                and (
                    a["pred_total_cost_usd"] < b["pred_total_cost_usd"] - 1e-9
                    or a["pred_makespan_s"] < b["pred_makespan_s"] - 1e-6
                    or a["pred_min_quality"] > b["pred_min_quality"] + 1e-9
                )
            )

        for i, a in enumerate(front):
            for j, b in enumerate(front):
                if i != j:
                    self.assertFalse(dominates(a, b), (a, b))

    def test_apply_and_claim_planned(self):
        plan = solve_plan(self.c, self.summary, self.p["project_id"], quality_floor=0.8)
        self.assertTrue(plan["feasible"], plan)
        applied = self.c.apply_plan(plan)
        self.assertTrue(applied["plan_id"].startswith("PL-"))

        stored = self.c.get_plan(self.p["project_id"])
        self.assertEqual(stored["plan_id"], applied["plan_id"])
        tasks = {t["id"]: t for t in self.c.list_tasks(project_id=self.p["project_id"])}
        for item in plan["assignment"]:
            self.assertEqual(tasks[item["task_id"]]["planned_worker"], item["worker"])
            self.assertIsNotNone(tasks[item["task_id"]]["planned_start_s"])

        first_worker = plan["assignment"][0]["worker"]
        first_task = plan["assignment"][0]["task_id"]
        blocked = self.c.claim_task("W-other", task_id=first_task)
        self.assertFalse(blocked["claimed"])
        self.assertTrue(blocked["reason"].startswith("reserved_for:"), blocked)

        claimed = self.c.claim_planned(first_worker, project_id=self.p["project_id"])
        self.assertTrue(claimed["claimed"], claimed)
        self.assertEqual(claimed["task"]["planned_worker"], first_worker)
        consumed = self.c.list_reservations(status="consumed")
        self.assertGreaterEqual(len(consumed), 1)

        none = self.c.claim_planned("W-nobody", project_id=self.p["project_id"])
        self.assertFalse(none["claimed"])
        self.assertEqual(none["reason"], "no_planned_task")

        cleared = self.c.clear_plan(self.p["project_id"])
        self.assertGreater(cleared, 0)
        tasks = self.c.list_tasks(project_id=self.p["project_id"])
        self.assertTrue(all(t["planned_worker"] is None for t in tasks))

    def test_renew_reservation(self):
        plan = solve_plan(self.c, self.summary, self.p["project_id"], quality_floor=0.8)
        self.c.apply_plan(plan, reserve_ttl=0.2)
        item = plan["assignment"][0]
        renewed = self.c.renew_reservation(item["task_id"], item["worker"], ttl_seconds=10)
        self.assertGreater(renewed["expires_in"], 1)
        time.sleep(0.3)
        blocked = self.c.claim_task("W-other", task_id=item["task_id"])
        self.assertFalse(blocked["claimed"])
        self.assertTrue(blocked["reason"].startswith("reserved_for:"), blocked)

    def test_reservation_expiry(self):
        plan = solve_plan(self.c, self.summary, self.p["project_id"], quality_floor=0.8)
        self.c.apply_plan(plan, reserve_ttl=0.2)
        time.sleep(0.3)
        first_task = plan["assignment"][0]["task_id"]
        claimed = self.c.claim_task("W-other", task_id=first_task)
        self.assertTrue(claimed["claimed"], claimed)

    def test_apply_infeasible(self):
        with self.assertRaises(Exception):
            self.c.apply_plan({"feasible": False, "project_id": self.p["project_id"]})

    def test_time_mode(self):
        plan = solve_plan(self.c, self.summary, self.p["project_id"], quality_floor=0.8, mode="time")
        self.assertTrue(plan["feasible"], plan)
        self.assertGreater(plan["pred_makespan_s"], 0)

    def test_infeasible_quality(self):
        plan = solve_plan(self.c, self.summary, self.p["project_id"], quality_floor=0.99)
        self.assertFalse(plan["feasible"])
        self.assertIn("no_worker_meets_quality", plan["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
