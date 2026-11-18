"""Read-only local process/receipt inspection; never reruns business operations."""
import ctypes
import json
import os
from pathlib import Path
import sys


def process_state(pid, started):
    """Match a process instance by PID and Windows creation FILETIME, not PID alone."""
    if (sys.platform != 'win32' or type(pid) is not int or pid <= 0
            or not isinstance(started, (str, int)) or not str(started).isdigit()
            or not 0 < int(started) < 2**64):
        return 'unknown'
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
    if not handle:
        return 'stopped' if ctypes.get_last_error() == 87 else 'unknown'
    try:
        times = [ctypes.c_ulonglong() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return 'unknown'
        if str(times[0].value) != str(started):
            return 'stopped'  # This PID now belongs to a different instance.
        wait = kernel.WaitForSingleObject(handle, 0)
        return 'stopped' if wait == 0 else 'running' if wait == 258 else 'unknown'
    finally:
        kernel.CloseHandle(handle)


def current_process_started():
    if sys.platform != 'win32':
        return ''
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
    times = [ctypes.c_ulonglong() for _ in range(4)]
    if kernel.GetProcessTimes(kernel.GetCurrentProcess(), *(ctypes.byref(t) for t in times)):
        return str(times[0].value)
    return ''


def execution_owner():
    request_id = os.environ.get('GUILD_REQUEST_ID', '')
    path = os.environ.get('GUILD_REQUEST_RECEIPT', '')
    if not request_id or not path:
        return {}
    return {'local_request': {'request_id': request_id, 'receipt_path': path,
                              'worker_pid': os.getpid(), 'worker_started': current_process_started()}}


def inspect_execution(execution, receipt_directory):
    meta = execution.get('meta')
    owner = meta.get('local_request') if isinstance(meta, dict) else None
    if not isinstance(owner, dict):
        return 'unknown'
    if process_state(owner.get('worker_pid'), owner.get('worker_started')) == 'running':
        return 'running'
    try:
        path = Path(owner['receipt_path']).resolve()
        request_id = owner['request_id']
        if path.parent != Path(receipt_directory).resolve() or path.name != request_id + '.json':
            return 'unknown'
        if path.stat().st_size > 65536:
            return 'unknown'
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            return 'unknown'
        if data.get('version') != 1 or data.get('ownership_version') != 2 or data.get('request_id') != request_id:
            return 'unknown'
        states = [process_state(data.get('host_pid'), data.get('host_started')),
                  process_state(owner.get('worker_pid'), owner.get('worker_started'))]
        if 'running' in states:
            return 'running'
        return 'stopped' if states == ['stopped', 'stopped'] else 'unknown'
    except (KeyError, ValueError, OSError, TypeError):
        return 'unknown'


def report(coord, project_id=None):
    executions, merges = [], []
    with coord._conn() as conn:
        query = "SELECT e.* FROM executions e JOIN tasks t ON t.id=e.task_id WHERE e.status='running'"
        args = ()
        if project_id:
            query += ' AND t.project_id=?'
            args = (project_id,)
        rows = conn.execute(query, args).fetchall()
        for raw in rows:
            ex = coord._execution(raw)
            latest = conn.execute('SELECT MAX(generation) FROM executions WHERE task_id=?', (ex['task_id'],)).fetchone()[0]
            state = inspect_execution(ex, Path(coord.db_path).resolve().parent / 'request-receipts')
            current = ex['generation'] == latest and ex['role'] in ('author', 'reviewer')
            workspace = next((w for w in coord.list_workspaces(ex['task_id']) if w['path'] == ex['workspace']), None)
            changes, error = [], ''
            if workspace:
                try:
                    changes = coord.workspace_changed_files(workspace['workspace_id'])
                except Exception as exc:
                    error = str(exc)
            executions.append({**ex, 'local_state': state, 'can_reconcile': current and state == 'stopped',
                               'manual_confirmation_allowed': current and state == 'unknown',
                               'workspace_changes': changes, 'workspace_error': error})
        query = "SELECT m.* FROM merge_attempts m JOIN candidates c ON c.id=m.candidate_id JOIN tasks t ON t.id=c.task_id WHERE m.status IN ('pending','unknown')"
        if project_id:
            query += ' AND t.project_id=?'
        for raw in conn.execute(query, args):
            attempt = coord._merge_attempt(raw)
            actual, state, error = '', 'unknown', ''
            try:
                actual = coord._git(attempt['repo_path'], 'rev-parse', '--verify', attempt['target_ref']).stdout.strip()
                if actual == attempt['git_result_sha'] and actual:
                    state = 'published'
                elif actual == attempt['target_before_sha']:
                    state = 'prepared' if attempt['git_result_sha'] else 'unprepared'
                else:
                    state = 'target_changed'
            except Exception as exc:
                error = str(exc)
            merges.append({**attempt, 'actual_target_sha': actual, 'recovery_state': state,
                           'inspection_error': error, 'can_confirm': state == 'published',
                           'can_abandon': state == 'prepared' and attempt['status'] == 'pending'})
    return {'executions': executions, 'merges': merges}
