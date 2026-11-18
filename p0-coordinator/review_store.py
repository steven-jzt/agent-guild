"""Atomic review attempts, provenance and evidence. No model calls in this module."""
import json
from pathlib import Path
import time
import uuid

SCHEMA = '''
CREATE TABLE IF NOT EXISTS review_runs (
    execution_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
    batch_id TEXT NOT NULL, check_id TEXT NOT NULL, candidate_sha TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'running',
    decision TEXT NOT NULL DEFAULT 'unknown', report_path TEXT NOT NULL DEFAULT '',
    report_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_review_active ON review_runs(task_id) WHERE status='running';
CREATE TABLE IF NOT EXISTS review_fixture_projects (project_id TEXT PRIMARY KEY, root TEXT NOT NULL);
'''


def fail(message):
    from coordinator import CoordinatorError
    raise CoordinatorError(message)


def fixture_allowed(coord, conn, candidate):
    row = conn.execute('SELECT f.root FROM review_fixture_projects f JOIN tasks t ON t.project_id=f.project_id WHERE t.id=?',
                       (candidate['task_id'],)).fetchone()
    if row is None:
        return False
    root = Path(row['root']).resolve()
    if Path(coord.db_path).resolve().parent != root or not root.name.startswith('review-fixture-'):
        return False
    ws = coord._candidate_workspace(conn, candidate)
    return ws is not None and all(Path(ws[key]).resolve().is_relative_to(root) for key in ('path', 'repo_path'))


def begin(coord, candidate_id, check_id, simulated=False, owner=None):
    with coord._tx() as conn:
        candidate = conn.execute('SELECT * FROM candidates WHERE id=?', (candidate_id,)).fetchone()
        if candidate is None or candidate['status'] not in ('validating', 'awaiting_human'):
            fail('review requires a frozen candidate in validation')
        batch = coord._active_batch_row(conn, candidate)
        if batch is None or batch['status'] != 'open' or batch['candidate_sha'] != candidate['candidate_sha']:
            fail('review requires the current open validation batch')
        checks = coord._required_checks_for_task(conn, candidate['task_id'])
        if not any(c['id'] == check_id and c['kind'] == 'review' for c in checks):
            fail('review check_id must identify a required review check')
        if simulated and not fixture_allowed(coord, conn, candidate):
            fail('offline review requires a registered isolated fixture project')
        if conn.execute("SELECT 1 FROM executions WHERE task_id=? AND role IN ('author','reviewer') AND status='running'",
                        (candidate['task_id'],)).fetchone():
            fail('task already has an active author or reviewer execution')
        generation = conn.execute('SELECT COALESCE(MAX(generation),0)+1 FROM executions WHERE task_id=?',
                                  (candidate['task_id'],)).fetchone()[0]
        eid = 'E-' + uuid.uuid4().hex[:12]
        meta = {**(owner or {}), 'review': {'candidate_id': candidate_id, 'candidate_sha': candidate['candidate_sha'],
                                        'batch_id': batch['id'], 'check_id': check_id, 'simulated': bool(simulated)}}
        now = time.time()
        conn.execute("INSERT INTO executions(id,task_id,agent,role,workspace,status,generation,started_at,meta) VALUES (?,?,?,'reviewer','','running',?,?,?)",
                     (eid, candidate['task_id'], 'codex-appserver-review', generation, now, json.dumps(meta)))
        conn.execute('INSERT INTO review_runs(execution_id,task_id,candidate_id,batch_id,check_id,candidate_sha,simulated,created_at) VALUES (?,?,?,?,?,?,?,?)',
                     (eid, candidate['task_id'], candidate_id, batch['id'], check_id, candidate['candidate_sha'], int(simulated), now))
        conn.execute("UPDATE candidates SET status='validating' WHERE id=?", (candidate_id,))
        # A pending attempt supersedes old passes immediately, in the same transaction.
        coord._record_evidence_in_tx(conn, candidate_id, 'review', command='agent-review:pending',
                                     sha=candidate['candidate_sha'], execution_id=eid,
                                     check_id=check_id, batch_id=batch['id'])
        coord._event(conn, 'review_started', candidate['task_id'], 'system', meta['review'] | {'execution_id': eid})
        return coord._execution(conn.execute('SELECT * FROM executions WHERE id=?', (eid,)).fetchone())


def current(conn, row):
    candidate = conn.execute('SELECT * FROM candidates WHERE id=?', (row['candidate_id'],)).fetchone()
    batch = conn.execute('SELECT * FROM validation_batches WHERE id=?', (row['batch_id'],)).fetchone()
    return (candidate is not None and batch is not None and candidate['status'] == 'validating'
            and candidate['active_batch_id'] == row['batch_id'] and batch['status'] == 'open'
            and candidate['candidate_sha'] == row['candidate_sha'] and batch['candidate_sha'] == row['candidate_sha'])


def finish(coord, execution_id, status, report, report_path):
    with coord._tx() as conn:
        row = conn.execute('SELECT * FROM review_runs WHERE execution_id=?', (execution_id,)).fetchone()
        ex = conn.execute('SELECT status FROM executions WHERE id=?', (execution_id,)).fetchone()
        if row is None or row['status'] != 'running' or ex is None or ex['status'] != 'running':
            fail('review attempt has ended; late results are ignored')
        fresh = current(conn, row)
        decision = report.get('decision', 'unknown') if fresh else 'unknown'
        if not fresh:
            status = 'interrupted'
            report = {**report, 'decision': 'unknown', 'error': 'validation batch or candidate changed; result discarded'}
        # Decide freshness before publishing the report; a discarded pass must not
        # remain a pass in the human-readable artifact either.
        report_path = Path(report_path)
        temporary = report_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(report_path)
        conn.execute('UPDATE review_runs SET status=?,decision=?,report_path=?,report_json=? WHERE execution_id=?',
                     (status, decision, str(report_path), json.dumps(report, ensure_ascii=False), execution_id))
        if fresh:
            code = 0 if status == 'succeeded' and decision == 'pass' else 1
            coord._record_evidence_in_tx(conn, row['candidate_id'], 'review', command='agent-review:codex-appserver',
                                         cwd='', exit_code=code, output_ref=str(report_path), sha=row['candidate_sha'],
                                         execution_id=execution_id, check_id=row['check_id'], batch_id=row['batch_id'])
        reason = report.get('error') or decision
        return coord._finish_execution_in_tx(conn, execution_id, status, reason)


def interrupt(coord, conn, execution, reason):
    row = conn.execute('SELECT * FROM review_runs WHERE execution_id=?', (execution['id'],)).fetchone()
    if row is None or row['status'] != 'running':
        return
    conn.execute("UPDATE review_runs SET status='interrupted',decision='unknown',report_json=? WHERE execution_id=?",
                 (json.dumps({'decision': 'unknown', 'error': reason, 'simulated': bool(row['simulated'])}), execution['id']))
    if current(conn, row):
        coord._record_evidence_in_tx(conn, row['candidate_id'], 'review', command='agent-review:interrupted',
                                     exit_code=1, sha=row['candidate_sha'], execution_id=execution['id'],
                                     check_id=row['check_id'], batch_id=row['batch_id'])


def gate_reason(coord, conn, candidate, batch, check, evidence):
    row = conn.execute('SELECT * FROM review_runs WHERE candidate_id=? AND batch_id=? AND check_id=? ORDER BY rowid DESC LIMIT 1',
                       (candidate['id'], batch['id'], check['id'])).fetchone()
    if row is None:
        return ''  # Preserve existing manually recorded and historical evidence semantics.
    if row['status'] == 'running':
        return 'review is still running'
    if evidence is None or evidence['execution_id'] != row['execution_id']:
        return 'review evidence is not from the latest attempt'
    if row['simulated'] and not fixture_allowed(coord, conn, candidate):
        return 'simulated evidence is not valid for a normal project'
    if row['candidate_sha'] != candidate['candidate_sha'] or row['status'] != 'succeeded' or row['decision'] != 'pass':
        return 'review did not produce a valid pass'
    return ''


def list_runs(coord, candidate_id=None):
    with coord._conn() as conn:
        query = 'SELECT * FROM review_runs'
        rows = conn.execute(query + (' WHERE candidate_id=?' if candidate_id else '') + ' ORDER BY created_at',
                            (candidate_id,) if candidate_id else ()).fetchall()
    return [{**dict(row), 'simulated': bool(row['simulated']), 'report': json.loads(row['report_json'])} for row in rows]
