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
  browser.innerWidth = 960; browser.innerHeight = 540;
  const observers = [];
  browser.ResizeObserver = class {
    constructor(callback) {this.callback = callback; observers.push(this);}
    observe(node) {this.node = node;}
    disconnect() {this.node = null;}
  };
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
  const {mountReplayControls:mountReplay} = await load("hud/chips.js");
  let screens;
  const replay = mountReplay(socket, {browser, back: () => screens.showTitle()});
  const {mountScreens} = await load("screens/title.js"); screens = mountScreens({host:ids["screen-host"], browser, socket}); await screens.ready;
  const message = value => socket.emit("message", {data:JSON.stringify(value)});
  if (studio) message({kind:"state", phase:"idle", state:{rooms:[]}});
  const button = label => flat(document.body).find(node => node.tagName === "button" && node.textContent === label);
  const key = (key, target = document.activeElement, shiftKey = false) => {
    const event = {key, target, shiftKey, prevented: false, stopped: false,
      preventDefault() {this.prevented = true;}, stopPropagation() {this.stopped = true;}};
    browser.emit("keydown", event);
    for (let node = target; node && !event.stopped; node = node.parent) node.dispatch("keydown", event);
    if (!event.prevented && key === "Enter" && target?.tagName === "button") target.click();
  };
  return {browser, document, ids, socket, sent, notifications, observers, message, button, key, destroy() {replay.destroy(); screens.destroy();}};
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

test("recorded_mage_approval_pause_boss_and_end_use_same_readonly_screens_in_order",async()=>{
 const h=await harness();try{begin(h);const observed=[];for(const [stream,events]of [["plan",fixture("plan")],["run",fixture("run")]]){
   if(stream==="run")h.message({kind:"state",phase:"replaying",replay:{...status,stream},state:{rooms:[]}});
   for(const event of events){h.message({kind:"event",event});const box=h.document.getElementById("dialogue-box");assert.ok(box);assert.equal(box.dataset.turn,"");assert.equal(box.getAttribute("role"),"status");assert.equal(flat(box).some(n=>n.tagName==="button"||n.tagName==="textarea"),false);observed.push(text(box));
     if(event.type==="clarify_question"){assert.match(text(box),/Which lantern/);assert.ok(flat(box).some(n=>n.dataset.cast==="mage"));}
     if(event.type==="clarify_answer")assert.match(text(box),/Recorded answer: Green/);
     if(event.type==="approval_requested"){assert.match(text(box),/echo synthetic-lantern/);assert.equal(h.document.getElementById("approval-choice"),undefined);}
     if(event.type==="approval_answered")assert.match(text(box),/approved/);
     if(event.type==="run_paused"){assert.equal(h.document.getElementById("pause"),undefined);assert.match(text(box),/paused/);}
     if(event.type==="attempt_started"&&event.attempt===2)assert.match(text(box),/Recorded choice: Retry/);
     if(event.type==="boss_started"){assert.ok(h.document.getElementById("boss"));await Promise.resolve();assert.equal(h.document.getElementById("boss").dataset.transition,"false");}
     if(event.type==="boss_scenario")assert.equal(flat(h.document.body).find(n=>n.attrs.role==="meter").attrs["aria-valuenow"],"0");
     if(event.type==="run_finished")assert.ok(h.document.getElementById("end"));
   }
 }assert.deepEqual(h.sent.filter(v=>v.kind!=="set_language"),[]);assert.deepEqual(h.notifications,[]);assert.ok(observed.some(v=>v.includes("Recorded answer: Green")));
 }finally{h.destroy();}

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
    assert.ok(h.document.getElementById("dialogue-box"));assert.equal(h.document.getElementById("dialogue-box").dataset.turn,"");
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

test("end_heading_bounds_clear_measured_replay_bar_at_960x540_and_controls_are_keyboard_reachable", async () => {
  const h = await harness();
  try {
    const {setLanguage} = await load("i18n/index.js"); setLanguage("pt-BR");
    const bar = h.document.getElementById("replay-bar");
    let barBottom = 118;
    bar.getBoundingClientRect = () => ({left: 12, right: 940, top: 32, bottom: barBottom});
    // The code panel stays mounted offscreen, but is outside the replay dialogs.
    const codePanel = h.document.createElement("section"), codeButton = h.document.createElement("button");
    codePanel.setAttribute("role", "dialog"); codePanel.style.top = "0px"; codePanel.style.visibility = "hidden";
    codePanel.append(codeButton); h.document.body.append(codePanel);
    begin(h);
    for (const event of fixture("run")) h.message({kind:"event", event});
    const end = h.document.getElementById("end");
    const heading = flat(end).find(node => node.tagName === "h2");
    assert.equal(heading.textContent, "Resumo da expedição");
    // The DOM harness has no CSS engine. Model the heading's box using the
    // shipped end inset, panel padding/border and heading margin/font size.
    const css = fs.readFileSync(path.join(root, "index.html"), "utf8");
    const inset = Number(css.match(/\.expedition-dialog\[data-screen=end\] \{ inset: (\d+)px/)[1]);
    const padding = Number(css.match(/\.expedition-dialog \{[^}]*padding: (\d+)px/)[1]);
    const headingMargin = Number(css.match(/\.expedition-content h2 \{[^}]*margin: (\d+)px/)[1]);
    const headingSize = Number(css.match(/\.expedition-content h2 \{ font-size: (\d+)px/)[1]);
    heading.getBoundingClientRect = () => {
      const top = (parseFloat(end.style.top) || inset) + padding + parseFloat(end.style.border) + headingMargin;
      return {left: 44, right: 916, top, bottom: top + headingSize * parseFloat(end.style.lineHeight)};
    };
    const assertBounds = () => {
      const a = heading.getBoundingClientRect(), b = bar.getBoundingClientRect();
      assert.ok(a.bottom <= b.top || a.top >= b.bottom || a.right <= b.left || a.left >= b.right);
      assert.ok(a.bottom < h.browser.innerHeight, "Heading remains inside the viewport");
      assert.equal(bar.hidden, false);
      assert.equal(end.getAttribute("aria-modal"), "false", "The watch view also exposes playback controls");
    };
    assertBounds();
    const observer = h.observers.find(value => value.node === bar); assert.ok(observer);
    barBottom = 164; observer.callback(); assertBounds(); // Wrapped receipt or translated controls.
    barBottom = 142; h.browser.emit("resize"); assertBounds();
    const controls = bar.children.filter(node => node.tagName === "button");
    const reached = new Set();
    for (let i = 0; i < 12; i++) {h.key("Tab"); reached.add(h.document.activeElement);}
    assert.equal(reached.has(codeButton), false); assert.equal(codePanel.style.top, "0px");
    for (const button of controls) {
      assert.ok(reached.has(button), `${button.textContent} is reachable from the end dialog with Tab`);
      assert.equal(button.disabled, false);
    }
    const endBack = flat(end).filter(node => node.tagName === "button").at(-1);
    controls[0].focus(); h.key("Tab", controls[0], true); assert.equal(h.document.activeElement, endBack);
    endBack.focus(); h.key("Tab"); assert.equal(h.document.activeElement, controls[0]);
    h.key("Enter"); assert.equal(h.sent.at(-1).kind, "replay_speed");
    controls[3].focus(); h.key("Enter"); assert.equal(h.sent.at(-1).kind, "replay_pause");
    h.message({kind:"studio_state", phase:"replaying", replay:{...status, finished:true}});
    assert.equal(controls[3].disabled, true);
    controls[2].focus(); h.key("Tab"); assert.equal(h.document.activeElement, controls[4]);
    h.key("Enter"); assert.equal(h.sent.at(-1).kind, "close_replay");
    h.message({kind:"state", phase:"idle", state:{rooms:[]}});
    assert.equal(bar.hidden, true);
    assert.equal(end.style.top, "");
    assert.equal(end.getAttribute("aria-modal"), "true");
  } finally {h.destroy();}
  assert.ok(h.observers.every(observer => observer.node === null));
  assert.equal(h.browser.listenerCount("resize"), 0);
});

test("recorded_dialogs_reserve_bar_space_after_open_resize_and_language_change",async()=>{
 const h=await harness();try{const bar=h.document.getElementById("replay-bar");let bottom=120;bar.getBoundingClientRect=()=>({bottom});begin(h);
   for(const event of [{type:"clarify_question",question_id:"q",question:"Which?",options:["Blue"],default:"Blue"},{type:"approval_requested",request_id:"a",command:"echo demo",cwd:"/demo"},{type:"run_paused",reason:"out of lives"}]){
    h.message({kind:"event",event});const box=h.document.getElementById("dialogue-box");assert.equal(box.style.bottom,"12px");assert.equal(box.style.height,"auto");assert.ok(Number(box.dataset.height)>=104&&Number(box.dataset.height)<=124);assert.equal(box.dataset.turn,"");assert.ok(!flat(box).some(n=>n.tagName==="button"));
    assert.equal(h.document.getElementById("approval-choice"),undefined);assert.equal(h.document.getElementById("pause"),undefined);
   }
   h.message({kind:"event",event:{type:"boss_started",area_id:"A1",scenarios:["S1"]}});const boss=h.document.getElementById("boss");assert.ok(parseFloat(boss.style.top)>bottom);bottom=160;h.browser.emit("resize");assert.ok(parseFloat(boss.style.top)>bottom);
   const {setLanguage}=await load("i18n/index.js");bottom=180;setLanguage("pt-BR");assert.ok(parseFloat(boss.style.top)>bottom);setLanguage("en");
 }finally{h.destroy();}

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

test("recorded_denial_reason_appears_in_receipt_and_reconnect_in_both_languages", async () => {
  const h = await harness();
  const {setLanguage} = await load("i18n/index.js");
  const answer = {type:"approval_answered", request_id:"denial", answer:false, responder:"desktop window", reason:"Syntax is corrupted"};
  try {
    begin(h);
    h.message({kind:"event",event:answer});
    const bar = h.document.getElementById("replay-bar");
    assert.match(text(bar), /denied in desktop window: Syntax is corrupted/);
    setLanguage("pt-BR");
    assert.match(text(bar), /negado em janela desktop: Syntax is corrupted/);
    h.message({kind:"state",phase:"replaying",replay:{...status,dialogue:answer},state:{rooms:[]}});
    assert.match(text(bar), /negado em janela desktop: Syntax is corrupted/);
    assert.equal(h.sent.filter(message => message.kind === "approval_answer").length, 0);
  } finally {setLanguage("en"); h.destroy();}
});

test("long_recorded_choice_stays_single_line_with_accessible_full_text_and_nonwrapping_controls_on_end_screen", async () => {
  const h = await harness(), {setLanguage} = await load("i18n/index.js");
  try {
    begin(h);
    const reason = "Choose retry, skip or abort after examining the chamber. ".repeat(12);
    h.message({kind:"event", event:{type:"run_paused", reason}});
    h.message({kind:"event", event:{type:"room_skipped", room_id:"T01"}});
    h.message({kind:"event", event:fixture("run").at(-1)});
    assert.ok(h.document.getElementById("end"));
    const bar = h.document.getElementById("replay-bar"), summary = bar.children[0];
    for (const [width, height] of [[960, 540], [1280, 800]]) for (const language of ["en", "pt-BR"]) {
      h.browser.innerWidth = width; h.browser.innerHeight = height; h.browser.emit("resize"); setLanguage(language);
      assert.match(summary.textContent, language === "en" ? /Recorded choice: Skip/ : /Escolha gravada: Pular/);
      assert.ok(summary.textContent.includes(reason));
      assert.equal(summary.title, summary.textContent); assert.equal(summary.getAttribute("aria-label"), summary.textContent);
      assert.equal(summary.getAttribute("role"), "status");
      assert.equal(summary.style.whiteSpace, "nowrap"); assert.equal(summary.style.overflow, "hidden");
      assert.equal(summary.style.textOverflow, "ellipsis"); assert.equal(summary.style.minWidth, "0");
      assert.equal(bar.style.flexWrap, "nowrap"); assert.equal(bar.style.height, "32px");
      for (const button of bar.children.filter(node => node.tagName === "button")) {
        assert.equal(button.style.whiteSpace, "nowrap"); assert.equal(button.style.flexShrink, "0");
      }
    }
  } finally {setLanguage("en"); h.destroy();}
});
