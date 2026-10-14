"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {EventEmitter} = require("node:events");
const root = path.resolve(__dirname, "../../src/specvania/ui/static");
let imports = 0;
const flat = node => [node, ...node.children.flatMap(flat)];
class Element {
  constructor(tagName) {Object.assign(this, {tagName, children: [], style: {}, dataset: {}, attrs: {}});}
  append(...nodes) {this.children.push(...nodes); for (const node of nodes) node.parent = this;}
  setAttribute(key, value) {this.attrs[key] = String(value);}
  addEventListener() {}
  focus() {}
  remove() {if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this);}
}

async function main() {
  const connection = {}, runState = {}, sockets = [], states = [], events = [], studio = [], messages = [];
  const document = {createElement: tag => new Element(tag), body: new Element("body"),
    getElementById: id => id === "connection" ? connection : id === "run-state" ? runState : null};
  const browser = new EventEmitter(); browser.document = document;
  browser.addEventListener = browser.on.bind(browser); browser.removeEventListener = browser.off.bind(browser);
  browser.dispatchEvent = event => browser.emit(event.type, event);
  browser.location = {href: "http://127.0.0.1:8765/?view_token=watch", search: "?view_token=watch"};
  class Socket extends EventEmitter {
    constructor() {super(); this.readyState = 1; sockets.push(this);}
    addEventListener(type, fn) {this.on(type, fn);}
    removeEventListener(type, fn) {this.off(type, fn);}
    send() {}
    close() {this.readyState = 3;}
  }
  const previous = {window: global.window, document: global.document, WebSocket: global.WebSocket, CustomEvent: global.CustomEvent};
  Object.assign(global, {window: browser, document, WebSocket: Socket,
    CustomEvent: class {constructor(type, options) {Object.assign(this, {type, ...options});}}});
  browser.on("specvania:state", ({detail}) => states.push(detail));
  browser.on("specvania:event", ({detail}) => events.push(detail));
  browser.on("specvania:studio-state", ({detail}) => studio.push(detail));
  try {
    await import(`${pathToFileURL(path.join(root, "main.js"))}?state-order-${++imports}`);
  } catch (error) {Object.assign(global, previous); throw error;}
  const socket = sockets[0];
  socket.on("message", ({data}) => messages.push(JSON.parse(data)));
  return {states, events, studio, messages, document,
    message: value => socket.emit("message", {data: JSON.stringify(value)}),
    destroy() {socket.close(); Object.assign(global, previous);}};
}
const state = (run_id, seq, extra = {}) => ({kind: "state", state: {run_id, seq, status: "running"}, ...extra});
const event = (run_id, seq) => ({kind: "event", event: {run_id, seq, type: "clarify_answer"}});

test("main_stale_live_snapshot_preserves_newer_events_and_studio_approval_payload", async () => {
  const h = await main();
  try {
    h.message(state("plan", 4)); h.message(event("plan", 5));
    const stale = state("plan", 4, {phase: "planning", approval_request: {request_id: "pending", command: "echo ready", cwd: "/tmp/world"}});
    h.message(stale);
    assert.deepEqual(h.states.map(value => value.seq), [4]);
    assert.deepEqual(h.events.map(value => value.seq), [5]);
    assert.deepEqual(h.studio, [stale]); assert.deepEqual(h.messages.at(-1), stale);
    assert.ok(flat(h.document.body).some(node => node.tagName === "pre" && node.textContent.includes("echo ready")));
    h.message(state("plan", 5)); h.message(state("plan", 6)); h.message(state("plan", 5));
    assert.deepEqual(h.states.map(value => value.seq), [4, 5, 6]);
  } finally {h.destroy();}
});

test("main_live_sequences_are_tracked_per_run_across_run_changes", async () => {
  const h = await main();
  try {
    h.message(event("plan", 20)); h.message(state("run", 1)); h.message(event("run", 2));
    h.message(state("plan", 19)); h.message(state("run", 1)); h.message(state("run", 2));
    assert.deepEqual(h.states.map(value => [value.run_id, value.seq]), [["run", 1], ["run", 2]]);
  } finally {h.destroy();}
});

test("main_replay_seek_and_restart_allow_backward_states_and_events", async () => {
  const h = await main();
  try {
    h.message(event("recording", 30));
    for (const extra of [{replay: {name: "recording", speed: 1}}, {phase: "replaying", replay: {name: "demo", speed: 2}}]) {
      h.message(state("recording", 10, extra)); h.message(event("recording", 50));
      h.message(state("recording", 0, extra)); h.message(event("recording", 1));
    }
    h.message(state("live", 1)); h.message(event("live", 2)); h.message(state("live", 1));
    assert.deepEqual(h.states.map(value => value.seq), [10, 0, 10, 0, 1]);
    assert.deepEqual(h.events.map(value => value.seq), [30, 50, 1, 50, 1, 2]);
  } finally {h.destroy();}
});
