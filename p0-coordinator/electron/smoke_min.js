const { app, BrowserWindow } = require('electron');

app.whenReady().then(() => {
  const win = new BrowserWindow({ show: false, webPreferences: { sandbox: false } });
  console.log('MINIMAL_OK');
  app.quit();
});
