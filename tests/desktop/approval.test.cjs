"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { pathToFileURL } = require("node:url");
const path = require("node:path");
const vm = require("node:vm");
const { EventEmitter } = require("node:events");
const root = path.resolve(__dirname, "../..");
const token = "desktop-control-secret";

class Element {
  constructor(tag) { this.tagName = tag; this.style = {}; this.children = []; this.listeners = {}; this.disabled = false; }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  focus() { this.focused = true; }
  click() { if (!this.disabled) this.listeners.click?.(); }
}
async function mount(desktop = undefined) {
  const body = new Element("body");
  const head = new Element("head");
  const listeners = {};
  const sent = [];
  const socket = { readyState: 1, addEventListener(name, callback) { listeners[name] = callback; },
    send(data) { sent.push(JSON.parse(data)); } };
  const document = { body, head, createElement: tag => new Element(tag) };
  const window = desktop === undefined ? {} : { specvaniaDesktop: desktop };
  const { mountApproval } = await import(pathToFileURL(path.join(root, "src/specvania/ui/static/hud/approval.js")));
  mountApproval(socket, { window, document });
  return { body, head, sent, socket, message: message => listeners.message({ data: JSON.stringify(message) }),
    event: event => listeners.message({ data: JSON.stringify({ kind: "event", event }) }),
    close: () => listeners.close() };
}
const request = { type: "approval_requested", request_id: "approval-1", command: "printf '<script>safe</script>'", cwd: "/tmp/world" };
function flatten(element) { return [element, ...element.children.flatMap(flatten)]; }
function buttons(ui) { return flatten(ui.body).filter(element => element.tagName === "button"); }

test("rpg_choice_shows_command_cwd_palette_font_and_sends_one_authenticated_answer", async () => {
  const { UI_COLORS } = await import(pathToFileURL(path.join(root, "src/specvania/ui/static/pixel/palette.js")));
  for (const [label, answer] of [["Allow", true], ["Deny", false]]) {
    const ui = await mount({ controlToken: () => token, notify() {} });
    ui.event(request);
    const elements = flatten(ui.body);
    const box = elements.find(element => element.role === "dialog");
    assert.ok(box);
    assert.equal(box.style.backgroundColor, UI_COLORS.surface);
    assert.equal(box.style.color, UI_COLORS.text);
    assert.match(box.style.border, new RegExp(UI_COLORS.border));
    assert.match(box.style.fontFamily, /Press Start 2P/);
    assert.ok(elements.some(element => element.textContent === request.command));
    assert.ok(elements.some(element => element.textContent === `cwd: ${request.cwd}`));
    assert.deepEqual(buttons(ui).map(button => button.textContent), ["Allow", "Deny"]);
    buttons(ui).find(button => button.textContent === label).click();
    assert.deepEqual(ui.sent, [{ kind: "approval_answer", request_id: "approval-1", answer, control_token: token }]);
    for (const button of buttons(ui)) { assert.equal(button.disabled, true); button.click(); }
    assert.equal(ui.sent.length, 1);
    assert.ok(!flatten(ui.body).some(element => (element.textContent || "").includes(token)));
    ui.event({ type: "approval_answered", request_id: "approval-1", answer, responder: "desktop window" });
    assert.equal(buttons(ui).length, 0);
    assert.ok(!flatten(ui.body).some(element => element.role === "dialog"));
  }
});

test("terminal_answer_dismisses_only_matching_choice_and_socket_close_disables_answers", async () => {
  const ui = await mount({ controlToken: () => token, notify() {} });
  ui.event(request);
  ui.event({ type: "approval_answered", request_id: "other", answer: true, responder: "terminal" });
  assert.equal(buttons(ui).length, 2);
  ui.event({ type: "approval_answered", request_id: request.request_id, answer: false, responder: "terminal" });
  assert.equal(buttons(ui).length, 0);
  assert.ok(!flatten(ui.body).some(element => element.role === "dialog"));
  assert.ok(flatten(ui.body).some(element => element.textContent === "denied in terminal"));
  ui.event({ ...request, request_id: "next" });
  assert.equal(buttons(ui).length, 2);
  ui.close();
  for (const button of buttons(ui)) button.click();
  assert.equal(ui.sent.length, 0);
});

test("browser_read_only_for_browser_and_desktop_without_control_token", async () => {
  for (const desktop of [undefined, { controlToken: () => "", notify() {} }]) {
    const ui = await mount(desktop);
    ui.event(request);
    assert.equal(buttons(ui).length, 0);
    const elements = flatten(ui.body);
    assert.ok(elements.some(element => element.textContent === "waiting for player — answer in the terminal or desktop window"));
    assert.ok(elements.some(element => element.textContent === request.command));
    assert.equal(ui.sent.length, 0);
  }
});

test("notification_renderer_filters_exactly_three_event_types_and_failed_review", async () => {
  const notices = [];
  const ui = await mount({ controlToken: () => token, notify: (...args) => notices.push(args) });
  ui.event(request);
  ui.event({ type: "review", verdict: "fail", room_id: "T20", feedback: "Gate failed" });
  ui.event({ type: "boss_result", area_id: "A5", passed: true });
  ui.event({ type: "boss_result", area_id: "A5", passed: false });
  assert.equal(notices.length, 4);
  assert.deepEqual(notices.map(notice => notice[0]), ["Approval requested", "Paladin review failed", "Boss result", "Boss result"]);
  assert.match(notices[0][1], /printf/);
  assert.match(notices[1][1], /T20/);
  assert.match(notices[2][1], /passed/);
  assert.match(notices[3][1], /failed/);
  for (const type of ["review", "approval_answered", "boss_started", "boss_scenario", "room_cleared", "cooldown", "fallback", "run_paused"]) {
    ui.event({ type, verdict: "pass", request_id: "other" });
  }
  assert.equal(notices.length, 4);
});

test("rpg_choice_restores_pending_snapshot_and_uses_full_transient_prompt", async () => {
  const notices = [];
  const ui = await mount({ controlToken: () => token, notify: (...args) => notices.push(args) });
  const full = { request_id: "pending", command: "printf " + "x".repeat(500), cwd: "/tmp/world" };
  ui.message({ kind: "state", state: { seq: 1 }, approval_request: full });
  assert.ok(flatten(ui.body).some(element => element.textContent === full.command));
  assert.equal(buttons(ui).length, 2);
  assert.equal(notices.length, 0, "Connecting must not repeat a native notification");
  ui.event({ type: "approval_answered", request_id: "pending", answer: false, responder: "terminal" });
  assert.equal(buttons(ui).length, 0);
  ui.message({ kind: "event", event: { ...request, command: "summary" }, approval_request: { ...full, request_id: request.request_id } });
  assert.ok(flatten(ui.body).some(element => element.textContent === full.command));
  assert.equal(notices.length, 1);
});

test("notification_main_process_checks_actual_window_focus_and_notification_support", async () => {
  let window;
  let supported = true;
  const notices = [];
  const ipcMain = new EventEmitter();
  const app = new EventEmitter();
  app.whenReady = () => Promise.resolve();
  app.exit = () => assert.fail("Main failed");
  app.quit = () => {};
  class BrowserWindow {
    constructor() { window = this; this.focused = false; this.webContents = new EventEmitter(); this.webContents.setWindowOpenHandler = () => {}; }
    setAspectRatio() {}
    isFocused() { return this.focused; }
    loadURL() { return Promise.resolve(); }
  }
  class Notification {
    static isSupported() { return supported; }
    constructor(message) { this.message = message; }
    show() { notices.push(this.message); }
  }
  vm.runInNewContext(readFileSync(path.join(root, "apps/desktop/main.js"), "utf8"), {
    require: name => name === "electron" ? { app, BrowserWindow, ipcMain, Notification, shell: {} } : require(name),
    process: { argv: ["electron", ".", "--url", "http://127.0.0.1:8765/?view_token=view", "--control-token", token] },
    __dirname: path.join(root, "apps/desktop"), URL, console,
  });
  await new Promise(resolve => setImmediate(resolve));
  const sender = { sender: window.webContents, senderFrame: { url: "http://127.0.0.1:8765/" } };
  for (const focused of [false, true]) {
    window.focused = focused;
    const before = notices.length;
    const ui = await mount({ controlToken: () => token, notify(title, body) { ipcMain.emit("specvania:notify", sender, { title, body }); } });
    ui.event(request);
    ui.event({ type: "review", verdict: "fail", room_id: "T20", feedback: "Failed gate" });
    ui.event({ type: "boss_result", passed: false, area_id: "A5" });
    assert.equal(notices.length - before, focused ? 0 : 3);
  }
  window.focused = false;
  supported = false;
  ipcMain.emit("specvania:notify", sender, { title: "Boss result", body: "A5 passed" });
  assert.equal(notices.length, 3);
});
