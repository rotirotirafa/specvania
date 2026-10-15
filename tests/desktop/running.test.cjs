"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const load = file => import(pathToFileURL(path.resolve(__dirname, "../../src/specvania/ui/static", file)));
const flat = node => [node, ...node.children.flatMap(flat)];
const text = node => flat(node).map(node => node.textContent).join("\n");
class Element {
  constructor(tag) {Object.assign(this, {tagName: tag, children: [], style: {}, dataset: {}, attrs: {}, listeners: {}, textContent: ""});}
  append(...nodes) {for (const node of nodes) {node.parent = this; this.children.push(node);}}
  replaceChildren(...nodes) {this.children = []; this.append(...nodes);}
  remove() {if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this);}
  setAttribute(key, value) {this.attrs[key] = String(value);}
  addEventListener(key, fn) {(this.listeners[key] ||= []).push(fn);}
  removeEventListener(key, fn) {this.listeners[key] = (this.listeners[key] || []).filter(item => item !== fn);}
  focus() {}
}
function harness(reduced = false) {
  let time = Date.parse("2026-10-15T12:00:00Z"), next = 0;
  const handlers = new Map(), intervals = new Map(), frames = new Map(), mediaHandlers = new Set();
  const document = {createElement: tag => new Element(tag)};
  const media = {matches: reduced, addEventListener(_, fn) {mediaHandlers.add(fn);}, removeEventListener(_, fn) {mediaHandlers.delete(fn);}};
  const browser = {document, matchMedia: () => media,
    addEventListener(key, fn) {const list = handlers.get(key) || []; list.push(fn); handlers.set(key, list);},
    removeEventListener(key, fn) {handlers.set(key, (handlers.get(key) || []).filter(item => item !== fn));},
    setInterval(fn, ms) {const id = ++next; intervals.set(id, {fn, ms}); return id;}, clearInterval(id) {intervals.delete(id);},
    requestAnimationFrame(fn) {frames.set(++next, fn); return next;}, cancelAnimationFrame(id) {frames.delete(id);}};
  const host = new Element("main");
  return {host, browser, intervals, frames, mediaHandlers, timing: {clock: {now: () => time}, timer: browser},
    ts: (offset = 0) => new Date(time + offset).toISOString(),
    emit(key, detail) {for (const fn of handlers.get(key) || []) fn({detail});},
    advance(ms) {time += ms; for (const {fn} of [...intervals.values()]) fn(); const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn());},
    reduce(value) {media.matches = value; for (const fn of mediaHandlers) fn();},
    listeners: () => [...handlers.values()].reduce((sum, list) => sum + list.length, 0) + mediaHandlers.size};
}
async function party(reduced) {
  const {mountParty} = await load("hud/party.js");
  const {setLanguage} = await load("i18n/index.js"); setLanguage("en");
  const h = harness(reduced); h.mount = mountParty(h.host, h.browser, h.timing);
  h.event = event => h.emit("specvania:event", {ts: h.ts(), ...event});
  h.row = role => flat(h.host).find(node => node.dataset.role === role);
  return h;
}
test("party_dots_cycle_every_500ms_and_seconds_start_at_event_timestamp", async () => {
  const h = await party(); try {
    h.event({type: "model_requested", role: "warrior", ts: h.ts(-2000)});
    assert.match(text(h.row("warrior")), /thinking\.\. 2 s/);
    h.advance(500); assert.match(text(h.row("warrior")), /thinking\.\.\. 2 s/);
    h.advance(500); assert.match(text(h.row("warrior")), /thinking\. 3 s/);
    h.advance(500); assert.match(text(h.row("warrior")), /thinking\.\. 3 s/);
    const {UI_COLORS} = await load("pixel/palette.js");
    assert.equal(h.row("warrior").style.backgroundColor, UI_COLORS.background);
    assert.equal(h.row("mage").style.backgroundColor, "");
    assert.equal(h.intervals.size, 1);
  } finally {h.mount.destroy();}
});
test("party_every_new_wait_resets_elapsed_and_leaving_thinking_removes_indicator", async () => {
  const h = await party(); try {
    for (const event of [{type: "room_entered"}, {type: "attempt_started"}, {type: "respawn"},
      {type: "tool_refused"}, {type: "model_requested", role: "warrior"}, {type: "fallback", role: "warrior", to_model: "w2"},
      {type: "model_call", role: "warrior"}, {type: "gate_result"}, {type: "boss_started"}, {type: "model_requested", role: "paladin"}]) {
      h.advance(12000); h.event(event);
      assert.match(text(h.row(["gate_result", "boss_started"].includes(event.type) || event.role === "paladin" ? "paladin" : "warrior")), /thinking\. 0 s/);
    }
    h.event({type: "review"}); assert.doesNotMatch(text(h.host), /thinking[.…]/); assert.equal(h.intervals.size, 0);
    h.event({type: "model_requested", role: "warrior"}); h.event({type: "action", action: "read_file"});
    assert.doesNotMatch(text(h.row("warrior")), /thinking/); assert.equal(h.row("warrior").style.backgroundColor, "");
    h.event({type: "model_requested", role: "mage"}); h.event({type: "clarify_question"});
    assert.doesNotMatch(text(h.row("mage")), /thinking/);
  } finally {h.mount.destroy();}
});
test("party_snapshot_restores_wait_from_when_learned_and_cooldown_expiry_starts_wait", async () => {
  const h = await party(); try {
    h.emit("specvania:state", {party: [{role: "mage", status: "thinking"}]});
    assert.match(text(h.row("mage")), /thinking\. 0 s/); h.advance(12000);
    assert.match(text(h.row("mage")), /thinking\. 12 s/);
    h.emit("specvania:state", {party: []}); assert.equal(h.intervals.size, 0);
    h.event({type: "cooldown", role: "paladin", seconds: 2}); h.advance(3000);
    assert.match(text(h.row("paladin")), /thinking\.\.\. 1 s/);
    h.advance(1000); assert.match(text(h.row("paladin")), /thinking\.\. 2 s/);
  } finally {h.mount.destroy();}
});
test("party_reduced_motion_setting_and_media_keep_static_dots_and_localized_seconds", async () => {
  const h = await party(true); try {
    h.event({type: "model_requested", role: "warrior"}); h.advance(500);
    assert.match(text(h.row("warrior")), /thinking… 0 s/);
    h.advance(1000); assert.match(text(h.row("warrior")), /thinking… 1 s/);
    h.reduce(false); h.emit("specvania:settings", {reducedMotion: true}); h.advance(500);
    assert.match(text(h.row("warrior")), /thinking… 2 s/);
    const {setLanguage} = await load("i18n/index.js"); h.advance(1232000);
    assert.match(text(h.row("warrior")), /thinking… 1,234 s/);
    setLanguage("pt-BR"); assert.match(text(h.row("warrior")), /pensando… 1\.234 s/);
  } finally {h.mount.destroy();}
});
test("party_watch_demo_uses_recorded_timestamps_speed_and_pause", async () => {
  const h = await party(); try {
    h.emit("specvania:state", {replay: {speed: 4}});
    h.event({type: "model_requested", role: "warrior", ts: "2024-01-01T00:00:00Z"});
    h.advance(500); assert.match(text(h.row("warrior")), /thinking\.\. 2 s/);
    h.event({type: "bug_opened", ts: "2024-01-01T00:00:12Z"});
    assert.match(text(h.row("warrior")), /thinking\. 12 s/);
    h.emit("specvania:studio-state", {replay: {speed: 4, paused: true}});
    h.advance(10000); assert.match(text(h.row("warrior")), /thinking\. 12 s/);
    h.emit("specvania:studio-state", {replay: {speed: 2}});
    h.advance(1000); assert.match(text(h.row("warrior")), /thinking\.\. 14 s/);
  } finally {h.mount.destroy();}
});
test("party_destroy_cleans_tickers_frames_and_event_and_motion_listeners", async () => {
  const h = await party(); h.event({type: "model_requested", role: "warrior"});
  assert.equal(h.intervals.size, 1); h.mount.destroy();
  assert.equal(h.intervals.size, 0); assert.equal(h.frames.size, 0); assert.equal(h.listeners(), 0);
  h.advance(1000); assert.equal(h.host.children.length, 0);
});
test("clarify_working_text_animates_resets_for_mage_wait_and_cleans_up", async () => {
  const {ideaScreen} = await load("screens/idea.js"); const {t, setLanguage} = await load("i18n/index.js"); setLanguage("en");
  const h = harness(); const ui = {browser: h.browser, document: h.browser.document, t, palette: {}, dialogueLayout() {},
    label(node, key) {node.textContent = t(key);}, element(tag) {const node = new Element(tag); h.host.append(node); return node;}};
  const screen = ideaScreen(ui, {phase: "planning"}, h.timing);
  try {
    assert.match(text(h.host), /world\. 0 s/); h.advance(500); assert.match(text(h.host), /world\.\. 0 s/);
    h.advance(500); assert.match(text(h.host), /world\.\.\. 1 s/);
    screen.update({phase: "planning"}); h.advance(1000); assert.match(text(h.host), /world\.\. 2 s/);
    h.emit("specvania:event", {type: "model_requested", role: "mage", ts: h.ts(-5000)});
    assert.match(text(h.host), /world\.\. 5 s/);
    h.emit("specvania:settings", {reducedMotion: true}); h.advance(1000); assert.match(text(h.host), /world… 6 s/);
    setLanguage("pt-BR"); screen.render(); assert.match(text(h.host), /mundo… 6 s/);
    screen.update({phase: "planning", clarify_request: {question_id: "q1", text: "Choose", options: [], default: "A"}});
    assert.equal(h.intervals.size, 0); assert.doesNotMatch(text(h.host), /trabalhando/);
    screen.update({phase: "planning"}); assert.match(text(h.host), /mundo… 0 s/);
    screen.update({phase: "world_ready", world_slug: "world"}); assert.equal(h.intervals.size, 0);
    screen.update({phase: "planning"}); assert.equal(h.intervals.size, 1);
  } finally {screen.destroy();}
  assert.equal(h.intervals.size, 0); assert.equal(h.listeners(), 0);
});
test("dialogue_finish_is_human_and_localized_and_empty_args_omit_colon", async () => {
  const {dialogueFor} = await load("hud/dialogue.js"); const {setLanguage} = await load("i18n/index.js");
  for (const [language, finished] of [["en", "Room finished."], ["pt-BR", "Terminei a sala."]]) {
    setLanguage(language);
    assert.equal(dialogueFor({type: "action", action: "finish", args_summary: ""}).text, finished);
    assert.equal(dialogueFor({type: "action", action: "finish", thought: "Done", args_summary: ""}).text, `Done\n${finished}`);
    for (const args_summary of ["", "   ", undefined]) assert.equal(dialogueFor({type: "action", action: "read_file", args_summary}).text, "read_file");
    assert.equal(dialogueFor({type: "action", action: "read_file", args_summary: "src/main.py"}).text, "read_file: src/main.py");
  }
});

test("party_repeated_snapshots_preserve_known_wait_until_a_new_run", async () => {
  const h = await party(); try {
    h.event({type: "run_started", run_id: "run-1"});
    h.event({type: "model_requested", role: "warrior", model: "w1", ts: h.ts(-12000)});
    h.emit("specvania:state", {run_id: "run-1", party: [{role: "warrior", model: "w1", status: "thinking"}]});
    assert.match(text(h.row("warrior")), /thinking\. 12 s/);
    h.advance(3000);
    h.emit("specvania:state", {run_id: "run-1", party: [{role: "warrior", model: "w1", status: "thinking"}]});
    assert.match(text(h.row("warrior")), /thinking\. 15 s/);
    h.emit("specvania:state", {run_id: "run-2", party: [{role: "warrior", model: "w1", status: "thinking"}]});
    assert.match(text(h.row("warrior")), /thinking\. 0 s/);
  } finally {h.mount.destroy();}
});
test("party_replay_snapshot_without_wait_event_keeps_elapsed_when_first_recorded_event_arrives", async () => {
  const h = await party(); try {
    h.emit("specvania:state", {replay: {speed: 2}, party: [{role: "mage", status: "thinking"}]});
    h.advance(3000); assert.match(text(h.row("mage")), /thinking\. 6 s/);
    h.event({type: "bug_opened", ts: "2024-01-01T00:00:12Z"});
    assert.match(text(h.row("mage")), /thinking\. 6 s/);
    h.advance(1000); assert.match(text(h.row("mage")), /thinking\.\. 8 s/);
  } finally {h.mount.destroy();}
});
