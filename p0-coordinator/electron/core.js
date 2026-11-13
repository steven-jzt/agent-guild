// utilityProcess 内的核心桥：接收 main 的 RPC，调用 Python 并回传 JSON。
const { runCli } = require('./cli_client');

process.parentPort.on('message', (event) => {
  const msg = event.data || {};
  const id = msg.id;
  try {
    let data;
    switch (msg.cmd) {
      case 'projects':
        data = runCli(['projects']);
        break;
      case 'status':
        if (!msg.payload || !msg.payload.projectId) throw new Error('projectId required');
        data = runCli(['status', '--project', msg.payload.projectId]);
        break;
      case 'recovery':
        data = runCli(msg.payload?.projectId ? ['recovery-report', '--project', msg.payload.projectId] : ['recovery-report']);
        break;
      case 'reconcile_execution': {
        const p = msg.payload || {};
        const args = ['reconcile-execution', '--execution', p.executionId, '--generation', String(p.generation), '--reason', p.reason];
        if (p.confirmLocalStopped === true) args.push('--confirm-local-stopped');
        data = runCli(args);
        break;
      }
      case 'reconcile_merge': {
        const p = msg.payload || {};
        const args = ['reconcile-merge', '--attempt', p.attemptId, '--action', p.action, '--result-sha', p.resultSha];
        if (p.reason) args.push('--reason', p.reason);
        data = runCli(args);
        break;
      }
      case 'adapters':
        data = runCli(['adapters']);
        break;
      default:
        throw new Error(`unknown command: ${msg.cmd}`);
    }
    process.parentPort.postMessage({ id, ok: true, data });
  } catch (err) {
    process.parentPort.postMessage({ id, ok: false, error: String(err.message || err) });
  }
});
