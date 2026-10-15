"use strict";

const path = require("node:path");
const { spawn } = require("node:child_process");
const { mkdirSync, createWriteStream, readFileSync, writeFileSync, renameSync } = require("node:fs");
const { launchStudio, quitStudio, readSettings, startingPage, connectControl, readAppSettings, writeSettings } = require(path.join(__dirname, "studio.cjs"));
const { app, BrowserWindow, ipcMain, Notification, shell, dialog, screen, Menu } = require("electron");

function readWindowState(file) {
  const defaults = { bounds: { width: 960, height: 540, center: true }, maximized: false, fullScreen: false, zoomLevel: 0 };
  try {
    const value = JSON.parse(readFileSync(file, "utf8"));
    const { x, y, width, height } = value.bounds;
    if (![x, y, width, height].every(Number.isSafeInteger) || width < 960 || height < 540 ||
        typeof value.maximized !== "boolean" || typeof value.fullScreen !== "boolean" ||
        !Number.isFinite(value.zoomLevel) || Math.abs(value.zoomLevel) > 10) return defaults;
    // At least a draggable part of the title bar must remain on a display.
    const visible = screen.getAllDisplays().some(({ workArea: area }) =>
      Math.min(x + width, area.x + area.width) - Math.max(x, area.x) >= 80 &&
      y >= area.y && y + 32 <= area.y + area.height);
    return { bounds: visible ? { x, y, width, height } : defaults.bounds,
      maximized: value.maximized, fullScreen: value.fullScreen, zoomLevel: value.zoomLevel };
  } catch { return defaults; }
}

function installMenu() {
  const mac = process.platform === "darwin";
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { ...(mac ? { role: "appMenu" } : { label: app.name }), submenu: [
      { role: "about" }, { type: "separator" },
      ...(mac ? [{ role: "hide" }, { role: "hideOthers" }, { role: "unhide" }, { type: "separator" }] : []),
      { role: "quit" },
    ] },
    { role: "editMenu", submenu: [
      { role: "undo" }, { role: "redo" }, { type: "separator" },
      { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" },
    ] },
    { role: "viewMenu", submenu: [
      { role: "resetZoom", accelerator: "CmdOrCtrl+0" },
      { role: "zoomIn", accelerator: "CmdOrCtrl+=" },
      { role: "zoomOut", accelerator: "CmdOrCtrl+-" },
      { type: "separator" }, { role: "togglefullscreen" },
      ...(!app.isPackaged ? [{ type: "separator" }, { role: "reload" }, { role: "toggleDevTools" }] : []),
    ] },
    { role: "windowMenu", submenu: [{ role: "minimize" }, { role: "zoom" }, { role: "close" }] },
  ]));
}

function argument(name, required = false) {
  const index = process.argv.indexOf(name);
  if (index === -1 && !required) return "";
  const value = process.argv[index + 1];
  if (index === -1 || !value || value.startsWith("--")) {
    throw new Error("Missing desktop launch argument.");
  }
  return value;
}

function safeUrl(value, controlToken) {
  try {
    const url = new URL(value);
    const decoded = decodeURIComponent(url.href);
    if (url.username || url.password || url.searchParams.has("control_token") ||
        (controlToken && decoded.includes(controlToken))) return null;
    return url;
  } catch {
    return null;
  }
}

async function openWindow() {
  let lifecycle;
  let quitting = false;
  let quitComplete = false;
  let controlToken = argument("--control-token");
  const launchUrl = argument("--url");
  let startupUrl = "";
  let studioStatus = { kind: "ready" };
  let target = launchUrl ? safeUrl(launchUrl, controlToken) : null;
  if (launchUrl && (!target || target.protocol !== "http:" || target.hostname !== "127.0.0.1")) {
    throw new Error("Desktop needs the loopback UI URL.");
  }
  const userData = app.getPath("userData");
  const stateFile = path.join(userData, "desktop-window.json");
  const saved = readWindowState(stateFile);
  const window = new BrowserWindow({
    ...saved.bounds,
    minWidth: 960,
    minHeight: 540,
    frame: true,
    ...(process.platform === "darwin" ? { titleBarStyle: "hiddenInset" } : {}),
    resizable: true,
    minimizable: true,
    maximizable: true,
    fullscreenable: true,
    title: "specvania",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  let zoomLevel = saved.zoomLevel, loaded = false;
  window.webContents.on("did-start-navigation", (_event, _url, _inPlace, mainFrame) => {
    if (mainFrame && loaded) zoomLevel = window.webContents.getZoomLevel();
  });
  window.webContents.on("did-finish-load", () => {
    window.webContents.setZoomLevel(zoomLevel);
    loaded = true;
  });
  const saveWindowState = () => {
    try {
      // getNormalBounds is stable while maximized/full screen. Persist only
      // numeric geometry and flags, never launch arguments, URLs or tokens.
      const { x, y, width, height } = window.getNormalBounds();
      const state = { bounds: { x, y, width, height }, maximized: window.isMaximized(),
        fullScreen: window.isFullScreen(), zoomLevel: window.webContents.getZoomLevel() };
      mkdirSync(userData, { recursive: true });
      writeFileSync(stateFile + ".tmp", JSON.stringify(state), { mode: 0o600 });
      renameSync(stateFile + ".tmp", stateFile);
    } catch { /* A closed window or unwritable preferences must not stop play. */ }
  };
  for (const event of ["resize", "move", "maximize", "unmaximize", "enter-full-screen", "leave-full-screen", "close"]) {
    window.on(event, saveWindowState);
  }
  app.on("before-quit", saveWindowState);
  if (saved.maximized) window.maximize();
  if (saved.fullScreen) window.setFullScreen(true);
  installMenu();
  const sameOrigin = (value) => {
    if (startupUrl && value === startupUrl) return true;
    const url = safeUrl(value, controlToken);
    return target !== null && url !== null && url.origin === target.origin;
  };
  const guardNavigation = (event, url) => {
    // Recent Electron frame events put the URL on the event; redirects also
    // provide it positionally. Guard both main-frame and subframe navigation.
    if (!sameOrigin(event.url || url)) event.preventDefault();
  };
  for (const name of ["will-navigate", "will-frame-navigate", "will-redirect"]) {
    window.webContents.on(name, guardNavigation);
  }
  window.webContents.setWindowOpenHandler(({ url: value }) => {
    const url = safeUrl(value, controlToken);
    if (url && (url.protocol === "http:" || url.protocol === "https:")) {
      // Never log an error from Electron: it can contain the request URL.
      shell.openExternal(url.href).catch(() => {});
    }
    return { action: "deny" };
  });
  window.webContents.on("will-attach-webview", (event) => event.preventDefault());
  ipcMain.on("specvania:control-token", (event) => {
    event.returnValue = event.sender === window.webContents && sameOrigin(event.senderFrame?.url)
      ? controlToken : "";
  });
  ipcMain.on("specvania:notify", (event, message) => {
    if (event.sender !== window.webContents || !sameOrigin(event.senderFrame?.url) ||
        !message || typeof message.title !== "string" || typeof message.body !== "string" ||
        message.title.length > 200 || message.body.length > 500 ||
        (controlToken && (message.title.includes(controlToken) || message.body.includes(controlToken)))) return;
    if (!window.isFocused() && Notification.isSupported()) {
      new Notification({ title: message.title, body: message.body }).show();
    }
  });
  const trusted = event => event.sender === window.webContents && sameOrigin(event.senderFrame?.url);
  const language = () => app.getLocale?.().toLowerCase().startsWith("pt") ? "pt-BR" : "en";
  const handle = (channel, action) => ipcMain.handle?.(channel, (event, ...args) => {
    if (!trusted(event)) throw new Error("Untrusted desktop request.");
    return action(...args);
  });
  handle("specvania:pick-folder", async () => {
    const result = await dialog.showOpenDialog(window, { properties: ["openDirectory"] });
    return result.canceled ? null : result.filePaths[0] || null;
  });
  handle("specvania:settings-get", () => readAppSettings(app.getPath("userData"), language()));
  handle("specvania:settings-set", value => {
    if (controlToken && JSON.stringify(value)?.includes(controlToken)) throw new Error("Invalid settings.");
    return writeSettings(app.getPath("userData"), value, language());
  });
  handle("specvania:studio", () => studioStatus);
  let startStudio, restarting = false;
  handle("specvania:studio-restart", () => {
    if (!startStudio || quitting || restarting || studioStatus.kind === "starting") return;
    restarting = true;
    return quitStudio(lifecycle, {connect: connectControl}).then(() => {
      if (!quitting) startStudio();
    }).finally(() => {restarting = false;});
  });
  if (target) {
    await window.loadURL(target.href);
  } else {
    const userData = app.getPath("userData");
    const logs = path.join(userData, "logs");
    mkdirSync(logs, { recursive: true });
    const logPath = path.join(logs, "studio.log");
    startStudio = () => {
      studioStatus = {kind: "starting"};
      controlToken = "";
      target = null;
      startupUrl = "data:text/html;charset=utf-8," + encodeURIComponent(startingPage());
      window.loadURL(startupUrl).catch(() => {});
      const log = createWriteStream(logPath, { flags: "a", mode: 0o600 });
      log.on("error", () => {});
      lifecycle = launchStudio({
        spawn, executable: readSettings(userData), logPath,
        log: (data) => log.write(data),
        open: (url, token) => {
          studioStatus = {kind: "ready"};
          startupUrl = "";
          controlToken = token;
          target = safeUrl(url, token);
          window.loadURL(target.href).catch(() => {
            console.error("Cannot load the local studio game.");
            app.quit();
          });
        },
        error: (page, status) => {
          controlToken = "";
          target = null;
          studioStatus = status;
          startupUrl = "data:text/html;charset=utf-8," + encodeURIComponent(page);
          window.loadURL(startupUrl).catch(() => {});
        },
      });
      lifecycle.child?.once("exit", () => log.end());
    };
    startStudio();
  }
  app.on("before-quit", (event) => {
    if (!lifecycle || lifecycle.exited || quitComplete) return;
    event.preventDefault();
    if (quitting) return;
    quitting = true;
    quitStudio(lifecycle, { connect: connectControl }).then(() => {
      quitComplete = true;
      app.quit();
    });
  });
}

app.whenReady().then(openWindow).catch(() => {
  // No argv, URL, token or underlying error is included in diagnostics.
  console.error("Cannot open specvania desktop; check --url and the local UI server.");
  app.exit(1);
});
app.on("window-all-closed", () => app.quit());
