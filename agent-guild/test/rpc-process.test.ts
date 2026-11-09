import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { RpcProcess, type RpcFrame } from '../src/transport/rpc-process.ts';

const FIXTURE = fileURLToPath(new URL('./fixtures/rpc-server.mjs', import.meta.url));
// 启动等待期限与请求期限分开：夹具就绪最多等 5s，单个请求仍用自己的短期限。
const READY_TIMEOUT_MS = 5000;

type PeerOptions = { readyDelayMs?: number; readyTimeoutMs?: number };

async function peer(options: PeerOptions = {}): Promise<RpcProcess> {
  const env = { ...process.env };
  if (options.readyDelayMs !== undefined) {
    env.GUILD_FIXTURE_READY_DELAY_MS = String(options.readyDelayMs);
  }
  const rpc = new RpcProcess(process.execPath, [FIXTURE], process.cwd(), env);
  try {
    await waitForReady(rpc, options.readyTimeoutMs ?? READY_TIMEOUT_MS);
    return rpc;
  } catch (error) {
    await rpc.close().catch(() => {});
    throw error;
  }
}

function waitForReady(rpc: RpcProcess, timeoutMs: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error(`fixture did not become ready within ${timeoutMs}ms`));
    }, timeoutMs);
    const onNotification = (frame: RpcFrame) => {
      if (frame.method === 'fixture/ready') {
        cleanup();
        resolve();
      }
    };
    const onFailure = (error: Error) => {
      cleanup();
      reject(error);
    };
    const cleanup = () => {
      clearTimeout(timer);
      rpc.off('notification', onNotification);
      rpc.off('failure', onFailure);
    };
    rpc.on('notification', onNotification);
    rpc.on('failure', onFailure);
  });
}

test('waits for a delayed fixture start before applying tight request deadlines', async () => {
  const began = Date.now();
  const rpc = await peer({ readyDelayMs: 300 });
  const readyAfter = Date.now() - began;
  assert.ok(readyAfter >= 250, `readiness sync should wait for the fixture, got ${readyAfter}ms`);
  try {
    // 没有就绪同步时，这个 100ms 的请求会在 300ms 启动完成前发出并先超时。
    await assert.rejects(rpc.request('bad-id', {}, 100), /Invalid JSON-RPC identity/);
  } finally { await rpc.close(); }
});

test('reports startup readiness timeout separately from request timeout', async () => {
  await assert.rejects(
    peer({ readyDelayMs: 2000, readyTimeoutMs: 100 }),
    /fixture did not become ready within 100ms/,
  );
});

test('matches concurrent JSON-RPC responses to the correct request', async () => {
  const rpc = await peer();
  try { assert.deepEqual(await Promise.all([rpc.request('echo', { value: 1 }), rpc.request('echo', { value: 2 })]), [{ value: 1 }, { value: 2 }]); }
  finally { await rpc.close(); }
});

test('times out without automatically repeating a request', async () => {
  const rpc = await peer();
  try { await assert.rejects(rpc.request('stall', {}, 100), /timed out/); }
  finally { await rpc.close(); }
});

test('delivers server questions and returns the specific user answer', async () => {
  const rpc = await peer();
  rpc.on('request', frame => rpc.respond(frame.id, { allowed: false }));
  try { assert.deepEqual(await rpc.request('ask', {}, 2000), { allowed: false }); }
  finally { await rpc.close(); }
});

test('closing transport rejects pending work and refuses reuse', async () => {
  const rpc = await peer();
  const waiting = assert.rejects(rpc.request('stall'), /closed|exited/);
  await rpc.close(); await waiting;
  await assert.rejects(rpc.request('echo'), /closed|exited/);
});

test('rejects an invalid response identity instead of leaving work pending', async () => {
  const rpc = await peer();
  try { await assert.rejects(rpc.request('bad-id', {}, 500), /Invalid JSON-RPC identity/); }
  finally { await rpc.close(); }
});

test('rejects a response missing both result and error', async () => {
  const rpc = await peer();
  try { await assert.rejects(rpc.request('bad-result', {}, 500), /Invalid JSON-RPC response/); }
  finally { await rpc.close(); }
});
