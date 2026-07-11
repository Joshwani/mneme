import { app, BrowserWindow, session, shell } from "electron";
import path from "node:path";
import started from "electron-squirrel-startup";
import { updateElectronApp } from "update-electron-app";
import { MnemeApi } from "./api-client";
import { CredentialStore } from "./credential-store";
import { registerIpc } from "./ipc";
import { ServerSupervisor } from "./server-supervisor";

if (started) app.quit();

let mainWindow: BrowserWindow | null = null;
let server: ServerSupervisor | undefined;
let shutdownStarted = false;

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1180,
    height: 760,
    minWidth: 900,
    minHeight: 620,
    title: "Mneme",
    backgroundColor: "#0d1117",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
    },
  });

  window.once("ready-to-show", () => window.show());
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    const allowed =
      url.startsWith("file://") ||
      (MAIN_WINDOW_VITE_DEV_SERVER_URL &&
        url.startsWith(MAIN_WINDOW_VITE_DEV_SERVER_URL));
    if (!allowed) event.preventDefault();
  });

  if (MAIN_WINDOW_VITE_DEV_SERVER_URL) {
    void window.loadURL(MAIN_WINDOW_VITE_DEV_SERVER_URL);
  } else {
    void window.loadFile(
      path.join(__dirname, `../renderer/${MAIN_WINDOW_VITE_NAME}/index.html`),
    );
  }
  return window;
}

app.whenReady().then(() => {
  if (app.isPackaged) updateElectronApp();

  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });

  const credentials = new CredentialStore();
  server = new ServerSupervisor(credentials);
  const api = new MnemeApi(server);
  mainWindow = createWindow();
  registerIpc(mainWindow, server, api, credentials);
  void server.start().catch((error) => console.error("Mneme startup failed:", error));
});

app.on("window-all-closed", () => app.quit());
app.on("before-quit", (event) => {
  if (shutdownStarted || !server) return;
  event.preventDefault();
  shutdownStarted = true;
  void server
    .stop()
    .catch((error) => console.error("Failed to stop Mneme:", error))
    .finally(() => app.quit());
});
