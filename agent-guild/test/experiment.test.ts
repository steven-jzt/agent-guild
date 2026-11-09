import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, mkdir, rm, writeFile, symlink, realpath } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, relative, resolve, isAbsolute } from 'node:path';
import { execFileSync } from 'node:child_process';
import { prepareExperiment } from '../src/experiments/workspace.ts';
import { redact } from '../src/experiments/evidence.ts';

test('experiment edits an independent worktree without changing the source repository', async () => {
  const root = await mkdtemp(join(tmpdir(), 'guild 中文 空格-'));
  try {
    const fixture = await prepareExperiment(root);
    assert.notEqual(fixture.source, fixture.workspace);
    await writeFile(join(fixture.workspace, 'src', 'total.ts'), 'export const totalCents = () => 400;\n');
    assert.equal(execFileSync('git', ['status', '--porcelain'], { cwd: fixture.source, encoding: 'utf8' }), '');
    assert.match(execFileSync('git', ['status', '--porcelain'], { cwd: fixture.workspace, encoding: 'utf8' }), /src\/total.ts/);
    assert.equal(execFileSync('git', ['rev-parse', 'HEAD'], { cwd: fixture.source, encoding: 'utf8' }).trim(), fixture.baseline);
  } finally {
    const withinTemp = relative(resolve(tmpdir()), resolve(root));
    assert.ok(withinTemp && !withinTemp.startsWith('..') && !isAbsolute(withinTemp));
    await rm(root, { recursive: true, force: true });
  }
});

test('redacts credential fields and bearer values before persisting evidence', () => {
  assert.deepEqual(redact({ apiKey: 'do-not-record', nested: { authorization: 'Bearer abc' }, message: 'Authorization: Bearer secret-value' }), {
    apiKey: '[REDACTED]', nested: { authorization: '[REDACTED]' }, message: 'Authorization: Bearer [REDACTED]',
  });
});

test('passes canonical physical workspace paths across process and sandbox boundaries', async () => {
  const root = await mkdtemp(join(tmpdir(), 'guild-physical-'));
  const actual = join(root, 'actual'); const alias = join(root, 'alias');
  try {
    await mkdir(actual); await symlink(actual, alias, process.platform === 'win32' ? 'junction' : 'dir');
    const fixture = await prepareExperiment(alias);
    assert.equal(fixture.workspace, await realpath(fixture.workspace));
    assert.equal(fixture.source, await realpath(fixture.source));
  } finally {
    const withinTemp = relative(resolve(tmpdir()), resolve(root));
    assert.ok(withinTemp && !withinTemp.startsWith('..') && !isAbsolute(withinTemp));
    await rm(root, { recursive: true, force: true });
  }
});
