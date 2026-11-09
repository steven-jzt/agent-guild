"""P1-1 回归测试：Candidate 证据门禁。

覆盖：
- 必需测试通过但审查失败 / 缺少审查；
- 多个必需测试中的一个失败；
- 同一检查项重跑的确定性排序（失败→成功、成功→失败、同时间戳）；
- 证据属于其他 Candidate / 其他 SHA / 旧验证批次；
- awaiting_human 之后新增有效失败；
- 绕过入口（直接批准、CLI、MCP）；
- 默认必需项、检查配置变化、批次基线变化；
- 旧数据库升级：历史证据保留但不被静默认定为通过。

单元测试中的 review 证据是**受控测试证据**（command 明确标注），
不代表真实 Agent 审查结果，也不用于任何 pilot/demo 的成功叙事。
"""

from __future__ import annotations

import sqlite3
import sys
import types
import unittest
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from coordinator import Coordinator, CoordinatorError

ROOT = Path(__file__).resolve().parent
TMP = ROOT / ".tmp"

BASE = "b" * 40
SHA = "c" * 40
OTHER_SHA = "d" * 40

TEST_ID = "platform-test"
REVIEW_ID = "agent-review"


class EvidenceGateTest(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)
        self.db = TMP / f"gate-{uuid.uuid4().hex}.db"
        self.c = Coordinator(self.db)
        self.p = self.c.create_project("gate")
        self.t = self.c.create_task(
            self.p["project_id"], "gate task", ["src/a.py"], repo_id="repo1"
        )
        execution = self.c.start_execution(self.t["id"], "agent-a")
        self.c.finish_execution(execution["execution_id"], "succeeded", "done")
        self.cand = self.c.create_candidate(
            self.t["id"], "repo1", BASE, SHA,
            author_execution_id=execution["execution_id"],
        )
        self.cid = self.cand["candidate_id"]
        self.c.freeze_candidate(self.cid)

    def tearDown(self):
        try:
            self.db.unlink()
        except OSError:
            pass

    # ---------- helpers ----------
    def ev(self, check_id, exit_code, sha=SHA, type_="test", **kwargs):
        return self.c.record_evidence(
            self.cid, type_, check_id=check_id, sha=sha, exit_code=exit_code,
            command=f"controlled-test:{check_id}", **kwargs,
        )

    def pass_both(self):
        self.ev(TEST_ID, 0)
        self.ev(REVIEW_ID, 0, type_="review")

    def force_awaiting(self):
        with self.c._conn() as conn:
            conn.execute("UPDATE candidates SET status='awaiting_human' WHERE id=?", (self.cid,))
            conn.execute("UPDATE tasks SET lifecycle='human_review' WHERE id=?", (self.t["id"],))

    def force_approved(self):
        self.force_awaiting()
        with self.c._conn() as conn:
            conn.execute("UPDATE candidates SET status='approved' WHERE id=?", (self.cid,))
            conn.execute("UPDATE tasks SET lifecycle='ready_to_merge' WHERE id=?", (self.t["id"],))

    # ---------- 基本门禁 ----------
    def test_required_test_passes_but_review_fails_blocks_ready(self):
        self.ev(TEST_ID, 0)
        self.ev(REVIEW_ID, 1, type_="review")
        with self.assertRaisesRegex(CoordinatorError, "agent-review"):
            self.c.mark_candidate_ready(self.cid)
        self.assertEqual(self.c.get_candidate(self.cid)["status"], "validating")

    def test_missing_review_blocks_ready(self):
        self.ev(TEST_ID, 0)
        with self.assertRaisesRegex(CoordinatorError, "agent-review"):
            self.c.mark_candidate_ready(self.cid)

    def test_missing_test_blocks_ready(self):
        self.ev(REVIEW_ID, 0, type_="review")
        with self.assertRaisesRegex(CoordinatorError, "platform-test"):
            self.c.mark_candidate_ready(self.cid)

    def test_two_required_tests_one_fails_blocks(self):
        self.c.set_required_checks(self.p["project_id"], [
            {"id": TEST_ID, "kind": "test"},
            {"id": "integration-test", "kind": "test"},
            {"id": REVIEW_ID, "kind": "review"},
        ])
        self.c.begin_validation_batch(self.cid, "config changed")
        self.ev(TEST_ID, 0)
        self.ev("integration-test", 1)
        self.ev(REVIEW_ID, 0, type_="review")
        with self.assertRaisesRegex(CoordinatorError, "integration-test"):
            self.c.mark_candidate_ready(self.cid)

    def test_all_required_pass_allows_ready_and_approve(self):
        self.pass_both()
        ready = self.c.mark_candidate_ready(self.cid)
        self.assertEqual(ready["status"], "awaiting_human")
        approved = self.c.approve_candidate(self.cid, "alice")
        self.assertEqual(approved["status"], "approved")

    # ---------- 重跑与排序 ----------
    def test_fail_then_rerun_success_allows_and_keeps_history(self):
        self.ev(TEST_ID, 1)
        self.ev(TEST_ID, 0)
        self.ev(REVIEW_ID, 0, type_="review")
        self.assertEqual(self.c.mark_candidate_ready(self.cid)["status"], "awaiting_human")
        history = [e for e in self.c.list_evidence(self.cid) if e["check_id"] == TEST_ID]
        self.assertEqual([e["exit_code"] for e in history], [1, 0])
        self.assertEqual(len(history), 2)

    def test_success_then_fail_blocks(self):
        self.ev(TEST_ID, 0)
        self.ev(TEST_ID, 1)
        self.ev(REVIEW_ID, 0, type_="review")
        with self.assertRaisesRegex(CoordinatorError, "platform-test"):
            self.c.mark_candidate_ready(self.cid)

    def test_ordering_does_not_depend_on_timestamp(self):
        self.ev(TEST_ID, 0)
        self.ev(TEST_ID, 1)
        # 时间戳完全相同，仍必须按递增 seq 取最后一条（失败）
        with self.c._conn() as conn:
            conn.execute(
                "UPDATE evidence SET created_at=123.0 WHERE candidate_id=?", (self.cid,)
            )
        self.ev(REVIEW_ID, 0, type_="review")
        with self.assertRaisesRegex(CoordinatorError, "platform-test"):
            self.c.mark_candidate_ready(self.cid)

        self.ev(TEST_ID, 0)
        with self.c._conn() as conn:
            conn.execute(
                "UPDATE evidence SET created_at=123.0 WHERE candidate_id=?", (self.cid,)
            )
        self.assertEqual(self.c.mark_candidate_ready(self.cid)["status"], "awaiting_human")

    # ---------- 绑定关系 ----------
    def test_evidence_from_old_batch_does_not_count(self):
        self.ev(TEST_ID, 0)
        self.c.begin_validation_batch(self.cid, "revalidate")
        self.ev(REVIEW_ID, 0, type_="review")
        with self.assertRaisesRegex(CoordinatorError, "platform-test"):
            self.c.mark_candidate_ready(self.cid)

    def test_evidence_for_other_sha_cannot_satisfy(self):
        with self.assertRaisesRegex(CoordinatorError, "SHA"):
            self.ev(TEST_ID, 0, sha=OTHER_SHA)
        # 直接写库模拟历史脏数据：门禁仍必须拒绝
        batch = self.c.get_candidate(self.cid)["active_batch_id"]
        with self.c._tx() as conn:
            conn.execute(
                "INSERT INTO evidence(id, candidate_id, batch_id, check_id, seq, type, "
                "command, cwd, exit_code, output_ref, sha, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("EV-othersha", self.cid, batch, TEST_ID, 999, "test", "", "", 0, "", OTHER_SHA,
                 "valid", 1.0),
            )
        with self.assertRaisesRegex(CoordinatorError, "platform-test"):
            self.c.mark_candidate_ready(self.cid)

    def test_evidence_for_other_candidate_cannot_satisfy(self):
        self.ev(TEST_ID, 0)
        # 造一个别的候选，把它的证据塞进本候选的批次里
        other = self.c.create_candidate(self.t["id"], "repo1", BASE, OTHER_SHA)
        batch = self.c.get_candidate(self.cid)["active_batch_id"]
        with self.c._tx() as conn:
            conn.execute(
                "INSERT INTO evidence(id, candidate_id, batch_id, check_id, seq, type, "
                "command, cwd, exit_code, output_ref, sha, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("EV-othercand", other["candidate_id"], batch, REVIEW_ID, 999, "review", "", "",
                 0, "", SHA, "valid", 1.0),
            )
        with self.assertRaises(CoordinatorError):
            self.c.mark_candidate_ready(self.cid)

    def test_void_evidence_does_not_count(self):
        row = self.ev(TEST_ID, 0)
        self.c.void_evidence(row["evidence_id"], "invalid workspace")
        self.ev(REVIEW_ID, 0, type_="review")
        with self.assertRaisesRegex(CoordinatorError, "platform-test"):
            self.c.mark_candidate_ready(self.cid)

    # ---------- 类别规范（R1） ----------
    def test_test_evidence_cannot_satisfy_agent_review(self):
        self.ev(TEST_ID, 0)
        with self.assertRaisesRegex(CoordinatorError, "kind|type"):
            self.ev(REVIEW_ID, 0, type_="test")
        self.assertFalse(self.c.evaluate_candidate_gate(self.cid)["ok"])

        # 直接写库模拟绕过写入校验的历史/脏数据：门禁也必须拒绝
        batch = self.c.get_candidate(self.cid)["active_batch_id"]
        with self.c._tx() as conn:
            conn.execute(
                "INSERT INTO evidence(id, candidate_id, batch_id, check_id, seq, type, "
                "command, cwd, exit_code, output_ref, sha, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("EV-wrongkind", self.cid, batch, REVIEW_ID, 100, "test", "", "",
                 0, "", SHA, "valid", 1.0),
            )
            conn.execute("UPDATE validation_batches SET next_seq=100 WHERE id=?", (batch,))
        gate = self.c.evaluate_candidate_gate(self.cid)
        self.assertFalse(gate["ok"])
        self.assertIn(REVIEW_ID, gate["invalid"])

        # 正确类别的新结果可以恢复
        self.ev(REVIEW_ID, 0, type_="review")
        self.assertTrue(self.c.evaluate_candidate_gate(self.cid)["ok"])
        self.assertEqual(self.c.mark_candidate_ready(self.cid)["status"], "awaiting_human")

    # ---------- 作废最新结果（R2） ----------
    def test_void_latest_failure_requires_revalidation(self):
        self.pass_both()
        failure = self.ev(REVIEW_ID, 1, type_="review")
        self.assertFalse(self.c.evaluate_candidate_gate(self.cid)["ok"])

        voided = self.c.void_evidence(failure["evidence_id"], "needs revalidation")
        self.assertEqual(voided["status"], "void")
        gate = self.c.evaluate_candidate_gate(self.cid)
        self.assertFalse(gate["ok"], "作废最新失败后不能回退到更早的成功")
        self.assertIn(REVIEW_ID, gate["invalid"])

        # 新的有效结果通过后才恢复
        self.ev(REVIEW_ID, 0, type_="review")
        self.assertTrue(self.c.evaluate_candidate_gate(self.cid)["ok"])
        self.assertEqual(self.c.mark_candidate_ready(self.cid)["status"], "awaiting_human")

    def test_void_latest_success_also_requires_revalidation(self):
        self.pass_both()
        latest = [e for e in self.c.list_evidence(self.cid) if e["check_id"] == REVIEW_ID][-1]
        self.c.void_evidence(latest["evidence_id"], "suspect result")
        self.assertFalse(self.c.evaluate_candidate_gate(self.cid)["ok"])
        self.ev(REVIEW_ID, 0, type_="review")
        self.assertTrue(self.c.evaluate_candidate_gate(self.cid)["ok"])

    def test_extra_non_required_evidence_allowed_but_not_substitutable(self):
        self.pass_both()
        self.ev("lint", 1, type_="check")
        self.assertEqual(self.c.mark_candidate_ready(self.cid)["status"], "awaiting_human")

    # ---------- awaiting_human 之后 ----------
    def test_new_failure_after_awaiting_blocks_approve(self):
        self.pass_both()
        self.c.mark_candidate_ready(self.cid)
        self.ev(REVIEW_ID, 1, type_="review")
        with self.assertRaisesRegex(CoordinatorError, "agent-review"):
            self.c.approve_candidate(self.cid, "alice")
        self.assertEqual(self.c.get_candidate(self.cid)["status"], "awaiting_human")
        # 再重跑成功，批准恢复
        self.ev(REVIEW_ID, 0, type_="review")
        self.assertEqual(self.c.approve_candidate(self.cid, "alice")["status"], "approved")

    def test_direct_approve_without_results_blocks(self):
        self.force_awaiting()
        with self.assertRaisesRegex(CoordinatorError, "platform-test"):
            self.c.approve_candidate(self.cid, "alice")

    # ---------- 配置与批次失效 ----------
    def test_required_checks_change_invalidates_batch(self):
        self.pass_both()
        self.assertTrue(self.c.evaluate_candidate_gate(self.cid)["ok"])
        self.c.set_required_checks(self.p["project_id"], [
            {"id": TEST_ID, "kind": "test"},
            {"id": "integration-test", "kind": "test"},
            {"id": REVIEW_ID, "kind": "review"},
        ])
        gate = self.c.evaluate_candidate_gate(self.cid)
        self.assertFalse(gate["ok"])
        self.assertTrue(gate["stale"], gate)
        with self.assertRaisesRegex(CoordinatorError, "stale|integration-test"):
            self.c.mark_candidate_ready(self.cid)
        self.c.begin_validation_batch(self.cid, "config changed")
        self.ev(TEST_ID, 0)
        self.ev("integration-test", 0)
        self.ev(REVIEW_ID, 0, type_="review")
        self.assertEqual(self.c.mark_candidate_ready(self.cid)["status"], "awaiting_human")

    def test_batch_base_sha_mismatch_invalidates(self):
        self.pass_both()
        batch = self.c.get_candidate(self.cid)["active_batch_id"]
        with self.c._tx() as conn:
            conn.execute("UPDATE validation_batches SET base_sha=? WHERE id=?", ("e" * 40, batch))
        gate = self.c.evaluate_candidate_gate(self.cid)
        self.assertFalse(gate["ok"])
        self.assertTrue(gate["stale"], gate)

    def test_task_required_checks_override_adds_item(self):
        t2 = self.c.create_task(
            self.p["project_id"], "stricter", ["src/b.py"], repo_id="repo1",
            required_checks=[
                {"id": TEST_ID, "kind": "test"},
                {"id": "integration-test", "kind": "test"},
                {"id": REVIEW_ID, "kind": "review"},
            ],
        )
        execution = self.c.start_execution(t2["id"], "agent-a")
        self.c.finish_execution(execution["execution_id"], "succeeded", "done")
        cand = self.c.create_candidate(
            t2["id"], "repo1", BASE, SHA, author_execution_id=execution["execution_id"]
        )
        cid = cand["candidate_id"]
        self.c.freeze_candidate(cid)
        self.c.record_evidence(cid, "test", check_id=TEST_ID, sha=SHA, exit_code=0)
        self.c.record_evidence(cid, "review", check_id=REVIEW_ID, sha=SHA, exit_code=0)
        with self.assertRaisesRegex(CoordinatorError, "integration-test"):
            self.c.mark_candidate_ready(cid)
        self.c.record_evidence(cid, "test", check_id="integration-test", sha=SHA, exit_code=0)
        self.assertEqual(self.c.mark_candidate_ready(cid)["status"], "awaiting_human")

    def test_task_additions_keep_project_required_review(self):
        project = self.c.create_project("strict-project", required_checks=[
            {"id": TEST_ID, "kind": "test"},
            {"id": "integration-test", "kind": "test"},
            {"id": REVIEW_ID, "kind": "review"},
        ])
        task = self.c.create_task(
            project["project_id"], "task additions", ["src/b.py"], repo_id="repo1",
            required_checks=[{"id": "lint", "kind": "check"}],
        )
        ids = [item["id"] for item in self.c.required_checks_for_task(task["id"])]
        self.assertEqual(set(ids), {TEST_ID, "integration-test", REVIEW_ID, "lint"})

    def test_task_addition_cannot_change_project_kind(self):
        with self.assertRaises(CoordinatorError):
            self.c.create_task(
                self.p["project_id"], "kind conflict", ["src/b.py"], repo_id="repo1",
                required_checks=[{"id": TEST_ID, "kind": "review"}],
            )

    def test_project_addition_applies_to_existing_task(self):
        task = self.c.create_task(
            self.p["project_id"], "existing", ["src/b.py"], repo_id="repo1"
        )
        self.c.set_required_checks(self.p["project_id"], [
            {"id": TEST_ID, "kind": "test"},
            {"id": "integration-test", "kind": "test"},
            {"id": REVIEW_ID, "kind": "review"},
        ])
        ids = [item["id"] for item in self.c.required_checks_for_task(task["id"])]
        self.assertIn("integration-test", ids)

    def test_task_configuration_cannot_remove_project_check(self):
        project = self.c.create_project("policy", required_checks=[
            {"id": TEST_ID, "kind": "test"},
            {"id": "integration-test", "kind": "test"},
            {"id": REVIEW_ID, "kind": "review"},
        ])
        task = self.c.create_task(
            project["project_id"], "weaker task", ["src/b.py"], repo_id="repo1",
            required_checks=[
                {"id": TEST_ID, "kind": "test"},
                {"id": REVIEW_ID, "kind": "review"},
            ],
        )
        ids = [item["id"] for item in self.c.required_checks_for_task(task["id"])]
        self.assertIn("integration-test", ids)

        execution = self.c.start_execution(task["id"], "agent-a")
        self.c.finish_execution(execution["execution_id"], "succeeded", "done")
        cand = self.c.create_candidate(
            task["id"], "repo1", BASE, SHA, author_execution_id=execution["execution_id"]
        )
        cid = cand["candidate_id"]
        self.c.freeze_candidate(cid)
        self.c.record_evidence(cid, "test", check_id=TEST_ID, sha=SHA, exit_code=0)
        self.c.record_evidence(cid, "review", check_id=REVIEW_ID, sha=SHA, exit_code=0)
        with self.assertRaisesRegex(CoordinatorError, "integration-test"):
            self.c.mark_candidate_ready(cid)

    def test_config_change_after_awaiting_blocks_approve(self):
        self.pass_both()
        self.c.mark_candidate_ready(self.cid)
        self.c.set_required_checks(self.p["project_id"], [
            {"id": TEST_ID, "kind": "test"},
            {"id": "integration-test", "kind": "test"},
            {"id": REVIEW_ID, "kind": "review"},
        ])
        with self.assertRaisesRegex(CoordinatorError, "stale"):
            self.c.approve_candidate(self.cid, "alice")
        self.c.begin_validation_batch(self.cid, "config changed")
        self.ev(TEST_ID, 0)
        self.ev("integration-test", 0)
        self.ev(REVIEW_ID, 0, type_="review")
        self.assertEqual(self.c.approve_candidate(self.cid, "alice")["status"], "approved")

    def test_new_batch_requires_fresh_approval_chain(self):
        project = self.c.create_project("chain-batch", approval_chain="reviewer,admin")
        task = self.c.create_task(project["project_id"], "chain", ["src/c.py"], repo_id="repo1")
        execution = self.c.start_execution(task["id"], "agent-a")
        self.c.finish_execution(execution["execution_id"], "succeeded", "done")
        cand = self.c.create_candidate(
            task["id"], "repo1", BASE, SHA, author_execution_id=execution["execution_id"]
        )
        cid = cand["candidate_id"]
        self.c.freeze_candidate(cid)
        self.c.record_evidence(cid, "test", check_id=TEST_ID, sha=SHA, exit_code=0)
        self.c.record_evidence(cid, "review", check_id=REVIEW_ID, sha=SHA, exit_code=0)
        self.c.mark_candidate_ready(cid)

        first = self.c.approve_candidate(cid, "reviewer-1", role="reviewer")
        self.assertEqual(first["status"], "awaiting_human")
        self.assertEqual(first["pending_roles"], ["admin"])
        old_batch = self.c.get_candidate(cid)["active_batch_id"]

        self.c.set_required_checks(project["project_id"], [
            {"id": TEST_ID, "kind": "test"},
            {"id": "integration-test", "kind": "test"},
            {"id": REVIEW_ID, "kind": "review"},
        ])
        self.c.begin_validation_batch(cid, "config changed")
        new_batch = self.c.get_candidate(cid)["active_batch_id"]
        self.assertNotEqual(old_batch, new_batch)
        self.c.record_evidence(cid, "test", check_id=TEST_ID, sha=SHA, exit_code=0)
        self.c.record_evidence(cid, "review", check_id=REVIEW_ID, sha=SHA, exit_code=0)
        self.c.record_evidence(cid, "test", check_id="integration-test", sha=SHA, exit_code=0)

        # 旧批次的 reviewer 批准不能补齐新批次审批链
        with self.assertRaisesRegex(CoordinatorError, "next approval role is reviewer"):
            self.c.approve_candidate(cid, "admin-1", role="admin")
        self.assertNotEqual(self.c.get_candidate(cid)["status"], "approved")

        second = self.c.approve_candidate(cid, "reviewer-2", role="reviewer")
        self.assertEqual(second["status"], "awaiting_human")
        self.assertEqual(second["pending_roles"], ["admin"])
        third = self.c.approve_candidate(cid, "admin-2", role="admin")
        self.assertEqual(third["status"], "approved")

        approvals = self.c.list_candidate_approvals(cid)
        self.assertEqual(len(approvals), 3)
        reviewer_batches = {a["batch_id"] for a in approvals if a["role"] == "reviewer"}
        self.assertEqual(reviewer_batches, {old_batch, new_batch})
        self.assertIn(new_batch, {a["batch_id"] for a in approvals if a["role"] == "admin"})

    def test_invalid_required_checks_rejected(self):
        with self.assertRaises(CoordinatorError):
            self.c.create_project("empty", required_checks=[])
        with self.assertRaises(CoordinatorError):
            self.c.create_project("noreview", required_checks=[{"id": "t", "kind": "test"}])
        with self.assertRaises(CoordinatorError):
            self.c.set_required_checks(self.p["project_id"], [{"id": "x", "kind": "bogus"}])

    # ---------- 绕过入口 ----------
    def test_cli_approve_cannot_bypass_gate(self):
        from contextlib import redirect_stderr
        from io import StringIO

        from worker_cli import main as cli_main
        self.force_awaiting()
        err = StringIO()
        with redirect_stderr(err):
            rc = cli_main([
                "--db", str(self.db), "approve-candidate",
                "--candidate", self.cid, "--approver", "cli",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("gate failed", err.getvalue())
        self.assertEqual(self.c.get_candidate(self.cid)["status"], "awaiting_human")

    def test_mcp_approve_cannot_bypass_gate(self):
        mcp = _import_mcp_server()
        self.force_awaiting()
        with self.assertRaisesRegex(CoordinatorError, "gate failed"):
            mcp.call_tool(self.c, "approve_candidate", {
                "candidate_id": self.cid, "approver": "mcp",
            })

    def test_mcp_record_evidence_keeps_check_id(self):
        mcp = _import_mcp_server()
        row = mcp.call_tool(self.c, "record_evidence", {
            "candidate_id": self.cid, "type": "test", "check_id": TEST_ID,
            "sha": SHA, "exit_code": 0,
        })
        self.assertEqual(row["check_id"], TEST_ID)
        self.assertEqual(row["batch_id"], self.c.get_candidate(self.cid)["active_batch_id"])


class LegacyDatabaseTest(unittest.TestCase):
    """旧库升级：不删历史，未证明有效的记录不能算通过。"""

    OLD_DDL = """
    CREATE TABLE projects (
        id TEXT PRIMARY KEY, name TEXT NOT NULL,
        require_approval INTEGER NOT NULL DEFAULT 0,
        require_verification INTEGER NOT NULL DEFAULT 0,
        approvals_required INTEGER NOT NULL DEFAULT 1,
        approval_ttl_seconds REAL NOT NULL DEFAULT 86400,
        required_approver_role TEXT NOT NULL DEFAULT 'reviewer',
        approval_chain TEXT NOT NULL DEFAULT '',
        budget_usd REAL, created_at REAL NOT NULL
    );
    CREATE TABLE candidates (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL, repo_id TEXT NOT NULL,
        author_execution_id TEXT, base_sha TEXT NOT NULL, candidate_sha TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'building', created_at REAL NOT NULL,
        frozen_at REAL, approved_by TEXT,
        approver_role TEXT NOT NULL DEFAULT 'reviewer',
        approved_at REAL, merged_at REAL, meta TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE evidence (
        id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, execution_id TEXT,
        type TEXT NOT NULL, command TEXT NOT NULL DEFAULT '',
        cwd TEXT NOT NULL DEFAULT '', exit_code INTEGER,
        output_ref TEXT NOT NULL DEFAULT '', sha TEXT, created_at REAL NOT NULL
    );
    CREATE TABLE candidate_approvals (
        id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, approver TEXT NOT NULL,
        role TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
        UNIQUE(candidate_id, role)
    );
    """

    def test_legacy_evidence_is_preserved_but_not_authoritative(self):
        TMP.mkdir(exist_ok=True)
        db = TMP / f"legacy-{uuid.uuid4().hex}.db"
        conn = sqlite3.connect(db)
        conn.executescript(self.OLD_DDL)
        conn.execute(
            "INSERT INTO projects(id, name, created_at) VALUES (?,?,?)",
            ("P-legacy", "legacy", 1.0),
        )
        conn.commit()
        conn.close()
        try:
            c = Coordinator(db)
            t = c.create_task("P-legacy", "legacy task", ["src/a.py"], repo_id="repo1")
            with c._tx() as tx:
                tx.execute(
                    "INSERT INTO candidates(id, task_id, repo_id, base_sha, candidate_sha, "
                    "status, created_at, frozen_at) VALUES (?,?,?,?,?,?,?,?)",
                    ("C-legacy", t["id"], "repo1", BASE, SHA, "validating", 1.0, 1.0),
                )
                tx.execute(
                    "INSERT INTO evidence(id, candidate_id, type, command, cwd, exit_code, "
                    "output_ref, sha, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("EV-legacy", "C-legacy", "test", "pytest", "", 0, "", SHA, 1.0),
                )
                tx.execute(
                    "INSERT INTO candidate_approvals(id, candidate_id, approver, role, note, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    ("CA-legacy", "C-legacy", "old-reviewer", "reviewer", "old batch", 1.0),
                )
            # 迁移后：历史证据/批准仍在，但没有批次/检查项，不能算通过
            with c._conn() as tx:
                rows = tx.execute("SELECT * FROM evidence").fetchall()
                approvals = tx.execute("SELECT * FROM candidate_approvals").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(approvals), 1)
            self.assertIsNone(approvals[0]["batch_id"])
            gate = c.evaluate_candidate_gate("C-legacy")
            self.assertFalse(gate["ok"])
            self.assertIsNone(gate["batch_id"])
            with self.assertRaises(CoordinatorError):
                c.mark_candidate_ready("C-legacy")
            # 显式开新批次并补齐两项后才可放行
            c.begin_validation_batch("C-legacy", "migration recovery")
            c.record_evidence("C-legacy", "test", check_id=TEST_ID, sha=SHA, exit_code=0)
            c.record_evidence("C-legacy", "review", check_id=REVIEW_ID, sha=SHA, exit_code=0)
            execution = c.start_execution(t["id"], "agent-a")
            c.finish_execution(execution["execution_id"], "succeeded", "done")
            self.assertEqual(c.mark_candidate_ready("C-legacy")["status"], "awaiting_human")
            c.approve_candidate("C-legacy", "new-reviewer")
            with c._conn() as tx:
                self.assertEqual(
                    tx.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 3
                )
                all_approvals = tx.execute(
                    "SELECT * FROM candidate_approvals ORDER BY created_at"
                ).fetchall()
            # 旧批准保留但 batch_id 为 NULL；新批准绑定当前批次
            self.assertEqual(len(all_approvals), 2)
            self.assertIsNone(all_approvals[0]["batch_id"])
            self.assertIsNotNone(all_approvals[1]["batch_id"])
        finally:
            try:
                db.unlink()
            except OSError:
                pass


OLD_APPROVALS_DDL = """
CREATE TABLE candidate_approvals (
    id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, approver TEXT NOT NULL,
    role TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
    UNIQUE(candidate_id, role)
);
"""

NEW_APPROVALS_DDL = """
CREATE TABLE candidate_approvals (
    id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, batch_id TEXT,
    approver TEXT NOT NULL, role TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
    UNIQUE(candidate_id, batch_id, role)
);
"""


class _InterruptApprovalCopy(Coordinator):
    """让迁移中的 INSERT ... SELECT 失败，用于验证回滚（不改产品源码）。"""

    @contextmanager
    def _conn(self):
        with super()._conn() as conn:
            def authorize(action, first, second, database, trigger):
                if action == sqlite3.SQLITE_INSERT and first == "candidate_approvals":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            conn.set_authorizer(authorize)
            yield conn


class ApprovalMigrationTest(unittest.TestCase):
    """candidate_approvals 重建：历史数据必须在 Coordinator 初始化之前写入。"""

    def setUp(self):
        TMP.mkdir(exist_ok=True)
        self.db = TMP / f"approval-migration-{uuid.uuid4().hex}.db"

    def tearDown(self):
        try:
            self.db.unlink()
        except OSError:
            pass

    def seed_legacy(self, rows):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executescript(OLD_APPROVALS_DDL)
            conn.executemany(
                "INSERT INTO candidate_approvals"
                "(id, candidate_id, approver, role, note, created_at) "
                "VALUES (?,?,?,?,?,?)",
                rows,
            )
            conn.commit()

    def make_half_migrated(self, copied_ids):
        """模拟一次中断：旧表已改名、新表已建、只复制了部分行。"""
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute(
                "ALTER TABLE candidate_approvals RENAME TO candidate_approvals_legacy"
            )
            conn.executescript(NEW_APPROVALS_DDL)
            if copied_ids:
                placeholders = ",".join("?" for _ in copied_ids)
                conn.execute(
                    "INSERT INTO candidate_approvals"
                    "(id, candidate_id, batch_id, approver, role, note, created_at) "
                    "SELECT id, candidate_id, NULL, approver, role, note, created_at "
                    "FROM candidate_approvals_legacy WHERE id IN (" + placeholders + ")",
                    tuple(copied_ids),
                )
            conn.commit()

    def tables(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

    def approval_columns(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return {row[1] for row in conn.execute("PRAGMA table_info(candidate_approvals)")}

    def approval_count(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute("SELECT COUNT(*) FROM candidate_approvals").fetchone()[0]

    def test_normal_upgrade_and_restarts_preserve_seeded_rows(self):
        self.seed_legacy([
            ("CA-1", "C-1", "human-1", "reviewer", "keep-1", 1.0),
            ("CA-2", "C-2", "human-2", "admin", "keep-2", 2.0),
        ])
        first = Coordinator(self.db)
        approvals = first.list_candidate_approvals("C-1")
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["approver"], "human-1")
        self.assertIsNone(approvals[0]["batch_id"])
        self.assertNotIn("candidate_approvals_legacy", self.tables())
        self.assertIn("batch_id", self.approval_columns())

        # 重复启动幂等：不丢行、不重复迁移
        Coordinator(self.db)
        second = Coordinator(self.db)
        self.assertEqual(self.approval_count(), 2)
        self.assertEqual(len(second.list_candidate_approvals("C-2")), 1)
        self.assertNotIn("candidate_approvals_legacy", self.tables())

    def test_copy_failure_rolls_back_and_restart_recovers(self):
        self.seed_legacy([
            ("CA-1", "C-1", "human-1", "reviewer", "keep", 1.0),
        ])
        with self.assertRaises(sqlite3.DatabaseError):
            _InterruptApprovalCopy(self.db)

        # 回滚后仍是旧表，legacy 不存在，历史行原样保留
        self.assertNotIn("candidate_approvals_legacy", self.tables())
        self.assertNotIn("batch_id", self.approval_columns())
        self.assertEqual(self.approval_count(), 1)

        core = Coordinator(self.db)
        approvals = core.list_candidate_approvals("C-1")
        self.assertEqual(len(approvals), 1)
        self.assertIsNone(approvals[0]["batch_id"])
        self.assertNotIn("candidate_approvals_legacy", self.tables())

        # 再次启动仍幂等
        Coordinator(self.db)
        self.assertEqual(self.approval_count(), 1)

    def test_half_migrated_state_is_reconciled(self):
        self.seed_legacy([
            ("CA-1", "C-1", "human-1", "reviewer", "keep-1", 1.0),
            ("CA-2", "C-2", "human-2", "admin", "keep-2", 2.0),
        ])
        self.make_half_migrated(["CA-1"])
        self.assertEqual(self.approval_count(), 1)
        self.assertIn("candidate_approvals_legacy", self.tables())

        core = Coordinator(self.db)
        self.assertEqual(self.approval_count(), 2)
        self.assertNotIn("candidate_approvals_legacy", self.tables())
        for candidate, approver in (("C-1", "human-1"), ("C-2", "human-2")):
            approvals = core.list_candidate_approvals(candidate)
            self.assertEqual(len(approvals), 1)
            self.assertEqual(approvals[0]["approver"], approver)
            self.assertIsNone(approvals[0]["batch_id"])

        Coordinator(self.db)
        self.assertEqual(self.approval_count(), 2)

    def test_half_migrated_copy_failure_blocks_then_recovers(self):
        self.seed_legacy([
            ("CA-1", "C-1", "human-1", "reviewer", "keep", 1.0),
        ])
        self.make_half_migrated([])
        with self.assertRaises(sqlite3.DatabaseError):
            _InterruptApprovalCopy(self.db)
        # 失败后 legacy 保留，可重试
        self.assertIn("candidate_approvals_legacy", self.tables())
        self.assertEqual(self.approval_count(), 0)

        core = Coordinator(self.db)
        self.assertNotIn("candidate_approvals_legacy", self.tables())
        approvals = core.list_candidate_approvals("C-1")
        self.assertEqual(len(approvals), 1)
        self.assertIsNone(approvals[0]["batch_id"])

    def test_ambiguous_half_migration_blocks_without_data_loss(self):
        self.seed_legacy([
            ("CA-1", "C-1", "human-1", "reviewer", "keep", 1.0),
        ])
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute(
                "ALTER TABLE candidate_approvals RENAME TO candidate_approvals_legacy"
            )
            conn.executescript(OLD_APPROVALS_DDL)
            conn.commit()

        with self.assertRaisesRegex(CoordinatorError, "ambiguous"):
            Coordinator(self.db)
        self.assertIn("candidate_approvals_legacy", self.tables())
        self.assertEqual(self.approval_count(), 0)
        with closing(sqlite3.connect(self.db)) as conn:
            legacy_count = conn.execute(
                "SELECT COUNT(*) FROM candidate_approvals_legacy"
            ).fetchone()[0]
        self.assertEqual(legacy_count, 1)


def _import_mcp_server():
    """导入 mcp_server；本机没装 PuLP 时用空壳替代（与门禁无关的可选依赖）。"""
    try:
        import mcp_server
        return mcp_server
    except ModuleNotFoundError as exc:
        if exc.name != "pulp":
            raise
        sys.modules["pulp"] = types.ModuleType("pulp")
        import mcp_server
        return mcp_server


if __name__ == "__main__":
    unittest.main(verbosity=2)
