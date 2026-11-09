// Electron 主进程：窗口 + utilityProcess 核心 + IPC。
const { app, BrowserWindow, ipcMain, utilityProcess } = require('electron');
const path = require('node:path');

let win = null;
let core = null;
let seq = 0;
const pending = new Map();
const smoke = process.argv.includes('--smoke');

function startCore() {
  core = utilityProcess.fork(path.join(__dirname, 'core.js'), [], { stdio: 'pipe' });
  core.on('message', (msg) => {
    const entry = pending.get(msg.id);
    if (!entry) return;
    pending.delete(msg.id);
    if (msg.ok) entry.resolve(msg.data);
    else entry.reject(new Error(msg.error || 'core error'));
  });
  core.on('exit', () => {
    for (const { reject } of pending.values()) reject(new Error('core exited'));
    pending.clear();
    core = null;
  });
}

function rpc(cmd, payload) {
  if (!core) return Promise.reject(new Error('core not started'));
  const id = ++seq;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    core.postMessage({ id, cmd, payload });
  });
}

function createWindow() {
  win = new BrowserWindow({
    width: 1100,
    height: 720,
    show: !smoke,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
}

app.whenReady().then(() => {
  startCore();
  ipcMain.handle('guild:rpc', (_event, req) => rpc(req.cmd, req.payload));
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
  if (smoke) {
    setTimeout(async () => {
      try {
        const projects = await rpc('projects');
        console.log('ELECTRON_SMOKE_OK', JSON.stringify({ projects: projects.length }));
      } catch (err) {
        console.error('ELECTRON_SMOKE_FAIL', String(err.message || err));
        process.exitCode = 1;
      } finally {
        app.quit();
      }
    }, 2500);
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (core) core.kill();
});
