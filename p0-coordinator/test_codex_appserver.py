"""P1-3：Python Codex App Server 状态、时序、超时与审批的离线回归。

所有场景由 codex_appserver_stub.py 驱动，不调用真实模型。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from adapters import AdapterError, CodexAppServerAdapter, _AppServerClient

ROOT = Path(__file__).resolve().parent
TMP = ROOT / ".tmp"
STUB = ROOT / "codex_appserver_stub.py"


class AppServerTest(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)
        self.workspace = TMP / f"appserver-{uuid.uuid4().hex}"
        self.workspace.mkdir()
        self.log_path = self.workspace / "requests.jsonl"
        self.adapter: CodexAppServerAdapter | None = None
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        if self.adapter is not None:
            for item in list(self.adapter._runs.values()):
                proc = item.get("proc")
                if proc is not None and proc.poll() is None:
                    proc.kill()
            self.adapter._runs.clear()
        shutil.rmtree(self.workspace, ignore_errors=True)

    def make_adapter(self, scenario: str, late_event_grace: float = 0.2) -> CodexAppServerAdapter:
        self.adapter = CodexAppServerAdapter(
            command=[sys.executable, str(STUB), scenario],
            late_event_grace=late_event_grace,
        )
        return self.adapter

    def start(self, scenario: str, late_event_grace: float = 0.2) -> tuple[CodexAppServerAdapter, dict]:
        adapter = self.make_adapter(scenario, late_event_grace)
        run = adapter.start(
            "T-1", str(self.workspace), prompt="offline fixture",
            env={"GUILD_STUB_LOG": str(self.log_path)},
        )
        return adapter, run

    def frames(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        rows = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def incoming(self, method: str) -> list[dict]:
        return [row["frame"] for row in self.frames()
                if row["direction"] == "in" and row["frame"].get("method") == method]

    def outgoing_result(self, mid: object) -> dict | None:
        for row in self.frames():
            frame = row["frame"]
            if (
                row["direction"] == "in"
                and frame.get("id") == mid
                and "method" not in frame
            ):
                return frame
        return None

    # ---------- 终态 ----------
    def test_completed_is_success(self):
        adapter, run = self.start("completed")
        result = adapter.wait(run["run_id"], timeout=2)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["error"], "")
        self.assertTrue(any(
            event.get("method") == "turn/completed"
            for event in adapter.events(run["run_id"])
        ))

    def test_failed_is_not_success(self):
        adapter, run = self.start("failed")
        result = adapter.wait(run["run_id"], timeout=2)
        self.assertEqual(result["status"], "failed")
        self.assertNotEqual(result["exit_code"], 0)
        self.assertIn("stub turn failed", result["error"])

    def test_interrupted_is_not_success(self):
        adapter, run = self.start("interrupted")
        result = adapter.wait(run["run_id"], timeout=2)
        self.assertEqual(result["status"], "interrupted")
        self.assertIn("interrupted", result["error"].lower())

    def test_incomplete_status_is_unknown(self):
        adapter, run = self.start("invalid_status")
        result = adapter.wait(run["run_id"], timeout=2)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("unknown", result["error"].lower())

    def test_failed_turn_start_response_is_not_success(self):
        adapter, run = self.start("turn_start_failed_response")
        result = adapter.wait(run["run_id"], timeout=1)
        self.assertEqual(result["status"], "failed")
        self.assertIn("immediate failure", result["error"])

    # ---------- 时序与关联 ----------
    def test_completion_before_turn_start_response_is_not_lost(self):
        adapter, run = self.start("completed_before_response")
        result = adapter.wait(run["run_id"], timeout=2)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["exit_code"], 0)

    def test_other_thread_completion_is_ignored(self):
        adapter, run = self.start("wrong_thread")
        result = adapter.wait(run["run_id"], timeout=0.3)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("timeout", result["error"].lower())

    def test_other_turn_completion_is_ignored_then_correct_turn_completes(self):
        adapter, run = self.start("wrong_turn_then_correct")
        result = adapter.wait(run["run_id"], timeout=2)
        self.assertEqual(result["status"], "completed")
        completions = [
            event for event in adapter.events(run["run_id"])
            if event.get("method") == "turn/completed"
        ]
        self.assertEqual(len(completions), 2)
        self.assertEqual(completions[0]["params"]["turn"]["id"], "turn-other")

    # ---------- 超时、取消、退出、迟到 ----------
    def test_timeout_is_unknown_and_never_retries_turn_start(self):
        adapter, run = self.start("timeout")
        result = adapter.wait(run["run_id"], timeout=0.2)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("execution state is unknown", result["error"])
        self.assertEqual(len(self.incoming("turn/start")), 1)
        self.assertEqual(len(self.incoming("thread/start")), 1)

    def test_late_completion_does_not_change_timeout_result(self):
        adapter, run = self.start("late_completion", late_event_grace=0.3)
        result = adapter.wait(run["run_id"], timeout=0.1)
        self.assertEqual(result["status"], "unknown")
        time.sleep(0.1)
        methods = [
            event.get("method") for event in adapter.events(run["run_id"])
        ]
        self.assertIn("turn/completed", methods)

    def test_stop_sends_interrupt_and_reports_turn_status(self):
        adapter, run = self.start("interrupt")
        stopped = adapter.stop(run["run_id"])
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["turn_status"], "interrupted")
        self.assertTrue(stopped["confirmed"])
        self.assertEqual(len(self.incoming("turn/interrupt")), 1)

    def test_process_exit_before_completion_is_closed(self):
        adapter, run = self.start("exit_early")
        result = adapter.wait(run["run_id"], timeout=2)
        self.assertEqual(result["status"], "closed")
        self.assertIn("exited", result["error"])

    def test_start_failure_from_turn_start_is_reported(self):
        adapter = self.make_adapter("turn_start_error")
        with self.assertRaisesRegex(AdapterError, "turn start rejected"):
            adapter.start("T-1", str(self.workspace), prompt="offline fixture")

    def test_client_request_timeout_does_not_resend(self):
        proc = subprocess.Popen(
            [sys.executable, str(STUB), "stall_method"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "GUILD_STUB_LOG": str(self.log_path)},
        )
        client = _AppServerClient(proc)
        try:
            with self.assertRaisesRegex(AdapterError, "timed out; execution state is unknown"):
                client.request("turn/start", {}, timeout=0.2)
            self.assertEqual(len(self.incoming("turn/start")), 1)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            for stream in (proc.stdin, proc.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass

    # ---------- 服务端请求与审批 ----------
    def test_approval_request_is_denied_by_default(self):
        adapter, run = self.start("approval")
        result = adapter.wait(run["run_id"], timeout=2)
        self.assertEqual(result["status"], "completed")
        requests = adapter.server_requests(run["run_id"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["method"], "item/commandExecution/requestApproval")
        self.assertEqual(requests[0]["source"], "default-deny")
        response = self.outgoing_result(900)
        self.assertIsNotNone(response)
        self.assertEqual(response["result"], {"decision": "decline"})

    def test_approval_handler_can_decide_explicitly(self):
        self.adapter = CodexAppServerAdapter(
            command=[sys.executable, str(STUB), "approval"],
            approval_handler=lambda method, params: {"decision": "accept"},
            late_event_grace=0.2,
        )
        run = self.adapter.start(
            "T-1", str(self.workspace), prompt="offline fixture",
            env={"GUILD_STUB_LOG": str(self.log_path)},
        )
        result = self.adapter.wait(run["run_id"], timeout=2)
        self.assertEqual(result["status"], "completed")
        requests = self.adapter.server_requests(run["run_id"])
        self.assertEqual(requests[0]["source"], "handler")
        self.assertEqual(self.outgoing_result(900)["result"], {"decision": "accept"})

    def test_unsupported_server_request_gets_jsonrpc_error(self):
        adapter, run = self.start("unsupported")
        result = adapter.wait(run["run_id"], timeout=2)
        self.assertEqual(result["status"], "completed")
        response = self.outgoing_result(901)
        self.assertIsNotNone(response)
        self.assertEqual(response["error"]["code"], -32601)
        self.assertIn("unsupported", response["error"]["message"])

    def test_notifications_are_not_answered(self):
        adapter, run = self.start("notification_only")
        result = adapter.wait(run["run_id"], timeout=2)
        self.assertEqual(result["status"], "completed")
        events = adapter.events(run["run_id"])
        self.assertTrue(any(event.get("method") == "warning" for event in events))
        # 通知不是服务端请求，不应产生审批记录或响应
        self.assertEqual(adapter.server_requests(run["run_id"]), [])

    # ---------- 审批策略配置 ----------
    def test_thread_start_keeps_approval_channel_enabled(self):
        adapter, run = self.start("completed")
        adapter.wait(run["run_id"], timeout=2)
        thread_start = self.incoming("thread/start")[0]
        self.assertEqual(thread_start["params"]["approvalPolicy"], "on-request")
        self.assertEqual(thread_start["params"]["approvalsReviewer"], "user")
        self.assertEqual(thread_start["params"]["sandbox"], "workspace-write")
        self.assertNotEqual(thread_start["params"]["approvalPolicy"], "never")
        self.assertNotEqual(thread_start["params"]["sandbox"], "danger-full-access")

    def test_sandbox_can_only_be_changed_explicitly(self):
        with patch.dict(os.environ, {"CODEX_APP_SERVER_SANDBOX": "read-only"}):
            adapter, run = self.start("completed")
            adapter.wait(run["run_id"], timeout=2)
        thread_start = self.incoming("thread/start")[0]
        self.assertEqual(thread_start["params"]["sandbox"], "read-only")
        self.assertEqual(thread_start["params"]["approvalPolicy"], "on-request")

    def test_invalid_sandbox_env_is_rejected(self):
        with patch.dict(os.environ, {"CODEX_APP_SERVER_SANDBOX": "unsafe-mode"}):
            adapter = self.make_adapter("completed")
            with self.assertRaisesRegex(AdapterError, "invalid CODEX_APP_SERVER_SANDBOX"):
                adapter.start("T-1", str(self.workspace), prompt="offline fixture")


if __name__ == "__main__":
    unittest.main(verbosity=2)
