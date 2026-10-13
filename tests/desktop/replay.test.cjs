"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {EventEmitter} = require("node:events");
const root = path.resolve(__dirname, "../../src/specvania/ui/static");
const load = name => import(pathToFileURL(path.join(root, name)));
const flat = node => [node, ...node.children.flatMap(flat)];
const text = node => flat(node).map(node => node.textContent).join("\n");
class Element {
  constructor(tag, document) {Object.assign(this, {tagName:tag, document, children:[], style:{}, attrs:{}, dataset:{}, listeners:{}, textContent:"", disabled:false, hidden:false});}
  append(...nodes) {for (const node of nodes) {node.remove(); node.parent = this; this.children.push(node);}}
  replaceChildren(...nodes) {for (const node of this.children) node.parent = null; this.children = []; this.append(...nodes);}
  remove() {if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); this.parent = null;}
  setAttribute(key, value) {this.attrs[key] = String(value);}
  getAttribute(key) {return this.attrs[key] ?? null;}
  removeAttribute(key) {delete this.attrs[key];}
  addEventListener(key, fn) {(this.listeners[key] ||= []).push(fn);}
  removeEventListener(key, fn) {this.listeners[key] = (this.listeners[key] || []).filter(value => value !== fn);}
  dispatch(key, event = {}) {for (const fn of this.listeners[key] || []) fn(event);}
  focus() {this.document.activeElement = this;}
  click() {if (!this.disabled) this.dispatch("click");}
  set innerHTML(_) {assert.fail("Recorded text must be inert");}
}
async function harness(desktop = true, studio = true) {
  const {setLanguage} = await load("i18n/index.js"); setLanguage("en");
  const document = {createElement: tag => new Element(tag, document)};
  document.body = new Element("body", document); document.head = new Element("head", document);
  const ids = Object.fromEntries(["world-map", "castle-hud", "screen-host"].map(id => [id, new Element("section", document)]));
  document.body.append(...Object.values(ids)); document.getElementById = id => ids[id] || flat(document.body).find(node => node.id === id);
  const browser = new EventEmitter(); browser.document = document;
  browser.addEventListener = browser.on.bind(browser); browser.removeEventListener = browser.off.bind(browser);
  browser.CustomEvent = class {constructor(type, options) {Object.assign(this, {type, ...options});}};
  browser.dispatchEvent = event => browser.emit(event.type, event);
  const notifications = [];
  if (desktop) Object.defineProperty(browser, "specvaniaDesktop", {
    value: Object.freeze({controlToken: () => "control", notify: (...args) => notifications.push(args), settings: {get: async () => ({reducedMotion:true})}}),
    writable: false, configurable: false,
  });
  const socket = new EventEmitter(), sent = [];
  socket.addEventListener = socket.on.bind(socket); socket.removeEventListener = socket.off.bind(socket);
  socket.readyState = 1; socket.send = value => sent.push(JSON.parse(value));
  const {mountReplay} = await load("screens/replay.js");
  let screens;
  const replay = mountReplay(socket, {browser, back: () => screens.showTitle()});
  const {mountScreens} = await load("screens/title.js"); screens = mountScreens({host:ids["screen-host"], browser, socket}); await screens.ready;
  const message = value => socket.emit("message", {data:JSON.stringify(value)});
  if (studio) message({kind:"state", phase:"idle", state:{rooms:[]}});
  const button = label => flat(document.body).find(node => node.tagName === "button" && node.textContent === label);
  const key = (key, target = document.activeElement) => {
    browser.emit("keydown", {key, target, preventDefault(){}});
    if (key === "Enter" && target?.tagName === "button") target.click();
  };
  return {browser, document, ids, socket, sent, notifications, message, button, key, destroy() {replay.destroy(); screens.destroy();}};
}
const status = {name:"demo", feature:"synthetic-lantern", speed:1, paused:false, finished:false, stream:"plan"};
const begin = h => h.message({kind:"state", phase:"replaying", replay:status, state:{rooms:[],status:"pending"}});
const fixture = name => fs.readFileSync(path.resolve(__dirname, `../fixtures/replay-two-stream/${name}.jsonl`), "utf8").trim().split("\n").map(JSON.parse);

test("watch_demo_enabled_on_title_and_missing_key_error_sends_only_open_replay", async () => {
  const h = await harness(); try {
    assert.equal(h.button("Watch demo").disabled, false); h.button("Watch demo").focus(); h.key("Enter");
    assert.deepEqual(h.sent.at(-1), {kind:"open_replay",control_token:"control"});
    assert.equal(h.ids["screen-host"].hidden, true);
    h.message({kind:"approval_rejected",reason:"API key missing"});
    // The title's error screen remains reachable with a missing key.
    h.message({kind:"state",phase:"idle",state:{rooms:[]}});
    h.button("Open project")?.click();
    h.message({kind:"approval_rejected",reason:"API key missing"});
    assert.equal(h.button("Watch demo").disabled, false);
  } finally {h.destroy();}
});

test("recorded_mage_approval_pause_boss_and_end_use_same_readonly_screens_in_order", async () => {
  const h = await harness(); try {
    begin(h); await Promise.resolve(); assert.match(text(h.document.body), /Recorded world: synthetic lantern/);
    assert.ok(flat(h.document.body).some(node => node.dataset.cast === "mage"));
    const plan = fixture("plan"), run = fixture("run");
    const observed = [];
    for (const event of [...plan, ...run]) {
      if (event === run[0]) h.message({kind:"state",phase:"replaying",replay:{...status,stream:"run"},state:{rooms:[]}});
      h.message({kind:"event", event}); observed.push(text(h.document.body));
      if (event.type === "clarify_question") {
        assert.match(observed.at(-1), /Which lantern\?/); assert.match(observed.at(-1), /Blue/); assert.match(observed.at(-1), /Green/);
        assert.equal(flat(h.document.body).filter(node => node.tagName === "textarea").length, 0);
      }
      if (event.type === "clarify_answer") assert.match(observed.at(-1), /Recorded answer: Green/);
      if (event.type === "world_loaded") assert.equal(h.document.getElementById("replay-dialogue"), undefined);
      if (event.type === "approval_requested") {
        const panel = h.document.getElementById("approval-choice"); assert.ok(panel); assert.match(text(panel), /echo synthetic-lantern/);
        assert.match(text(panel), /Recorded dialogue/);
        assert.ok(flat(panel).find(node => /answer in the terminal/i.test(node.textContent)).hidden);
        assert.equal(h.button("Allow"), undefined); assert.equal(h.button("Deny"), undefined);
        panel.dispatch("keydown", {key:"Escape",preventDefault(){}});
      }
      if (event.type === "approval_answered") {assert.equal(h.document.getElementById("approval-choice"), undefined); assert.match(observed.at(-1), /approved/);}
      if (event.type === "run_paused") {
        assert.ok(h.document.getElementById("pause")); assert.equal(h.button("Retry"), undefined); assert.equal(h.button("Abort"), undefined);
        h.message({kind:"studio_state",phase:"replaying",replay:{...status,stream:"run",speed:4,paused:true},pause_request:null});
        assert.ok(h.document.getElementById("pause"), "Playback controls keep the recorded pause open until its recorded choice");
        h.message({kind:"studio_state",phase:"replaying",replay:{...status,stream:"run",speed:2,paused:false},pause_request:null});
      }
      if (event.type === "attempt_started" && event.attempt === 2) {assert.equal(h.document.getElementById("pause"), undefined); assert.match(observed.at(-1), /Recorded choice: Retry/);}
      if (event.type === "boss_started") {assert.ok(h.document.getElementById("boss")); assert.equal(h.document.getElementById("boss").dataset.transition, "false", "Replay respects the desktop reduced-motion setting");}
      if (event.type === "boss_scenario") assert.equal(flat(h.document.body).find(node => node.attrs.role === "meter").attrs["aria-valuenow"], "0");
      if (event.type === "run_finished") {assert.ok(h.document.getElementById("end")); assert.match(text(h.document.getElementById("end")), /1/);}
    }
    assert.deepEqual(h.sent.filter(value => value.kind !== "set_language"), []);
    assert.deepEqual(h.notifications, []);
    assert.ok(observed[1].includes("Which lantern?")); assert.ok(observed[2].includes("Recorded answer: Green"));
    assert.ok(observed[8].includes("echo synthetic-lantern")); assert.ok(observed[9].includes("approved"));
  } finally {h.destroy();}
});

test("speed_and_pause_controls_mouse_keyboard_modal_escape_and_back_to_title", async () => {
  const h = await harness(); try {
    begin(h);
    for (const speed of [1, 2, 4]) {h.button(`${speed}×`).click(); assert.equal(h.sent.at(-1).speed, speed); h.key(String(speed)); assert.equal(h.sent.at(-1).speed, speed);}
    h.button("Pause playback").click(); assert.equal(h.sent.at(-1).paused, true);
    h.message({kind:"studio_state",phase:"replaying",replay:{...status,speed:4,paused:true}});
    h.button("Resume playback").click(); assert.equal(h.sent.at(-1).paused, false);
    h.key("p"); assert.equal(h.sent.at(-1).paused, false);
    h.message({kind:"event",event:fixture("run")[4]});
    assert.ok(h.document.getElementById("approval-choice"));
    h.key("2"); assert.equal(h.sent.at(-1).speed, 2);
    h.button("Back to title").click(); assert.equal(h.sent.at(-1).kind, "close_replay");
    h.key("Escape"); assert.equal(h.sent.at(-1).kind, "close_replay");
    h.message({kind:"state",phase:"idle",replay:null,state:{rooms:[]}});
    assert.equal(h.document.getElementById("approval-choice"), undefined);
    assert.equal(h.document.getElementById("replay-bar").hidden, true);
    assert.equal(h.ids["screen-host"].dataset.screen, "title");
    assert.equal(h.ids["screen-host"].hidden, false);
    assert.equal(h.ids["world-map"].getAttribute("inert"), "");
  } finally {h.destroy();}
});

test("reconnect_restores_recorded_answers_and_language_changes_without_reload", async () => {
  const h = await harness(); try {
    const answer = fixture("plan")[2];
    h.message({kind:"state",phase:"replaying",replay:{...status,dialogue:answer},state:{rooms:[]}});
    assert.match(text(h.document.body), /Recorded answer: Green/);
    const {setLanguage} = await load("i18n/index.js"); setLanguage("pt-BR");
    assert.match(text(h.document.body), /Resposta gravada: Green/);
    assert.ok(h.button("Pausar reprodução")); assert.ok(h.button("Voltar ao título"));
    assert.equal(h.sent.filter(message => message.kind === "clarify_answer").length, 0);
    setLanguage("en");
  } finally {h.destroy();}
});

test("browser_cli_replay_renders_recorded_dialogues_without_control_or_notifications", async () => {
  for (const desktop of [false, true]) {
  const h = await harness(desktop, false); try {
    h.message({kind:"state",replay:status,state:{rooms:[]}});
    for (const event of [...fixture("plan"), ...fixture("run")]) h.message({kind:"event",event});
    assert.ok(h.document.getElementById("end")); assert.equal(h.button("1×").disabled,true);
    h.key("p"); h.key("2"); h.key("Escape"); assert.deepEqual(h.sent, []);
  } finally {h.destroy();}
  }
});


for (const result of ["aborted", "finished"]) {
  test(`immutable_contextBridge_property_replay_is_readonly_and_shows_${result}_end_screen`, async () => {
    const h = await harness();
    try {
      const descriptor = Object.getOwnPropertyDescriptor(h.browser, "specvaniaDesktop");
      assert.equal(descriptor.writable, false);
      assert.equal(descriptor.configurable, false);
      const original = h.browser.specvaniaDesktop;
      assert.doesNotThrow(() => begin(h));
      await Promise.resolve();
      for (const event of fixture("plan")) assert.doesNotThrow(() => h.message({kind:"event", event}));
      assert.doesNotThrow(() => h.message({kind:"state",phase:"replaying",replay:{...status,stream:"run"},state:{rooms:[]}}));
      const events = result === "aborted"
        ? fs.readFileSync(path.resolve(root, "../../examples/demo/events.jsonl"), "utf8").trim().split("\n").map(JSON.parse)
        : fixture("run");
      assert.equal(events.at(-1).type, "run_finished");
      assert.equal(events.at(-1).status, result);
      for (const event of events) {
        assert.doesNotThrow(() => h.message({kind:"event", event}));
        if (event.type === "approval_requested") {assert.equal(h.button("Allow"), undefined); assert.equal(h.button("Deny"), undefined);}
        if (event.type === "run_paused") {assert.equal(h.button("Retry"), undefined); assert.equal(h.button("Abort"), undefined);}
      }
      const end = h.document.getElementById("end");
      assert.ok(end, "T36 end screen appears for a recorded run_finished");
      assert.match(text(end), new RegExp(result));
      const cleared = flat(end).find(node => node.dataset.metric === "cleared");
      assert.equal(cleared.textContent, String(events.filter(event => event.type === "room_cleared").length));
      assert.doesNotThrow(() => h.message({kind:"studio_state",phase:"replaying",replay:{...status,stream:"run",finished:true}}));
      assert.equal(h.document.getElementById("end"), end, "Playback completion keeps the summary visible");
      assert.deepEqual(h.notifications, []);
      assert.deepEqual(h.sent.filter(message => message.kind !== "set_language"), []);
      assert.equal(h.browser.specvaniaDesktop, original, "The real contextBridge capability is preserved");
      h.button("4×").click();
      assert.deepEqual(h.sent.at(-1), {kind:"replay_speed",speed:4,control_token:"control"});
      h.button("Back to title").click();
      assert.doesNotThrow(() => h.message({kind:"state",phase:"idle",state:{rooms:[]}}));
      assert.equal(h.document.getElementById("end"), undefined);
      assert.equal(h.ids["screen-host"].dataset.screen, "title");
    } finally {h.destroy();}
  });
}
