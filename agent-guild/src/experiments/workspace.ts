import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { realpathSync } from 'node:fs';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { execFileSync } from 'node:child_process';

export const expenseTask = '只修改 src/total.ts，实现 totalCents 对整数金额（分）数组求和，空数组为0。运行 node --test spec/total.test.mjs。不要修改其他文件、不要提交Git、不要联网、不要安装包、不要启动子Agent。最后简短汇报。';

export async function createExperimentRoot(prefix: string): Promise<string> {
  const base = join(process.env.LOCALAPPDATA ?? join(homedir(), '.local', 'share'), 'AgentGuild', 'experiments');
  await mkdir(base, { recursive: true });
  // Packaged desktop apps can redirect AppData. Restricted child users need the
  // physical path rather than the parent's virtualized view of the directory.
  return realpathSync.native(await mkdtemp(join(realpathSync.native(base), prefix)));
}

export function git(cwd: string, args: string[]): string {
  return execFileSync('git', ['-c', 'core.autocrlf=false', '-c', 'commit.gpgsign=false', ...args], {
    cwd, encoding: 'utf8', windowsHide: true,
    env: { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: process.platform === 'win32' ? 'NUL' : '/dev/null', GIT_TERMINAL_PROMPT: '0' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}

export async function prepareExperiment(root: string): Promise<{ source: string; workspace: string; baseline: string }> {
  root = realpathSync.native(root);
  const source = join(root, 'source');
  const managed = join(root, 'managed.git');
  const workspace = join(root, '执行工作区 空格');
  await mkdir(join(source, 'src'), { recursive: true });
  await mkdir(join(source, 'spec'));
  await writeFile(join(source, 'package.json'), '{"type":"module","private":true}\n');
  await writeFile(join(source, 'src', 'total.ts'), 'export function totalCents(amounts: number[]): number { return 0; }\n');
  await writeFile(join(source, 'spec', 'total.test.mjs'), `import assert from 'node:assert/strict';
import { test } from 'node:test';
import { totalCents } from '../src/total.ts';
test('totals integer cents', () => assert.equal(totalCents([125, 275]), 400));
test('empty list', () => assert.equal(totalCents([]), 0));
`);
  git(source, ['init', '-b', 'main']);
  git(source, ['config', 'user.name', 'Agent Guild Experiment']);
  git(source, ['config', 'user.email', 'experiment@localhost']);
  git(source, ['add', '--', '.']);
  git(source, ['commit', '-m', 'fixture: add expense total task']);
  const baseline = git(source, ['rev-parse', 'HEAD']).trim();
  git(root, ['clone', '--bare', '--no-local', source, managed]);
  git(managed, ['worktree', 'add', '-b', 'task-m0', workspace, baseline]);
  return { source, workspace, baseline };
}
