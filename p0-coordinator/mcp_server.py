"""MCP stdio server：把 Coordinator 暴露成 MCP 工具。

协议：JSON-RPC 2.0，按行分隔（MCP stdio transport）。
启动：python mcp_server.py
环境变量：COORDINATOR_DB（默认 ./data/coordinator.db）
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from coordinator import Coordinator, CoordinatorError
from estimate import summarize
from plan_milp import pareto_front, pareto_front_3d, solve_plan
from verifier import auto_verify

ROOT = Path(__file__).resolve().parent

TOOLS = [
    {
        "name": "create_project",
        "description": "创建一个项目，返回 project_id。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "require_approval": {"type": "boolean"},
                "require_verification": {"type": "boolean"},
                "approvals_required": {"type": "integer"},
                "approval_ttl_seconds": {"type": "number"},
                "required_checks": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "必需检查项；至少一项 kind=test、一项 kind=review",
                },
                "budget_usd": {"type": "number"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "create_task",
        "description": "创建任务：标题、写集（互斥资源）、依赖、DoD。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "title": {"type": "string"},
                "write_set": {"type": "array", "items": {"type": "string"}},
                "deps": {"type": "array", "items": {"type": "string"}},
                "dod": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "integer"},
                "kind": {"type": "string"},
                "checks": {"type": "array", "items": {"type": "object"}},
                "required_checks": {"type": "array", "items": {"type": "object"}},
                "repo_id": {"type": "string"},
            },
            "required": ["project_id", "title"],
        },
    },
    {
        "name": "list_tasks",
        "description": "列出任务，可按项目/状态过滤。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    },
    {
        "name": "claim_task",
        "description": "原子领取任务，返回 task + lease + fencing_token；无可用任务返回 claimed=false。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "worker": {"type": "string"},
                "project_id": {"type": "string"},
                "task_id": {"type": "string"},
                "ttl_seconds": {"type": "number"},
            },
            "required": ["worker"],
        },
    },
    {
        "name": "renew_lease",
        "description": "续约：必须带正确的 fencing_token。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "worker": {"type": "string"},
                "fencing_token": {"type": "integer"},
                "ttl_seconds": {"type": "number"},
            },
            "required": ["task_id", "worker", "fencing_token"],
        },
    },
    {
        "name": "release_lease",
        "description": "主动释放租约，任务回到 ready。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "worker": {"type": "string"},
                "fencing_token": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "worker", "fencing_token"],
        },
    },
    {
        "name": "commit_task",
        "description": "提交任务：校验租约/fencing_token 后标记 done；可附 run 遥测。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "worker": {"type": "string"},
                "fencing_token": {"type": "integer"},
                "result": {"type": "object"},
                "require_approval": {"type": "boolean"},
                "run": {
                    "type": "object",
                    "properties": {
                        "duration_s": {"type": "number"},
                        "input_tokens": {"type": "integer"},
                        "output_tokens": {"type": "integer"},
                        "cost_usd": {"type": "number"},
                        "quality": {"type": "number"},
                        "diff_lines": {"type": "integer"},
                        "files_changed": {"type": "integer"},
                        "status": {"type": "string"},
                        "meta": {"type": "object"},
                    },
                },
            },
            "required": ["task_id", "worker", "fencing_token"],
        },
    },
    {
        "name": "record_run",
        "description": "记录一次运行遥测（token/成本/质量/diff）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "worker": {"type": "string"},
                "fencing_token": {"type": "integer"},
                "duration_s": {"type": "number"},
                "input_tokens": {"type": "integer"},
                "output_tokens": {"type": "integer"},
                "cost_usd": {"type": "number"},
                "quality": {"type": "number"},
                "diff_lines": {"type": "integer"},
                "files_changed": {"type": "integer"},
                "status": {"type": "string"},
                "meta": {"type": "object"},
            },
            "required": ["task_id", "worker"],
        },
    },
    {
        "name": "list_runs",
        "description": "查看运行记录。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "task_id": {"type": "string"},
                "worker": {"type": "string"},
            },
        },
    },
    {
        "name": "get_estimates",
        "description": "聚合 runs，输出 worker/kind 的成本、质量、工时先验。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
        },
    },
    {
        "name": "plan_milp",
        "description": "用先验 + MILP 做带依赖/容量/写集互斥/质量下限的调度。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "quality_floor": {"type": "number"},
                "mode": {"type": "string", "enum": ["cost", "time", "balanced"]},
                "time_limit": {"type": "integer"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "plan_front",
        "description": "扫 deadline，输出 cost–makespan Pareto 前沿。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "quality_floor": {"type": "number"},
                "points": {"type": "integer"},
                "time_limit": {"type": "integer"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "plan_front_3d",
        "description": "扫 quality floor + deadline，输出 cost–time–quality 三维 Pareto 前沿。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "floors": {"type": "array", "items": {"type": "number"}},
                "points": {"type": "integer"},
                "time_limit": {"type": "integer"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "apply_plan",
        "description": "把 plan 回写到任务板（planned_worker/start/end）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan": {"type": "object"},
                "reserve_ttl": {"type": "number"},
            },
            "required": ["plan"],
        },
    },
    {
        "name": "get_plan",
        "description": "查看项目最近的计划。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
        },
    },
    {
        "name": "clear_plan",
        "description": "清除项目的计划回写。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
        },
    },
    {
        "name": "claim_planned",
        "description": "按计划领取：只领 planned_worker 是自己的、最早的 ready 任务。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "worker": {"type": "string"},
                "project_id": {"type": "string"},
                "ttl_seconds": {"type": "number"},
            },
            "required": ["worker"],
        },
    },
    {
        "name": "list_reservations",
        "description": "查看计划预留（防止别的 worker 抢走已分配任务）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "project_id": {"type": "string"},
            },
        },
    },
    {
        "name": "renew_reservation",
        "description": "续期计划预留（长任务晚开始时用）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "worker": {"type": "string"},
                "ttl_seconds": {"type": "number"},
            },
            "required": ["task_id", "worker"],
        },
    },
    {
        "name": "record_verification",
        "description": "独立验证：passed=true 进入审批/done，false 退回 ready。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "verifier": {"type": "string"},
                "passed": {"type": "boolean"},
                "score": {"type": "number"},
                "notes": {"type": "string"},
            },
            "required": ["task_id", "verifier", "passed"],
        },
    },
    {
        "name": "list_verifications",
        "description": "查看验证记录。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "auto_verify",
        "description": "执行任务的 checks（file_exists/file_contains/file_regex/command），自动记录验证结论。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "verifier": {"type": "string"},
                "root": {"type": "string"},
                "allow_command": {"type": "boolean"},
                "allowed_commands": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "start_execution",
        "description": "开始一次 Run（V1 执行实体），同一任务同时只能有一个 author Run。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "agent": {"type": "string"},
                "role": {"type": "string"},
                "workspace": {"type": "string"},
                "session": {"type": "string"},
            },
            "required": ["task_id", "agent"],
        },
    },
    {
        "name": "finish_execution",
        "description": "结束一次 Run：succeeded/failed/cancelled/interrupted。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "string"},
                "status": {"type": "string"},
                "exit_reason": {"type": "string"},
            },
            "required": ["execution_id"],
        },
    },
    {
        "name": "reserve_files",
        "description": "预约 repo/path 文件范围；父子目录/大小写冲突会拒绝，全有或全无。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "repo_id": {"type": "string"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "execution_id": {"type": "string"},
                "kind": {"type": "string", "enum": ["file", "dir"]},
            },
            "required": ["task_id", "repo_id", "paths"],
        },
    },
    {
        "name": "release_files",
        "description": "释放文件预约。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "repo_id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    {
        "name": "list_executions",
        "description": "查看 Run。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "list_file_reservations",
        "description": "查看文件预约。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "repo_id": {"type": "string"},
                "active_only": {"type": "boolean"},
            },
        },
    },
    {
        "name": "create_candidate",
        "description": "创建 Candidate（base_sha + candidate_sha）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "repo_id": {"type": "string"},
                "base_sha": {"type": "string"},
                "candidate_sha": {"type": "string"},
                "author_execution_id": {"type": "string"},
            },
            "required": ["task_id", "repo_id", "base_sha", "candidate_sha"],
        },
    },
    {
        "name": "freeze_candidate",
        "description": "冻结 Candidate：building -> validating。",
        "inputSchema": {
            "type": "object",
            "properties": {"candidate_id": {"type": "string"}},
            "required": ["candidate_id"],
        },
    },
    {
        "name": "record_evidence",
        "description": "记录证据。带 check_id 的门禁证据必须绑定 candidate_sha 并落在打开的验证批次；不带 check_id 只作记录，不能替代必需检查。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "type": {"type": "string"},
                "check_id": {"type": "string"},
                "batch_id": {"type": "string"},
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "exit_code": {"type": "integer"},
                "output_ref": {"type": "string"},
                "sha": {"type": "string"},
                "execution_id": {"type": "string"},
            },
            "required": ["candidate_id", "type"],
        },
    },
    {
        "name": "mark_candidate_ready",
        "description": "验证通过 -> awaiting_human。要求当前验证批次内所有必需检查项（至少一项 test + 一项 review）均有效通过。",
        "inputSchema": {
            "type": "object",
            "properties": {"candidate_id": {"type": "string"}},
            "required": ["candidate_id"],
        },
    },
    {
        "name": "begin_validation_batch",
        "description": "为 Candidate 开启新的验证批次；旧批次证据不再参与门禁。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["candidate_id"],
        },
    },
    {
        "name": "candidate_gate",
        "description": "只读查看 Candidate 的必需检查门禁结果（missing/failed/stale）。",
        "inputSchema": {
            "type": "object",
            "properties": {"candidate_id": {"type": "string"}},
            "required": ["candidate_id"],
        },
    },
    {
        "name": "list_validation_batches",
        "description": "查看 Candidate 的验证批次历史。",
        "inputSchema": {
            "type": "object",
            "properties": {"candidate_id": {"type": "string"}},
            "required": ["candidate_id"],
        },
    },
    {
        "name": "approve_candidate",
        "description": "人工批准 Candidate -> approved。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "approver": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["candidate_id", "approver"],
        },
    },
    {
        "name": "start_merge",
        "description": "创建 MergeAttempt，要求 candidate approved 且 base_sha 匹配。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "expected_base_sha": {"type": "string"},
                "requested_by": {"type": "string"},
            },
            "required": ["candidate_id", "expected_base_sha"],
        },
    },
    {
        "name": "finish_merge",
        "description": "结束 MergeAttempt；succeeded 时 Candidate -> merged。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "attempt_id": {"type": "string"},
                "status": {"type": "string", "enum": ["succeeded", "failed", "unknown"]},
                "result_sha": {"type": "string"},
                "error": {"type": "string"},
            },
            "required": ["attempt_id"],
        },
    },
    {
        "name": "transition_task",
        "description": "V1 Task lifecycle 迁移（ready/assigned/in_progress/...）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "state": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "state"],
        },
    },
    {
        "name": "create_workspace",
        "description": "创建 git worktree（repo/path/base_sha）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "repo_id": {"type": "string"},
                "repo_path": {"type": "string"},
                "base_sha": {"type": "string"},
                "branch": {"type": "string"},
                "path": {"type": "string"},
                "execution_id": {"type": "string"},
            },
            "required": ["task_id", "repo_id", "repo_path"],
        },
    },
    {
        "name": "list_workspaces",
        "description": "查看 worktree。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    },
    {
        "name": "workspace_changes",
        "description": "查看 worktree 相对 base_sha 的改动文件（含未跟踪）。",
        "inputSchema": {
            "type": "object",
            "properties": {"workspace_id": {"type": "string"}},
            "required": ["workspace_id"],
        },
    },
    {
        "name": "workspace_scope",
        "description": "检查 worktree 改动是否越过 file_reservations。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["workspace_id"],
        },
    },
    {
        "name": "commit_workspace",
        "description": "提交 worktree 改动，返回 SHA。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["workspace_id"],
        },
    },
    {
        "name": "candidate_from_workspace",
        "description": "用 worktree HEAD 创建 Candidate。",
        "inputSchema": {
            "type": "object",
            "properties": {"workspace_id": {"type": "string"}},
            "required": ["workspace_id"],
        },
    },
    {
        "name": "remove_workspace",
        "description": "删除 git worktree。",
        "inputSchema": {
            "type": "object",
            "properties": {"workspace_id": {"type": "string"}},
            "required": ["workspace_id"],
        },
    },
    {
        "name": "approve_task",
        "description": "审批通过：review -> done。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "approver": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["task_id", "approver"],
        },
    },
    {
        "name": "reject_task",
        "description": "审批退回：review -> ready。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "approver": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "approver"],
        },
    },
    {
        "name": "list_approvals",
        "description": "查看审批队列。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "request_approvals",
        "description": "重新发起审批（超时/作废后），可覆盖 count/ttl。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "requested_by": {"type": "string"},
                "count": {"type": "integer"},
                "ttl_seconds": {"type": "number"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "get_budget",
        "description": "查看项目预算/已花费/剩余。",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
        },
    },
    {
        "name": "get_events",
        "description": "查看事件流（审计/调试）。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}, "task_id": {"type": "string"}},
        },
    },
    {
        "name": "get_metrics",
        "description": "查看任务/租约/事件统计。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "reap_expired",
        "description": "回收过期租约（claim 时也会自动做）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def call_tool(c: Coordinator, name: str, args: dict[str, Any]) -> Any:
    if name == "create_project":
        return c.create_project(
            args["name"],
            bool(args.get("require_approval", False)),
            bool(args.get("require_verification", False)),
            int(args.get("approvals_required", 1)),
            float(args.get("approval_ttl_seconds", 86400)),
            args.get("budget_usd"),
            required_checks=args.get("required_checks"),
        )
    if name == "create_task":
        return c.create_task(
            project_id=args["project_id"],
            title=args["title"],
            write_set=args.get("write_set", []),
            deps=args.get("deps", []),
            dod=args.get("dod", ""),
            description=args.get("description", ""),
            priority=int(args.get("priority", 0)),
            kind=args.get("kind", "code"),
            checks=args.get("checks", []),
            repo_id=args.get("repo_id", ""),
            required_checks=args.get("required_checks"),
        )
    if name == "list_tasks":
        return c.list_tasks(args.get("project_id"), args.get("status"))
    if name == "claim_task":
        return c.claim_task(
            worker=args["worker"],
            project_id=args.get("project_id"),
            ttl_seconds=args.get("ttl_seconds"),
            task_id=args.get("task_id"),
        )
    if name == "renew_lease":
        return c.renew_lease(
            args["task_id"], args["worker"], int(args["fencing_token"]), args.get("ttl_seconds")
        )
    if name == "release_lease":
        return c.release_lease(
            args["task_id"], args["worker"], int(args["fencing_token"]), args.get("reason", "")
        )
    if name == "commit_task":
        return c.commit_task(
            args["task_id"], args["worker"], int(args["fencing_token"]),
            args.get("result"), args.get("run"), args.get("require_approval"),
        )
    if name == "record_run":
        return c.record_run(
            task_id=args["task_id"],
            worker=args["worker"],
            fencing_token=args.get("fencing_token"),
            duration_s=args.get("duration_s", 0),
            input_tokens=args.get("input_tokens", 0),
            output_tokens=args.get("output_tokens", 0),
            cost_usd=args.get("cost_usd", 0),
            quality=args.get("quality"),
            diff_lines=args.get("diff_lines", 0),
            files_changed=args.get("files_changed", 0),
            status=args.get("status", "ok"),
            meta=args.get("meta"),
        )
    if name == "list_runs":
        return c.list_runs(
            limit=int(args.get("limit", 100)),
            task_id=args.get("task_id"),
            worker=args.get("worker"),
        )
    if name == "get_estimates":
        return summarize(c.db_path, limit=int(args.get("limit", 10000)))
    if name == "start_execution":
        return c.start_execution(
            args["task_id"], args["agent"], args.get("role", "author"),
            args.get("workspace", ""), args.get("session", ""),
        )
    if name == "finish_execution":
        return c.finish_execution(
            args["execution_id"], args.get("status", "succeeded"),
            args.get("exit_reason", ""),
        )
    if name == "reserve_files":
        return c.reserve_files(
            args["task_id"], args["repo_id"], args["paths"],
            args.get("execution_id"), args.get("kind", "file"),
        )
    if name == "release_files":
        return {"released": c.release_file_reservations(
            args.get("task_id"), args.get("repo_id"), args.get("reason", "")
        )}
    if name == "list_executions":
        return c.list_executions(args.get("task_id"), int(args.get("limit", 100)))
    if name == "list_file_reservations":
        return c.list_file_reservations(
            args.get("task_id"), args.get("repo_id"),
            active_only=bool(args.get("active_only", True)),
        )
    if name == "create_candidate":
        return c.create_candidate(
            args["task_id"], args["repo_id"], args["base_sha"], args["candidate_sha"],
            args.get("author_execution_id"),
        )
    if name == "freeze_candidate":
        return c.freeze_candidate(args["candidate_id"])
    if name == "record_evidence":
        return c.record_evidence(
            args["candidate_id"], args["type"], args.get("command", ""),
            args.get("cwd", ""), args.get("exit_code"), args.get("output_ref", ""),
            args.get("sha"), args.get("execution_id"),
            check_id=args.get("check_id", ""),
            batch_id=args.get("batch_id"),
        )
    if name == "mark_candidate_ready":
        return c.mark_candidate_ready(args["candidate_id"])
    if name == "begin_validation_batch":
        return c.begin_validation_batch(
            args["candidate_id"], args.get("reason", "")
        )
    if name == "candidate_gate":
        return c.evaluate_candidate_gate(args["candidate_id"])
    if name == "list_validation_batches":
        return c.list_validation_batches(args["candidate_id"])
    if name == "approve_candidate":
        return c.approve_candidate(
            args["candidate_id"], args["approver"], args.get("note", "")
        )
    if name == "start_merge":
        return c.start_merge(
            args["candidate_id"], args["expected_base_sha"],
            args.get("requested_by", "human"),
        )
    if name == "finish_merge":
        return c.finish_merge(
            args["attempt_id"], args.get("status", "succeeded"),
            args.get("result_sha", ""), args.get("error", ""),
        )
    if name == "transition_task":
        return c.transition_task(
            args["task_id"], args["state"], args.get("reason", "")
        )
    if name == "create_workspace":
        return c.create_workspace(
            args["task_id"], args["repo_id"], args["repo_path"],
            args.get("base_sha", "HEAD"), args.get("branch"),
            args.get("path"), args.get("execution_id"),
        )
    if name == "list_workspaces":
        return c.list_workspaces(args.get("task_id"), args.get("status"))
    if name == "workspace_changes":
        return c.workspace_changed_files(args["workspace_id"])
    if name == "workspace_scope":
        return c.check_workspace_scope(args["workspace_id"], args.get("task_id"))
    if name == "commit_workspace":
        return {"sha": c.commit_workspace(
            args["workspace_id"], args.get("message", "guild: snapshot")
        )}
    if name == "candidate_from_workspace":
        return c.create_candidate_from_workspace(args["workspace_id"])
    if name == "remove_workspace":
        return c.remove_workspace(args["workspace_id"])
    if name == "approve_task":
        return c.approve_task(args["task_id"], args["approver"], args.get("note", ""))
    if name == "reject_task":
        return c.reject_task(args["task_id"], args["approver"], args.get("reason", ""))
    if name == "list_approvals":
        return c.list_approvals(args.get("status"), int(args.get("limit", 100)))
    if name == "request_approvals":
        return c.request_approvals(
            args["task_id"], args.get("requested_by", "human"),
            args.get("count"), args.get("ttl_seconds"),
        )
    if name == "get_budget":
        return c.project_budget(args["project_id"])
    if name == "plan_milp":
        return solve_plan(
            c,
            summarize(c.db_path),
            args["project_id"],
            quality_floor=float(args.get("quality_floor", 0.8)),
            mode=args.get("mode", "cost"),
            time_limit=int(args.get("time_limit", 30)),
        )
    if name == "plan_front":
        return pareto_front(
            c,
            summarize(c.db_path),
            args["project_id"],
            quality_floor=float(args.get("quality_floor", 0.8)),
            points=int(args.get("points", 8)),
            time_limit=int(args.get("time_limit", 30)),
        )
    if name == "plan_front_3d":
        floors = tuple(float(x) for x in args.get("floors", [0.7, 0.8, 0.9]))
        return pareto_front_3d(
            c,
            summarize(c.db_path),
            args["project_id"],
            floors=floors,
            points=int(args.get("points", 5)),
            time_limit=int(args.get("time_limit", 30)),
        )
    if name == "apply_plan":
        return c.apply_plan(args["plan"], float(args.get("reserve_ttl", 3600)))
    if name == "get_plan":
        return c.get_plan(args["project_id"])
    if name == "clear_plan":
        return {"cleared": c.clear_plan(args["project_id"])}
    if name == "claim_planned":
        return c.claim_planned(
            args["worker"], args.get("project_id"), args.get("ttl_seconds")
        )
    if name == "list_reservations":
        return c.list_reservations(args.get("status"), args.get("project_id"))
    if name == "renew_reservation":
        return c.renew_reservation(
            args["task_id"], args["worker"], float(args.get("ttl_seconds", 3600))
        )
    if name == "record_verification":
        return c.record_verification(
            args["task_id"], args["verifier"], bool(args["passed"]),
            args.get("score"), args.get("notes", ""),
        )
    if name == "list_verifications":
        return c.list_verifications(args.get("task_id"), int(args.get("limit", 100)))
    if name == "auto_verify":
        return auto_verify(
            c, args["task_id"], args.get("verifier", "auto"),
            args.get("root", "."), bool(args.get("allow_command", False)),
            args.get("allowed_commands"),
        )
    if name == "get_events":
        return c.events(int(args.get("limit", 100)), args.get("task_id"))
    if name == "get_metrics":
        return c.metrics()
    if name == "reap_expired":
        return {"reaped": c.reap_expired()}
    raise CoordinatorError(f"unknown tool: {name}")


def start_reaper(coord: Coordinator, interval: float) -> None:
    if interval <= 0:
        return

    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                n = coord.reap_expired()
                if n:
                    print(f"[p0-coordinator] reaped {n} expired lease(s)", file=sys.stderr, flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[p0-coordinator] reaper error: {exc}", file=sys.stderr, flush=True)

    threading.Thread(target=loop, daemon=True).start()


def main() -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    db = os.environ.get("COORDINATOR_DB", str(ROOT / "data" / "coordinator.db"))
    coord = Coordinator(db)
    start_reaper(coord, float(os.environ.get("REAPER_INTERVAL", "15")))
    print(f"[p0-coordinator] db={db}", file=sys.stderr, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[p0-coordinator] bad json: {exc}", file=sys.stderr, flush=True)
            continue

        method = msg.get("method")
        msg_id = msg.get("id")
        if method is None:
            continue  # response from client, ignore

        try:
            if method == "initialize":
                params = msg.get("params") or {}
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "p0-coordinator", "version": "0.1.0"},
                    },
                })
            elif method == "notifications/initialized":
                continue
            elif method == "ping":
                send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
            elif method == "tools/list":
                send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})
            elif method == "tools/call":
                params = msg.get("params") or {}
                name = params.get("name")
                args = params.get("arguments") or {}
                try:
                    result = call_tool(coord, name, args)
                    text = json.dumps(result, ensure_ascii=False, default=str)
                    send({
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": text}],
                            "isError": False,
                        },
                    })
                except Exception as exc:  # noqa: BLE001
                    send({
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                            "isError": True,
                        },
                    })
            else:
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                })
        except Exception as exc:  # noqa: BLE001
            print(f"[p0-coordinator] handler error: {exc}", file=sys.stderr, flush=True)
            if msg_id is not None:
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32603, "message": str(exc)},
                })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
