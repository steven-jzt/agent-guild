import { HarnessClient } from '@deepseek-ai/dsh-sdk-client';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { windowsHost } from '../transport/managed-process.ts';
import { createExperimentRoot, prepareExperiment, git, expenseTask } from './workspace.ts';
import { redact } from './evidence.ts';
import { dshFailure } from '../adapters/dsh-outcome.ts';
import { dshLaunch } from './dsh-launch.ts';

const mode = process.argv[2];
if (mode !== 'api-key' && mode !== 'account') throw new Error('Use: npm run m0:dsh -- api-key|account');
const root = await createExperimentRoot(`m0-dsh-${mode}-`);
const fixture = await prepareExperiment(root);
const launch = await dshLaunch(root, fixture.workspace);
const client = new HarnessClient({
  command: process.platform === 'win32' ? windowsHost() : process.execPath,
  args: process.platform === 'win32' ? [String(process.pid), process.execPath, ...launch.args] : launch.args,
  cwd: fixture.workspace,
  env: launch.env,
  requestTimeoutMs: 30000, shutdownTimeoutMs: 2000, disposeEofGraceMs: 1500, disposeGraceMs: 3000,
});
const notifications: unknown[] = [];
const report: Record<string, unknown> = { route: `dsh-${mode}`, root, runtimeVersion: '0.2.2-rc.1', clientVersion: '0.0.1-rc.1', startedAt: new Date().toISOString(), status: 'running' };
console.log(`DSH ${mode} experiment: ${root}`);
let observer: Promise<void> | undefined;
try {
  const initialized = await client.initialize({ cwd: fixture.workspace, provider: mode === 'account' ? 'deepseek-account' : 'deepseek-official', model: 'deepseek-flash', maxTokens: 8192 });
  report.handshake = initialized;
  console.log('Handshake passed; sending isolated expense task.');
  const subscription = client.subscribe();
  observer = (async () => {
    try { for await (const notification of subscription) { notifications.push(notification); if (notification.method === 'session.status') console.log('DSH session status received'); } }
    catch { /* close is expected after evidence collection */ }
  })();
  const sessionId = randomUUID();
  report.sessionId = sessionId;
  report.messageId = await client.prompt(sessionId, [{ type: 'text', text: expenseTask }]);
  await client.request('session/wait', { sessionId }, 180000);
  const failure = dshFailure(notifications, sessionId);
  if (failure) throw failure;
  await client.close(); await observer;
  const changed = [...git(fixture.workspace, ['diff', '--name-only', '-z']).split('\0'), ...git(fixture.workspace, ['ls-files', '--others', '--exclude-standard', '-z']).split('\0')].filter(Boolean);
  if (changed.length !== 1 || changed[0] !== 'src/total.ts') throw new Error(`Unexpected changed paths: ${changed.join(', ')}`);
  const testOutput = execFileSync(process.execPath, ['--test', 'spec/total.test.mjs'], { cwd: fixture.workspace, encoding: 'utf8', windowsHide: true });
  if (git(fixture.source, ['status', '--porcelain']) || git(fixture.source, ['rev-parse', 'HEAD']).trim() !== fixture.baseline) throw new Error('Source repository changed');
  Object.assign(report, { status: 'passed', changed, testOutput, sourceUnchanged: true });
} catch (error) {
  Object.assign(report, { status: 'failed', error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
} finally {
  await client.close(); if (observer) await observer;
  report.finishedAt = new Date().toISOString();
  await writeFile(join(root, 'report.json'), JSON.stringify(redact(report), null, 2));
  await writeFile(join(root, 'events.json'), JSON.stringify(redact(notifications), null, 2));
  console.log(JSON.stringify(redact(report), null, 2));
}
