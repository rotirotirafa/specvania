"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { EventEmitter } = require("node:events");

const desktop = path.resolve(__dirname, "../../apps/desktop");
const viewUrl = "http://127.0.0.1:8765/?view_token=fake-view";
const control = "fake-control-secret";

async function loadMain(args = ["--url", viewUrl, "--control-token", control], failLoad = false) {
  const windows = [];
  const external = [];
  const messages = [];
  const notifications = [];
  const exits = [];
  const app = new EventEmitter();
  app.whenReady = () => Promise.resolve();
  app.exit = (code) => exits.push(code);
  app.quit = () => exits.push(0);
  const ipcMain = new EventEmitter();
  class BrowserWindow {
    constructor(options) {
      this.options = options;
      this.webContents = new EventEmitter();
      this.webContents.setWindowOpenHandler = (handler) => { this.openHandler = handler; };
      this.webContents.getURL = () => this.url;
      this.focused = false;
      windows.push(this);
    }
    setAspectRatio(ratio) { this.ratio = ratio; }
    isFocused() { return this.focused; }
    loadURL(url) {
      this.url = url;
      return failLoad ? Promise.reject(new Error(`secret failure ${control}`)) : Promise.resolve();
    }
  }
  class Notification {
    static isSupported() { return true; }
    constructor(options) { this.options = options; }
    show() { notifications.push(this.options); }
  }
  vm.runInNewContext(readFileSync(path.join(desktop, "main.js"), "utf8"), {
    require: (name) => name === "electron" ? {
      app, BrowserWindow, ipcMain, Notification,
      shell: { openExternal: (url) => { external.push(url); return Promise.resolve(); } },
    } : require(name),
    process: { argv: ["electron", ".", ...args] },
    __dirname: desktop,
    URL,
    console: { error: (...items) => messages.push(items.join(" ")) },
  }, { filename: "main.js" });
  // Settle whenReady and loadURL's promise without timers or a real Electron app.
  await new Promise((resolve) => setImmediate(resolve));
  return { windows, external, messages, notifications, exits, app, ipcMain };
}

test("secure_window_options_and_single_frameless_16_by_9_window", async () => {
  const loaded = await loadMain();
  assert.equal(loaded.windows.length, 1);
  const window = loaded.windows[0];
  const options = window.options;
  assert.equal(options.frame, false);
  assert.equal(options.minWidth, 960);
  assert.equal(options.minHeight, 540);
  assert.equal(options.width / options.height, 16 / 9);
  assert.ok(options.width >= 960 && options.height >= 540);
  assert.equal(window.ratio, 16 / 9);
  assert.equal(options.webPreferences.contextIsolation, true);
  assert.equal(options.webPreferences.nodeIntegration, false);
  assert.equal(options.webPreferences.sandbox, true);
  assert.equal(options.webPreferences.preload, path.join(desktop, "preload.js"));
  assert.equal(window.url, viewUrl);
  loaded.app.emit("window-all-closed");
  assert.deepEqual(loaded.exits, [0]);
});

test("navigation_and_redirects_are_locked_to_server_origin", async () => {
  const loaded = await loadMain();
  const contents = loaded.windows[0].webContents;
  for (const eventName of ["will-navigate", "will-redirect", "will-frame-navigate"]) {
    assert.ok(contents.listenerCount(eventName) > 0, `${eventName} must be guarded`);
    for (const url of [
      "https://example.invalid/", "http://127.0.0.1:9999/", "http://localhost:8765/",
      "file:///etc/passwd", "javascript:alert(1)", "not a URL",
      `${viewUrl}&control_token=${control}`, `http://127.0.0.1:8765/${control}`,
    ]) {
      let prevented = false;
      contents.emit(eventName, { url, preventDefault() { prevented = true; } }, url);
      assert.equal(prevented, true, `${eventName} allowed ${url}`);
    }
    let prevented = false;
    const url = "http://127.0.0.1:8765/main.js";
    contents.emit(eventName, { url, preventDefault() { prevented = true; } }, url);
    assert.equal(prevented, false, "same-origin UI navigation should work");
  }
  let webviewPrevented = false;
  contents.emit("will-attach-webview", { preventDefault() { webviewPrevented = true; } });
  assert.equal(webviewPrevented, true);
  assert.deepEqual(loaded.external, []);
});

test("window_open_hands_http_and_https_to_system_browser_and_denies_other_schemes", async () => {
  const loaded = await loadMain();
  const window = loaded.windows[0];
  for (const url of ["http://example.invalid/help", "https://example.invalid/help"]) {
    assert.equal(window.openHandler({ url }).action, "deny");
  }
  for (const url of ["file:///etc/passwd", "javascript:alert(1)", "mailto:x@y.invalid", "bad URL"]) {
    assert.equal(window.openHandler({ url }).action, "deny");
  }
  assert.deepEqual(loaded.external, ["http://example.invalid/help", "https://example.invalid/help"]);
  assert.equal(loaded.windows.length, 1);
});

test("control_token_only_reaches_preload_ipc_and_never_urls_or_logs", async () => {
  const loaded = await loadMain();
  const window = loaded.windows[0];
  assert.equal(window.options.webPreferences.additionalArguments, undefined);
  const trusted = { sender: window.webContents, senderFrame: { url: viewUrl } };
  loaded.ipcMain.emit("specvania:control-token", trusted);
  assert.equal(trusted.returnValue, control);
  for (const event of [
    { sender: {}, senderFrame: { url: viewUrl } },
    { sender: window.webContents, senderFrame: { url: "https://example.invalid" } },
    { sender: window.webContents, senderFrame: { url: "http://127.0.0.1:9999/" } },
  ]) {
    loaded.ipcMain.emit("specvania:control-token", event);
    assert.equal(event.returnValue, "");
  }
  for (const url of [
    `https://example.invalid/?token=${control}`,
    "https://example.invalid/%66ake-control-secret",
    "https://example.invalid/?control_token=other",
  ]) window.openHandler({ url });
  assert.deepEqual(loaded.external, []);
  assert.ok(!window.url.includes(control));
  assert.ok(!JSON.stringify(loaded.messages).includes(control));
  const failed = await loadMain(undefined, true);
  assert.deepEqual(failed.exits, [1]);
  assert.ok(!JSON.stringify(failed.messages).includes(control));
});

test("bad_launch_arguments_fail_without_echoing_tokens", async () => {
  for (const args of [
    ["--url"], ["--url", "not a URL"],
    ["--url", "https://example.invalid/"],
    ["--url", `${viewUrl}&control_token=${control}`, "--control-token", control],
    ["--url", `http://127.0.0.1:8765/${control}`, "--control-token", control],
    ["--url", viewUrl, "--control-token"],
  ]) {
    const loaded = await loadMain(args);
    assert.equal(loaded.windows.length, 0);
    assert.deepEqual(loaded.exits, [1]);
    assert.ok(!JSON.stringify(loaded.messages).includes(control));
  }
  // A view-only launch must work for the documented npm start command.
  const viewOnly = await loadMain(["--url", viewUrl]);
  assert.equal(viewOnly.windows.length, 1);
  assert.equal(viewOnly.windows[0].options.webPreferences.additionalArguments, undefined);
});

function loadPreload(args) {
  let exposed;
  const sent = [];
  vm.runInNewContext(readFileSync(path.join(desktop, "preload.js"), "utf8"), {
    require: (name) => {
      assert.equal(name, "electron");
      return {
        contextBridge: { exposeInMainWorld(name, api) { assert.equal(name, "specvaniaDesktop"); exposed = api; } },
        ipcRenderer: { send: (...args) => sent.push(args), sendSync: (channel) => {
          assert.equal(channel, "specvania:control-token");
          return args.includes("--control-token") ? control : "";
        } },
      };
    },
    process: { argv: ["electron", ...args] },
  }, { filename: "preload.js" });
  return { api: exposed, sent };
}

test("preload_exposes_only_T33_capabilities", () => {
  const { api, sent } = loadPreload(["--control-token", control]);
  assert.deepEqual(Object.keys(api).sort(), ["controlToken", "isDesktop", "notify", "pickFolder", "settings", "studio"]);
  assert.equal(api.isDesktop, true);
  assert.equal(api.controlToken(), control);
  assert.ok(!JSON.stringify(api).includes(control));
  assert.equal(loadPreload([]).api.controlToken(), "");
  api.notify("Approval requested", "Warrior is waiting for player");
  assert.equal(sent.length, 1);
  assert.equal(sent[0][0], "specvania:notify");
  assert.equal(sent[0][1].title, "Approval requested");
  assert.equal(sent[0][1].body, "Warrior is waiting for player");
  assert.ok(!JSON.stringify(sent).includes(control));
});

test("notification_bridge_rejects_foreign_senders_and_secrets", async () => {
  const loaded = await loadMain();
  const window = loaded.windows[0];
  const trusted = { sender: window.webContents, senderFrame: { url: viewUrl } };
  loaded.ipcMain.emit("specvania:notify", trusted, { title: "Room", body: "Room cleared" });
  assert.equal(loaded.notifications.length, 1);
  for (const [event, message] of [
    [{ sender: {}, senderFrame: { url: viewUrl } }, { title: "Room", body: "Room" }],
    [{ sender: window.webContents, senderFrame: { url: "https://example.invalid" } }, { title: "Room", body: "Room" }],
    [trusted, { title: control, body: "Room" }], [trusted, { title: "Room", body: control }],
    [trusted, { title: 1, body: "Room" }], [trusted, null],
  ]) loaded.ipcMain.emit("specvania:notify", event, message);
  assert.equal(loaded.notifications.length, 1);
  assert.ok(!JSON.stringify(loaded.notifications).includes(control));
});

test("desktop_package_uses_only_the_allowed_pinned_electron_dependency", () => {
  const metadata = JSON.parse(readFileSync(path.join(desktop, "package.json"), "utf8"));
  assert.deepEqual(metadata.dependencies || {}, {});
  assert.deepEqual(metadata.devDependencies, { electron: "44.7.0", "electron-builder": "26.15.3" });
  assert.equal(metadata.main, "main.js");
  assert.equal(metadata.scripts.start, "electron .");
});
