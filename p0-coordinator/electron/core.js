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
