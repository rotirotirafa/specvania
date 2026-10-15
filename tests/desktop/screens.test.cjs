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
async function harness({studio = true, desktop = true} = {}) {
  const {setLanguage} = await load("i18n/index.js"); setLanguage("en");
  const doc = {createElement:tag=>new Element(tag,doc)};
  doc.head=new Element("head",doc);doc.body=new Element("body",doc);
  const host=new Element("section",doc);host.hidden=true;
  const game=new Element("div",doc),hud=new Element("main",doc);
  const summary=new Element("summary",doc);summary.textContent="Rooms and states";hud.append(summary);
  for(const label of ["HUD PT","HUD EN"]) {const button=new Element("button",doc);button.textContent=label;hud.append(button);}
  doc.body.append(game,host,hud);doc.getElementById=id=>id==="world-map"?game:id==="castle-hud"?hud:null;
  const events=new Map(),sent=[],writes=[],motion=[],restarts=[];let saved={language:"en",tokenBudget:1200,specvaniaPath:"specvania",reducedMotion:false},folder="/repo";
  const intervals=new Map();let now=0,intervalId=0;
  const browser={document:doc,innerWidth:960,innerHeight:540,navigator:{language:"en"},CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}},
    performance:{now:()=>now},setInterval(fn,ms){const id=++intervalId;intervals.set(id,{fn,ms});return id;},clearInterval(id){intervals.delete(id);},
    addEventListener(type,fn){const a=events.get(type)||[];a.push(fn);events.set(type,a);},removeEventListener(type,fn){events.set(type,(events.get(type)||[]).filter(f=>f!==fn));},
    dispatchEvent(event){if(event.type==="specvania:settings")motion.push(event.detail);for(const fn of events.get(event.type)||[])fn(event);},
    specvaniaDesktop:{isDesktop:true,controlToken:()=>"control",pickFolder:async()=>folder,settings:{get:async()=>({...saved}),set:async value=>{writes.push(value);saved={...saved,...value};return {...saved};}},studio:{get:async()=>({kind:"ready"}),restart:async()=>{restarts.push(true);}}}};
  if (!desktop) delete browser.specvaniaDesktop;
  const socket=new EventEmitter();socket.readyState=1;socket.addEventListener=socket.on.bind(socket);socket.removeEventListener=socket.off.bind(socket);socket.send=data=>sent.push(JSON.parse(data));
  const {mountScreens}=await load("screens/title.js");const screens=mountScreens({host,browser,socket});
  const initial={hidden:host.hidden,children:host.children.length,gameInert:game.getAttribute("inert"),hudInert:hud.getAttribute("inert")};
  await screens.ready;
  const message=value=>socket.emit("message",{data:JSON.stringify(value)});
  if (studio) message({kind:"state",phase:"idle",state:{run_id:null,status:"pending"}});
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  function key(key,shiftKey=false) {const event={key,shiftKey,target:doc.activeElement,preventDefault(){this.prevented=true;}};host.dispatch("keydown",event);
    // Simulate the browser's default focus traversal and button activation.
    if(!event.prevented && key==="Tab") {const nodes=flat(doc.body).filter(n=>["button","input","select","summary"].includes(n.tagName)&&!n.disabled&&!function inert(node){return node.attrs.inert!==undefined||node.hidden||node.parent&&inert(node.parent);}(n));const at=nodes.indexOf(doc.activeElement);nodes[(at+(shiftKey?-1:1)+nodes.length)%nodes.length]?.focus();}
    if(!event.prevented && ["Enter"," "].includes(key) && doc.activeElement?.tagName==="button")doc.activeElement.click();}
  return {host,doc,game,hud,browser,socket,screens,sent,writes,motion,restarts,message,key,tick,initial,intervals,
    advance(ms){now+=ms;for(const {fn} of intervals.values())fn();},
    folder:value=>{folder=value;},button:label=>flat(host).find(n=>n.tagName==="button"&&n.textContent===label)};
}
test("screens_stay_hidden_before_first_message_without_isolating_castle_or_hud",async()=>{
  const h=await harness({studio:false});try {
    assert.deepEqual(h.initial,{hidden:true,children:0,gameInert:null,hudInert:null});
    h.socket.emit("open");
    assert.equal(h.host.hidden,true);assert.equal(h.host.children.length,0);
    h.socket.emit("error");h.socket.emit("close");
    assert.equal(h.host.hidden,true);assert.equal(h.host.children.length,0);
  } finally {h.screens.destroy();}
});
for (const mode of ["replay","run","plan"]) for (const desktop of [false,true]) {
  test(`${mode}_state_without_phase_keeps_screens_unmounted_and_castle_hud_interactive_${desktop ? "desktop" : "browser"}`,async()=>{
    const h=await harness({studio:false,desktop});try {
      h.socket.emit("open");
      h.message({kind:"state",state:{run_id:`${mode}-run`,status:"running"},approval_request:null});
      assert.equal(h.host.hidden,true);assert.equal(h.host.children.length,0);
      for (const node of [h.game,h.hud]) {assert.equal(node.getAttribute("inert"),null);assert.equal(node.getAttribute("aria-hidden"),null);}
      h.message({kind:"event",event:{type:"world_loaded",run_id:`${mode}-run`,seq:1}});
      h.message({kind:"approval_rejected",reason:"API key missing; check the configured environment variable."});
      h.socket.emit("error");h.socket.emit("close");
      assert.equal(h.host.hidden,true);assert.equal(h.host.children.length,0);assert.equal(h.button("New project"),undefined);
      for (const node of [h.game,h.hud]) {assert.equal(node.getAttribute("inert"),null);assert.equal(node.getAttribute("aria-hidden"),null);}
      h.key("Tab");assert.equal(h.doc.activeElement.textContent,"Rooms and states");
      h.key("Tab");assert.equal(h.doc.activeElement.textContent,"HUD PT");
      assert.deepEqual(h.sent,[]);
    } finally {h.screens.destroy();}
  });
}
for (const kind of ["state","studio_state"]) {
  test(`${kind}_idle_identifies_studio_and_mounts_title`,async()=>{
    const h=await harness({studio:false});try {
      assert.equal(h.host.hidden,true);
      h.message({kind,phase:"idle",state:{run_id:null,status:"pending"},project:null,worlds:[],open_bugs:0});
      assert.equal(h.host.hidden,false);assert.equal(h.host.dataset.screen,"title");
      assert.equal(h.doc.activeElement.textContent,"New project");
      for (const node of [h.game,h.hud]) {assert.equal(node.getAttribute("inert"),"");assert.equal(node.getAttribute("aria-hidden"),"true");}
      h.socket.emit("close");assert.equal(h.host.dataset.screen,"error");assert.match(text(h.host),/Connection lost/);
    } finally {h.screens.destroy();}
  });
}
test("keyboard_only_title_project_settings_error_navigation_and_mouse",async()=>{
  const h=await harness();try {
    assert.equal(h.doc.activeElement.textContent,"New project");h.key("Enter");await h.tick();
    assert.deepEqual(h.sent.at(-1),{kind:"open_project",path:"/repo",control_token:"control"});
    h.message({kind:"studio_state",phase:"project_open",language:"en",project:{path:"/repo",initialized:false},worlds:[],open_bugs:0});
    assert.equal(h.doc.activeElement.textContent,"Initialize project");h.key(" ");assert.equal(h.sent.at(-1).kind,"init_project");
    h.key("Escape");assert.equal(h.doc.activeElement.textContent,"New project");
    h.key("Tab");assert.equal(h.doc.activeElement.textContent,"Open project");h.key("Tab",true);assert.equal(h.doc.activeElement.textContent,"New project");
    // Reach Settings solely by Tab, including enabled Watch demo.
    for(let i=0;i<3;i++)h.key("Tab");assert.equal(h.doc.activeElement.textContent,"Settings");h.key("Enter");
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
    assert.equal(h.button("Watch demo").disabled,false);
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
    assert.match(text(h.host),/0.0 s/);assert.match(text(h.host),/HTTP 404/);assert.match(text(h.host),/Timeout/);
    const rows=flat(h.host).filter(n=>n.dataset.probe);assert.equal(rows.length,3);assert.notEqual(rows[0].style.color,rows[1].style.color);assert.equal(rows[1].style.color,rows[2].style.color);
    h.folder(null);const count=h.sent.length;h.button("Choose folder").click();await h.tick();assert.equal(h.sent.length,count);
  } finally {h.screens.destroy();}
});
test("project_probe_pending_rows_fill_in_arrival_order_and_enable_only_when_done",async()=>{
  const h=await harness();try {
    h.message({kind:"studio_state",phase:"project_open",project:{path:"/repo",initialized:true},worlds:[],open_bugs:0});
    const button=h.button("Check the party");button.click();
    const rows=()=>flat(h.host).filter(node=>node.dataset.probe);
    h.message({kind:"probe_result",models:["one","two"],results:[],done:false});
    const pending=rows();assert.deepEqual(pending.map(node=>node.textContent),["one: checking…","two: checking…"]);
    assert.equal(button.disabled,true);
    h.message({kind:"probe_result",models:["one","two"],results:[{model:"two",status:"ok",latency_ms:37775.874}],done:false});
    assert.deepEqual(rows(),pending);assert.equal(rows()[0].textContent,"one: checking…");
    assert.equal(rows()[1].textContent,"two: OK · 37.8 s");assert.equal(button.disabled,true);
    h.message({kind:"probe_result",models:["one","two"],results:[{model:"one",status:"timeout",latency_ms:null},{model:"two",status:"ok",latency_ms:37775.874}],done:true});
    assert.deepEqual(rows(),pending);assert.equal(rows()[0].textContent,"one: Timeout");assert.equal(button.disabled,false);
  } finally {h.screens.destroy();}
});
test("project_probe_stays_pending_after_folder_rejection_and_screen_navigation",async()=>{
  const h=await harness();try {
    h.message({kind:"studio_state",phase:"project_open",project:{path:"/repo",initialized:true},worlds:[],open_bugs:0});
    h.button("Check the party").click();
    h.message({kind:"probe_result",models:["one","two"],results:[],done:false});
    h.button("Choose folder").click();await h.tick();
    h.message({kind:"approval_rejected",reason:"The studio already has an active plan, run or probe."});
    assert.equal(h.button("Check the party").disabled,true);
    assert.deepEqual(flat(h.host).filter(node=>node.dataset.probe).map(node=>node.textContent),["one: checking…","two: checking…"]);
    h.button("Settings").click();h.key("Escape");h.button("Open project").click();await h.tick();
    assert.equal(h.button("Check the party").disabled,true);
    assert.equal(flat(h.host).filter(node=>node.dataset.probe).length,2);
    h.message({kind:"probe_result",models:["one","two"],results:[{model:"one",status:"ok",latency_ms:700},{model:"two",status:404}],done:true});
    assert.equal(h.button("Check the party").disabled,false);
  } finally {h.screens.destroy();}
});
test("project_probe_reconnect_discards_stale_pending_and_restores_active_snapshot",async()=>{
  const h=await harness();try {
    h.message({kind:"studio_state",phase:"project_open",project:{path:"/repo",initialized:true},worlds:[],open_bugs:0});
    h.message({kind:"probe_result",models:["old"],results:[],done:false});
    h.socket.reconnecting=true;h.socket.emit("close");
    h.message({kind:"state",phase:"project_open",state:{}});
    h.message({kind:"studio_state",phase:"project_open",project:{path:"/another",initialized:true},worlds:[],open_bugs:0});
    assert.equal(h.button("Check the party").disabled,false);
    assert.equal(flat(h.host).filter(node=>node.dataset.probe).length,0);
    h.message({kind:"probe_result",models:["current"],results:[],done:false});
    assert.equal(h.button("Check the party").disabled,true);
    assert.deepEqual(flat(h.host).filter(node=>node.dataset.probe).map(node=>node.textContent),["current: checking…"]);
  } finally {h.screens.destroy();}
});
for(const [language,long,short] of [["en","37.8","0.7"],["pt-BR","37,8","0,7"]]) {
  test(`project_probe_seconds_format_and_pending_translation_${language}`,async()=>{
    const h=await harness();try {
      h.message({kind:"studio_state",phase:"project_open",project:{path:"/repo",initialized:true},worlds:[],open_bugs:0});
      h.message({kind:"probe_result",models:["long","short","pending"],results:[{model:"long",status:"ok",latency_ms:37775.874},{model:"short",status:"ok",latency_ms:700}],done:false});
      const {setLanguage}=await load("i18n/index.js");setLanguage(language);
      const rows=flat(h.host).filter(node=>node.dataset.probe);
      assert.equal(rows[0].textContent,`long: OK · ${long} s`);assert.equal(rows[1].textContent,`short: OK · ${short} s`);
      assert.equal(rows[2].textContent,language==="en"?"pending: checking…":"pending: verificando…");
      assert.equal(h.button(language==="en"?"Check the party":"Verificar o grupo").disabled,true);
    } finally {h.screens.destroy();}
  });
}
function preload() {
  let api;const calls=[];
  vm.runInNewContext(fs.readFileSync(path.join(desktop,"preload.js"),"utf8"),{process:{platform:"darwin"},require:()=>({contextBridge:{exposeInMainWorld:(_,value)=>{api=value;}},ipcRenderer:{sendSync:()=>"control",send:(...a)=>calls.push(a),invoke:async(...a)=>{calls.push(a);return a;}}})});
  return {api,calls};
}
test("preload_exact_capabilities_use_narrow_ipc",async()=>{
  const {api,calls}=preload();assert.deepEqual(Object.keys(api).sort(),["controlToken","isDesktop","notify","pickFolder","platform","settings","studio"]);
  await api.pickFolder();await api.settings.get();await api.settings.set({tokenBudget:10});await api.studio.get();await api.studio.restart();
  assert.deepEqual(calls.map(c=>c[0]),["specvania:pick-folder","specvania:settings-get","specvania:settings-set","specvania:studio","specvania:studio-restart"]);
});
async function main(directory) {
  const app=new EventEmitter();app.getPath=()=>directory;app.whenReady=()=>Promise.resolve();app.quit=()=>{};app.exit=()=>assert.fail("main failed");
  const ipcMain=new EventEmitter();const handlers=new Map();ipcMain.handle=(key,fn)=>handlers.set(key,fn);
  let window;const dialogs=[];
  class BrowserWindow extends EventEmitter {
    constructor(options){super();window=this;this.options=options;this.webContents=new EventEmitter();this.webContents.setWindowOpenHandler=()=>{};this.webContents.getZoomLevel=()=>0;this.webContents.setZoomLevel=()=>{};}
    loadURL(url){this.url=url;this.webContents.emit("did-finish-load");return Promise.resolve();}
  }
  vm.runInNewContext(fs.readFileSync(path.join(desktop,"main.js"),"utf8"),{__dirname:desktop,URL,process:{argv:["electron",".","--url","http://127.0.0.1:8765/","--control-token","control"]},console,
    require:name=>name==="electron"?{app,BrowserWindow,ipcMain,Notification:{},shell:{},Menu:{buildFromTemplate:v=>v,setApplicationMenu(){}},screen:{getAllDisplays:()=>[{workArea:{x:0,y:0,width:1920,height:1080}}]},dialog:{showOpenDialog:async(...args)=>{dialogs.push(args);return {canceled:false,filePaths:["/chosen"]};}}}:require(name)});
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

for (const language of ["en","pt-BR"]) test(`launcher_starting_page_elapsed_hint_and_cleanup_${language}`,async()=>{
  const h=await harness();h.screens.destroy();
  h.browser.specvaniaDesktop.settings.get=async()=>({language});
  const page=require("../../apps/desktop/studio.cjs").startingPage();
  const dictionary=JSON.parse(fs.readFileSync(path.join(root,`i18n/${language}.json`),"utf8"));
  const colors=(await load("pixel/palette.js")).UI_COLORS;
  assert.ok(page.includes(`background:${colors.background}`));
  assert.match(page,/default-src 'none'; script-src 'nonce-[a-f0-9]+'; style-src 'unsafe-inline'; font-src data:; img-src data:/);
  assert.doesNotMatch(page,/<iframe|<script[^>]+src=/);
  const script=page.match(/<script nonce="[a-f0-9]+">([\s\S]*?)<\/script>/)[1];
  h.doc.getElementById=id=>id==="screen-host"?h.host:null;
  const screens=vm.runInNewContext(script,{window:h.browser,document:h.doc,globalThis:{window:h.browser},console});
  const hint=()=>flat(h.host).find(n=>n.textContent===dictionary["starting.firstStart"]);
  try {
    assert.equal(h.host.hidden,false);assert.equal(h.host.dataset.screen,"starting");
    assert.match(text(h.host),/Starting the studio…/);assert.match(text(h.host),/Elapsed: 0 s/);
    assert.ok(flat(h.host).find(n=>n.textContent==="The first start after installing can take longer.").hidden);
    assert.equal(h.intervals.size,1);assert.equal([...h.intervals.values()][0].ms,1000);
    h.advance(9000);
    await screens.ready;
    assert.equal(flat(h.host).find(n=>n.tagName==="h1").textContent,dictionary["starting.title"]);
    assert.ok(text(h.host).includes(dictionary["starting.elapsed"].replace("{seconds}","9")));
    assert.ok(hint().hidden);assert.ok(text(h.doc.head).includes(`color: ${colors.text}`));
    h.advance(1000);assert.equal(hint().hidden,false);
    assert.ok(text(h.host).includes(dictionary["starting.elapsed"].replace("{seconds}","10")));
    h.key("Escape");assert.equal(h.host.dataset.screen,"starting");
    h.advance(24000);
    assert.ok(text(h.host).includes(dictionary["starting.elapsed"].replace("{seconds}","34")));
    assert.equal(flat(h.host).filter(n=>n.tagName==="button").length,0);
    screens.showError({reasonKey:"error.startTimeout"});
    assert.equal(h.host.dataset.screen,"error");assert.equal(h.intervals.size,0);
    assert.ok(text(h.host).includes(dictionary["error.startTimeout"]));
  } finally {screens.destroy();assert.equal(h.intervals.size,0);}
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
    focusCycle(h,["New project","Open project","Watch demo","Settings","PT","EN"]);
    h.button("Open project").click();await h.tick();
    h.message({kind:"studio_state",phase:"project_open",project:{path:"/repo",initialized:true},worlds:[],open_bugs:0});
    focusCycle(h,["Choose folder","New expedition","Check the party","Settings","Back"]);
    h.button("Settings").click();focusCycle(h,["select","number","text","Use the default runtime","checkbox","Back"]);
    h.screens.showError({kind:"missingKey"});focusCycle(h,["Watch demo","Settings","Back","div"]);
    h.screens.showError({reasonKey:"error.stopped"});focusCycle(h,["Restart studio","Settings","div"]);
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
    assert.match(text(h.doc.head),/#screen-host \{[^}]*align-items: safe center; justify-content: safe center/);assert.match(text(h.doc.head),/font-size: 36px/);
  } finally {h.screens.destroy();}
});
