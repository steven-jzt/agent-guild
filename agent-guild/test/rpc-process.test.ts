import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { RpcProcess } from '../src/transport/rpc-process.ts';

function peer() { return new RpcProcess(process.execPath, [fileURLToPath(new URL('./fixtures/rpc-server.mjs', import.meta.url))], process.cwd()); }

test('matches concurrent JSON-RPC responses to the correct request', async () => {
  const rpc = peer();
  try { assert.deepEqual(await Promise.all([rpc.request('echo', { value: 1 }), rpc.request('echo', { value: 2 })]), [{ value: 1 }, { value: 2 }]); }
  finally { await rpc.close(); }
});

test('times out without automatically repeating a request', async () => {
  const rpc = peer();
  try { await assert.rejects(rpc.request('stall', {}, 100), /timed out/); }
  finally { await rpc.close(); }
});

test('delivers server questions and returns the specific user answer', async () => {
  const rpc = peer();
  rpc.on('request', frame => rpc.respond(frame.id, { allowed: false }));
  try { assert.deepEqual(await rpc.request('ask', {}, 2000), { allowed: false }); }
  finally { await rpc.close(); }
});

test('closing transport rejects pending work and refuses reuse', async () => {
  const rpc = peer();
  const waiting = assert.rejects(rpc.request('stall'), /closed|exited/);
  await rpc.close(); await waiting;
  await assert.rejects(rpc.request('echo'), /closed|exited/);
});

test('rejects an invalid response identity instead of leaving work pending', async () => {
  const rpc = peer();
  try { await assert.rejects(rpc.request('bad-id', {}, 500), /Invalid JSON-RPC identity/); }
  finally { await rpc.close(); }
});

test('rejects a response missing both result and error', async () => {
  const rpc = peer();
  try { await assert.rejects(rpc.request('bad-result', {}, 500), /Invalid JSON-RPC response/); }
  finally { await rpc.close(); }
});
