// 无 Electron 运行时也能跑的冒烟：验证 core 的 Python CLI 桥。
const path = require('node:path');
const fs = require('node:fs');
const { runCli } = require('./cli_client');

process.env.GUILD_DB = path.join(__dirname, '..', '.tmp', 'electron-smoke.db');
try { fs.unlinkSync(process.env.GUILD_DB); } catch (_) { /* ignore */ }

const created = runCli(['create-project', '--name', 'electron-smoke']);
const projects = runCli(['projects']);
const status = runCli(['status', '--project', created.project_id]);
const adapters = runCli(['adapters']);

console.log(JSON.stringify({
  projects: projects.length,
  project_id: created.project_id,
  tasks: status.tasks.length,
  adapters: adapters.map((a) => a.name),
}, null, 2));
