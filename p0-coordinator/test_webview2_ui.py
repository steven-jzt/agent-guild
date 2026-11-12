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


if __name__ == '__main__':
    unittest.main(verbosity=2)
