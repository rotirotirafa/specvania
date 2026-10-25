"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {pathToFileURL} = require("node:url");
const path = require("node:path");
const vm = require("node:vm");
const {EventEmitter} = require("node:events");
const {harness, flat, text, load} = require("./play-dom.cjs");
const root = path.resolve(__dirname,"../..");
const token = "desktop-control-secret";
const mount = desktop => harness({desktop:desktop || false});
const request = {type:"approval_requested",request_id:"approval-1",command:"printf '<script>safe</script>'",cwd:"/tmp/world"};
const buttons = h => flat(h.box).filter(n=>n.tagName==="button"&&!n.hidden);

test("rpg_choice_shows_command_cwd_palette_font_and_sends_one_authenticated_answer",async()=>{
  const {UI_COLORS,PALETTE}=await load("pixel/palette.js");
  for(const answer of [true,false]) {const h=await mount({controlToken:()=>token});try{
    h.event(request);assert.equal(h.box.getAttribute("role"),"status");assert.equal(h.box.getAttribute("aria-live"),"polite");
    assert.equal(h.box.style.backgroundColor,UI_COLORS.surface);assert.equal(h.box.style.borderColor,PALETTE.Y);assert.match(h.box.style.fontFamily,/Press Start/);
    assert.ok(text(h.box).includes(request.command));assert.ok(text(h.box).includes(request.cwd));assert.equal(h.document.getElementById("approval-choice"),null);
    assert.equal(h.document.activeElement,h.button("A · Allow"));
    h.button(answer?"A · Allow":"N · Deny").click();if(!answer)h.button("Deny command").click();
    assert.deepEqual(h.sent,[{kind:"approval_answer",request_id:request.request_id,answer,control_token:token}]);
    h.key("A");assert.equal(h.sent.length,1);h.event({type:"approval_answered",request_id:request.request_id,answer,responder:"terminal"});
    assert.equal(buttons(h).length,0);assert.equal(h.document.activeElement,h.document.getElementById("world-map"));assert.ok(!text(h.body).includes(token));
  }finally{h.destroy();}}
});
test("terminal_answer_dismisses_only_matching_choice_and_socket_close_disables_answers",async()=>{
 const h=await mount({controlToken:()=>token});try{h.event(request);h.event({type:"approval_answered",request_id:"other",answer:true,responder:"terminal"});assert.equal(h.box.dataset.turn,"approval");
 h.event({type:"approval_answered",request_id:request.request_id,answer:false,responder:"terminal"});assert.equal(h.box.dataset.turn,"");assert.match(text(h.box),/denied in terminal/);
 h.event({...request,request_id:"next"});h.close();h.key("A");assert.equal(h.sent.length,0);assert.equal(h.button("A · Allow").disabled,true);
 }finally{h.destroy();}
});
test("browser_read_only_for_browser_and_desktop_without_control_token",async()=>{
 for(const desktop of [false,{controlToken:()=>""}]){const h=await harness({desktop});try{h.event(request);assert.ok(h.button("A · Allow").disabled);h.key("A");h.key("N");assert.equal(h.sent.length,0);assert.match(text(h.box),/terminal or desktop window/);h.key("V");assert.ok(h.document.getElementById("command-overlay"));h.key("Escape");assert.equal(h.document.getElementById("command-overlay"),null);}finally{h.destroy();}}
});
test("notification_renderer_filters_exactly_three_event_types_and_failed_review",async()=>{
 const notices=[],h=await mount({controlToken:()=>token,notify:(...args)=>notices.push(args)});try{
 h.event(request);h.event({type:"review",verdict:"fail",room_id:"T20",feedback:"Gate failed"});h.event({type:"boss_result",area_id:"A5",passed:true});h.event({type:"boss_result",area_id:"A5",passed:false});
 assert.deepEqual(notices.map(n=>n[0]),["Approval requested","Paladin review failed","Boss result","Boss result"]);
 for(const type of ["review","approval_answered","boss_started","boss_scenario","room_cleared","cooldown","fallback","run_paused"])h.event({type,verdict:"pass",request_id:"other"});assert.equal(notices.length,4);
 }finally{h.destroy();}
});
test("rpg_choice_restores_pending_snapshot_and_uses_full_transient_prompt",async()=>{
 const h=await mount({controlToken:()=>token});try{const full={...request,command:"printf "+"x".repeat(500)};h.message({kind:"state",state:{seq:1},approval_request:full});assert.ok(text(h.box).includes(full.command));assert.equal(h.notifications.length,0);
 h.key("V");assert.ok(text(h.document.getElementById("command-overlay")).includes(full.command));h.key("Escape");
 h.event({type:"approval_answered",request_id:request.request_id,answer:false,responder:"terminal"});assert.equal(buttons(h).length,0);
 h.message({kind:"event",event:{...request,command:"summary"},approval_request:full});assert.ok(text(h.box).includes(full.command));
 }finally{h.destroy();}
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

test("denial_reason_is_labelled_single_line_bounded_trimmed_and_sent_only_on_deny",async()=>{
 for(const [answer,value,reason] of [[false,"  Fix script  ","Fix script"],[false,"  ",undefined],[true,"ignored",undefined],[false,"x".repeat(200),"x".repeat(200)]]){
 const h=await mount({controlToken:()=>token});try{h.event(request);h.key("N");const input=h.input;assert.equal(input.type,"text");assert.equal(input.maxLength,200);assert.equal(h.document.activeElement,input);
 const label=flat(h.box).find(n=>n.tagName==="label");assert.equal(label.htmlFor,input.id);assert.equal(label.textContent,"Reason for denial (optional)");input.value=value;
 h.button(answer?"A · Allow":"Deny command").click();const expected={kind:"approval_answer",request_id:request.request_id,answer,control_token:token};if(reason)expected.reason=reason;assert.deepEqual(h.sent,[expected]);assert.equal(input.disabled,true);
 }finally{h.destroy();}}
});
test("denial_reason_is_in_dialog_tab_order_and_preserves_text_cursor_keys",async()=>{
 const h=await mount({controlToken:()=>token});try{h.event(request);h.key("N");assert.equal(h.input.hidden,false);assert.equal(h.button("Deny command").hidden,false);h.input.value="Keep draft";
 for(const key of ["ArrowLeft","ArrowRight","A","N","V","M","1","?"]){const event=h.key(key,h.input);assert.ok(!event.defaultPrevented);assert.equal(h.document.activeElement,h.input);}assert.equal(h.sent.length,0);
 h.key("Enter",h.input);assert.equal(h.sent[0].reason,"Keep draft");
 }finally{h.destroy();}
});
test("denial_reason_resolved_status_and_replay_dialogue_translate_and_render_as_text",async()=>{
 const h=await mount({controlToken:()=>token}),{setLanguage}=await load("i18n/index.js"),{dialogueFor}=await load("hud/dialogue.js");const event={type:"approval_answered",request_id:request.request_id,answer:false,responder:"desktop window",reason:"<script>corrupted</script>"};
 try{h.event(request);h.event(event);for(const [language,expected] of [["en","denied in desktop window: <script>corrupted</script>"],["pt-BR","negado em janela desktop: <script>corrupted</script>"]]){setLanguage(language);assert.equal(dialogueFor(event).text,expected);assert.ok(text(h.box).includes(expected));assert.ok(!flat(h.body).some(n=>n.tagName==="script"));}}finally{setLanguage("en");h.destroy();}
});
test("rejected_denial_reenables_controls_preserves_reason_and_allows_correction",async()=>{
 const h=await mount({controlToken:()=>token});try{h.event(request);h.key("N");const input=h.input;input.value="mistyped-secret";h.button("Deny command").click();assert.equal(input.disabled,true);
 h.message({kind:"approval_rejected",reason:"invalid message"});assert.equal(h.input,input);assert.equal(input.disabled,false);assert.equal(input.value,"mistyped-secret");assert.equal(h.box.dataset.turn,"approval");
 input.value="Fix script";h.button("Deny command").click();assert.equal(h.sent[1].reason,"Fix script");h.message({kind:"approval_rejected",reason:"invalid message"});input.value="";h.button("Deny command").click();assert.ok(!Object.hasOwn(h.sent[2],"reason"));h.close();h.message({kind:"approval_rejected",reason:"unavailable"});assert.equal(input.disabled,true);
 }finally{h.destroy();}
});
