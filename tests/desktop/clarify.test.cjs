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
  constructor(tag,document) { Object.assign(this,{tagName:tag,document,children:[],style:{},attrs:{},dataset:{},listeners:{},_text:"",value:"",disabled:false}); }
  get textContent() {return this._text;}
  set textContent(value) {this._text=String(value);this.replaceChildren();}
  append(...nodes) { for(const node of nodes) {if(node.parent) node.parent.children=node.parent.children.filter(n=>n!==node);node.parent=this;this.children.push(node);} }
  replaceChildren(...nodes) {this.children.forEach(n=>n.parent=null);this.children=[];this.append(...nodes);}
  setAttribute(key,value) {this.attrs[key]=String(value);}
  getAttribute(key) {return this.attrs[key] ?? null;}
  removeAttribute(key) {delete this.attrs[key];}
  addEventListener(key,fn) {(this.listeners[key] ||= []).push(fn);}
  removeEventListener(key,fn) {this.listeners[key]=(this.listeners[key]||[]).filter(f=>f!==fn);}
  dispatch(type,event={}) {event.target ||= this; for(const fn of this.listeners[type]||[]) fn(event); if(type === "keydown" && this.parent) this.parent.dispatch(type,event);}
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
  const browser={document:doc,innerWidth:960,innerHeight:540,navigator:{language:"en"},CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}},
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
  function key(key,shiftKey=false,ctrlKey=false) {const event={key,shiftKey,ctrlKey,target:doc.activeElement,preventDefault(){this.prevented=true;}};(doc.activeElement || host).dispatch("keydown",event);
    // Simulate the browser's default focus traversal and button activation.
    if(!event.prevented && key==="Tab") {const nodes=flat(doc.body).filter(n=>["button","input","select","textarea","summary"].includes(n.tagName)&&!n.disabled&&!function inert(node){return node.attrs.inert!==undefined||node.hidden||node.parent&&inert(node.parent);}(n));const at=nodes.indexOf(doc.activeElement);nodes[(at+(shiftKey?-1:1)+nodes.length)%nodes.length]?.focus();}
    if(!event.prevented && ["Enter"," "].includes(key) && doc.activeElement?.tagName==="button")doc.activeElement.click();}
  return {host,doc,game,hud,browser,socket,screens,sent,writes,motion,restarts,message,key,tick,initial,folder:value=>{folder=value;},button:label=>flat(host).find(n=>n.tagName==="button"&&n.textContent===label)};
}
const projectState = {kind:"studio_state",phase:"project_open",language:"en",project:{path:"/repo",initialized:true},worlds:["old-world"],open_bugs:0};
const question = {question_id:"q1",text:"Which behavior?",options:["Yes","No","Maybe"],default:"No"};
const readyState = {...projectState,phase:"world_ready",world_slug:"new-world",world:{areas:[],rooms:[],doors:[],boss_ids:[]},worlds:["new-world"]};
async function entrance() {const h=await harness();h.message(projectState);h.button("New expedition").click();return h;}
function textarea(h) {return flat(h.host).find(n=>n.tagName==="textarea");}
function ask(h, value=question) {h.message({...projectState,phase:"planning",clarify_request:value});}
function last(h,kind) {return h.sent.filter(n=>n.kind===kind).at(-1);}
test("idea_enter_ctrl_enter_mouse_skip_and_empty_ideas_use_studio_messages",async()=>{
  for(const method of ["Enter","Ctrl+Enter","mouse"]) {
    const h=await entrance();try {
      assert.match(text(h.host),/Mage build/);assert.equal(h.doc.activeElement,textarea(h));
      const portrait=flat(h.host).find(n=>n.dataset.cast==="mage");assert.ok(portrait);assert.equal(portrait.style.width,"32px");assert.equal(portrait.style.imageRendering,"pixelated");
      h.key("Enter");assert.equal(last(h,"start_plan"),undefined);
      const field=textarea(h);field.value="Add invoices\nwith PDF export";h.key("Enter",true);assert.equal(last(h,"start_plan"),undefined);
      flat(h.host).find(n=>n.type==="checkbox").checked=method==="mouse";
      if(method==="mouse")h.button("Plan the world").click();else h.key("Enter",false,method==="Ctrl+Enter");
      assert.deepEqual(last(h,"start_plan"),{kind:"start_plan",idea:"Add invoices\nwith PDF export",no_clarify:method==="mouse",control_token:"control"});
      assert.match(text(h.host),/Mage is working/);
    } finally {h.screens.destroy();}
  }
});
test("clarify_enter_accepts_default_arrows_and_mouse_select_each_answer_shape",async()=>{
  const h=await entrance();try {
    ask(h);assert.equal(h.doc.activeElement.textContent,"No");assert.equal(h.doc.activeElement.getAttribute("aria-selected"),"true");h.key("Enter");
    assert.deepEqual(last(h,"clarify_answer"),{kind:"clarify_answer",question_id:"q1",answer:"No",control_token:"control"});
    h.key("Enter");assert.equal(h.sent.filter(n=>n.kind==="clarify_answer").length,1);
    ask(h,{...question,question_id:"q2"});h.key("ArrowDown");assert.equal(h.doc.activeElement.textContent,"Maybe");h.key("ArrowDown");assert.equal(h.doc.activeElement.textContent,"Yes");h.key("ArrowUp");h.key("Enter");
    assert.deepEqual(last(h,"clarify_answer"),{kind:"clarify_answer",question_id:"q2",answer:"Maybe",control_token:"control"});
    ask(h,{...question,question_id:"q3"});h.button("Yes").click();assert.equal(h.button("Yes").getAttribute("aria-selected"),"true");h.button("Send answer").click();
    assert.deepEqual(last(h,"clarify_answer"),{kind:"clarify_answer",question_id:"q3",answer:"Yes",control_token:"control"});
  } finally {h.screens.destroy();}
});
test("clarify_free_text_no_options_reconnect_and_language_preserve_input",async()=>{
  const h=await harness();try {
    ask(h);assert.equal(h.host.dataset.screen,"idea");assert.equal(h.doc.activeElement.textContent,"No");
    const field=textarea(h);field.focus();field.value="Different behavior\nwith details";
    const {setLanguage}=await load("i18n/index.js");setLanguage("pt-BR");
    assert.equal(textarea(h),field);assert.equal(field.value,"Different behavior\nwith details");assert.ok(field.parent);assert.match(text(h.host),/Enviar resposta/);
    ask(h);assert.equal(textarea(h),field);h.key("Enter");assert.equal(last(h,"clarify_answer").answer,"Different behavior\nwith details");
    ask(h,{question_id:"q2",text:"How?",options:[],default:"Default text"});h.key("Enter");assert.equal(last(h,"clarify_answer").answer,"Default text");
  } finally {h.screens.destroy();}
});
test("start_disabled_until_save_ack_and_settings_budget_rides_on_start_run",async()=>{
  const h=await entrance();try {
    h.message(readyState);const start=h.button("Start expedition");assert.equal(start.disabled,true);start.click();assert.equal(last(h,"start_run"),undefined);
    h.button("Save map").click();assert.deepEqual(last(h,"save_world"),{kind:"save_world",slug:"new-world",control_token:"control"});assert.ok(h.button("Save map").disabled);assert.ok(h.button("Start expedition").disabled);
    h.message({...readyState});assert.equal(h.button("Start expedition").disabled,false);h.button("Start expedition").click();
    assert.deepEqual(last(h,"start_run"),{kind:"start_run",slug:"new-world",allow_dirty:false,token_budget:1200,control_token:"control"});
    h.message({...projectState,phase:"running"});assert.equal(h.host.hidden,true);assert.equal(h.host.children.length,0);assert.equal(h.game.getAttribute("inert"),null);assert.equal(h.hud.getAttribute("inert"),null);
  } finally {h.screens.destroy();}
});
test("dirty_paths_are_scrollable_inert_text_cancel_and_start_anyway",async()=>{
  const h=await entrance();try {
    h.message(readyState);h.button("Save map").click();h.message(readyState);h.button("Start expedition").click();
    const rejection={kind:"approval_rejected",reason:"Git tree is dirty",dirty_paths:["space name.txt","<img src=x onerror=evil>","src/other.js"]};
    h.message(rejection);assert.ok(text(h.host).includes(rejection.dirty_paths[1]));
    const paths=flat(h.host).find(n=>n.style.overflow==="auto");assert.ok(paths);assert.equal(paths.tabIndex,0);h.key("Tab",true);assert.equal(h.doc.activeElement,paths);h.key("Tab");assert.equal(h.doc.activeElement.textContent,"Cancel");assert.deepEqual(paths.children.map(n=>n.textContent),rejection.dirty_paths);
    const before=h.sent.length;h.button("Cancel").click();assert.equal(h.sent.length,before);assert.equal(h.button("Start expedition").disabled,false);
    h.button("Start expedition").click();h.message(rejection);h.button("Start anyway").click();
    assert.deepEqual(last(h,"start_run"),{kind:"start_run",slug:"new-world",allow_dirty:true,token_budget:1200,control_token:"control"});
  } finally {h.screens.destroy();}
});
test("existing_world_choice_enters_same_dialogue_then_explicitly_saves",async()=>{
  const h=await harness();try {
    h.message(projectState);h.button("old-world").click();assert.equal(last(h,"save_world"),undefined);assert.ok(h.button("Start expedition").disabled);h.button("Save map").click();assert.deepEqual(last(h,"save_world"),{kind:"save_world",slug:"old-world",control_token:"control"});
    h.message({...readyState,world_slug:"old-world"});assert.equal(h.host.dataset.screen,"idea");assert.equal(h.button("Start expedition").disabled,false);h.button("Start expedition").click();assert.equal(last(h,"start_run").slug,"old-world");
  } finally {h.screens.destroy();}
});
test("dialogue_tab_focus_isolated_including_textarea_and_errors_localized_without_secrets",async()=>{
  const h=await entrance();try {
    assert.equal(h.game.getAttribute("inert"),"");assert.equal(h.hud.getAttribute("aria-hidden"),"true");
    assert.equal(h.doc.activeElement,textarea(h));h.key("Tab",true);assert.equal(h.doc.activeElement.textContent,"Plan the world");h.key("Tab");assert.equal(h.doc.activeElement,textarea(h));h.key("Escape");assert.equal(h.host.dataset.screen,"idea");
    const {setLanguage}=await load("i18n/index.js");setLanguage("pt-BR");
    h.message({kind:"approval_rejected",reason:"API key missing SECRET-SHOULD-NOT-ECHO"});assert.match(text(h.host),/Chave de API/);assert.doesNotMatch(text(h.host),/SECRET-SHOULD/);assert.match(text(h.host),/NVIDIA_API_KEY/);h.button("Continuar").click();assert.ok(textarea(h));
    h.message({...projectState,error:"PRIVATE-SECRET from provider",phase:"project_open"});assert.doesNotMatch(text(h.host),/PRIVATE-SECRET/);assert.match(text(h.host),/provedor/);h.button("Continuar").click();assert.ok(textarea(h));
  } finally {h.screens.destroy();}
});
test("idea_never_mounts_in_replay_run_or_view_only_studio",async()=>{
  for(const mode of ["replay","run"]) {
    const h=await harness({studio:false});try {
      h.message({kind:"state",state:{run_id:mode,rooms:[]}});h.message({kind:"event",event:{type:"spec_written",feature:"new-world"}});
      assert.equal(h.host.hidden,true);assert.equal(h.host.children.length,0);assert.equal(h.game.getAttribute("inert"),null);assert.equal(h.sent.length,0);
    } finally {h.screens.destroy();}
  }
  const h=await harness({desktop:false});try {h.message(projectState);assert.ok(h.button("New expedition").disabled);assert.ok(h.button("old-world").disabled);h.button("New expedition").click();assert.notEqual(h.host.dataset.screen,"idea");h.message({...projectState,phase:"planning",clarify_request:question});assert.notEqual(h.host.dataset.screen,"idea");assert.equal(last(h,"clarify_answer"),undefined);}finally{h.screens.destroy();}
});

/** Real map mount + real T31 reveal, with controllable Phaser tweens. */
function mapHarness(reduced=false) {
  const events=new Map(),objects=[],tweens=[];
  const object=(kind,x=0,y=0,key)=>({kind,x,y,key,commands:[],destroyed:false,
    destroy(){this.destroyed=true;},setTexture(key){this.key=key;return this;},setPosition(x,y){this.x=x;this.y=y;return this;},setAlpha(){return this;},setVisible(){return this;},setInteractive(){return this;},on(){return this;},
    clear(){this.commands=[];return this;},fillStyle(){return this;},fillRect(...args){this.commands.push(args);return this;}});
  const create=(...args)=>{const item=object(...args);objects.push(item);return item;};
  const camera={width:960,height:540,x:0,y:0,zoom:1,center:[0,0],stopFollow(){},startFollow(){},setViewport(x,y,width,height){Object.assign(this,{x,y,width,height});},setZoom(zoom){assert.ok(Number.isInteger(zoom));this.zoom=zoom;},setBounds(...bounds){this.bounds=bounds;},centerOn(x,y){this.center=[x,y];}};
  const scene={textures:{exists:()=>true},input:{keyboard:{on(){}}},children:{bringToTop(){}},
    cameras:{main:camera},
    add:{image:(...args)=>create("image",...args),zone:(...args)=>create("zone",...args),text:(...args)=>create("text",...args),graphics:()=>create("graphics")},
    tweens:{add(config){const tween={config,stopped:false,stop(){this.stopped=true;}};tweens.push(tween);return tween;},killTweensOf(target){for(const tween of tweens)if(tween.config.targets===target)tween.stop();}}};
  const description={};
  const browser={innerWidth:960,innerHeight:540,document:{getElementById:id=>id==="map-rooms"?description:null},matchMedia:()=>({matches:reduced,addEventListener(){},removeEventListener(){}}),
    CustomEvent:class {constructor(type,value){this.type=type;Object.assign(this,value);}},dispatchEvent(event){for(const fn of events.get(event.type)||[])fn(event);},
    addEventListener(type,fn){const list=events.get(type)||[];list.push(fn);events.set(type,list);},removeEventListener(type,fn){events.set(type,(events.get(type)||[]).filter(f=>f!==fn));}};
  let config;
  const Phaser={CANVAS:1,Scale:{NONE:0},Game:class {constructor(value){config=value;this.scale={resize(){}};}destroy(){}}};
  return {Phaser,browser,objects,description,camera,emit(type,detail){for(const fn of events.get(type)||[])fn({detail});},create(){config.scene.create.call(scene);},
    step(){const tween=tweens.find(t=>!t.stopped);assert.ok(tween);tween.stopped=true;tween.config.targets.t=1;tween.config.onUpdate();tween.config.onComplete();},frame(){config.scene.update.call(scene);},tweens};
}
const previewWorld={areas:[{id:"A1",title:"Preview",boss_ids:["S1"]}],boss_ids:["S1"],doors:[{src:"T01",dst:"T02"}],rooms:[
  {id:"T01",title:"First",area:"A1",depends_on:[],state:"open"},
  {id:"T02",title:"Second",area:"A1",depends_on:["T01"],state:"locked"}]};
test("studio_world_reveals_one_chamber_at_a_time_then_run_world_takes_over",async()=>{
  const {mountMap}=await load("map/scene.js");
  for(const early of [false,true]) {
    const h=mapHarness(),map=mountMap(h.Phaser,{},h.browser);try {
      if(!early)h.create();
      h.emit("specvania:studio-state",{phase:"world_ready",world_slug:"preview",world:previewWorld});if(early)h.create();
      const mask=()=>h.objects.findLast(n=>n.kind==="graphics"&&!n.destroyed&&n.commands.some(c=>c[2]===80&&c[3]===48));
      const cover=mask();assert.ok(cover);assert.equal(cover.commands.length,2);
      const signals=[];h.browser.addEventListener("specvania:world-revealed",event=>signals.push(event.detail));
      h.frame();assert.equal(signals.length,0);h.step();h.frame();assert.equal(cover.commands.length,1);assert.equal(signals.length,0);h.step();h.frame();assert.equal(cover.commands.length,0);assert.deepEqual(signals,[{slug:"preview"}]);
      const count=h.tweens.length;h.emit("specvania:studio-state",{phase:"world_ready",world_slug:"preview",world:previewWorld});assert.equal(h.tweens.length,count,"Save ack does not replay reveal");
      h.emit("specvania:event",{type:"world_loaded",...previewWorld,rooms:[{...previewWorld.rooms[0],id:"T09",title:"Run owns the map"}],doors:[]});
      assert.match(h.description.textContent,/T09: Run owns the map/);assert.doesNotMatch(h.description.textContent,/T02/);
      h.emit("specvania:studio-state",{phase:"project_open"});assert.equal(h.description.textContent,"");
      assert.ok(h.objects.some(n=>!n.destroyed&&n.key==="door_double_left:idle:0"));
    } finally {map.destroy();}
  }
});
test("reduced_motion_studio_world_reveal_settles_immediately",async()=>{
  const {mountMap}=await load("map/scene.js");const h=mapHarness(true),map=mountMap(h.Phaser,{},h.browser);try {
    h.create();h.emit("specvania:studio-state",{phase:"world_ready",world_slug:"preview",world:previewWorld});assert.equal(h.tweens.length,0);
    assert.ok(h.objects.filter(n=>!n.destroyed&&n.kind==="graphics").every(n=>!n.commands.some(c=>c[2]===80&&c[3]===48)));
  } finally {map.destroy();}
});
test("save_rejection_keeps_start_disabled_and_a_different_world_needs_its_own_save",async()=>{
  const h=await entrance();try {
    h.message(readyState);h.button("Save map").click();h.message({kind:"approval_rejected",reason:"World has 1 lint errors"});
    assert.match(text(h.host),/lint errors/);h.button("Continue").click();assert.equal(h.button("Start expedition").disabled,true);
    h.button("Save map").click();h.message(readyState);assert.equal(h.button("Start expedition").disabled,false);
    h.message({...readyState,world_slug:"different-world"});assert.equal(h.button("Start expedition").disabled,true);h.button("Start expedition").click();assert.equal(last(h,"start_run"),undefined);
  } finally {h.screens.destroy();}
});
test("start_waits_for_castle_reveal_even_after_map_is_saved",async()=>{
  const h=await harness({studio:false});h.browser.Phaser={};
  // The controller captures availability when it mounts.
  h.screens.destroy();const {mountScreens}=await load("screens/title.js");h.screens=mountScreens({host:h.host,browser:h.browser,socket:h.socket});await h.screens.ready;
  try {
    h.message(projectState);h.button("New expedition").click();h.message(readyState);h.button("Save map").click();h.message(readyState);
    assert.ok(h.button("Start expedition").disabled);h.button("Start expedition").click();assert.equal(last(h,"start_run"),undefined);
    h.browser.dispatchEvent(new h.browser.CustomEvent("specvania:world-revealed",{detail:{slug:"other-world"}}));assert.ok(h.button("Start expedition").disabled);
    h.browser.dispatchEvent(new h.browser.CustomEvent("specvania:world-revealed",{detail:{slug:"new-world"}}));assert.equal(h.button("Start expedition").disabled,false);h.button("Start expedition").click();assert.equal(last(h,"start_run").slug,"new-world");
  } finally {h.screens.destroy();}
});

test("dialogue_close_and_running_finished_states_restore_hidden_hud_and_game",async()=>{
  for(const kind of ["state","studio_state"])for(const phase of ["running","finished"]) {
    const h=await entrance();try {
      h.message(readyState);h.button("Save map").click();h.message(readyState);h.button("Start expedition").click();
      // The map's preview previously left this property behind.
      h.hud.hidden=true;
      h.message({...projectState,kind,phase,state:{rooms:[]}});
      assert.equal(h.hud.hidden,false);assert.equal(h.host.hidden,true);
      for(const node of [h.game,h.hud]) {assert.equal(node.getAttribute("inert"),null);assert.equal(node.getAttribute("aria-hidden"),null);}
    } finally {h.screens.destroy();}
  }
  const h=await entrance();try {h.hud.hidden=true;h.screens.close();assert.equal(h.hud.hidden,false);assert.equal(h.hud.getAttribute("inert"),null);}finally{h.screens.destroy();}
});
test("dirty_dialog_cancel_restores_visible_hud_and_underlying_game",async()=>{
  const h=await entrance();try {
    h.message(readyState);h.button("Save map").click();h.message(readyState);h.button("Start expedition").click();
    h.message({kind:"approval_rejected",reason:"dirty",dirty_paths:["file.txt"]});h.hud.hidden=true;
    h.button("Cancel").click();assert.equal(h.hud.hidden,false);assert.equal(h.hud.getAttribute("inert"),null);assert.equal(h.game.getAttribute("inert"),null);
    assert.equal(h.button("Start expedition").disabled,false);
  }finally{h.screens.destroy();}
});
const styleRule = selector => {
  const source=fs.readFileSync(path.join(root,"index.html"),"utf8");
  const escaped=selector.replace(/[.*+?^${}()|[\]\\]/g,"\\$&");
  const match=source.match(new RegExp(escaped+"\\s*\\{([^}]+)\\}"));assert.ok(match,selector);return match[1];
};
test("mage_dialogue_stays_left_of_party_column_at_960_and_1920_without_clipping_content",async()=>{
  const h=await entrance();try {
    const shell=styleRule("#screen-host[data-screen=idea]"),hud=styleRule("main");
    const pixels=(rule,property,fallback=0)=>Number(new RegExp(`(?:^|;)\\s*${property}:\\s*(\\d+)px`).exec(rule)?.[1] ?? fallback);
    const screenCSS=text(h.doc.head);
    const panel=screenCSS.match(/#screen-host \.screen-panel\s*\{([^}]+)\}/)[1];
    const hostCSS=screenCSS.match(/#screen-host\s*\{([^}]+)\}/)[1];
    const padding=Number(/padding:\s*\d+px (\d+)px/.exec(hostCSS)[1]);
    for(const [width,height]of [[960,540],[1920,1080]]) {
      const hudLeft=width-pixels(hud,"right")-pixels(hud,"width")-2*pixels(hud,"padding")-2*pixels(hud,"border");
      const contentWidth=width-pixels(shell,"right")-2*padding;
      for(const mode of ["idea","clarify","working","world"]) {
        const panelWidth=mode==="world"?contentWidth:Math.min(pixels(panel,"width"),contentWidth);
        const dialogueRight=padding+(contentWidth+panelWidth)/2;
        assert.ok(dialogueRight<hudLeft,`${mode} dialogue overlaps the party at ${width}×${height}`);
        assert.ok(panelWidth>0);
      }
    }
    ask(h,{...question,text:"A long question ".repeat(50)});
    assert.ok(text(h.host).includes("A long question ".repeat(50)));
    assert.match(styleRule(".dialogue-content"),/overflow:\s*auto/);
  }finally{h.screens.destroy();}
});
test("dialogue_layout_fits_viewport_with_actions_outside_scroll_content",async()=>{
  const h=await entrance();try {
    const check=()=>{
      const box=flat(h.host).find(n=>n.className==="mage-dialogue");
      const body=flat(box).find(n=>n.className==="dialogue-content");
      const actions=flat(box).find(n=>n.className==="dialogue-actions");
      assert.ok(body);assert.ok(actions);assert.equal(actions.parent,box);assert.ok(!flat(body).includes(actions));
      for(const button of flat(box).filter(n=>n.tagName==="button"&&n.getAttribute("role")!=="option"))assert.ok(flat(actions).includes(button),button.textContent);
    };
    check();ask(h,{...question,options:Array.from({length:30},(_,i)=>`Option ${i}`),default:"Option 0"});check();
    h.button("Send answer").click();h.message({...projectState,phase:"planning",clarify_request:null});check();
    h.message(readyState);check();h.button("Save map").click();h.message(readyState);h.button("Start expedition").click();
    h.message({kind:"approval_rejected",reason:"dirty",dirty_paths:Array.from({length:100},(_,i)=>`src/file-${i}.js`)});check();
    h.button("Cancel").click();h.message({kind:"approval_rejected",reason:"Cannot save"});check();
    const shell=styleRule(".mage-dialogue");assert.match(shell,/box-sizing:\s*border-box/);assert.match(shell,/max-height:\s*calc\(100vh - 64px\)/);assert.match(shell,/overflow:\s*hidden/);
    assert.match(styleRule(".dialogue-content"),/overflow:\s*auto/);assert.match(styleRule(".dialogue-content"),/min-height:\s*0/);
    assert.match(styleRule(".dialogue-actions"),/flex-shrink:\s*0/);
    for(const height of [540,720,1080])assert.ok(height-64<=height-32,"Dialogue stays below the title bar");
  }finally{h.screens.destroy();}
});
test("world_ready_bar_leaves_visible_castle_region_during_reveal_at_both_window_sizes",async()=>{
  const {mountMap}=await load("map/scene.js");const {layoutWorld}=await load("map/layout.js");
  for(const [width,height] of [[960,540],[1280,720]]) {
    const h=await entrance(),m=mapHarness();h.browser.innerWidth=width;h.browser.innerHeight=height;
    h.browser.matchMedia=m.browser.matchMedia;h.hud.getBoundingClientRect=()=>({width:260});
    const map=mountMap(m.Phaser,h.game,h.browser);try {
      m.create();h.message({...readyState,world:previewWorld});
      h.browser.dispatchEvent(new h.browser.CustomEvent("specvania:studio-state",{detail:{...readyState,world:previewWorld}}));
      const box=flat(h.host).find(n=>n.className==="mage-dialogue");assert.equal(box.dataset.mode,"world");
      const bar=styleRule(".mage-dialogue[data-mode=world]");assert.match(bar,/height:\s*72px/);assert.match(bar,/display:\s*grid/);
      assert.match(styleRule("#screen-host[data-screen=idea][data-dialogue=world] .screen-panel"),/width:\s*100%/);
      const camera=m.camera;assert.ok(camera.height>0);assert.ok(camera.y>=32);assert.ok(camera.y+camera.height<=height-72-16);
      const layout=layoutWorld(previewWorld);
      for(const room of layout.rooms) {
        const x=camera.x+camera.width/2+(room.x-camera.center[0])*camera.zoom;
        const y=camera.y+camera.height/2+(room.y-camera.center[1])*camera.zoom;
        assert.ok(x>=camera.x&&x+room.width*camera.zoom<=camera.x+camera.width);
        assert.ok(y>=camera.y&&y+room.height*camera.zoom<=height-72-16,"Chamber must appear above the bar");
      }
      assert.equal(h.host.hidden,false);assert.equal(h.hud.hidden,false);
      const cover=m.objects.findLast(n=>n.kind==="graphics"&&!n.destroyed&&n.commands.some(c=>c[2]===80&&c[3]===48));assert.equal(cover.commands.length,2);
      m.step();assert.equal(box.dataset.mode,"world");assert.equal(cover.commands.length,1);m.step();m.frame();assert.equal(cover.commands.length,0);
      h.button("Save map").click();h.message({...readyState,world:previewWorld});h.button("Start expedition").click();
      h.message({kind:"approval_rejected",reason:"dirty",dirty_paths:["src/file.js"]});h.button("Cancel").click();
      assert.equal(h.hud.hidden,false);assert.equal(h.hud.getAttribute("inert"),null);assert.equal(h.game.getAttribute("inert"),null);
      h.button("Start expedition").click();
      for(const phase of ["running","finished"]) {
        h.hud.hidden=true;h.message({...projectState,phase});h.browser.dispatchEvent(new h.browser.CustomEvent("specvania:studio-state",{detail:{phase}}));
        assert.equal(h.hud.hidden,false);assert.equal(h.hud.getAttribute("inert"),null);assert.deepEqual([camera.x,camera.y,camera.width,camera.height],[120,0,width-120,height]);
      }
    }finally{map.destroy();h.screens.destroy();}
  }
});
test("tall_planned_world_frames_first_floor_above_world_ready_bar",async()=>{
  const {mountMap}=await load("map/scene.js"),{layoutWorld}=await load("map/layout.js");
  const world={areas:[],rooms:[],doors:[],boss_ids:[]};
  for(let i=1;i<=12;i++) {
    world.areas.push({id:`A${i}`,title:`Floor ${i}`,boss_ids:[]});
    world.rooms.push({id:`T${String(i).padStart(2,"0")}`,title:"Room",area:`A${i}`,depends_on:[],state:"open"});
  }
  for(const [width,height]of [[960,540],[1280,720]]) {
    const m=mapHarness();m.browser.innerWidth=width;m.browser.innerHeight=height;const map=mountMap(m.Phaser,{},m.browser);try {
      m.create();m.emit("specvania:studio-state",{phase:"world_ready",world_slug:"tall",world});
      const room=layoutWorld(world).rooms[0],camera=m.camera;
      const top=camera.y+camera.height/2+(room.y-camera.center[1])*camera.zoom;
      assert.ok(top>=32&&top+room.height*camera.zoom<=height-88,"First floor must stay visible when the whole castle cannot fit");
    }finally{map.destroy();}
  }
});
