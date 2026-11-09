import subprocess
import tempfile
import unittest
from pathlib import Path

from coordinator import Coordinator, CoordinatorError
from or_router import recommend_agent
from semantic import extract_changes


def _git(cwd, *args):
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


class SemanticTest(unittest.TestCase):
    def test_added_function_is_compatible(self):
        changes = extract_changes(
            "a.py",
            "def add(a, b):\n    return a + b\n",
            "def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n",
        )
        self.assertEqual([c["change_type"] for c in changes], ["added"])
        self.assertEqual(changes[0]["object"], "mul")
        self.assertEqual(changes[0]["compatibility"], "compatible")

    def test_signature_change_is_breaking(self):
        changes = extract_changes(
            "a.py",
            "def div(a, b):\n    return a / b\n",
            "def div(a, b, default=1):\n    return a / b if b else default\n",
        )
        self.assertEqual(changes[0]["object"], "div")
        self.assertEqual(changes[0]["change_type"], "signature_changed")
        self.assertEqual(changes[0]["compatibility"], "breaking")

    def test_removed_function_is_breaking(self):
        changes = extract_changes(
            "a.py",
            "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n",
            "def add(a, b):\n    return a + b\n",
        )
        self.assertEqual(changes[0]["object"], "sub")
        self.assertEqual(changes[0]["change_type"], "removed")
        self.assertEqual(changes[0]["compatibility"], "breaking")

    def test_body_change_is_unknown(self):
        changes = extract_changes(
            "a.py",
            "def add(a, b):\n    return a + b\n",
            "def add(a, b):\n    return b + a\n",
        )
        self.assertEqual(changes[0]["change_type"], "body_changed")
        self.assertEqual(changes[0]["compatibility"], "unknown")

    def test_semantic_gate_and_impact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            (repo / "src").mkdir(parents=True)
            (repo / "src" / "a.py").write_text("def div(a, b):\n    return a / b\n", encoding="utf-8")
            _git(repo, "init", "-b", "main")
            _git(repo, "config", "user.name", "test")
            _git(repo, "config", "user.email", "test@local")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "base")

            c = Coordinator(root / "db.sqlite")
            project = c.create_project("semantic")
            task = c.create_task(project["project_id"], "change div", ["src/a.py"], repo_id="repo1")
            other = c.create_task(project["project_id"], "consumer", ["src/a.py"], repo_id="repo1")
            c.reserve_files(task["id"], "repo1", ["src/a.py"])
            ex = c.start_execution(task["id"], "dsh", workspace=str(root / "wt"))
            ws = c.create_workspace(
                task["id"], "repo1", str(repo), execution_id=ex["execution_id"], path=str(root / "wt")
            )
            (root / "wt" / "src" / "a.py").write_text(
                "def div(a, b, default=1):\n    return a / b if b else default\n",
                encoding="utf-8",
            )
            c.commit_workspace(ws["workspace_id"], "change signature")
            c.finish_execution(ex["execution_id"], "succeeded")

            candidate = c.create_candidate_from_workspace(ws["workspace_id"])
            changes = c.list_semantic_changes(candidate_id=candidate["candidate_id"])
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0]["compatibility"], "breaking")

            impact = c.semantic_impact(changes[0]["change_id"])
            affected = {x["task_id"] for x in impact["affected_tasks"]}
            self.assertIn(other["id"], affected)
            self.assertTrue(impact["requires_admin"])

            c.ack_semantic_change(changes[0]["change_id"], other["id"], "dsh")
            impact = c.semantic_impact(changes[0]["change_id"])
            acked = {x["task_id"]: x["acked"] for x in impact["affected_tasks"]}
            self.assertTrue(acked[other["id"]])

            c.freeze_candidate(candidate["candidate_id"])
            c.record_evidence(
                candidate["candidate_id"], "test", exit_code=0,
                command="controlled-test:platform-test",
                sha=candidate["candidate_sha"], check_id="platform-test",
            )
            c.record_evidence(
                candidate["candidate_id"], "review", exit_code=0,
                command="controlled-test:agent-review",
                sha=candidate["candidate_sha"], check_id="agent-review",
            )
            c.mark_candidate_ready(candidate["candidate_id"])
            with self.assertRaises(CoordinatorError):
                c.approve_candidate(candidate["candidate_id"], "alice", role="reviewer")
            approved = c.approve_candidate(candidate["candidate_id"], "bob", role="admin")
            self.assertEqual(approved["status"], "approved")

    def test_agent_capability_filters_recommendation(self):
        with tempfile.TemporaryDirectory() as td:
            c = Coordinator(Path(td) / "db.sqlite")
            project = c.create_project("caps")
            task = c.create_task(project["project_id"], "python task", ["src/a.py"], repo_id="repo1")
            c.set_agent_capability("dsh", "language", "python", confidence=0.9, source="manual")
            c.set_agent_capability("codex-deepseek", "language", "typescript", confidence=0.8)
            result = recommend_agent(
                c, task["id"], strategy="time", required_tags=["language:python"],
            )
            self.assertEqual(result["recommended"]["agent"], "dsh")
            self.assertEqual(result["required_tags"], ["language:python"])
            self.assertEqual([x["agent"] for x in result["candidates"]], ["dsh"])


if __name__ == "__main__":
    unittest.main()
