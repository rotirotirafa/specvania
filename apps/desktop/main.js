"use strict";

const path = require("node:path");
const { spawn } = require("node:child_process");
const { mkdirSync, createWriteStream } = require("node:fs");
const { launchStudio, quitStudio, readSettings, startingPage, connectControl, readAppSettings, writeSettings } = require(path.join(__dirname, "studio.cjs"));
const { app, BrowserWindow, ipcMain, Notification, shell, dialog } = require("electron");

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
  const window = new BrowserWindow({
    width: 960,
    height: 540,
    minWidth: 960,
    minHeight: 540,
    useContentSize: true,
    frame: false,
    title: "specvania",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  window.setAspectRatio(16 / 9);
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
