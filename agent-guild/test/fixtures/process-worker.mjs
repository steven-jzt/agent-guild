import { spawn } from 'node:child_process';
const grandchild = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore', windowsHide: true, detached: true });
console.log(JSON.stringify({ pid: process.pid, grandchild: grandchild.pid }));
setInterval(() => {}, 1000);
