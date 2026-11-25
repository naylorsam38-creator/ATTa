/** Electron shell: hosts the ATTa web UI in a locked-down window (no Node in the renderer, sandboxed, one origin). */
import { app, BrowserWindow, shell } from "electron";
import { allowedOrigin, isAllowedNavigation } from "./security";

const ORIGIN = allowedOrigin(process.env);

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1200,
    height: 820,
    title: "ATTa",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true },
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (!isAllowedNavigation(url, ORIGIN)) event.preventDefault();
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  void win.loadURL(ORIGIN);
}

app.enableSandbox();
app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
