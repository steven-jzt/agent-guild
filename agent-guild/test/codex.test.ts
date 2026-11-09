import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { CodexAdapter } from '../src/adapters/codex.ts';

test('captures completion even when notifications precede the turn acknowledgement', async () => {
  const adapter = new CodexAdapter(process.execPath, [fileURLToPath(new URL('./fixtures/codex-server.mjs', import.meta.url))], process.cwd());
  try {
    await adapter.initialize();
    const events: string[] = [];
    const result = await adapter.run(process.cwd(), 'test', frame => events.push(frame.method!), 2000);
    assert.deepEqual(result, { status: 'completed', text: '交付完成' });
    assert.ok(events.includes('turn/completed'));
  } finally { await adapter.close(); }
});
