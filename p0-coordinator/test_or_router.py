import unittest

from or_router import assign_batch, rank_agents


def run(worker, project="P1", seconds=10, cost=0.001, status="ok"):
    return {
        "worker": worker, "project_id": project, "duration_s": seconds,
        "cost_usd": cost, "status": status,
        "input_tokens": 0, "output_tokens": 0,
    }


class OrRouterTest(unittest.TestCase):
    def test_default_cost_prefers_dsh(self):
        result = rank_agents([], "P1", strategy="cost")
        self.assertEqual(result["recommended"]["agent"], "dsh")
        self.assertEqual(result["recommended"]["source"], "default")

    def test_history_time_prefers_faster_agent(self):
        runs = [
            run("codex-deepseek", seconds=8),
            run("codex-deepseek", seconds=10),
            run("dsh", seconds=20),
            run("dsh", seconds=22),
        ]
        result = rank_agents(runs, "P1", strategy="time")
        self.assertEqual(result["recommended"]["agent"], "codex-deepseek")
        self.assertEqual(result["recommended"]["source"], "history")

    def test_quality_prefers_success(self):
        runs = [
            run("codex-deepseek", status="ok"),
            run("dsh", status="failed"),
        ]
        result = rank_agents(runs, "P1", strategy="quality")
        self.assertEqual(result["recommended"]["agent"], "codex-deepseek")

    def test_budget_excludes_expensive_agent(self):
        runs = [
            run("codex-deepseek", cost=0.010),
            run("dsh", cost=0.001),
        ]
        result = rank_agents(runs, "P1", budget_usd=0.002, strategy="cost")
        self.assertEqual(result["recommended"]["agent"], "dsh")
        expensive = [c for c in result["candidates"] if c["agent"] == "codex-deepseek"][0]
        self.assertFalse(expensive["feasible"])

    def test_batch_assigns_distinct_agents(self):
        tasks = [{"id": f"T{i}", "title": f"t{i}", "priority": 0} for i in range(4)]
        result = assign_batch(tasks, [], "P1", strategy="quality", max_parallel=4)
        self.assertEqual(len(result["assignments"]), 4)
        self.assertEqual(len({a["agent"] for a in result["assignments"]}), 4)

    def test_batch_respects_parallel_limit(self):
        tasks = [{"id": f"T{i}", "title": f"t{i}", "priority": 0} for i in range(4)]
        result = assign_batch(tasks, [], "P1", max_parallel=2)
        self.assertEqual(len(result["assignments"]), 2)
        self.assertEqual(len(result["unassigned"]), 2)

    def test_batch_respects_budget(self):
        tasks = [{"id": f"T{i}", "title": f"t{i}", "priority": 0} for i in range(4)]
        result = assign_batch(tasks, [], "P1", budget_usd=0.002, max_parallel=4)
        self.assertEqual(len(result["assignments"]), 1)
        self.assertEqual(result["assignments"][0]["agent"], "dsh")
        self.assertEqual(result["unassigned"][0]["reason"], "剩余预算不足")


if __name__ == "__main__":
    unittest.main()
