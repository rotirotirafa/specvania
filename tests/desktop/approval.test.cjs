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
  constructor(tag, document) { this.document = document; this.value = ""; this.tagName = tag; this.style = {}; this.dataset = {}; this.children = []; this.listeners = {}; this.disabled = false; }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  focus() { this.focused = true; this.document.activeElement = this; }
  click() { if (!this.disabled) this.listeners.click?.(); }
}
async function mount(desktop = undefined) {
  const document = {};
  const body = new Element("body", document);
  const head = new Element("head", document);
  const listeners = {};
  const sent = [];
  const socket = { readyState: 1, addEventListener(name, callback) { listeners[name] = callback; },
    send(data) { sent.push(JSON.parse(data)); } };
  Object.assign(document, { body, head, createElement: tag => new Element(tag, document) });
  const window = desktop === undefined ? {} : { specvaniaDesktop: desktop };
  const { mountApproval } = await import(pathToFileURL(path.join(root, "src/specvania/ui/static/hud/approval.js")));
  mountApproval(socket, { window, document });
  return { body, head, sent, socket, document, message: message => listeners.message({ data: JSON.stringify(message) }),
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
  app.getPath = () => path.join(require("node:os").tmpdir(), "sv-notification-no-state");
  app.exit = () => assert.fail("Main failed");
  app.quit = () => {};
  class BrowserWindow extends EventEmitter {
    constructor() { super(); window = this; this.focused = false; this.webContents = new EventEmitter(); this.webContents.setWindowOpenHandler = () => {}; }
    isFocused() { return this.focused; }
    loadURL() { return Promise.resolve(); }
  }
  class Notification {
    static isSupported() { return supported; }
    constructor(message) { this.message = message; }
    show() { notices.push(this.message); }
  }
  vm.runInNewContext(readFileSync(path.join(root, "apps/desktop/main.js"), "utf8"), {
    require: name => name === "electron" ? { app, BrowserWindow, ipcMain, Notification, shell: {}, Menu: {buildFromTemplate: value => value, setApplicationMenu() {}} } : require(name),
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


test("denial_reason_is_labelled_single_line_bounded_trimmed_and_sent_only_on_deny", async () => {
  for (const [answer, value, reason] of [[false, "  Syntax is corrupted  ", "Syntax is corrupted"], [false, "  ", undefined], [true, "Syntax is corrupted", undefined], [false, "x".repeat(200), "x".repeat(200)]]) {
    const ui = await mount({controlToken: () => token, notify() {}});
    ui.event(request);
    const input = flatten(ui.body).find(node => node.tagName === "input");
    assert.ok(input);
    assert.equal(input.type, "text");
    assert.equal(input.maxLength, 200);
    const label = flatten(ui.body).find(node => node.tagName === "label");
    assert.equal(label.textContent, "Reason for denial (optional)");
    assert.equal(label.htmlFor, input.id);
    input.value = value;
    buttons(ui).find(button => button.textContent === (answer ? "Allow" : "Deny")).click();
    const expected = {kind: "approval_answer", request_id: request.request_id, answer, control_token: token};
    if (reason) expected.reason = reason;
    assert.deepEqual(ui.sent, [expected]);
    assert.equal(input.disabled, true);
  }
});

test("denial_reason_is_in_dialog_tab_order_and_preserves_text_cursor_keys", async () => {
  const ui = await mount({controlToken: () => token, notify() {}});
  ui.event(request);
  const elements = flatten(ui.body), dialog = elements.find(node => node.role === "dialog");
  const input = elements.find(node => node.tagName === "input");
  assert.ok(input);
  const [allow, deny] = buttons(ui);
  assert.equal(ui.document.activeElement, deny);
  const key = (key, shiftKey = false) => dialog.listeners.keydown({key, shiftKey, preventDefault() {}});
  key("Tab");
  assert.equal(ui.document.activeElement.className, "expedition-content");
  key("Tab"); assert.equal(ui.document.activeElement, allow);
  key("Tab"); assert.equal(ui.document.activeElement, input);
  key("Tab"); assert.equal(ui.document.activeElement, deny);
  key("Tab", true); assert.equal(ui.document.activeElement, input);
  let prevented = false;
  dialog.listeners.keydown({key: "ArrowLeft", preventDefault() {prevented = true;}});
  assert.equal(prevented, false);
  assert.equal(ui.document.activeElement, input);
  input.value = "Fix the script";
  key("Tab"); key("Enter");
  assert.equal(ui.sent[0].reason, "Fix the script");
});

test("denial_reason_resolved_status_and_replay_dialogue_translate_and_render_as_text", async () => {
  const {setLanguage} = await import(pathToFileURL(path.join(root, "src/specvania/ui/static/i18n/index.js")));
  const {dialogueFor} = await import(pathToFileURL(path.join(root, "src/specvania/ui/static/hud/dialogue.js")));
  const event = {type: "approval_answered", request_id: request.request_id, answer: false, responder: "desktop window", reason: "<script>corrupted</script>"};
  const ui = await mount({controlToken: () => token, notify() {}});
  ui.event(request); ui.event(event);
  try {
    for (const [language, expected] of [["en", "denied in desktop window: <script>corrupted</script>"], ["pt-BR", "negado em janela desktop: <script>corrupted</script>"]]) {
      setLanguage(language);
      assert.equal(dialogueFor(event).text, expected);
      assert.ok(flatten(ui.body).some(node => node.textContent === expected));
      assert.ok(!flatten(ui.body).some(node => node.tagName === "script"));
    }
    setLanguage("en");
    assert.equal(dialogueFor({...event, answer: true}).text, "approved in desktop window");
  } finally {setLanguage("en");}
});


test("rejected_denial_reenables_controls_preserves_reason_and_allows_correction", async () => {
  const ui = await mount({controlToken: () => token, notify() {}});
  ui.event(request);
  const input = flatten(ui.body).find(node => node.tagName === "input");
  input.value = "mistyped-secret";
  buttons(ui).find(button => button.textContent === "Deny").click();
  assert.equal(input.disabled, true);
  ui.message({kind:"approval_rejected", reason:"invalid message"});
  assert.equal(input.disabled, false);
  assert.ok(buttons(ui).every(button => !button.disabled));
  assert.equal(input.value, "mistyped-secret");
  assert.ok(flatten(ui.body).some(node => node.role === "dialog"));
  input.value = "Fix the script";
  buttons(ui).find(button => button.textContent === "Deny").click();
  assert.equal(ui.sent[1].reason, "Fix the script");
  ui.message({kind:"approval_rejected", reason:"invalid message"});
  input.value = "";
  buttons(ui).find(button => button.textContent === "Deny").click();
  assert.deepEqual(ui.sent[2], {kind:"approval_answer",request_id:request.request_id,answer:false,control_token:token});
  ui.close();
  ui.message({kind:"approval_rejected", reason:"request unavailable"});
  assert.equal(input.disabled, true);
  assert.ok(buttons(ui).every(button => button.disabled));
});
