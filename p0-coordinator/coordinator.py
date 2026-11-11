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

import hashlib
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
# 必需检查项：至少一项 test + 一项 review。项目可增删具体项，但不能退化成只测。
CHECK_KINDS = ("test", "review", "check")
DEFAULT_REQUIRED_CHECKS = (
    {"id": "platform-test", "kind": "test", "title": "平台测试"},
    {"id": "agent-review", "kind": "review", "title": "Agent 审查"},
)
DEFAULT_REQUIRED_CHECKS_JSON = json.dumps(list(DEFAULT_REQUIRED_CHECKS), ensure_ascii=False)
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
    required_checks TEXT NOT NULL DEFAULT '[{"id": "platform-test", "kind": "test", "title": "平台测试"}, {"id": "agent-review", "kind": "review", "title": "Agent 审查"}]',
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
    required_checks TEXT NOT NULL DEFAULT '',
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
    active_batch_id TEXT,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS candidate_approvals (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    batch_id TEXT,
    approver TEXT NOT NULL,
    role TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    UNIQUE(candidate_id, batch_id, role)
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
    check_id TEXT NOT NULL DEFAULT '',
    batch_id TEXT,
    seq INTEGER,
    type TEXT NOT NULL,
    command TEXT NOT NULL DEFAULT '',
    cwd TEXT NOT NULL DEFAULT '',
    exit_code INTEGER,
    output_ref TEXT NOT NULL DEFAULT '',
    sha TEXT,
    status TEXT NOT NULL DEFAULT 'valid',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS validation_batches (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    candidate_sha TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    required_checks TEXT NOT NULL DEFAULT '[]',
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open',
    next_seq INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    closed_at REAL,
    close_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS merge_attempts (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    expected_base_sha TEXT NOT NULL,
    repo_path TEXT NOT NULL DEFAULT '',
    target_ref TEXT NOT NULL DEFAULT '',
    target_before_sha TEXT NOT NULL DEFAULT '',
    target_after_sha TEXT NOT NULL DEFAULT '',
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
CREATE INDEX IF NOT EXISTS idx_validation_candidate ON validation_batches(candidate_id, attempt);
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


def _normalize_check_items(items: Any) -> list[dict]:
    """校验检查项列表本身（不要求 test/review 齐全），用于任务追加项。"""
    if items is None:
        return []
    if not isinstance(items, (list, tuple)):
        raise CoordinatorError("required_checks must be a list")
    out: list[dict] = []
    seen: set[str] = set()
    for raw in items:
        if isinstance(raw, str):
            raw = {"id": raw, "kind": "test"}
        if not isinstance(raw, dict):
            raise CoordinatorError("required_checks items must be objects")
        check_id = str(raw.get("id", "")).strip()
        kind = str(raw.get("kind", "") or "test").strip()
        if not check_id:
            raise CoordinatorError("required check id is required")
        if check_id in seen:
            raise CoordinatorError(f"duplicate required check id: {check_id}")
        if kind not in CHECK_KINDS:
            raise CoordinatorError(f"invalid required check kind: {kind}")
        seen.add(check_id)
        out.append({"id": check_id, "kind": kind, "title": str(raw.get("title", "") or "")})
    return out


def _merge_required_checks(project_checks: Iterable[dict], task_checks: Iterable[dict]) -> list[dict]:
    """项目必需项 + 任务追加项；任务不能删项，也不能改同名项的 kind。"""
    merged = [dict(item) for item in project_checks]
    by_id = {item["id"]: item for item in merged}
    for item in task_checks:
        existing = by_id.get(item["id"])
        if existing is not None:
            if existing["kind"] != item["kind"]:
                raise CoordinatorError(
                    f"task check {item['id']} kind does not match project requirement"
                )
            continue
        merged.append(dict(item))
        by_id[item["id"]] = merged[-1]
    return merged


def _normalize_required_checks(items: Any) -> list[dict]:
    """校验并规范化必需检查项。

    规则（P1-1）：
    - 每项必须有稳定的 id，kind ∈ {test, review, check}；
    - 至少一项 test、至少一项 review，避免退化成“只要测试成功”；
    - id 全局唯一。
    """
    if items is None:
        return [dict(x) for x in DEFAULT_REQUIRED_CHECKS]
    if not isinstance(items, (list, tuple)):
        raise CoordinatorError("required_checks must be a list")
    out: list[dict] = []
    seen: set[str] = set()
    for raw in items:
        if isinstance(raw, str):
            raw = {"id": raw, "kind": "test"}
        if not isinstance(raw, dict):
            raise CoordinatorError("required_checks items must be objects")
        check_id = str(raw.get("id", "")).strip()
        kind = str(raw.get("kind", "") or "test").strip()
        if not check_id:
            raise CoordinatorError("required check id is required")
        if check_id in seen:
            raise CoordinatorError(f"duplicate required check id: {check_id}")
        if kind not in CHECK_KINDS:
            raise CoordinatorError(f"invalid required check kind: {kind}")
        seen.add(check_id)
        out.append({"id": check_id, "kind": kind, "title": str(raw.get("title", "") or "")})
    if not out:
        raise CoordinatorError("required_checks must not be empty")
    if not any(item["kind"] == "test" for item in out):
        raise CoordinatorError("required_checks must include at least one test item")
    if not any(item["kind"] == "review" for item in out):
        raise CoordinatorError("required_checks must include at least one review item")
    return out


def _checks_config_hash(checks: Iterable[dict]) -> str:
    """必需项快照指纹；只按 id/kind 排序计算，重排不改变语义。"""
    canonical = sorted(
        ({"id": item["id"], "kind": item["kind"]} for item in checks),
        key=lambda item: item["id"],
    )
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        with self._migration_conn() as conn:
            # executescript() 会隐式提交并结束未完成事务，必须在 BEGIN 之前调用。
            conn.executescript(SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
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
            if "required_checks" not in pcols:
                conn.execute(
                    "ALTER TABLE projects ADD COLUMN required_checks TEXT NOT NULL DEFAULT '"
                    + DEFAULT_REQUIRED_CHECKS_JSON + "'"
                )
            if "required_checks" not in cols:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN required_checks TEXT NOT NULL DEFAULT ''"
                )
            acols = {row["name"] for row in conn.execute("PRAGMA table_info(approvals)")}
            if "expires_at" not in acols:
                conn.execute("ALTER TABLE approvals ADD COLUMN expires_at REAL")
            ccols = {row["name"] for row in conn.execute("PRAGMA table_info(candidates)")}
            if "approver_role" not in ccols:
                conn.execute(
                    "ALTER TABLE candidates ADD COLUMN approver_role TEXT NOT NULL DEFAULT 'reviewer'"
                )
            if "active_batch_id" not in ccols:
                conn.execute("ALTER TABLE candidates ADD COLUMN active_batch_id TEXT")
            ecols = {row["name"] for row in conn.execute("PRAGMA table_info(evidence)")}
            for col, ddl in (
                ("check_id", "TEXT NOT NULL DEFAULT ''"),
                ("batch_id", "TEXT"),
                ("seq", "INTEGER"),
                ("status", "TEXT NOT NULL DEFAULT 'valid'"),
            ):
                if col not in ecols:
                    conn.execute(f"ALTER TABLE evidence ADD COLUMN {col} {ddl}")
            mcols = {row["name"] for row in conn.execute("PRAGMA table_info(merge_attempts)")}
            for col, ddl in (
                ("repo_path", "TEXT NOT NULL DEFAULT ''"),
                ("target_ref", "TEXT NOT NULL DEFAULT ''"),
                ("target_before_sha", "TEXT NOT NULL DEFAULT ''"),
                ("target_after_sha", "TEXT NOT NULL DEFAULT ''"),
            ):
                if col not in mcols:
                    conn.execute(f"ALTER TABLE merge_attempts ADD COLUMN {col} {ddl}")
            # 新列在旧库上是后加的，索引必须等 ALTER 之后再建。
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_evidence_batch "
                "ON evidence(batch_id, check_id, seq)"
            )
            # 候选批准需要绑定验证批次；旧表的 UNIQUE(candidate_id, role) 会挡住
            # 新批次的同角色重新批准，必须重建表（历史行原样迁移，batch_id 为 NULL）。
            self._migrate_candidate_approvals(conn)

    @contextmanager
    def _migration_conn(self):
        """初始化迁移专用连接：显式事务，失败回滚并保留原始异常类型。"""
        with self._conn() as conn:
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    @staticmethod
    def _migrate_candidate_approvals(conn: sqlite3.Connection) -> None:
        """把旧 candidate_approvals 迁移为按验证批次唯一。

        全过程运行在调用方的显式事务里；失败由调用方回滚，历史行不会丢。
        对上次迁移中断留下的 candidate_approvals_legacy 做补齐校验后再删除，
        无法安全判定的状态明确报错，不静默跳过。
        """
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(candidate_approvals)")
        }
        has_legacy = "candidate_approvals_legacy" in tables

        if has_legacy:
            if "batch_id" not in cols:
                raise CoordinatorError(
                    "candidate_approvals migration state is ambiguous: both "
                    "candidate_approvals_legacy and an old-schema candidate_approvals exist; "
                    "inspect the database before retrying"
                )
            # 补齐上次中断时未复制完的历史行（按主键幂等）。
            conn.execute(
                "INSERT OR IGNORE INTO candidate_approvals"
                "(id, candidate_id, batch_id, approver, role, note, created_at) "
                "SELECT id, candidate_id, NULL, approver, role, note, created_at "
                "FROM candidate_approvals_legacy"
            )
            missing = conn.execute(
                "SELECT COUNT(*) AS n FROM candidate_approvals_legacy l "
                "WHERE NOT EXISTS (SELECT 1 FROM candidate_approvals n WHERE n.id=l.id)"
            ).fetchone()["n"]
            if missing:
                raise CoordinatorError(
                    "candidate_approvals migration incomplete: "
                    f"{missing} legacy row(s) missing from the target table"
                )
            # 只比较 ID 会把“目标已有一条同 ID 但内容不同”的行当成已复制。
            # 删除 legacy 之前逐字段核对，冲突时保留两表并报错，不自动覆盖。
            conflicts = conn.execute(
                "SELECT l.id AS id FROM candidate_approvals_legacy l "
                "JOIN candidate_approvals n ON n.id = l.id "
                "WHERE n.batch_id IS NOT NULL "
                "   OR n.candidate_id IS NOT l.candidate_id "
                "   OR n.approver IS NOT l.approver "
                "   OR n.role IS NOT l.role "
                "   OR n.note IS NOT l.note "
                "   OR n.created_at IS NOT l.created_at "
                "ORDER BY l.id LIMIT 5"
            ).fetchall()
            if conflicts:
                ids = ", ".join(row["id"] for row in conflicts)
                raise CoordinatorError(
                    "candidate_approvals migration conflict: target rows differ from "
                    f"legacy history for id(s) {ids}; both tables were preserved, "
                    "reconcile manually before retrying"
                )
            conn.execute("DROP TABLE candidate_approvals_legacy")
        elif "batch_id" not in cols:
            conn.execute(
                "ALTER TABLE candidate_approvals RENAME TO candidate_approvals_legacy"
            )
            conn.execute(
                "CREATE TABLE candidate_approvals ("
                "id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, batch_id TEXT, "
                "approver TEXT NOT NULL, role TEXT NOT NULL, "
                "note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, "
                "UNIQUE(candidate_id, batch_id, role))"
            )
            conn.execute(
                "INSERT INTO candidate_approvals"
                "(id, candidate_id, batch_id, approver, role, note, created_at) "
                "SELECT id, candidate_id, NULL, approver, role, note, created_at "
                "FROM candidate_approvals_legacy"
            )
            missing = conn.execute(
                "SELECT COUNT(*) AS n FROM candidate_approvals_legacy l "
                "WHERE NOT EXISTS (SELECT 1 FROM candidate_approvals n WHERE n.id=l.id)"
            ).fetchone()["n"]
            if missing:
                raise CoordinatorError(
                    "candidate_approvals migration incomplete: "
                    f"{missing} legacy row(s) missing from the target table"
                )
            conn.execute("DROP TABLE candidate_approvals_legacy")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_approvals "
            "ON candidate_approvals(candidate_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_approvals_batch "
            "ON candidate_approvals(batch_id, candidate_id)"
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
            "required_checks": _json_load(row["required_checks"], []),
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
        required_checks: Iterable[dict] | None = None,
    ) -> dict:
        if required_approver_role not in ("reviewer", "admin"):
            raise CoordinatorError(f"invalid required approver role: {required_approver_role}")
        chain = (approval_chain or "").strip()
        chain_roles = [r.strip() for r in chain.split(",") if r.strip()] if chain else []
        for item in chain_roles:
            if item not in APPROVER_ROLE_RANK:
                raise CoordinatorError(f"invalid approval chain role: {item}")
        checks = _normalize_required_checks(required_checks)
        pid = "P-" + uuid.uuid4().hex[:8]
        now = _now()
        approvals_required = max(1, int(approvals_required))
        approval_ttl_seconds = max(0.001, float(approval_ttl_seconds))
        budget = None if budget_usd is None else max(0.0, float(budget_usd))
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO projects(id, name, require_approval, require_verification, approvals_required, approval_ttl_seconds, required_approver_role, approval_chain, required_checks, budget_usd, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (pid, name, 1 if require_approval else 0, 1 if require_verification else 0,
                 approvals_required, approval_ttl_seconds, required_approver_role, chain,
                 json.dumps(checks, ensure_ascii=False), budget, now),
            )
            self._event(conn, "project_created", None, "system",
                        {"project_id": pid, "name": name,
                         "require_approval": bool(require_approval),
                         "require_verification": bool(require_verification),
                         "approvals_required": approvals_required,
                         "approval_ttl_seconds": approval_ttl_seconds,
                         "required_approver_role": required_approver_role,
                         "approval_chain": chain_roles,
                         "required_checks": checks,
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
            "required_checks": checks,
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
                "required_checks": _json_load(r["required_checks"], []),
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
        required_checks: Iterable[dict] | None = None,
    ) -> dict:
        tid = "T-" + uuid.uuid4().hex[:8]
        now = _now()
        ws = list(dict.fromkeys(write_set or []))
        dp = list(dict.fromkeys(deps or []))
        ck = list(checks or [])
        task_checks: list[dict] = []
        if required_checks is not None:
            # 任务只能追加检查项；test/review 的最少项由项目要求保证。
            task_checks = _normalize_check_items(required_checks)
        with self._tx() as conn:
            project = conn.execute(
                "SELECT required_checks FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if project is None:
                raise CoordinatorError(f"unknown project: {project_id}")
            _merge_required_checks(
                _normalize_required_checks(
                    _json_load(project["required_checks"], DEFAULT_REQUIRED_CHECKS)
                ),
                task_checks,
            )
            for dep in dp:
                if conn.execute("SELECT 1 FROM tasks WHERE id=?", (dep,)).fetchone() is None:
                    raise CoordinatorError(f"unknown dependency: {dep}")
            conn.execute(
                "INSERT INTO tasks(id, project_id, title, description, kind, status, repo_id, write_set, deps, dod, checks, required_checks, priority, created_at, updated_at) "
                "VALUES (?,?,?,?,?, 'ready', ?,?,?,?,?,?,?,?,?)",
                (tid, project_id, title, description, kind, repo_id, json.dumps(ws, ensure_ascii=False),
                 json.dumps(dp, ensure_ascii=False), dod, json.dumps(ck, ensure_ascii=False),
                 json.dumps(task_checks, ensure_ascii=False) if task_checks else "",
                 int(priority), now, now),
            )
            self._event(conn, "task_created", tid, "system",
                        {"project_id": project_id, "title": title, "kind": kind,
                         "write_set": ws, "deps": dp, "checks": len(ck),
                         "required_checks": task_checks, "repo_id": repo_id})
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
            "active_batch_id": row["active_batch_id"],
            "meta": _json_load(row["meta"], {}),
        }

    @staticmethod
    def _evidence(row: sqlite3.Row) -> dict:
        return {
            "evidence_id": row["id"],
            "candidate_id": row["candidate_id"],
            "execution_id": row["execution_id"],
            "check_id": row["check_id"],
            "batch_id": row["batch_id"],
            "seq": row["seq"],
            "type": row["type"],
            "command": row["command"],
            "cwd": row["cwd"],
            "exit_code": row["exit_code"],
            "output_ref": row["output_ref"],
            "sha": row["sha"],
            "status": row["status"],
            "created_at": _iso(row["created_at"]),
        }

    @staticmethod
    def _merge_attempt(row: sqlite3.Row) -> dict:
        return {
            "attempt_id": row["id"],
            "candidate_id": row["candidate_id"],
            "expected_base_sha": row["expected_base_sha"],
            "repo_path": row["repo_path"],
            "target_ref": row["target_ref"],
            "target_before_sha": row["target_before_sha"],
            "target_after_sha": row["target_after_sha"],
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
        check_id: str = "",
        batch_id: str | None = None,
        status: str = "valid",
    ) -> dict:
        """记录证据。

        - 带 check_id 的证据属于门禁证据：必须绑定当前 candidate SHA，并落在打开的验证批次；
        - 不带 check_id 的证据只作记录，不能替代必需检查；
        - status='void' 表示该条结果作废，不参与门禁。
        """
        if status not in ("valid", "void"):
            raise CoordinatorError(f"invalid evidence status: {status}")
        check_id = (check_id or "").strip()
        if batch_id and not check_id:
            raise CoordinatorError("batch_id requires check_id")
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
            batch = None
            seq = None
            if check_id:
                required = self._required_checks_for_task(conn, candidate["task_id"])
                required_item = next(
                    (item for item in required if item["id"] == check_id), None
                )
                if required_item is not None and type != required_item["kind"]:
                    # 类别规范：证据类别必须与检查项 kind 一致，普通测试不能冒充审查。
                    raise CoordinatorError(
                        "evidence type does not match required check kind: "
                        f"{type} != {required_item['kind']} ({check_id})"
                    )
                if not sha:
                    raise CoordinatorError("evidence for a check must bind candidate SHA")
                if sha != candidate["candidate_sha"]:
                    raise CoordinatorError(
                        "evidence SHA does not match candidate SHA: "
                        f"{sha} != {candidate['candidate_sha']}"
                    )
                if batch_id:
                    batch = conn.execute(
                        "SELECT * FROM validation_batches WHERE id=? AND candidate_id=?",
                        (batch_id, candidate_id),
                    ).fetchone()
                    if batch is None:
                        raise CoordinatorError(f"unknown validation batch for candidate: {batch_id}")
                    if batch["status"] != "open":
                        raise CoordinatorError(f"validation batch is not open: {batch['status']}")
                else:
                    batch = self._active_batch_row(conn, candidate)
                    if batch is None:
                        raise CoordinatorError(
                            "candidate has no active validation batch; call begin_validation_batch"
                        )
                if batch["candidate_sha"] != candidate["candidate_sha"]:
                    raise CoordinatorError("validation batch belongs to another candidate SHA")
                seq = int(batch["next_seq"]) + 1
                conn.execute(
                    "UPDATE validation_batches SET next_seq=? WHERE id=?", (seq, batch["id"])
                )
            evidence_id = "EV-" + uuid.uuid4().hex[:8]
            conn.execute(
                "INSERT INTO evidence(id, candidate_id, execution_id, check_id, batch_id, seq, "
                "type, command, cwd, exit_code, output_ref, sha, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (evidence_id, candidate_id, execution_id, check_id,
                 batch["id"] if batch is not None else None, seq, type, command, cwd,
                 exit_code, output_ref, sha, status, now),
            )
            self._event(conn, "evidence_recorded", candidate["task_id"], "system",
                        {"evidence_id": evidence_id, "candidate_id": candidate_id,
                         "check_id": check_id,
                         "batch_id": batch["id"] if batch is not None else None,
                         "seq": seq, "type": type, "exit_code": exit_code,
                         "sha": sha, "status": status})
            row = conn.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
            return self._evidence(row)

    def list_evidence(self, candidate_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM evidence WHERE candidate_id=? "
                "ORDER BY created_at, COALESCE(seq, 0), id",
                (candidate_id,),
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
                "batch_id": r["batch_id"],
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

    # ---------- Candidate 证据门禁（P1-1） ----------
    @staticmethod
    def _batch(row: sqlite3.Row) -> dict:
        return {
            "batch_id": row["id"],
            "candidate_id": row["candidate_id"],
            "candidate_sha": row["candidate_sha"],
            "base_sha": row["base_sha"],
            "config_hash": row["config_hash"],
            "required_checks": _json_load(row["required_checks"], []),
            "attempt": row["attempt"],
            "status": row["status"],
            "next_seq": row["next_seq"],
            "created_at": _iso(row["created_at"]),
            "closed_at": _iso(row["closed_at"]),
            "close_reason": row["close_reason"],
        }

    @staticmethod
    def _required_checks_for_task(conn: sqlite3.Connection, task_id: str) -> list[dict]:
        row = conn.execute(
            "SELECT t.required_checks AS task_checks, p.required_checks AS project_checks "
            "FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.id=?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise CoordinatorError(f"unknown task: {task_id}")
        project_checks = _normalize_required_checks(
            _json_load(row["project_checks"], DEFAULT_REQUIRED_CHECKS)
        )
        task_checks = _normalize_check_items(_json_load(row["task_checks"], []))
        # 任务配置只作为“追加项”：项目必需项始终保留，后续项目新增项也自动生效。
        return _merge_required_checks(project_checks, task_checks)

    def required_checks_for_task(self, task_id: str) -> list[dict]:
        """任务实际生效的必需检查项：项目要求 + 任务追加，任务不能删项。"""
        with self._conn() as conn:
            return self._required_checks_for_task(conn, task_id)

    def _create_batch(
        self,
        conn: sqlite3.Connection,
        candidate: sqlite3.Row,
        checks: list[dict],
        reason: str,
    ) -> sqlite3.Row:
        now = _now()
        attempt = conn.execute(
            "SELECT COALESCE(MAX(attempt), 0) + 1 AS a FROM validation_batches WHERE candidate_id=?",
            (candidate["id"],),
        ).fetchone()["a"]
        batch_id = "VB-" + uuid.uuid4().hex[:8]
        conn.execute(
            "INSERT INTO validation_batches(id, candidate_id, candidate_sha, base_sha, config_hash, "
            "required_checks, attempt, status, next_seq, created_at) "
            "VALUES (?,?,?,?,?,?,?, 'open', 0, ?)",
            (batch_id, candidate["id"], candidate["candidate_sha"], candidate["base_sha"],
             _checks_config_hash(checks), json.dumps(checks, ensure_ascii=False), attempt, now),
        )
        conn.execute(
            "UPDATE candidates SET active_batch_id=? WHERE id=?", (batch_id, candidate["id"])
        )
        self._event(conn, "validation_batch_started", candidate["task_id"], "system",
                    {"candidate_id": candidate["id"], "batch_id": batch_id, "attempt": attempt,
                     "candidate_sha": candidate["candidate_sha"], "base_sha": candidate["base_sha"],
                     "required_checks": checks, "reason": reason})
        return conn.execute(
            "SELECT * FROM validation_batches WHERE id=?", (batch_id,)
        ).fetchone()

    def _active_batch_row(
        self, conn: sqlite3.Connection, candidate: sqlite3.Row
    ) -> sqlite3.Row | None:
        if candidate["active_batch_id"]:
            row = conn.execute(
                "SELECT * FROM validation_batches WHERE id=?", (candidate["active_batch_id"],)
            ).fetchone()
            if row is not None:
                return row
        return conn.execute(
            "SELECT * FROM validation_batches WHERE candidate_id=? AND status='open' "
            "ORDER BY attempt DESC LIMIT 1",
            (candidate["id"],),
        ).fetchone()

    def begin_validation_batch(self, candidate_id: str, reason: str = "") -> dict:
        """为候选开启新的验证批次；旧批次被标记 superseded，其证据不再参与门禁。"""
        with self._tx() as conn:
            candidate = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if candidate is None:
                raise CoordinatorError(f"unknown candidate: {candidate_id}")
            if candidate["status"] in ("approved", "merged", "rejected", "invalidated"):
                raise CoordinatorError(f"candidate is already {candidate['status']}")
            checks = self._required_checks_for_task(conn, candidate["task_id"])
            now = _now()
            conn.execute(
                "UPDATE validation_batches SET status='superseded', closed_at=?, close_reason=? "
                "WHERE candidate_id=? AND status='open'",
                (now, reason or "superseded", candidate_id),
            )
            row = self._create_batch(conn, candidate, checks, reason or "manual")
            return self._batch(row)

    def list_validation_batches(self, candidate_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM validation_batches WHERE candidate_id=? ORDER BY attempt",
                (candidate_id,),
            ).fetchall()
        return [self._batch(row) for row in rows]

    def set_required_checks(self, project_id: str, required_checks: Iterable[dict]) -> dict:
        """更新项目必需项；已有批次的 config_hash 会失配，需重新开批次。"""
        checks = _normalize_required_checks(required_checks)
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise CoordinatorError(f"unknown project: {project_id}")
            conn.execute(
                "UPDATE projects SET required_checks=? WHERE id=?",
                (json.dumps(checks, ensure_ascii=False), project_id),
            )
            self._event(conn, "required_checks_updated", None, "system",
                        {"project_id": project_id, "required_checks": checks})
        return {"project_id": project_id, "required_checks": checks}

    def void_evidence(self, evidence_id: str, reason: str = "") -> dict:
        """把一条证据标记为 void；门禁只认 status='valid' 的最新结果。"""
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
            if row is None:
                raise CoordinatorError(f"unknown evidence: {evidence_id}")
            if row["status"] != "void":
                conn.execute("UPDATE evidence SET status='void' WHERE id=?", (evidence_id,))
                candidate = conn.execute(
                    "SELECT task_id FROM candidates WHERE id=?", (row["candidate_id"],)
                ).fetchone()
                self._event(conn, "evidence_voided", candidate["task_id"] if candidate else None,
                            "system", {"evidence_id": evidence_id,
                                       "candidate_id": row["candidate_id"], "reason": reason})
            updated = conn.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
            return self._evidence(updated)

    @staticmethod
    def _gate_error(gate: dict) -> str:
        parts = []
        if gate["stale"]:
            parts.append("stale=" + ",".join(gate["stale"]))
        if gate["missing"]:
            parts.append("missing=" + ",".join(gate["missing"]))
        if gate["invalid"]:
            parts.append("invalid=" + ",".join(gate["invalid"]))
        if gate["failed"]:
            parts.append("failed=" + ",".join(gate["failed"]))
        if not parts:
            parts.append(gate.get("reason") or "required checks not satisfied")
        return "candidate gate failed: " + "; ".join(parts)

    def evaluate_candidate_gate(self, candidate_id: str) -> dict:
        """只读评估：当前候选的必需检查是否全部有效通过。"""
        with self._conn() as conn:
            return self._evaluate_gate(conn, candidate_id)

    def _evaluate_gate(self, conn: sqlite3.Connection, candidate_id: str) -> dict:
        candidate = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if candidate is None:
            raise CoordinatorError(f"unknown candidate: {candidate_id}")
        checks = self._required_checks_for_task(conn, candidate["task_id"])
        batch = self._active_batch_row(conn, candidate)
        result: dict[str, Any] = {
            "candidate_id": candidate_id,
            "candidate_sha": candidate["candidate_sha"],
            "base_sha": candidate["base_sha"],
            "batch_id": batch["id"] if batch is not None else None,
            "batch_attempt": batch["attempt"] if batch is not None else None,
            "batch_status": batch["status"] if batch is not None else None,
            "required_checks": checks,
            "items": [],
            "missing": [],
            "invalid": [],
            "failed": [],
            "stale": [],
            "ok": False,
            "reason": "",
        }
        if batch is None:
            result["missing"] = [item["id"] for item in checks]
            result["items"] = [
                {"id": item["id"], "kind": item["kind"], "status": "missing",
                 "reason": "no active validation batch"}
                for item in checks
            ]
            result["reason"] = "no active validation batch; call begin_validation_batch"
            return result

        stale = result["stale"]
        if batch["status"] != "open":
            stale.append(f"batch_status={batch['status']}")
        if batch["candidate_sha"] != candidate["candidate_sha"]:
            stale.append("candidate_sha_changed")
        if batch["base_sha"] != candidate["base_sha"]:
            stale.append("base_sha_changed")
        if batch["config_hash"] != _checks_config_hash(checks):
            stale.append("required_checks_changed")

        for item in checks:
            # 取该检查项最新一条记录（不按 status 过滤）：最新结果被作废时必须等待重新验证，
            # 不能回退到更早的成功记录。
            row = conn.execute(
                "SELECT * FROM evidence WHERE candidate_id=? AND batch_id=? AND check_id=? "
                "ORDER BY seq DESC, id DESC LIMIT 1",
                (candidate_id, batch["id"], item["id"]),
            ).fetchone()
            entry: dict[str, Any] = {
                "id": item["id"], "kind": item["kind"], "status": "missing",
                "evidence_id": None, "exit_code": None, "sha": None, "reason": "",
            }
            if row is None:
                result["missing"].append(item["id"])
                entry["reason"] = "no evidence in current batch"
            else:
                entry.update(evidence_id=row["id"], exit_code=row["exit_code"], sha=row["sha"])
                if row["status"] != "valid":
                    entry["status"] = "void"
                    entry["reason"] = "latest evidence was voided; revalidation required"
                    result["invalid"].append(item["id"])
                elif row["type"] != item["kind"]:
                    entry["status"] = "kind_mismatch"
                    entry["reason"] = (
                        f"evidence type {row['type']} does not match required kind {item['kind']}"
                    )
                    result["invalid"].append(item["id"])
                elif row["sha"] != candidate["candidate_sha"]:
                    entry["status"] = "invalid_sha"
                    entry["reason"] = "evidence is bound to another candidate SHA"
                    result["missing"].append(item["id"])
                elif row["exit_code"] is None or int(row["exit_code"]) != 0:
                    entry["status"] = "failed"
                    entry["reason"] = f"exit_code={row['exit_code']}"
                    result["failed"].append(item["id"])
                else:
                    entry["status"] = "passed"
            result["items"].append(entry)

        result["ok"] = (
            not stale and not result["missing"] and not result["invalid"]
            and not result["failed"]
        )
        if not result["ok"]:
            result["reason"] = "required checks not satisfied"
        return result

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
            checks = self._required_checks_for_task(conn, candidate["task_id"])
            batch = self._create_batch(conn, candidate, checks, "candidate_frozen")
            self._event(conn, "candidate_frozen", candidate["task_id"], "system",
                        {"candidate_id": candidate_id, "candidate_sha": candidate["candidate_sha"],
                         "batch_id": batch["id"]})
            row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            result = self._candidate(row)
            result["validation_batch"] = self._batch(batch)
            return result

    def mark_candidate_ready(self, candidate_id: str) -> dict:
        with self._tx() as conn:
            candidate = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if candidate is None:
                raise CoordinatorError(f"unknown candidate: {candidate_id}")
            if candidate["status"] != "validating":
                raise CoordinatorError(f"candidate is not validating: {candidate['status']}")
            gate = self._evaluate_gate(conn, candidate_id)
            if not gate["ok"]:
                raise CoordinatorError(self._gate_error(gate))
            conn.execute("UPDATE candidates SET status='awaiting_human' WHERE id=?", (candidate_id,))
            self._transition_in_tx(conn, candidate["task_id"], "human_review", "candidate_ready")
            self._event(conn, "candidate_ready", candidate["task_id"], "system",
                        {"candidate_id": candidate_id, "candidate_sha": candidate["candidate_sha"],
                         "batch_id": gate["batch_id"],
                         "required_checks": [item["id"] for item in gate["items"]]})
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
            # 与 mark_candidate_ready 共用同一门禁；进入 awaiting_human 之后新增的有效失败同样会阻止批准。
            gate = self._evaluate_gate(conn, candidate_id)
            if not gate["ok"]:
                raise CoordinatorError(self._gate_error(gate))
            batch = self._active_batch_row(conn, candidate)
            if batch is None:
                raise CoordinatorError("candidate has no active validation batch")

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
                    "SELECT role FROM candidate_approvals WHERE candidate_id=? AND batch_id=?",
                    (candidate_id, batch["id"]),
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
                        "INSERT INTO candidate_approvals(id, candidate_id, batch_id, approver, role, note, created_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        ("CA-" + uuid.uuid4().hex[:8], candidate_id, batch["id"],
                         approver, role, note, now),
                    )
            else:
                pending = [item for item in chain if item not in recorded]
                if not pending:
                    raise CoordinatorError("all approval stages already recorded")
                if role != pending[0]:
                    raise CoordinatorError(f"next approval role is {pending[0]}; got {role}")
                conn.execute(
                    "INSERT INTO candidate_approvals(id, candidate_id, batch_id, approver, role, note, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    ("CA-" + uuid.uuid4().hex[:8], candidate_id, batch["id"],
                     approver, role, note, now),
                )
                if len(pending) > 1:
                    self._event(conn, "candidate_approval_recorded", candidate["task_id"], approver,
                                {"candidate_id": candidate_id, "role": role,
                                 "batch_id": batch["id"],
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
            conn.execute(
                "UPDATE validation_batches SET status='closed', closed_at=?, close_reason='candidate_approved' "
                "WHERE candidate_id=? AND status='open'",
                (now, candidate_id),
            )
            self._transition_in_tx(conn, candidate["task_id"], "ready_to_merge", "candidate_approved")
            self._event(conn, "candidate_approved", candidate["task_id"], approver,
                        {"candidate_id": candidate_id, "candidate_sha": candidate["candidate_sha"],
                         "note": note, "role": role, "chain": chain,
                         "batch_id": batch["id"]})
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
            conn.execute(
                "UPDATE validation_batches SET status='closed', closed_at=?, close_reason='candidate_rejected' "
                "WHERE candidate_id=? AND status='open'",
                (_now(), candidate_id),
            )
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
            conn.execute(
                "UPDATE validation_batches SET status='superseded', closed_at=?, "
                "close_reason='candidate_invalidated' WHERE candidate_id=? AND status='open'",
                (_now(), candidate_id),
            )
            try:
                self._transition_in_tx(conn, candidate["task_id"], "needs_fix", "candidate_invalidated")
            except CoordinatorError:
                pass
            self._event(conn, "candidate_invalidated", candidate["task_id"], "system",
                        {"candidate_id": candidate_id, "reason": reason})
            row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            return self._candidate(row)

    def _candidate_repo_path(
        self, conn: sqlite3.Connection, candidate: sqlite3.Row, repo_path: str | None = None,
    ) -> Path:
        """解析候选所属仓库的顶层路径；优先显式路径，其次最近的工作区。"""
        if repo_path:
            candidate_path = Path(repo_path).expanduser().resolve()
        else:
            row = conn.execute(
                "SELECT repo_path FROM workspaces WHERE task_id=? AND repo_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (candidate["task_id"], candidate["repo_id"]),
            ).fetchone()
            if row is None:
                raise CoordinatorError(
                    "repo_path required: candidate has no workspace with a repo path"
                )
            candidate_path = Path(row["repo_path"]).resolve()
        if not candidate_path.is_dir():
            raise CoordinatorError(f"repo path not found: {candidate_path}")
        try:
            top = self._git(candidate_path, "rev-parse", "--show-toplevel").stdout.strip()
        except CoordinatorError as exc:
            raise CoordinatorError(f"not a git repository: {candidate_path}") from exc
        return Path(top).resolve()

    def _resolve_local_target(self, repo_dir: Path, target_ref: str) -> tuple[str, str]:
        """把 target_ref 解析成本地分支的完整 ref 和当前 HEAD SHA。

        只接受本地分支或 HEAD；远程跟踪分支、tag 和 detached HEAD 都拒绝，
        避免 finish_merge 往一个不可写的 ref 上校验或更新。
        """
        ref = (target_ref or "HEAD").strip()
        if not ref:
            raise CoordinatorError("target_ref must not be empty")
        try:
            full = self._git(repo_dir, "rev-parse", "--symbolic-full-name", ref).stdout.strip()
        except CoordinatorError as exc:
            raise CoordinatorError(f"target ref not found: {ref}") from exc
        if full == "HEAD":
            branch = self._git(
                repo_dir, "symbolic-ref", "--short", "-q", "HEAD", check=False
            ).stdout.strip()
            if not branch:
                raise CoordinatorError("target ref HEAD is detached; specify a local branch")
            full = f"refs/heads/{branch}"
        if not full.startswith("refs/heads/"):
            raise CoordinatorError(f"target ref must be a local branch or HEAD: {ref}")
        sha = self._git(repo_dir, "rev-parse", "--verify", f"{full}^{{commit}}").stdout.strip()
        return full, sha

    def _advance_target_ref(self, attempt: sqlite3.Row, result_sha: str) -> str:
        """把目标分支推进到 result_sha，并返回实际观察到的目标 HEAD。

        只允许 fast-forward；如果目标分支在候选生成/开始合入后前进过，或者
        结果 SHA 不是目标基线的后代，直接拒绝，不能把“临时分支成功”当成合入。
        """
        repo_path = attempt["repo_path"]
        target_ref = attempt["target_ref"]
        before = attempt["target_before_sha"]
        if not repo_path or not target_ref or not before:
            raise CoordinatorError(
                "merge attempt has no target repo/ref/base; cannot verify target branch"
            )
        if not result_sha:
            raise CoordinatorError("result_sha is required for a succeeded merge")
        if result_sha == before:
            raise CoordinatorError("target branch did not change; refusing to mark merged")

        repo_dir = Path(repo_path).resolve()
        full, actual = self._resolve_local_target(repo_dir, target_ref)
        if full != target_ref:
            raise CoordinatorError(f"target ref changed: {target_ref} -> {full}")
        if actual != before and actual != result_sha:
            raise CoordinatorError(
                f"target branch moved during merge: expected {before}, found {actual}"
            )

        try:
            self._git(repo_dir, "cat-file", "-e", f"{result_sha}^{{commit}}")
            ancestor = self._git(
                repo_dir, "merge-base", "--is-ancestor", before, result_sha, check=False
            )
        except CoordinatorError as exc:
            raise CoordinatorError(f"invalid merge result: {exc}") from exc
        if ancestor.returncode != 0:
            raise CoordinatorError("merge result is not a descendant of the target base")
        if actual == result_sha:
            return actual

        current = self._git(
            repo_dir, "symbolic-ref", "--short", "-q", "HEAD", check=False
        ).stdout.strip()
        if current and f"refs/heads/{current}" == full:
            # 目标分支已检出：用 ff-only merge 同时更新工作树和分支。
            self._git(repo_dir, "merge", "--ff-only", result_sha)
        else:
            # 目标分支未检出：用 compare-and-swap 更新 ref，避免覆盖并发前进。
            self._git(repo_dir, "update-ref", full, result_sha, before)

        _, after = self._resolve_local_target(repo_dir, full)
        if after != result_sha:
            raise CoordinatorError(
                f"target ref {full} is {after} after merge; expected {result_sha}"
            )
        return after

    def _fail_merge_attempt(self, attempt_id: str, result_sha: str, error: str) -> dict:
        """合入校验失败：记录 failed，任务退回 needs_fix，目标分支不动。"""
        now = _now()
        with self._tx() as conn:
            attempt = conn.execute("SELECT * FROM merge_attempts WHERE id=?", (attempt_id,)).fetchone()
            if attempt is None:
                raise CoordinatorError(f"unknown merge attempt: {attempt_id}")
            if attempt["status"] != "pending":
                raise CoordinatorError(f"merge attempt is not pending: {attempt['status']}")
            conn.execute(
                "UPDATE merge_attempts SET status='failed', result_sha=?, error=?, finished_at=? "
                "WHERE id=?",
                (result_sha, error, now, attempt_id),
            )
            candidate = conn.execute(
                "SELECT * FROM candidates WHERE id=?", (attempt["candidate_id"],)
            ).fetchone()
            if candidate is not None:
                try:
                    self._transition_in_tx(
                        conn, candidate["task_id"], "needs_fix", "merge_verification_failed"
                    )
                except CoordinatorError:
                    pass
                self._event(
                    conn, "merge_failed", candidate["task_id"], attempt["requested_by"],
                    {"attempt_id": attempt_id, "candidate_id": attempt["candidate_id"],
                     "result_sha": result_sha, "error": error,
                     "target_ref": attempt["target_ref"],
                     "target_before_sha": attempt["target_before_sha"]},
                )
            row = conn.execute("SELECT * FROM merge_attempts WHERE id=?", (attempt_id,)).fetchone()
            return self._merge_attempt(row)

    def start_merge(
        self,
        candidate_id: str,
        expected_base_sha: str,
        requested_by: str = "human",
        repo_path: str | None = None,
        target_ref: str = "HEAD",
    ) -> dict:
        """创建 MergeAttempt，并校验目标分支实际 HEAD。

        目标分支当前 HEAD 必须等于候选的 base SHA；候选生成后目标分支前进过，
        必须先重新验证候选，不能直接合入。
        """
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

            repo_dir = self._candidate_repo_path(conn, candidate, repo_path)
            full_ref, target_sha = self._resolve_local_target(repo_dir, target_ref)
            if target_sha != candidate["base_sha"]:
                raise CoordinatorError(
                    f"target {full_ref} HEAD {target_sha} != candidate base "
                    f"{candidate['base_sha']}; candidate must be revalidated"
                )

            attempt_id = "M-" + uuid.uuid4().hex[:8]
            conn.execute(
                "INSERT INTO merge_attempts(id, candidate_id, expected_base_sha, repo_path, "
                "target_ref, target_before_sha, requested_by, status, created_at) "
                "VALUES (?,?,?,?,?,?,?, 'pending', ?)",
                (attempt_id, candidate_id, expected_base_sha, str(repo_dir),
                 full_ref, target_sha, requested_by, now),
            )
            self._event(
                conn, "merge_started", candidate["task_id"], requested_by,
                {"attempt_id": attempt_id, "candidate_id": candidate_id,
                 "expected_base_sha": expected_base_sha,
                 "repo_path": str(repo_dir), "target_ref": full_ref,
                 "target_before_sha": target_sha},
            )
            row = conn.execute("SELECT * FROM merge_attempts WHERE id=?", (attempt_id,)).fetchone()
            return self._merge_attempt(row)

    def finish_merge(
        self,
        attempt_id: str,
        status: str = "succeeded",
        result_sha: str = "",
        error: str = "",
    ) -> dict:
        """结束 MergeAttempt；succeeded 必须通过目标分支校验并实际推进 ref。"""
        if status not in ("succeeded", "failed", "unknown"):
            raise CoordinatorError(f"invalid merge status: {status}")
        with self._conn() as conn:
            attempt = conn.execute("SELECT * FROM merge_attempts WHERE id=?", (attempt_id,)).fetchone()
            candidate = None
            if attempt is not None:
                candidate = conn.execute(
                    "SELECT * FROM candidates WHERE id=?", (attempt["candidate_id"],)
                ).fetchone()
        if attempt is None:
            raise CoordinatorError(f"unknown merge attempt: {attempt_id}")
        if attempt["status"] != "pending":
            raise CoordinatorError(f"merge attempt is not pending: {attempt['status']}")

        if status == "succeeded":
            if candidate is None or candidate["status"] != "approved":
                raise CoordinatorError("candidate is not approved; cannot confirm merge")
            if not result_sha:
                return self._fail_merge_attempt(
                    attempt_id, result_sha, "result_sha is required for a succeeded merge"
                )
            recorded = attempt["result_sha"] or ""
            if not recorded:
                return self._fail_merge_attempt(
                    attempt_id, result_sha,
                    "no merge result recorded; run merge_candidate_git first",
                )
            if result_sha != recorded:
                return self._fail_merge_attempt(
                    attempt_id, result_sha,
                    "result_sha does not match the recorded merge result",
                )
            try:
                target_after = self._advance_target_ref(attempt, result_sha)
            except CoordinatorError as exc:
                return self._fail_merge_attempt(attempt_id, result_sha, str(exc))

            now = _now()
            with self._tx() as conn:
                current = conn.execute(
                    "SELECT * FROM merge_attempts WHERE id=?", (attempt_id,)
                ).fetchone()
                if current is None or current["status"] != "pending":
                    raise CoordinatorError("merge attempt changed while verifying target ref")
                conn.execute(
                    "UPDATE merge_attempts SET status='succeeded', result_sha=?, "
                    "target_after_sha=?, error='', finished_at=? WHERE id=?",
                    (result_sha, target_after, now, attempt_id),
                )
                conn.execute(
                    "UPDATE candidates SET status='merged', merged_at=? WHERE id=?",
                    (now, attempt["candidate_id"]),
                )
                self._transition_in_tx(conn, candidate["task_id"], "accepted", "merge_succeeded")
                self._event(
                    conn, "merge_succeeded", candidate["task_id"], attempt["requested_by"],
                    {"attempt_id": attempt_id, "candidate_id": attempt["candidate_id"],
                     "result_sha": result_sha, "target_ref": attempt["target_ref"],
                     "target_before_sha": attempt["target_before_sha"],
                     "target_after_sha": target_after},
                )
                row = conn.execute("SELECT * FROM merge_attempts WHERE id=?", (attempt_id,)).fetchone()
                return self._merge_attempt(row)

        now = _now()
        with self._tx() as conn:
            current = conn.execute("SELECT * FROM merge_attempts WHERE id=?", (attempt_id,)).fetchone()
            if current is None or current["status"] != "pending":
                raise CoordinatorError("merge attempt changed while finishing")
            conn.execute(
                "UPDATE merge_attempts SET status=?, result_sha=?, error=?, finished_at=? WHERE id=?",
                (status, result_sha, error, now, attempt_id),
            )
            candidate_row = conn.execute(
                "SELECT * FROM candidates WHERE id=?", (attempt["candidate_id"],)
            ).fetchone()
            if candidate_row is not None and status == "failed":
                try:
                    self._transition_in_tx(
                        conn, candidate_row["task_id"], "needs_fix", "merge_failed"
                    )
                except CoordinatorError:
                    pass
            if candidate_row is not None:
                self._event(
                    conn, f"merge_{status}", candidate_row["task_id"], attempt["requested_by"],
                    {"attempt_id": attempt_id, "candidate_id": attempt["candidate_id"],
                     "result_sha": result_sha, "error": error,
                     "target_ref": attempt["target_ref"]},
                )
            row = conn.execute("SELECT * FROM merge_attempts WHERE id=?", (attempt_id,)).fetchone()
            return self._merge_attempt(row)

    def merge_candidate_git(
        self,
        candidate_id: str,
        repo_path: str,
        worktree_path: str,
        check_command: list[str] | None = None,
        timeout: float = 120,
        target_ref: str | None = None,
    ) -> dict:
        """真实 Git cherry-pick：从目标分支 HEAD 建集成分支，不推进目标分支。

        目标 HEAD 必须等于候选 base，且与 start_merge 记录的 target_before_sha
        一致；返回的 result_sha 由 finish_merge 校验并推进目标 ref。
        """
        with self._conn() as conn:
            candidate = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            attempt = conn.execute(
                "SELECT * FROM merge_attempts WHERE candidate_id=? AND status='pending' "
                "ORDER BY created_at DESC LIMIT 1",
                (candidate_id,),
            ).fetchone()
        if candidate is None:
            raise CoordinatorError(f"unknown candidate: {candidate_id}")
        if candidate["status"] != "approved":
            raise CoordinatorError(f"candidate is not approved: {candidate['status']}")
        if attempt is None:
            raise CoordinatorError("no pending merge attempt; call start_merge first")
        if not attempt["repo_path"] or not attempt["target_ref"]:
            raise CoordinatorError(
                "merge attempt has no target repo/ref; call start_merge with repo_path/target_ref"
            )
        if Path(attempt["repo_path"]).resolve() != Path(repo_path).resolve():
            raise CoordinatorError("repo_path does not match the pending merge attempt")
        if target_ref:
            provided_full, _ = self._resolve_local_target(
                Path(attempt["repo_path"]).resolve(), target_ref
            )
            if provided_full != attempt["target_ref"]:
                raise CoordinatorError("target_ref does not match the pending merge attempt")

        repo_dir = Path(attempt["repo_path"]).resolve()
        full_ref, target_sha = self._resolve_local_target(repo_dir, attempt["target_ref"])
        if target_sha != candidate["base_sha"] or target_sha != attempt["target_before_sha"]:
            raise CoordinatorError(
                f"target {full_ref} moved after start_merge: {target_sha} != "
                f"{attempt['target_before_sha']}; revalidate candidate"
            )
        self._git(repo_dir, "cat-file", "-e", f"{candidate['candidate_sha']}^{{commit}}")

        integration = Path(worktree_path).resolve()
        if integration.exists():
            raise CoordinatorError(f"integration path already exists: {integration}")
        branch = f"guild/merge-{candidate_id.lower()}-{uuid.uuid4().hex[:6]}"
        integration.parent.mkdir(parents=True, exist_ok=True)
        self._git(repo_dir, "worktree", "add", "-b", branch, str(integration), target_sha)
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
                updated = conn.execute(
                    "UPDATE merge_attempts SET result_sha=? WHERE id=? AND status='pending'",
                    (result_sha, attempt["id"]),
                )
                if updated.rowcount != 1:
                    raise CoordinatorError("merge attempt changed while preparing result")
                self._event(conn, "merge_git_succeeded", candidate["task_id"], "system", {
                    "candidate_id": candidate_id,
                    "base_sha": candidate["base_sha"],
                    "candidate_sha": candidate["candidate_sha"],
                    "target_ref": full_ref,
                    "target_before_sha": target_sha,
                    "result_sha": result_sha,
                    "branch": branch,
                    "worktree": str(integration),
                })
            return {
                "candidate_id": candidate_id,
                "result_sha": result_sha,
                "target_ref": full_ref,
                "target_before_sha": target_sha,
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
