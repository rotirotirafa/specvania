"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {EventEmitter} = require("node:events");
const root = path.resolve(__dirname, "../../src/specvania/ui/static");
const load = name => import(pathToFileURL(path.join(root, name)));

async function transport(options = {}) {
  const {connectSocket} = await load("transport.js");
  const sockets = [], timers = new Map(), delays = [];
  let nextTimer = 0;
  class FakeWebSocket extends EventEmitter {
    constructor(url) {super(); this.url = url; this.readyState = 0; this.sent = []; sockets.push(this);}
    addEventListener(kind, fn) {this.on(kind, fn);}
    removeEventListener(kind, fn) {this.off(kind, fn);}
    send(data) {assert.equal(this.readyState, 1); this.sent.push(data);}
    open() {this.readyState = 1; this.emit("open", {});}
    message(value) {this.emit("message", {data: JSON.stringify(value)});}
    drop(code = 1006) {this.readyState = 3; this.emit("close", {code, reason: "", wasClean: code === 1000});}
    close(code = 1000, reason = "") {this.closeArgs = [code, reason]; this.drop(code);}
  }
  const timer = {
    setTimeout(fn, delay) {delays.push(delay); timers.set(++nextTimer, fn); return nextTimer;},
    clearTimeout(id) {timers.delete(id);},
  };
  const socket = connectSocket("ws://localhost/ws?view_token=watch", {WebSocketImpl: FakeWebSocket, timer, ...options});
  const tick = () => {
    assert.equal(timers.size, 1, "Only one reconnect may be scheduled");
    const [id, fn] = timers.entries().next().value; timers.delete(id); fn();
  };
  return {socket, sockets, timers, delays, tick, WebSocketImpl: FakeWebSocket, timer};
}

test("abnormal_close_retries_with_capped_backoff_and_open_resets_it", async () => {
  const h = await transport();
  assert.equal(h.socket.readyState, 0);
  h.sockets[0].open(); assert.equal(h.socket.readyState, 1);
  for (const delay of [1000, 2000, 4000, 8000, 10000, 10000]) {
    h.sockets.at(-1).drop();
    assert.equal(h.socket.reconnecting, true); assert.equal(h.socket.readyState, 3);
    assert.equal(h.delays.at(-1), delay); h.tick();
    assert.equal(h.socket.readyState, 0);
    assert.equal(h.sockets.at(-1).url, h.sockets[0].url);
  }
  h.sockets.at(-1).open(); assert.equal(h.socket.reconnecting, false);
  h.sockets.at(-1).drop(1011); assert.equal(h.delays.at(-1), 1000);
  h.socket.close(); assert.equal(h.timers.size, 0);
});

test("listeners_survive_reconnect_once_and_send_uses_the_current_socket", async () => {
  const h = await transport(), messages = [], opens = [], closes = [], errors = [];
  const message = event => messages.push(JSON.parse(event.data));
  h.socket.addEventListener("message", message); h.socket.addEventListener("message", message);
  h.socket.addEventListener("open", () => opens.push(h.socket.reconnecting));
  h.socket.addEventListener("close", event => closes.push([event.code, h.socket.reconnecting]));
  h.socket.addEventListener("error", () => errors.push(h.socket.reconnecting));
  const first = h.sockets[0]; first.open(); first.message({seq: 1}); h.socket.send("first");
  first.emit("error", {}); assert.deepEqual(errors, [true]); assert.equal(h.timers.size, 0);
  first.drop(1011); first.drop(1011); h.tick();
  const second = h.sockets[1]; second.open(); second.message({seq: 2}); h.socket.send("second");
  first.message({seq: 999}); first.emit("open", {}); first.drop();
  assert.deepEqual(messages, [{seq: 1}, {seq: 2}]); assert.deepEqual(opens, [false, false]);
  assert.deepEqual(closes, [[1011, true]]); assert.equal(h.timers.size, 0);
  assert.deepEqual(first.sent, ["first"]); assert.deepEqual(second.sent, ["second"]);
  h.socket.removeEventListener("message", message); second.message({seq: 3});
  assert.equal(messages.length, 2); h.socket.close();
});

test("policy_close_and_explicit_close_never_reconnect", async () => {
  for (const mode of ["policy", "connecting", "open", "waiting"]) {
    const h = await transport(), closed = [];
    h.socket.addEventListener("close", event => closed.push([event.code, h.socket.reconnecting]));
    if (mode !== "connecting") h.sockets[0].open();
    if (mode === "policy") h.sockets[0].drop(1008);
    else {
      if (mode === "waiting") h.sockets[0].drop();
      h.socket.close(1000, "page closed");
    }
    assert.equal(h.socket.reconnecting, false); assert.equal(h.timers.size, 0);
    assert.equal(closed.at(-1)[1], false);
    const count = closed.length; h.socket.close(); h.sockets[0].drop();
    assert.equal(closed.length, count); assert.equal(h.timers.size, 0);
  }
});

test("server_close_1000_retries_unless_the_connection_is_a_nonresumable_replay", async () => {
  for (const replay of [false, true]) {
    const h = await transport({shouldReconnect: () => !replay});
    h.sockets[0].open(); h.sockets[0].drop(1000);
    assert.equal(h.timers.size, replay ? 0 : 1);
    assert.equal(h.socket.reconnecting, !replay); h.socket.close();
  }
});

const flat = node => [node, ...node.children.flatMap(flat)];
class Element {
  constructor(tag, document) {Object.assign(this, {tagName: tag, document, children: [], style: {}, attrs: {}, dataset: {}, listeners: {}, textContent: "", value: "", disabled: false, hidden: false});}
  append(...nodes) {for (const node of nodes) {node.remove(); node.parent = this; this.children.push(node);}}
  replaceChildren(...nodes) {for (const node of this.children) node.parent = null; this.children = []; this.append(...nodes);}
  remove() {if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); this.parent = null;}
  setAttribute(key, value) {this.attrs[key] = String(value);}
  getAttribute(key) {return this.attrs[key] ?? null;}
  removeAttribute(key) {delete this.attrs[key];}
  addEventListener(key, fn) {(this.listeners[key] ||= []).push(fn);}
  removeEventListener(key, fn) {this.listeners[key] = (this.listeners[key] || []).filter(value => value !== fn);}
  focus() {this.document.activeElement = this;}
  click() {if (!this.disabled) for (const fn of this.listeners.click || []) fn();}
}
async function screens() {
  const h = await transport();
  const {setLanguage, connectLanguage} = await load("i18n/index.js"); setLanguage("en");
  const document = {createElement: tag => new Element(tag, document)};
  document.body = new Element("body", document); document.head = new Element("head", document);
  const ids = Object.fromEntries(["world-map", "castle-hud", "screen-host"].map(id => [id, new Element("section", document)]));
  document.body.append(...Object.values(ids)); document.getElementById = id => ids[id] || flat(document.body).find(node => node.id === id);
  const browser = new EventEmitter(); browser.document = document;
  browser.addEventListener = browser.on.bind(browser); browser.removeEventListener = browser.off.bind(browser);
  browser.CustomEvent = class {constructor(type, options) {Object.assign(this, {type, ...options});}};
  browser.dispatchEvent = event => browser.emit(event.type, event);
  const notifications = [];
  browser.specvaniaDesktop = {controlToken: () => "control", notify: (...args) => notifications.push(args)};
  const {mountReplayControls:mountReplay} = await load("hud/chips.js"), {mountScreens} = await load("screens/title.js");
  const replay = mountReplay(h.socket, {browser});
  const title = mountScreens({host: ids["screen-host"], browser, socket: h.socket}); await title.ready;
  const unsubscribe = connectLanguage(h.socket, browser);
  h.sockets[0].open();
  return {...h, document, ids, notifications, setLanguage,
    message: value => h.sockets.at(-1).message(value),
    button: label => flat(document.body).find(node => node.tagName === "button" && node.textContent === label),
    destroy() {replay.destroy(); title.destroy(); unsubscribe(); h.socket.close(); setLanguage("en");}};
}
const request = {request_id: "pending", command: "echo approved", cwd: "/tmp/world"};

test("reconnect_restores_approval_and_language_without_error_screen_or_duplicate_notification", async () => {
  const h = await screens();
  try {
    const snapshot = {kind: "state", phase: "running", state: {status: "running"}, approval_request: request};
    h.message(snapshot); h.button("A · Allow").click();
    h.sockets[0].emit("error", {}); h.sockets[0].drop();
    assert.equal(h.ids["screen-host"].hidden, true); assert.equal(h.button("A · Allow").disabled, true);
    h.tick(); h.sockets[1].open(); h.message(snapshot);
    assert.equal(h.button("A · Allow").disabled, false); h.button("N · Deny").click();h.button("Deny command").click();
    assert.deepEqual(JSON.parse(h.sockets[1].sent.at(-1)), {kind: "approval_answer", request_id: request.request_id, answer: false, control_token: "control"});
    h.message({kind: "studio_state", phase: "running", language: "pt-BR"});
    h.setLanguage("en", {manual: true});
    assert.deepEqual(JSON.parse(h.sockets[1].sent.at(-1)), {kind: "set_language", language: "en", control_token: "control"});
    h.message({...snapshot, approval_request: null});
    assert.equal(h.document.getElementById("approval-choice"), undefined);
    assert.equal(h.notifications.length, 0);
    h.sockets[1].drop(1008); assert.equal(h.ids["screen-host"].dataset.screen, "error");
  } finally {h.destroy();}
});

test("reconnect_restores_an_unanswered_pause_decision_and_clears_a_resolved_pause", async () => {
  const h = await screens();
  try {
    const snapshot = {kind: "state", phase: "running", state: {status: "paused", reason: "out of lives"}};
    h.message(snapshot); h.button("1 · Retry").click(); h.sockets[0].drop();
    h.tick(); h.sockets[1].open(); h.message(snapshot);
    assert.equal(h.button("1 · Retry").disabled, false); h.button("1 · Retry").click();
    assert.deepEqual(JSON.parse(h.sockets[1].sent.at(-1)), {kind: "pause_decision", choice: "retry", control_token: "control"});
    h.message({...snapshot, state: {status: "running"}});
    assert.equal(h.document.getElementById("pause"), undefined);
  } finally {h.destroy();}
});

test("reconnect_restores_the_same_clarify_question_after_a_lost_answer", async () => {
  const h = await screens();
  try {
    const snapshot = {kind: "studio_state", phase: "planning", language: "en",
      clarify_request: {question_id: "question", text: "Which lantern?", options: ["Blue", "Green"], default: "Blue"}};
    h.message(snapshot); h.button("Send answer").click();
    h.sockets[0].drop(); h.tick(); h.sockets[1].open();
    h.message({kind: "state", phase: "planning", state: {status: "running"}}); h.message(snapshot);
    h.button("Send answer").click();
    assert.equal(h.sockets[1].sent.length, 1);
    assert.deepEqual(JSON.parse(h.sockets[1].sent.at(-1)), {kind: "clarify_answer", question_id: "question", answer: "Blue", control_token: "control"});
  } finally {h.destroy();}
});

test("finished_studio_replay_remains_finished_after_reconnect_without_open_replay", async () => {
  const h = await screens();
  try {
    const snapshot = {kind: "state", phase: "replaying", state: {status: "finished"}, replay: {name: "demo", speed: 2, finished: true}};
    h.message(snapshot); h.sockets[0].drop();
    assert.equal(h.button("Back to title").disabled, true);
    h.tick(); h.sockets[1].open(); h.message(snapshot);
    assert.equal(h.button("Pause playback").disabled, true);
    assert.equal(h.button("Back to title").disabled, false);
    assert.ok(!h.sockets.flatMap(socket => socket.sent).map(JSON.parse).some(value => value.kind === "open_replay"));
    assert.equal(h.ids["screen-host"].hidden, true);
  } finally {h.destroy();}
});

test("main_does_not_reconnect_a_cli_replay_from_the_beginning", async () => {
  const h = await transport(); h.socket.close();
  const connection = {}, runState = {};
  const document = {createElement: tag => new Element(tag, document),
    getElementById: id => id === "connection" ? connection : id === "run-state" ? runState : null};
  document.body = new Element("body", document); document.head = new Element("head", document);
  const browser = new EventEmitter(); browser.document = document;
  browser.addEventListener = browser.on.bind(browser); browser.removeEventListener = browser.off.bind(browser);
  browser.dispatchEvent = event => browser.emit(event.type, event);
  browser.location = {href: "http://127.0.0.1:8765/?view_token=watch", search: "?view_token=watch"};
  const previous = {window: global.window, document: global.document, WebSocket: global.WebSocket,
    CustomEvent: global.CustomEvent, setTimeout: global.setTimeout, clearTimeout: global.clearTimeout};
  Object.assign(global, {WebSocket: h.WebSocketImpl, setTimeout: h.timer.setTimeout, clearTimeout: h.timer.clearTimeout,
    window: browser, document,
    CustomEvent: class {constructor(type, options) {Object.assign(this, {type, ...options});}}});
  try {
    await import(`${pathToFileURL(path.join(root, "main.js"))}?cli-replay-transport`);
    const socket = h.sockets.at(-1); socket.open();
    socket.message({kind: "state", replay: {name: "recording", speed: 1}, state: {status: "pending"}});
    socket.message({kind: "event", event: {type: "run_finished", status: "finished", seq: 99}});
    socket.drop();
    assert.equal(h.timers.size, 0); assert.equal(connection.textContent, "Run connection closed");
    assert.equal(runState.textContent, "Event 99: run finished");
  } finally {Object.assign(global, previous);}
});
