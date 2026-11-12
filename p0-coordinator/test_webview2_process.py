"""Windows offline regressions for the exact C# process runner used by WebView2."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent


@unittest.skipUnless(sys.platform == 'win32', 'Windows Job Object integration')
class WebViewProcessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='p14-', dir=ROOT / '.tmp')
        cls.directory = Path(cls.temp.name)
        cls.host = cls.directory / 'JobHost.exe'
        cls.harness = cls.directory / 'Harness.exe'
        csc = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        subprocess.run([str(csc), '/nologo', '/platform:x64', '/out:' + str(cls.host),
                        str(ROOT.parent / 'agent-guild/src/transport/JobHost.cs')], check=True, capture_output=True)
        subprocess.run([str(csc), '/nologo', '/platform:x64', '/out:' + str(cls.harness),
                        str(ROOT / 'webview2/AsyncProcessRunner.cs'),
                        str(ROOT / 'webview2/ProcessRunnerHarness.cs')], check=True, capture_output=True)
        cls.fixture = cls.directory / 'fixture.py'
        cls.fixture.write_text('''import json,os,subprocess,sys,time
mode,pid_file=sys.argv[1:3]
if mode in ('cancel','timeout','owner','childexit'):
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
    with open(pid_file+'.tmp','w') as f: json.dump([os.getpid(),child.pid],f)
    os.replace(pid_file+'.tmp',pid_file)
    if mode!='childexit': time.sleep(60)
elif mode=='parallel': time.sleep(2)
elif mode=='flood':
    sys.stderr.write('e'*1048576); sys.stderr.flush()
    print(json.dumps({'out':'o'*1048576})); sys.exit(0)
elif mode=='limit': print('x'*3145728); sys.exit(0)
elif mode=='error': sys.stderr.write('fixture failure'); sys.exit(7)
print(json.dumps(sys.argv[3:],ensure_ascii=False))
''', encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def pid_file(self, mode):
        return self.directory / (mode + '.json')

    def command(self, mode):
        return [str(self.harness), str(self.host), sys.executable, str(self.fixture), mode, str(self.pid_file(mode))]

    def run_mode(self, mode):
        return subprocess.run(self.command(mode), capture_output=True, encoding='utf-8', timeout=25)

    def assert_tree_gone(self, mode):
        pids = json.loads(self.pid_file(mode).read_text())
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        for pid in pids:
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if handle:
                try:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0, f'PID {pid} survived')
                finally:
                    kernel.CloseHandle(handle)

    def test_unicode_spaces_quotes_and_trailing_slashes(self):
        result = self.run_mode('echo')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ['中文 空格"引号\\'])

    def test_both_pipes_are_drained_without_deadlock(self):
        result = self.run_mode('flood')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['out']), 1048576)

    def test_output_is_bounded(self):
        result = self.run_mode('limit')
        self.assertEqual(result.returncode, 1)
        self.assertIn('output exceeded', result.stderr)

    def test_nonzero_exit_keeps_stderr(self):
        result = self.run_mode('error')
        self.assertEqual(result.returncode, 1)
        self.assertIn('fixture failure', result.stderr)
        self.assertIn('(7)', result.stderr)

    def test_quick_request_can_finish_while_long_request_is_active(self):
        result = self.run_mode('parallel')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cancel_kills_worker_and_grandchild(self):
        result = self.run_mode('cancel')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('unknown', result.stdout)
        self.assert_tree_gone('cancel')

    def test_timeout_kills_tree_and_is_not_success(self):
        result = self.run_mode('timeout')
        self.assertEqual(result.returncode, 1)
        self.assertIn('timed out', result.stderr)
        self.assert_tree_gone('timeout')

    def test_owner_exit_kills_tree(self):
        process = subprocess.Popen(self.command('owner'), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not self.pid_file('owner').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(self.pid_file('owner').exists())
        finally:
            process.kill()
            process.communicate(timeout=10)
        self.assert_tree_gone('owner')

    def test_early_exit_does_not_leave_descendant_holding_pipe_open(self):
        result = self.run_mode('childexit')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_tree_gone('childexit')

    def test_host_killed_during_child_creation_does_not_leave_suspended_orphan(self):
        # Pause immediately after native creation, before the old separate job assignment.
        source = (ROOT.parent / 'agent-guild/src/transport/JobHost.cs').read_text(encoding='utf-8')
        marker = 'out child));'
        self.assertEqual(source.count(marker), 1)
        pid_file = self.pid_file('birth')
        pause = (' System.IO.File.WriteAllText(' + json.dumps(str(pid_file)) +
                 ', "[" + child.Pid + "]"); System.Threading.Thread.Sleep(30000);')
        controlled = self.directory / 'BirthHost.cs'
        controlled.write_text(source.replace(marker, marker + pause), encoding='utf-8')
        host = self.directory / 'BirthHost.exe'
        csc = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        subprocess.run([str(csc), '/nologo', '/platform:x64', '/out:' + str(host), str(controlled)],
                       check=True, capture_output=True)
        process = subprocess.Popen([str(host), str(os.getpid()), sys.executable, '-c',
                                    'import time; time.sleep(60)'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(pid_file.exists(), 'child creation was not reached')
            process.kill()
            process.wait(timeout=5)
            self.assert_tree_gone('birth')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if pid_file.exists():
                for pid in json.loads(pid_file.read_text()):
                    subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
