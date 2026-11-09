import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { test } from 'node:test';
import { managedSpawn } from '../src/transport/managed-process.ts';

function alive(pid: number) { try { process.kill(pid, 0); return true; } catch { return false; } }

test('managed process preserves unicode, spaces, quotes and trailing backslashes', async () => {
  const args = ['中文 空格', '引号"值', 'trailing\\'];
  const child = managedSpawn(process.execPath, ['-e', 'console.log(JSON.stringify(process.argv.slice(1)))', ...args]);
  let output = '';
  child.stdout.on('data', chunk => output += chunk);
  const [code] = await once(child, 'exit');
  assert.equal(code, 0);
  assert.deepEqual(JSON.parse(output), args);
});

test('terminates the worker and its grandchild when the owning parent dies', { skip: process.platform !== 'win32', timeout: 15000 }, async () => {
  const parent = spawn(process.execPath, ['--experimental-strip-types', fileURLToPath(new URL('./fixtures/process-parent.ts', import.meta.url))], { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  let ids: { pid: number; grandchild: number } | undefined;
  try {
    const line = await new Promise<string>((resolve, reject) => {
      let output = '';
      const timer = setTimeout(() => reject(new Error('worker did not start')), 7000);
      parent.stdout.on('data', chunk => { output += chunk; if (output.includes('\n')) { clearTimeout(timer); resolve(output.split('\n')[0]!); } });
      parent.on('error', reject);
    });
    ids = JSON.parse(line);
    assert.ok(ids && alive(ids.pid) && alive(ids.grandchild));
    parent.kill();
    for (let attempt = 0; attempt < 30 && (alive(ids.pid) || alive(ids.grandchild)); attempt++) await delay(100);
    assert.equal(alive(ids.pid), false, 'worker must not survive its owner');
    assert.equal(alive(ids.grandchild), false, 'grandchild must not survive its owner');
  } finally {
    parent.kill();
    if (ids) for (const pid of [ids.pid, ids.grandchild]) if (alive(pid)) {
      try { execFileSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true }); } catch { /* already exited */ }
    }
  }
});
