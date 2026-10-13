import assert from "node:assert/strict";
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const { events, snapshots } = JSON.parse(fs.readFileSync(0, "utf8"));
const base = pathToFileURL(`${process.cwd()}/src/specvania/ui/static/`);
const check = process.argv[2];
const { MONSTERS, monsterForBug } = await import(new URL("pixel/cast.js",base));
// DOM/Phaser boundaries are faked; projections and mounting code are real.
class Element {
  constructor(tag) { this.tagName = tag; this.style = {}; this.children = []; this.attrs = {}; this.listeners = {}; this.textContent = ""; }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  prepend(child) { child.parent = this; this.children.unshift(child); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  focus() {}
}
function flatten(node) { return [node, ...node.children.flatMap(flatten)]; }
function texts(node) { return flatten(node).map(node => node.textContent).join("\n"); }
function browser(reduced = false) {
  const handlers = new Map(), frames = new Map();
  let next = 0, elapsed = 0;
  const body = new Element("body"), head = new Element("head");
  const host = new Element("main"); body.append(host);
  const description = new Element("p");
  const document = { body, head, createElement: tag => new Element(tag),
    getElementById: id => id === "map-rooms" ? description : new Element("p"), querySelector: () => host };
  const media = { matches: reduced, addEventListener(_, fn) { this.change = fn; }, removeEventListener() {} };
  const win = { document, location:{href:"http://127.0.0.1:8765/?view_token=view",search:"?view_token=view"}, innerHeight: 720, matchMedia: () => media,
    addEventListener(name, fn) { const list = handlers.get(name) || []; list.push(fn); handlers.set(name, list); },
    removeEventListener(name, fn) { handlers.set(name, (handlers.get(name) || []).filter(item => item !== fn)); },
    requestAnimationFrame(fn) { frames.set(++next, fn); return next; }, cancelAnimationFrame(id) { frames.delete(id); },
    performance: { now: () => elapsed },
  };
  const dispatch = (name, detail) => { for (const fn of handlers.get(name) || []) fn({detail}); };
  win.dispatchEvent = event => dispatch(event.type,event.detail);
  return { win, document, host, media, frames, handlers, description,
    event: event => dispatch("specvania:event", event), state: state => dispatch("specvania:state", state),
    tick(ms) { elapsed = ms; const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn(ms)); } };
}
function phaserScene() {
  const images = [], tweens = [], keyboard = new Map(), displayList=[];
  const object = (x, y, key) => ({ x, y, key, alpha: 1, destroyed: false,
    setInteractive() { return this; }, on() { return this; },
    setTexture(key) { this.key = key; return this; }, setVisible(value) { this.visible = value; return this; },
    setPosition(x, y) { this.x = x; this.y = y; return this; }, setAlpha(value) { this.alpha = value; return this; },
    destroy() { this.destroyed = true; } });
  const graphics = { fillStyle() { return this; }, lineStyle() { return this; }, fillRect() { return this; },
    strokeRect() { return this; }, lineBetween() { return this; }, destroy() {} };
  const camera = { stopFollow() {}, startFollow() {}, setZoom() {}, setBounds() {}, setScroll() {}, centerOn() {} };
  const scene = { cameras: { main: camera }, textures: {exists: () => true},
    input: { keyboard: { on: (name, fn) => keyboard.set(name, fn) } },
    children:{bringToTop(sprite) {const i=displayList.indexOf(sprite);if(i>=0) displayList.splice(i,1);displayList.push(sprite);}},
    add: { graphics: () => {const item={...graphics};displayList.push(item);return item;}, text: object, zone: object,
      image(x, y, key) { const image = object(x, y, key); images.push(image); displayList.push(image); return image; } },
    tweens: { add(options) { const tween = {options, stopped: false, stop() { this.stopped = true; } }; tweens.push(tween); return tween; },
      killTweensOf(target) { for (const tween of tweens) if (tween.options.targets === target) tween.stop(); } },
  };
  return { scene, images, tweens, keyboard, displayList,
    finish() { for (const tween of tweens) if (!tween.stopped) {
      tween.stopped = true; const opts = tween.options;
      for (const key of ["x", "y", "alpha"]) if (typeof opts[key] === "number") opts.targets[key] = opts[key];
      opts.onComplete?.();
    } } };
}
const { UI_COLORS } = await import(new URL("pixel/palette.js", base));
function palettePanel(panel) {
  assert.equal(panel.style.color, UI_COLORS.text);
  assert.equal(panel.style.backgroundColor, UI_COLORS.surface);
  assert.match(panel.style.fontFamily, /Press Start 2P/);
}

if (["statuses", "fallback", "metrics", "integration"].includes(check)) {
  const { partyFromState, projectParty, partyView, mountParty, eventStatuses } = await import(new URL("hud/party.js", base));
  if (check === "statuses") {
    // Independent table catches wrong role, status, or dropped event mapping.
    const table = [
      [{type:"room_entered"}, {warrior:"thinking",mage:"idle",paladin:"idle"}],
      [{type:"attempt_started"}, {warrior:"thinking"}],
      [{type:"respawn"}, {warrior:"thinking"}],
      [{type:"model_call",role:"mage"}, {mage:"thinking"}],
      [{type:"cooldown",role:"warrior",seconds:5}, {warrior:"cooldown"}],
      [{type:"fallback",role:"paladin"}, {paladin:"thinking"}],
      [{type:"action",action:"edit_file"}, {warrior:"acting"}],
      [{type:"action",action:"finish"}, {warrior:"idle"}],
      [{type:"tool_refused"}, {warrior:"thinking"}],
      [{type:"gate_result"}, {warrior:"idle",paladin:"thinking"}],
      [{type:"review"}, {warrior:"idle",paladin:"idle"}],
      [{type:"approval_requested"}, {warrior:"waiting for player"}],
      [{type:"approval_answered"}, {warrior:"acting"}],
      [{type:"clarify_question"}, {mage:"waiting for player"}],
      [{type:"clarify_answer"}, {mage:"thinking"}],
      [{type:"spec_written"}, {mage:"idle"}],
      [{type:"boss_started"}, {paladin:"thinking",mage:"idle",warrior:"idle"}],
      [{type:"boss_scenario"}, {paladin:"acting"}],
      [{type:"boss_result"}, {paladin:"idle"}],
      ...["room_cleared","room_failed","room_skipped"].map(type => [{type},{warrior:"idle"}]),
      [{type:"run_paused"}, {mage:"waiting for player",warrior:"waiting for player",paladin:"waiting for player"}],
      [{type:"run_finished"}, {mage:"idle",warrior:"idle",paladin:"idle"}],
      [{type:"bug_opened"}, {}],
    ];
    for (const [event, expected] of table) {
      assert.deepEqual(eventStatuses(event), expected);
      const view = partyView(projectParty(partyFromState({}), event, 0), 0);
      for (const [role,status] of Object.entries(expected)) assert.equal(view.members.find(member => member.role === role).status, status);
    }
    let state = projectParty(partyFromState({}), {type:"cooldown",role:"warrior",model:"w1",seconds:5}, 1000);
    assert.equal(partyView(state, 1000).members[1].countdown, 5);
    assert.equal(partyView(state, 3500).members[1].countdown, 3);
    assert.equal(partyView(state, 6000).members[1].status, "thinking");
    assert.equal(partyView(state, 6000).members[1].countdown, 0);
    const ui = browser(); const mounted = mountParty(ui.host, ui.win);
    ui.event({type:"cooldown",role:"warrior",model:"w1",seconds:5});
    assert.match(texts(ui.host), /cooldown · 5s/);
    ui.tick(2000); assert.match(texts(ui.host), /cooldown · 3s/);
    ui.tick(5000); assert.match(texts(ui.host), /Warrior.*thinking/s);
    ui.tick(10000); ui.event({type:"cooldown",role:"warrior",model:"w1",seconds:5});
    assert.match(texts(ui.host), /cooldown · 5s/); ui.tick(12000); assert.match(texts(ui.host), /cooldown · 3s/);
    mounted.destroy(); assert.equal(ui.frames.size, 0);
  }
  if (check === "fallback") {
    const ui = browser(); const mounted = mountParty(ui.host, ui.win);
    ui.event({type:"cooldown",role:"warrior",model:"w1",seconds:10});
    let meters = flatten(ui.host).filter(node => node.attrs.role === "progressbar");
    assert.equal(meters.length, 3); assert.ok(meters.every(node => node.attrs["aria-valuenow"] === "0"));
    ui.tick(5000); meters = flatten(ui.host).filter(node => node.attrs.role === "progressbar");
    assert.ok(meters.every(node => node.attrs["aria-valuenow"] === "50"));
    ui.event({type:"fallback",role:"warrior",from_model:"w1",to_model:"w2"});
    assert.match(texts(ui.host), /w2/); assert.doesNotMatch(texts(ui.host), /w1/);
    assert.ok(flatten(ui.host).filter(node => node.attrs.role === "progressbar").every(node => node.attrs["aria-valuenow"] === "100"));
    ui.state({party:[{role:"warrior",model:"w2",status:"cooldown",cooldown_seconds:5,cooldown_duration:10}]});
    assert.ok(flatten(ui.host).filter(node => node.attrs.role === "progressbar").every(node => node.attrs["aria-valuenow"] === "50"));
    palettePanel(flatten(ui.host).find(node => node.id === "party-hud"));
    mounted.destroy();
  }
  if (check === "metrics") {
    const ui = browser(); const mounted = mountParty(ui.host, ui.win);
    ui.event({type:"run_started", lives:5, prices:[{model:"w1",input_per_mtok:"2",output_per_mtok:"4",currency:"USD"}]});
    ui.event({type:"room_entered",room_id:"T01"});
    ui.event({type:"attempt_started",room_id:"T01",attempt:1});
    ui.event({type:"model_call",role:"warrior",model:"w1",tokens_in:1000000,tokens_out:500000});
    assert.match(texts(ui.host), /4\.000000 USD coins/);
    ui.event({type:"model_call",role:"mage",model:"m1",tokens_in:9,tokens_out:7});
    assert.match(texts(ui.host), /16 tokens/);
    ui.event({type:"review",room_id:"T01",verdict:"fail"});
    ui.event({type:"room_failed",room_id:"T01"});
    assert.ok(flatten(ui.host).filter(node => node.attrs["aria-label"] === "Lives: 4 of 5").length === 3);
    ui.event({type:"room_entered",room_id:"T01"});
    ui.event({type:"respawn",room_id:"T01",attempt:2});
    ui.event({type:"attempt_started",room_id:"T01",attempt:2});
    assert.equal(flatten(ui.host).filter(node => node.attrs["aria-label"] === "Lives: 4 of 5").length, 3);
    ui.event({type:"review",room_id:"T01",verdict:"fail"});
    assert.equal(flatten(ui.host).filter(node => node.attrs["aria-label"] === "Lives: 3 of 5").length, 3);
    ui.event({type:"attempt_started",room_id:"T01",attempt:1});
    assert.equal(flatten(ui.host).filter(node => node.attrs["aria-label"] === "Lives: 5 of 5").length, 3);
    ui.event({type:"room_entered",room_id:"T02"});
    assert.equal(flatten(ui.host).filter(node => node.attrs["aria-label"] === "Lives: 5 of 5").length, 3);
    ui.state({lives:2,prices:[],active_room:"T02",rooms:[{id:"T02",state:"failed",attempt:2}],party:[{role:"mage",model:"m1",status:"idle"}],models:[{model:"m1",tokens_in:9,tokens_out:7}]});
    assert.match(texts(ui.host), /16 tokens/); assert.doesNotMatch(texts(ui.host), /coins/);
    assert.equal(flatten(ui.host).filter(node => node.attrs["aria-label"] === "Lives: 0 of 2").length, 3);
    ui.state({}); assert.equal(flatten(ui.host).filter(node => node.attrs["aria-label"] === "Lives: 3 of 3").length, 3);
    mounted.destroy();
  }
}

if (check === "dialogue") {
  const { mountDialogue, dialogueFor } = await import(new URL("hud/dialogue.js", base));
  const ui = browser(); const mounted = mountDialogue(ui.host, ui.win);
  const action = {type:"action",thought:"<script>think</script>",action:"edit_file",args_summary:"room.txt"};
  ui.event(action); assert.match(texts(ui.host), /<script>think<\/script>/); assert.match(texts(ui.host), /edit_file: room.txt/);
  const panel = flatten(ui.host).find(node => node.id === "dialogue-hud"); palettePanel(panel);
  assert.equal(ui.host.children.at(-1), panel); // Bottom of HUD container.
  ui.event({type:"review",feedback:"Add real evidence"}); assert.match(texts(ui.host), /Paladin/); assert.match(texts(ui.host), /Add real evidence/);
  ui.event({type:"cooldown"}); assert.match(texts(ui.host), /Add real evidence/);
  ui.event({type:"approval_requested",command:"pytest -q",cwd:"/repo"});
  assert.match(texts(ui.host), /pytest -q/); assert.match(texts(ui.host), /waiting for player — answer in the terminal or desktop window/);
  ui.win.specvaniaDesktop = {controlToken: () => "control"};
  ui.event({type:"approval_requested",command:"pytest -q",cwd:"/repo"});
  assert.doesNotMatch(texts(ui.host), /answer in the terminal or desktop window/);
  ui.state({dialogue:{type:"approval_answered",answer:true,responder:"terminal"}});
  assert.match(texts(ui.host), /approved in terminal/); assert.doesNotMatch(texts(ui.host), /waiting for player/);
  ui.state({dialogue:action}); assert.match(texts(ui.host), /<script>think<\/script>/);
  assert.equal(dialogueFor({type:"bug_opened"}), null);
  mounted.destroy();
}

if (["boss", "bugs", "motion", "integration"].includes(check)) {
  const { bestiaryFromState, projectBestiary, openBugs, bossSegments, mountBestiary } = await import(new URL("hud/bestiary.js", base));
  const { createMapAnimations } = await import(new URL("map/animations.js", base));
  const { worldFromRooms, layoutWorld } = await import(new URL("map/layout.js", base));
  const payload = events.find(event => event.type === "world_loaded");
  const layout = layoutWorld(worldFromRooms(payload.rooms, payload.areas, payload.doors));
  if (check === "boss") {
    const ui = browser(); const mounted = mountBestiary(ui.host, ui.win);
    ui.event({type:"boss_started",area_id:"A1",scenarios:["S1","S2","S3"]});
    const hp = () => flatten(ui.host).find(node => node.attrs.role === "meter").attrs["aria-valuenow"];
    assert.equal(hp(), "3");
    ui.event({type:"boss_scenario",area_id:"A1",scenario:"S1",passed:true}); assert.equal(hp(), "2");
    ui.event({type:"boss_scenario",area_id:"A1",scenario:"S1",passed:true}); assert.equal(hp(), "2");
    ui.event({type:"boss_scenario",area_id:"A1",scenario:"S2",passed:false}); assert.equal(hp(), "2");
    ui.event({type:"boss_scenario",area_id:"A1",scenario:"other",passed:true}); assert.equal(hp(), "2");
    ui.event({type:"boss_scenario",area_id:"A1",scenario:"S2",passed:true}); assert.equal(hp(), "1");
    ui.event({type:"boss_result",area_id:"A1",passed:true}); assert.equal(hp(), "1");
    ui.event({type:"boss_started",area_id:"A1",scenarios:["S1","S2","S3"]}); assert.equal(hp(), "3");
    ui.state(snapshots[1]); assert.equal(hp(), "0");
    assert.deepEqual(bossSegments({scenarios:["S1","S2"],results:[{scenario:"S1",passed:true}]}).map(segment => segment.alive), [false,true]);
    mounted.destroy();
  }
  if (check === "bugs") {
    const ui = browser(); const mounted = mountBestiary(ui.host, ui.win);
    const map = createMapAnimations(ui.win); const fake = phaserScene();
    let state = bestiaryFromState({feature:"synthetic-demo"});
    const bug = events.find(event => event.type === "bug_opened");
    state = projectBestiary(state, bug); state = projectBestiary(state, bug);
    state = projectBestiary(state, {...bug,bug_id:"foreign",feature:"other"});
    assert.equal(openBugs(state).length, 1);
    ui.state({feature:"synthetic-demo",bugs:state.bugs});
    assert.match(texts(ui.host), /Bell wheel missing/); assert.doesNotMatch(texts(ui.host), /foreign/);
    map.setState({feature:"synthetic-demo",bugs:state.bugs}); map.render(fake.scene, layout, "T04");
    const monster = fake.images.find(image => image.key === `${monsterForBug(events.find(event => event.type === "bug_opened").bug_id)}:idle:0`); assert.ok(monster);
    const room = layout.rooms.find(room => room.id === "T04");
    assert.ok(monster.x > room.x && monster.x < room.x + room.width && monster.y > room.y && monster.y < room.y + room.height);
    const closed = events.find(event => event.type === "bug_closed");
    ui.event(closed); map.onEvent(closed); map.render(fake.scene,layout,"T04");
    assert.doesNotMatch(texts(ui.host), /Bell wheel missing/);
    assert.equal(monster.key, `${monsterForBug(events.find(event => event.type === "bug_opened").bug_id)}:defeated:0`); assert.equal(monster.destroyed,false);
    assert.equal(fake.tweens.at(-1).options.duration,400);
    assert.equal(fake.tweens.at(-1).options.targets.t,0);
    fake.finish(); assert.equal(monster.destroyed,true);
    ui.state(snapshots[2]); assert.match(texts(ui.host), /Bell wheel missing/);
    ui.state(snapshots[3]); assert.doesNotMatch(texts(ui.host), /Bell wheel missing/);
    ui.event({...bug,bug_id:"no-room",room_id:null}); assert.match(texts(ui.host), /no-room/);
    palettePanel(flatten(ui.host).find(node => node.id === "bestiary-hud"));
    map.destroy(); mounted.destroy();
  }
  if (check === "motion") {
    for (const reduced of [false,true]) {
      const ui = browser(reduced), map = createMapAnimations(ui.win), fake = phaserScene();
      map.render(fake.scene,layout,"T01");
      const party = fake.images.filter(image => image.key.startsWith("warrior:"))[0];
      map.onEvent({type:"room_entered",room_id:"T02"}); map.render(fake.scene,layout,"T02");
      if (!reduced) { assert.equal(party.key,"warrior:idle:0"); assert.equal(fake.tweens.length,3);
        const walk = fake.tweens[1];
        walk.options.targets.t=0.3; walk.options.onUpdate?.();
        assert.ok(Number.isInteger(party.x) && Number.isInteger(party.y));
        assert.ok(party.y < layout.rooms.find(room => room.id === "T02").y + 24); fake.finish(); }
      else assert.equal(fake.tweens.length,0);
      map.onEvent({type:"respawn",room_id:"T02"}); map.render(fake.scene,layout,"T02");
      const respawn = fake.tweens.filter(tween => !tween.stopped);
      assert.equal(respawn.length,reduced ? 0 : 1);
      fake.finish();
      assert.equal(party.x,layout.rooms.find(room => room.id === "T02").x + 8);
      const bug = events.find(event => event.type === "bug_opened");
      map.onEvent(bug); map.render(fake.scene,layout,"T02");
      const monster = fake.images.find(image => image.key === `${monsterForBug(events.find(event => event.type === "bug_opened").bug_id)}:idle:0`);
      map.onEvent(events.find(event => event.type === "bug_closed")); map.render(fake.scene,layout,"T02");
      assert.equal(monster.destroyed,reduced);
      if (!reduced) { fake.finish(); assert.equal(monster.destroyed,true); }
      map.onEvent({type:"room_entered",room_id:"T03"}); map.render(fake.scene,layout,"T03");
      ui.media.matches = true; ui.media.change({matches:true});
      assert.ok(fake.tweens.every(tween => tween.stopped));
      assert.equal(party.key,"warrior:idle:0"); assert.equal(party.alpha,1);
      const target = layout.rooms.find(room => room.id === "T03");
      assert.equal(party.x,target.x + 40);
      map.destroy();
    }
  }
  if (check === "integration") {
    const { mountParty } = await import(new URL("hud/party.js", base));
    const { mountDialogue } = await import(new URL("hud/dialogue.js", base));
    const { mountMap } = await import(new URL("map/scene.js", base));
    const ui = browser(true), fake = phaserScene(); let config;
    const Phaser = {CANVAS:1,Scale:{NONE:0},Game:class {
      constructor(value) {config=value;this.scale={resize() {}};} destroy() {}
    }};
    let socket;
    globalThis.window = ui.win; globalThis.document = ui.document;
    globalThis.CustomEvent = class { constructor(type,options) {this.type=type;this.detail=options.detail;} };
    globalThis.WebSocket = class {
      constructor() {socket=this;this.handlers=new Map();this.readyState=1;}
      addEventListener(name,fn) {const list=this.handlers.get(name)||[];list.push(fn);this.handlers.set(name,list);}
      send() {}
      message(message) {for(const fn of this.handlers.get("message")) fn({data:JSON.stringify(message)});}
    };
    const mapHost = new Element("div"); mapHost.clientWidth=960; mapHost.offsetTop=0; ui.host.append(mapHost);
    const originalGet = ui.document.getElementById;
    ui.document.getElementById = id => id === "world-map" ? mapHost : originalGet(id);
    ui.document.documentElement = {style:{setProperty() {}}};
    globalThis.Phaser = Phaser;
    const html = fs.readFileSync(new URL("index.html",base),"utf8");
    const script = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1]
      .replace(/from "(\.\/[^"]+)"/g, (_,path) => `from "${new URL(path,base)}"`)
      .replace(/import\("(\.\/[^"]+)"\)/g, (_,path) => `import("${new URL(path,base)}")`);
    await import(`data:text/javascript,${encodeURIComponent(script)}`);
    socket.message({kind:"state",state:{rooms:[],party:[],bugs:[],bosses:[]}}); config.scene.create.call(fake.scene);
    for (const event of events) {
      socket.message({kind:"event",event});
      // T31's sparkles, zzz and hit overlays are graphics above the actors.
      const lastRoomPaint = fake.displayList.findLastIndex(item => typeof item.key === "string" &&
        ["wall", "door", "stairs", "floor", "chest"].some(name => item.key.startsWith(name)));
      for (const image of fake.images.filter(image => !image.destroyed && image.visible !== false &&
        ["mage", "warrior", "paladin", ...MONSTERS].some(name => image.key.startsWith(`${name}:`)))) {
        assert.ok(fake.displayList.indexOf(image) > lastRoomPaint,"Room redraw hides a persistent sprite");
      }
      if (event.type === "fallback") assert.match(texts(ui.host), /synthetic\/warrior-backup/);
      if (event.type === "bug_opened") {
        assert.match(texts(ui.host),/Bell wheel missing/);
        assert.ok(fake.images.some(image => !image.destroyed && image.key === `${monsterForBug(events.find(event => event.type === "bug_opened").bug_id)}:idle:0`));
      }
      if (event.type === "bug_closed") assert.ok(fake.images.filter(image => MONSTERS.some(name => image.key.startsWith(`${name}:`))).every(image => image.destroyed));
    }
    assert.match(texts(ui.host), /The bell needs a replacement wheel/);
    assert.ok(flatten(ui.host).some(node => node.attrs.role === "meter" && node.attrs["aria-valuenow"] === "0"));
    assert.equal(fake.tweens.length,0);
    assert.equal(config.pixelArt,true);
  }
}
if (check === "approval") {
  const { mountApproval } = await import(new URL("hud/approval.js",base));
  for (const token of ["", "control"]) {
    const ui = browser(); const handlers = new Map();
    if (token) ui.win.specvaniaDesktop = {controlToken:()=>token,notify() {}};
    mountApproval({addEventListener(name,fn) {handlers.set(name,fn);},readyState:1,send() {}}, {window:ui.win,document:ui.document});
    const request = {type:"approval_requested",request_id:"one",command:"pytest -q",cwd:"/repo"};
    const message = value => handlers.get("message")({data:JSON.stringify(value)});
    message({kind:"event",event:request});
    assert.equal(texts(ui.document.body).includes("answer in the terminal or desktop window"), !token);
    assert.equal(flatten(ui.document.body).filter(node => node.tagName === "button").length,token ? 2 : 0);
    message({kind:"approval_rejected"});
    assert.equal(texts(ui.document.body).includes("answer in the terminal or desktop window"), !token);
  }
}
