const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('guild', {
  rpc: (cmd, payload) => ipcRenderer.invoke('guild:rpc', { cmd, payload }),
});
