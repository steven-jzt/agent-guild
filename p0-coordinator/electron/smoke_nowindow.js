const { app } = require('electron');

app.disableHardwareAcceleration();

app.whenReady().then(() => {
  console.log('NO_WINDOW_OK');
  app.quit();
});
