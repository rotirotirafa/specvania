"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const root = path.resolve(__dirname, "../../src/specvania/ui/static");
const load = name => import(pathToFileURL(path.join(root, name)));
const en = JSON.parse(fs.readFileSync(path.join(root, "i18n/en.json")));
const pt = JSON.parse(fs.readFileSync(path.join(root, "i18n/pt-BR.json")));
const placeholders = value => [...value.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort();

test("dictionaries_have_identical_keys_and_placeholders_and_glossary_terms", () => {
  assert.deepEqual(Object.keys(pt).sort(), Object.keys(en).sort());
  for (const key of Object.keys(en)) assert.deepEqual(placeholders(pt[key]), placeholders(en[key]), key);
  for (const [key, value] of Object.entries({"role.mage":"Mago", "role.warrior":"Guerreiro", "role.paladin":"Paladino",
    "bestiary.title":"Bestiário", "party.stamina":"Stamina", "status.cooldown":"Recarga"})) assert.equal(pt[key].toLocaleLowerCase("pt-BR"), value.toLocaleLowerCase("pt-BR"));
  for (const [key, value] of Object.entries(pt)) {
    if (/^(status|event)\./.test(key) && key !== "status.clarify") assert.equal(value[0], value[0].toLocaleLowerCase("pt-BR"), key);
    assert.doesNotMatch(value, /\b(?:do|ao|o|seguir|de) (?:Mundo|Grupo)\b/, key);
  }
  assert.equal(pt["map.hint"], "Mapa do mundo · M: mapa completo / seguir o grupo");
  assert.equal(pt["dialogue.awaiting"], "O grupo aguarda a próxima sala.");
});

// Minimal lexer: inspect every quoted literal AND each template's static text;
// recurse through interpolations, skip comments and regex bodies. No npm parser.
function literals(source) {
  const found = []; let i = 0;
  function code(end = false) {
    let depth = 0;
    while (i < source.length) {
      const c = source[i];
      if (end && c === "}" && depth === 0) { i++; return; }
      if (c === "{" ) depth++;
      if (c === "}" ) depth--;
      if (source.startsWith("//", i)) { i = source.indexOf("\n", i); if (i < 0) i = source.length; continue; }
      if (source.startsWith("/*", i)) { const at = source.indexOf("*/", i + 2); i = at < 0 ? source.length : at + 2; continue; }
      if (c === '"' || c === "'" || c === "`") {
        const start = i++; let value = "";
        while (i < source.length) {
          if (source[i] === "\\") { value += source.slice(i, i + 2); i += 2; continue; }
          if (source[i] === c) { i++; break; }
          if (c === "`" && source.startsWith("${", i)) { value += "${}"; i += 2; code(true); continue; }
          value += source[i++];
        }
        found.push({value, start}); continue;
      }
      i++;
    }
  }
  code(); return found;
}

// Reviewed technical vocabulary. These are identifiers, not English UI prose.
const technical = new Set(`en pt-BR specvania.language specvania.settings text/javascript
  role dialog status progressbar meter img aria-label aria-pressed aria-valuemin aria-valuemax aria-valuenow aria-valuetext
  div section p h1 h2 h3 strong small span button canvas nav header style pre main 2d
  click message close error open resize pointerdown pointerup pointermove pointercancel wheel keydown-M change
  function boolean anywhere json pt Linear shake kenney-tiny-dungeon slime bat ghost spider rat red_crab hooded_figure coin stamina_pip V C P idle thinking acting cooldown running paused finished aborted locked active cleared failed skipped
  mage warrior paladin state event studio_state set_language approval_answer approval_rejected desktop-title-bar connection run-state
  view_token ws: /ws world-map castle-hud map-rooms party-hud bestiary-hud dialogue-hud approval-choice
  action finish pass fail walk act rest defeated escaped swing shield plan hit escape bossResult arena reveal defeat
  party torch padlock check cross cobweb castle boss monster door heart label zone
  wall_top_left wall_top_right wall_top_middle wall_left wall_right back_wall floor_grate floor_stones
  floor_plain banner wall_grate door_closed door_open_middle chest_open chest_closed stairs sword_long shield_round cyclops
  fixed fonts truetype inherit normal nowrap hidden ellipsis grid flex none no-drag drag solid pointer no-repeat pixelated
  rgba null zzz K S B W Y O G R N input output diffuse
  data-i18n data-i18n-aria [data-i18n] [data-i18n-aria] .webp .png
  art`.split(/\s+/));
technical.add("waiting for player");
// T33 DOM/input identifiers, screen names and control protocol values only.
for (const value of `error.start screen-host missingKey inert aria-hidden screen-panel title-castle title-menu title-footer footer absolute true Tab settings init_project probe_party project_open world_ready ok timeout number select option checkbox text title specvania string project open_project probe_result start_run Escape keydown`.split(/\s+/)) technical.add(value);

// T34 DOM identifiers, input keys, screen modes and studio message kinds only.
for (const value of `dirty dialogue-content dialogue-actions dialogue-speaker mage-dialogue aria-modal block aria-selected textarea auto world working idea start_plan clarify listbox save_world planning Enter ArrowDown ArrowUp door_double_left door_double_right`.split(/\s+/)) technical.add(value);

const exact = new Set([
  '"Press Start 2P", monospace', "SpecvaniaPixel, monospace", "@font-face { font-family: SpecvaniaPixel; src: url(\"./vendor/fonts/PressStart2P-Regular.ttf\"); }",
  "repeat(3, minmax(0, 1fr))", "32px minmax(0, 1fr)", "(prefers-reduced-motion: reduce)", "${}:idle:0", "${}:boss", "\\n", "${}:${}:${}",
  "${}px", "${}%", "${} ${}", "${}\\n${}", "${}:", "${} ", "2px solid ${}", "4px solid ${}", "4px 4px ${}",
  "--${}", "Missing translation: ${}/${}", "role.${}", "status.${}", "room.${}", "event.${}", "responder.${}",
  ".", "heart:idle:0", "monster:${}", "boss:${}", "♥", "♡", "■", "□", "pre-wrap",
]);
// Technical CSS only: reviewed T33 screen styles, without text content.
exact.add(`#screen-host { position: fixed; inset: 32px 0 0; z-index: 3; overflow: auto; box-sizing: border-box; background: \${}; color: \${}; padding: 16px 24px; font: 12px/1.8 "Press Start 2P", monospace; display: flex; align-items: center; justify-content: center; }
    #screen-host[hidden] { display: none; }
    #screen-host .screen-panel { width: 720px; max-width: 100%; margin: auto; text-align: center; }
    #screen-host button, #screen-host input, #screen-host select { box-sizing: border-box; font: inherit; color: \${}; background: \${}; border: 2px solid \${}; padding: 12px; }
    #screen-host button { cursor: pointer; min-height: 48px; margin: 6px; } #screen-host button:disabled { opacity: 0.6; cursor: default; }
    #screen-host :focus-visible { outline: 3px solid \${}; outline-offset: 2px; }
    #screen-host label { display: grid; grid-template-columns: 1fr 1fr; align-items: center; gap: 16px; text-align: left; margin: 12px 0; }
    #screen-host input, #screen-host select { width: 100%; min-width: 0; } #screen-host input[type=checkbox] { width: 24px; height: 24px; }
    #screen-host h1 { color: \${}; font-size: 28px; line-height: 1.4; margin: 0 0 16px; }
    #screen-host[data-screen=title] h1 { font-size: 36px; margin-bottom: 8px; }
    #screen-host h2 { font-size: 16px; } #screen-host p { overflow-wrap: anywhere; }
    #screen-host .title-castle { position: relative; margin: 0 auto 12px; }
    #screen-host .title-menu { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    #screen-host .title-menu button { margin: 0; min-height: 52px; }
    #screen-host .title-footer { margin-top: 16px; font-size: 8px; } #screen-host .title-footer p { margin: 4px 0 0; }
    #screen-host .title-footer button { min-height: 32px; padding: 8px 16px; margin: 0 6px; }`);
exact.add("button:not(:disabled), input, select");
exact.add("\\0");
exact.add('url("${}")');
exact.add("${}px ${}px");
const diagnostics = {
  "screens/title.js": ["API key missing", "git work tree"], // Match non-secret studio errors, never render these fragments.
  "hud/room-panel.js": ["Repository unavailable for this view.", "Room unavailable in the current world."],
  "map/castle.js": ["Cyclic room doors", "Door has an unknown room"],
  "pixel/cast.js": ["Invalid area id: ${}", "Art: Kenney Tiny Dungeon · CC0 1.0"], // CREDIT is consumed only by the allowlisted developer gallery until T33.
  "pixel/textures.js": ["Kenney sheet is missing; call preloadTextures in preload()", "Unknown cast entry: ${}", "Could not create tile texture: ${}", "Invalid 16x16 sprite frame: ${}", "Could not create sprite texture: ${}", "Unknown palette symbol in ${}: ${}"],
};
// T35 technical identifiers only; prose remains rejected by the same scan.
const panelTechnical = new Set(`python javascript typescript markdown ini yaml html css shell plaintext
  /vendor/monaco/worker.js /vendor/monaco/vs vs/nls vs/editor/editor.main script /vendor/monaco/vs/loader.js
  same-origin vscode-worker-ready files room-panel aria-modal true border-box column room-panel-title aria-labelledby room-panel-content
  tabpanel auto details summary tablist mission code diff tab aria-controls keydown ArrowRight ArrowLeft Home End
  aria-selected specvania vs-dark editor.background editor.foreground editorLineNumber.foreground
  editor.selectionBackground diffEditor.insertedLineBackground diffEditor.removedLineBackground off on select option
  65vh visible Escape Tab wrap`.split(/\s+/));
for (const value of ["-", "+", " ", "*", "diff --git", "/api/rooms/${}/code", "translateX(100%)", "translateX(0)",
  "room-panel-${}", "transform 160ms", "panel.${}", "button, select, [tabindex]"]) panelTechnical.add(value);
// T36 DOM/CSS/input/protocol identifiers; engine reason templates are matched
// only as diagnostics and rendered exclusively through translated templates.
const expeditionTechnical = new Set(`boss-hp transparent end end-metrics tokens_in tokens_out calls fallbacks latency
  e end-commits expedition-dialog expedition-content expedition-actions ArrowLeft ArrowRight pause retry abort skip pause_decision`.split(/\s+/));
for (const value of ["expedition-dialog boss-dialog", ".${}", "end.${}", "384px 352px", "pause.${}Help", "pause.${}"]) expeditionTechnical.add(value);
diagnostics["screens/pause.js"] = ["stamina exhausted", "model chain exhausted", "boss failed"];
function approved(value, file) {
  return !value || Object.hasOwn(en, value) || Object.hasOwn(en, `event.${value}`) || technical.has(value) || file === "hud/room-panel.js" && panelTechnical.has(value) || /^(screens\/(pause|boss|end)\.js)$/.test(file) && expeditionTechnical.has(value) || exact.has(value) || diagnostics[file]?.includes(value)
    || /^specvania:[a-z-]+$/.test(value) // internal DOM event names
    || /^(\.\.\/|\.\/)[\w/.-]+$/.test(value) // local imports/assets only
    || /^\d+(px|%|px 0)?$/.test(value) || /^\d+(\.\d+)?$/.test(value) // CSS sizes and numeric formats
    || /^[#][\da-fA-F]{6}$/.test(value) // palette data
    || /^[a-z]+_[a-z_]+$/.test(value) && (Object.hasOwn(en, `event.${value}`) || value === "kenney_tiny_dungeon")
    || file === "pixel/palette.js" && /^A\d+$/.test(value)
    || file === "pixel/sprites.js" && (/^room_a\d+$/.test(value) || /^[KSBWYOGRN.VCP]{16}$/.test(value)); // pixel rows/symbols
}
function scanJS(source, file) {
  return literals(source).filter(({value, start}) => {
    // Technical ids cannot be used as direct visible labels merely because they
    // are also status/event keys. Check common sinks independently of allowlist.
    const before = source.slice(Math.max(0, start - 100), start);
    const visible = /(?:textContent\s*=|(?:label|text)\([^\n]*,|setAttribute\("aria-label",)\s*$/.test(before);
    return (!approved(value, file) || visible && (technical.has(value) || expeditionTechnical.has(value) || diagnostics[file]?.includes(value)) && !["aria-label", "p", "h2", "h3", "div", "button", "span", "strong", "small"].includes(value));
  }).map(({value}) => value);
}
function walk(directory) {
  return fs.readdirSync(directory, {withFileTypes: true}).flatMap(entry => entry.isDirectory() ? walk(path.join(directory,entry.name)) : [path.join(directory,entry.name)]);
}
test("static_JS_and_HTML_have_no_untranslated_visible_literals", () => {
  const failures = [];
  for (const filename of walk(root)) {
    const file = path.relative(root, filename).replaceAll(path.sep, "/");
    if (file.startsWith("vendor/") || file === "sprites.html" || !/\.(js|html)$/.test(file)) continue; // Vendored source and developer gallery only.
    let source = fs.readFileSync(filename, "utf8");
    if (file.endsWith("html")) {
      for (const script of source.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)) failures.push(...scanJS(script[1], file).map(value=>`${file}: ${value}`));
      source = source.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/g, "").replace(/<!--[\s\S]*?-->/g, "");
      for (const match of source.matchAll(/>([^<]+)</g)) if (match[1].trim()) failures.push(`${file}: text ${match[1].trim()}`);
      for (const match of source.matchAll(/\b(?:aria-label|title|placeholder)="([^"]+)"/g)) failures.push(`${file}: attribute ${match[1]}`);
    } else failures.push(...scanJS(source,file).map(value=>`${file}: ${value}`));
  }
  assert.deepEqual(failures, []);
  // T36's new protocol identifiers are never accepted as direct UI labels.
  assert.ok(scanJS('node.textContent = "abort";', "screens/pause.js").length);
  assert.ok(scanJS('node.textContent = "stamina exhausted";', "screens/pause.js").length);
  // Prove the scan rejects prose, single words and technical keys in text sinks.
  for (const source of ['const hint = "World map · M";', 'node.textContent = "running";', 'label(document, "h2", "Mage");', 'text("p", `Oops ${id}`);']) assert.ok(scanJS(source,"main.js").length, source);
});

class Element {
  constructor(tag) { this.tagName = tag; this.style = {}; this.children = []; this.attrs = {}; this.dataset = {}; this.listeners = {}; this.textContent = ""; }
  append(...children) { for (const child of children) { child.remove(); child.parent = this; this.children.push(child); } }
  prepend(child) { child.parent = this; this.children.unshift(child); }
  replaceChildren(...children) { for (const child of this.children) child.parent = null; this.children = []; this.append(...children); }
  remove() { if (this.parent) { this.parent.children = this.parent.children.filter(child => child !== this); this.parent = null; } }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  getContext() { return {drawImage: (...args) => { this.draw = args; }, set imageSmoothingEnabled(value) { assert.equal(value, false); }}; }
  addEventListener(key, fn) { this.listeners[key] = fn; }
  click() { this.listeners.click?.(); }
  focus() {}
  getAttribute(key) {return this.attrs[key] ?? null;}
  removeAttribute(key) {delete this.attrs[key];}
  set innerHTML(_) { assert.fail("HTML injection"); }
}
const flatten = node => [node, ...node.children.flatMap(flatten)];
const texts = node => flatten(node).map(node => node.textContent).filter(Boolean).join("\n");
function harness(desktop) {
  const events = new Map(), socketEvents = new Map(), sent = [], body = new Element("body"), head = new Element("head");
  const host = new Element("main"); body.append(host);
  const ids = Object.fromEntries(["connection","run-state","map-rooms"].map(id => [id, new Element("p")]));
  for (const node of Object.values(ids)) host.append(node);
  const document = {body, head, documentElement: new Element("html"), createElement: tag => new Element(tag),
    getElementById: id => ids[id], querySelectorAll: selector => flatten(body).filter(node => selector === "[data-i18n]" ? node.dataset.i18n : node.dataset.i18nAria)};
  const browser = {document, navigator: {language: "en"}, specvaniaDesktop: desktop, performance: {now: () => 0},
    location: {href:"http://127.0.0.1:8765/?view_token=watch", search:"?view_token=watch"},
    addEventListener(key, fn) { const list = events.get(key) || []; list.push(fn); events.set(key,list); },
    removeEventListener(key, fn) { events.set(key,(events.get(key)||[]).filter(item=>item!==fn)); },
    dispatchEvent(event) { for (const fn of events.get(event.type)||[]) fn(event); }, cancelAnimationFrame() {}, requestAnimationFrame() {return 1;}};
  const socket = {readyState: 1, addEventListener(key, fn) { const list=socketEvents.get(key)||[]; list.push(fn); socketEvents.set(key,list); },
    send(data) {sent.push(JSON.parse(data));}};
  return {document, browser, host, body, ids, sent, socket, emit(type, detail) {browser.dispatchEvent({type,detail});},
    message(value) {for (const fn of socketEvents.get("message")||[]) fn({data:JSON.stringify(value)});},
    socketEvent(name) {for (const fn of socketEvents.get(name)||[]) fn();}};
}

test("language_source_storage_fallback_and_inert_placeholders", async () => {
  const {createI18n} = await load("i18n/index.js");
  for (const [browser, expected] of [
    [{navigator:{language:"pt-PT"}},"pt-BR"], [{navigator:{language:"fr"}},"en"],
    [{navigator:{language:"pt"},specvaniaDesktop:{settings:{language:"en"}}},"en"],
    [{navigator:{language:"en"},specvaniaDesktop:{settings:{language:"bad"}}},"en"],
    [{navigator:{language:"en"},localStorage:{getItem(){throw Error();},setItem(){throw Error();}}},"en"],
    [{navigator:{language:"en"},localStorage:{getItem:()=>"pt-BR"}},"pt-BR"],
  ]) { const service=createI18n(browser); assert.equal(service.language,expected); assert.equal(service.setLanguage("pt-BR"),true); }
  const saved = new Map(), browser = {localStorage:{getItem:key=>saved.get(key),setItem:(key,value)=>saved.set(key,value)}};
  const service=createI18n(browser); service.setLanguage("pt-BR"); assert.equal(createI18n(browser).language,"pt-BR");
  assert.equal(service.setLanguage("es"),false); assert.equal(service.language,"pt-BR");
  assert.equal(service.t("approval.cwd",{cwd:"<img src=x onerror=evil>{name}"}),"Diretório: <img src=x onerror=evil>{name}");
  const warnings=[]; const missing=createI18n({navigator:{language:"pt"},console:{warn:text=>warnings.push(text)}},{en:{key:"Fallback {name}"},"pt-BR":{}});
  assert.equal(missing.t("key",{name:"safe"}),"Fallback safe"); assert.equal(warnings.length,1);
});

test("set_language_is_sent_only_by_a_controlling_desktop_in_studio_and_server_language_has_no_echo", async () => {
  const {createI18n,connectLanguage} = await load("i18n/index.js");
  for (const [desktop, expected] of [[undefined,0],[{controlToken:()=>""},0],[{controlToken:()=>"control"},1]]) {
    const h=harness(desktop), service=createI18n(h.browser); connectLanguage(h.socket,h.browser,service);
    service.setLanguage("pt-BR",{manual:true}); assert.equal(h.sent.length,0,"A CLI viewer is not a studio");
    h.message({kind:"studio_state",language:"en"}); assert.equal(service.language,"en"); assert.equal(h.sent.length,0);
    service.setLanguage("pt-BR",{manual:true}); assert.equal(h.sent.length,expected);
    if (expected) assert.deepEqual(h.sent[0],{kind:"set_language",language:"pt-BR",control_token:"control"});
    h.socket.readyState=3; service.setLanguage("en",{manual:true}); assert.equal(h.sent.length,expected);
    h.message({kind:"studio_state",language:"pt-BR"}); assert.equal(service.language,"pt-BR"); assert.equal(h.sent.length,expected);
  }
});

test("switch_updates_static_transport_map_party_bestiary_dialogue_and_approval_text_without_reload", async () => {
  const {setLanguage,bindDocument,i18n} = await load("i18n/index.js");
  setLanguage("en");
  const h=harness({controlToken:()=>"control",notify(){}});
  let reloads=0; h.browser.location.reload=()=>reloads++;
  // The real HTML declares its labels; the fake DOM mounts those declarations.
  const html=fs.readFileSync(path.join(root,"index.html"),"utf8");
  for (const match of html.matchAll(/data-i18n(-aria)?="([^"]+)"/g)) {
    const node=new Element("p"); node.dataset[match[1] ? "i18nAria" : "i18n"]=match[2]; h.host.append(node);
  }
  const unbind=bindDocument(h.document);
  const {mountParty}=await load("hud/party.js"), {mountBestiary}=await load("hud/bestiary.js"), {mountDialogue}=await load("hud/dialogue.js");
  const mounts=[mountParty(h.host,h.browser),mountBestiary(h.host,h.browser),mountDialogue(h.host,h.browser)];
  const {mountMap}=await load("map/scene.js");
  h.browser.matchMedia=()=>({matches:true,addEventListener(){},removeEventListener(){}});
  h.browser.innerWidth=960; h.browser.innerHeight=540;
  let config; const object=()=>({destroy(){},setVisible(){},setAlpha(){},setPosition(){},setTexture(){},setInteractive(){},on(){}});
  const graphics=()=>({destroy(){},fillStyle(){return this;},fillRect(){return this;}});
  const camera={stopFollow(){},setZoom(){},setBounds(){},centerOn(){}};
  const scene={textures:{exists:()=>true,get:()=>undefined},input:{keyboard:{on(){}}},cameras:{main:camera},
    add:{image:object,text:object,zone:object,graphics},children:{bringToTop(){}},tweens:{killTweensOf(){}}};
  const Phaser={CANVAS:1,Scale:{NONE:0},Game:class{constructor(value){config=value;this.scale={resize(){}};}destroy(){}}};
  const map=mountMap(Phaser,{},h.browser);
  const originalGet=h.document.getElementById;
  // The camera measures an actual HUD width; the projection updates map labels.
  h.host.getBoundingClientRect=()=>({width:260});
  h.document.getElementById=id=>id==="castle-hud" ? h.host : originalGet(id);
  global.window=h.browser; global.document=h.document;
  global.CustomEvent=class {constructor(type,options){Object.assign(this,{type,...options});}};
  h.browser.CustomEvent=global.CustomEvent;
  global.WebSocket=class {constructor(){return h.socket;}};
  try {
    await import(`${pathToFileURL(path.join(root,"main.js"))}?i18n-test`);
    h.socketEvent("open");
    h.message({kind:"studio_state",phase:"running",language:"en"});
    const room={id:"T01",area:"A1",title:"User title",state:"active",depends_on:[],boss_ids:["S1"]};
    const state={run_id:"run",status:"running",token_budget:200000,tokens_spent:100000,tokens_spent_by_role:{warrior:90000},rooms:[room],
      areas:[{id:"A1",title:"User area",boss_ids:["S1"]}],party:[{role:"warrior",model:"full/model-id",status:"thinking"}],
      bugs:[{bug_id:"bug-1",room_id:"T01",title:"<img>external</img>",status:"open"}],bosses:[{area_id:"A1",scenarios:["S1"],results:[]}]};
    h.message({kind:"state",state}); config.scene.create.call(scene);
    setLanguage("pt-BR"); assert.ok(texts(h.body).includes("pensando")); setLanguage("en");
    const event={type:"approval_requested",seq:9,request_id:"request",command:"<script>inert</script>",cwd:"/tmp/world"};
    h.message({kind:"event",event});
    const choices=()=>flatten(h.body).filter(node=>node.tagName==="button" && ["Allow","Deny","Permitir","Negar"].includes(node.textContent));
    const originalChoices=choices();
    flatten(h.host).find(node=>node.tagName==="button" && node.textContent==="PT").click();
    for (const label of ["Acompanhando a execução","Evento 9: aprovação solicitada","Mapa do mundo","Salas e estados","Grupo","Mago","Guerreiro","Paladino","90000 tokens gastos","Bestiário","Chefe de A1","Diálogo","O Guerreiro pede aprovação","Permitir","Negar","Diretório: /tmp/world","aguardando o jogador"]) {
      // Party has no visible heading: its accessible name carries Grupo.
      assert.ok(texts(h.body).includes(label) || flatten(h.body).some(node=>Object.values(node.attrs).some(value=>value.includes(label))),label);
    }
    assert.match(h.ids["map-rooms"].textContent,/User title — ativa/);
    assert.deepEqual(choices(),originalChoices,"Switching preserves focusable buttons and pending request");
    assert.ok(texts(h.body).includes("<script>inert</script>")); assert.ok(texts(h.body).includes("<img>external</img>"));
    assert.equal(reloads,0); assert.equal(h.document.documentElement.lang,"pt-BR");
    assert.deepEqual(h.sent,[{kind:"set_language",language:"pt-BR",control_token:"control"}]);
    flatten(h.host).find(node=>node.tagName==="button" && node.textContent==="EN").click();
    assert.deepEqual(h.sent.at(-1),{kind:"set_language",language:"en",control_token:"control"});
    assert.match(h.ids["map-rooms"].textContent,/User title — active/);
    for (const label of ["Watching the run","Event 9: approval requested","Mage","Warrior","Paladin","Bestiary","Allow","Deny"]) assert.ok(texts(h.body).includes(label),label);
    // Every built-in label is rerendered back to the starting language.
    assert.ok(!/Guerreiro|Paladino|Bestiário|Permitir|Negar|aguardando/.test(texts(h.body)));
    for (const [message,key] of [[{kind:"studio_state",phase:"project_open",language:"pt-BR"},"status.project_open"], [{kind:"state",state:{run_id:"r",rooms:[],status:"paused"}},"status.paused"]]) {
      h.message(message); assert.ok(h.ids["run-state"].textContent.includes(pt[key]));
    }
    h.socketEvent("close"); assert.equal(h.ids.connection.textContent,pt["connection.closed"]);
    setLanguage("en"); assert.equal(h.ids.connection.textContent,en["connection.closed"]);
    h.socketEvent("error"); setLanguage("pt-BR"); assert.equal(h.ids.connection.textContent,pt["connection.unavailable"]);
    assert.equal(reloads,0);
  } finally {
    unbind(); mounts.forEach(m=>m.destroy()); map.destroy();
    setLanguage("en"); delete global.window; delete global.document; delete global.CustomEvent; delete global.WebSocket;
  }
});

test("rendered_stamina_role_tokens_portrait_tiles_and_sourced_coins_match_RunState", async () => {
  const {setLanguage}=await load("i18n/index.js"); setLanguage("en");
  const {mountParty}=await load("hud/party.js"); const {CAST}=await load("pixel/cast.js");
  const h=harness(), mounted=mountParty(h.host,h.browser);
  const sources=new Map(["mage","warrior","paladin"].map(role=>[`${role}:idle:0`,{tile:CAST[role]}]));
  h.emit("specvania:textures-ready",{get:key=>({getSourceImage:()=>sources.get(key)})});
  const model="vendor/very-long-model-token-that-must-not-wrap";
  const state={lives:3,token_budget:200000,tokens_spent:100000,tokens_spent_by_role:{mage:10000,warrior:90000},
    party:[{role:"warrior",model,status:"thinking"}],models:[{model,tokens_in:50000,tokens_out:40000}],
    prices:[{model,input_per_mtok:"2",output_per_mtok:"4",currency:"USD",source_url:"https://example.test/prices"}]};
  h.emit("specvania:state",state);
  const meter=()=>flatten(h.host).filter(node=>node.attrs.role==="progressbar");
  assert.equal(meter().length,1); assert.equal(meter()[0].attrs["aria-valuemax"],"200000"); assert.equal(meter()[0].attrs["aria-valuenow"],"100000");
  assert.equal(meter()[0].attrs["aria-valuetext"],"100000 / 200000 tokens"); assert.equal(meter()[0].children[0].style.width,"50%");
  for (const expected of ["10000 tokens spent","90000 tokens spent","0.260000 USD coins"]) assert.ok(texts(h.host).includes(expected),expected);
  assert.ok(flatten(h.host).some(node=>node.title==="Price source: https://example.test/prices" && node.textContent==="Source"));
  for (const [index,portrait] of flatten(h.host).filter(node=>node.tagName==="canvas").entries()) {
    const role=["mage","warrior","paladin"][index]; assert.equal(portrait.width,32); assert.equal(portrait.height,32);
    assert.equal(portrait.style.imageRendering,"pixelated"); assert.deepEqual(portrait.draw,[{tile:CAST[role]},0,0,16,16,0,0,32,32]);
  }
  const name=flatten(h.host).find(node=>node.textContent===model); assert.equal(name.title,model); assert.equal(name.style.whiteSpace,"nowrap"); assert.equal(name.style.textOverflow,"ellipsis");
  h.emit("specvania:event",{type:"cooldown",role:"warrior",seconds:5}); assert.equal(meter()[0].attrs["aria-valuenow"],"100000");
  h.emit("specvania:event",{type:"model_call",role:"paladin",model:"p",tokens_in:100000,tokens_out:0}); assert.equal(meter()[0].attrs["aria-valuenow"],"200000");
  h.emit("specvania:event",{type:"stamina_restored",token_budget:250000}); assert.equal(meter()[0].attrs["aria-valuetext"],"200000 / 250000 tokens");
  h.emit("specvania:state",{...state,token_budget:null,prices:state.prices.map(({source_url,...price})=>price)});
  assert.equal(meter().length,0); assert.ok(texts(h.host).includes("100000 tokens")); assert.ok(!texts(h.host).includes("coins")); assert.ok(!texts(h.host).includes("Stamina"));
  h.emit("specvania:state",{models:[{model:"legacy",tokens_in:25,tokens_out:30}]}); assert.ok(texts(h.host).includes("55 tokens"));
  assert.equal(meter().length,0); mounted.destroy();
});

// TrueType cmap formats 4 (BMP) and 12 (full Unicode), using Node Buffer only.
function fontHas(buffer) {
  let cmap;
  for (let i=0;i<buffer.readUInt16BE(4);i++) {
    const at=12+i*16; if (buffer.toString("ascii",at,at+4)==="cmap") cmap=buffer.readUInt32BE(at+8);
  }
  assert.ok(cmap,"vendored font has a cmap"); const maps=[];
  for (let i=0;i<buffer.readUInt16BE(cmap+2);i++) {
    const at=cmap+4+i*8, platform=buffer.readUInt16BE(at), encoding=buffer.readUInt16BE(at+2);
    if (platform===0 || platform===3 && [1,10].includes(encoding)) maps.push(cmap+buffer.readUInt32BE(at+4));
  }
  return code=>maps.some(at=>{
    const format=buffer.readUInt16BE(at);
    if (format===12) {
      for (let i=0;i<buffer.readUInt32BE(at+12);i++) { const group=at+16+i*12, start=buffer.readUInt32BE(group),end=buffer.readUInt32BE(group+4);
        if (code>=start && code<=end) return buffer.readUInt32BE(group+8)+code-start!==0; }
    }
    if (format===4 && code<=65535) {
      const count=buffer.readUInt16BE(at+6)/2, endAt=at+14, startAt=endAt+count*2+2,deltaAt=startAt+count*2,offsetAt=deltaAt+count*2;
      for (let i=0;i<count;i++) if (code>=buffer.readUInt16BE(startAt+i*2) && code<=buffer.readUInt16BE(endAt+i*2)) {
        const delta=buffer.readInt16BE(deltaAt+i*2),offset=buffer.readUInt16BE(offsetAt+i*2);
        if (!offset) return ((code+delta)&65535)!==0;
        const glyph=buffer.readUInt16BE(offsetAt+i*2+offset+2*(code-buffer.readUInt16BE(startAt+i*2)));
        return glyph!==0 && ((glyph+delta)&65535)!==0;
      }
    }
    return false;
  });
}
test("vendored_font_cmap_covers_every_Portuguese_character", () => {
  const has=fontHas(fs.readFileSync(path.join(root,"vendor/fonts/PressStart2P-Regular.ttf")));
  const characters=new Set(Object.values(pt).join(""));
  for (const char of characters) if (char!=="\n") assert.ok(has(char.codePointAt(0)), `${char} U+${char.codePointAt(0).toString(16)}`);
});
