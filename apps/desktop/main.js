"use strict";

const path = require("node:path");
const { app, BrowserWindow, ipcMain, Notification, shell } = require("electron");

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
  const controlToken = argument("--control-token");
  const target = safeUrl(argument("--url", true), controlToken);
  if (!target || target.protocol !== "http:" || target.hostname !== "127.0.0.1") {
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
      additionalArguments: controlToken ? ["--control-token", controlToken] : [],
    },
  });
  window.setAspectRatio(16 / 9);
  const sameOrigin = (value) => {
    const url = safeUrl(value, controlToken);
    return url !== null && url.origin === target.origin;
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
  ipcMain.on("specvania:notify", (event, message) => {
    if (event.sender !== window.webContents || !sameOrigin(event.senderFrame?.url) ||
        !message || typeof message.title !== "string" || typeof message.body !== "string" ||
        message.title.length > 200 || message.body.length > 500 ||
        (controlToken && (message.title.includes(controlToken) || message.body.includes(controlToken)))) return;
    if (Notification.isSupported()) {
      new Notification({ title: message.title, body: message.body }).show();
    }
  });
  await window.loadURL(target.href);
}

app.whenReady().then(openWindow).catch(() => {
  // No argv, URL, token or underlying error is included in diagnostics.
  console.error("Cannot open specvania desktop; check --url and the local UI server.");
  app.exit(1);
});
app.on("window-all-closed", () => app.quit());
