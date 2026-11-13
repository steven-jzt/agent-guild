"""Recovery is read-only until an explicit, current-generation reconciliation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from coordinator import Coordinator, CoordinatorError


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parent / '.tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root, prefix='recover-')
        self.root = Path(self.temp.name).resolve()
        self.c = Coordinator(self.root / 'state.sqlite')
        self.project = self.c.create_project('recovery')
        self.task = self.c.create_task(self.project['project_id'], 'task', ['a.txt'])
        self.c.reserve_files(self.task['id'], 'repo1', ['a.txt'])
        self.ex = self.c.start_execution(self.task['id'], 'fixture')

    def tearDown(self):
        self.temp.cleanup()

    def test_report_is_read_only_and_legacy_record_remains_unknown(self):
        before = self.c.get_task(self.task['id'])
        report = self.c.recovery_report(self.project['project_id'])
        row = report['executions'][0]
        self.assertEqual(row['execution_id'], self.ex['execution_id'])
        self.assertEqual(row['local_state'], 'unknown')
        self.assertFalse(row['can_reconcile'])
        self.assertEqual(self.c.get_task(self.task['id']), before)
        self.assertEqual(self.c.get_execution(self.ex['execution_id'])['status'], 'running')

    def test_legacy_requires_explicit_manual_confirmation_and_keeps_reservations(self):
        with self.assertRaises(CoordinatorError):
            self.c.reconcile_execution(self.ex['execution_id'], self.ex['generation'], 'checked')
        out = self.c.reconcile_execution(self.ex['execution_id'], self.ex['generation'],
                                         'operator checked local processes and workspace', confirm_local_stopped=True)
        self.assertEqual(out['status'], 'interrupted')
        self.assertEqual(self.c.get_task(self.task['id'])['lifecycle'], 'needs_fix')
        self.assertTrue(self.c.list_file_reservations(task_id=self.task['id'], active_only=True))
        with self.assertRaises(CoordinatorError):
            self.c.reconcile_execution(self.ex['execution_id'], self.ex['generation'], 'repeat', confirm_local_stopped=True)

    def test_stale_generation_and_empty_reason_cannot_finish_execution(self):
        for generation, reason in ((self.ex['generation'] + 1, 'checked'), (self.ex['generation'], ''), (True, 'checked')):
            with self.assertRaises(CoordinatorError):
                self.c.reconcile_execution(self.ex['execution_id'], generation, reason, confirm_local_stopped=True)
        self.assertEqual(self.c.get_execution(self.ex['execution_id'])['status'], 'running')

    def attach_receipt(self):
        directory = self.root / 'request-receipts'
        directory.mkdir(exist_ok=True)
        path = directory / ('a' * 32 + '.json')
        path.write_text(json.dumps({'version': 1, 'ownership_version': 2, 'request_id': 'a' * 32,
                                    'host_pid': 123, 'host_started': '456', 'state': 'unknown'}))
        with self.c._tx() as conn:
            meta = {'local_request': {'request_id': 'a' * 32, 'receipt_path': str(path),
                                      'worker_pid': 789, 'worker_started': '1011'}}
            conn.execute('UPDATE executions SET meta=? WHERE id=?', (json.dumps(meta), self.ex['execution_id']))
        return path

    def test_live_owned_process_cannot_be_overridden_by_manual_confirmation(self):
        self.attach_receipt()
        with patch('recovery.process_state', return_value='running'):
            self.assertEqual(self.c.recovery_report()['executions'][0]['local_state'], 'running')
            with self.assertRaises(CoordinatorError):
                self.c.reconcile_execution(self.ex['execution_id'], self.ex['generation'], 'checked', confirm_local_stopped=True)

    def test_confirmed_dead_owned_process_can_be_reconciled_after_restart(self):
        self.attach_receipt()
        reopened = Coordinator(self.root / 'state.sqlite')
        with patch('recovery.process_state', return_value='stopped'):
            row = reopened.recovery_report()['executions'][0]
            self.assertTrue(row['can_reconcile'])
            self.assertEqual(reopened.reconcile_execution(self.ex['execution_id'], self.ex['generation'], 'checked')['status'], 'interrupted')

    def test_wrong_receipt_id_or_path_cannot_claim_process_is_stopped(self):
        path = self.attach_receipt()
        data = json.loads(path.read_text()); data['request_id'] = 'wrong'
        path.write_text(json.dumps(data))
        with patch('recovery.process_state', return_value='stopped'):
            self.assertEqual(self.c.recovery_report()['executions'][0]['local_state'], 'unknown')

    def test_report_project_filter_and_no_automatic_model_retry(self):
        other = self.c.create_project('other')
        self.assertEqual(self.c.recovery_report(other['project_id'])['executions'], [])
        self.c.recovery_report()
        self.assertEqual(len(self.c.list_executions(self.task['id'])), 1)

    def test_missing_receipt_cannot_override_a_known_live_worker(self):
        path = self.attach_receipt()
        path.unlink()
        with patch('recovery.process_state', return_value='running'):
            with self.assertRaises(CoordinatorError):
                self.c.reconcile_execution(self.ex['execution_id'], self.ex['generation'], 'checked', confirm_local_stopped=True)

    def test_windows_pid_reuse_does_not_match_a_different_creation_time(self):
        import os
        from recovery import process_state, current_process_started
        started = current_process_started()
        if not started:
            self.skipTest('Windows process identity')
        self.assertEqual(process_state(os.getpid(), started), 'running')
        self.assertEqual(process_state(os.getpid(), str(int(started) + 1)), 'stopped')

    def test_cli_and_mcp_expose_the_same_read_only_report(self):
        import worker_cli
        import mcp_server
        args = worker_cli.build_parser().parse_args(['--db', self.c.db_path, 'recovery-report', '--project', self.project['project_id']])
        self.assertEqual(worker_cli.run(args)['executions'][0]['execution_id'], self.ex['execution_id'])
        self.assertEqual(mcp_server.call_tool(self.c, 'recovery_report', {})['executions'][0]['execution_id'], self.ex['execution_id'])
        names = {tool['name'] for tool in mcp_server.TOOLS}
        self.assertTrue({'recovery_report', 'reconcile_execution', 'reconcile_merge'} <= names)

    def test_malformed_legacy_metadata_cannot_break_read_only_recovery(self):
        with self.c._tx() as conn:
            conn.execute('UPDATE executions SET meta=? WHERE id=?', ('{"local_request":"legacy text"}', self.ex['execution_id']))
        self.assertEqual(self.c.recovery_report()['executions'][0]['local_state'], 'unknown')

    def test_malformed_receipt_is_unknown_and_invalid_process_identity_is_not_stopped(self):
        from recovery import process_state
        import os
        path = self.attach_receipt()
        path.write_text('[]')
        with patch('recovery.process_state', return_value='stopped'):
            self.assertEqual(self.c.recovery_report()['executions'][0]['local_state'], 'unknown')
        self.assertEqual(process_state(os.getpid(), 'not-a-creation-time'), 'unknown')


if __name__ == '__main__':
    unittest.main()
