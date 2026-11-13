"""Coordinator 的 Shell/CLI 入口，方便任意 agent 直接调用。

示例：
    python worker_cli.py --db data/coordinator.db create-project --name demo
    python worker_cli.py --db data/coordinator.db create-task --project P-xxxx --title t --write-set a --kind code
    python worker_cli.py --db data/coordinator.db claim --worker W1 --project P-xxxx --ttl 120
    python worker_cli.py --db data/coordinator.db commit --task T-xxxx --worker W1 --token 1 \
        --run '{"cost_usd":0.05,"quality":0.9,"duration_s":30}'
    python worker_cli.py --db data/coordinator.db approve --task T-xxxx --approver human

所有命令输出 JSON；失败时 stderr 打 `错误: ...`，退出码 1。
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from coordinator import Coordinator, CoordinatorError, LeaseError
from adapters import probe_all
from agent_runner import run_agent
from or_router import recommend_agent, recommend_batch
from verifier import auto_verify

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "coordinator.db"


def _json_arg(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CoordinatorError(f"invalid JSON: {exc}") from None
    if not isinstance(value, dict):
        raise CoordinatorError("JSON argument must be an object")
    return value


def _json_array_arg(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CoordinatorError(f"invalid JSON: {exc}") from None
    if not isinstance(value, list):
        raise CoordinatorError("JSON argument must be an array")
    return value


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="P0 coordinator worker CLI")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create-project", help="创建项目")
    p.add_argument("--name", required=True)
    p.add_argument("--require-approval", action="store_true")
    p.add_argument("--require-verification", action="store_true")
    p.add_argument("--approvals-required", type=int, default=1)
    p.add_argument("--approval-ttl", type=float, default=86400)
    p.add_argument("--required-approver-role", choices=["reviewer", "admin"], default="reviewer")
    p.add_argument("--approval-chain", default="", help="逗号分隔，如 reviewer,admin")
    p.add_argument("--required-checks", help="JSON array: [{\"id\":\"platform-test\",\"kind\":\"test\"}]")
    p.add_argument("--budget", type=float)

    p = sub.add_parser("projects", help="列出项目")

    p = sub.add_parser("adapters", help="探测 Codex/DSH/Mock adapter")

    p = sub.add_parser("create-task", help="创建任务")
    p.add_argument("--project", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--write-set", nargs="*", default=[])
    p.add_argument("--deps", nargs="*", default=[])
    p.add_argument("--kind", default="code")
    p.add_argument("--priority", type=int, default=0)
    p.add_argument("--dod", default="")
    p.add_argument("--description", default="")
    p.add_argument("--checks", help="JSON array: [{\"type\":\"file_exists\",\"path\":\"...\"}]")
    p.add_argument("--required-checks", help="JSON array，任务追加检查项（不能删除项目必需项）")
    p.add_argument("--repo-id", default="")

    p = sub.add_parser("claim", help="领取任务")
    p.add_argument("--worker", required=True)
    p.add_argument("--project")
    p.add_argument("--task")
    p.add_argument("--ttl", type=float, default=120)
    p.add_argument("--planned", action="store_true", help="只领计划给自己的任务")

    p = sub.add_parser("renew", help="续约")
    p.add_argument("--task", required=True)
    p.add_argument("--worker", required=True)
    p.add_argument("--token", type=int, required=True)
    p.add_argument("--ttl", type=float, default=120)

    p = sub.add_parser("commit", help="提交任务")
    p.add_argument("--task", required=True)
    p.add_argument("--worker", required=True)
    p.add_argument("--token", type=int, required=True)
    p.add_argument("--result", help="JSON object")
    p.add_argument("--run", help="JSON object: cost_usd/quality/duration_s/...")
    p.add_argument("--cost", type=float, help="快捷：cost_usd")
    p.add_argument("--quality", type=float, help="快捷：quality 0..1")
    p.add_argument("--duration", type=float, help="快捷：duration_s")
    p.add_argument("--diff-lines", type=int, help="快捷：diff_lines")
    p.add_argument("--files-changed", type=int, help="快捷：files_changed")
    p.add_argument("--require-approval", action="store_true")

    p = sub.add_parser("release", help="释放租约")
    p.add_argument("--task", required=True)
    p.add_argument("--worker", required=True)
    p.add_argument("--token", type=int, required=True)
    p.add_argument("--reason", default="")

    p = sub.add_parser("approve", help="审批通过")
    p.add_argument("--task", required=True)
    p.add_argument("--approver", required=True)
    p.add_argument("--note", default="")

    p = sub.add_parser("reject", help="审批退回")
    p.add_argument("--task", required=True)
    p.add_argument("--approver", required=True)
    p.add_argument("--reason", default="")

    p = sub.add_parser("request-approvals", help="重新发起审批")
    p.add_argument("--task", required=True)
    p.add_argument("--requested-by", default="human")
    p.add_argument("--count", type=int)
    p.add_argument("--ttl", type=float)

    p = sub.add_parser("renew-reservation", help="续期计划预留")
    p.add_argument("--task", required=True)
    p.add_argument("--worker", required=True)
    p.add_argument("--ttl", type=float, default=3600)

    p = sub.add_parser("record-verification", help="独立验证（默认 passed）")
    p.add_argument("--task", required=True)
    p.add_argument("--verifier", required=True)
    p.add_argument("--failed", action="store_true", help="标记失败（默认通过）")
    p.add_argument("--score", type=float)
    p.add_argument("--notes", default="")

    p = sub.add_parser("list-verifications", help="查看验证记录")
    p.add_argument("--task")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("auto-verify", help="执行 checks 并记录验证结论")
    p.add_argument("--task", required=True)
    p.add_argument("--verifier", default="auto")
    p.add_argument("--root", default=".")
    p.add_argument("--allow-command", action="store_true")
    p.add_argument("--allowed-commands", default="python,python3,py,pytest,node,npm,git")

    p = sub.add_parser("start-execution", help="开始一次 Run（V1 执行实体）")
    p.add_argument("--task", required=True)
    p.add_argument("--agent", required=True)
    p.add_argument("--role", default="author")
    p.add_argument("--workspace", default="")
    p.add_argument("--session", default="")

    p = sub.add_parser("start-agent", help="真正启动 agent 并产出 Candidate")
    p.add_argument("--task", required=True)
    p.add_argument("--agent", required=True,
                   choices=["auto", "dsh", "codex", "codex-deepseek", "zhipu", "codex-zhipu",
                            "appserver", "codex-appserver"])
    p.add_argument("--strategy", default="quality", choices=["cost", "time", "quality"])
    p.add_argument("--prompt", default="")
    p.add_argument("--check", default="")
    p.add_argument("--timeout", type=float, default=300)
    p.add_argument("--require-tag", action="append", default=[],
                   help="能力标签过滤，如 language:python")

    p = sub.add_parser("recommend-agent", help="按成本/时间/质量推荐 agent")
    p.add_argument("--task", required=True)
    p.add_argument("--strategy", default="quality", choices=["cost", "time", "quality"])
    p.add_argument("--require-tag", action="append", default=[],
                   help="能力标签过滤，如 language:python")

    p = sub.add_parser("recommend-batch", help="为项目内 ready 任务批量分配 agent")
    p.add_argument("--project", required=True)
    p.add_argument("--strategy", default="quality", choices=["cost", "time", "quality"])
    p.add_argument("--max-parallel", type=int, default=4)

    p = sub.add_parser("semantic-changes", help="查看候选的语义变更")
    p.add_argument("--candidate")
    p.add_argument("--task")

    p = sub.add_parser("semantic-impact", help="查看语义变更影响面")
    p.add_argument("--change", required=True)

    p = sub.add_parser("semantic-ack", help="ACK 一个语义变更")
    p.add_argument("--change", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--agent", required=True)

    p = sub.add_parser("add-semantic-dep", help="声明任务依赖某个语义对象")
    p.add_argument("--task", required=True)
    p.add_argument("--object", required=True)

    p = sub.add_parser("tag-agent", help="设置 Agent 能力标签")
    p.add_argument("--agent", required=True)
    p.add_argument("--dimension", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--confidence", type=float, default=0.5)
    p.add_argument("--source", default="manual")
    p.add_argument("--sample-size", type=int, default=0)

    p = sub.add_parser("agent-profile", help="查看 Agent 能力标签")
    p.add_argument("--agent")

    p = sub.add_parser("finish-execution", help="结束一次 Run")
    p.add_argument("--execution", required=True)
    p.add_argument("--status", default="succeeded",
                   choices=["succeeded", "failed", "cancelled", "interrupted"])
    p.add_argument("--reason", default="")

    p = sub.add_parser("reserve-files", help="预约 repo/path 文件范围")
    p.add_argument("--task", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--paths", nargs="+", required=True)
    p.add_argument("--execution")
    p.add_argument("--kind", choices=["file", "dir"], default="file")

    p = sub.add_parser("release-files", help="释放文件预约")
    p.add_argument("--task")
    p.add_argument("--repo")
    p.add_argument("--reason", default="")

    p = sub.add_parser("executions", help="查看 Run")
    p.add_argument("--task")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("create-candidate", help="创建 Candidate（SHA 冻结）")
    p.add_argument("--task", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--base-sha", required=True)
    p.add_argument("--candidate-sha", required=True)
    p.add_argument("--execution")

    p = sub.add_parser("freeze-candidate", help="冻结 Candidate")
    p.add_argument("--candidate", required=True)

    p = sub.add_parser("review-candidate", help="手动启动独立只读审查，不批准或合入")
    p.add_argument("--candidate", required=True)
    p.add_argument("--check-id", default="agent-review")
    p.add_argument("--timeout", type=float, default=180)

    p = sub.add_parser("reviews", help="查看候选审查、报告和门禁")
    p.add_argument("--candidate", required=True)

    p = sub.add_parser("record-evidence", help="记录绑定 candidate_sha 的证据")
    p.add_argument("--candidate", required=True)
    p.add_argument("--type", required=True)
    p.add_argument("--check-id", default="", help="必需检查项 ID（门禁证据必须填写）")
    p.add_argument("--batch", help="验证批次 ID（缺省用候选当前打开批次）")
    p.add_argument("--command", default="")
    p.add_argument("--cwd", default="")
    p.add_argument("--exit-code", type=int)
    p.add_argument("--output-ref", default="")
    p.add_argument("--sha")
    p.add_argument("--execution")

    p = sub.add_parser("mark-candidate-ready", help="验证通过 -> awaiting_human")
    p.add_argument("--candidate", required=True)

    p = sub.add_parser("begin-validation-batch", help="为 Candidate 开启新的验证批次")
    p.add_argument("--candidate", required=True)
    p.add_argument("--reason", default="")

    p = sub.add_parser("validation-batches", help="查看 Candidate 的验证批次")
    p.add_argument("--candidate", required=True)

    p = sub.add_parser("candidate-gate", help="查看 Candidate 的必需检查门禁结果")
    p.add_argument("--candidate", required=True)

    p = sub.add_parser("approve-candidate", help="人工批准 Candidate")
    p.add_argument("--candidate", required=True)
    p.add_argument("--approver", required=True)
    p.add_argument("--role", choices=["reviewer", "admin"], default="reviewer")
    p.add_argument("--note", default="")

    p = sub.add_parser("reject-candidate", help="拒绝 Candidate")
    p.add_argument("--candidate", required=True)
    p.add_argument("--approver", required=True)
    p.add_argument("--reason", default="")

    p = sub.add_parser("start-merge", help="创建 MergeAttempt 并校验目标分支 HEAD")
    p.add_argument("--candidate", required=True)
    p.add_argument("--base-sha", required=True)
    p.add_argument("--repo-path", default="", help="目标仓库路径；省略时用候选最近的工作区")
    p.add_argument("--target-ref", "--target-branch", default="HEAD", help="目标本地分支；默认当前 HEAD")
    p.add_argument("--requested-by", default="human")

    p = sub.add_parser("finish-merge", help="结束 MergeAttempt 并校验目标 ref")
    p.add_argument("--attempt", required=True)
    p.add_argument("--status", default="succeeded",
                   choices=["succeeded", "failed", "unknown"])
    p.add_argument("--result-sha", default="")
    p.add_argument("--error", default="")

    p = sub.add_parser("merge-candidate-git", help="准备完整候选并可选确认合入")
    p.add_argument("--candidate", required=True)
    p.add_argument("--repo-path", required=True)
    p.add_argument("--worktree", required=True)
    p.add_argument("--target-ref", "--target-branch", default="HEAD", help="目标本地分支；默认当前 HEAD")
    p.add_argument("--check", default="", help="可选检查命令，shell=False")
    p.add_argument("--requested-by", default="human")
    p.add_argument("--finish", action="store_true", help="成功后直接 finish_merge")

    p = sub.add_parser("transition-task", help="V1 Task lifecycle 迁移")
    p.add_argument("--task", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--reason", default="")

    p = sub.add_parser("candidates", help="查看 Candidate")
    p.add_argument("--task")
    p.add_argument("--status")

    p = sub.add_parser("evidence", help="查看证据")
    p.add_argument("--candidate", required=True)

    p = sub.add_parser("merge-attempts", help="查看 MergeAttempt")
    p.add_argument("--candidate")

    p = sub.add_parser("create-workspace", help="创建独立 Git 工作区")
    p.add_argument("--task", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--repo-path", required=True)
    p.add_argument("--base-sha", default="HEAD")
    p.add_argument("--branch")
    p.add_argument("--path")
    p.add_argument("--execution")

    p = sub.add_parser("workspaces", help="查看 workspace")
    p.add_argument("--task")
    p.add_argument("--status")

    p = sub.add_parser("workspace-changes", help="查看 worktree 改动文件")
    p.add_argument("--workspace", required=True)

    p = sub.add_parser("workspace-scope", help="检查改动是否越界")
    p.add_argument("--workspace", required=True)
    p.add_argument("--task")

    p = sub.add_parser("commit-workspace", help="提交 worktree 改动")
    p.add_argument("--workspace", required=True)
    p.add_argument("--message", default="guild: snapshot")

    p = sub.add_parser("candidate-from-workspace", help="从 worktree HEAD 创建 Candidate")
    p.add_argument("--workspace", required=True)

    p = sub.add_parser("remove-workspace", help="删除 worktree")
    p.add_argument("--workspace", required=True)

    p = sub.add_parser("file-reservations", help="查看文件预约")
    p.add_argument("--task")
    p.add_argument("--repo")
    p.add_argument("--all", action="store_true")

    p = sub.add_parser("status", help="查看项目任务与指标")
    p.add_argument("--project")

    p = sub.add_parser("budget", help="查看项目预算/花费")
    p.add_argument("--project", required=True)

    p = sub.add_parser("runs", help="查看运行记录")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--worker")
    p.add_argument("--task")

    p = sub.add_parser("events", help="查看事件")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--task")

    p = sub.add_parser("recovery-report", help="只读核对未结束执行与合入，不自动重跑")
    p.add_argument("--project")
    p = sub.add_parser("reconcile-execution", help="核对后将遗留执行结束为 interrupted")
    p.add_argument("--execution", required=True)
    p.add_argument("--generation", type=int, required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--confirm-local-stopped", action="store_true")
    p = sub.add_parser("reconcile-merge", help="仅确认已发布的合入或显式放弃准备结果")
    p.add_argument("--attempt", required=True)
    p.add_argument("--action", choices=["confirm", "abandon"], required=True)
    p.add_argument("--result-sha", required=True)
    p.add_argument("--reason", default="")

    return ap


def run(args) -> object:
    coord = Coordinator(args.db)
    if args.cmd == "recovery-report":
        return coord.recovery_report(args.project)
    if args.cmd == "reconcile-execution":
        return coord.reconcile_execution(args.execution, args.generation, args.reason,
                                         confirm_local_stopped=args.confirm_local_stopped)
    if args.cmd == "reconcile-merge":
        return coord.reconcile_merge(args.attempt, args.action, args.result_sha, args.reason)
    if args.cmd == "create-project":
        return coord.create_project(
            args.name, args.require_approval, args.require_verification,
            args.approvals_required, args.approval_ttl, args.budget,
            required_approver_role=args.required_approver_role,
            approval_chain=args.approval_chain,
            required_checks=(
                _json_array_arg(args.required_checks) if args.required_checks else None
            ),
        )
    if args.cmd == "projects":
        return coord.list_projects()
    if args.cmd == "adapters":
        return probe_all()
    if args.cmd == "create-task":
        return coord.create_task(
            project_id=args.project,
            title=args.title,
            write_set=args.write_set,
            deps=args.deps,
            kind=args.kind,
            priority=args.priority,
            dod=args.dod,
            description=args.description,
            checks=_json_array_arg(args.checks),
            repo_id=args.repo_id,
            required_checks=(
                _json_array_arg(args.required_checks) if args.required_checks else None
            ),
        )
    if args.cmd == "claim":
        if args.planned:
            return coord.claim_planned(args.worker, args.project, args.ttl)
        return coord.claim_task(args.worker, args.project, args.ttl, args.task)
    if args.cmd == "renew":
        return coord.renew_lease(args.task, args.worker, args.token, args.ttl)
    if args.cmd == "commit":
        run = _json_arg(args.run) or {}
        if args.cost is not None:
            run["cost_usd"] = args.cost
        if args.quality is not None:
            run["quality"] = args.quality
        if args.duration is not None:
            run["duration_s"] = args.duration
        if args.diff_lines is not None:
            run["diff_lines"] = args.diff_lines
        if args.files_changed is not None:
            run["files_changed"] = args.files_changed
        return coord.commit_task(
            args.task,
            args.worker,
            args.token,
            _json_arg(args.result),
            run or None,
            True if args.require_approval else None,
        )
    if args.cmd == "release":
        return coord.release_lease(args.task, args.worker, args.token, args.reason)
    if args.cmd == "approve":
        return coord.approve_task(args.task, args.approver, args.note)
    if args.cmd == "reject":
        return coord.reject_task(args.task, args.approver, args.reason)
    if args.cmd == "request-approvals":
        return coord.request_approvals(
            args.task, args.requested_by, args.count, args.ttl
        )
    if args.cmd == "renew-reservation":
        return coord.renew_reservation(args.task, args.worker, args.ttl)
    if args.cmd == "record-verification":
        return coord.record_verification(
            args.task, args.verifier, not args.failed, args.score, args.notes
        )
    if args.cmd == "list-verifications":
        return coord.list_verifications(args.task, args.limit)
    if args.cmd == "auto-verify":
        allowed = [x.strip() for x in args.allowed_commands.split(",") if x.strip()]
        return auto_verify(
            coord, args.task, args.verifier, args.root, args.allow_command, allowed
        )
    if args.cmd == "start-execution":
        return coord.start_execution(
            args.task, args.agent, args.role, args.workspace, args.session
        )
    if args.cmd == "start-agent":
        agent = args.agent
        recommendation = None
        if agent == "auto":
            recommendation = recommend_agent(
                coord, args.task, args.strategy, required_tags=args.require_tag,
            )
            agent = recommendation["recommended"]["agent"]
        result = run_agent(
            coord, args.task, agent,
            prompt=args.prompt or None, check=args.check or None, timeout=args.timeout,
        )
        if recommendation is not None:
            result["or"] = recommendation
        return result
    if args.cmd == "recommend-agent":
        return recommend_agent(
            coord, args.task, args.strategy, required_tags=args.require_tag,
        )
    if args.cmd == "recommend-batch":
        return recommend_batch(
            coord, args.project, args.strategy, max_parallel=args.max_parallel,
        )
    if args.cmd == "semantic-changes":
        return coord.list_semantic_changes(
            candidate_id=args.candidate, task_id=args.task,
        )
    if args.cmd == "semantic-impact":
        return coord.semantic_impact(args.change)
    if args.cmd == "semantic-ack":
        return coord.ack_semantic_change(args.change, args.task, args.agent)
    if args.cmd == "add-semantic-dep":
        return coord.add_semantic_dependency(args.task, args.object)
    if args.cmd == "tag-agent":
        return coord.set_agent_capability(
            args.agent, args.dimension, args.value,
            confidence=args.confidence, source=args.source, sample_size=args.sample_size,
        )
    if args.cmd == "agent-profile":
        return coord.list_agent_capabilities(args.agent)
    if args.cmd == "finish-execution":
        return coord.finish_execution(args.execution, args.status, args.reason)
    if args.cmd == "reserve-files":
        return coord.reserve_files(
            args.task, args.repo, args.paths, args.execution, args.kind
        )
    if args.cmd == "release-files":
        return {"released": coord.release_file_reservations(
            args.task, args.repo, args.reason
        )}
    if args.cmd == "executions":
        return coord.list_executions(args.task, args.limit)
    if args.cmd == "file-reservations":
        return coord.list_file_reservations(
            args.task, args.repo, active_only=not args.all
        )
    if args.cmd == "create-candidate":
        return coord.create_candidate(
            args.task, args.repo, args.base_sha, args.candidate_sha, args.execution
        )
    if args.cmd == "freeze-candidate":
        return coord.freeze_candidate(args.candidate)
    if args.cmd == "review-candidate":
        from agent_review import review_candidate
        return review_candidate(coord, args.candidate, args.check_id, args.timeout)
    if args.cmd == "reviews":
        return {"reviews": coord.list_reviews(args.candidate),
                "gate": coord.evaluate_candidate_gate(args.candidate)}
    if args.cmd == "record-evidence":
        return coord.record_evidence(
            args.candidate, args.type, args.command, args.cwd,
            args.exit_code, args.output_ref, args.sha, args.execution,
            check_id=args.check_id, batch_id=args.batch,
        )
    if args.cmd == "mark-candidate-ready":
        return coord.mark_candidate_ready(args.candidate)
    if args.cmd == "begin-validation-batch":
        return coord.begin_validation_batch(args.candidate, args.reason)
    if args.cmd == "validation-batches":
        return coord.list_validation_batches(args.candidate)
    if args.cmd == "candidate-gate":
        return coord.evaluate_candidate_gate(args.candidate)
    if args.cmd == "approve-candidate":
        return coord.approve_candidate(
            args.candidate, args.approver, args.note, role=args.role,
        )
    if args.cmd == "reject-candidate":
        return coord.reject_candidate(args.candidate, args.approver, args.reason)
    if args.cmd == "start-merge":
        return coord.start_merge(
            args.candidate, args.base_sha, args.requested_by,
            repo_path=args.repo_path or None, target_ref=args.target_ref,
        )
    if args.cmd == "finish-merge":
        return coord.finish_merge(
            args.attempt, args.status, args.result_sha, args.error
        )
    if args.cmd == "merge-candidate-git":
        candidate = coord.get_candidate(args.candidate)
        attempts = coord.list_merge_attempts(args.candidate)
        pending = [a for a in attempts if a["status"] == "pending"]
        attempt = pending[0] if pending else coord.start_merge(
            args.candidate, candidate["base_sha"], args.requested_by,
            repo_path=args.repo_path, target_ref=args.target_ref,
        )
        check = shlex.split(args.check) if args.check else None
        provided_ref, _ = coord._resolve_local_target(Path(args.repo_path), args.target_ref)
        if provided_ref != attempt["target_ref"]:
            raise CoordinatorError("target branch does not match the pending merge attempt")
        result = coord.merge_candidate_git(
            args.candidate, args.repo_path, args.worktree, check,
        )
        if args.finish:
            result["merge_attempt"] = coord.finish_merge(
                attempt["attempt_id"], "succeeded", result["result_sha"]
            )
        else:
            result["merge_attempt"] = next(a for a in coord.list_merge_attempts(args.candidate)
                                           if a["attempt_id"] == attempt["attempt_id"])
        return result
    if args.cmd == "transition-task":
        return coord.transition_task(args.task, args.state, args.reason)
    if args.cmd == "candidates":
        return coord.list_candidates(args.task, args.status)
    if args.cmd == "evidence":
        return coord.list_evidence(args.candidate)
    if args.cmd == "merge-attempts":
        return coord.list_merge_attempts(args.candidate)
    if args.cmd == "create-workspace":
        return coord.create_workspace(
            args.task, args.repo, args.repo_path, args.base_sha,
            args.branch, args.path, args.execution,
        )
    if args.cmd == "workspaces":
        return coord.list_workspaces(args.task, args.status)
    if args.cmd == "workspace-changes":
        return coord.workspace_changed_files(args.workspace)
    if args.cmd == "workspace-scope":
        return coord.check_workspace_scope(args.workspace, args.task)
    if args.cmd == "commit-workspace":
        return {"sha": coord.commit_workspace(args.workspace, args.message)}
    if args.cmd == "candidate-from-workspace":
        return coord.create_candidate_from_workspace(args.workspace)
    if args.cmd == "remove-workspace":
        return coord.remove_workspace(args.workspace)
    if args.cmd == "status":
        return {
            "tasks": coord.list_tasks(project_id=args.project),
            "metrics": coord.metrics(),
        }
    if args.cmd == "budget":
        return coord.project_budget(args.project)
    if args.cmd == "runs":
        return coord.list_runs(args.limit, task_id=args.task, worker=args.worker)
    if args.cmd == "events":
        return coord.events(args.limit, args.task)
    raise CoordinatorError(f"unknown command: {args.cmd}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except (CoordinatorError, LeaseError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"错误: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
