import assert from 'node:assert/strict';
import { test } from 'node:test';
import { JsonLines } from '../src/transport/json-lines.ts';

test('preserves split JSON frames and decodes multiple CRLF frames', () => {
  const lines = new JsonLines();
  assert.deepEqual(lines.push('{"id":'), []);
  assert.deepEqual(lines.push('1,"result":{}}\r\n\n{"method":"ready"}\n'), [
    { id: 1, result: {} }, { method: 'ready' },
  ]);
});

test('rejects malformed protocol frames instead of silently losing events', () => {
  assert.throws(() => new JsonLines().push('not json\n'), /JSON/);
});

test('bounds an unterminated frame to prevent unbounded protocol memory', () => {
  assert.throws(() => new JsonLines(32).push('x'.repeat(33)), /limit/);
});
