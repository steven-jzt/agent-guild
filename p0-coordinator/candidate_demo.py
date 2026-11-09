"""Candidate / Evidence / MergeAttempt 演示：SHA 冻结 -> 证据 -> 人工批准 -> 合入。"""

from __future__ import annotations

import json
from pathlib import Path

from coordinator import Coordinator, CoordinatorError

ROOT = Path(__file__).resolve().parent
DB = ROOT / ".tmp" / "candidate-demo.db"


def show(label: str, data) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    c = Coordinator(DB)
    p = c.create_project("candidate-demo")
    t = c.create_task(p["project_id"], "write code", ["src/a.py"], kind="code")

    e = c.start_execution(t["id"], "agent-a", workspace="/w/a")
    c.finish_execution(e["execution_id"], "succeeded", "done")
    show("task lifecycle after run", c.get_task(t["id"])["lifecycle"])
    cand = c.create_candidate(
        t["id"], "repo1", "base-abc", "cand-def",
        author_execution_id=e["execution_id"],
    )
    show("candidate created", cand)
    show("freeze", c.freeze_candidate(cand["candidate_id"]))

    show("evidence (platform-test, bound to cand-def)", c.record_evidence(
        cand["candidate_id"], "test", command="pytest -q", exit_code=0,
        output_ref="artifacts/pytest.log", sha="cand-def",
        check_id="platform-test",
    ))
    gate = c.evaluate_candidate_gate(cand["candidate_id"])
    show("gate", gate)
    if not gate["ok"]:
        print("\n[BLOCKED] 缺少真实 Agent 审查证据：本原型还没有审查生产者；")
        print("          demo 不伪造 review，因此停在人工验收之前。")
        return 0
    show("ready", c.mark_candidate_ready(cand["candidate_id"]))
    show("human approve", c.approve_candidate(cand["candidate_id"], "alice", "lgtm"))

    attempt = c.start_merge(cand["candidate_id"], "base-abc", "alice")
    show("merge attempt", attempt)
    show("merge done", c.finish_merge(attempt["attempt_id"], "succeeded", "cand-def"))
    show("final candidate", c.get_candidate(cand["candidate_id"]))
    show("final task lifecycle", c.get_task(t["id"])["lifecycle"])

    try:
        c.start_merge(cand["candidate_id"], "base-abc")
    except CoordinatorError as exc:
        show("cannot merge twice", str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
