"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync, existsSync } = require("node:fs");
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const root = path.resolve(__dirname, "../..");
const staticRoot = path.join(root, "src/specvania/ui/static");
const load = file => import(pathToFileURL(path.join(staticRoot, file)));

// Use the actual parser and world_loaded builder, rather than a second parser
// or a stale JSON snapshot. Checklist ticks are never execution progress.
function payload(directory) {
  const python = path.join(root, ".venv/bin/python");
  const result = spawnSync(existsSync(python) ? python : "python3", ["-c",
    "from pathlib import Path; import sys; from specvania.sdd.world import parse_world; from specvania.run.events import build_world_payload; print(build_world_payload(parse_world(Path(sys.argv[1]))).model_dump_json())",
    path.join(root, directory)], { cwd: root, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}
const repository = payload("docs");
const sample = payload("tests/fixtures/worlds/sample-todo-api");
const states = ["locked", "open", "active", "cleared", "failed", "skipped"];
const freeze = value => { if (value && typeof value === "object") { Object.values(value).forEach(freeze); Object.freeze(value); } return value; };
function overlap(a, b) { return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height; }

for (const [name, world, count, areas] of [["repository", repository, 47, 12], ["sample", sample, 3, 3]]) {
  test(`${name}_layout_is_deterministic_integer_and_without_overlaps`, async () => {
    const { castleLayout } = await load("map/castle.js");
    const { layoutWorld, worldFromRooms } = await load("map/layout.js");
    const original = JSON.stringify(world);
    const layout = castleLayout(freeze(structuredClone(world)));
    const reordered = { ...world, areas: [...world.areas].reverse(), doors: [...world.doors].reverse(),
      rooms: [...world.rooms].reverse().map(room => ({ ...room, depends_on: [...room.depends_on].reverse() })) };
    assert.deepEqual(layout, castleLayout(reordered));
    assert.equal(JSON.stringify(world), original);
    assert.equal(layout.chambers.length, count); assert.equal(layout.floors.length, areas);
    assert.deepEqual(layout.floors.map(floor => floor.id), world.areas.map(area => area.id));
    const rectangles = [...layout.chambers, ...layout.floors.map(floor => floor.boss)];
    for (const room of rectangles) {
      assert.ok([room.x, room.y, room.width, room.height].every(Number.isInteger));
      assert.ok(room.width > 0 && room.height > 0);
      assert.ok(room.x >= 0 && room.x + room.width <= layout.width && room.y + room.height <= layout.height);
      for (const other of rectangles) if (room.id !== other.id) assert.equal(overlap(room, other), false, `${room.id}/${other.id}`);
    }
    for (const floor of layout.floors) {
      for (let i = 0; i < floor.chambers.length; i++) {
        const room = floor.chambers[i];
        const sources = room.depends_on.map(id => layout.chambers.find(source => source.id === id)).filter(source => source.area === room.area);
        assert.equal(room.depth, sources.length ? Math.max(...sources.map(source => source.depth)) + 1 : 0);
        if (i) {
          const previous = floor.chambers[i - 1];
          assert.ok(previous.x + previous.width < room.x);
          assert.ok(previous.depth < room.depth || previous.depth === room.depth && previous.id < room.id);
        }
      }
    }
    const adapted = layoutWorld(worldFromRooms(world.rooms, world.areas, world.doors));
    assert.deepEqual({ ...adapted, overview: true }, layoutWorld(worldFromRooms(world.rooms, world.areas, world.doors), true));
    assert.deepEqual(adapted.rooms.map(room => [room.id, room.x, room.y]), layout.chambers.map(room => [room.id, room.x * 16, room.y * 16]));
    if (name === "repository") {
      assert.deepEqual([adapted.width, adapted.height], [832, 624]);
      const {castleViewZoom} = await load("map/layout.js");
      assert.equal(castleViewZoom(960, 540, adapted, true), 1);
      assert.deepEqual(adapted.areas.map(area => [area.id, area.y, area.height]).filter(([id]) => id === "A1" || id === "A12"),
        [["A1", 32, 48], ["A12", 560, 48]]);
    } else assert.ok(adapted.width <= 960 && adapted.height <= 540, "Whole castle must fit at zoom 1");
  });

  test(`${name}_every_floor_ends_in_boss_and_stairs_connect_only_adjacent_floors`, async () => {
    const { castleLayout } = await load("map/castle.js");
    const castle = castleLayout(world);
    assert.equal(castle.stairs.length, areas - 1);
    for (const [index, floor] of castle.floors.entries()) {
      assert.equal(floor.boss.area, floor.id);
      assert.ok(floor.chambers.every(room => room.x + room.width < floor.boss.x));
      assert.equal(floor.boss.x + floor.boss.width, floor.x + floor.width);
      assert.equal(floor.boss.y, floor.y);
      const stair = castle.stairs.find(stair => stair.src === floor.boss.id);
      if (index === areas - 1) { assert.equal(stair, undefined); continue; }
      const next = castle.floors[index + 1];
      assert.equal(stair.dst, next.boss.id);
      assert.equal(stair.from_area, floor.id); assert.equal(stair.to_area, next.id);
      assert.deepEqual(stair.points[0], { x: floor.boss.x + floor.boss.width, y: floor.boss.y + 2 });
      assert.deepEqual(stair.points.at(-1), { x: next.boss.x + next.boss.width, y: next.boss.y + 2 });
      assert.ok(stair.points.every(point => Number.isInteger(point.x) && Number.isInteger(point.y)));
      assert.ok(stair.points.at(-1).y > stair.points[0].y);
    }
  });
}

test("consecutive_doors_wait_for_every_dependency_including_other_floors_and_skips", async () => {
  const { castleLayout } = await load("map/castle.js");
  const { projectEvent } = await load("map/scene.js");
  const { worldFromRooms } = await load("map/layout.js");
  let world = worldFromRooms(repository.rooms, repository.areas, repository.doors);
  const check = () => {
    const layout = castleLayout(world);
    assert.equal(layout.doors.length, world.rooms.length - world.areas.length);
    for (const floor of layout.floors) {
      const doors = layout.doors.filter(door => door.area === floor.id);
      assert.deepEqual(doors.map(door => [door.src, door.dst]), floor.chambers.slice(1).map((room, index) => [floor.chambers[index].id, room.id]));
      for (const door of doors) {
        const next = world.rooms.find(room => room.id === door.dst);
        assert.equal(door.locked, !next.depends_on.every(id => world.rooms.find(room => room.id === id).state === "cleared"));
      }
    }
  };
  check();
  // T04 depends on both T02 and T03. Its consecutive predecessor alone is
  // insufficient, even when the other dependency is skipped rather than locked.
  world = projectEvent(world, {type: "room_cleared", room_id: "T03"});
  world = projectEvent(world, {type: "room_skipped", room_id: "T02"}); check();
  assert.equal(castleLayout(world).doors.find(door => door.dst === "T04").locked, true);
  world = projectEvent(world, {type: "room_cleared", room_id: "T02"}); check();
  assert.equal(castleLayout(world).doors.find(door => door.dst === "T04").locked, false);
  for (const room of repository.rooms) { world = projectEvent(world, {type: "room_cleared", room_id: room.id}); check(); }
  assert.ok(castleLayout(world).doors.every(door => !door.locked));
  const crossFloor = structuredClone(sample);
  crossFloor.rooms.push({id: "T04", area: "A2", title: "Branch", depends_on: ["T01"], state: "open"});
  // Stale open metadata must never bypass a dependency's actual state.
  assert.equal(castleLayout(crossFloor).doors.find(door => door.dst === "T04").locked, true);
  crossFloor.rooms[0].state = "cleared";
  assert.equal(castleLayout(crossFloor).doors.find(door => door.dst === "T04").locked, false);
});

test("depth_uses_payload_doors_and_ties_use_room_id_independent_of_input_order", async () => {
  const { castleLayout } = await load("map/castle.js");
  const world = {areas: [{id: "A1", title: "Castle"}], rooms: [
    {id: "T03", area: "A1", depends_on: []}, {id: "T02", area: "A1", depends_on: []},
    {id: "T01", area: "A1", depends_on: [], state: "cleared"}],
    doors: [{src: "T01", dst: "T03"}, {src: "T01", dst: "T02"}]};
  const castle = castleLayout(world);
  assert.deepEqual(castle.chambers.map(room => [room.id, room.depth]), [["T01", 0], ["T02", 1], ["T03", 1]]);
  assert.ok(castle.doors.every(door => !door.locked));
  assert.throws(() => castleLayout({...world, doors: [...world.doors, {src: "T03", dst: "T01"}]}), /Cyclic/);
  assert.throws(() => castleLayout({...world, doors: [{src: "T99", dst: "T01"}]}), /unknown/);
  const empty = castleLayout({rooms: [], doors: [], areas: [{id: "A1"}, {id: "A2"}]});
  assert.equal(empty.floors.length, 2); assert.equal(empty.stairs.length, 1);
});

// A minimal Phaser/DOM boundary; production mounting, layout, projection,
// textures and accent drawing execute unchanged. Objects retain paint order.
const phaserSource = readFileSync(path.join(staticRoot, "vendor/phaser.min.js"), "utf8");
const cameraMethod = name => {
  const match = phaserSource.match(new RegExp(`${name}:(function\\([^)]*\\)\\{[^}]*\\})`));
  assert.ok(match, `Pinned Phaser camera method ${name}`);
  return Function(`return (${match[1]})`)();
};

function harness(width = 960, height = 540, reduced = false) {
  const canvas = {tagName: "CANVAS"};
  const handlers = new Map(), hostHandlers = new Map(), keys = new Map(), paints = [], selections = [], resizes = [];
  const descriptions = {"map-rooms": {}, "castle-hud": {hidden: false}};
  const object = (kind, x = 0, y = 0, key) => ({kind, x, y, key, destroyed: false, visible: true, alpha: 1, listeners: new Map(),
    destroy() {this.destroyed = true;}, setInteractive() {this.interactive = true; return this;},
    on(type, fn) {this.listeners.set(type, fn); return this;}, setTexture(key) {this.key = key; return this;},
    setPosition(x, y) {this.x = x; this.y = y; return this;}, setAlpha(alpha) {this.alpha = alpha; return this;},
    setVisible(visible) {this.visible = visible; return this;} });
  const make = (...args) => {const item = object(...args); paints.push(item); return item;};
  const camera = {zoom: 1, following: null, centers: [], width, height, midPoint: {},
    scrollX: 0, scrollY: 0, useBounds: false,
    _bounds: {setTo(x, y, width, height) {Object.assign(this, {x, y, width, height});}},
    get displayWidth() {return this.width / this.zoom;}, get displayHeight() {return this.height / this.zoom;},
    clampX: cameraMethod("clampX"), clampY: cameraMethod("clampY"),
    centerOnX: cameraMethod("centerOnX"), centerOnY: cameraMethod("centerOnY"),
    stopFollow() {this.following = null;}, startFollow(marker) {this.following = marker;},
    setZoom(zoom) {assert.ok(Number.isInteger(zoom) && zoom >= 1); this.zoom = zoom;},
    setBounds(...args) {this.bounds = args; cameraMethod("setBounds").apply(this, args);},
    centerOn(x, y) {this.centers.push([x, y]); this.centerOnX(x); this.centerOnY(y);},
    setScroll(x, y) {this.scrollX = x; this.scrollY = y; return this;},
    setViewport(x, y, width, height) {Object.assign(this, {x, y, width, height});} };
  const scene = {cameras: {main: camera}, textures: {exists: () => true},
    input: {keyboard: {on: (type, fn) => keys.set(type, fn)}},
    children: {bringToTop(item) {const index = paints.indexOf(item); paints.splice(index, 1); paints.push(item);}},
    add: {image: (x, y, key) => make("image", x, y, key),
      text: (x, y, text, style) => Object.assign(make("text", x, y), {text, style}),
      zone: (x, y, width, height) => Object.assign(make("zone", x, y), {width, height}),
      graphics() {
        const item = Object.assign(make("graphics"), {commands: [], color: 0, opacity: 1,
          fillStyle(color, opacity = 1) {this.color = color; this.opacity = opacity; return this;},
          fillRect(x, y, w, h) { assert.ok([x, y, w, h].every(Number.isInteger));
            this.commands.push({x, y, w, h, color: this.color, opacity: this.opacity}); return this;} });
        return item;
      } },
  };
  const mediaHandlers = new Set();
  const media = {matches: reduced, addEventListener(type, fn) {mediaHandlers.add(fn);}, removeEventListener(type, fn) {mediaHandlers.delete(fn);} };
  let config, destroyed = false;
  const Phaser = {CANVAS: 1, Scale: {NONE: 0}, Game: class {
    constructor(options) {config = options; this.canvas = canvas; this.scale = {resize: (w, h) => (resizes.push([w, h]), camera.width = w, camera.height = h)};}
    destroy() {destroyed = true;}
  } };
  const browser = {innerWidth: width, innerHeight: height, matchMedia: () => media,
    document: {activeElement: null, getElementById: id => descriptions[id]},
    CustomEvent: class {constructor(type, options) {this.type = type; this.detail = options.detail;}},
    addEventListener(type, fn) {const list = handlers.get(type) || []; list.push(fn); handlers.set(type, list);},
    removeEventListener(type, fn) {handlers.set(type, handlers.get(type).filter(item => item !== fn));},
    dispatchEvent(event) {selections.push(event);},
  };
  const host = {clientWidth: width, tabIndex: -1, inert: false,
    getAttribute: name => name === "inert" && host.inert ? "" : null,
    focus() {browser.document.activeElement = host;}, contains: target => target === host,
    addEventListener(type, fn) {hostHandlers.set(type, fn);}, removeEventListener(type) {hostHandlers.delete(type);} };
  const dom = (surface, type, fields = {}) => {
    const event = {target: host, defaultPrevented: false, preventDefault() {this.defaultPrevented = true;}, ...fields};
    if (surface === "host") hostHandlers.get(type)?.(event);
    else handlers.get(type)?.forEach(fn => fn(event));
    return event;
  };
  const emit = (type, detail) => handlers.get(type).forEach(fn => fn({detail}));
  return {Phaser, browser, host, canvas, scene, paints, camera, keys, descriptions, selections, resizes, mediaHandlers,
    emit, dom, config: () => config, create: () => config.scene.create.call(scene),
    resize(w, h) {browser.innerWidth = w; browser.innerHeight = h; emit("resize");},
    reduce() {media.matches = true; [...mediaHandlers].forEach(fn => fn({matches: true}));},
    live: () => paints.filter(item => !item.destroyed && item.visible), destroyed: () => destroyed,
    listenerCount: () => hostHandlers.size + [...handlers.values()].reduce((sum, list) => sum + list.length, 0) };
}

// Independent of the repository's current number of areas and rooms.
function syntheticWorld(areaCount = 12, roomsPerArea = 1) {
  const areas = Array.from({length: areaCount}, (_, i) => ({id: `A${i + 1}`, title: `Floor ${i + 1}`, boss_ids: [`S${i + 1}`]}));
  const rooms = areas.flatMap(area => Array.from({length: roomsPerArea}, (_, j) => ({
    id: `T${String((Number(area.id.slice(1)) - 1) * roomsPerArea + j + 1).padStart(2, "0")}`,
    area: area.id, title: `Chamber ${j + 1}`, state: "open", depends_on: [],
  })));
  return {areas, rooms, doors: []};
}

async function overviewHarness(world = syntheticWorld()) {
  const { mountMap } = await load("map/scene.js");
  const h = harness(), map = mountMap(h.Phaser, h.host, h.browser);
  h.emit("specvania:event", {type: "world_loaded", ...world}); h.create();
  h.keys.get("keydown-M")({repeat: false});
  return {h, map};
}

test("oversized_overview_starts_at_top_at_zoom_one_and_wheel_scrolls_integer_clamped_pixels", async () => {
  const {h, map} = await overviewHarness();
  assert.equal(h.camera.zoom, 1);
  assert.equal(h.camera.scrollY, 0, "First floor starts below the title bar, not midway through the castle");
  assert.equal(h.camera.scrollX, -304, "352px castle centered in 960px window");
  assert.ok(32 - h.camera.scrollY >= 32 && 80 - h.camera.scrollY <= 540);
  assert.equal(h.dom("host", "wheel", {deltaX: 0, deltaY: 23.7, deltaMode: 0}).defaultPrevented, true);
  assert.equal(h.camera.scrollY, 24);
  h.emit("specvania:event", {type: "room_cleared", room_id: "T01"});
  assert.equal(h.camera.scrollY, 24, "Live redraw retains manual scrolling");
  h.dom("host", "wheel", {deltaX: 0, deltaY: 10000, deltaMode: 0});
  assert.equal(h.camera.scrollY, 84, "624px castle bottom clamps at 540px window bottom");
  assert.ok(560 - h.camera.scrollY >= 32 && 608 - h.camera.scrollY <= 540, "Last boss arena fully visible");
  h.dom("host", "wheel", {deltaX: -9999, deltaY: -10000, deltaMode: 0});
  assert.deepEqual([h.camera.scrollX, h.camera.scrollY], [-304, 0]);
  h.dom("host", "wheel", {deltaX: 0, deltaY: 2, deltaMode: 1});
  assert.equal(h.camera.scrollY, 32, "Wheel line deltas use tile-sized pixels");
  map.destroy(); assert.equal(h.listenerCount(), 0);
});

test("oversized_overview_arrow_and_page_keys_scroll_only_with_castle_focus", async () => {
  const {h, map} = await overviewHarness();
  assert.equal(h.host.tabIndex, 0, "Castle can receive keyboard focus");
  const key = (value, fields) => h.dom("host", "keydown", {key: value, ...fields});
  key("ArrowDown"); assert.equal(h.camera.scrollY, 0);
  h.host.focus();
  assert.equal(key("ArrowDown").defaultPrevented, true); assert.equal(h.camera.scrollY, 16);
  key("ArrowUp"); assert.equal(h.camera.scrollY, 0);
  key("PageDown"); assert.equal(h.camera.scrollY, 84);
  key("PageDown", {repeat: true}); assert.equal(h.camera.scrollY, 84);
  key("PageUp"); assert.equal(h.camera.scrollY, 0);
  for (const fields of [{target: {tagName: "INPUT"}}, {target: {tagName: "TEXTAREA"}}, {target: {isContentEditable: true}}]) {
    assert.equal(key("PageDown", fields).defaultPrevented, false); assert.equal(h.camera.scrollY, 0);
  }
  h.host.inert = true; key("PageDown"); assert.equal(h.camera.scrollY, 0); h.host.inert = false;
  h.keys.get("keydown-M")({repeat: false});
  const before = [h.camera.scrollX, h.camera.scrollY];
  key("PageDown"); h.dom("host", "wheel", {deltaY: 1000});
  assert.deepEqual([h.camera.scrollX, h.camera.scrollY], before, "Follow mode ignores overview scrolling");
  map.destroy();
});

test("oversized_overview_drag_scrolls_integer_pixels_clamps_and_does_not_select_chambers", async () => {
  const {h, map} = await overviewHarness();
  const zone = h.live().find(item => item.kind === "zone");
  zone.listeners.get("pointerdown")({downElement: h.canvas});
  h.dom("host", "pointerdown", {button: 0, pointerId: 1, clientX: 400, clientY: 300});
  assert.equal(h.browser.document.activeElement, h.host);
  h.dom("browser", "pointermove", {pointerId: 1, clientX: 390.4, clientY: 279.3});
  assert.deepEqual([h.camera.scrollX, h.camera.scrollY], [-304, 21]);
  h.dom("browser", "pointermove", {pointerId: 1, clientX: -1000, clientY: -1000});
  assert.equal(h.camera.scrollY, 84);
  h.dom("browser", "pointermove", {pointerId: 1, clientX: 2000, clientY: 2000});
  assert.equal(h.camera.scrollY, 0);
  // DOM Pointer Events precede Phaser's compatibility mouseup event.
  h.dom("browser", "pointerup", {pointerId: 1});
  zone.listeners.get("pointerup")?.({downElement: h.canvas});
  assert.ok(!h.selections.some(event => event.type === "specvania:room-selected"));
  h.dom("browser", "pointermove", {pointerId: 1, clientX: 400, clientY: 200});
  assert.equal(h.camera.scrollY, 0, "Released drag stops scrolling");
  h.dom("host", "pointerdown", {button: 0, pointerId: 2, clientX: 150, clientY: 50});
  zone.listeners.get("pointerdown")({downElement: h.canvas});
  h.dom("browser", "pointerup", {pointerId: 2});
  zone.listeners.get("pointerup")?.({downElement: h.canvas});
  assert.deepEqual(h.selections.at(-1).detail, {room_id: "T01"}, "A click still selects the chamber");
  map.destroy();
});

test("oversized_overview_ignores_overlay_presses_and_releases_but_selects_canvas_clicks", async () => {
  const {h, map} = await overviewHarness();
  try {
    const zone = h.live().find(item => item.kind === "zone");
    // Phaser's window listeners deliver both events even for DOM overlays.
    for (const downElement of [{tagName: "BUTTON"}, {tagName: "SECTION"}, undefined]) {
      const pointer = {downElement, event: {target: downElement}};
      zone.listeners.get("pointerdown")(pointer);
      zone.listeners.get("pointerup")(pointer);
    }
    assert.ok(!h.selections.some(event => event.type === "specvania:room-selected"));
    zone.listeners.get("pointerdown")({downElement: h.canvas});
    zone.listeners.get("pointerup")({downElement: h.canvas});
    assert.deepEqual(h.selections.filter(event => event.type === "specvania:room-selected").map(event => event.detail),
      [{room_id: "T01"}]);
  } finally {map.destroy();}
});

test("non_scrolling_castle_selects_on_canvas_press_and_ignores_overlay_presses_and_releases", async () => {
  const {h, map} = await overviewHarness(sample);
  try {
    const zone = h.live().find(item => item.kind === "zone");
    // The same window events reach the zone in the non-scrolling path.
    for (const downElement of [{tagName: "BUTTON"}, {tagName: "SECTION"}, undefined]) {
      const pointer = {downElement, event: {target: downElement}};
      zone.listeners.get("pointerdown")(pointer);
      assert.ok(!h.selections.some(event => event.type === "specvania:room-selected"));
      zone.listeners.get("pointerup")(pointer);
      assert.ok(!h.selections.some(event => event.type === "specvania:room-selected"));
    }
    zone.listeners.get("pointerdown")({downElement: h.canvas});
    assert.deepEqual(h.selections.at(-1).detail, {room_id: "T01"}, "Canvas press selects without waiting for release");
    zone.listeners.get("pointerup")({downElement: h.canvas});
    assert.deepEqual(h.selections.filter(event => event.type === "specvania:room-selected").map(event => event.detail),
      [{room_id: "T01"}]);
  } finally {map.destroy();}
});

test("oversized_overview_scrolls_both_axes_to_last_arena_left_of_measured_hud_overlay", async () => {
  const {layoutWorld} = await load("map/layout.js");
  const world = syntheticWorld(12, 10), {h, map} = await overviewHarness(world);
  // Retain a measured overlay reservation even while M hides the HUD.
  h.descriptions["castle-hud"].getBoundingClientRect = () => ({width: 260});
  h.resize(960, 540);
  assert.equal(h.camera.zoom, 1);
  assert.equal(h.camera.scrollY, 0);
  h.host.focus();
  const before = h.camera.scrollX;
  h.dom("host", "keydown", {key: "ArrowRight"}); assert.equal(h.camera.scrollX, before + 16);
  h.dom("host", "keydown", {key: "ArrowLeft"}); assert.equal(h.camera.scrollX, before);
  h.dom("host", "wheel", {deltaX: 10000.4, deltaY: 10000.4, deltaMode: 0});
  assert.deepEqual([h.camera.scrollX, h.camera.scrollY], [516, 84]);
  const arena = layoutWorld(world).bosses.at(-1);
  assert.ok(arena.x - h.camera.scrollX >= 0);
  assert.ok(arena.x + arena.width - h.camera.scrollX <= 700, "Arena ends left of 260px HUD");
  assert.ok(arena.y - h.camera.scrollY >= 32 && arena.y + arena.height - h.camera.scrollY <= 540);
  assert.equal(h.camera.clampX(h.camera.scrollX), h.camera.scrollX, "Phaser bounds preserve HUD-aware scroll");
  assert.equal(h.camera.clampY(h.camera.scrollY), h.camera.scrollY);
  h.dom("host", "wheel", {deltaX: -10000, deltaY: -10000, deltaMode: 0});
  assert.deepEqual([h.camera.scrollX, h.camera.scrollY], [0, 0]);
  h.descriptions["castle-hud"].getBoundingClientRect = () => ({width: 260.5});
  h.resize(960, 540);
  h.dom("host", "wheel", {deltaX: 10000, deltaY: 10000, deltaMode: 0});
  assert.deepEqual([h.camera.scrollX, h.camera.scrollY], [517, 84], "Fractional DOM measurements still yield integer scroll bounds");
  assert.ok(arena.x + arena.width - h.camera.scrollX <= 699.5);
  map.destroy();
  // Width alone can overflow, even when every floor fits vertically.
  const wide = await overviewHarness(syntheticWorld(1, 10));
  assert.equal(wide.h.camera.zoom, 1); assert.equal(wide.h.camera.scrollY, 0);
  wide.h.dom("host", "wheel", {deltaX: 10000, deltaY: 10000});
  assert.deepEqual([wide.h.camera.scrollX, wide.h.camera.scrollY], [256, 0]); wide.map.destroy();
});

test("small_overview_keeps_largest_integer_zoom_and_centering_and_ignores_scroll_inputs", async () => {
  const {h, map} = await overviewHarness(syntheticWorld(2));
  assert.equal(h.camera.zoom, 2);
  assert.deepEqual(h.camera.centers.at(-1), [176, 72]);
  const before = [h.camera.scrollX, h.camera.scrollY];
  h.host.focus(); h.dom("host", "wheel", {deltaY: 10000}); h.dom("host", "keydown", {key: "PageDown"});
  h.dom("host", "pointerdown", {button: 0, pointerId: 1, clientX: 400, clientY: 300});
  h.dom("browser", "pointermove", {pointerId: 1, clientX: 200, clientY: 100});
  assert.deepEqual([h.camera.scrollX, h.camera.scrollY], before);
  map.destroy();
});

function stateWorld() {
  return {areas: [{id: "A1", title: "States", boss_ids: []}], doors: [],
    rooms: states.map((state, i) => ({id: `T0${i + 1}`, area: "A1", title: state, state, depends_on: []}))};
}

test("state_semantics_draw_padlock_torch_warm_party_open_chest_failure_cross_and_cobweb", async () => {
  const { mountMap, roomAppearance, drawRoom } = await load("map/scene.js");
  const { PALETTE } = await load("pixel/palette.js");
  const color = symbol => parseInt(PALETTE[symbol].slice(1), 16);
  const commands = state => {
    const result = [], graphics = {fillStyle(ink, alpha = 1) {this.ink = ink; this.alpha = alpha; return this;},
      fillRect(x, y, w, h) {result.push({x, y, w, h, ink: this.ink, alpha: this.alpha}); return this;} };
    drawRoom(graphics, {x: 0, y: 0, width: 80, height: 48, state}); return result;
  };
  assert.equal(roomAppearance("locked").fill, "K");
  assert.ok(commands("locked").some(mark => mark.ink === color("Y") && mark.w === 10 && mark.h === 6), "Padlock body");
  assert.ok(commands("locked").some(mark => mark.ink === color("K") && mark.w === 2 && mark.h === 3), "Padlock keyhole");
  assert.ok(commands("open").some(mark => mark.ink === color("Y") && mark.w === 16 && mark.h === 16 && mark.alpha < 1), "Torch glow");
  assert.ok(commands("open").some(mark => mark.ink === color("Y") && mark.w === 4 && mark.h === 6 && mark.alpha === 1), "Torch flame");
  assert.equal(roomAppearance("active").fill, "Y");
  assert.equal(commands("active").filter(mark => mark.ink === color("W") && mark.alpha === 1).length, 3);
  assert.equal(commands("cleared").filter(mark => mark.ink === color("G") && mark.alpha === 1).length, 5, "Green check");
  assert.equal(roomAppearance("failed").fill, "R");
  assert.equal(commands("failed").filter(mark => mark.ink === color("W") && mark.alpha === 1).length, 10, "Failure cross survives grayscale");
  assert.equal(roomAppearance("skipped").fill, "S");
  const webs = commands("skipped").filter(mark => mark.ink === color("W") && mark.alpha === 1);
  for (const [left, right] of [[0, 24], [56, 80]]) {
    const pixels = new Set();
    for (const mark of webs) for (let y = mark.y; y < mark.y + mark.h; y++)
      for (let x = mark.x; x < mark.x + mark.w; x++) if (x >= left && x < right) pixels.add(`${x},${y}`);
    assert.ok(pixels.size >= 70, `Readable cobweb in corner ${left}`);
    const points = [...pixels].map(point => point.split(",").map(Number));
    assert.ok(Math.max(...points.map(point => point[0])) - Math.min(...points.map(point => point[0])) >= 18);
    assert.ok(Math.max(...points.map(point => point[1])) - Math.min(...points.map(point => point[1])) >= 12);
  }
  const { layoutWorld } = await load("map/layout.js");
  const h = harness(), map = mountMap(h.Phaser, h.host, h.browser);
  h.emit("specvania:state", {...stateWorld(), active_room: "T03"}); h.create();
  for (const room of layoutWorld(stateWorld()).rooms) {
    const chest = h.live().find(item => item.kind === "image" && item.x === room.x + 56 && item.y === room.y + 24 && item.key.startsWith("chest_"));
    if (room.state === "active") assert.equal(chest, undefined);
    else assert.equal(chest.key, room.state === "cleared" ? "chest_open:idle:0" : "chest_closed:idle:0");
    const door = h.live().find(item => item.kind === "image" && item.x === room.x + 8 && item.y === room.y + 24 && item.key.startsWith("door_"));
    assert.equal(door.key, room.state === "locked" ? "door_closed:idle:0" : "door_open_middle:idle:0");
  }
  map.destroy();
});

test("scene_draws_cast_cross_section_bosses_state_accents_party_and_stable_monsters", async () => {
  const { mountMap } = await load("map/scene.js");
  const { bossForArea, monsterForBug } = await load("pixel/cast.js");
  const { layoutWorld } = await load("map/layout.js");
  const h = harness(); const mounted = mountMap(h.Phaser, h.host, h.browser);
  h.emit("specvania:state", {...stateWorld(), active_room: "T03"}); h.create();
  const keys = () => h.live().filter(item => item.kind === "image").map(item => item.key);
  for (const name of ["back_wall", "wall_left", "wall_right", "wall_top_left", "wall_top_middle", "wall_top_right",
    "floor_stones", "floor_grate", "floor_plain", "banner", "wall_grate", "chest_closed", "chest_open", "door_closed", "door_open_middle", bossForArea("A1"), "mage", "warrior", "paladin"]) {
    assert.ok(keys().includes(`${name}:idle:0`), name);
  }
  for (const role of ["mage", "warrior", "paladin"]) {
    const party = h.live().find(item => item.key === `${role}:idle:0`);
    const active = layoutWorld(stateWorld()).rooms.find(room => room.id === "T03");
    assert.ok(party.x > active.x && party.x < active.x + active.width && party.y === active.y + 24);
  }
  assert.equal(h.camera.following.key, "warrior:idle:0");
  assert.ok(h.live().some(item => item.kind === "text" && item.text === "A1\nStates"));
  for (const room of stateWorld().rooms) assert.ok(h.live().some(item => item.kind === "text" && item.text === room.id));
  h.emit("specvania:event", {type: "bug_opened", bug_id: "bug-1", room_id: "T06"});
  assert.ok(keys().includes(`${monsterForBug("bug-1")}:idle:0`));
  h.keys.get("keydown-M")({repeat: false});
  assert.ok(keys().includes("warrior:idle:0"), "Active party remains visible in whole-castle view");
  h.emit("specvania:event", {type: "bug_closed", bug_id: "bug-1", status: "fixed"});
  assert.ok(!keys().includes(`${monsterForBug("bug-1")}:idle:0`));
  mounted.destroy();
  // The repository scene must draw all eleven inter-floor stairs and all bosses.
  const all = harness(); const map = mountMap(all.Phaser, all.host, all.browser);
  all.emit("specvania:event", {type: "world_loaded", ...repository}); all.create();
  assert.equal(all.live().filter(item => item.key === "stairs:idle:0").length, 33);
  for (const area of repository.areas) {
    const boss = layoutWorld(repository).bosses.find(boss => boss.area === area.id);
    assert.ok(all.live().some(item => item.key === `${bossForArea(area.id)}:idle:0` && item.x === boss.x + 40 && item.y === boss.y + 24));
  }
  map.destroy();
});

const luminance = color => {
  const channels = typeof color === "number" ? [color >> 16 & 255, color >> 8 & 255, color & 255] : color;
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
};

test("state_palette_and_actual_cast_accents_remain_distinct_in_grayscale_luminance", async () => {
  const { roomAppearance, drawRoom, mountMap } = await load("map/scene.js");
  const { PALETTE } = await load("pixel/palette.js");
  const { CAST } = await load("pixel/cast.js");
  const { decodePng } = await import(pathToFileURL(path.join(root, "apps/desktop/scripts/make-icon.mjs")));
  const sheet = decodePng(readFileSync(path.join(staticRoot, "vendor/kenney-tiny-dungeon/tilemap_packed.png")));
  const palette = symbol => parseInt(PALETTE[symbol].slice(1), 16);
  const brightness = states.map(state => luminance(palette(roomAppearance(state).fill)));
  for (let i = 0; i < brightness.length; i++) for (let j = i + 1; j < brightness.length; j++) {
    assert.ok(Math.abs(brightness[i] - brightness[j]) > 20, `${states[i]}/${states[j]} palette luminance`);
  }
  const h = harness(); const mounted = mountMap(h.Phaser, h.host, h.browser);
  h.emit("specvania:state", {...stateWorld(), active_room: "T03"}); h.create();
  const { layoutWorld } = await load("map/layout.js");
  const raster = room => {
    const pixels = new Float64Array(room.width * room.height).fill(luminance(palette("K")));
    const paint = (x, y, light, alpha) => {
      const px = x - room.x, py = y - room.y;
      if (px >= 0 && px < room.width && py >= 0 && py < room.height) {
        const index = py * room.width + px; pixels[index] = pixels[index] * (1 - alpha) + light * alpha;
      }
    };
    for (const item of h.live()) {
      if (item.kind === "image") {
        const name = item.key.split(":")[0], index = CAST[name];
        assert.ok(Number.isInteger(index), item.key);
        for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
          const offset = ((Math.floor(index / 12) * 16 + y) * sheet.width + index % 12 * 16 + x) * 4;
          const rgba = sheet.pixels.subarray(offset, offset + 4);
          paint(item.x - 8 + x, item.y - 8 + y, luminance(rgba), rgba[3] / 255);
        }
      } else if (item.kind === "graphics") for (const command of item.commands) {
        for (let y = command.y; y < command.y + command.h; y++) for (let x = command.x; x < command.x + command.w; x++) paint(x, y, luminance(command.color), command.opacity);
      }
    }
    return pixels;
  };
  const images = layoutWorld(stateWorld()).rooms.map(raster);
  const means = images.map(pixels => pixels.reduce((sum, light) => sum + light, 0) / pixels.length);
  const skipped = states.indexOf("skipped");
  for (let i = 0; i < means.length; i++) if (i !== skipped) {
    assert.ok(Math.abs(means[skipped] - means[i]) > 10,
      `skipped/${states[i]} rendered mean luminance (${means[skipped]} / ${means[i]})`);
  }
  assert.ok(means[skipped] > means[states.indexOf("locked")] + 10, "Skipped is lighter than locked");
  for (let i = 0; i < images.length; i++) for (let j = i + 1; j < images.length; j++) {
    const different = images[i].filter((value, p) => Math.abs(value - images[j][p]) > 20).length;
    assert.ok(different >= 100, `${states[i]}/${states[j]} actual grayscale pixels (${different})`);
  }
  // Strip the tint entirely: small accent silhouettes must also remain unique.
  const marks = states.map(state => {
    const commands = [], graphics = {fillStyle() {return this;}, fillRect(...args) {commands.push(args); return this;}};
    drawRoom(graphics, {x: 0, y: 0, width: 80, height: 48, state});
    return JSON.stringify(commands.slice(1));
  });
  assert.equal(new Set(marks).size, 6);
  assert.throws(() => roomAppearance("unknown"), /Unknown room state/);
  mounted.destroy();
});

test("chamber_click_dispatches_dom_room_id_and_M_toggles_same_castle_geometry", async () => {
  const { mountMap } = await load("map/scene.js");
  const h = harness(); const mounted = mountMap(h.Phaser, h.host, h.browser);
  h.emit("specvania:event", {type: "world_loaded", ...repository}); h.create();
  for (const overview of [false, true]) {
    const { layoutWorld } = await load("map/layout.js");
    const layout = layoutWorld(repository, overview);
    const zones = h.live().filter(item => item.kind === "zone");
    assert.equal(zones.length, repository.rooms.length);
    for (const [index, zone] of zones.entries()) {
      assert.equal(zone.interactive, true);
      const selections = h.selections.length;
      zone.listeners.get("pointerdown")({downElement: h.canvas});
      if (overview) {
        assert.equal(h.selections.length, selections, "Scrollable overview waits for release");
        zone.listeners.get("pointerup")({downElement: h.canvas});
      }
      const selected = h.selections.at(-1);
      assert.equal(h.selections.length, selections + 1);
      assert.ok(selected instanceof h.browser.CustomEvent);
      assert.equal(selected.type, "specvania:room-selected");
      assert.deepEqual(selected.detail, {room_id: layout.rooms[index].id});
    }
    const before = zones.map(zone => [zone.x, zone.y, zone.width, zone.height]);
    h.keys.get("keydown-M")({repeat: true});
    assert.deepEqual(h.live().filter(item => item.kind === "zone").map(zone => [zone.x, zone.y, zone.width, zone.height]), before);
    h.keys.get("keydown-M")({repeat: false});
    assert.deepEqual(h.live().filter(item => item.kind === "zone").map(zone => [zone.x, zone.y, zone.width, zone.height]), before);
    assert.equal(h.descriptions["castle-hud"].hidden, !overview);
    assert.equal(h.camera.zoom, overview ? 3 : 1);
  }
  h.keys.get("keydown-M")({repeat: false, target: {tagName: "INPUT"}});
  assert.equal(h.camera.zoom, 3);
  mounted.destroy(); assert.equal(h.listenerCount(), 0); assert.equal(h.mediaHandlers.size, 0); assert.equal(h.destroyed(), true);
});

test("canvas_fills_windows_and_resizes_with_integer_camera_zoom_in_both_views", async () => {
  const { mountMap } = await load("map/scene.js");
  const { layoutWorld, castleViewZoom } = await load("map/layout.js");
  const sizes = [[960, 540], [1280, 720], [1920, 1080], [1000, 600], [1280, 1000], [1920, 800]];
  const demo = readFileSync(path.join(root, "src/specvania/examples/demo/events.jsonl"), "utf8")
    .trim().split("\n").map(JSON.parse).find(event => event.type === "world_loaded");
  for (const world of [repository, sample, demo]) {
    const h = harness(); const mounted = mountMap(h.Phaser, h.host, h.browser);
    const overlayWidth = world === repository ? 260 : 0;
    if (world === repository) h.descriptions["castle-hud"].getBoundingClientRect = () => ({width: overlayWidth});
    h.emit("specvania:event", {type: "world_loaded", ...world}); h.create();
    assert.equal(h.config().width, 960); assert.equal(h.config().height, 540);
    assert.equal(h.config().pixelArt, true); assert.equal(h.config().roundPixels, true);
    assert.equal(h.config().scale.zoom, 1); assert.equal(h.config().scale.mode, h.Phaser.Scale.NONE);
    for (const overview of [false, true]) {
      if (overview) h.keys.get("keydown-M")({repeat: false});
      for (const [w, height] of sizes) {
        h.resize(w, height);
        assert.deepEqual(h.resizes.at(-1), [w, height]);
        const layout = layoutWorld(world, overview);
        const zoom = h.camera.zoom;
        assert.ok(Number.isInteger(zoom) && zoom >= 1);
        assert.equal(zoom, castleViewZoom(overview ? w - overlayWidth : w, height, layout, overview));
        assert.equal(h.camera.roundPixels, true);
        if (overview) {
          const screenX = x => (x - h.camera.scrollX - w / 2) * zoom + w / 2;
          const screenY = y => (y - h.camera.scrollY - height / 2) * zoom + height / 2;
          if (world === repository && (layout.width > w - overlayWidth || layout.height > height)) {
            assert.deepEqual([w, height], w === 960 ? [960, 540] : [1000, 600]);
            // Re-enter at this size; resize alone intentionally retains scrolling.
            h.keys.get("keydown-M")({repeat: false});
            h.keys.get("keydown-M")({repeat: false});
            assert.equal(zoom, 1);
            assert.deepEqual([h.camera.scrollX, h.camera.scrollY], [w === 960 ? 66 : 46, 0]);
            const first = layout.areas[0];
            assert.equal(first.id, "A1");
            assert.deepEqual([screenY(first.y), screenY(first.y + first.height)], [32, 80]);
            assert.ok(screenX(first.chambers[0].x * 16) >= 0);
            h.dom("host", "wheel", {deltaX: 128 - h.camera.scrollX, deltaY: 10000, deltaMode: 0});
            assert.deepEqual([h.camera.scrollX, h.camera.scrollY],
              [Math.min(128, layout.width - (w - overlayWidth)), w === 960 ? 84 : 24],
              "Horizontal scroll = min(128, castle width - (window width - measured HUD width))");
            const last = layout.areas.at(-1);
            assert.equal(last.id, "A12");
            assert.deepEqual(last.chambers.map(room => room.id), ["T42", "T43", "T44", "T45", "T46", "T47"]);
            assert.deepEqual([screenY(last.y), screenY(last.y + last.height)], w === 960 ? [476, 524] : [536, 584]);
            for (const room of [...layout.rooms.filter(room => room.area === "A12"), last.boss]) {
              assert.ok(screenX(room.x) >= 0 && screenX(room.x + room.width) <= w - overlayWidth, `${room.id} is fully left of the HUD`);
              assert.ok(screenY(room.y) >= 32 && screenY(room.y + room.height) <= height, `${room.id} is fully visible vertically`);
            }
            assert.equal(h.camera.clampX(h.camera.scrollX), h.camera.scrollX);
            assert.equal(h.camera.clampY(h.camera.scrollY), h.camera.scrollY);
            continue;
          }
          assert.ok(Math.abs((screenX(0) + screenX(layout.width)) / 2 - (w - overlayWidth) / 2) <= 1, "Castle centered horizontally in visible region");
          assert.ok(Math.abs((screenY(0) + screenY(layout.height)) / 2 - height / 2) <= 1, "Castle centered vertically");
          for (const floor of layout.areas) {
            assert.ok(screenX(floor.x) >= 0 && screenY(floor.y) >= 0, "Floor labels remain visible");
          }
          assert.ok(layout.width * zoom <= w - overlayWidth && layout.height * zoom <= height);
          assert.ok(layout.width * (zoom + 1) > w - overlayWidth || layout.height * (zoom + 1) > height, "Use largest fitting integer zoom");
        }
      }
    }
    const html = readFileSync(path.join(staticRoot, "index.html"), "utf8");
    assert.match(html, /#world-map\s*\{[^}]*position:\s*fixed;[^}]*inset:\s*0;[^}]*width:\s*100vw;[^}]*height:\s*100vh/);
    assert.match(html, /canvas\s*\{[^}]*image-rendering:\s*pixelated/);
    // Texture filtering itself is exercised by T29's actual texture-manager test.
    mounted.destroy();
  }
});

test("live_replay_and_reconnect_paint_room_events_synchronously_and_keep_reduced_motion", async () => {
  const { mountMap, projectEvent } = await load("map/scene.js");
  const { worldFromRooms, layoutWorld } = await load("map/layout.js");
  const { PALETTE } = await load("pixel/palette.js");
  const h = harness(); const mounted = mountMap(h.Phaser, h.host, h.browser);
  h.emit("specvania:event", {type: "world_loaded", ...repository}); h.create();
  for (const [type, state] of [["room_entered", "active"], ["room_failed", "failed"], ["respawn", "active"],
    ["attempt_started", "active"], ["room_skipped", "skipped"], ["room_cleared", "cleared"]]) {
    const start = performance.now();
    h.emit("specvania:event", {type, room_id: "T01"});
    assert.ok(h.descriptions["map-rooms"].textContent.includes(`T01: Scaffold the project — ${state}`));
    assert.ok(performance.now() - start < 1000, "No animation delay in state projection");
    const fills = {active: "Y", failed: "R", skipped: "S", cleared: "W"};
    const room = layoutWorld(repository).rooms.find(room => room.id === "T01");
    assert.ok(h.live().some(item => item.kind === "graphics" && item.commands.some(command => command.x === room.x && command.y === room.y + 16 && command.w === room.width && command.color === parseInt(PALETTE[fills[state]].slice(1), 16))));
  }
  assert.ok(h.descriptions["map-rooms"].textContent.includes("T02: Config and `init` — open"));
  h.reduce(); h.emit("specvania:event", {type: "room_entered", room_id: "T02"});
  assert.equal(h.camera.following, null);
  const warrior = h.live().find(item => item.key === "warrior:idle:0");
  const target = layoutWorld(repository).rooms.find(room => room.id === "T02");
  assert.deepEqual([warrior.x, warrior.y], [target.x + 40, target.y + 24]);
  let world = worldFromRooms(repository.rooms, repository.areas, repository.doors);
  world = projectEvent(world, {type: "room_cleared", room_id: "T01"});
  const geometry = h.live().filter(item => item.kind === "zone").map(item => [item.x, item.y]);
  h.emit("specvania:state", {rooms: world.rooms, party_room: "T02"});
  assert.deepEqual(h.live().filter(item => item.kind === "zone").map(item => [item.x, item.y]), geometry);
  assert.ok(h.descriptions["map-rooms"].textContent.includes("T01: Scaffold the project — cleared"));
  assert.equal(h.camera.following, null);
  mounted.destroy();
  // The bundled demo traverses the very same mount/event path at replay speed.
  const demo = readFileSync(path.join(root, "src/specvania/examples/demo/events.jsonl"), "utf8").trim().split("\n").map(JSON.parse);
  const replay = harness(960, 540, true); const map = mountMap(replay.Phaser, replay.host, replay.browser); replay.create();
  for (const event of demo) replay.emit("specvania:event", event);
  assert.ok(replay.descriptions["map-rooms"].textContent.includes("cleared"));
  assert.equal(replay.camera.following, null); map.destroy();
});


test("canvas_hud_overlay_blocks_have_opaque_palette_backgrounds_and_borders", async () => {
  const { UI_COLORS } = await load("pixel/palette.js");
  const html = readFileSync(path.join(staticRoot, "index.html"), "utf8");
  const css = html.match(/main\s*\{([^}]+)\}/)[1];
  assert.match(css, /background(?:-color)?:\s*var\(--(?:surface|background)\)/);
  assert.match(css, /border:\s*2px solid var\(--border\)/);
  assert.doesNotMatch(css, /opacity:/);
  for (const color of [UI_COLORS.surface, UI_COLORS.background]) assert.match(color, /^#[0-9a-f]{6}$/i);
  const { panel } = await load("hud/party.js");
  for (const id of ["party-hud", "bestiary-hud", "dialogue-hud"]) {
    const document = {createElement: () => ({style: {}, setAttribute() {}})};
    const block = panel(document, id, id);
    assert.ok([UI_COLORS.surface, UI_COLORS.background].includes(block.style.backgroundColor));
    assert.equal(block.style.border, `2px solid ${UI_COLORS.border}`);
    assert.ok(block.style.opacity === undefined || block.style.opacity === 1);
  }
  // The existing desktop title-bar test exercises mounting and drag behavior.
  const main = readFileSync(path.join(staticRoot, "main.js"), "utf8");
  assert.match(main, /backgroundColor:\s*UI_COLORS\.(?:surface|background)/);
  assert.match(main, /borderBottom:\s*`2px solid \$\{UI_COLORS\.border\}`/);
  assert.match(main, /webkitAppRegion:\s*"drag"/);
});
