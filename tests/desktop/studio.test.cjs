"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { spawnSync } = require("node:child_process");
const { launchStudio, quitStudio, shellLaunch } = require("../../apps/desktop/studio.cjs");
const handshake = { port: 8765, view_token: "view-secret", control_token: "control-secret" };
function harness(options = {}) {
  const child = new EventEmitter();
  child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
  child.kill = () => { child.killed = true; };
  const timers = new Map(); let timerId = 0;
  const opened = [], errors = [], logs = [], spawns = [];
  const timer = { setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { fn, ms }); return id; }, clearTimeout(id) { timers.delete(id); } };
  const lifecycle = launchStudio({
    spawn: (...args) => { spawns.push(args); return child; },
    env: { SHELL: "/bin/bash", PATH: "/safe/bin" }, platform: "linux", executable: "specvania",
    timer, logPath: "/safe/userData/logs/studio.log", log: (data) => logs.push(String(data)),
    open: (url, token) => opened.push({ url, token }), error: (page) => errors.push(page), ...options,
  });
  return { child, timers, opened, errors, logs, spawns, lifecycle, timer };
}
test("login_shell_spawn_and_handshake_open_game_without_tokens_in_spawn", () => {
  const h = harness();
  assert.deepEqual(h.spawns[0], ["/bin/bash", ["-lic", "exec 'specvania' studio"], { env: { SHELL: "/bin/bash", PATH: "/safe/bin" }, stdio: ["ignore", "pipe", "pipe"] }]);
  h.child.stdout.emit("data", JSON.stringify(handshake).slice(0, 20));
  assert.equal(h.opened.length, 0);
  h.child.stdout.emit("data", JSON.stringify(handshake).slice(20) + "\n");
  assert.deepEqual(h.opened, [{ url: "http://127.0.0.1:8765/?view_token=view-secret", token: "control-secret" }]);
  assert.equal(h.timers.size, 0);
  for (const token of [handshake.view_token, handshake.control_token]) assert.ok(!JSON.stringify(h.spawns).includes(token));
  h.child.stderr.emit("data", "ordinary log\n");
  assert.equal(h.logs.join(""), "ordinary log\n");
});
test("settings_executable_is_shell_quoted_even_with_spaces_quotes_and_injection", () => {
  for (const executable of ["/a path/specvania", "/a'path/specvania", "/a'$(echo injected); path/specvania"]) {
    const launch = shellLaunch({ executable, env: {}, platform: "linux" });
    assert.equal(launch.command, "/bin/bash");
    // Have a real shell parse the quoted command, replacing exec with argv printing.
    const parsed = spawnSync("/bin/bash", ["-c", launch.args[1].replace(/^exec /, "printf '%s\\n' ")], { encoding: "utf8" });
    assert.equal(parsed.status, 0);
    assert.equal(parsed.stdout, executable + "\nstudio\n");
  }
  assert.equal(shellLaunch({ executable: "specvania", env: {}, platform: "darwin" }).command, "/bin/zsh");
});
test("invalid_handshakes_produce_local_nonsecret_error_with_log_path", () => {
  for (const value of [{}, { ...handshake, port: 0 }, { ...handshake, port: 65536 }, { ...handshake, port: true }, { ...handshake, port: "8765" }, { ...handshake, control_token: "" }, { ...handshake, control_token: handshake.view_token }]) {
    const h = harness();
    h.child.stdout.emit("data", typeof value === "string" ? value + "\n" : JSON.stringify(value) + "\n");
    assert.equal(h.opened.length, 0); assert.equal(h.errors.length, 1);
    assert.match(h.errors[0], /\/safe\/userData\/logs\/studio.log/);
    for (const token of [handshake.view_token, handshake.control_token]) assert.ok(!h.errors[0].includes(token));
    assert.ok(h.child.killed);
  }
});
test("twenty_second_timeout_and_early_exit_show_plain_error_page", () => {
  for (const early of [false, true]) {
    const h = harness();
    assert.equal([...h.timers.values()][0].ms, 20000);
    h.child.stdout.emit("data", '{"control_token":"control-secret"');
    if (early) h.child.emit("exit", 1); else [...h.timers.values()][0].fn();
    assert.equal(h.errors.length, 1); assert.equal(h.opened.length, 0);
    assert.match(h.errors[0], /\/safe\/userData\/logs\/studio.log/);
    assert.ok(!h.errors[0].includes(handshake.control_token));
    assert.ok(!/https?:|<script|<iframe/.test(h.errors[0]));
    h.child.stdout.emit("data", JSON.stringify(handshake) + "\n");
    assert.equal(h.opened.length, 0);
  }
});
test("quit_sends_authenticated_shutdown_then_kills_after_five_seconds", async () => {
  const h = harness(); h.child.stdout.emit("data", JSON.stringify(handshake) + "\n");
  const sockets = [];
  const quitting = quitStudio(h.lifecycle, { timer: h.timer, connect(url, origin) {
    const socket = new EventEmitter(); socket.send = (data) => { socket.message = JSON.parse(data); }; socket.close = () => {};
    sockets.push({ url, origin, socket }); return socket;
  }});
  assert.equal(sockets[0].url, "ws://127.0.0.1:8765/ws?view_token=view-secret");
  assert.equal(sockets[0].origin, "http://127.0.0.1:8765");
  sockets[0].socket.emit("open");
  assert.deepEqual(sockets[0].socket.message, { kind: "shutdown", control_token: handshake.control_token });
  assert.equal([...h.timers.values()][0].ms, 5000); assert.ok(!h.child.killed);
  [...h.timers.values()][0].fn(); assert.ok(h.child.killed); await quitting;
});
test("quit_cancels_kill_when_child_exits_normally", async () => {
  const h = harness(); h.child.stdout.emit("data", JSON.stringify(handshake) + "\n");
  const quitting = quitStudio(h.lifecycle, { timer: h.timer, connect() { return new EventEmitter(); } });
  h.child.emit("exit", 0); await quitting;
  assert.equal(h.timers.size, 0); assert.ok(!h.child.killed);
});

test("control_websocket_upgrade_checks_origin_and_sends_masked_shutdown_frame", () => {
  const { connectControl } = require("../../apps/desktop/studio.cjs");
  const { createHash } = require("node:crypto");
  let options; const request = new EventEmitter(); request.end = () => {}; request.destroy = () => {};
  const socket = new EventEmitter(); const written = []; socket.write = (data) => written.push(data); socket.destroy = () => {};
  const control = connectControl("ws://127.0.0.1:8765/ws?view_token=view-secret", "http://127.0.0.1:8765", (value) => { options = value; return request; });
  control.on("open", () => control.send(JSON.stringify({ kind: "shutdown", control_token: "control-secret" })));
  assert.equal(options.headers.Origin, "http://127.0.0.1:8765");
  assert.equal(options.path, "/ws?view_token=view-secret");
  const accept = createHash("sha1").update(options.headers["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest("base64");
  request.emit("upgrade", { headers: { "sec-websocket-accept": accept } }, socket);
  const frame = written[0]; assert.equal(frame[0], 0x81); assert.equal(frame[1] & 0x80, 0x80);
  const payload = Buffer.from(frame.subarray(6));
  for (let index = 0; index < payload.length; index++) payload[index] ^= frame[2 + index % 4];
  assert.deepEqual(JSON.parse(payload.toString()), { kind: "shutdown", control_token: "control-secret" });
});

test("main_without_url_reads_settings_logs_stderr_and_uses_guarded_ipc", async () => {
  const vm = require("node:vm");
  const fs = require("node:fs");
  const path = require("node:path");
  const os = require("node:os");
  const studio = require("../../apps/desktop/studio.cjs");
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "sv-settings-"));
  const h = harness(); // Independent fake child and clock used by actual main wiring.
  h.child.emit("exit", 0);
  const child = new EventEmitter(); child.stdout = new EventEmitter(); child.stderr = new EventEmitter(); child.kill = () => {};
  const spawns = [], pages = [], logged = [];
  const app = new EventEmitter(); app.whenReady = () => Promise.resolve(); app.getPath = () => directory; app.quit = () => {}; app.exit = () => assert.fail("main should open studio");
  const ipcMain = new EventEmitter();
  let window;
  class BrowserWindow {
    constructor(options) { this.options = options; this.webContents = new EventEmitter(); this.webContents.setWindowOpenHandler = () => {}; window = this; }
    setAspectRatio() {} isFocused() { return true; } loadURL(url) { pages.push(url); return Promise.resolve(); }
  }
  try {
    fs.writeFileSync(path.join(directory, "settings.json"), JSON.stringify({ specvaniaPath: "/a path/spec'vania" }));
    assert.equal(studio.readSettings(directory), "/a path/spec'vania");
    vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, "../../apps/desktop/main.js"), "utf8"), {
      require(name) {
        if (name === "electron") return { app, BrowserWindow, ipcMain, Notification: {}, shell: {} };
        if (name === "node:child_process") return { spawn: (...args) => { spawns.push(args); return child; } };
        if (name === "node:fs") return { mkdirSync: fs.mkdirSync, createWriteStream: () => { const stream = new EventEmitter(); stream.write = (data) => logged.push(String(data)); stream.end = () => {}; return stream; } };
        if (name.endsWith("studio.cjs")) return { ...studio, launchStudio: (options) => studio.launchStudio({ ...options, timer: h.timer, env: { SHELL: "/bin/bash" } }) };
        return require(name);
      },
      __dirname: path.resolve(__dirname, "../../apps/desktop"), process: { argv: ["electron", "."] }, URL, console,
    });
    await new Promise((resolve) => setImmediate(resolve));
    assert.match(spawns[0][1][1], /^exec /);
    assert.ok(spawns[0][1][1].includes("/a path/spec"));
    assert.equal(pages.length, 0);
    child.stderr.emit("data", "safe diagnostic\n");
    assert.equal(logged.join(""), "safe diagnostic\n");
    child.stdout.emit("data", JSON.stringify(handshake) + "\n");
    assert.equal(pages[0], "http://127.0.0.1:8765/?view_token=view-secret");
    assert.equal(window.options.webPreferences.additionalArguments, undefined);
    const trusted = { sender: window.webContents, senderFrame: { url: pages[0] } };
    ipcMain.emit("specvania:control-token", trusted); assert.equal(trusted.returnValue, handshake.control_token);
    const foreign = { sender: window.webContents, senderFrame: { url: "https://example.invalid/" } };
    ipcMain.emit("specvania:control-token", foreign); assert.equal(foreign.returnValue, "");
    assert.ok(!JSON.stringify(spawns).includes(handshake.control_token));
    assert.ok(!JSON.stringify(spawns).includes(handshake.view_token));
    child.emit("exit", 1);
    assert.equal(pages.length, 2);
    assert.ok(pages[1].startsWith("data:text/html;charset=utf-8,"));
    const stoppedPage = decodeURIComponent(pages[1].split(",").slice(1).join(","));
    assert.ok(stoppedPage.includes(path.join(directory, "logs/studio.log")));
    for (const token of [handshake.view_token, handshake.control_token]) assert.ok(!stoppedPage.includes(token));
    ipcMain.emit("specvania:control-token", trusted);
    assert.equal(trusted.returnValue, "");
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test("idle_studio_transport_does_not_send_empty_run_to_map", async () => {
  const { pathToFileURL } = require("node:url");
  const path = require("node:path");
  const listeners = {}, dispatched = [];
  const runState = {};
  global.window = { location: { href: "http://127.0.0.1:8765/?view_token=watch", search: "?view_token=watch" }, dispatchEvent: (event) => dispatched.push(event) };
  global.document = { getElementById: (id) => id === "run-state" ? runState : {} };
  global.CustomEvent = class { constructor(type, options) { this.type = type; this.detail = options.detail; } };
  global.WebSocket = class { addEventListener(name, fn) { listeners[name] = fn; } };
  try {
    await import(pathToFileURL(path.resolve(__dirname, "../../src/specvania/ui/static/main.js")).href + "?studio-idle");
    listeners.message({ data: JSON.stringify({ kind: "state", phase: "idle", state: { run_id: null } }) });
    assert.equal(runState.textContent, "Studio: idle");
    assert.deepEqual(dispatched.map((event) => event.type), ["specvania:studio-state"]);
  } finally { delete global.window; delete global.document; delete global.CustomEvent; delete global.WebSocket; }
});

test("studio_exit_after_handshake_shows_local_error_without_tokens", () => {
  const h = harness();
  h.child.stdout.emit("data", JSON.stringify(handshake) + "\n");
  h.child.emit("exit", 1);
  assert.equal(h.opened.length, 1);
  assert.equal(h.errors.length, 1);
  assert.match(h.errors[0], /Studio stopped\./);
  assert.match(h.errors[0], /\/safe\/userData\/logs\/studio.log/);
  for (const token of [handshake.view_token, handshake.control_token]) assert.ok(!h.errors[0].includes(token));
  assert.ok(!/https?:|<script|<iframe/.test(h.errors[0]));
});

test("studio_exit_during_quit_does_not_show_error_page", async () => {
  const h = harness();
  h.child.stdout.emit("data", JSON.stringify(handshake) + "\n");
  const quitting = quitStudio(h.lifecycle, { timer: h.timer, connect() { return new EventEmitter(); } });
  h.child.emit("exit", 0);
  await quitting;
  assert.equal(h.errors.length, 0);
});

test("login_shell_noise_before_handshake_is_skipped", () => {
  for (const chunked of [false, true]) {
    const h = harness();
    const noise = "Welcome!\n\u001b[32mShell ready\u001b[0m\n";
    if (chunked) {
      h.child.stdout.emit("data", noise.slice(0, 5));
      h.child.stdout.emit("data", noise.slice(5));
      assert.equal(h.errors.length, 0);
      assert.equal([...h.timers.values()][0].ms, 20000);
      h.child.stdout.emit("data", JSON.stringify(handshake) + "\n");
    } else {
      h.child.stdout.emit("data", noise + JSON.stringify(handshake) + "\n");
    }
    assert.deepEqual(h.opened, [{ url: "http://127.0.0.1:8765/?view_token=view-secret", token: "control-secret" }]);
    assert.equal(h.errors.length, 0);
    assert.equal(h.timers.size, 0);
  }
});

test("login_shell_noise_keeps_size_cap_timeout_and_json_validation", () => {
  const oversized = harness();
  for (let index = 0; index < 17; index++) oversized.child.stdout.emit("data", "x".repeat(1023) + "\n");
  assert.equal(oversized.errors.length, 1);
  assert.equal(oversized.opened.length, 0);
  const timeout = harness();
  timeout.child.stdout.emit("data", "Welcome!\n");
  [...timeout.timers.values()][0].fn();
  assert.equal(timeout.errors.length, 1);
  assert.equal(timeout.opened.length, 0);
  const invalid = harness();
  invalid.child.stdout.emit("data", "Welcome!\n{}\n" + JSON.stringify(handshake) + "\n");
  assert.equal(invalid.errors.length, 1);
  assert.equal(invalid.opened.length, 0);
});
