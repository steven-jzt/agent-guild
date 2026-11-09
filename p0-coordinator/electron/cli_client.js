// 桌面壳的 Python 桥：所有核心调用都通过 worker_cli.py 的 JSON 输出。
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const PYTHON = process.env.PYTHON || 'python';

function runCli(args) {
  const full = process.env.GUILD_DB
    ? ['--db', process.env.GUILD_DB, ...args]
    : args;
  const proc = spawnSync(PYTHON, [path.join(ROOT, 'worker_cli.py'), ...full], {
    cwd: ROOT,
    encoding: 'utf8',
    maxBuffer: 10 * 1024 * 1024,
  });
  if (proc.error) throw proc.error;
  if (proc.status !== 0) {
    throw new Error((proc.stderr || proc.stdout || 'worker_cli failed').trim());
  }
  return JSON.parse(proc.stdout);
}

module.exports = { runCli, ROOT };
