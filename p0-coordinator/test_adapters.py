"""Adapter 合同测试：probe/start/stop，不依赖真实 Codex/DSH。"""

from __future__ import annotations

import os
import sys
import time
import unittest
import uuid
from pathlib import Path

from adapters import (
    AdapterError,
    CodexAdapter,
    CodexAppServerAdapter,
    CodexExecAdapter,
    CommandAdapter,
    DshAdapter,
    MockAdapter,
    all_adapters,
    extract_usage,
    probe_all,
)

ROOT = Path(__file__).resolve().parent
TMP = ROOT / ".tmp"


class AdapterTest(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)
        self.workspace = TMP / f"adapter-{uuid.uuid4().hex}"
        self.workspace.mkdir()

    def tearDown(self):
        try:
            self.workspace.rmdir()
        except OSError:
            pass

    def test_mock_lifecycle(self):
        a = MockAdapter()
        self.assertTrue(a.probe()["available"])
        run = a.start("T-1", str(self.workspace))
        self.assertEqual(run["status"], "running")
        self.assertTrue(a.events(run["run_id"]))
        self.assertEqual(a.stop(run["run_id"])["status"], "stopped")

    def test_missing_command(self):
        a = CommandAdapter("missing", ["definitely-not-a-real-exe-xyz"])
        self.assertFalse(a.probe()["available"])
        with self.assertRaises(AdapterError):
            a.start("T-1", str(self.workspace))

    def test_command_start_stop(self):
        a = CommandAdapter("py", [sys.executable, "-c", "import time; time.sleep(30)"])
        self.assertTrue(a.probe()["available"])
        run = a.start("T-1", str(self.workspace))
        self.assertIsInstance(run["pid"], int)
        stopped = a.stop(run["run_id"])
        self.assertEqual(stopped["status"], "stopped")

    def test_codex_unavailable_without_binary(self):
        a = CodexAdapter(command=["definitely-not-codex-xyz", "app-server"])
        self.assertFalse(a.probe()["available"])

    def test_dsh_requires_api_key(self):
        old = os.environ.pop("DEEPSEEK_API_KEY", None)
        try:
            a = DshAdapter(command=[sys.executable, "-c", "pass"])
            info = a.probe()
            self.assertFalse(info["available"])
            self.assertIn("DEEPSEEK_API_KEY", info["reason"])
        finally:
            if old is not None:
                os.environ["DEEPSEEK_API_KEY"] = old

    def test_dsh_headless_events(self):
        old = os.environ.get("DEEPSEEK_API_KEY")
        os.environ["DEEPSEEK_API_KEY"] = "test"
        try:
            code = (
                "import sys, json; "
                "print(json.dumps({'type':'final','text':sys.argv[1]}))"
            )
            a = DshAdapter(command=[sys.executable, "-c", code])
            self.assertTrue(a.probe()["available"])
            run = a.start("T-1", str(self.workspace), prompt="hello")
            a.wait(run["run_id"], timeout=10)
            time.sleep(0.1)
            events = a.events(run["run_id"])
            self.assertTrue(any(e.get("type") == "final" for e in events), events)
        finally:
            if old is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = old

    def test_codex_appserver_probe(self):
        a = CodexAppServerAdapter(command=[sys.executable, "-c", "pass"])
        info = a.probe()
        self.assertTrue(info["available"])
        self.assertEqual(info["transport"], "stdio")

    def test_codex_exec_headless_events(self):
        code = (
            "import sys, json; "
            "print(json.dumps({'type':'item.completed','text':sys.argv[1]}))"
        )
        a = CodexExecAdapter(command=[sys.executable, "-c", code], check_login=False)
        self.assertTrue(a.probe()["available"])
        run = a.start("T-1", str(self.workspace), prompt="hello-codex")
        a.wait(run["run_id"], timeout=10)
        time.sleep(0.1)
        events = a.events(run["run_id"])
        self.assertTrue(any(e.get("type") == "item.completed" for e in events), events)

    def test_extract_usage(self):
        events = [
            {"type": "step", "usage": {"inputTokens": 10, "outputTokens": 5}},
            {"type": "final", "total_cost_usd": 0.0123,
             "usage": {"inputTokens": 12, "cacheReadTokens": 8, "cacheWriteTokens": 2}},
        ]
        usage = extract_usage(events)
        self.assertEqual(usage["input_tokens"], 12)
        self.assertEqual(usage["output_tokens"], 5)
        self.assertEqual(usage["cached_input_tokens"], 8)
        self.assertEqual(usage["cache_write_tokens"], 2)
        self.assertAlmostEqual(usage["cost_usd"], 0.0123)

    def test_registry(self):
        self.assertEqual(set(all_adapters()), {"codex", "codex-exec", "codex-appserver", "dsh", "mock"})
        self.assertEqual(len(probe_all()), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
