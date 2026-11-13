const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function bridge() {
  const source = fs.readFileSync(path.join(__dirname, 'Program.cs'), 'utf8');
  const method = source.slice(source.indexOf('private static string BridgeScript()'), source.indexOf('private static void CancelRequests()'));
  const js = [...method.matchAll(/"(?:\\.|[^"\\])*"/g)].map(m => JSON.parse(m[0])).join('');
  const frames = [];
  let receive;
  const window = { chrome: { webview: {
    postMessage: frame => frames.push(frame),
    addEventListener: (_, callback) => { receive = callback; },
  } } };
  vm.runInNewContext(js, { window, Promise, Error });
  return { api: window.guild, frames, reply: message => receive({ data: message }) };
}

test('cancellation has its own response and cannot settle the original operation', async () => {
  const b = bridge();
  const original = b.api.rpc('start_run', { taskId: 'T' });
  const rejected = assert.rejects(original, /stopped; unknown/);
  let settled = false;
  original.then(() => { settled = true; }, () => { settled = true; });
  const cancel = original.cancel();
  assert.equal(b.frames[1].cmd, 'cancel_request');
  assert.equal(b.frames[1].payload.requestId, b.frames[0].id);
  b.reply({ id: b.frames[1].id, ok: true, data: { requested: true } });
  await cancel;
  assert.equal(settled, false);
  b.reply({ id: b.frames[0].id, ok: false, error: 'stopped; unknown' });
  await rejected;
  assert.equal(b.frames.length, 2);
});

test('concurrent responses are correlated even when returned out of order', async () => {
  const b = bridge();
  const a = b.api.rpc('status'), c = b.api.rpc('projects');
  b.reply({ id: b.frames[1].id, ok: true, data: 'projects' });
  b.reply({ id: b.frames[0].id, ok: true, data: 'status' });
  assert.deepEqual(await Promise.all([a, c]), ['status', 'projects']);
});

async function renderer() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', innerHTML: '', textContent: '', style: {}, dataset: {},
      addEventListener() {}, appendChild() {}, scrollIntoView() {},
    });
    return elements.get(id);
  }
  const window = { addEventListener() {}, guild: { rpc: command => Promise.resolve(command === 'demo_paths' ? {} : []) } };
  const context = vm.createContext({ window, document: {
    getElementById: element, createElement: () => element('option'), querySelector: () => element('settings'),
  }, localStorage: { getItem: () => '', setItem() {} }, console });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../electron/renderer/renderer.js'), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  return { context, elements, window };
}

test('refresh preserves pending operations and prevents duplicate submission until termination', async () => {
  const r = await renderer();
  let reject, calls = 0, cancellations = 0;
  const pending = new Promise((_, no) => { reject = no; });
  pending.cancel = () => { cancellations++; return Promise.resolve({ requested: true }); };
  r.window.guild.rpc = () => { calls++; return pending; };
  const operation = vm.runInContext("runAction('run:T','start_run',{})", r.context);
  const rejected = assert.rejects(operation, /stopped/);
  vm.runInContext('renderAll()', r.context);
  assert.match(vm.runInContext("taskAction({id:'T'})", r.context), /停止当前请求/);
  await assert.rejects(vm.runInContext("runAction('run:T','start_run',{})", r.context), /重复提交/);
  await vm.runInContext("cancelAction('run:T')", r.context);
  assert.equal(vm.runInContext("pendingActions.has('run:T')", r.context), true);
  assert.equal(calls, 1);
  assert.equal(cancellations, 1);
  reject(new Error('stopped'));
  await rejected;
  assert.equal(vm.runInContext('pendingActions.size', r.context), 0);
});

test('an unconfirmed merge result cannot display a success notice', async () => {
  const r = await renderer();
  r.elements.get('repo').value = 'repo';
  r.elements.get('worktree').value = 'integration';
  r.window.guild.rpc = command => Promise.resolve(command === 'merge'
    ? { merge_attempt: { status: 'failed', error: 'fixture conflict' } } : []);
  await assert.rejects(vm.runInContext("mergeCandidate('C')", r.context), /fixture conflict/);
  assert.doesNotMatch(r.elements.get('notice').textContent, /已合入/);
});

test('recovery renders unknown executions without claiming local cancellation or completion', async () => {
  const r = await renderer();
  vm.runInContext("state.recovery={executions:[{execution_id:'E-old',task_id:'T',generation:1,local_state:'unknown',manual_confirmation_allowed:true,workspace_changes:['<unsafe>']}],merges:[]};renderRecovery()", r.context);
  const html = r.elements.get('recoveryRows').innerHTML;
  assert.match(html, /手工确认/);
  assert.match(html, /&lt;unsafe&gt;/);
  assert.doesNotMatch(html, /已取消|已完成/);
});

test('unknown execution needs explicit confirmation and transmits its exact generation', async () => {
  const r = await renderer();
  vm.runInContext("state.recovery={executions:[{execution_id:'E',generation:7,local_state:'unknown',manual_confirmation_allowed:true}],merges:[]}", r.context);
  let payload;
  r.window.guild.rpc = (command, data) => {
    if (command === 'reconcile_execution') payload = data;
    return Promise.resolve(command === 'reconcile_execution' ? { status: 'interrupted' } : []);
  };
  r.elements.set('recovery-reason-E', { value: 'workspace checked' });
  r.elements.set('recovery-confirm-E', { checked: false });
  await assert.rejects(vm.runInContext("reconcileExecution('E')", r.context), /确认/);
  assert.equal(payload, undefined);
  r.elements.get('recovery-confirm-E').checked = true;
  await vm.runInContext("reconcileExecution('E')", r.context);
  assert.equal(payload.generation, 7);
  assert.equal(payload.confirmLocalStopped, true);
});

test('merge recovery cannot claim success for an unconfirmed backend result', async () => {
  const r = await renderer();
  vm.runInContext("state.recovery={executions:[],merges:[{attempt_id:'M',git_result_sha:'sha',can_confirm:true}]}", r.context);
  r.window.guild.rpc = command => Promise.resolve(command === 'reconcile_merge' ? { status: 'unknown' } : []);
  await assert.rejects(vm.runInContext("reconcileMerge('M','confirm')", r.context), /尚未确认/);
});
