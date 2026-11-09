import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { CodexAdapter } from '../adapters/codex.ts';
import { DshAdapter } from '../adapters/dsh.ts';
import { createExperimentRoot, prepareExperiment, git, expenseTask } from './workspace.ts';
import { dshLaunch } from './dsh-launch.ts';
import { redact } from './evidence.ts';
import { execFileSync } from 'node:child_process';

const route = process.argv[2];
if (route !== 'codex' && route !== 'dsh-account') throw new Error('Use: npm run m0:cancel -- codex|dsh-account');
const root = await createExperimentRoot(`m0-cancel-${route}-`);
const fixture = await prepareExperiment(root);
const report: Record<string, unknown> = { route: `${route}-cancel-resume`, root, status: 'running', startedAt: new Date().toISOString() };
const events: unknown[] = [];
let ready!: () => void;
const started = new Promise<void>(resolve => ready = resolve);
let releaseQuestion: ((result: unknown) => void) | undefined;
const codexLauncher = join(process.env.APPDATA!, 'npm', 'node_modules', '@openai', 'codex', 'bin', 'codex.js');
const launch = route === 'dsh-account' ? await dshLaunch(root, fixture.workspace) : undefined;
const adapter = launch ? new DshAdapter(launch.command, launch.args, fixture.workspace, launch.env,
  async method => {
    if (method === 'user-question/ask') { ready(); return await new Promise(resolve => releaseQuestion = resolve); }
    return { outcome: 'rejected' };
  }) : new CodexAdapter(process.execPath, [codexLauncher, 'app-server', '--listen', 'stdio://'], fixture.workspace);
let resumed: CodexAdapter | DshAdapter | undefined;
let timer: NodeJS.Timeout | undefined;
console.log(`Cancellation/resume experiment: ${root}`);
try {
  if (adapter instanceof CodexAdapter) await adapter.initialize();
  else await adapter.initialize(fixture.workspace, 'deepseek-account', 'deepseek-flash');
  const running = adapter instanceof CodexAdapter
    ? adapter.run(fixture.workspace, '只执行一个shell命令：node -e "console.log(\'M0_RUNNING\');setTimeout(()=>{},60000)"。不修改文件、不联网、不使用子Agent。', frame => {
      events.push(frame);
      if (frame.method === 'item/commandExecution/outputDelta' && JSON.stringify(frame.params).includes('M0_RUNNING')) ready();
    }).then(result => { report.initialResult = result; return 'completed'; }, error => { report.initialRunError = String(error); return 'interrupted'; })
    : adapter.run(randomUUID(), '请只调用 ask_user_question，提问 M0_CANCEL_CHECK 并提供选项 Continue。等待答复，不修改任何文件、不执行其他工具。').then(() => 'completed', () => 'interrupted');
  await Promise.race([started, running.then(() => { throw new Error('Execution ended before cancellation checkpoint'); }), new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('Cancellation checkpoint timed out')), 60000); })]);
  clearTimeout(timer);
  const pid = adapter.rpc.child.pid!;
  await adapter.close(); releaseQuestion?.({ cancelled: true });
  await running;
  let hostAlive = true;
  try { process.kill(pid, 0); } catch { hostAlive = false; }
  if (hostAlive) throw new Error('Execution host survived cancellation');
  if (git(fixture.workspace, ['status', '--porcelain'])) throw new Error('Cancellation fixture was unexpectedly modified');
  report.cancelledHostStopped = true;
  console.log('Active execution stopped; starting a fresh runtime on the preserved workspace.');
  if (route === 'codex') {
    resumed = new CodexAdapter(process.execPath, [codexLauncher, 'app-server', '--listen', 'stdio://'], fixture.workspace);
    await resumed.initialize(); await resumed.run(fixture.workspace, `前次运行已取消，工作区保留且尚无代码修改。本次作为新的执行继续：${expenseTask}`);
  } else {
    resumed = new DshAdapter(launch!.command, launch!.args, fixture.workspace, launch!.env);
    await resumed.initialize(fixture.workspace, 'deepseek-account', 'deepseek-flash');
    await resumed.run(randomUUID(), `前次运行已取消，工作区保留且尚无代码修改。本次作为新的执行继续：${expenseTask}`);
  }
  await resumed.close();
  const testOutput = execFileSync(process.execPath, ['--test', 'spec/total.test.mjs'], { cwd: fixture.workspace, encoding: 'utf8', windowsHide: true });
  if (git(fixture.source, ['status', '--porcelain']) || git(fixture.source, ['rev-parse', 'HEAD']).trim() !== fixture.baseline) throw new Error('Source repository changed');
  const changed = [...git(fixture.workspace, ['diff', '--name-only', '-z']).split('\0'), ...git(fixture.workspace, ['ls-files', '--others', '--exclude-standard', '-z']).split('\0')].filter(Boolean);
  if (changed.length !== 1 || changed[0] !== 'src/total.ts') throw new Error('Resume changed files outside its scope');
  Object.assign(report, { status: 'passed', freshRuntimeResumed: true, sourceUnchanged: true, testOutput });
} catch (error) {
  report.status = 'failed'; report.error = error instanceof Error ? error.message : String(error); process.exitCode = 1;
} finally {
  clearTimeout(timer); await adapter.close(); releaseQuestion?.({ cancelled: true }); if (resumed) await resumed.close();
  report.finishedAt = new Date().toISOString();
  await writeFile(join(root, 'report.json'), JSON.stringify(redact(report), null, 2));
  await writeFile(join(root, 'events.json'), JSON.stringify(redact(events), null, 2));
  console.log(JSON.stringify(redact(report), null, 2));
}
