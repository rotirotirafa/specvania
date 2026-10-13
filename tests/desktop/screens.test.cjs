"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const vm = require("node:vm");
const {EventEmitter} = require("node:events");
const {pathToFileURL} = require("node:url");
const root = path.resolve(__dirname,"../../src/specvania/ui/static");
const desktop = path.resolve(__dirname,"../../apps/desktop");
const load = file => import(pathToFileURL(path.join(root,file)).href);
const flat = node => [node,...node.children.flatMap(flat)];
const text = node => flat(node).map(n=>n.textContent).join("\n");
class Element {
  constructor(tag,document) { Object.assign(this,{tagName:tag,document,children:[],style:{},attrs:{},dataset:{},listeners:{},textContent:"",value:"",disabled:false}); }
  append(...nodes) { for(const node of nodes) {node.parent=this;this.children.push(node);} }
  replaceChildren(...nodes) {this.children.forEach(n=>n.parent=null);this.children=[];this.append(...nodes);}
  setAttribute(key,value) {this.attrs[key]=String(value);}
  getAttribute(key) {return this.attrs[key] ?? null;}
  removeAttribute(key) {delete this.attrs[key];}
  addEventListener(key,fn) {(this.listeners[key] ||= []).push(fn);}
  removeEventListener(key,fn) {this.listeners[key]=(this.listeners[key]||[]).filter(f=>f!==fn);}
  dispatch(type,event={}) {for(const fn of this.listeners[type]||[]) fn(event);}
  click() {if(!this.disabled) this.dispatch("click");}
  focus() {this.document.activeElement=this;}
  set innerHTML(_) {assert.fail("Use inert text nodes");}
  remove() {if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);}
}
async function harness() {
  const {setLanguage} = await load("i18n/index.js"); setLanguage("en");
  const doc = {createElement:tag=>new Element(tag,doc)};
  doc.head=new Element("head",doc);doc.body=new Element("body",doc);
  const host=new Element("section",doc);
  const game=new Element("div",doc),hud=new Element("main",doc);
  const summary=new Element("summary",doc);summary.textContent="Rooms and states";hud.append(summary);
  for(const label of ["HUD PT","HUD EN"]) {const button=new Element("button",doc);button.textContent=label;hud.append(button);}
  doc.body.append(game,host,hud);doc.getElementById=id=>id==="world-map"?game:id==="castle-hud"?hud:null;
  const events=new Map(),sent=[],writes=[],motion=[],restarts=[];let saved={language:"en",tokenBudget:1200,specvaniaPath:"specvania",reducedMotion:false},folder="/repo";
  const browser={document:doc,innerWidth:960,innerHeight:540,navigator:{language:"en"},CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}},
    addEventListener(type,fn){const a=events.get(type)||[];a.push(fn);events.set(type,a);},removeEventListener(type,fn){events.set(type,(events.get(type)||[]).filter(f=>f!==fn));},
    dispatchEvent(event){if(event.type==="specvania:settings")motion.push(event.detail);for(const fn of events.get(event.type)||[])fn(event);},
    specvaniaDesktop:{isDesktop:true,controlToken:()=>"control",pickFolder:async()=>folder,settings:{get:async()=>({...saved}),set:async value=>{writes.push(value);saved={...saved,...value};return {...saved};}},studio:{get:async()=>({kind:"ready"}),restart:async()=>{restarts.push(true);}}}};
  const socket=new EventEmitter();socket.readyState=1;socket.addEventListener=socket.on.bind(socket);socket.removeEventListener=socket.off.bind(socket);socket.send=data=>sent.push(JSON.parse(data));
  const {mountScreens}=await load("screens/title.js");const screens=mountScreens({host,browser,socket}); await screens.ready;
  const message=value=>socket.emit("message",{data:JSON.stringify(value)});
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  function key(key,shiftKey=false) {const event={key,shiftKey,target:doc.activeElement,preventDefault(){this.prevented=true;}};host.dispatch("keydown",event);
    // Simulate the browser's default focus traversal and button activation.
    if(!event.prevented && key==="Tab") {const nodes=flat(doc.body).filter(n=>["button","input","select","summary"].includes(n.tagName)&&!n.disabled&&!function inert(node){return node.attrs.inert!==undefined||node.hidden||node.parent&&inert(node.parent);}(n));const at=nodes.indexOf(doc.activeElement);nodes[(at+(shiftKey?-1:1)+nodes.length)%nodes.length]?.focus();}
    if(!event.prevented && ["Enter"," "].includes(key) && doc.activeElement?.tagName==="button")doc.activeElement.click();}
  return {host,doc,game,hud,browser,socket,screens,sent,writes,motion,restarts,message,key,tick,folder:value=>{folder=value;},button:label=>flat(host).find(n=>n.tagName==="button"&&n.textContent===label)};
}
test("keyboard_only_title_project_settings_error_navigation_and_mouse",async()=>{
  const h=await harness();try {
    assert.equal(h.doc.activeElement.textContent,"New project");h.key("Enter");await h.tick();
    assert.deepEqual(h.sent.at(-1),{kind:"open_project",path:"/repo",control_token:"control"});
    h.message({kind:"studio_state",phase:"project_open",language:"en",project:{path:"/repo",initialized:false},worlds:[],open_bugs:0});
    assert.equal(h.doc.activeElement.textContent,"Initialize project");h.key(" ");assert.equal(h.sent.at(-1).kind,"init_project");
    h.key("Escape");assert.equal(h.doc.activeElement.textContent,"New project");
    h.key("Tab");assert.equal(h.doc.activeElement.textContent,"Open project");h.key("Tab",true);assert.equal(h.doc.activeElement.textContent,"New project");
    // Reach Settings solely by Tab, skipping disabled Watch demo.
    for(let i=0;i<2;i++)h.key("Tab");assert.equal(h.doc.activeElement.textContent,"Settings");h.key("Enter");
    assert.match(text(h.host),/takes effect on the next app start/);
    const controls=flat(h.host).filter(n=>["input","select"].includes(n.tagName));
    controls[0].value="pt-BR";controls[0].dispatch("change");await h.tick();assert.match(text(h.host),/Configurações/);
    controls[1].value="2345";controls[1].dispatch("change");await h.tick();
    controls[2].value="/new/specvania";controls[2].dispatch("change");await h.tick();
    controls[3].checked=true;controls[3].dispatch("change");await h.tick();
    assert.equal(h.writes.at(-1).reducedMotion,true);assert.equal(h.motion.at(-1).reducedMotion,true);
    assert.equal(h.sent.filter(n=>n.kind==="set_language").at(-1).language,"pt-BR");
    h.screens.startRun("world",true);assert.deepEqual(h.sent.at(-1),{kind:"start_run",slug:"world",allow_dirty:true,token_budget:2345,control_token:"control"});
    h.key("Escape");h.button("EN").click();await h.tick();h.button("Open project").click();await h.tick();
    h.message({kind:"approval_rejected",reason:"API key missing; check the configured environment variable."});
    assert.match(text(h.host),/NVIDIA_API_KEY/);assert.match(text(h.host),/~\/.zshrc/);assert.match(text(h.host),/restart the app/);
    assert.ok(h.button("Watch demo").disabled);assert.match(h.button("Watch demo").title,/demo/);
    h.key("Escape");assert.ok(h.button("New project"));
    h.socket.emit("close");assert.match(text(h.host),/Connection lost/);h.key("Escape");
    h.screens.showError({reasonKey:"error.startFailed",logPath:"/safe/studio.log"});assert.match(text(h.host),/\/safe\/studio.log/);h.key("Escape");
    assert.ok(h.button("Restart studio"));assert.equal(h.button("Watch demo").disabled,true);
    assert.match(text(h.doc.head),/:focus-visible/);
  } finally {h.screens.destroy();}
});
test("outside_git_reason_init_worlds_bugs_and_probe_results",async()=>{
  const h=await harness();try {
    h.button("Open project").click();await h.tick();h.message({kind:"approval_rejected",reason:"Choose a folder inside a git work tree."});
    assert.match(text(h.host),/git work tree/);assert.match(text(h.host),/Choose a folder/);
    h.message({kind:"studio_state",phase:"project_open",project:{path:"/repo",initialized:false},worlds:[],open_bugs:0});assert.ok(h.button("Initialize project"));
    h.message({kind:"studio_state",phase:"project_open",project:{path:"/repo",initialized:true},worlds:["hello-world","other-world"],open_bugs:2});
    assert.match(text(h.host),/hello-world/);assert.match(text(h.host),/other-world/);assert.match(text(h.host),/2 open bugs/);
    h.button("Check the party").click();assert.deepEqual(h.sent.at(-1),{kind:"probe_party",control_token:"control"});assert.ok(h.button("Check the party").disabled);
    h.message({kind:"probe_result",results:[{model:"ok/model",status:"ok",latency_ms:12.5},{model:"bad/model",status:404,latency_ms:null},{model:"slow/model",status:"timeout",latency_ms:null}]});
    assert.match(text(h.host),/12.5 ms/);assert.match(text(h.host),/HTTP 404/);assert.match(text(h.host),/Timeout/);
    const rows=flat(h.host).filter(n=>n.dataset.probe);assert.equal(rows.length,3);assert.notEqual(rows[0].style.color,rows[1].style.color);assert.equal(rows[1].style.color,rows[2].style.color);
    h.folder(null);const count=h.sent.length;h.button("Choose folder").click();await h.tick();assert.equal(h.sent.length,count);
  } finally {h.screens.destroy();}
});
function preload() {
  let api;const calls=[];
  vm.runInNewContext(fs.readFileSync(path.join(desktop,"preload.js"),"utf8"),{require:()=>({contextBridge:{exposeInMainWorld:(_,value)=>{api=value;}},ipcRenderer:{sendSync:()=>"control",send:(...a)=>calls.push(a),invoke:async(...a)=>{calls.push(a);return a;}}})});
  return {api,calls};
}
test("preload_exact_capabilities_use_narrow_ipc",async()=>{
  const {api,calls}=preload();assert.deepEqual(Object.keys(api).sort(),["controlToken","isDesktop","notify","pickFolder","settings","studio"]);
  await api.pickFolder();await api.settings.get();await api.settings.set({tokenBudget:10});await api.studio.get();await api.studio.restart();
  assert.deepEqual(calls.map(c=>c[0]),["specvania:pick-folder","specvania:settings-get","specvania:settings-set","specvania:studio","specvania:studio-restart"]);
});
async function main(directory) {
  const app=new EventEmitter();app.getPath=()=>directory;app.whenReady=()=>Promise.resolve();app.quit=()=>{};app.exit=()=>assert.fail("main failed");
  const ipcMain=new EventEmitter();const handlers=new Map();ipcMain.handle=(key,fn)=>handlers.set(key,fn);
  let window;const dialogs=[];
  class BrowserWindow {constructor(){window=this;this.webContents=new EventEmitter();this.webContents.setWindowOpenHandler=()=>{};}setAspectRatio(){}loadURL(url){this.url=url;return Promise.resolve();}}
  vm.runInNewContext(fs.readFileSync(path.join(desktop,"main.js"),"utf8"),{__dirname:desktop,URL,process:{argv:["electron",".","--url","http://127.0.0.1:8765/","--control-token","control"]},console,
    require:name=>name==="electron"?{app,BrowserWindow,ipcMain,Notification:{},shell:{},dialog:{showOpenDialog:async(...args)=>{dialogs.push(args);return {canceled:false,filePaths:["/chosen"]};}}}:require(name)});
  await new Promise(resolve=>setImmediate(resolve));
  return {handlers,dialogs,trusted:{sender:window.webContents,senderFrame:{url:window.url}},foreign:{sender:window.webContents,senderFrame:{url:"https://evil.invalid"}}};
}
test("settings_survive_restart_are_validated_and_ipc_rejects_foreign_origins",async()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),"sv-t33-"));
  try {
    const h=await main(directory);const get=h.handlers.get("specvania:settings-get"),set=h.handlers.get("specvania:settings-set");
    const value={language:"pt-BR",tokenBudget:4567,specvaniaPath:"/a path/specvania",reducedMotion:true};
    assert.deepEqual(JSON.parse(JSON.stringify(await set(h.trusted,value))),value);
    for(const bad of [{tokenBudget:0},{tokenBudget:-1},{tokenBudget:1.5},{tokenBudget:"10"},{tokenBudget:Number.MAX_SAFE_INTEGER+1},{language:"fr"},{reducedMotion:1},{specvaniaPath:"bad\0path"},{specvaniaPath:""},{unknown:true},null,[]]) {
      await assert.rejects(async()=>set(h.trusted,bad));assert.deepEqual(JSON.parse(JSON.stringify(await get(h.trusted))),value);
    }
    for(const key of ["specvania:settings-get","specvania:settings-set","specvania:pick-folder","specvania:studio","specvania:studio-restart"]) {
      for(const event of [h.foreign,{sender:{},senderFrame:h.trusted.senderFrame}]) await assert.rejects(async()=>h.handlers.get(key)(event,value));
    }
    assert.equal(h.dialogs.length,0);assert.equal(await h.handlers.get("specvania:pick-folder")(h.trusted),"/chosen");assert.deepEqual(Array.from(h.dialogs[0][1].properties),["openDirectory"]);
    const restarted=await main(directory);assert.deepEqual(JSON.parse(JSON.stringify(await restarted.handlers.get("specvania:settings-get")(restarted.trusted))),value);
    assert.equal(require("../../apps/desktop/studio.cjs").readSettings(directory),"/a path/specvania");
  } finally {fs.rmSync(directory,{recursive:true,force:true});}
});

test("launcher_error_page_executes_shared_translated_screens_and_preserves_log_path",async()=>{
  const h=await harness();h.screens.destroy();
  const page=require("../../apps/desktop/studio.cjs").errorPage("error.startFailed","/safe/<path>/studio.log");
  const background = (await load("pixel/palette.js")).UI_COLORS.background;
  assert.ok(page.includes(`background:${background}`));assert.match(page, /-webkit-app-region:drag/);
  assert.match(page, /img-src data:/);
  const script=page.match(/<script nonce="[a-f0-9]+">([\s\S]*?)<\/script>/)[1];
  h.doc.getElementById=id=>id==="screen-host"?h.host:null;
  vm.runInNewContext(script,{window:h.browser,document:h.doc,globalThis:{window:h.browser},console});
  await h.tick();
  assert.match(text(h.host),/Cannot start studio/);assert.match(text(h.host),/Studio could not be started/);assert.match(text(h.host),/\/safe\/<path>\/studio.log/);
  assert.ok(h.button("Watch demo").disabled);assert.ok(h.button("Settings"));
  h.button("Settings").click();assert.match(text(h.host),/Token budget/);
  const language=flat(h.host).find(n=>n.tagName==="select");language.value="pt-BR";language.dispatch("change");await h.tick();
  assert.match(text(h.host),/Configurações/);
});

const controlName = node => node.textContent || node.type || node.tagName;
function focusCycle(h, expected) {
  const forward=[],backward=[];
  for(let i=0;i<expected.length;i++) {forward.push(controlName(h.doc.activeElement));h.key("Tab");}
  assert.deepEqual(forward,expected);assert.equal(controlName(h.doc.activeElement),expected[0]);
  for(let i=0;i<expected.length;i++) {h.key("Tab",true);backward.push(controlName(h.doc.activeElement));}
  assert.deepEqual(backward,[...expected].reverse());
}
test("each_screen_focus_cycle_excludes_underlying_game_and_hud_and_restores_them",async()=>{
  const h=await harness();try {
    assert.equal(h.game.getAttribute("inert"),"");assert.equal(h.hud.getAttribute("aria-hidden"),"true");
    focusCycle(h,["New project","Open project","Settings","PT","EN"]);
    h.button("Open project").click();await h.tick();
    h.message({kind:"studio_state",phase:"project_open",project:{path:"/repo",initialized:true},worlds:[],open_bugs:0});
    focusCycle(h,["Choose folder","Check the party","Settings","Back"]);
    h.button("Settings").click();focusCycle(h,["select","number","text","checkbox","Back"]);
    h.screens.showError({kind:"missingKey"});focusCycle(h,["Settings","Back"]);
    h.screens.showError({reasonKey:"error.stopped"});focusCycle(h,["Restart studio","Settings"]);
    h.screens.close();assert.equal(h.host.hidden,true);
    assert.equal(h.game.getAttribute("inert"),null);assert.equal(h.hud.getAttribute("aria-hidden"),null);
    h.hud.setAttribute("aria-hidden","false");
    h.screens.showError({kind:"connection"});assert.equal(h.hud.getAttribute("inert"),"");
  } finally {h.screens.destroy();assert.equal(h.hud.getAttribute("aria-hidden"),"false");}
});
test("settings_confirmation_only_after_save_and_validation_failure_is_translated",async()=>{
  const h=await harness();try {
    h.button("Settings").click();assert.doesNotMatch(text(h.host),/Settings saved/);
    const budget=flat(h.host).find(n=>n.type==="number");budget.value="2400";budget.dispatch("change");await h.tick();
    assert.match(text(h.host),/Settings saved/);assert.equal(h.writes.at(-1).tokenBudget,2400);
    budget.value="0";budget.dispatch("change");await h.tick();assert.match(text(h.host),/Invalid settings/);assert.doesNotMatch(text(h.host),/Settings saved/);
    h.key("Escape");h.button("Settings").click();assert.doesNotMatch(text(h.host),/Settings saved|Invalid settings/);
    const input=flat(h.host).find(n=>n.type==="text");input.value="";input.dispatch("change");await h.tick();assert.match(text(h.host),/Invalid settings/);
  } finally {h.screens.destroy();}
});
test("error_headings_distinguish_start_stop_connection_and_missing_key_and_restart",async()=>{
  const h=await harness();try {
    for(const [error,heading] of [[{reasonKey:"error.startFailed"},"Cannot start studio"],[{reasonKey:"error.stopped"},"Studio stopped"],[{kind:"connection"},"Connection lost"],[{kind:"missingKey"},"API key missing"]]) {
      h.screens.showError(error);assert.equal(flat(h.host).find(n=>n.tagName==="h1").textContent,heading);
      if(error.kind!=="missingKey") {assert.equal(h.button("Back"),undefined);assert.ok(h.button("Restart studio"));}
    }
    h.screens.showError({reasonKey:"error.stopped"});h.button("Restart studio").click();await h.tick();assert.equal(h.restarts.length,1);
    h.button("Settings").click();h.key("Escape");assert.ok(h.button("Restart studio"));
  } finally {h.screens.destroy();}
});
test("title_uses_cast_party_at_integer_scale_with_castle_and_shared_centered_layout",async()=>{
  const h=await harness();try {
    const tiles=flat(h.host).filter(n=>n.dataset.cast);
    for(const name of ["mage","warrior","paladin","back_wall","floor_stones","door_closed","banner"]) assert.ok(tiles.some(n=>n.dataset.cast===name),name);
    for(const tile of tiles) {assert.equal(tile.style.width,"48px");assert.equal(tile.style.height,"48px");assert.equal(tile.style.imageRendering,"pixelated");}
    assert.match(text(h.host),/Kenney Tiny Dungeon/);
    assert.match(text(h.doc.head),/align-items: center/);assert.match(text(h.doc.head),/font-size: 36px/);
  } finally {h.screens.destroy();}
});
