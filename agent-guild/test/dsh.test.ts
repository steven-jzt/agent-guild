import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { dshFailure } from '../src/adapters/dsh-outcome.ts';
import { DshAdapter } from '../src/adapters/dsh.ts';

test('idle and successful session/wait do not hide a model authentication failure', () => {
  const events = [{ method: 'session.event', params: { sessionId: 'current', event: { type: 'turn/end', data: { reason: { kind: 'error', error: { code: 'AUTH', message: 'invalid key' } } } } } }];
  assert.match(dshFailure(events, 'current')?.message ?? '', /AUTH.*invalid key/);
  assert.equal(dshFailure(events, 'other-session'), undefined);
});

test('SDK protocol question can be answered while a session is waiting', async () => {
  const questions: string[] = [];
  const adapter = new DshAdapter(process.execPath, [fileURLToPath(new URL('./fixtures/dsh-server.mjs', import.meta.url))], process.cwd(), process.env,
    async method => { questions.push(method); return { cancelled: true }; });
  try {
    await adapter.initialize(process.cwd(), 'deepseek-account', 'test-model');
    const events = await adapter.run('test-session', 'test');
    assert.deepEqual(questions, ['user-question/ask']);
    assert.equal(events.length, 1);
  } finally { await adapter.close(); }
});
