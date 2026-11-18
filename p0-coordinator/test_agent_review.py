"""Only isolated, registered fixture projects receive simulated review evidence."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from coordinator import Coordinator, CoordinatorError


class AgentReviewTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parent / '.tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root, prefix='review-fixture-')
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        (self.source / 'src').mkdir(parents=True)
        self.git(self.source, 'init', '-b', 'main')
        self.git(self.source, 'config', 'user.name', 'fixture')
        self.git(self.source, 'config', 'user.email', 'fixture@local')
        (self.source / 'src/a.py').write_text('value = 1\n')
        (self.source / 'src/审查.py').write_text('value = 1\n', encoding='utf-8')
        self.git(self.source, 'add', '.')
        self.git(self.source, 'commit', '-m', 'base')
        self.c = Coordinator(self.root / 'offline.sqlite')
        project = self.c.create_project('OFFLINE REVIEW FIXTURE')
        self.project = project['project_id']
        self.task = self.c.create_task(self.project, 'change value', ['src/a.py'], repo_id='r')
        self.author = self.c.start_execution(self.task['id'], 'fixture-author')
        self.ws = self.c.create_workspace(self.task['id'], 'r', str(self.source),
                                          path=str(self.root / 'author'), execution_id=self.author['execution_id'])
        (Path(self.ws['path']) / 'src/a.py').write_text('value = 2\n')
        self.c.commit_workspace(self.ws['workspace_id'])
        self.c.finish_execution(self.author['execution_id'], 'succeeded')
        self.candidate = self.c.create_candidate_from_workspace(self.ws['workspace_id'])
        self.cid = self.candidate['candidate_id']
        self.c.freeze_candidate(self.cid)
        self.c.record_evidence(self.cid, 'test', check_id='platform-test', sha=self.candidate['candidate_sha'], exit_code=0)

    @staticmethod
    def git(path, *args):
        result = subprocess.run(['git','-C',str(path),*args], capture_output=True, text=True, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    def tearDown(self):
        self.temp.cleanup()

    def register(self):
        with self.c._tx() as conn:
            conn.execute('INSERT INTO review_fixture_projects(project_id,root) VALUES (?,?)', (self.project, str(self.root)))

    def run_review(self, scenario='pass'):
        from agent_review import review_candidate
        return review_candidate(self.c, self.cid, offline_scenario=scenario, timeout=3)

    def test_completed_review_with_defect_is_successful_execution_but_failed_gate(self):
        self.register()
        out = self.run_review('defect')
        self.assertEqual(out['execution']['status'], 'succeeded')
        self.assertEqual(out['decision'], 'request_changes')
        self.assertFalse(out['gate']['ok'])
        self.assertEqual(out['report']['findings'][0]['path'], 'src/a.py')
        self.assertEqual(self.c.get_execution(self.author['execution_id'])['status'], 'succeeded')
        self.assertEqual(self.c.get_task(self.task['id'])['lifecycle'], 'auto_review')

    def test_pass_waits_for_human_and_does_not_change_source(self):
        self.register()
        before = self.git(self.source, 'show-ref')
        out = self.run_review()
        self.assertTrue(out['simulated'])
        self.assertEqual(out['decision'], 'pass')
        self.assertTrue(out['gate']['ok'])
        self.assertEqual(self.c.get_candidate(self.cid)['status'], 'awaiting_human')
        self.assertEqual(self.c.list_merge_attempts(self.cid), [])
        self.assertEqual(self.git(self.source, 'show-ref'), before)

    def test_running_rereview_blocks_prior_success_and_duplicate_or_author_start(self):
        self.register()
        self.run_review()
        ex = self.c.begin_candidate_review(self.cid, 'agent-review', simulated=True)
        self.assertFalse(self.c.evaluate_candidate_gate(self.cid)['ok'])
        with self.assertRaises(CoordinatorError):
            self.c.approve_candidate(self.cid, 'human')
        with self.assertRaises(CoordinatorError):
            self.c.begin_candidate_review(self.cid, 'agent-review', simulated=True)
        with self.assertRaises(CoordinatorError):
            self.c.start_execution(self.task['id'], 'new-author')
        self.c.finish_execution(ex['execution_id'], 'interrupted', 'controlled interruption')
        self.assertFalse(self.c.evaluate_candidate_gate(self.cid)['ok'])
        self.assertEqual(self.c.get_execution(self.author['execution_id'])['status'], 'succeeded')
        self.assertEqual(self.c.get_task(self.task['id'])['lifecycle'], 'human_review')

    def test_offline_review_cannot_run_in_normal_project(self):
        with self.assertRaises(CoordinatorError):
            self.run_review()
        self.assertEqual(len(self.c.list_executions(self.task['id'])), 1)

    def test_reviewer_readonly_override_does_not_change_author_configuration(self):
        from adapters import CodexAppServerAdapter
        with patch.dict('os.environ', {'CODEX_APP_SERVER_SANDBOX': 'danger-full-access'}):
            reviewer = CodexAppServerAdapter(sandbox='read-only')
            self.assertEqual(reviewer.sandbox_mode(), 'read-only')
            self.assertEqual(CodexAppServerAdapter().sandbox_mode(), 'danger-full-access')

    def test_interfaces_expose_manual_review_and_state(self):
        import worker_cli
        import mcp_server
        names = {tool['name'] for tool in mcp_server.TOOLS}
        self.assertTrue({'review_candidate', 'list_reviews'} <= names)
        args = worker_cli.build_parser().parse_args(['review-candidate', '--candidate', self.cid,
                                                    '--check-id', 'agent-review', '--timeout', '2'])
        self.assertEqual(args.timeout, 2)

    def test_mcp_review_and_cli_report_use_same_persisted_execution(self):
        import mcp_server
        self.register()
        with patch.dict('os.environ', {'GUILD_REVIEW_OFFLINE_SCENARIO':'pass'}):
            out = mcp_server.call_tool(self.c, 'review_candidate',
                {'candidate_id':self.cid, 'check_id':'agent-review', 'timeout':3})
        listed = mcp_server.call_tool(self.c, 'list_reviews', {'candidate_id':self.cid})
        self.assertEqual(listed[-1]['execution_id'], out['execution_id'])
        result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).with_name('worker_cli.py')),
            '--db', self.c.db_path, 'reviews', '--candidate', self.cid], capture_output=True,
            encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['reviews'][-1]['execution_id'], out['execution_id'])

    def test_untrusted_results_never_pass(self):
        self.register()
        for scenario in ('invalid', 'failed', 'interrupted', 'tamper', 'wrong-sha',
                         'wrong-batch', 'wrong-turn', 'contradictory'):
            with self.subTest(scenario=scenario):
                out = self.run_review(scenario)
                self.assertFalse(out['gate']['ok'])
                self.assertNotEqual(out['decision'], 'pass')
                self.assertEqual(self.c.get_execution(self.author['execution_id'])['status'], 'succeeded')

    def test_advice_passes_and_simulation_registry_is_rechecked(self):
        self.register()
        out = self.run_review('advice')
        self.assertTrue(out['gate']['ok'])
        self.assertEqual(out['report']['findings'][0]['severity'], 'P3')
        with self.c._tx() as conn:
            conn.execute('DELETE FROM review_fixture_projects')
        self.assertFalse(self.c.evaluate_candidate_gate(self.cid)['ok'])

    def test_raw_trace_and_report_redact_credentials(self):
        self.register()
        out = self.run_review('secret')
        for name in ('report.json', 'events.json'):
            content = Path(out['report_path']).with_name(name).read_text(encoding='utf-8')
            self.assertNotIn('sk-', content)
            self.assertNotIn('secret-token', content)

    def test_batch_change_discards_late_pass(self):
        from adapters import CodexAppServerAdapter
        original = CodexAppServerAdapter.wait
        def change_batch(adapter, *args, **kwargs):
            result = original(adapter, *args, **kwargs)
            self.c.begin_validation_batch(self.cid, 'changed during review')
            return result
        self.register()
        with patch.object(CodexAppServerAdapter, 'wait', change_batch):
            out = self.run_review()
        self.assertEqual(out['decision'], 'unknown')
        self.assertFalse(out['gate']['ok'])
        self.assertEqual(json.loads(Path(out['report_path']).read_text(encoding='utf-8'))['decision'], 'unknown')

    def test_reviewer_recovery_keeps_author_and_allows_explicit_retry(self):
        self.register()
        ex = self.c.begin_candidate_review(self.cid, simulated=True)
        self.c.reconcile_execution(ex['execution_id'], ex['generation'], 'fixture process stopped',
                                   confirm_local_stopped=True)
        self.assertEqual(self.c.get_execution(self.author['execution_id'])['status'], 'succeeded')
        self.assertFalse(self.c.evaluate_candidate_gate(self.cid)['ok'])
        self.assertTrue(self.run_review()['gate']['ok'])

    def test_timeout_does_not_retry_or_release_gate(self):
        from agent_review import review_candidate
        self.register()
        out = review_candidate(self.c, self.cid, timeout=.2, offline_scenario='timeout')
        self.assertEqual(out['decision'], 'unknown')
        self.assertFalse(out['gate']['ok'])
        self.assertEqual(len(self.c.list_reviews(self.cid)), 1)

    def test_start_failure_keeps_author_success_and_blocks(self):
        self.register()
        with patch('agent_review.CodexAppServerAdapter.start', side_effect=RuntimeError('controlled start failure')):
            out = self.run_review()
        self.assertEqual(out['status'], 'failed')
        self.assertFalse(out['gate']['ok'])
        self.assertEqual(self.c.get_execution(self.author['execution_id'])['status'], 'succeeded')

    def test_review_cannot_overlap_running_author(self):
        self.register()
        self.c.start_execution(self.task['id'], 'new-author')
        with self.assertRaisesRegex(CoordinatorError, 'active author or reviewer'):
            self.c.begin_candidate_review(self.cid, simulated=True)
        self.assertEqual(self.c.list_reviews(self.cid), [])

    def test_snapshot_fingerprint_distinguishes_binary_file_boundaries(self):
        from agent_review import fingerprint
        path = Path(self.ws['path'])
        (path / 'a').write_bytes(b'payload\0b\0tail')
        before = fingerprint(self.c, path)
        (path / 'a').write_bytes(b'payload')
        (path / 'b').write_bytes(b'tail')
        self.assertNotEqual(fingerprint(self.c, path), before)

    def test_report_accepts_actual_unicode_file_location(self):
        self.register()
        report = {'candidate_sha':self.candidate['candidate_sha'],
                  'batch_id':self.c.get_candidate(self.cid)['active_batch_id'],
                  'decision':'request_changes', 'summary':'OFFLINE unicode finding',
                  'findings':[{'severity':'P2', 'path':'src/审查.py', 'line':1, 'message':'受控缺陷'}]}
        with patch('agent_review.final_text', return_value=json.dumps(report, ensure_ascii=False)):
            out = self.run_review()
        self.assertEqual(out['status'], 'succeeded')
        self.assertEqual(out['report']['findings'][0]['path'], 'src/审查.py')

    def test_late_finish_after_recovery_cannot_overwrite_interruption(self):
        import review_store
        self.register()
        ex = self.c.begin_candidate_review(self.cid, simulated=True)
        self.c.reconcile_execution(ex['execution_id'], ex['generation'], 'checked stopped', confirm_local_stopped=True)
        with self.assertRaisesRegex(CoordinatorError, 'late results'):
            review_store.finish(self.c, ex['execution_id'], 'succeeded', {'decision':'pass'}, 'late.json')
        self.assertFalse(self.c.evaluate_candidate_gate(self.cid)['ok'])

    def test_author_test_pass_waits_for_review_without_failure(self):
        import agent_runner
        task = self.c.create_task(self.project, 'another edit', ['src/a.py'], repo_id='r2')
        ws = self.c.create_workspace(task['id'], 'r2', str(self.source), path=str(self.root / 'second-author'))
        self.c.reserve_files(task['id'], 'r2', ['src/a.py'])
        class OfflineAuthor:
            def start(adapter, *args, **kwargs):
                (Path(ws['path']) / 'src/a.py').write_text('value = 3\n')
                return {'run_id':'offline'}
            def wait(adapter, *args, **kwargs):
                return {'status':'completed', 'exit_code':0}
            def events(adapter, *args):
                return []
        with patch.object(agent_runner, '_adapter', return_value=(OfflineAuthor(), 'fixture-author')), \
             patch.object(agent_runner, '_adapter_env', return_value={}):
            out = agent_runner.run_agent(self.c, task['id'], 'fixture', check='git status --porcelain')
        self.assertTrue(out['ok'], out)
        self.assertEqual(out['candidate_status'], 'validating')
        self.assertEqual(self.c.get_execution(out['execution_id'])['status'], 'succeeded')
        self.assertIn('等待审查', out['message'])


if __name__ == '__main__':
    unittest.main()
