"""P0 协调器：SQLite + 任务/租约/事件。

核心保证：
- claim 是原子的（BEGIN IMMEDIATE）；
- 租约有 TTL，过期可回收；
- 提交必须带正确的 fencing token，旧租约写不进来；
- 任务有写集（write_set）和依赖（deps），claim 时检查冲突/依赖；
- 所有状态变化写事件表，便于审计。

只用标准库，方便在 DSH 沙箱/普通 Python 里直接跑。
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import semantic

DEFAULT_TTL = 120.0
APPROVER_ROLE_RANK = {"reviewer": 1, "admin": 2}
SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    require_approval INTEGER NOT NULL DEFAULT 0,
    require_verification INTEGER NOT NULL DEFAULT 0,
    approvals_required INTEGER NOT NULL DEFAULT 1,
    approval_ttl_seconds REAL NOT NULL DEFAULT 86400,
    required_approver_role TEXT NOT NULL DEFAULT 'reviewer',
    approval_chain TEXT NOT NULL DEFAULT '',
    budget_usd REAL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'code',
    status TEXT NOT NULL DEFAULT 'ready',
    lifecycle TEXT NOT NULL DEFAULT 'ready',
    owner TEXT,
    repo_id TEXT NOT NULL DEFAULT '',
    write_set TEXT NOT NULL DEFAULT '[]',
    deps TEXT NOT NULL DEFAULT '[]',
    dod TEXT NOT NULL DEFAULT '',
    checks TEXT NOT NULL DEFAULT '[]',
    priority INTEGER NOT NULL DEFAULT 0,
    plan_id TEXT,
    planned_worker TEXT,
    planned_start_s REAL,
    planned_end_s REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS leases (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    status TEXT NOT NULL,
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL,
    renewed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    execution_id TEXT,
    worker TEXT NOT NULL,
    fencing_token INTEGER,
    duration_s REAL NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    quality REAL,
    diff_lines INTEGER NOT NULL DEFAULT 0,
    files_changed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    meta TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'author',
    workspace TEXT NOT NULL DEFAULT '',
    session TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    generation INTEGER NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    exit_reason TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS file_reservations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    repo_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    execution_id TEXT,
    path TEXT NOT NULL,
    path_key TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'file',
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    released_at REAL,
    release_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    repo_id TEXT NOT NULL,
    author_execution_id TEXT,
    base_sha TEXT NOT NULL,
    candidate_sha TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'building',
    created_at REAL NOT NULL,
    frozen_at REAL,
    approved_by TEXT,
    approver_role TEXT NOT NULL DEFAULT 'reviewer',
    approved_at REAL,
    merged_at REAL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS candidate_approvals (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    approver TEXT NOT NULL,
    role TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    UNIQUE(candidate_id, role)
);
CREATE TABLE IF NOT EXISTS semantic_changes (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    path TEXT NOT NULL,
    object TEXT NOT NULL,
    change_type TEXT NOT NULL,
    compatibility TEXT NOT NULL,
    before_value TEXT NOT NULL DEFAULT '',
    after_value TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS semantic_acks (
    change_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    acked_at REAL NOT NULL,
    PRIMARY KEY(change_id, task_id)
);
CREATE TABLE IF NOT EXISTS semantic_dependencies (
    task_id TEXT NOT NULL,
    object TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(task_id, object)
);
CREATE TABLE IF NOT EXISTS agent_capabilities (
    agent_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    source TEXT NOT NULL DEFAULT 'manual',
    sample_size INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY(agent_id, dimension, value)
);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    execution_id TEXT,
    type TEXT NOT NULL,
    command TEXT NOT NULL DEFAULT '',
    cwd TEXT NOT NULL DEFAULT '',
    exit_code INTEGER,
    output_ref TEXT NOT NULL DEFAULT '',
    sha TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS merge_attempts (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    expected_base_sha TEXT NOT NULL,
    requested_by TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    result_sha TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    execution_id TEXT,
    repo_id TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    path TEXT NOT NULL,
    branch TEXT NOT NULL DEFAULT '',
    base_sha TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    removed_at REAL
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    requested_by TEXT,
    requested_at REAL NOT NULL,
    status TEXT NOT NULL,
    decided_by TEXT,
    decided_at REAL,
    note TEXT NOT NULL DEFAULT '',
    expires_at REAL
);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    mode TEXT NOT NULL,
    quality_floor REAL NOT NULL,
    makespan_s REAL,
    total_cost_usd REAL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    worker TEXT NOT NULL,
    plan_id TEXT,
    status TEXT NOT NULL,
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS verifications (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    verifier TEXT NOT NULL,
    passed INTEGER NOT NULL,
    score REAL,
    notes TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    task_id TEXT,
    actor TEXT,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id, status);
CREATE INDEX IF NOT EXISTS idx_leases_task ON leases(task_id, status);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, id);
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id);
CREATE INDEX IF NOT EXISTS idx_runs_worker ON runs(worker);
CREATE INDEX IF NOT EXISTS idx_runs_execution ON runs(execution_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_exec_one_author_active
    ON executions(task_id) WHERE role='author' AND status='running';
CREATE INDEX IF NOT EXISTS idx_exec_task ON executions(task_id, generation);
CREATE INDEX IF NOT EXISTS idx_file_res_repo ON file_reservations(repo_id, path_key, status);
CREATE INDEX IF NOT EXISTS idx_file_res_task ON file_reservations(task_id, status);
CREATE INDEX IF NOT EXISTS idx_candidates_task ON candidates(task_id, status);
CREATE INDEX IF NOT EXISTS idx_evidence_candidate ON evidence(candidate_id, created_at);
CREATE INDEX IF NOT EXISTS idx_candidate_approvals ON candidate_approvals(candidate_id, created_at);
CREATE INDEX IF NOT EXISTS idx_semantic_candidate ON semantic_changes(candidate_id, created_at);
CREATE INDEX IF NOT EXISTS idx_semantic_object ON semantic_changes(object, created_at);
CREATE INDEX IF NOT EXISTS idx_semantic_dep ON semantic_dependencies(object);
CREATE INDEX IF NOT EXISTS idx_agent_capability ON agent_capabilities(agent_id, dimension);
CREATE INDEX IF NOT EXISTS idx_merge_candidate ON merge_attempts(candidate_id, status);
CREATE INDEX IF NOT EXISTS idx_workspaces_task ON workspaces(task_id, status);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, requested_at);
CREATE INDEX IF NOT EXISTS idx_plans_project ON plans(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_reservations_task ON reservations(task_id, status);
CREATE INDEX IF NOT EXISTS idx_verifications_task ON verifications(task_id, created_at);
INSERT OR IGNORE INTO counters(name, value) VALUES ('fencing', 0);
"""


class CoordinatorError(RuntimeError):
    pass


class LeaseError(CoordinatorError):
    pass


def _now() -> float:
    return time.time()


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _json_load(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


class Coordinator:
    LIFECYCLE_TRANSITIONS = {
        "ready": {"assigned", "in_progress", "cancelled", "blocked"},
        "assigned": {"in_progress", "ready", "cancelled"},
        "in_progress": {"auto_review", "needs_fix", "cancelled"},
        "auto_review": {"human_review", "needs_fix", "in_progress", "cancelled"},
        "human_review": {"ready_to_merge", "needs_fix", "in_progress", "cancelled"},
        "ready_to_merge": {"accepted", "needs_fix", "in_progress"},
        "needs_fix": {"in_progress", "ready", "cancelled"},
        "blocked": {"ready", "cancelled"},
        "accepted": set(),
        "cancelled": set(),
    }

    def __init__(self, db_path: str | Path, default_ttl: float = DEFAULT_TTL) -> None:
        self.db_path = str(db_path)
        self.default_ttl = float(default_ttl)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
            if "kind" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'code'")
            for col, ddl in (
                ("plan_id", "TEXT"),
                ("planned_worker", "TEXT"),
                ("planned_start_s", "REAL"),
                ("planned_end_s", "REAL"),
                ("checks", "TEXT NOT NULL DEFAULT '[]'"),
            ):
                if col not in cols:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {ddl}")
            rcols = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
            if "execution_id" not in rcols:
                conn.execute("ALTER TABLE runs ADD COLUMN execution_id TEXT")
            if "lifecycle" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'ready'")
            if "repo_id" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN repo_id TEXT NOT NULL DEFAULT ''")
            pcols = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
            if "require_approval" not in pcols:
                conn.execute(
                    "ALTER TABLE projects ADD COLUMN require_approval INTEGER NOT NULL DEFAULT 0"
                )
            if "require_verification" not in pcols:
                conn.execute(
                    "ALTER TABLE projects ADD COLUMN require_verification INTEGER NOT NULL DEFAULT 0"
                )
            if "approvals_required" not in pcols:
                conn.execute(
                    "ALTER TABLE projects ADD COLUMN approvals_required INTEGER NOT NULL DEFAULT 1"
                )
            if "approval_ttl_seconds" not in pcols:
                conn.execute(
                    "ALTER TABLE projects ADD COLUMN approval_ttl_seconds REAL NOT NULL DEFAULT 86400"
                )
            if "budget_usd" not in pcols:
                conn.execute("ALTER TABLE projects ADD COLUMN budget_usd REAL")
            if "required_approver_role" not in pcols:
                conn.execute(
                    "ALTER TABLE projects ADD COLUMN required_approver_role TEXT NOT NULL DEFAULT 'reviewer'"
                )
            if "approval_chain" not in pcols:
                conn.execute(
                    "ALTER TABLE projects ADD COLUMN approval_chain TEXT NOT NULL DEFAULT ''"
                )
            acols = {row["name"] for row in conn.execute("PRAGMA table_info(approvals)")}
            if "expires_at" not in acols:
                conn.execute("ALTER TABLE approvals ADD COLUMN expires_at REAL")
            ccols = {row["name"] for row in conn.execute("PRAGMA table_info(candidates)")}
            if "approver_role" not in ccols:
                conn.execute(
                    "ALTER TABLE candidates ADD COLUMN approver_role TEXT NOT NULL DEFAULT 'reviewer'"
                )

    # ---------- 基础设施 ----------
    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tx(self):
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    @staticmethod
    def _task(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "title": row["title"],
            "description": row["description"],
            "kind": row["kind"],
            "status": row["status"],
            "lifecycle": row["lifecycle"],
            "owner": row["owner"],
            "repo_id": row["repo_id"],
            "write_set": _json_load(row["write_set"], []),
            "deps": _json_load(row["deps"], []),
            "dod": row["dod"],
            "checks": _json_load(row["checks"], []),
            "priority": row["priority"],
            "plan_id": row["plan_id"],
            "planned_worker": row["planned_worker"],
            "planned_start_s": row["planned_start_s"],
            "planned_end_s": row["planned_end_s"],
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }

    def _transition_in_tx(
        self,
        conn: sqlite3.Connection,
        task_id: str,
        to_state: str,
        reason: str = "",
    ) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise CoordinatorError(f"unknown task: {task_id}")
        current = row["lifecycle"]
        if current == to_state:
            return row
        allowed = self.LIFECYCLE_TRANSITIONS.get(current)
        if allowed is None:
            raise CoordinatorError(f"unknown lifecycle state: {current}")
        if to_state not in allowed:
            raise CoordinatorError(
                f"invalid lifecycle transition: {current} -> {to_state}"
            )
        now = _now()
        if to_state == "accepted":
            conn.execute(
                "UPDATE tasks SET lifecycle=?, status='done', updated_at=? WHERE id=?",
                (to_state, now, task_id),
            )
        else:
            conn.execute(
                "UPDATE tasks SET lifecycle=?, updated_at=? WHERE id=?",
                (to_state, now, task_id),
            )
        self._event(conn, "task_state_changed", task_id, "system",
                    {"from": current, "to": to_state, "reason": reason})
        return conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    def transition_task(self, task_id: str, to_state: str, reason: str = "") -> dict:
        with self._tx() as conn:
            row = self._transition_in_tx(conn, task_id, to_state, reason)
            return self._task(row)

    @staticmethod
    def _lease(row: sqlite3.Row) -> dict:
        return {
            "lease_id": row["id"],
            "task_id": row["task_id"],
            "owner": row["owner"],
            "fencing_token": row["fencing_token"],
            "status": row["status"],
            "expires_at": _iso(row["expires_at"]),
            "expires_in": round(row["expires_at"] - _now(), 1),
        }

    @staticmethod
    def _run(row: sqlite3.Row) -> dict:
        return {
            "run_id": row["id"],
            "task_id": row["task_id"],
            "execution_id": row["execution_id"],
            "worker": row["worker"],
            "fencing_token": row["fencing_token"],
            "duration_s": row["duration_s"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "cost_usd": row["cost_usd"],
            "quality": row["quality"],
            "diff_lines": row["diff_lines"],
            "files_changed": row["files_changed"],
            "status": row["status"],
            "meta": _json_load(row["meta"], {}),
            "created_at": _iso(row["created_at"]),
        }

    @staticmethod
    def _reservation(row: sqlite3.Row) -> dict:
        return {
            "reservation_id": row["id"],
            "task_id": row["task_id"],
            "worker": row["worker"],
            "plan_id": row["plan_id"],
            "status": row["status"],
            "expires_at": _iso(row["expires_at"]),
            "expires_in": round(row["expires_at"] - _now(), 1),
        }

    @staticmethod
    def _event(conn: sqlite3.Connection, type_: str, task_id: str | None, actor: str | None, payload: dict | None = None) -> None:
        conn.execute(
            "INSERT INTO events(ts, type, task_id, actor, payload) VALUES (?,?,?,?,?)",
            (_now(), type_, task_id, actor, json.dumps(payload or {}, ensure_ascii=False)),
        )

    @staticmethod
    def _insert_approvals(
        conn: sqlite3.Connection,
        task_id: str,
        requested_by: str | None,
        count: int,
        now: float,
        expires_at: float | None = None,
    ) -> list[str]:
        ids = []
        for _ in range(max(1, int(count))):
            approval_id = "A-" + uuid.uuid4().hex[:8]
            conn.execute(
                "INSERT INTO approvals(id, task_id, requested_by, requested_at, status, note, expires_at) "
                "VALUES (?,?,?,?, 'pending', '', ?)",
                (approval_id, task_id, requested_by, now, expires_at),
            )
            ids.append(approval_id)
        return ids

    @staticmethod
    def _insert_run(
        conn: sqlite3.Connection,
        task_id: str,
        worker: str,
        fencing_token: int | None,
        run: dict,
    ) -> int:
        now = _now()
        duration = max(0.0, float(run.get("duration_s", 0) or 0))
        input_tokens = max(0, int(run.get("input_tokens", 0) or 0))
        output_tokens = max(0, int(run.get("output_tokens", 0) or 0))
        cost = max(0.0, float(run.get("cost_usd", 0) or 0))
        quality = run.get("quality")
        quality = None if quality is None else max(0.0, min(1.0, float(quality)))
        diff_lines = max(0, int(run.get("diff_lines", 0) or 0))
        files_changed = max(0, int(run.get("files_changed", 0) or 0))
        status = str(run.get("status", "ok"))
        meta = json.dumps(run.get("meta", {}) or {}, ensure_ascii=False)
        cur = conn.execute(
            "INSERT INTO runs(task_id, execution_id, worker, fencing_token, duration_s, input_tokens, output_tokens, "
            "cost_usd, quality, diff_lines, files_changed, status, meta, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, run.get("execution_id"), worker, fencing_token, duration, input_tokens, output_tokens,
             cost, quality, diff_lines, files_changed, status, meta, now),
        )
        run_id = int(cur.lastrowid)
        Coordinator._event(
            conn, "run_recorded", task_id, worker,
            {"run_id": run_id, "cost_usd": cost, "quality": quality,
             "input_tokens": input_tokens, "output_tokens": output_tokens},
        )
        return run_id

    def _reap(self, conn: sqlite3.Connection, now: float) -> int:
        rows = conn.execute(
            "SELECT * FROM leases WHERE status='active' AND expires_at <= ?", (now,)
        ).fetchall()
        for lease in rows:
            conn.execute("UPDATE leases SET status='expired' WHERE id=?", (lease["id"],))
            conn.execute(
                "UPDATE tasks SET status='ready', owner=NULL, updated_at=? "
                "WHERE id=? AND status='claimed' AND owner=?",
                (now, lease["task_id"], lease["owner"]),
            )
            self._event(
                conn,
                "lease_expired",
                lease["task_id"],
                lease["owner"],
                {"lease_id": lease["id"], "fencing_token": lease["fencing_token"]},
            )
        reservations = conn.execute(
            "SELECT * FROM reservations WHERE status='active' AND expires_at <= ?", (now,)
        ).fetchall()
        for res in reservations:
            conn.execute("UPDATE reservations SET status='expired' WHERE id=?", (res["id"],))
            self._event(conn, "reservation_expired", res["task_id"], res["worker"],
                        {"reservation_id": res["id"], "plan_id": res["plan_id"]})
        approvals = conn.execute(
            "SELECT * FROM approvals WHERE status='pending' AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        ).fetchall()
        for approval in approvals:
            conn.execute("UPDATE approvals SET status='expired' WHERE id=?", (approval["id"],))
            self._event(conn, "approval_expired", approval["task_id"], approval["requested_by"],
                        {"approval_id": approval["id"]})
        return len(rows)

    def reap_expired(self) -> int:
        with self._tx() as conn:
            return self._reap(conn, _now())

    # ---------- 项目/任务 ----------
    def create_project(
        self,
        name: str,
        require_approval: bool = False,
        require_verification: bool = False,
        approvals_required: int = 1,
        approval_ttl_seconds: float = 86400.0,
        budget_usd: float | None = None,
        required_approver_role: str = "reviewer",
        approval_chain: str = "",
    ) -> dict:
        if required_approver_role not in ("reviewer", "admin"):
            raise CoordinatorError(f"invalid required approver role: {required_approver_role}")
        chain = (approval_chain or "").strip()
        chain_roles = [r.strip() for r in chain.split(",") if r.strip()] if chain else []
        for item in chain_roles:
            if item not in APPROVER_ROLE_RANK:
                raise CoordinatorError(f"invalid approval chain role: {item}")
        pid = "P-" + uuid.uuid4().hex[:8]
        now = _now()
        approvals_required = max(1, int(approvals_required))
        approval_ttl_seconds = max(0.001, float(approval_ttl_seconds))
        budget = None if budget_usd is None else max(0.0, float(budget_usd))
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO projects(id, name, require_approval, require_verification, approvals_required, approval_ttl_seconds, required_approver_role, approval_chain, budget_usd, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pid, name, 1 if require_approval else 0, 1 if require_verification else 0,
                 approvals_required, approval_ttl_seconds, required_approver_role, chain, budget, now),
            )
            self._event(conn, "project_created", None, "system",
                        {"project_id": pid, "name": name,
                         "require_approval": bool(require_approval),
                         "require_verification": bool(require_verification),
                         "approvals_required": approvals_required,
                         "approval_ttl_seconds": approval_ttl_seconds,
                         "required_approver_role": required_approver_role,
                         "approval_chain": chain_roles,
                         "budget_usd": budget})
        return {
            "project_id": pid,
            "name": name,
            "require_approval": bool(require_approval),
            "require_verification": bool(require_verification),
            "approvals_required": approvals_required,
            "approval_ttl_seconds": approval_ttl_seconds,
            "required_approver_role": required_approver_role,
            "approval_chain": chain_roles,
            "budget_usd": budget,
        }

    def list_projects(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY created_at").fetchall()
        return [
            {
                "project_id": r["id"],
                "name": r["name"],
                "require_approval": bool(r["require_approval"]),
                "require_verification": bool(r["require_verification"]),
                "approvals_required": r["approvals_required"],
                "approval_ttl_seconds": r["approval_ttl_seconds"],
                "required_approver_role": r["required_approver_role"],
                "approval_chain": [
                    x.strip() for x in (r["approval_chain"] or "").split(",") if x.strip()
                ],
                "budget_usd": r["budget_usd"],
                "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ]

    def project_budget(self, project_id: str) -> dict:
        with self._conn() as conn:
            project = conn.execute(
                "SELECT budget_usd FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if project is None:
                raise CoordinatorError(f"unknown project: {project_id}")
            spent = conn.execute(
                "SELECT COALESCE(SUM(r.cost_usd), 0) AS s FROM runs r "
                "JOIN tasks t ON t.id=r.task_id WHERE t.project_id=?",
                (project_id,),
            ).fetchone()["s"]
        budget = project["budget_usd"]
        remaining = None if budget is None else max(0.0, float(budget) - float(spent))
        return {
            "project_id": project_id,
            "budget_usd": budget,
            "spent_usd": round(float(spent), 4),
            "remaining_usd": None if remaining is None else round(remaining, 4),
            "exceeded": budget is not None and float(spent) >= float(budget),
        }

    def _budget_exceeded(self, conn: sqlite3.Connection, project_id: str) -> bool:
        project = conn.execute(
            "SELECT budget_usd FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if project is None or project["budget_usd"] is None:
            return False
        spent = conn.execute(
            "SELECT COALESCE(SUM(r.cost_usd), 0) AS s FROM runs r "
            "JOIN tasks t ON t.id=r.task_id WHERE t.project_id=?",
            (project_id,),
        ).fetchone()["s"]
        return float(spent) >= float(project["budget_usd"])

    def _write_set_file_conflicts(self, conn: sqlite3.Connection, task: dict) -> list[dict]:
        repo_id = task.get("repo_id") or task["project_id"]
        keys = []
        for path in task.get("write_set") or []:
            try:
                _, key = self._normalize_repo_path(path)
            except CoordinatorError:
                continue
            keys.append((path, key))
        if not keys:
            return []
        rows = conn.execute(
            "SELECT fr.*, t.title FROM file_reservations fr JOIN tasks t ON t.id=fr.task_id "
            "WHERE fr.repo_id=? AND fr.status='active' AND fr.task_id != ?",
            (repo_id, task["id"]),
        ).fetchall()

        def conflicts(a: str, b: str) -> bool:
            return a == b or a.startswith(b + "/") or b.startswith(a + "/")

        out = []
        for raw, key in keys:
            for row in rows:
                if conflicts(row["path_key"], key):
                    out.append({"path": raw, "with_task": row["task_id"],
                                "with_title": row["title"], "with_path": row["path"]})
        return out

    def create_task(
        self,
        project_id: str,
        title: str,
        write_set: Iterable[str] | None = None,
        deps: Iterable[str] | None = None,
        dod: str = "",
        description: str = "",
        priority: int = 0,
        kind: str = "code",
        checks: Iterable[dict] | None = None,
        repo_id: str = "",
    ) -> dict:
        tid = "T-" + uuid.uuid4().hex[:8]
        now = _now()
        ws = list(dict.fromkeys(write_set or []))
        dp = list(dict.fromkeys(deps or []))
        ck = list(checks or [])
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise CoordinatorError(f"unknown project: {project_id}")
            for dep in dp:
                if conn.execute("SELECT 1 FROM tasks WHERE id=?", (dep,)).fetchone() is None:
                    raise CoordinatorError(f"unknown dependency: {dep}")
            conn.execute(
                "INSERT INTO tasks(id, project_id, title, description, kind, status, repo_id, write_set, deps, dod, checks, priority, created_at, updated_at) "
                "VALUES (?,?,?,?,?, 'ready', ?,?,?,?,?,?,?,?)",
                (tid, project_id, title, description, kind, repo_id, json.dumps(ws, ensure_ascii=False),
                 json.dumps(dp, ensure_ascii=False), dod, json.dumps(ck, ensure_ascii=False),
                 int(priority), now, now),
            )
            self._event(conn, "task_created", tid, "system",
                        {"project_id": project_id, "title": title, "kind": kind,
                         "write_set": ws, "deps": dp, "checks": len(ck), "repo_id": repo_id})
        return self.get_task(tid)

    def get_task(self, task_id: str) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise CoordinatorError(f"unknown task: {task_id}")
        return self._task(row)

    def list_tasks(self, project_id: str | None = None, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM tasks WHERE 1=1"
        params: list[Any] = []
        if project_id:
            sql += " AND project_id=?"
            params.append(project_id)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY priority DESC, created_at"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._task(r) for r in rows]

    # ---------- 依赖/冲突 ----------
    def _deps_done(self, conn: sqlite3.Connection, deps: list[str]) -> bool:
        if not deps:
            return True
        placeholders = ",".join("?" for _ in deps)
        rows = conn.execute(
            f"SELECT id, status FROM tasks WHERE id IN ({placeholders})", deps
        ).fetchall()
        return len(rows) == len(deps) and all(r["status"] == "done" for r in rows)

    def _write_conflict(self, conn: sqlite3.Connection, write_set: list[str], now: float) -> str | None:
        if not write_set:
            return None
        rows = conn.execute(
            "SELECT t.id, t.write_set FROM leases l JOIN tasks t ON t.id=l.task_id "
            "WHERE l.status='active' AND l.expires_at > ?",
            (now,),
        ).fetchall()
        wanted = set(write_set)
        for row in rows:
            other = set(_json_load(row["write_set"], []))
            if wanted & other:
                return row["id"]
        return None

    # ---------- 租约 ----------
    def claim_task(
        self,
        worker: str,
        project_id: str | None = None,
        ttl_seconds: float | None = None,
        task_id: str | None = None,
    ) -> dict:
        ttl = float(ttl_seconds if ttl_seconds is not None else self.default_ttl)
        if ttl <= 0:
            raise CoordinatorError("ttl_seconds must be > 0")
        now = _now()
        with self._tx() as conn:
            reaped = self._reap(conn, now)
            if task_id:
                rows = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchall()
            else:
                sql = "SELECT * FROM tasks WHERE status='ready'"
                params: list[Any] = []
                if project_id:
                    sql += " AND project_id=?"
                    params.append(project_id)
                sql += " ORDER BY priority DESC, created_at"
                rows = conn.execute(sql, params).fetchall()

            conflict_reason = None
            deps_reason = False
            reserved_reason = None
            file_reserved_reason = None
            budget_reason = False
            budget_cache: dict[str, bool] = {}
            for row in rows:
                task = self._task(row)
                reservation = conn.execute(
                    "SELECT worker FROM reservations WHERE task_id=? AND status='active' "
                    "AND expires_at > ? ORDER BY created_at DESC LIMIT 1",
                    (task["id"], now),
                ).fetchone()
                if reservation is not None and reservation["worker"] != worker:
                    reserved_reason = reservation["worker"]
                    continue
                file_conflicts = self._write_set_file_conflicts(conn, task)
                if file_conflicts:
                    file_reserved_reason = file_conflicts[0]["with_task"]
                    continue
                pid = task["project_id"]
                if pid not in budget_cache:
                    budget_cache[pid] = self._budget_exceeded(conn, pid)
                if budget_cache[pid]:
                    budget_reason = True
                    continue
                if not self._deps_done(conn, task["deps"]):
                    deps_reason = True
                    continue
                conflict = self._write_conflict(conn, task["write_set"], now)
                if conflict:
                    conflict_reason = conflict
                    continue
                conn.execute(
                    "UPDATE counters SET value=value+1 WHERE name='fencing'"
                )
                token = conn.execute("SELECT value FROM counters WHERE name='fencing'").fetchone()["value"]
                lease_id = "L-" + uuid.uuid4().hex[:8]
                expires = now + ttl
                conn.execute(
                    "INSERT INTO leases(id, task_id, owner, fencing_token, status, expires_at, created_at, renewed_at) "
                    "VALUES (?,?,?,?, 'active', ?,?,?)",
                    (lease_id, task["id"], worker, token, expires, now, now),
                )
                conn.execute(
                    "UPDATE tasks SET status='claimed', owner=?, updated_at=? WHERE id=?",
                    (worker, now, task["id"]),
                )
                conn.execute(
                    "UPDATE reservations SET status='consumed' WHERE task_id=? AND worker=? AND status='active'",
                    (task["id"], worker),
                )
                self._event(conn, "task_claimed", task["id"], worker,
                            {"lease_id": lease_id, "fencing_token": token, "ttl_seconds": ttl})
                lease = conn.execute("SELECT * FROM leases WHERE id=?", (lease_id,)).fetchone()
                return {
                    "claimed": True,
                    "task": self._task(conn.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone()),
                    "lease": self._lease(lease),
                    "reaped": reaped,
                }

            reason = "no_ready_task"
            if reserved_reason:
                reason = f"reserved_for:{reserved_reason}"
            elif file_reserved_reason:
                reason = f"file_reserved_by:{file_reserved_reason}"
            elif budget_reason:
                reason = "budget_exceeded"
            elif conflict_reason:
                reason = f"write_conflict_with:{conflict_reason}"
            elif deps_reason:
                reason = "deps_blocked"
            return {"claimed": False, "reason": reason, "reaped": reaped}

    # ---------- 计划回写 ----------
    def apply_plan(self, plan: dict, reserve_ttl: float = 3600.0) -> dict:
        if not plan.get("feasible"):
            raise CoordinatorError("cannot apply an infeasible plan")
        project_id = plan.get("project_id")
        assignment = plan.get("assignment") or []
        if not project_id or not assignment:
            raise CoordinatorError("plan missing project_id/assignment")
        if reserve_ttl <= 0:
            raise CoordinatorError("reserve_ttl must be > 0")
        plan_id = "PL-" + uuid.uuid4().hex[:8]
        now = _now()
        with self._tx() as conn:
            conn.execute(
                "UPDATE reservations SET status='released' WHERE status='active' "
                "AND task_id IN (SELECT id FROM tasks WHERE project_id=?)",
                (project_id,),
            )
            conn.execute(
                "INSERT INTO plans(id, project_id, created_at, mode, quality_floor, makespan_s, total_cost_usd, payload) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    plan_id,
                    project_id,
                    now,
                    str(plan.get("mode", "cost")),
                    float(plan.get("quality_floor", 0.0)),
                    plan.get("pred_makespan_s"),
                    plan.get("pred_total_cost_usd"),
                    json.dumps(plan, ensure_ascii=False),
                ),
            )
            for item in assignment:
                cur = conn.execute(
                    "UPDATE tasks SET plan_id=?, planned_worker=?, planned_start_s=?, planned_end_s=?, updated_at=? "
                    "WHERE id=? AND project_id=?",
                    (
                        plan_id,
                        item.get("worker"),
                        item.get("start_s"),
                        item.get("end_s"),
                        now,
                        item.get("task_id"),
                        project_id,
                    ),
                )
                if cur.rowcount == 0:
                    raise CoordinatorError(f"task not found in project: {item.get('task_id')}")
                conn.execute(
                    "INSERT INTO reservations(id, task_id, worker, plan_id, status, expires_at, created_at) "
                    "VALUES (?,?,?,?, 'active', ?,?)",
                    (
                        "R-" + uuid.uuid4().hex[:8],
                        item.get("task_id"),
                        item.get("worker"),
                        plan_id,
                        now + float(reserve_ttl),
                        now,
                    ),
                )
            self._event(conn, "plan_applied", None, "system",
                        {"plan_id": plan_id, "project_id": project_id,
                         "items": len(assignment), "reservations": len(assignment)})
        return {
            "plan_id": plan_id,
            "project_id": project_id,
            "items": len(assignment),
            "reservations": len(assignment),
            "mode": plan.get("mode"),
            "quality_floor": plan.get("quality_floor"),
        }

    def get_plan(self, project_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM plans WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "plan_id": row["id"],
            "project_id": row["project_id"],
            "created_at": _iso(row["created_at"]),
            "mode": row["mode"],
            "quality_floor": row["quality_floor"],
            "makespan_s": row["makespan_s"],
            "total_cost_usd": row["total_cost_usd"],
            "plan": _json_load(row["payload"], {}),
        }

    def clear_plan(self, project_id: str) -> int:
        now = _now()
        with self._tx() as conn:
            conn.execute(
                "UPDATE reservations SET status='released' WHERE status='active' "
                "AND task_id IN (SELECT id FROM tasks WHERE project_id=?)",
                (project_id,),
            )
            cur = conn.execute(
                "UPDATE tasks SET plan_id=NULL, planned_worker=NULL, planned_start_s=NULL, planned_end_s=NULL, updated_at=? "
                "WHERE project_id=?",
                (now, project_id),
            )
            self._event(conn, "plan_cleared", None, "system",
                        {"project_id": project_id, "cleared": cur.rowcount})
            return cur.rowcount

    def list_reservations(self, status: str | None = None, project_id: str | None = None) -> list[dict]:
        sql = (
            "SELECT r.*, t.title AS task_title, t.project_id AS project_id "
            "FROM reservations r JOIN tasks t ON t.id=r.task_id WHERE 1=1"
        )
        params: list[Any] = []
        if status:
            sql += " AND r.status=?"
            params.append(status)
        if project_id:
            sql += " AND t.project_id=?"
            params.append(project_id)
        sql += " ORDER BY r.created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "reservation_id": r["id"],
                "task_id": r["task_id"],
                "task_title": r["task_title"],
                "project_id": r["project_id"],
                "worker": r["worker"],
                "plan_id": r["plan_id"],
                "status": r["status"],
                "expires_at": _iso(r["expires_at"]),
                "expires_in": round(r["expires_at"] - _now(), 1),
            }
            for r in rows
        ]

    def renew_reservation(self, task_id: str, worker: str, ttl_seconds: float = 3600.0) -> dict:
        if ttl_seconds <= 0:
            raise CoordinatorError("ttl_seconds must be > 0")
        now = _now()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM reservations WHERE task_id=? AND worker=? AND status='active' "
                "ORDER BY created_at DESC LIMIT 1",
                (task_id, worker),
            ).fetchone()
            if row is None:
                raise CoordinatorError("no active reservation for task/worker")
            if row["expires_at"] <= now:
                conn.execute("UPDATE reservations SET status='expired' WHERE id=?", (row["id"],))
                raise CoordinatorError("reservation already expired")
            expires = now + float(ttl_seconds)
            conn.execute("UPDATE reservations SET expires_at=? WHERE id=?", (expires, row["id"]))
            self._event(conn, "reservation_renewed", task_id, worker,
                        {"reservation_id": row["id"], "ttl_seconds": ttl_seconds})
            updated = conn.execute("SELECT * FROM reservations WHERE id=?", (row["id"],)).fetchone()
            return self._reservation(updated)

    def claim_planned(
        self,
        worker: str,
        project_id: str | None = None,
        ttl_seconds: float | None = None,
    ) -> dict:
        sql = (
            "SELECT id FROM tasks WHERE planned_worker=? AND status='ready'"
        )
        params: list[Any] = [worker]
        if project_id:
            sql += " AND project_id=?"
            params.append(project_id)
        sql += " ORDER BY (planned_start_s IS NULL), planned_start_s, priority DESC LIMIT 1"
        with self._conn() as conn:
            row = conn.execute(sql, params).fetchone()
        if row is None:
            return {"claimed": False, "reason": "no_planned_task"}
        return self.claim_task(worker, task_id=row["id"], ttl_seconds=ttl_seconds)

    def _lease_for(self, conn: sqlite3.Connection, task_id: str, worker: str, token: int, now: float) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM leases WHERE task_id=? AND owner=? AND fencing_token=? AND status='active'",
            (task_id, worker, token),
        ).fetchone()
        if row is None:
            raise LeaseError("lease not found or not active")
        if row["expires_at"] <= now:
            raise LeaseError("lease expired")
        return row

    def renew_lease(self, task_id: str, worker: str, fencing_token: int, ttl_seconds: float | None = None) -> dict:
        ttl = float(ttl_seconds if ttl_seconds is not None else self.default_ttl)
        if ttl <= 0:
            raise CoordinatorError("ttl_seconds must be > 0")
        now = _now()
        with self._tx() as conn:
            lease = self._lease_for(conn, task_id, worker, int(fencing_token), now)
            expires = now + ttl
            conn.execute(
                "UPDATE leases SET expires_at=?, renewed_at=? WHERE id=?",
                (expires, now, lease["id"]),
            )
            self._event(conn, "lease_renewed", task_id, worker,
                        {"lease_id": lease["id"], "fencing_token": lease["fencing_token"], "ttl_seconds": ttl})
            updated = conn.execute("SELECT * FROM leases WHERE id=?", (lease["id"],)).fetchone()
            return self._lease(updated)

    def release_lease(self, task_id: str, worker: str, fencing_token: int, reason: str = "") -> dict:
        now = _now()
        with self._tx() as conn:
            lease = self._lease_for(conn, task_id, worker, int(fencing_token), now)
            conn.execute("UPDATE leases SET status='released' WHERE id=?", (lease["id"],))
            conn.execute(
                "UPDATE tasks SET status='ready', owner=NULL, updated_at=? WHERE id=? AND status='claimed'",
                (now, task_id),
            )
            self._event(conn, "lease_released", task_id, worker,
                        {"lease_id": lease["id"], "fencing_token": lease["fencing_token"], "reason": reason})
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return {"released": True, "task": self._task(row)}

    def commit_task(
        self,
        task_id: str,
        worker: str,
        fencing_token: int,
        result: dict | None = None,
        run: dict | None = None,
        require_approval: bool | None = None,
    ) -> dict:
        now = _now()
        with self._tx() as conn:
            self._reap(conn, now)
            lease = self._lease_for(conn, task_id, worker, int(fencing_token), now)
            task_row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            project_row = conn.execute(
                "SELECT require_approval, require_verification, approvals_required, approval_ttl_seconds "
                "FROM projects WHERE id=?",
                (task_row["project_id"],),
            ).fetchone()
            need_approval = (
                bool(project_row["require_approval"])
                if require_approval is None
                else bool(require_approval)
            )
            need_verification = bool(project_row["require_verification"])
            conn.execute("UPDATE leases SET status='committed' WHERE id=?", (lease["id"],))
            if need_verification:
                conn.execute(
                    "UPDATE tasks SET status='verification', owner=NULL, updated_at=? WHERE id=?",
                    (now, task_id),
                )
                self._event(
                    conn, "verification_requested", task_id, worker,
                    {"lease_id": lease["id"], "fencing_token": lease["fencing_token"],
                     "result": result or {}},
                )
            elif need_approval:
                conn.execute(
                    "UPDATE tasks SET status='review', owner=NULL, updated_at=? WHERE id=?",
                    (now, task_id),
                )
                approval_ids = self._insert_approvals(
                    conn, task_id, worker, project_row["approvals_required"], now,
                    now + float(project_row["approval_ttl_seconds"]),
                )
                self._event(
                    conn, "approval_requested", task_id, worker,
                    {"approval_ids": approval_ids,
                     "approvals_required": project_row["approvals_required"],
                     "lease_id": lease["id"],
                     "fencing_token": lease["fencing_token"], "result": result or {}},
                )
            else:
                conn.execute(
                    "UPDATE tasks SET status='done', owner=NULL, updated_at=? WHERE id=?",
                    (now, task_id),
                )
                self._event(conn, "task_committed", task_id, worker,
                            {"lease_id": lease["id"], "fencing_token": lease["fencing_token"],
                             "result": result or {}})
            if run is not None:
                self._insert_run(conn, task_id, worker, int(fencing_token), run)
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return self._task(row)

    # ---------- V1 执行实体 / 文件预约 ----------
    @staticmethod
    def _execution(row: sqlite3.Row) -> dict:
        return {
            "execution_id": row["id"],
            "task_id": row["task_id"],
            "agent": row["agent"],
            "role": row["role"],
            "workspace": row["workspace"],
            "session": row["session"],
            "status": row["status"],
            "generation": row["generation"],
            "started_at": _iso(row["started_at"]),
            "ended_at": _iso(row["ended_at"]),
            "exit_reason": row["exit_reason"],
            "meta": _json_load(row["meta"], {}),
        }

    def start_execution(
        self,
        task_id: str,
        agent: str,
        role: str = "author",
        workspace: str = "",
        session: str = "",
        meta: dict | None = None,
    ) -> dict:
        now = _now()
        with self._tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise CoordinatorError(f"unknown task: {task_id}")
            if task["status"] in ("done", "cancelled"):
                raise CoordinatorError(f"task is already {task['status']}")
            conflicts = self._write_set_file_conflicts(conn, self._task(task))
            if conflicts:
                raise CoordinatorError(
                    "file reservation conflict: " + json.dumps(conflicts, ensure_ascii=False)
                )
            if role == "author":
                active = conn.execute(
                    "SELECT id FROM executions WHERE task_id=? AND role='author' AND status='running'",
                    (task_id,),
                ).fetchone()
                if active is not None:
                    raise CoordinatorError(f"task already has an active author execution: {active['id']}")
            self._transition_in_tx(conn, task_id, "in_progress", "execution_started")
            generation = conn.execute(
                "SELECT COALESCE(MAX(generation), 0) + 1 AS g FROM executions WHERE task_id=?",
                (task_id,),
            ).fetchone()["g"]
            execution_id = "E-" + uuid.uuid4().hex[:8]
            conn.execute(
                "INSERT INTO executions(id, task_id, agent, role, workspace, session, status, generation, started_at, meta) "
                "VALUES (?,?,?,?,?,?, 'running', ?,?,?)",
                (execution_id, task_id, agent, role, workspace, session, generation, now,
                 json.dumps(meta or {}, ensure_ascii=False)),
            )
            self._event(conn, "execution_started", task_id, agent,
                        {"execution_id": execution_id, "role": role, "generation": generation})
            row = conn.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
            return self._execution(row)

    def finish_execution(
        self,
        execution_id: str,
        status: str = "succeeded",
        exit_reason: str = "",
        meta: dict | None = None,
    ) -> dict:
        allowed = {"succeeded", "failed", "cancelled", "interrupted"}
        if status not in allowed:
            raise CoordinatorError(f"invalid execution status: {status}")
        now = _now()
        with self._tx() as conn:
            execution = conn.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
            if execution is None:
                raise CoordinatorError(f"unknown execution: {execution_id}")
            if execution["status"] != "running":
                raise CoordinatorError(f"execution is not running: {execution['status']}")
            conn.execute(
                "UPDATE executions SET status=?, ended_at=?, exit_reason=?, meta=? WHERE id=?",
                (status, now, exit_reason,
                 json.dumps(meta if meta is not None else _json_load(execution["meta"], {}), ensure_ascii=False),
                 execution_id),
            )
            target = {
                "succeeded": "auto_review",
                "failed": "needs_fix",
                "cancelled": "cancelled",
                "interrupted": "needs_fix",
            }[status]
            self._transition_in_tx(conn, execution["task_id"], target, f"execution_{status}")
            self._event(conn, "execution_finished", execution["task_id"], execution["agent"],
                        {"execution_id": execution_id, "status": status,
                         "generation": execution["generation"], "exit_reason": exit_reason})
            row = conn.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
            return self._execution(row)

    def get_execution(self, execution_id: str) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
        if row is None:
            raise CoordinatorError(f"unknown execution: {execution_id}")
        return self._execution(row)

    def list_executions(self, task_id: str | None = None, limit: int = 100) -> list[dict]:
        sql = (
            "SELECT e.*, t.title AS task_title FROM executions e "
            "JOIN tasks t ON t.id=e.task_id WHERE 1=1"
        )
        params: list[Any] = []
        if task_id:
            sql += " AND e.task_id=?"
            params.append(task_id)
        sql += " ORDER BY e.started_at DESC LIMIT ?"
        params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            item = self._execution(row)
            item["task_title"] = row["task_title"]
            out.append(item)
        return out

    @staticmethod
    def _normalize_repo_path(path: str) -> tuple[str, str]:
        raw = str(path).replace("\\", "/").strip()
        while raw.startswith("./"):
            raw = raw[2:]
        if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
            raise CoordinatorError(f"absolute path not allowed: {path}")
        raw = raw.strip("/")
        if not raw:
            raise CoordinatorError("empty repo path")
        parts = raw.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise CoordinatorError(f"path escapes repo root: {path}")
        return raw, raw.lower()

    def reserve_files(
        self,
        task_id: str,
        repo_id: str,
        paths: Iterable[str],
        execution_id: str | None = None,
        kind: str = "file",
    ) -> list[dict]:
        if kind not in ("file", "dir"):
            raise CoordinatorError("kind must be file or dir")
        normalized = []
        seen = set()
        for path in paths:
            raw, key = self._normalize_repo_path(path)
            if key not in seen:
                seen.add(key)
                normalized.append((raw, key))
        if not normalized:
            raise CoordinatorError("no paths to reserve")
        now = _now()
        with self._tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise CoordinatorError(f"unknown task: {task_id}")
            if execution_id:
                execution = conn.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
                if execution is None:
                    raise CoordinatorError(f"unknown execution: {execution_id}")
            existing = conn.execute(
                "SELECT fr.*, t.title FROM file_reservations fr JOIN tasks t ON t.id=fr.task_id "
                "WHERE fr.repo_id=? AND fr.status='active' AND fr.task_id != ?",
                (repo_id, task_id),
            ).fetchall()

            def conflicts(a: str, b: str) -> bool:
                return a == b or a.startswith(b + "/") or b.startswith(a + "/")

            conflicts_found = []
            for raw, key in normalized:
                for row in existing:
                    if conflicts(row["path_key"], key):
                        conflicts_found.append(
                            {"path": raw, "with_task": row["task_id"],
                             "with_title": row["title"], "with_path": row["path"]}
                        )
            if conflicts_found:
                raise CoordinatorError(
                    "file reservation conflict: "
                    + json.dumps(conflicts_found, ensure_ascii=False)
                )

            ids = []
            for raw, key in normalized:
                reservation_id = "FR-" + uuid.uuid4().hex[:8]
                conn.execute(
                    "INSERT INTO file_reservations(id, project_id, repo_id, task_id, execution_id, "
                    "path, path_key, kind, status, created_at) VALUES (?,?,?,?,?,?,?,?, 'active', ?)",
                    (reservation_id, task["project_id"], repo_id, task_id, execution_id,
                     raw, key, kind, now),
                )
                ids.append(reservation_id)
            self._event(conn, "files_reserved", task_id, "system",
                        {"repo_id": repo_id, "reservation_ids": ids,
                         "paths": [raw for raw, _ in normalized], "kind": kind})
            rows = conn.execute(
                "SELECT * FROM file_reservations WHERE id IN (%s)" % ",".join("?" * len(ids)),
                ids,
            ).fetchall()
            return [self._file_reservation(row) for row in rows]

    @staticmethod
    def _file_reservation(row: sqlite3.Row) -> dict:
        return {
            "reservation_id": row["id"],
            "project_id": row["project_id"],
            "repo_id": row["repo_id"],
            "task_id": row["task_id"],
            "execution_id": row["execution_id"],
            "path": row["path"],
            "path_key": row["path_key"],
            "kind": row["kind"],
            "status": row["status"],
            "created_at": _iso(row["created_at"]),
            "released_at": _iso(row["released_at"]),
            "release_reason": row["release_reason"],
        }

    def release_file_reservations(
        self,
        task_id: str | None = None,
        repo_id: str | None = None,
        reason: str = "",
    ) -> int:
        if not task_id and not repo_id:
            raise CoordinatorError("task_id or repo_id required")
        now = _now()
        sql = "UPDATE file_reservations SET status='released', released_at=?, release_reason=? WHERE status='active'"
        params: list[Any] = [now, reason]
        if task_id:
            sql += " AND task_id=?"
            params.append(task_id)
        if repo_id:
            sql += " AND repo_id=?"
            params.append(repo_id)
        with self._tx() as conn:
            cur = conn.execute(sql, params)
            self._event(conn, "files_released", task_id, "system",
                        {"repo_id": repo_id, "count": cur.rowcount, "reason": reason})
            return cur.rowcount

    def list_file_reservations(
        self,
        task_id: str | None = None,
        repo_id: str | None = None,
        active_only: bool = True,
        limit: int = 200,
    ) -> list[dict]:
        sql = (
            "SELECT fr.*, t.title AS task_title FROM file_reservations fr "
            "JOIN tasks t ON t.id=fr.task_id WHERE 1=1"
        )
        params: list[Any] = []
        if task_id:
            sql += " AND fr.task_id=?"
            params.append(task_id)
        if repo_id:
            sql += " AND fr.repo_id=?"
            params.append(repo_id)
        if active_only:
            sql += " AND fr.status='active'"
        sql += " ORDER BY fr.created_at DESC LIMIT ?"
        params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            item = self._file_reservation(row)
            item["task_title"] = row["task_title"]
            out.append(item)
        return out

    # ---------- V1 Candidate / Evidence / Merge ----------
    @staticmethod
    def _required_approver_role(conn: sqlite3.Connection, task_id: str) -> str:
        row = conn.execute(
            "SELECT p.required_approver_role FROM projects p "
            "JOIN tasks t ON t.project_id=p.id WHERE t.id=?",
            (task_id,),
        ).fetchone()
        return row["required_approver_role"] if row else "reviewer"

    @staticmethod
    def _approval_chain_for_task(conn: sqlite3.Connection, task_id: str) -> list[str]:
        row = conn.execute(
            "SELECT p.required_approver_role, p.approval_chain FROM projects p "
            "JOIN tasks t ON t.project_id=p.id WHERE t.id=?",
            (task_id,),
        ).fetchone()
        if row is None:
            return ["reviewer"]
        chain = [x.strip() for x in (row["approval_chain"] or "").split(",") if x.strip()]
        if chain:
            return chain
        return [row["required_approver_role"] or "reviewer"]

    @staticmethod
    def _candidate(row: sqlite3.Row) -> dict:
        return {
            "candidate_id": row["id"],
            "task_id": row["task_id"],
            "repo_id": row["repo_id"],
            "author_execution_id": row["author_execution_id"],
            "base_sha": row["base_sha"],
            "candidate_sha": row["candidate_sha"],
            "status": row["status"],
            "created_at": _iso(row["created_at"]),
            "frozen_at": _iso(row["frozen_at"]),
            "approved_by": row["approved_by"],
            "approver_role": row["approver_role"],
            "approved_at": _iso(row["approved_at"]),
            "merged_at": _iso(row["merged_at"]),
            "meta": _json_load(row["meta"], {}),
        }

    @staticmethod
    def _evidence(row: sqlite3.Row) -> dict:
        return {
            "evidence_id": row["id"],
            "candidate_id": row["candidate_id"],
            "execution_id": row["execution_id"],
            "type": row["type"],
            "command": row["command"],
            "cwd": row["cwd"],
            "exit_code": row["exit_code"],
            "output_ref": row["output_ref"],
            "sha": row["sha"],
            "created_at": _iso(row["created_at"]),
        }

    @staticmethod
    def _merge_attempt(row: sqlite3.Row) -> dict:
        return {
            "attempt_id": row["id"],
            "candidate_id": row["candidate_id"],
            "expected_base_sha": row["expected_base_sha"],
            "requested_by": row["requested_by"],
            "status": row["status"],
            "result_sha": row["result_sha"],
            "error": row["error"],
            "created_at": _iso(row["created_at"]),
            "finished_at": _iso(row["finished_at"]),
        }

    def create_candidate(
        self,
        task_id: str,
        repo_id: str,
        base_sha: str,
        candidate_sha: str,
        author_execution_id: str | None = None,
        meta: dict | None = None,
    ) -> dict:
        if not base_sha or not candidate_sha or not repo_id:
            raise CoordinatorError("repo_id/base_sha/candidate_sha required")
        now = _now()
        with self._tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise CoordinatorError(f"unknown task: {task_id}")
            if author_execution_id:
                execution = conn.execute(
                    "SELECT * FROM executions WHERE id=?", (author_execution_id,)
                ).fetchone()
                if execution is None:
                    raise CoordinatorError(f"unknown execution: {author_execution_id}")
                if execution["task_id"] != task_id:
                    raise CoordinatorError("author execution does not belong to task")
                if execution["status"] != "succeeded":
                    raise CoordinatorError("author execution must be succeeded")
            candidate_id = "C-" + uuid.uuid4().hex[:8]
            conn.execute(
                "INSERT INTO candidates(id, task_id, repo_id, author_execution_id, base_sha, "
                "candidate_sha, status, created_at, meta) VALUES (?,?,?,?,?,?, 'building', ?,?)",
                (candidate_id, task_id, repo_id, author_execution_id, base_sha, candidate_sha,
                 now, json.dumps(meta or {}, ensure_ascii=False)),
            )
            self._event(conn, "candidate_created", task_id, "system",
                        {"candidate_id": candidate_id, "repo_id": repo_id,
                         "base_sha": base_sha, "candidate_sha": candidate_sha})
            row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            return self._candidate(row)

    def get_candidate(self, candidate_id: str) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if row is None:
            raise CoordinatorError(f"unknown candidate: {candidate_id}")
        return self._candidate(row)

    def list_candidates(self, task_id: str | None = None, status: str | None = None) -> list[dict]:
        sql = (
            "SELECT c.*, t.title AS task_title FROM candidates c "
            "JOIN tasks t ON t.id=c.task_id WHERE 1=1"
        )
        params: list[Any] = []
        if task_id:
            sql += " AND c.task_id=?"
            params.append(task_id)
        if status:
            sql += " AND c.status=?"
            params.append(status)
        sql += " ORDER BY c.created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            item = self._candidate(row)
            item["task_title"] = row["task_title"]
            out.append(item)
        return out

    def record_evidence(
        self,
        candidate_id: str,
        type: str,
        command: str = "",
        cwd: str = "",
        exit_code: int | None = None,
        output_ref: str = "",
        sha: str | None = None,
        execution_id: str | None = None,
    ) -> dict:
        now = _now()
        with self._tx() as conn:
            candidate = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if candidate is None:
                raise CoordinatorError(f"unknown candidate: {candidate_id}")
            if candidate["status"] in ("approved", "merged", "rejected", "invalidated"):
                raise CoordinatorError(f"candidate is already {candidate['status']}")
            if execution_id:
                execution = conn.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
                if execution is None:
                    raise CoordinatorError(f"unknown execution: {execution_id}")
            evidence_id = "EV-" + uuid.uuid4().hex[:8]
            conn.execute(
                "INSERT INTO evidence(id, candidate_id, execution_id, type, command, cwd, "
                "exit_code, output_ref, sha, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (evidence_id, candidate_id, execution_id, type, command, cwd,
                 exit_code, output_ref, sha, now),
            )
            self._event(conn, "evidence_recorded", candidate["task_id"], "system",
                        {"evidence_id": evidence_id, "candidate_id": candidate_id,
                         "type": type, "exit_code": exit_code, "sha": sha})
            row = conn.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
            return self._evidence(row)

    def list_evidence(self, candidate_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM evidence WHERE candidate_id=? ORDER BY created_at", (candidate_id,)
            ).fetchall()
        return [self._evidence(row) for row in rows]

    def list_candidate_approvals(self, candidate_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM candidate_approvals WHERE candidate_id=? ORDER BY created_at",
                (candidate_id,),
            ).fetchall()
        return [
            {
                "approver": r["approver"], "role": r["role"],
                "note": r["note"], "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ]

    @staticmethod
    def _semantic_change(row: sqlite3.Row) -> dict:
        return {
            "change_id": row["id"],
            "candidate_id": row["candidate_id"],
            "task_id": row["task_id"],
            "path": row["path"],
            "object": row["object"],
            "change_type": row["change_type"],
            "compatibility": row["compatibility"],
            "before_value": row["before_value"],
            "after_value": row["after_value"],
            "created_at": _iso(row["created_at"]),
        }

    def record_semantic_changes(self, candidate_id: str, changes: Iterable[dict]) -> list[dict]:
        changes = list(changes)
        now = _now()
        with self._tx() as conn:
            candidate = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if candidate is None:
                raise CoordinatorError(f"unknown candidate: {candidate_id}")
            conn.execute("DELETE FROM semantic_changes WHERE candidate_id=?", (candidate_id,))
            for item in changes:
                conn.execute(
                    "INSERT INTO semantic_changes(id, candidate_id, task_id, path, object, change_type, compatibility, before_value, after_value, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("SC-" + uuid.uuid4().hex[:8], candidate_id, candidate["task_id"],
                     item.get("path", ""), item.get("object", ""), item.get("change_type", "unknown"),
                     item.get("compatibility", "unknown"), item.get("before_value", ""),
                     item.get("after_value", ""), now),
                )
            self._event(conn, "semantic_changes_recorded", candidate["task_id"], "system",
                        {"candidate_id": candidate_id, "count": len(changes)})
        return self.list_semantic_changes(candidate_id=candidate_id)

    def capture_semantic_changes(self, candidate_id: str) -> list[dict]:
        candidate = self.get_candidate(candidate_id)
        with self._conn() as conn:
            ws = conn.execute(
                "SELECT * FROM workspaces WHERE task_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
                (candidate["task_id"],),
            ).fetchone()
        if ws is None:
            return []
        changes = semantic.git_changes(
            ws["repo_path"], candidate["base_sha"], candidate["candidate_sha"],
        )
        return self.record_semantic_changes(candidate_id, changes)

    def list_semantic_changes(
        self, candidate_id: str | None = None, task_id: str | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM semantic_changes WHERE 1=1"
        params: list[Any] = []
        if candidate_id:
            sql += " AND candidate_id=?"
            params.append(candidate_id)
        if task_id:
            sql += " AND task_id=?"
            params.append(task_id)
        sql += " ORDER BY created_at, object"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._semantic_change(row) for row in rows]

    def add_semantic_dependency(self, task_id: str, object_name: str) -> dict:
        now = _now()
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone() is None:
                raise CoordinatorError(f"unknown task: {task_id}")
            conn.execute(
                "INSERT OR REPLACE INTO semantic_dependencies(task_id, object, created_at) VALUES (?,?,?)",
                (task_id, object_name, now),
            )
            self._event(conn, "semantic_dependency_added", task_id, "system",
                        {"object": object_name})
        return {"task_id": task_id, "object": object_name}

    def list_semantic_dependencies(self, task_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM semantic_dependencies WHERE 1=1"
        params: list[Any] = []
        if task_id:
            sql += " AND task_id=?"
            params.append(task_id)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [{"task_id": r["task_id"], "object": r["object"], "created_at": _iso(r["created_at"])} for r in rows]

    def ack_semantic_change(self, change_id: str, task_id: str, agent: str) -> dict:
        now = _now()
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM semantic_changes WHERE id=?", (change_id,)).fetchone() is None:
                raise CoordinatorError(f"unknown semantic change: {change_id}")
            if conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone() is None:
                raise CoordinatorError(f"unknown task: {task_id}")
            conn.execute(
                "INSERT OR REPLACE INTO semantic_acks(change_id, task_id, agent, acked_at) VALUES (?,?,?,?)",
                (change_id, task_id, agent, now),
            )
            self._event(conn, "semantic_change_acked", task_id, agent,
                        {"change_id": change_id})
        return {"change_id": change_id, "task_id": task_id, "agent": agent}

    def semantic_impact(self, change_id: str) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM semantic_changes WHERE id=?", (change_id,)).fetchone()
            if row is None:
                raise CoordinatorError(f"unknown semantic change: {change_id}")
            change = self._semantic_change(row)
            deps = {
                r["task_id"] for r in conn.execute(
                    "SELECT task_id FROM semantic_dependencies WHERE object=?", (change["object"],),
                )
            }
            acked = {
                r["task_id"] for r in conn.execute(
                    "SELECT task_id FROM semantic_acks WHERE change_id=?", (change_id,),
                )
            }
            tasks = conn.execute("SELECT * FROM tasks WHERE status NOT IN ('done','cancelled')").fetchall()
            candidate = conn.execute(
                "SELECT repo_id FROM candidates WHERE id=?", (change["candidate_id"],)
            ).fetchone()
        affected = []
        for task_row in tasks:
            task = self._task(task_row)
            if task["id"] == change["task_id"]:
                continue
            if candidate is not None and task.get("repo_id") != candidate["repo_id"]:
                continue
            reasons = []
            if change["path"] in task["write_set"]:
                reasons.append(f"write_set:{change['path']}")
            if task["id"] in deps:
                reasons.append("semantic_dep")
            if not reasons:
                continue
            affected.append({
                "task_id": task["id"],
                "title": task["title"],
                "lifecycle": task["lifecycle"],
                "reasons": reasons,
                "acked": task["id"] in acked,
                "pending_ack": task["id"] not in acked and change["compatibility"] in ("breaking", "unknown"),
            })
        return {
            "change": change,
            "affected_tasks": affected,
            "requires_admin": change["compatibility"] == "breaking",
        }

    def set_agent_capability(
        self, agent_id: str, dimension: str, value: str,
        confidence: float = 0.5, source: str = "manual", sample_size: int = 0,
    ) -> dict:
        if not agent_id or not dimension or not value:
            raise CoordinatorError("agent_id, dimension and value are required")
        confidence = max(0.0, min(1.0, float(confidence)))
        now = _now()
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agent_capabilities(agent_id, dimension, value, confidence, source, sample_size, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (agent_id, dimension, value, confidence, source, max(0, int(sample_size)), now),
            )
            self._event(conn, "agent_capability_set", None, "system",
                        {"agent_id": agent_id, "dimension": dimension, "value": value,
                         "confidence": confidence, "source": source})
        return {"agent_id": agent_id, "dimension": dimension, "value": value,
                "confidence": confidence, "source": source, "sample_size": sample_size}

    def list_agent_capabilities(self, agent_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM agent_capabilities WHERE 1=1"
        params: list[Any] = []
        if agent_id:
            sql += " AND agent_id=?"
            params.append(agent_id)
        sql += " ORDER BY agent_id, dimension, value"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {"agent_id": r["agent_id"], "dimension": r["dimension"], "value": r["value"],
             "confidence": r["confidence"], "source": r["source"],
             "sample_size": r["sample_size"], "updated_at": _iso(r["updated_at"])}
            for r in rows
        ]

    def freeze_candidate(self, candidate_id: str) -> dict:
        now = _now()
        with self._tx() as conn:
            candidate = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if candidate is None:
                raise CoordinatorError(f"unknown candidate: {candidate_id}")
            if candidate["status"] != "building":
                raise CoordinatorError(f"candidate is not building: {candidate['status']}")
            conn.execute(
                "UPDATE candidates SET status='validating', frozen_at=? WHERE id=?",
                (now, candidate_id),
            )
            self._event(conn, "candidate_frozen", candidate["task_id"], "system",
                        {"candidate_id": candidate_id, "candidate_sha": candidate["candidate_sha"]})
            row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            return self._candidate(row)

    def mark_candidate_ready(self, candidate_id: str) -> dict:
        with self._tx() as conn:
            candidate = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if candidate is None:
                raise CoordinatorError(f"unknown candidate: {candidate_id}")
            if candidate["status"] != "validating":
                raise CoordinatorError(f"candidate is not validating: {candidate['status']}")
            ok = conn.execute(
                "SELECT 1 FROM evidence WHERE candidate_id=? AND sha=? AND exit_code=0 LIMIT 1",
                (candidate_id, candidate["candidate_sha"]),
            ).fetchone()
            if ok is None:
                raise CoordinatorError("candidate needs passing evidence bound to candidate_sha")
            conn.execute("UPDATE candidates SET status='awaiting_human' WHERE id=?", (candidate_id,))
            self._transition_in_tx(conn, candidate["task_id"], "human_review", "candidate_ready")
            self._event(conn, "candidate_ready", candidate["task_id"], "system",
                        {"candidate_id": candidate_id, "candidate_sha": candidate["candidate_sha"]})
            row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            return self._candidate(row)

    def approve_candidate(
        self, candidate_id: str, approver: str, note: str = "", role: str = "reviewer",
    ) -> dict:
        if role not in ("reviewer", "admin"):
            raise CoordinatorError(f"invalid approver role: {role}")
        now = _now()
        with self._tx() as conn:
            candidate = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if candidate is None:
                raise CoordinatorError(f"unknown candidate: {candidate_id}")
            if candidate["status"] != "awaiting_human":
                raise CoordinatorError(f"candidate is not awaiting human: {candidate['status']}")
            evidence = conn.execute(
                "SELECT 1 FROM evidence WHERE candidate_id=? AND sha=? AND exit_code=0 LIMIT 1",
                (candidate_id, candidate["candidate_sha"]),
            ).fetchone()
            if evidence is None:
                raise CoordinatorError("candidate has no passing evidence bound to candidate_sha")

            chain = self._approval_chain_for_task(conn, candidate["task_id"])
            breaking = conn.execute(
                "SELECT 1 FROM semantic_changes WHERE candidate_id=? AND compatibility='breaking' LIMIT 1",
                (candidate_id,),
            ).fetchone()
            if breaking is not None:
                if len(chain) == 1 and APPROVER_ROLE_RANK[role] < APPROVER_ROLE_RANK["admin"]:
                    raise CoordinatorError("breaking semantic change requires admin approval")
                if len(chain) > 1 and "admin" not in chain:
                    raise CoordinatorError("breaking semantic change requires an admin approval stage")
            recorded = {
                row["role"] for row in conn.execute(
                    "SELECT role FROM candidate_approvals WHERE candidate_id=?",
                    (candidate_id,),
                )
            }
            if len(chain) == 1:
                required_role = chain[0]
                if APPROVER_ROLE_RANK[role] < APPROVER_ROLE_RANK[required_role]:
                    raise CoordinatorError(
                        f"project requires {required_role} approval; got {role}"
                    )
                if role not in recorded:
                    conn.execute(
                        "INSERT INTO candidate_approvals(id, candidate_id, approver, role, note, created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        ("CA-" + uuid.uuid4().hex[:8], candidate_id, approver, role, note, now),
                    )
            else:
                pending = [item for item in chain if item not in recorded]
                if not pending:
                    raise CoordinatorError("all approval stages already recorded")
                if role != pending[0]:
                    raise CoordinatorError(f"next approval role is {pending[0]}; got {role}")
                conn.execute(
                    "INSERT INTO candidate_approvals(id, candidate_id, approver, role, note, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    ("CA-" + uuid.uuid4().hex[:8], candidate_id, approver, role, note, now),
                )
                if len(pending) > 1:
                    self._event(conn, "candidate_approval_recorded", candidate["task_id"], approver,
                                {"candidate_id": candidate_id, "role": role,
                                 "pending_roles": pending[1:], "chain": chain})
                    row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
                    result = self._candidate(row)
                    result["approval_chain"] = chain
                    result["pending_roles"] = pending[1:]
                    return result

            conn.execute(
                "UPDATE candidates SET status='approved', approved_by=?, approver_role=?, approved_at=? WHERE id=?",
                (approver, role, now, candidate_id),
            )
            self._transition_in_tx(conn, candidate["task_id"], "ready_to_merge", "candidate_approved")
            self._event(conn, "candidate_approved", candidate["task_id"], approver,
                        {"candidate_id": candidate_id, "candidate_sha": candidate["candidate_sha"],
                         "note": note, "role": role, "chain": chain})
            row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            result = self._candidate(row)
            result["approval_chain"] = chain
            result["pending_roles"] = []
            return result

    def reject_candidate(self, candidate_id: str, approver: str, reason: str = "") -> dict:
        with self._tx() as conn:
            candidate = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if candidate is None:
                raise CoordinatorError(f"unknown candidate: {candidate_id}")
            if candidate["status"] not in ("validating", "awaiting_human"):
                raise CoordinatorError(f"candidate cannot be rejected: {candidate['status']}")
            conn.execute("UPDATE candidates SET status='rejected' WHERE id=?", (candidate_id,))
            self._transition_in_tx(conn, candidate["task_id"], "needs_fix", "candidate_rejected")
            self._event(conn, "candidate_rejected", candidate["task_id"], approver,
                        {"candidate_id": candidate_id, "reason": reason})
            row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            return self._candidate(row)

    def invalidate_candidate(self, candidate_id: str, reason: str = "") -> dict:
        with self._tx() as conn:
            candidate = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if candidate is None:
                raise CoordinatorError(f"unknown candidate: {candidate_id}")
            if candidate["status"] == "merged":
                raise CoordinatorError("merged candidate cannot be invalidated")
            conn.execute("UPDATE candidates SET status='invalidated' WHERE id=?", (candidate_id,))
            try:
                self._transition_in_tx(conn, candidate["task_id"], "needs_fix", "candidate_invalidated")
            except CoordinatorError:
                pass
            self._event(conn, "candidate_invalidated", candidate["task_id"], "system",
                        {"candidate_id": candidate_id, "reason": reason})
            row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            return self._candidate(row)

    def start_merge(
        self,
        candidate_id: str,
        expected_base_sha: str,
        requested_by: str = "human",
    ) -> dict:
        now = _now()
        with self._tx() as conn:
            candidate = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if candidate is None:
                raise CoordinatorError(f"unknown candidate: {candidate_id}")
            if candidate["status"] != "approved":
                raise CoordinatorError(f"candidate is not approved: {candidate['status']}")
            if expected_base_sha != candidate["base_sha"]:
                raise CoordinatorError("expected base SHA does not match candidate base")
            required_role = self._required_approver_role(conn, candidate["task_id"])
            approved_role = candidate["approver_role"] or "reviewer"
            if APPROVER_ROLE_RANK.get(approved_role, 0) < APPROVER_ROLE_RANK[required_role]:
                raise CoordinatorError(
                    f"candidate was approved by {approved_role}; project requires {required_role}"
                )
            pending = conn.execute(
                "SELECT 1 FROM merge_attempts WHERE candidate_id=? AND status='pending'",
                (candidate_id,),
            ).fetchone()
            if pending is not None:
                raise CoordinatorError("candidate already has a pending merge attempt")
            attempt_id = "M-" + uuid.uuid4().hex[:8]
            conn.execute(
                "INSERT INTO merge_attempts(id, candidate_id, expected_base_sha, requested_by, "
                "status, created_at) VALUES (?,?,?,?, 'pending', ?)",
                (attempt_id, candidate_id, expected_base_sha, requested_by, now),
            )
            self._event(conn, "merge_started", candidate["task_id"], requested_by,
                        {"attempt_id": attempt_id, "candidate_id": candidate_id,
                         "expected_base_sha": expected_base_sha})
            row = conn.execute("SELECT * FROM merge_attempts WHERE id=?", (attempt_id,)).fetchone()
            return self._merge_attempt(row)

    def finish_merge(
        self,
        attempt_id: str,
        status: str = "succeeded",
        result_sha: str = "",
        error: str = "",
    ) -> dict:
        if status not in ("succeeded", "failed", "unknown"):
            raise CoordinatorError(f"invalid merge status: {status}")
        now = _now()
        with self._tx() as conn:
            attempt = conn.execute("SELECT * FROM merge_attempts WHERE id=?", (attempt_id,)).fetchone()
            if attempt is None:
                raise CoordinatorError(f"unknown merge attempt: {attempt_id}")
            if attempt["status"] != "pending":
                raise CoordinatorError(f"merge attempt is not pending: {attempt['status']}")
            conn.execute(
                "UPDATE merge_attempts SET status=?, result_sha=?, error=?, finished_at=? WHERE id=?",
                (status, result_sha, error, now, attempt_id),
            )
            if status == "succeeded":
                conn.execute(
                    "UPDATE candidates SET status='merged', merged_at=? WHERE id=?",
                    (now, attempt["candidate_id"]),
                )
            candidate = conn.execute(
                "SELECT task_id FROM candidates WHERE id=?", (attempt["candidate_id"],)
            ).fetchone()
            if status == "succeeded":
                self._transition_in_tx(conn, candidate["task_id"], "accepted", "merge_succeeded")
            elif status == "failed":
                self._transition_in_tx(conn, candidate["task_id"], "needs_fix", "merge_failed")
            self._event(conn, f"merge_{status}", candidate["task_id"], attempt["requested_by"],
                        {"attempt_id": attempt_id, "candidate_id": attempt["candidate_id"],
                         "result_sha": result_sha, "error": error})
            row = conn.execute("SELECT * FROM merge_attempts WHERE id=?", (attempt_id,)).fetchone()
            return self._merge_attempt(row)

    def merge_candidate_git(
        self,
        candidate_id: str,
        repo_path: str,
        worktree_path: str,
        check_command: list[str] | None = None,
        timeout: float = 120,
    ) -> dict:
        """真实 Git 合入：从 base 建集成分支，cherry-pick candidate，可选跑检查。

        不修改数据库；调用方拿到 result_sha 后自行 finish_merge()。
        """
        with self._conn() as conn:
            candidate = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if candidate is None:
            raise CoordinatorError(f"unknown candidate: {candidate_id}")
        if candidate["status"] != "approved":
            raise CoordinatorError(f"candidate is not approved: {candidate['status']}")
        with self._conn() as conn:
            required_role = self._required_approver_role(conn, candidate["task_id"])
        approved_role = candidate["approver_role"] or "reviewer"
        if APPROVER_ROLE_RANK.get(approved_role, 0) < APPROVER_ROLE_RANK[required_role]:
            raise CoordinatorError(
                f"candidate was approved by {approved_role}; project requires {required_role}"
            )
        repo_dir = Path(repo_path).resolve()
        if not repo_dir.is_dir():
            raise CoordinatorError(f"repo path not found: {repo_dir}")
        top = self._git(repo_dir, "rev-parse", "--show-toplevel").stdout.strip()
        repo_dir = Path(top).resolve()
        self._git(repo_dir, "cat-file", "-e", f"{candidate['candidate_sha']}^{{commit}}")
        integration = Path(worktree_path).resolve()
        if integration.exists():
            raise CoordinatorError(f"integration path already exists: {integration}")
        branch = f"guild/merge-{candidate_id.lower()}-{uuid.uuid4().hex[:6]}"
        integration.parent.mkdir(parents=True, exist_ok=True)
        self._git(repo_dir, "worktree", "add", "-b", branch, str(integration),
                  candidate["base_sha"])
        try:
            self._git(integration, "cherry-pick", candidate["candidate_sha"])
            check_exit = None
            check_output = ""
            if check_command:
                proc = subprocess.run(
                    check_command, cwd=str(integration), capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=timeout,
                )
                check_exit = proc.returncode
                check_output = (proc.stdout or "") + (proc.stderr or "")
                if proc.returncode != 0:
                    raise CoordinatorError(
                        f"merge check failed ({proc.returncode}): {check_output[-2000:]}"
                    )
            result_sha = self._git(integration, "rev-parse", "HEAD").stdout.strip()
            with self._tx() as conn:
                self._event(conn, "merge_git_succeeded", candidate["task_id"], "system", {
                    "candidate_id": candidate_id,
                    "base_sha": candidate["base_sha"],
                    "candidate_sha": candidate["candidate_sha"],
                    "result_sha": result_sha,
                    "branch": branch,
                    "worktree": str(integration),
                })
            return {
                "candidate_id": candidate_id,
                "result_sha": result_sha,
                "branch": branch,
                "worktree": str(integration),
                "check_exit": check_exit,
                "check_output": check_output[-2000:],
            }
        except Exception:
            self._git(integration, "cherry-pick", "--abort", check=False)
            self._git(repo_dir, "worktree", "remove", "--force", str(integration), check=False)
            self._git(repo_dir, "branch", "-D", branch, check=False)
            raise

    def list_merge_attempts(self, candidate_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM merge_attempts WHERE 1=1"
        params: list[Any] = []
        if candidate_id:
            sql += " AND candidate_id=?"
            params.append(candidate_id)
        sql += " ORDER BY created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._merge_attempt(row) for row in rows]

    # ---------- V1 Worktree / 越界 diff ----------
    @staticmethod
    def _git(cwd: Path | str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check and proc.returncode != 0:
            raise CoordinatorError(
                f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}"
            )
        return proc

    @staticmethod
    def _workspace(row: sqlite3.Row) -> dict:
        return {
            "workspace_id": row["id"],
            "task_id": row["task_id"],
            "execution_id": row["execution_id"],
            "repo_id": row["repo_id"],
            "repo_path": row["repo_path"],
            "path": row["path"],
            "branch": row["branch"],
            "base_sha": row["base_sha"],
            "status": row["status"],
            "created_at": _iso(row["created_at"]),
            "removed_at": _iso(row["removed_at"]),
        }

    def create_workspace(
        self,
        task_id: str,
        repo_id: str,
        repo_path: str,
        base_sha: str = "HEAD",
        branch: str | None = None,
        path: str | None = None,
        execution_id: str | None = None,
    ) -> dict:
        repo_dir = Path(repo_path).resolve()
        if not repo_dir.is_dir():
            raise CoordinatorError(f"repo path not found: {repo_dir}")
        with self._tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise CoordinatorError(f"unknown task: {task_id}")
            if execution_id:
                execution = conn.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
                if execution is None or execution["task_id"] != task_id:
                    raise CoordinatorError("execution does not belong to task")
        top = self._git(repo_dir, "rev-parse", "--show-toplevel").stdout.strip()
        repo_dir = Path(top).resolve()
        resolved_base = self._git(repo_dir, "rev-parse", f"{base_sha}^{{commit}}").stdout.strip()
        branch = branch or f"guild/{task_id.lower()}-{uuid.uuid4().hex[:6]}"
        if path is None:
            path = str(repo_dir.parent / ".guild-worktrees" / branch.replace("/", "-"))
        worktree = Path(path).resolve()
        if worktree.exists():
            raise CoordinatorError(f"worktree path already exists: {worktree}")
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self._git(repo_dir, "worktree", "add", "-b", branch, str(worktree), resolved_base)

        now = _now()
        workspace_id = "W-" + uuid.uuid4().hex[:8]
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO workspaces(id, task_id, execution_id, repo_id, repo_path, path, "
                "branch, base_sha, status, created_at) VALUES (?,?,?,?,?,?,?,?, 'active', ?)",
                (workspace_id, task_id, execution_id, repo_id, str(repo_dir),
                 str(worktree), branch, resolved_base, now),
            )
            self._event(conn, "workspace_created", task_id, "system",
                        {"workspace_id": workspace_id, "repo_id": repo_id,
                         "path": str(worktree), "branch": branch, "base_sha": resolved_base})
            row = conn.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
            return self._workspace(row)

    def get_workspace(self, workspace_id: str) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
        if row is None:
            raise CoordinatorError(f"unknown workspace: {workspace_id}")
        return self._workspace(row)

    def list_workspaces(self, task_id: str | None = None, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM workspaces WHERE 1=1"
        params: list[Any] = []
        if task_id:
            sql += " AND task_id=?"
            params.append(task_id)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._workspace(row) for row in rows]

    def workspace_changed_files(self, workspace_id: str) -> list[str]:
        ws = self.get_workspace(workspace_id)
        if ws["status"] != "active":
            raise CoordinatorError(f"workspace is {ws['status']}")
        path = Path(ws["path"])
        if not path.is_dir():
            raise CoordinatorError(f"workspace path missing: {path}")
        changed = set()
        diff = self._git(path, "diff", "--name-only", ws["base_sha"])
        changed.update(x.strip().replace("\\", "/") for x in diff.stdout.splitlines() if x.strip())
        untracked = self._git(path, "ls-files", "--others", "--exclude-standard")
        changed.update(x.strip().replace("\\", "/") for x in untracked.stdout.splitlines() if x.strip())
        ignored_parts = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"}
        filtered = set()
        for item in changed:
            parts = item.split("/")
            if any(part in ignored_parts for part in parts):
                continue
            if item.endswith((".pyc", ".pyo")):
                continue
            filtered.add(item)
        return sorted(filtered)

    def workspace_head(self, workspace_id: str) -> str:
        ws = self.get_workspace(workspace_id)
        return self._git(Path(ws["path"]), "rev-parse", "HEAD").stdout.strip()

    def commit_workspace(self, workspace_id: str, message: str = "guild: snapshot") -> str:
        ws = self.get_workspace(workspace_id)
        path = Path(ws["path"])
        self._git(path, "add", "-A")
        status = self._git(path, "status", "--porcelain").stdout.strip()
        if status:
            self._git(
                path,
                "-c", "user.name=AgentGuild",
                "-c", "user.email=guild@local",
                "commit", "-m", message,
            )
        head = self._git(path, "rev-parse", "HEAD").stdout.strip()
        with self._tx() as conn:
            self._event(conn, "workspace_committed", ws["task_id"], "system",
                        {"workspace_id": workspace_id, "sha": head})
        return head

    def check_workspace_scope(self, workspace_id: str, task_id: str | None = None) -> dict:
        ws = self.get_workspace(workspace_id)
        task_id = task_id or ws["task_id"]
        changed = self.workspace_changed_files(workspace_id)
        reservations = self.list_file_reservations(
            task_id=task_id, repo_id=ws["repo_id"], active_only=True
        )
        reserved = [r["path_key"] for r in reservations]
        violations = []
        for path in changed:
            _, key = self._normalize_repo_path(path)
            covered = any(
                key == r or key.startswith(r + "/") for r in reserved
            )
            if not covered:
                violations.append(path)
        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "changed": changed,
            "reserved": reserved,
            "violations": violations,
            "ok": not violations,
        }

    def create_candidate_from_workspace(
        self,
        workspace_id: str,
        repo_id: str | None = None,
        meta: dict | None = None,
    ) -> dict:
        ws = self.get_workspace(workspace_id)
        head = self.workspace_head(workspace_id)
        author_execution_id = ws["execution_id"]
        if not author_execution_id:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT id FROM executions WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
                    (ws["task_id"],),
                ).fetchone()
                if row is not None:
                    author_execution_id = row["id"]
        candidate = self.create_candidate(
            ws["task_id"],
            repo_id or ws["repo_id"],
            ws["base_sha"],
            head,
            author_execution_id=author_execution_id,
            meta=meta,
        )
        self._capture_semantic_best_effort(candidate["candidate_id"])
        return candidate

    def _capture_semantic_best_effort(self, candidate_id: str) -> None:
        try:
            self.capture_semantic_changes(candidate_id)
        except Exception as exc:  # semantic extraction must not block candidate creation
            try:
                with self._tx() as conn:
                    candidate = conn.execute(
                        "SELECT task_id FROM candidates WHERE id=?", (candidate_id,)
                    ).fetchone()
                    self._event(conn, "semantic_capture_failed", candidate["task_id"] if candidate else None,
                                "system", {"candidate_id": candidate_id, "error": str(exc)[:500]})
            except Exception:
                pass

    def remove_workspace(self, workspace_id: str, force: bool = True) -> dict:
        ws = self.get_workspace(workspace_id)
        if ws["status"] == "removed":
            return ws
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(ws["path"])
        self._git(ws["repo_path"], *args, check=False)
        now = _now()
        with self._tx() as conn:
            conn.execute(
                "UPDATE workspaces SET status='removed', removed_at=? WHERE id=?",
                (now, workspace_id),
            )
            self._event(conn, "workspace_removed", ws["task_id"], "system",
                        {"workspace_id": workspace_id, "path": ws["path"]})
            row = conn.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
            return self._workspace(row)

    # ---------- 验证 ----------
    def record_verification(
        self,
        task_id: str,
        verifier: str,
        passed: bool,
        score: float | None = None,
        notes: str = "",
    ) -> dict:
        now = _now()
        with self._tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise CoordinatorError(f"unknown task: {task_id}")
            if task["status"] != "verification":
                raise CoordinatorError(f"task is not awaiting verification: {task['status']}")
            project = conn.execute(
                "SELECT require_approval, approvals_required, approval_ttl_seconds "
                "FROM projects WHERE id=?",
                (task["project_id"],),
            ).fetchone()
            verification_id = "V-" + uuid.uuid4().hex[:8]
            conn.execute(
                "INSERT INTO verifications(id, task_id, verifier, passed, score, notes, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (verification_id, task_id, verifier, 1 if passed else 0, score, notes, now),
            )
            if not passed:
                conn.execute(
                    "UPDATE tasks SET status='ready', owner=NULL, updated_at=? WHERE id=?",
                    (now, task_id),
                )
                self._event(conn, "verification_failed", task_id, verifier,
                            {"verification_id": verification_id, "score": score, "notes": notes})
            elif project["require_approval"]:
                conn.execute(
                    "UPDATE tasks SET status='review', owner=NULL, updated_at=? WHERE id=?",
                    (now, task_id),
                )
                approval_ids = self._insert_approvals(
                    conn, task_id, verifier, project["approvals_required"], now,
                    now + float(project["approval_ttl_seconds"]),
                )
                self._event(conn, "verification_passed", task_id, verifier,
                            {"verification_id": verification_id, "score": score,
                             "next": "review"})
                self._event(conn, "approval_requested", task_id, verifier,
                            {"approval_ids": approval_ids,
                             "approvals_required": project["approvals_required"],
                             "verification_id": verification_id})
            else:
                conn.execute(
                    "UPDATE tasks SET status='done', owner=NULL, updated_at=? WHERE id=?",
                    (now, task_id),
                )
                self._event(conn, "verification_passed", task_id, verifier,
                            {"verification_id": verification_id, "score": score,
                             "next": "done"})
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return self._task(row)

    def list_verifications(self, task_id: str | None = None, limit: int = 100) -> list[dict]:
        sql = (
            "SELECT v.*, t.title AS task_title, t.kind AS task_kind "
            "FROM verifications v JOIN tasks t ON t.id=v.task_id WHERE 1=1"
        )
        params: list[Any] = []
        if task_id:
            sql += " AND v.task_id=?"
            params.append(task_id)
        sql += " ORDER BY v.created_at DESC LIMIT ?"
        params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "verification_id": r["id"],
                "task_id": r["task_id"],
                "task_title": r["task_title"],
                "task_kind": r["task_kind"],
                "verifier": r["verifier"],
                "passed": bool(r["passed"]),
                "score": r["score"],
                "notes": r["notes"],
                "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ]

    # ---------- 审批 ----------
    def approve_task(self, task_id: str, approver: str, note: str = "") -> dict:
        now = _now()
        with self._tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise CoordinatorError(f"unknown task: {task_id}")
            if task["status"] != "review":
                raise CoordinatorError(f"task is not in review: {task['status']}")
            project = conn.execute(
                "SELECT approvals_required FROM projects WHERE id=?", (task["project_id"],)
            ).fetchone()
            required = max(1, int(project["approvals_required"]))
            already = conn.execute(
                "SELECT 1 FROM approvals WHERE task_id=? AND status='approved' AND decided_by=?",
                (task_id, approver),
            ).fetchone()
            if already is not None:
                raise CoordinatorError("approver already approved this task")
            approval = conn.execute(
                "SELECT * FROM approvals WHERE task_id=? AND status='pending' "
                "ORDER BY requested_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if approval is None:
                raise CoordinatorError("no pending approval for task")
            conn.execute(
                "UPDATE approvals SET status='approved', decided_by=?, decided_at=?, note=? WHERE id=?",
                (approver, now, note, approval["id"]),
            )
            approved_count = conn.execute(
                "SELECT COUNT(*) AS n FROM approvals WHERE task_id=? AND status='approved'",
                (task_id,),
            ).fetchone()["n"]
            if approved_count >= required:
                conn.execute("UPDATE tasks SET status='done', owner=NULL, updated_at=? WHERE id=?", (now, task_id))
                self._event(conn, "approval_approved", task_id, approver,
                            {"approval_id": approval["id"], "note": note,
                             "approved": approved_count, "required": required})
            else:
                self._event(conn, "approval_recorded", task_id, approver,
                            {"approval_id": approval["id"], "note": note,
                             "approved": approved_count, "required": required,
                             "remaining": required - approved_count})
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return self._task(row)

    def request_approvals(
        self,
        task_id: str,
        requested_by: str = "human",
        count: int | None = None,
        ttl_seconds: float | None = None,
    ) -> dict:
        now = _now()
        with self._tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise CoordinatorError(f"unknown task: {task_id}")
            if task["status"] != "review":
                raise CoordinatorError(f"task is not in review: {task['status']}")
            project = conn.execute(
                "SELECT approvals_required, approval_ttl_seconds FROM projects WHERE id=?",
                (task["project_id"],),
            ).fetchone()
            n = max(1, int(count if count is not None else project["approvals_required"]))
            ttl = float(ttl_seconds if ttl_seconds is not None else project["approval_ttl_seconds"])
            if ttl <= 0:
                raise CoordinatorError("ttl_seconds must be > 0")
            conn.execute(
                "UPDATE approvals SET status='superseded' "
                "WHERE task_id=? AND status IN ('pending','approved')",
                (task_id,),
            )
            approval_ids = self._insert_approvals(
                conn, task_id, requested_by, n, now, now + ttl
            )
            self._event(conn, "approvals_requested", task_id, requested_by,
                        {"approval_ids": approval_ids, "approvals_required": n,
                         "ttl_seconds": ttl})
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return {
                "task": self._task(row),
                "approval_ids": approval_ids,
                "approvals_required": n,
                "expires_in": ttl,
            }

    def reject_task(self, task_id: str, approver: str, reason: str = "") -> dict:
        now = _now()
        with self._tx() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise CoordinatorError(f"unknown task: {task_id}")
            if task["status"] != "review":
                raise CoordinatorError(f"task is not in review: {task['status']}")
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM approvals WHERE task_id=? AND status='pending'",
                (task_id,),
            ).fetchone()["n"]
            conn.execute(
                "UPDATE approvals SET status='superseded', decided_by=?, decided_at=?, note=? "
                "WHERE task_id=? AND status='approved'",
                (approver, now, reason, task_id),
            )
            conn.execute(
                "UPDATE approvals SET status='rejected', decided_by=?, decided_at=?, note=? "
                "WHERE task_id=? AND status='pending'",
                (approver, now, reason, task_id),
            )
            conn.execute("UPDATE tasks SET status='ready', owner=NULL, updated_at=? WHERE id=?", (now, task_id))
            self._event(conn, "approval_rejected", task_id, approver,
                        {"rejected": pending, "reason": reason})
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return self._task(row)

    def list_approvals(self, status: str | None = None, limit: int = 100) -> list[dict]:
        sql = (
            "SELECT a.*, t.title AS task_title, t.kind AS task_kind "
            "FROM approvals a JOIN tasks t ON t.id=a.task_id WHERE 1=1"
        )
        params: list[Any] = []
        if status:
            sql += " AND a.status=?"
            params.append(status)
        sql += " ORDER BY a.requested_at DESC LIMIT ?"
        params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "approval_id": r["id"],
                "task_id": r["task_id"],
                "task_title": r["task_title"],
                "task_kind": r["task_kind"],
                "requested_by": r["requested_by"],
                "requested_at": _iso(r["requested_at"]),
                "status": r["status"],
                "decided_by": r["decided_by"],
                "decided_at": _iso(r["decided_at"]),
                "note": r["note"],
                "expires_at": _iso(r["expires_at"]),
                "expires_in": (
                    None if r["expires_at"] is None else round(r["expires_at"] - _now(), 1)
                ),
            }
            for r in rows
        ]

    # ---------- 遥测 ----------
    def record_run(
        self,
        task_id: str,
        worker: str,
        fencing_token: int | None = None,
        duration_s: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        quality: float | None = None,
        diff_lines: int = 0,
        files_changed: int = 0,
        status: str = "ok",
        meta: dict | None = None,
        execution_id: str | None = None,
    ) -> dict:
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone() is None:
                raise CoordinatorError(f"unknown task: {task_id}")
            run_id = self._insert_run(
                conn, task_id, worker, fencing_token,
                {
                    "duration_s": duration_s,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": cost_usd,
                    "quality": quality,
                    "diff_lines": diff_lines,
                    "files_changed": files_changed,
                    "status": status,
                    "meta": meta or {},
                    "execution_id": execution_id,
                },
            )
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return self._run(row)

    def list_runs(
        self,
        limit: int = 100,
        task_id: str | None = None,
        worker: str | None = None,
    ) -> list[dict]:
        sql = (
            "SELECT r.*, t.title AS task_title, t.kind AS task_kind, t.project_id AS project_id "
            "FROM runs r JOIN tasks t ON t.id=r.task_id WHERE 1=1"
        )
        params: list[Any] = []
        if task_id:
            sql += " AND r.task_id=?"
            params.append(task_id)
        if worker:
            sql += " AND r.worker=?"
            params.append(worker)
        sql += " ORDER BY r.id DESC LIMIT ?"
        params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            item = self._run(row)
            item["task_title"] = row["task_title"]
            item["task_kind"] = row["task_kind"]
            item["project_id"] = row["project_id"]
            out.append(item)
        return out

    # ---------- 观测 ----------
    def events(self, limit: int = 100, task_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM events"
        params: list[Any] = []
        if task_id:
            sql += " WHERE task_id=?"
            params.append(task_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": r["id"],
                "ts": _iso(r["ts"]),
                "type": r["type"],
                "task_id": r["task_id"],
                "actor": r["actor"],
                "payload": _json_load(r["payload"], {}),
            }
            for r in rows
        ]

    def metrics(self) -> dict:
        with self._conn() as conn:
            task_rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"
            ).fetchall()
            active = conn.execute(
                "SELECT COUNT(*) AS n FROM leases WHERE status='active' AND expires_at > ?",
                (_now(),),
            ).fetchone()["n"]
            expired = conn.execute(
                "SELECT COUNT(*) AS n FROM leases WHERE status='expired'"
            ).fetchone()["n"]
            events = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
            projects = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
            tasks = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
            runs = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS cost, AVG(quality) AS q FROM runs"
            ).fetchone()
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM approvals WHERE status='pending'"
            ).fetchone()["n"]
            reservations = conn.execute(
                "SELECT COUNT(*) AS n FROM reservations WHERE status='active' AND expires_at > ?",
                (_now(),),
            ).fetchone()["n"]
            verifications = conn.execute(
                "SELECT COUNT(*) AS n, AVG(score) AS s FROM verifications WHERE passed=1"
            ).fetchone()
        status_counts = {r["status"]: r["n"] for r in task_rows}
        return {
            "projects": projects,
            "tasks": tasks,
            "tasks_by_status": status_counts,
            "active_leases": active,
            "expired_leases_total": expired,
            "events": events,
            "runs": runs["n"],
            "total_cost_usd": round(runs["cost"], 4),
            "avg_quality": None if runs["q"] is None else round(runs["q"], 3),
            "pending_approvals": pending,
            "pending_verifications": status_counts.get("verification", 0),
            "verifications": verifications["n"],
            "avg_verification_score": (
                None if verifications["s"] is None else round(verifications["s"], 3)
            ),
            "active_reservations": reservations,
        }
