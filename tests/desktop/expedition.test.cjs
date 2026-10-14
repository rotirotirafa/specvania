"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const {spawnSync} = require("node:child_process");
const {EventEmitter} = require("node:events");
const {pathToFileURL} = require("node:url");
const repo = path.resolve(__dirname, "../..");
const root = path.join(repo, "src/specvania/ui/static");
const load = name => import(pathToFileURL(path.join(root, name)).href);
const flat = node => [node, ...node.children.flatMap(flat)];
const texts = node => flat(node).map(n => n.textContent || "").join("\n");
class Element {
  constructor(tag, document) {Object.assign(this, {tagName:tag, document, children:[], style:{}, dataset:{}, attrs:{}, listeners:{}, _text:"", value:"", hidden:false, disabled:false});}
  set textContent(value) {this._text=String(value); this.replaceChildren();}
  get textContent() {return this._text;}
  append(...nodes) {for (const node of nodes) {node.remove(); node.parent=this; this.children.push(node);}}
  replaceChildren(...nodes) {for (const node of this.children) node.parent=null; this.children=[]; this.append(...nodes);}
  remove() {if(this.parent) this.parent.children=this.parent.children.filter(n=>n!==this); this.parent=null;}
  setAttribute(key, value) {this.attrs[key]=String(value);}
  getAttribute(key) {return this.attrs[key] ?? null;}
  removeAttribute(key) {delete this.attrs[key];}
  addEventListener(key, fn) {(this.listeners[key] ||= []).push(fn);}
  removeEventListener(key, fn) {this.listeners[key]=(this.listeners[key]||[]).filter(f=>f!==fn);}
  dispatch(key, event={}) {event.target ||= this; for(const fn of this.listeners[key]||[]) fn(event); if(this.parent) this.parent.dispatch(key,event);}
  click() {if(!this.disabled) this.dispatch("click");}
  focus() {this.document.activeElement=this;}
  set innerHTML(_) {assert.fail("Untrusted strings must be text");}
}
async function harness({desktop=true, studio=true, reduced=false, title=false}={}) {
  const {setLanguage} = await load("i18n/index.js"); setLanguage("en");
  const doc={createElement:tag=>new Element(tag,doc)};
  doc.head=doc.createElement("head"); doc.body=doc.createElement("body");
  const game=doc.createElement("div"), hud=doc.createElement("main"), screen=doc.createElement("section"); screen.hidden=true;
  const origin=doc.createElement("button"); hud.append(origin); doc.body.append(game,hud,screen); origin.focus();
  doc.getElementById=id=>({"world-map":game,"castle-hud":hud,"screen-host":screen}[id] || null);
  const browser=new EventEmitter(); browser.addEventListener=browser.on.bind(browser); browser.removeEventListener=browser.off.bind(browser);
  browser.dispatchEvent=e=>browser.emit(e.type,e); browser.document=doc; browser.innerWidth=960; browser.innerHeight=540;
  browser.CustomEvent=class {constructor(type, options) {this.type=type; this.detail=options.detail;}};
  browser.matchMedia=()=>({matches:reduced,addEventListener(){},removeEventListener(){}});
  browser.navigator={language:"en"}; browser.localStorage={getItem:()=>null};
  if(desktop) browser.specvaniaDesktop={controlToken:()=>"control",notify(){},settings:{get:async()=>({reducedMotion:reduced})}};
  const sent=[], socket=new EventEmitter(); socket.readyState=1; socket.addEventListener=socket.on.bind(socket); socket.removeEventListener=socket.off.bind(socket); socket.send=data=>sent.push(JSON.parse(data));
  const mounts=[];
  if(title) {const {mountScreens}=await load("screens/title.js"); const screens=mountScreens({host:screen,browser,socket}); await screens.ready; mounts.push(screens);}
  mounts.push((await load("hud/approval.js")).mountApproval(socket,{window:browser,document:doc}));
  mounts.push((await load("screens/pause.js")).mountPause(socket,{browser}));
  mounts.push((await load("screens/boss.js")).mountBoss(socket,{browser}));
  mounts.push((await load("screens/end.js")).mountEnd(socket,{browser}));
  const message=value=>socket.emit("message",{data:JSON.stringify(value)});
  if(studio) message({kind:"state",phase:"running",state:{run_id:null,status:"pending",rooms:[],models:[]}});
  const button=label=>flat(doc.body).find(n=>n.tagName==="button"&&n.textContent===label);
  const key=(key,shiftKey=false)=>{const e={key,shiftKey,preventDefault(){this.prevented=true;}}; (doc.activeElement||doc.body).dispatch("keydown",e); if(!e.prevented&&key==="Enter") doc.activeElement?.click();};
  return {doc,browser,socket,sent,game,hud,origin,screen,message,key,button,event:event=>message({kind:"event",event}),destroy(){for(const mount of mounts.reverse()) mount.destroy();}};
}
const request={type:"approval_requested",request_id:"a1",command:"printf '<script>literal</script>'",cwd:"/repo/<cwd>"};
function restored(h) {assert.equal(h.game.getAttribute("inert"),null); assert.equal(h.hud.getAttribute("aria-hidden"),null); assert.equal(h.hud.hidden,false); assert.equal(h.doc.activeElement,h.origin);}

test("approval_keyboard_mouse_escape_control_and_focus_restore",async()=>{
  for(const method of ["mouse allow","mouse deny","keyboard allow","escape"]) {
    const h=await harness(); try {
      h.event(request);
      assert.ok(flat(h.doc.body).some(n=>n.dataset.cast==="warrior"));
      assert.ok(texts(h.doc.body).includes(request.command)); assert.ok(texts(h.doc.body).includes(request.cwd));
      assert.equal(h.game.getAttribute("inert"),""); assert.equal(h.doc.activeElement,h.button("Deny"));
      h.key("Tab"); assert.equal(h.doc.activeElement.className,"expedition-content"); h.key("Tab"); assert.equal(h.doc.activeElement,h.button("Allow")); h.key("Tab"); assert.equal(h.doc.activeElement.tagName,"input"); h.key("Tab"); assert.equal(h.doc.activeElement,h.button("Deny")); h.key("Tab",true); assert.equal(h.doc.activeElement.tagName,"input"); h.key("Tab",true); assert.equal(h.doc.activeElement,h.button("Allow")); h.key("Tab",true); assert.equal(h.doc.activeElement.className,"expedition-content"); h.key("Tab",true); assert.equal(h.doc.activeElement,h.button("Deny"));
      if(method.startsWith("mouse")) h.button(method.endsWith("allow")?"Allow":"Deny").click();
      else if(method==="escape") h.key("Escape"); else {h.key("ArrowLeft");h.key("Enter");}
      assert.deepEqual(h.sent,[{kind:"approval_answer",request_id:"a1",answer:method.endsWith("allow"),control_token:"control"}]);
      h.key("Escape"); assert.equal(h.sent.length,1);
      h.event({type:"approval_answered",request_id:"a1",answer:true,responder:"terminal"}); restored(h);
      assert.match(texts(h.hud),/terminal/);
    }finally{h.destroy();}
  }
});

test("pause_and_stamina_keyboard_mouse_send_only_studio_choices",async()=>{
  for(const reason of ["Out of lives in T01: rejected","stamina exhausted"]) for(const method of ["mouse","keyboard"]) for(const choice of reason==="stamina exhausted"?["retry","abort"]:["retry","skip","abort"]) {
    const h=await harness(); try {
      h.event({type:"run_paused",reason,room_id:"T01"});
      const labels=reason==="stamina exhausted"?["Rest","Abort"]:["Retry","Skip","Abort"];
      assert.match(texts(h.doc.body),/The party fell!/);
      if(reason==="stamina exhausted") {assert.equal(h.button("Skip"),undefined); assert.match(texts(h.doc.body),/25%.*original/);}
      const at=choice==="retry"?0:labels.indexOf(choice==="skip"?"Skip":"Abort");
      if(method==="mouse") h.button(labels[at]).click(); else {for(let i=0;i<at;i++) h.key("ArrowRight");h.key("Enter");}
      assert.deepEqual(h.sent,[{kind:"pause_decision",choice,control_token:"control"}]); h.key("Enter"); assert.equal(h.sent.length,1);
      h.event({type:reason==="stamina exhausted"?"stamina_restored":"attempt_started",token_budget:1250,room_id:"T01"}); restored(h);
    }finally{h.destroy();}
  }
});

test("connected_pause_snapshots_preserve_dialog_focus_and_pending_choice_until_reconnect",async()=>{
  const h=await harness();try{
    const snapshot={kind:"state",phase:"running",state:{status:"paused",reason:"out of lives"}};
    const panel=()=>flat(h.doc.body).find(n=>n.id==="pause");
    h.message(snapshot);const original=panel(), retry=h.button("Retry");retry.focus();
    h.message(snapshot);assert.ok(panel()===original);assert.ok(h.doc.activeElement===retry);
    retry.click();assert.equal(h.sent.length,1);
    h.message(snapshot);assert.ok(panel()===original);assert.ok(h.doc.activeElement===retry);
    for(const label of ["Retry","Skip","Abort"]) {assert.equal(h.button(label).disabled,true);h.button(label).click();}
    assert.equal(h.sent.length,1);
    h.socket.readyState=3;h.socket.emit("close");const disconnected=panel();
    h.socket.readyState=1;h.message(snapshot);
    assert.ok(panel()!==original);assert.ok(panel()!==disconnected);
    for(const label of ["Retry","Skip","Abort"]) assert.equal(h.button(label).disabled,false);
    h.button("Retry").click();assert.equal(h.sent.length,2);
    assert.deepEqual(h.sent.at(-1),{kind:"pause_decision",choice:"retry",control_token:"control"});
  }finally{h.destroy();}
});

test("connected_approval_snapshots_preserve_dialog_focus_and_pending_answer_until_reconnect",async()=>{
  const h=await harness();try{
    const snapshot={kind:"state",phase:"running",state:{status:"running"},approval_request:request};
    const panel=()=>flat(h.doc.body).find(n=>n.id==="approval-choice");
    h.message(snapshot);const original=panel(), allow=h.button("Allow");allow.focus();
    h.message(snapshot);assert.ok(panel()===original);assert.ok(h.doc.activeElement===allow);
    allow.click();assert.equal(h.sent.length,1);
    h.message(snapshot);assert.ok(panel()===original);assert.ok(h.doc.activeElement===allow);
    for(const label of ["Allow","Deny"]) {assert.equal(h.button(label).disabled,true);h.button(label).click();}
    assert.equal(h.sent.length,1);
    h.socket.readyState=3;h.socket.emit("close");
    h.socket.readyState=1;h.message(snapshot);assert.ok(panel()!==original);
    for(const label of ["Allow","Deny"]) assert.equal(h.button(label).disabled,false);
    h.button("Allow").click();assert.equal(h.sent.length,2);
    assert.deepEqual(h.sent.at(-1),{kind:"approval_answer",request_id:"a1",answer:true,control_token:"control"});
  }finally{h.destroy();}
});

test("known_pause_reasons_translate_live_unknown_reason_is_literal_text",async()=>{
  const h=await harness(); try {
    const {setLanguage}=await load("i18n/index.js");
    for(const [reason,pattern] of [["out of lives",/vidas/],["Out of lives in T01: feedback",/T01.*feedback/],["warrior model chain exhausted; retry or change the chain.",/modelos.*Guerreiro/],["Boss A1: S1: feedback",/A1.*feedback/],["boss failed",/chefe/],["stamina exhausted",/Stamina/]]) {
      setLanguage("en");h.event({type:"run_paused",reason});setLanguage("pt-BR");assert.match(texts(h.doc.body),pattern);
    }
    const unknown="<img src=x onerror=evil> {unknown}";h.event({type:"run_paused",reason:unknown});assert.ok(texts(h.doc.body).includes(unknown));
  }finally{h.destroy();}
});

test("browser_replay_and_cli_watch_pauses_have_no_control_buttons_or_title",async()=>{
  for(const desktop of [false,true]) {
    const h=await harness({desktop,studio:false,title:true}); try {
      h.event(request); if(!desktop) {assert.equal(h.button("Allow"),undefined);assert.match(texts(h.doc.body),/terminal or desktop window/);} else h.event({type:"approval_answered",request_id:"a1",answer:false,responder:"terminal"});
      h.event({type:"run_paused",reason:"stamina exhausted"}); assert.equal(h.button("Rest"),undefined);assert.equal(h.button("Abort"),undefined);assert.match(texts(h.doc.body),/decided in the terminal/);h.key("Enter");assert.deepEqual(h.sent,[]);assert.equal(h.screen.hidden,true);
      h.event({type:"stamina_restored",token_budget:1250}); restored(h);
    }finally{h.destroy();}
  }
  const h=await harness({desktop:false});try{h.event({type:"run_paused",reason:"out of lives"});assert.equal(h.button("Retry"),undefined);assert.equal(h.sent.length,0);}finally{h.destroy();}
});

test("boss_segments_passed_scenarios_retry_results_and_focus_restore",async()=>{
  const h=await harness();try{
    const signals=[];h.browser.on("specvania:boss-screen",({detail})=>signals.push(detail));
    h.event({type:"boss_started",area_id:"A2",scenarios:["S1","S2","S3"]});
    const segments=()=>flat(h.doc.body).filter(n=>n.dataset.scenario); assert.equal(segments().length,3);
    assert.equal(h.doc.activeElement.dataset.screen,"boss");h.key("Tab");assert.equal(h.doc.activeElement.className,"expedition-content");
    assert.equal(signals.at(-1).area_id,"A2");assert.ok(flat(h.doc.body).some(n=>n.dataset.cast==="red_crab"));
    h.event({type:"boss_scenario",area_id:"A2",scenario:"S1",passed:true});assert.equal(segments().filter(n=>n.dataset.passed==="true").length,1);
    h.event({type:"boss_scenario",area_id:"A2",scenario:"S1",passed:true});
    h.event({type:"boss_scenario",area_id:"A2",scenario:"unknown",passed:true});
    assert.equal(flat(h.doc.body).find(n=>n.getAttribute("role")==="meter").getAttribute("aria-valuenow"),"2");
    h.event({type:"boss_scenario",area_id:"A2",scenario:"S2",passed:false});assert.equal(segments().filter(n=>n.dataset.passed==="true").length,1);
    h.event({type:"boss_scenario",area_id:"A2",scenario:"S3",passed:true});assert.equal(segments().filter(n=>n.dataset.passed==="true").length,2);
    h.event({type:"boss_result",area_id:"A2",passed:false});assert.match(texts(h.doc.body),/escaped/);h.key("Escape");restored(h);assert.equal(signals.at(-1).active,false);
    h.event({type:"boss_started",area_id:"A2",scenarios:["S1"]});h.event({type:"boss_scenario",area_id:"A2",scenario:"S1",passed:true});h.event({type:"boss_result",area_id:"A2",passed:true});assert.match(texts(h.doc.body),/defeated/);h.button("Continue").click();restored(h);assert.deepEqual(h.sent,[]);
  }finally{h.destroy();}
});

function fixture() {
  const prices=[{model:"priced",input_per_mtok:"2.50",output_per_mtok:"5.00",currency:"USD",source_url:"https://example.test/rates",checked_on:"2026-10-13"}];
  const rows=[{type:"run_started",feature:"fixture",branch:"specvania/fixture",prices},
    {type:"world_loaded",rooms:["T01","T02","T03"].map(id=>({id,area:"A1",title:id})),areas:[{id:"A1",title:"Fixture",boss_ids:["S1"]}],doors:[],boss_ids:["S1"]},
    {type:"model_call",role:"warrior",model:"priced",tokens_in:1200,tokens_out:200,status:200,latency:1.25},
    {type:"model_call",role:"paladin",model:"unpriced",tokens_in:300,tokens_out:70,status:429,latency:2.5},
    {type:"fallback",role:"paladin",from_model:"unpriced",to_model:"priced",reason:"429"},
    {type:"fallback",role:"warrior",from_model:"unused",to_model:"priced",reason:"unavailable"},
    {type:"model_call",role:"paladin",model:"priced",tokens_in:50,tokens_out:30,status:200,latency:0.75},
    {type:"room_cleared",room_id:"T01",commit:"abcdef123456"},{type:"room_failed",room_id:"T02",reason:"review failed"},
    {type:"room_skipped",room_id:"T03",reason:"skip"},{type:"run_finished",status:"finished"}];
  return {prices,events:rows.map((row,index)=>({...row,run_id:"fixture",seq:index+1,ts:`2026-10-13T00:00:${String(index).padStart(2,"0")}Z`}))};
}
function pythonSummary(data, directory) {
  const script=`import json, sys\nfrom pathlib import Path\nfrom pydantic import TypeAdapter\nfrom specvania.run.events import Event\nfrom specvania.run.state import fold_events\nfrom specvania.run.summary import summarize, render_summary\nfrom specvania.pricing import Pricing\ndata=json.load(sys.stdin)\nevents=TypeAdapter(list[Event]).validate_python(data['events'])\ntext=render_summary(summarize(events), Pricing.model_validate({'prices':data['prices']}))\nPath(sys.argv[1]).write_text(text)\nprint(fold_events(events).model_dump_json())\n`;
  const result=spawnSync(path.join(repo,".venv/bin/python"),["-c",script,path.join(directory,"summary.md")],{input:JSON.stringify(data),encoding:"utf8",cwd:repo});
  assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout);
}
test("end_screen_matches_python_summary_fixture_events_and_reconnect",async()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),"t36-summary-"));
  try {
    const data=fixture(), state=pythonSummary(data,directory), markdown=fs.readFileSync(path.join(directory,"summary.md"),"utf8");
    for(const requested of [false,true]) for(const snapshot of [false,true]) {
      const events = data.events.flatMap(event => requested && event.type === "model_call"
        ? [{type:"model_requested",role:event.role,model:event.model,run_id:event.run_id,ts:event.ts}, event] : [event])
        .map((event,index) => ({...event,seq:index+1}));
      const replayState = requested ? pythonSummary({...data,events},directory) : state;
      assert.equal(fs.readFileSync(path.join(directory,"summary.md"),"utf8"),markdown);
      const h=await harness({studio:false,title:true});try {
        if(snapshot) h.message({kind:"state",state:replayState}); else for(const event of events) h.event(event);
        const value=id=>flat(h.doc.body).find(n=>n.dataset.metric===id)?.textContent;
        for(const [metric,label] of [["cleared","Rooms cleared"],["calls","Calls"],["tokens_in","Tokens in"],["tokens_out","Tokens out"]]) assert.equal(value(metric),markdown.match(new RegExp(`^${label}: (.+)$`,"m"))[1]);
        for(const metric of ["failed","skipped"]) assert.equal(value(metric),String([...markdown.matchAll(new RegExp(`^\\| T\\d+ \\| \\d+ \\| ${metric} \\|`,"gm"))].length));
        const modelRows=[...markdown.matchAll(/^\| [^|]+ \| \d+ \| \d+ \| \d+ \| \d+ \| (\d+) \| ([\de.+-]+) \| [^|]+ \|$/gm)];
        assert.equal(value("fallbacks"),String(modelRows.reduce((sum,row)=>sum+Number(row[1]),0)));
        assert.equal(value("latency"),String(modelRows.reduce((sum,row)=>sum+Number(row[2]),0)));
        assert.match(markdown,/\| priced \| 2 \| 1250 \| 230 \| 0 \| 0 \| 2 \| 0.004275 USD/);assert.match(texts(h.doc.body),/0.004275 USD/);assert.match(texts(h.doc.body),/https:\/\/example.test\/rates/);
        assert.equal(h.screen.hidden,true);if(snapshot)h.key("Enter");else h.button("See commits").click();assert.ok(h.button("T01 · abcdef1"));assert.equal(h.button("T02"),undefined);
        const selected=[];h.browser.on("specvania:room-selected",e=>selected.push(e.detail.room_id));if(snapshot){assert.equal(h.doc.activeElement,h.button("T01 · abcdef1"));h.key("Enter");}else h.button("T01 · abcdef1").click();assert.deepEqual(selected,["T01"]);restored(h);assert.deepEqual(h.sent,[]);
        h.message({kind:"state",state});if(snapshot){h.key("ArrowRight");h.key("Enter");}else h.button("Back to the castle").click();restored(h);
      }finally{h.destroy();}
    }
  }finally{fs.rmSync(directory,{recursive:true,force:true});}
});

test("boss_arena_camera_uses_castle_geometry_and_integer_zoom_with_motion_preferences",async()=>{
  for(const reduced of [false,true]) {
    const h=await harness({reduced});
    let config;const paints=[], centers=[], keys=new Map();
    const object=(x=0,y=0,key)=>({x,y,key,visible:true,destroy(){this.destroyed=true;},setPosition(x,y){this.x=x;this.y=y;},setVisible(v){this.visible=v;},setAlpha(){},setInteractive(){},on(){}});
    const camera={stopFollow(){},setZoom(zoom){assert.ok(Number.isInteger(zoom));this.zoom=zoom;},setBounds(){},setViewport(){},centerOn(x,y){centers.push([x,y]);},startFollow(){this.following=true;}};
    const scene={textures:{exists:()=>true},cameras:{main:camera},input:{keyboard:{on(type,fn){keys.set(type,fn);}}},children:{bringToTop(){}},
      add:{image(x,y,key){const p=object(x,y,key);paints.push(p);return p;},text:object,zone:object,
      graphics(){return {destroy(){},clear(){},fillStyle(){return this;},fillRect(){return this;}};}}};
    const Phaser={CANVAS:1,Scale:{NONE:0},Game:class {constructor(options){config=options;this.scale={resize(){}};}destroy(){}}};
    const map=(await load("map/scene.js")).mountMap(Phaser,h.game,h.browser);
    try {
      const world=fixture().events.find(e=>e.type==="world_loaded");
      h.browser.dispatchEvent(new h.browser.CustomEvent("specvania:state",{detail:{...world,active_room:"T01",party_room:"T01"}}));config.scene.create.call(scene);
      keys.get("keydown-M")({});assert.equal(h.hud.hidden,true);
      h.event(request);assert.equal(h.hud.hidden,false);
      keys.get("keydown-M")({target:h.doc.activeElement});assert.equal(h.hud.hidden,false);
      h.event({type:"approval_answered",request_id:"a1",answer:false,responder:"terminal"});
      h.browser.dispatchEvent(new h.browser.CustomEvent("specvania:event",{detail:{type:"attempt_started",room_id:"T01"}}));assert.equal(h.hud.hidden,false);
      h.event({type:"boss_started",area_id:"A1",scenarios:["S1"]});
      const layout=(await load("map/layout.js")).layoutWorld((await load("map/layout.js")).worldFromRooms(world.rooms,world.areas));const arena=layout.areas[0].boss;
      assert.deepEqual(centers.at(-1),[arena.x+Math.floor(arena.width/2),arena.y+24]);
      assert.equal(flat(h.doc.body).find(n=>n.dataset.screen==="boss").dataset.transition,String(!reduced));
      h.browser.dispatchEvent(new h.browser.CustomEvent("specvania:settings",{detail:{reducedMotion:true}}));assert.equal(flat(h.doc.body).find(n=>n.dataset.screen==="boss").dataset.transition,"false");
      h.event({type:"boss_result",area_id:"A1",passed:true});h.button("Continue").click();assert.equal(h.hud.hidden,false);assert.ok(camera.following||reduced);
    } finally {map.destroy();h.destroy();}
  }
});

test("reconnect_pause_disconnect_and_dialog_handoffs_do_not_leak_focus_or_messages",async()=>{
  const h=await harness();try{
    h.message({kind:"state",phase:"running",state:{status:"paused",reason:"stamina exhausted"}});assert.ok(h.button("Rest"));
    h.socket.emit("close");assert.equal(h.button("Rest"),undefined);h.key("Enter");assert.deepEqual(h.sent,[]);
    h.event({type:"boss_started",area_id:"A1",scenarios:["S1"]});assert.equal(h.button("Abort"),undefined);
    h.event({type:"run_paused",reason:"boss failed"});assert.match(texts(h.doc.body),/boss fight failed/);assert.equal(flat(h.doc.body).filter(n=>n.getAttribute("role")==="dialog").length,1);
    h.event({type:"run_finished",status:"aborted"});h.key("Tab",true);assert.equal(h.doc.activeElement.className,"expedition-content");h.key("Tab",true);assert.equal(h.doc.activeElement,h.button("Back to the castle"));h.key("Enter");restored(h);
  }finally{h.destroy();}
});

test("end_hides_unsourced_coins_and_ignores_duplicate_events",async()=>{
  const h=await harness();try{
    const data=fixture();data.events[0].prices=data.prices.map(({source_url,...price})=>price);
    for(const event of data.events.slice(0,-1)) {h.event(event);h.event(event);}
    h.event(data.events.at(-1));
    assert.doesNotMatch(texts(h.doc.body),/coins|example.test/);
    assert.equal(flat(h.doc.body).find(n=>n.dataset.metric==="calls").textContent,"3");
    const {setLanguage}=await load("i18n/index.js");setLanguage("pt-BR");assert.ok(h.button("Voltar ao castelo"));assert.match(texts(h.doc.body),/Tokens de entrada/);
    h.key("Escape");restored(h);
  }finally{h.destroy();}
});

test("end_coins_keep_decimal_precision_including_exponent_rates",async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"t36-coins-"));
  try {
    const data=fixture();data.prices[0].input_per_mtok="1E-7";data.prices[0].output_per_mtok="2.3456789";
    const state=pythonSummary(data,dir);const md=fs.readFileSync(path.join(dir,"summary.md"),"utf8");
    const expected=md.match(/\| priced \|.*\| ([\d.]+) USD;/)[1];
    const h=await harness();try{h.message({kind:"state",state});assert.ok(texts(h.doc.body).includes(`${expected} USD`));}finally{h.destroy();}
  }finally{fs.rmSync(dir,{recursive:true,force:true});}
});

test("long_dialog_content_is_keyboard_scrollable_without_leaving_the_trap",async()=>{
  const h=await harness();try{
    h.event({...request,command:"long command\n".repeat(200)});
    h.key("Tab");const content=h.doc.activeElement;
    assert.equal(content.className,"expedition-content");assert.equal(content.tabIndex,0);
    const event={key:"ArrowDown",preventDefault(){this.prevented=true;}};content.dispatch("keydown",event);assert.equal(event.prevented,undefined,"Arrows scroll content using the browser default");
    h.key("Tab");assert.equal(h.doc.activeElement,h.button("Allow"));h.key("Tab");assert.equal(h.doc.activeElement.tagName,"input");h.key("Tab");assert.equal(h.doc.activeElement,h.button("Deny"));
    h.event({type:"approval_answered",request_id:"a1",answer:false,responder:"terminal"});restored(h);
  }finally{h.destroy();}
});
