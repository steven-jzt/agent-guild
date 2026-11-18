"""Fresh-source WebView2 tests with invisible windows and controlled offline workers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


@unittest.skipUnless(sys.platform == 'win32', 'Windows WebView2 runtime')
class WebViewUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='p14-ui-', dir=ROOT / '.tmp', ignore_cleanup_errors=True)
        cls.root = Path(cls.temp.name)
        cls.bin = cls.root / 'webview2/bin'
        cls.bin.mkdir(parents=True)
        for module in ROOT.glob('*.py'):
            shutil.copy2(module, cls.root / module.name)
        renderer = cls.root / 'electron/renderer'
        shutil.copytree(ROOT / 'electron/renderer', renderer)
        for dll in (ROOT / 'webview2/bin').glob('*.dll'):
            shutil.copy2(dll, cls.bin / dll.name)
        csc = str(Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe')
        subprocess.run([csc, '/nologo', '/platform:x64', '/out:' + str(cls.bin / 'AgentGuildJobHost.exe'),
                        str(ROOT.parent / 'agent-guild/src/transport/JobHost.cs')], check=True, capture_output=True)
        cls.shell = cls.bin / 'Shell.exe'
        refs = ['System.dll', 'System.Core.dll', 'System.Windows.Forms.dll', 'System.Drawing.dll', 'System.Web.Extensions.dll',
                str(cls.bin / 'Microsoft.Web.WebView2.Core.dll'), str(cls.bin / 'Microsoft.Web.WebView2.WinForms.dll')]
        subprocess.run([csc, '/nologo', '/platform:x64', '/out:' + str(cls.shell),
                        *['/r:' + ref for ref in refs], str(ROOT / 'webview2/Program.cs'),
                        str(ROOT / 'webview2/AsyncProcessRunner.cs')], check=True, capture_output=True)
        probe = cls.root / 'Probe.cs'
        probe.write_text(r'''using System; class Probe { static void Main() {
if(Environment.GetEnvironmentVariable("GUILD_UI_PROBE")=="large")
Console.WriteLine("[\""+new string('<',500000)+"\"]");
else { System.Threading.Thread.Sleep(1500); Console.WriteLine("[]"); }
} }''')
        cls.probe = cls.root / 'Probe.exe'
        subprocess.run([csc, '/nologo', '/out:' + str(cls.probe), str(probe)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def smoke(self, flag='--smoke', mode='slow'):
        result = subprocess.run([str(self.shell), flag], capture_output=True, encoding='utf-8', timeout=40,
                                env={**os.environ, 'PYTHON': str(self.probe), 'GUILD_UI_PROBE': mode})
        rows = [json.loads(line.split('WEBVIEW2_SMOKE ', 1)[1]) for line in result.stdout.splitlines()
                if 'WEBVIEW2_SMOKE ' in line]
        self.assertEqual(len(rows), 1, result.stdout + result.stderr)
        return result, rows[0]

    def test_timer_runs_while_real_rpc_waits_for_worker(self):
        result, row = self.smoke()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(row['smoke'])
        self.assertTrue(row['uiResponsive'])

    def test_cancel_round_trip_keeps_window_responsive_and_reports_unknown(self):
        result, row = self.smoke('--smoke-cancel')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(row['uiResponsive'])
        self.assertIn('unknown', row['error'])

    def test_oversized_serialized_response_rejects_instead_of_hanging(self):
        result, row = self.smoke(mode='large')
        self.assertEqual(result.returncode, 1)
        self.assertFalse(row['smoke'])
        self.assertIn('serialized', row['error'])

    def controlled_db(self, name):
        from coordinator import Coordinator
        coord = Coordinator(self.root / (name + '.sqlite'))
        project = coord.create_project('offline native recovery')
        task = coord.create_task(project['project_id'], 'legacy interrupted worker', ['a.txt'])
        ex = coord.start_execution(task['id'], 'controlled')
        return coord, ex

    def test_recovery_panel_renders_real_database_in_hidden_webview(self):
        coord, ex = self.controlled_db('window-recovery')
        result = subprocess.run([str(self.shell), '--smoke-recovery'], capture_output=True, encoding='utf-8', timeout=40,
                                env={**os.environ, 'PYTHON': sys.executable, 'GUILD_DB': coord.db_path})
        rows = [json.loads(line.split('WEBVIEW2_SMOKE ', 1)[1]) for line in result.stdout.splitlines() if 'WEBVIEW2_SMOKE ' in line]
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(rows[0]['smoke'])
        self.assertEqual(rows[0]['data']['executions'][0]['execution_id'], ex['execution_id'])
        self.assertEqual(coord.get_execution(ex['execution_id'])['status'], 'running')

    def test_native_cli_requires_explicit_confirmation_for_legacy_execution(self):
        coord, ex = self.controlled_db('native-reconcile')
        command = [str(self.shell), '--call', 'reconcile_execution', '--execution', ex['execution_id'],
                   '--generation', str(ex['generation']), '--reason', 'offline manual process and workspace inspection']
        env = {**os.environ, 'PYTHON': sys.executable, 'GUILD_DB': coord.db_path}
        denied = subprocess.run(command, capture_output=True, encoding='utf-8', timeout=20, env=env)
        self.assertEqual(denied.returncode, 1, denied.stdout)
        self.assertEqual(coord.get_execution(ex['execution_id'])['status'], 'running')
        accepted = subprocess.run(command + ['--confirm-local-stopped'], capture_output=True, encoding='utf-8', timeout=20, env=env)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)['status'], 'interrupted')
        self.assertEqual(coord.get_task(ex['task_id'])['lifecycle'], 'needs_fix')

    def test_review_button_reports_and_cancel_in_real_hidden_webview(self):
        from test_agent_review import AgentReviewTest
        for scenario in ('pass', 'defect', 'timeout'):
            with self.subTest(scenario=scenario):
                fixture = AgentReviewTest()
                fixture.setUp()
                try:
                    fixture.register()
                    result = subprocess.run([str(self.shell), '--smoke-review'], capture_output=True,
                        encoding='utf-8', timeout=55, env={**os.environ, 'PYTHON': sys.executable,
                        'GUILD_DB': fixture.c.db_path, 'GUILD_REVIEW_OFFLINE_SCENARIO': scenario})
                    rows = [json.loads(line.split('WEBVIEW2_SMOKE ', 1)[1]) for line in result.stdout.splitlines()
                            if 'WEBVIEW2_SMOKE ' in line]
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertTrue(rows[0]['smoke'])
                    self.assertEqual(fixture.c.get_execution(fixture.author['execution_id'])['status'], 'succeeded')
                    if scenario == 'timeout':
                        self.assertTrue(rows[0]['interrupted'])
                        self.assertFalse(fixture.c.evaluate_candidate_gate(fixture.cid)['ok'])
                    else:
                        self.assertEqual(rows[0]['data']['reviews'][-1]['decision'],
                                         'pass' if scenario == 'pass' else 'request_changes')
                finally:
                    fixture.tearDown()


if __name__ == '__main__':
    unittest.main(verbosity=2)
