"use strict";

const { test, after } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync, writeFileSync, mkdtempSync, rmSync } = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { EventEmitter } = require("node:events");

const desktop = path.resolve(__dirname, "../../apps/desktop");
const viewUrl = "http://127.0.0.1:8765/?view_token=fake-view";
const control = "fake-control-secret";

const directories = [];
after(() => directories.forEach(directory => rmSync(directory, {recursive: true, force: true})));

async function loadMain(args = ["--url", viewUrl, "--control-token", control], failLoad = false, config = {}) {
  const directory = config.directory || mkdtempSync(path.join(os.tmpdir(), "sv-window-"));
  if (!config.directory) directories.push(directory);
  const windows = [];
  const external = [];
  const messages = [];
  const notifications = [];
  const exits = [];
  const app = new EventEmitter();
  app.whenReady = () => Promise.resolve();
  app.getPath = name => { assert.equal(name, "userData"); return directory; };
  app.isPackaged = config.packaged ?? true;
  app.name = "specvania";
  let menu;
  app.exit = (code) => exits.push(code);
  app.quit = () => exits.push(0);
  const ipcMain = new EventEmitter();
  class BrowserWindow extends EventEmitter {
    constructor(options) {
      super();
      this.bounds = {x: options.x ?? 100, y: options.y ?? 80, width: options.width, height: options.height};
      this.maximized = false; this.fullScreen = false; this.zoomLevel = 0;
      this.options = options;
      this.webContents = new EventEmitter();
      this.webContents.setWindowOpenHandler = (handler) => { this.openHandler = handler; };
      this.webContents.getURL = () => this.url;
      this.webContents.getZoomLevel = () => this.zoomLevel;
      this.webContents.setZoomLevel = level => { this.zoomLevel = level; };
      this.focused = false;
      windows.push(this);
    }
    setAspectRatio(ratio) { this.ratio = ratio; }
    isFocused() { return this.focused; }
    getNormalBounds() { return this.bounds; }
    isMaximized() { return this.maximized; }
    isFullScreen() { return this.fullScreen; }
    maximize() { this.maximized = true; this.emit("maximize"); }
    setFullScreen(value) { this.fullScreen = value; this.emit(value ? "enter-full-screen" : "leave-full-screen"); }
    loadURL(url) {
      this.webContents.emit("did-start-navigation", {}, url, false, true);
      this.url = url;
      if (!failLoad) this.webContents.emit("did-finish-load");
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
      screen: {getAllDisplays: () => config.displays || [{workArea: {x: 0, y: 0, width: 2560, height: 1440}}]},
      Menu: {buildFromTemplate: template => template, setApplicationMenu: value => {menu = value;}},
      shell: { openExternal: (url) => { external.push(url); return Promise.resolve(); } },
    } : require(name),
    process: { argv: ["electron", ".", ...args], platform: config.platform || "darwin" },
    __dirname: desktop,
    URL,
    console: { error: (...items) => messages.push(items.join(" ")) },
  }, { filename: "main.js" });
  // Settle whenReady and loadURL's promise without timers or a real Electron app.
  await new Promise((resolve) => setImmediate(resolve));
  return { windows, external, messages, notifications, exits, app, ipcMain, menu, directory };
}

test("secure_window_options_and_single_resizable_native_window", async () => {
  const loaded = await loadMain();
  assert.equal(loaded.windows.length, 1);
  const window = loaded.windows[0];
  const options = window.options;
  assert.equal(options.frame, true);
  assert.equal(options.titleBarStyle, "hiddenInset");
  assert.equal(options.resizable, true);
  assert.equal(options.maximizable, true);
  assert.equal(options.fullscreenable, true);
  assert.equal(options.minWidth, 960);
  assert.equal(options.minHeight, 540);
  assert.deepEqual([options.width, options.height], [960, 540]);
  assert.ok(options.width >= 960 && options.height >= 540);
  assert.equal(window.ratio, undefined);
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
    process: { argv: ["electron", ...args], platform: "darwin" },
  }, { filename: "preload.js" });
  return { api: exposed, sent };
}

test("preload_exposes_only_T33_capabilities", () => {
  const { api, sent } = loadPreload(["--control-token", control]);
  assert.deepEqual(Object.keys(api).sort(), ["controlToken", "isDesktop", "notify", "pickFolder", "platform", "settings", "studio"]);
  assert.equal(api.isDesktop, true);
  assert.equal(api.platform, "darwin");
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

for (const platform of ["darwin", "win32", "linux"]) test(`native_controls_on_${platform}`, async () => {
  const h = await loadMain(undefined, false, {platform});
  const window = h.windows[0];
  assert.equal(window.options.frame, true);
  assert.equal(window.options.titleBarStyle, platform === "darwin" ? "hiddenInset" : undefined);
  assert.equal(window.options.minimizable, true);
  assert.equal(window.options.maximizable, true);
  assert.equal(window.options.fullscreenable, true);
  assert.equal(window.ratio, undefined);
});

test("window_state_restores_normal_bounds_modes_and_zoom_without_tokens", async () => {
  const first = await loadMain();
  const window = first.windows[0];
  window.bounds = {x: 180, y: 90, width: 1280, height: 1000}; window.zoomLevel = 1.5;
  window.emit("resize"); window.emit("move");
  const statePath = path.join(first.directory, "desktop-window.json");
  assert.deepEqual(JSON.parse(readFileSync(statePath, "utf8")), {
    bounds: {x: 180, y: 90, width: 1280, height: 1000}, maximized: false, fullScreen: false, zoomLevel: 1.5,
  });
  window.maximize(); window.setFullScreen(true); window.emit("close");
  assert.equal(JSON.parse(readFileSync(statePath, "utf8")).maximized, true);
  assert.equal(JSON.parse(readFileSync(statePath, "utf8")).fullScreen, true);
  const second = await loadMain(undefined, false, {directory: first.directory});
  const restored = second.windows[0];
  assert.deepEqual([restored.options.x, restored.options.y, restored.options.width, restored.options.height], [180, 90, 1280, 1000]);
  assert.equal(restored.maximized, true); assert.equal(restored.fullScreen, true); assert.equal(restored.zoomLevel, 1.5);
  restored.emit("resize"); restored.emit("close");
  const saved = readFileSync(statePath, "utf8");
  assert.ok(!saved.includes(control)); assert.ok(!saved.includes("fake-view"));
  assert.deepEqual(JSON.parse(saved).bounds, {x: 180, y: 90, width: 1280, height: 1000});
  restored.setFullScreen(false); restored.maximized = false; restored.emit("unmaximize");
  restored.bounds = {x: 40, y: 50, width: 1920, height: 800}; restored.zoomLevel = -1;
  second.app.emit("before-quit", {preventDefault() {}});
  const third = await loadMain(undefined, false, {directory: first.directory});
  assert.deepEqual([third.windows[0].options.width, third.windows[0].options.height], [1920, 800]);
  assert.equal(third.windows[0].maximized, false); assert.equal(third.windows[0].fullScreen, false); assert.equal(third.windows[0].zoomLevel, -1);
});

test("window_state_centers_default_when_offscreen_and_accepts_negative_display_coordinates", async () => {
  const first = await loadMain();
  const statePath = path.join(first.directory, "desktop-window.json");
  const saved = {bounds: {x: -1800, y: 100, width: 1280, height: 1000}, maximized: false, fullScreen: false, zoomLevel: 0};
  writeFileSync(statePath, JSON.stringify(saved));
  const offscreen = await loadMain(undefined, false, {directory: first.directory});
  assert.deepEqual([offscreen.windows[0].options.width, offscreen.windows[0].options.height], [960, 540]);
  assert.equal(offscreen.windows[0].options.x, undefined); assert.equal(offscreen.windows[0].options.y, undefined);
  assert.equal(offscreen.windows[0].options.center, true);
  writeFileSync(statePath, JSON.stringify(saved));
  const connected = await loadMain(undefined, false, {directory: first.directory, displays: [{workArea: {x: -1920, y: 0, width: 1920, height: 1080}}]});
  assert.equal(connected.windows[0].options.x, -1800); assert.equal(connected.windows[0].options.y, 100);
  writeFileSync(statePath, JSON.stringify({...saved, bounds: {x: 0, y: -990, width: 1280, height: 1000}}));
  const unreachable = await loadMain(undefined, false, {directory: first.directory});
  assert.equal(unreachable.windows[0].options.center, true, "visible bottom edge alone does not make the title bar reachable");
});

test("window_state_rejects_malformed_or_unvalidated_values", async () => {
  const first = await loadMain();
  const valid = {bounds: {x: 80, y: 60, width: 1280, height: 1000}, maximized: true, fullScreen: true, zoomLevel: 1};
  const invalid = [
    {bounds: {...valid.bounds, width: 959}}, {bounds: {...valid.bounds, height: 539}},
    {bounds: {...valid.bounds, x: 1.5}}, {bounds: {...valid.bounds, y: "60"}},
    {maximized: "true"}, {fullScreen: []}, {zoomLevel: "secret"}, {zoomLevel: 999},
  ];
  for (const value of ["not JSON", "null", ...invalid.map(patch => JSON.stringify({...valid, ...patch}))]) {
    writeFileSync(path.join(first.directory, "desktop-window.json"), value);
    const h = await loadMain(undefined, false, {directory: first.directory});
    assert.deepEqual([h.windows[0].options.width, h.windows[0].options.height], [960, 540]);
    assert.equal(h.windows[0].maximized, false); assert.equal(h.windows[0].fullScreen, false); assert.equal(h.windows[0].zoomLevel, 0);
  }
});

const menuItems = items => Array.from(items).flatMap(item => [item, ...menuItems(item.submenu || [])]);
test("application_menu_has_standard_roles_and_packaged_build_omits_development", async () => {
  for (const platform of ["darwin", "win32", "linux"]) for (const packaged of [true, false]) {
    const h = await loadMain(undefined, false, {platform, packaged});
    assert.ok(h.menu, "explicit application menu is installed");
    const roles = menuItems(h.menu).map(item => item.role).filter(Boolean);
    for (const role of ["about", "quit", "undo", "redo", "cut", "copy", "paste", "selectAll", "resetZoom", "zoomIn", "zoomOut", "togglefullscreen", "minimize", "zoom", "close"])
      assert.ok(roles.includes(role), role);
    if (platform === "darwin") assert.ok(roles.includes("hide"));
    for (const role of ["reload", "forceReload", "toggleDevTools"]) assert.ok(!packaged || !roles.includes(role), role);
    for (const [role, accelerator] of [["zoomIn", "CmdOrCtrl+="], ["zoomOut", "CmdOrCtrl+-"], ["resetZoom", "CmdOrCtrl+0"]])
      assert.equal(menuItems(h.menu).find(item => item.role === role).accelerator, accelerator);
    h.windows[0].zoomLevel = 2; h.windows[0].emit("close");
    const next = await loadMain(undefined, false, {directory: h.directory, platform, packaged});
    assert.equal(next.windows[0].zoomLevel, 2, "menu-originated zoom is saved on close");
  }
});

test("page_zoom_survives_navigation_between_local_startup_and_studio", async () => {
  const h = await loadMain(); const window = h.windows[0]; window.zoomLevel = 1;
  window.webContents.emit("did-start-navigation", {}, "http://127.0.0.1:8888/", false, true);
  window.zoomLevel = 0; // Electron has a separate initial zoom for a new origin.
  window.webContents.emit("did-finish-load");
  assert.equal(window.zoomLevel, 1);
});
