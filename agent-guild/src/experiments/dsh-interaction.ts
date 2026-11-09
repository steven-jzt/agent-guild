import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { DshAdapter } from '../adapters/dsh.ts';
import { isRecord } from '../transport/rpc-process.ts';
import { createExperimentRoot, prepareExperiment, git } from './workspace.ts';
import { redact } from './evidence.ts';
import { dshLaunch } from './dsh-launch.ts';

const root = await createExperimentRoot('m0-dsh-interaction-');
const fixture = await prepareExperiment(root);
const launch = await dshLaunch(root, fixture.workspace, true);
const interactions: { method: string; outcome: string }[] = [];
const events: unknown[] = [];
const report: Record<string, unknown> = { route: 'dsh-account-interaction', root, startedAt: new Date().toISOString(), status: 'running', responder: 'synthetic M0 fixture; not human acceptance' };
const adapter = new DshAdapter(launch.command, launch.args, fixture.workspace, launch.env, async (method, params) => {
    if (method === 'approval/request') { interactions.push({ method, outcome: 'rejected' }); return { outcome: 'rejected' }; }
    if (method === 'user-question/ask' && isRecord(params) && Array.isArray(params.questions)) {
      interactions.push({ method, outcome: 'fixture answer M0-OK' });
      return { answers: params.questions.map(question => {
        if (!isRecord(question) || typeof question.id !== 'string') throw new Error('Invalid test question');
        return { id: question.id, selected: ['M0-OK'] };
      }) };
    }
    return { cancelled: true };
  });
console.log(`DSH interaction experiment: ${root}`);
try {
  await adapter.initialize(fixture.workspace, 'deepseek-account', 'deepseek-flash');
  const notifications = await adapter.run(randomUUID(), '受控协议测试：先调用 ask_user_question，问题为 M0_PROTOCOL_CHECK，提供选项 M0-OK。收到答复后，仅尝试调用 plugin_manager 的 list 操作。按照工具规则，这个只读操作也需要本次审批；预期平台拒绝审批。拒绝后不要重试或换工具。不安装、删除或更新任何插件，不修改文件，不启动子Agent。最后回复 M0_PROTOCOL_DONE。');
  events.push(...notifications);
  if (!interactions.some(item => item.method === 'user-question/ask') || !interactions.some(item => item.method === 'approval/request')) throw new Error('Required question or approval callback not observed');
  if (git(fixture.workspace, ['status', '--porcelain']) || git(fixture.source, ['status', '--porcelain'])) throw new Error('Interaction probe modified repository files');
  Object.assign(report, { status: 'passed', approvalRejected: true, sourceUnchanged: true });
} catch (error) {
  Object.assign(report, { status: 'failed', error: error instanceof Error ? error.message : String(error) }); process.exitCode = 1;
} finally {
  await adapter.close(); report.finishedAt = new Date().toISOString(); report.interactions = interactions;
  await writeFile(join(root, 'report.json'), JSON.stringify(redact({ ...report, diagnostics: adapter.rpc.diagnostics() }), null, 2));
  await writeFile(join(root, 'events.json'), JSON.stringify(redact(events), null, 2));
  console.log(JSON.stringify(redact(report), null, 2));
}
