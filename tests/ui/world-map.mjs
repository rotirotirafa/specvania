import assert from "node:assert/strict";
import fs from "node:fs";
import { pathToFileURL } from "node:url";

const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const base = pathToFileURL(`${process.cwd()}/src/specvania/ui/static/`);
const { layoutWorld, worldFromRooms, integerDisplayZoom } = await import(new URL("map/layout.js", base));
const { roomAppearance, drawRoom, projectEvent, mountMap } = await import(new URL("map/scene.js", base));
const { PALETTE } = await import(new URL("pixel/palette.js", base));
const world = worldFromRooms(payload.rooms, payload.areas, payload.doors);

if (process.argv[2] === "layout") {
  for (const overview of [false, true]) {
    const layout = layoutWorld(world, overview);
    assert.equal(layout.rooms.length, 38);
    assert.equal(layout.areas.length, 10);
    assert.deepEqual(layout, layoutWorld(worldFromRooms([...payload.rooms].reverse(),
      [...payload.areas].reverse(), payload.doors), overview));
    for (const room of layout.rooms) {
      assert.ok([room.x, room.y, room.width, room.height].every(Number.isInteger));
      assert.ok(room.width > 2 && room.height > 4);
      for (const other of layout.rooms) {
        if (room.id === other.id) continue;
        assert.ok(room.x + room.width <= other.x || other.x + other.width <= room.x ||
          room.y + room.height <= other.y || other.y + other.height <= room.y,
        `${room.id} overlaps ${other.id}`);
        const areaIndex = layout.areas.findIndex(area => area.id === room.area);
        const otherIndex = layout.areas.findIndex(area => area.id === other.area);
        if (areaIndex < otherIndex) assert.ok(room.y + room.height <= other.y);
      }
      const sources = room.depends_on.map(id => layout.rooms.find(room => room.id === id))
        .filter(source => source.area === room.area);
      assert.equal(room.depth, sources.length ? Math.max(...sources.map(room => room.depth)) + 1 : 0);
      if (overview) assert.ok(room.x + room.width <= 960 && room.y + room.height <= 540);
    }
    assert.equal(layout.doors.length, payload.rooms.length - payload.areas.length);
    for (const door of layout.doors) {
      const src = layout.rooms.find(room => room.id === door.src);
      const dst = layout.rooms.find(room => room.id === door.dst);
      assert.equal(door.points[0].x, src.x + src.width);
      assert.equal(door.points.at(-1).x, dst.x);
      for (let i = 1; i < door.points.length; i++) {
        assert.ok(door.points[i].x === door.points[i - 1].x || door.points[i].y === door.points[i - 1].y);
      }
    }
  }
  assert.deepEqual([integerDisplayZoom(319, 179), integerDisplayZoom(960, 540), integerDisplayZoom(1279, 719)], [1, 3, 3]);
  assert.throws(() => layoutWorld({areas: [{id: "A1"}], rooms: [
    {id: "T01", area: "A1", depends_on: ["T02"]},
    {id: "T02", area: "A1", depends_on: ["T01"]}], doors: []}), /Cyclic/);
}

// Rasterize the actual geometry with colors entirely discarded. This proves
// state marks survive grayscale, independently of palette brightness or hue.
class Monochrome {
  constructor(w, h) { this.w = w; this.h = h; this.pixels = Array(w * h).fill(0); }
  point(x, y) { if (x >= 0 && y >= 0 && x < this.w && y < this.h) this.pixels[y * this.w + x] = 1; }
  fillStyle(color, opacity = 1) {
    assert.ok(Object.values(PALETTE).some(value => value && parseInt(value.slice(1), 16) === color));
    this.opacity = opacity; return this;
  }
  lineStyle(width, color) { return this.fillStyle(color); }
  fillRect(x, y, w, h) {
    if (w === this.w && h === this.h) return this; // Remove background shading.
    if (this.opacity < 1) return this; // T30 tints/glows; preserve only the opaque accent silhouettes.
    for (let py = y; py < y + h; py++) for (let px = x; px < x + w; px++) this.point(px, py);
    return this;
  }
  strokeRect(x, y, w, h) {
    return this.lineBetween(x, y, x + w, y).lineBetween(x, y, x, y + h)
      .lineBetween(x, y + h, x + w, y + h).lineBetween(x + w, y, x + w, y + h);
  }
  lineBetween(x, y, endX, endY) {
    const steps = Math.max(Math.abs(endX - x), Math.abs(endY - y));
    for (let i = 0; i <= steps; i++) this.point(Math.round(x + (endX - x) * i / (steps || 1)),
      Math.round(y + (endY - y) * i / (steps || 1)));
    return this;
  }
}
if (process.argv[2] === "looks") {
  for (const [overview, w, h] of [[false, 80, 48], [true, 80, 48]]) {
    const images = [];
    for (const state of ["locked", "open", "active", "cleared", "failed", "skipped"]) {
      const raster = new Monochrome(w, h);
      drawRoom(raster, { x: 0, y: 0, width: w, height: h, state }, overview);
      images.push(raster.pixels.join(""));
      assert.equal(roomAppearance(state).label, state);
    }
    assert.equal(new Set(images).size, 6, "State marks collapse without color");
  }
}

if (process.argv[2] === "projection") {
  let current = projectEvent(worldFromRooms([]), {type: "world_loaded", ...payload});
  assert.deepEqual(current, world);
  const original = JSON.stringify(current);
  for (const [type, state] of [["room_entered", "active"], ["room_failed", "failed"],
    ["respawn", "active"], ["attempt_started", "active"], ["room_skipped", "skipped"]]) {
    current = projectEvent(current, {type, room_id: "T01"});
    assert.equal(current.rooms.find(room => room.id === "T01").state, state);
    assert.equal(current.rooms.find(room => room.id === "T02").state, "locked");
  }
  current = projectEvent(current, {type: "room_cleared", room_id: "T01"});
  assert.equal(current.rooms.find(room => room.id === "T01").state, "cleared");
  assert.equal(current.rooms.find(room => room.id === "T02").state, "open");
  assert.equal(JSON.stringify(world), original);
  // The mid-run projection retains redundant area metadata and dependencies.
  assert.deepEqual(worldFromRooms(current.rooms).areas, world.areas);
  // T23 is last in tasks.md (A10), but reconnects rebuild doors by room id.
  // Compare every edge, including duplicates, independently of that order.
  const doorOrder = (a, b) => a.dst.localeCompare(b.dst) || a.src.localeCompare(b.src);
  assert.deepEqual(worldFromRooms(current.rooms).doors.sort(doorOrder), [...world.doors].sort(doorOrder));
}

if (process.argv[2] === "mount") {
  const handlers = new Map();
  const keyboard = new Map();
  const renders = [];
  const camera = { zoom: 0, following: null, center: null,
    stopFollow() { this.following = null; },
    setZoom(zoom) { this.zoom = zoom; }, setBounds() {}, setScroll(x, y) { this.center = [x, y]; },
    centerOn(x, y) { this.center = [x, y]; }, startFollow(marker) { this.following = marker; } };
  const object = (x = 0, y = 0) => ({ x, y, destroy() {}, setInteractive() { return this; }, on() { return this; } });
  const graphics = new Monochrome(2000, 400);
  graphics.destroy = () => {};
  const scene = { cameras: {main: camera}, textures: {exists: () => true},
    input: {keyboard: {on: (type, fn) => keyboard.set(type, fn)}},
    add: {graphics: () => graphics, image: (x, y, key) => { renders.push(key); return object(x, y); },
      text: (x, y, label) => { renders.push(label); return object(x, y); }, zone: object} };
  let mediaHandler;
  let config;
  let zoom;
  const media = { matches: false, addEventListener: (type, fn) => { mediaHandler = fn; },
    removeEventListener: () => {} };
  const description = {};
  const browser = {innerHeight: 720, matchMedia: () => media,
    document: {getElementById: () => description},
    addEventListener: (type, fn) => handlers.set(type, fn),
    removeEventListener: type => handlers.delete(type) };
  const Phaser = { CANVAS: 1, Scale: {NONE: 0}, Game: class {
    constructor(value) { config = value; this.scale = {resize: (w, h) => { zoom = [w, h]; }}; }
    destroy() {} } };
  const mounted = mountMap(Phaser, {clientWidth: 1280, offsetTop: 0}, browser);
  assert.equal(config.width, 1280); assert.equal(config.height, 720);
  assert.equal(config.pixelArt, true); assert.equal(config.roundPixels, true);
  assert.equal(config.scale.mode, Phaser.Scale.NONE);
  assert.deepEqual(zoom, [1280, 720]);
  // Snapshot arrives before asynchronous Phaser create. It must not disappear.
  handlers.get("specvania:state")({detail: {rooms: payload.rooms, active_room: null}});
  config.scene.create.call(scene);
  assert.ok(renders.includes("T01") && renders.includes("T23"));
  handlers.get("specvania:event")({detail: {type: "room_entered", room_id: "T16"}});
  assert.ok(description.textContent.includes("T16: World map — active"));
  assert.ok(["mage:idle:0", "warrior:idle:0", "paladin:idle:0"].every(key => renders.includes(key)));
  assert.ok(camera.following);
  const target = layoutWorld(world).rooms.find(room => room.id === "T16");
  assert.deepEqual(camera.center, [target.x + target.width / 2, target.y + target.height / 2]);
  keyboard.get("keydown-M")({repeat: false});
  assert.equal(camera.zoom, 1);
  assert.deepEqual(camera.center, [layoutWorld(world).width / 2, layoutWorld(world).height / 2]);
  assert.equal(camera.following, null);
  keyboard.get("keydown-M")({repeat: false});
  mediaHandler({matches: true});
  assert.equal(camera.following, null);
  handlers.get("specvania:event")({detail: {type: "room_entered", room_id: "T02"}});
  assert.equal(camera.following, null);
  assert.ok(description.textContent.includes("T02: Config and `init` — active"));
  // A mid-run reconnect rebuilds geometry and states without world_loaded.
  handlers.get("specvania:state")({detail: {rooms: projectEvent(world, {type: "room_cleared", room_id: "T01"}).rooms,
    active_room: "T02"}});
  assert.ok(description.textContent.includes("T01: Scaffold the project — cleared"));
  assert.ok(description.textContent.includes("T02: Config and `init` — open"));
  mounted.destroy(); assert.equal(handlers.size, 0);
}
