const { app, BrowserWindow } = require('electron');

app.disableHardwareAcceleration();
app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-software-rasterizer');

app.whenReady().then(() => {
  const win = new BrowserWindow({ show: false, webPreferences: { sandbox: false } });
  console.log('DISABLE_HW_OK');
  app.quit();
});
