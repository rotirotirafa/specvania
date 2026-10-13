import { PALETTE, AREA_COLORS, UI_COLORS } from "../pixel/palette.js";
import { TILE_SIZE } from "../pixel/cast.js";
import { preloadTextures, registerTextures, textureKey } from "../pixel/textures.js";
import { worldFromRooms, layoutWorld, castleViewZoom } from "./layout.js";
import { createMapAnimations } from "./animations.js";

const numeric = symbol => Number.parseInt(PALETTE[symbol].slice(1), 16);

/** Luminance and pixel silhouettes both encode state, independently of hue. */
export function roomAppearance(state) {
  const appearances = {
    locked: { fill: "K", border: "S", mark: "padlock", label: "locked" },
    open: { fill: "B", border: "W", mark: "torch", label: "open" },
    active: { fill: "Y", border: "O", mark: "party", label: "active" },
    cleared: { fill: "W", border: "G", mark: "check", label: "cleared" },
    failed: { fill: "R", border: "R", mark: "cross", label: "failed" },
    skipped: { fill: "S", border: "S", mark: "cobweb", label: "skipped" },
  };
  if (!appearances[state]) throw new Error(`Unknown room state: ${state}`);
  return appearances[state];
}

/** Same projection for live and replay. A skipped source never opens a door. */
export function projectEvent(world, event) {
  if (event.type === "world_loaded") return worldFromRooms(event.rooms, event.areas, event.doors);
  const states = { room_entered: "active", attempt_started: "active", respawn: "active",
    room_cleared: "cleared", room_failed: "failed", room_skipped: "skipped" };
  const rooms = world.rooms.map(room => ({ ...room,
    state: room.id === event.room_id && states[event.type] ? states[event.type] : room.state }));
  const byId = new Map(rooms.map(room => [room.id, room]));
  for (const room of rooms) {
    if (room.state === "locked" || room.state === "open") {
      room.state = room.depends_on.every(id => byId.get(id)?.state === "cleared") ? "open" : "locked";
    }
  }
  return { ...world, rooms };
}

/** Pack omissions only: padlock, torch/glow, check, failure cross, cobweb.
 * All accents are integer pixel rectangles, never smoothed vector strokes.
 * Three small lights accompany the active party, visible even in overview.
 */
export function drawRoom(graphics, room) {
  const look = roomAppearance(room.state);
  const { x, y, width: w, height: h } = room;
  graphics.fillStyle(numeric(look.fill), 0.7).fillRect(x, y + 16, w, h - 32);
  const pixel = (px, py, width, height, color) =>
    graphics.fillStyle(numeric(color), 1).fillRect(x + px, y + py, width, height);
  switch (look.mark) {
    case "padlock":
      pixel(5, 20, 6, 2, "Y"); pixel(4, 22, 2, 3, "Y"); pixel(10, 22, 2, 3, "Y");
      pixel(3, 25, 10, 6, "Y"); pixel(7, 26, 2, 3, "K");
      break;
    case "torch":
      graphics.fillStyle(numeric("Y"), 0.25).fillRect(x + 17, y + 16, 16, 16);
      pixel(24, 23, 2, 8, "N"); pixel(21, 19, 8, 5, "O"); pixel(23, 16, 4, 6, "Y");
      pixel(24, 18, 2, 3, "W");
      break;
    case "party":
      for (let i = 0; i < 3; i++) pixel(21 + i * 16, 13, 6, 2, "W");
      break;
    case "check":
      for (const [px, py] of [[57, 6], [59, 8], [61, 6], [63, 4], [65, 2]]) pixel(px, py, 2, 2, "G");
      break;
    case "cross":
      for (let i = 0; i < 5; i++) {
        pixel(23 + i * 2, 18 + i * 2, 2, 2, "W");
        pixel(31 - i * 2, 18 + i * 2, 2, 2, "W");
      }
      break;
    case "cobweb":
      // Mixing the pale and slate palette inks makes a light, desaturated grey.
      graphics.fillStyle(numeric("W"), 0.8).fillRect(x, y, w, h);
      graphics.fillStyle(numeric("S"), 0.2).fillRect(x, y, w, h);
      for (const mirror of [false, true]) {
        const strand = (px, py, width, height) =>
          pixel(mirror ? w - px - width : px, py, width, height, "W");
        strand(0, 14, 24, 2); strand(0, 14, 2, 16);
        for (let i = 0; i < 12; i++) strand(22 - i * 2, 14 + i, 2, 2);
        for (let i = 0; i < 8; i++) strand(14 - i * 2, 14 + i, 2, 2);
        for (let i = 0; i < 6; i++) strand(i * 2, 14 + i, 2, 2);
      }
      break;
  }
}

/** Install listeners before Phaser's asynchronous create so no snapshot is lost. */
export function mountMap(Phaser, host, browser = window) {
  let world = worldFromRooms([]), scene = null, overview = false, partyRoom = null;
  let width = browser.innerWidth || host.clientWidth || 960;
  let height = browser.innerHeight || 540;
  const objects = [];
  const animations = createMapAnimations(browser, () => redraw());

  function redraw() {
    if (!scene) return;
    scene.cameras.main.stopFollow();
    for (const object of objects.splice(0)) object.destroy();
    const layout = layoutWorld(world, overview);
    const tile = (x, y, name) => {
      const object = scene.add.image(x + 8, y + 8, textureKey(name, "idle"));
      objects.push(object);
      return object;
    };
    const text = (x, y, label, color, wrap) => {
      const object = scene.add.text(x, y, label, { fontFamily: '"Press Start 2P", monospace',
        fontSize: "8px", color, resolution: 1, wordWrap: wrap ? { width: wrap, useAdvancedWrap: true } : undefined });
      objects.push(object);
    };
    const chamber = room => {
      for (let column = 0; column < room.width / TILE_SIZE; column++) {
        const x = room.x + column * TILE_SIZE;
        tile(x, room.y, column === 0 ? "wall_top_left" : column === room.width / TILE_SIZE - 1 ? "wall_top_right" : "wall_top_middle");
        tile(x, room.y + 16, column === 0 ? "wall_left" : column === room.width / TILE_SIZE - 1 ? "wall_right" : "back_wall");
        tile(x, room.y + 32, column === 2 ? "floor_grate" : "floor_stones");
      }
    };
    for (const area of layout.areas) {
      // Continuous masonry joins chambers, hallway, boss arena and stairwell.
      for (let x = 128; x < area.boss.x; x += 16) {
        tile(x, area.y, "wall_top_middle"); tile(x, area.y + 16, "back_wall");
        tile(x, area.y + 32, "floor_plain");
      }
      text(area.x, area.y + 3, `${area.id}\n${area.title}`, PALETTE[AREA_COLORS[area.id] || "B"], 104);
      chamber(area.boss);
      tile(area.boss.x + 16, area.boss.y + 16, "banner");
      tile(area.boss.x + 48, area.boss.y + 16, "wall_grate");
      text(area.boss.x + 6, area.boss.y + 4, area.id, PALETTE.W);
    }
    for (const room of layout.rooms) {
      chamber(room);
      tile(room.x, room.y + 16, room.state === "locked" ? "door_closed" : "door_open_middle");
      const accents = scene.add.graphics(); objects.push(accents); drawRoom(accents, room);
      if (room.state !== "active") tile(room.x + 48, room.y + 16,
        room.state === "cleared" ? "chest_open" : "chest_closed");
      text(room.x + 18, room.y + 4, room.id, PALETTE.W);
      const zone = scene.add.zone(room.x + room.width / 2, room.y + room.height / 2, room.width, room.height);
      objects.push(zone);
      zone.setInteractive();
      zone.on("pointerdown", () => browser.dispatchEvent(new browser.CustomEvent("specvania:room-selected", { detail: { room_id: room.id } })));
    }
    for (const door of layout.doors) {
      tile(door.x, door.y + 16, door.locked ? "door_closed" : "door_open_middle");
      if (door.locked) {
        const accents = scene.add.graphics(); objects.push(accents);
        drawRoom(accents, { ...door, state: "locked" });
      }
    }
    for (const stair of layout.stairs) {
      // Three successive rows bridge the lower threshold to the next floor.
      for (let row = 0; row < 3; row++) tile(stair.x, stair.y + row * 16, "stairs");
      tile(stair.x - 16, stair.y, "door_open_middle");
      tile(stair.x - 16, stair.y + 48, "door_open_middle");
    }
    const party = animations.render(scene, layout, partyRoom);
    const camera = scene.cameras.main;
    const hud = browser.document.getElementById("castle-hud");
    if (hud) hud.hidden = overview; // M exposes the entire castle, including the stairwell.
    const overlayWidth = Math.min(width - 1, hud?.getBoundingClientRect?.().width || 0);
    const visibleWidth = width - overlayWidth;
    camera.roundPixels = true;
    const zoom = castleViewZoom(overview ? visibleWidth : width, height, layout, overview);
    camera.setZoom(zoom);
    // A small overview needs negative bounds so Phaser does not clamp its
    // centered scroll to the top-left. Labels share the castle's coordinates.
    const viewWidth = width / zoom, viewHeight = height / zoom;
    const coveredWidth = Math.ceil(overlayWidth / zoom);
    const offset = Math.round(overlayWidth / (2 * zoom));
    camera.setBounds(overview ? Math.min(0, (layout.width - viewWidth) / 2) : 0,
      overview ? Math.min(0, (layout.height - viewHeight) / 2) : 0,
      Math.max(layout.width, viewWidth) + coveredWidth, Math.max(layout.height, viewHeight));
    if (overview || !layout.rooms.length) camera.centerOn(Math.floor(layout.width / 2) + offset, Math.floor(layout.height / 2));
    else {
      const target = party || { x: layout.rooms[0].x + 40, y: layout.rooms[0].y + 24 };
      // Phaser subtracts its follow offset from the target before centering.
      // The HUD's screen width becomes world units at the integer zoom.
      if (party && !animations.isReducedMotion()) camera.startFollow(party, true, 1, 1, -offset || 0, 0);
      camera.centerOn(Math.round(target.x) + offset, Math.round(target.y));
    }
    const description = browser.document.getElementById("map-rooms");
    if (description) description.textContent = world.rooms.map(room => `${room.area} ${room.id}: ${room.title} — ${room.state}`).join("\n");
  }
  const onState = ({ detail }) => {
    world = worldFromRooms(detail.rooms, detail.areas, detail.doors);
    partyRoom = detail.active_room || detail.party_room || null;
    animations.setState(detail, layoutWorld(world, overview)); redraw();
  };
  const onEvent = ({ detail }) => {
    world = projectEvent(world, detail);
    if (detail.type === "world_loaded") partyRoom = null;
    if (["room_entered", "attempt_started", "respawn"].includes(detail.type)) partyRoom = detail.room_id;
    animations.onEvent(detail, layoutWorld(world, overview), partyRoom);
    redraw(); // No timeout or animation queue delaying chamber state.
  };
  browser.addEventListener("specvania:state", onState);
  browser.addEventListener("specvania:event", onEvent);
  const onSettings = ({ detail }) => animations.setReducedMotion(detail.reducedMotion);
  const onSpeed = ({ detail }) => animations.setReplaySpeed(detail.speed);
  browser.addEventListener("specvania:settings", onSettings);
  browser.addEventListener("specvania:replay-speed", onSpeed);
  const game = new Phaser.Game({
    type: Phaser.CANVAS, parent: host, width, height,
    pixelArt: true, roundPixels: true, backgroundColor: UI_COLORS.background,
    scale: { mode: Phaser.Scale.NONE, zoom: 1 }, audio: { noAudio: true }, banner: false,
    scene: { preload() { preloadTextures(this); }, create() {
      scene = this;
      registerTextures(this);
      this.input.keyboard.on("keydown-M", event => {
        if (event.repeat || /^(INPUT|TEXTAREA|SELECT)$/.test(event.target?.tagName) || event.target?.isContentEditable) return;
        overview = !overview; redraw();
      });
      redraw();
    } },
  });
  const resize = () => {
    width = Math.max(1, Math.floor(browser.innerWidth || host.clientWidth || 960));
    height = Math.max(1, Math.floor(browser.innerHeight || 540));
    game.scale.resize(width, height); redraw();
  };
  browser.addEventListener("resize", resize);
  resize();
  return { setReducedMotion: animations.setReducedMotion,
    setReplaySpeed: animations.setReplaySpeed, destroy() {
    browser.removeEventListener("specvania:state", onState);
    browser.removeEventListener("specvania:event", onEvent);
    browser.removeEventListener("resize", resize);
    browser.removeEventListener("specvania:settings", onSettings);
    browser.removeEventListener("specvania:replay-speed", onSpeed);
    animations.destroy(); game.destroy(true);
  } };
}
