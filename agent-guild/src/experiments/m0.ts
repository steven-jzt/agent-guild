import { writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { CodexAdapter } from '../adapters/codex.ts';
import { createExperimentRoot, prepareExperiment, git, expenseTask } from './workspace.ts';
import { redact } from './evidence.ts';
import type { RpcFrame } from '../transport/rpc-process.ts';

const root = await createExperimentRoot('m0-codex-');
const fixture = await prepareExperiment(root);
const launcher = process.env.AGENT_GUILD_CODEX_JS ?? join(process.env.APPDATA ?? '', 'npm', 'node_modules', '@openai', 'codex', 'bin', 'codex.js');
if (!existsSync(launcher)) throw new Error('Codex launcher not found; set AGENT_GUILD_CODEX_JS to its codex.js path');
const adapter = new CodexAdapter(process.execPath, [launcher, 'app-server', '--listen', 'stdio://'], fixture.workspace);
const events: RpcFrame[] = [];
const report: Record<string, unknown> = { route: 'codex', startedAt: new Date().toISOString(), root, allowedPaths: ['src/total.ts'], status: 'running' };
console.log(`Codex experiment: ${root}`);
try {
  await adapter.initialize();
  console.log('Handshake passed; sending isolated expense task.');
  const result = await adapter.run(fixture.workspace,
    expenseTask,
    frame => { events.push(frame); if (frame.method === 'item/completed') console.log('Codex item completed'); }, 180000);
  await adapter.close();
  const changed = [...git(fixture.workspace, ['diff', '--name-only', '-z']).split('\0'), ...git(fixture.workspace, ['ls-files', '--others', '--exclude-standard', '-z']).split('\0')].filter(Boolean);
  if (changed.length !== 1 || changed[0] !== 'src/total.ts') throw new Error(`Unexpected changed paths: ${changed.join(', ')}`);
  const testOutput = execFileSync(process.execPath, ['--test', 'spec/total.test.mjs'], { cwd: fixture.workspace, encoding: 'utf8', windowsHide: true });
  if (git(fixture.source, ['status', '--porcelain']) || git(fixture.source, ['rev-parse', 'HEAD']).trim() !== fixture.baseline) throw new Error('Source repository was modified');
  Object.assign(report, { status: 'passed', result, changed, testOutput, sourceUnchanged: true, eventsObserved: [...new Set(events.map(event => event.method))] });
} catch (error) {
  Object.assign(report, { status: 'failed', error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
} finally {
  await adapter.close();
  report.finishedAt = new Date().toISOString();
  await writeFile(join(root, 'report.json'), JSON.stringify(redact({ ...report, diagnostics: adapter.rpc.diagnostics() }), null, 2));
  await writeFile(join(root, 'events.json'), JSON.stringify(redact(events), null, 2));
  console.log(JSON.stringify(redact(report), null, 2));
}
