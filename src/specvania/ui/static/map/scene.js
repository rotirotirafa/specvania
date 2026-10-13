import { PALETTE, AREA_COLORS, UI_COLORS } from "../pixel/palette.js";
import { preloadTextures, registerTextures, textureKey } from "../pixel/textures.js";
import { worldFromRooms, layoutWorld, integerDisplayZoom } from "./layout.js";
import { createMapAnimations } from "./animations.js";

/** Geometric marks and literal labels encode state even with all color removed. */
export function roomAppearance(state) {
  const appearances = {
    locked: { fill: "K", border: "S", mark: "fog", label: "locked" },
    open: { fill: "S", border: "W", mark: "entrance", label: "open" },
    active: { fill: "S", border: "Y", mark: "party", label: "active" },
    cleared: { fill: "W", border: "G", mark: "check", label: "cleared" },
    failed: { fill: "S", border: "R", mark: "cross", label: "failed" },
    skipped: { fill: "K", border: "N", mark: "slash", label: "skipped" },
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

export function drawRoom(graphics, room, overview) {
  const look = roomAppearance(room.state);
  const color = symbol => Number.parseInt(PALETTE[symbol].slice(1), 16);
  const { x, y, width: w, height: h } = room;
  graphics.fillStyle(color(look.fill)).fillRect(x, y, w, h);
  graphics.lineStyle(1, color(look.border)).strokeRect(x, y, w, h);
  const left = x + 2, right = x + w - 2, top = y + 2, bottom = y + h - 2;
  const mid = Math.floor((top + bottom) / 2);
  graphics.lineStyle(1, color(room.state === "cleared" ? "K" : "W"));
  switch (look.mark) {
    case "fog":
      graphics.fillStyle(color("S"));
      for (let py = top; py < bottom; py += 4) {
        for (let px = left; px < right; px += 4) graphics.fillRect(px, py, 1, 1);
      }
      break;
    case "entrance": graphics.strokeRect(left, mid, overview ? 2 : 6, bottom - mid); break;
    case "party":
      // Three silhouettes remain legible in overview; full sprites in detail.
      for (let i = 0; i < 3; i++) graphics.fillStyle(color("W")).fillRect(left + i * 2, mid, 1, 3);
      break;
    case "check": graphics.lineBetween(left, mid, Math.floor((left + right) / 2), bottom)
      .lineBetween(Math.floor((left + right) / 2), bottom, right, top); break;
    case "cross": graphics.lineBetween(left, top, right, bottom).lineBetween(left, bottom, right, top); break;
    case "slash": graphics.lineBetween(left, bottom, right, top); break;
  }
}

/** Install listeners before Phaser's asynchronous create so no snapshot is lost. */
export function mountMap(Phaser, host, browser = window) {
  let world = worldFromRooms([]);
  let scene = null;
  let overview = false;
  let partyRoom = null;
  const objects = [];
  const animations = createMapAnimations(browser);
  const media = browser.matchMedia("(prefers-reduced-motion: reduce)");
  let reducedMotion = media.matches;
  const motionChange = event => {
    reducedMotion = event.matches;
    if (reducedMotion && scene) scene.cameras.main.stopFollow();
  };
  media.addEventListener("change", motionChange);

  function redraw() {
    if (!scene) return;
    for (const object of objects.splice(0)) object.destroy();
    const layout = layoutWorld(world, overview);
    const graphics = scene.add.graphics();
    objects.push(graphics);
    const numeric = symbol => Number.parseInt(PALETTE[symbol].slice(1), 16);
    const byId = new Map(layout.rooms.map(room => [room.id, room]));
    for (const door of layout.doors) {
      const open = byId.get(door.src).state === "cleared";
      graphics.lineStyle(overview ? 1 : 3, numeric(open ? "W" : "S"));
      for (let i = 1; i < door.points.length; i++) {
        const a = door.points[i - 1], b = door.points[i];
        graphics.lineBetween(a.x, a.y, b.x, b.y);
      }
      if (!overview) {
        const point = door.points[0];
        objects.push(scene.add.image(point.x + 8, point.y, textureKey("door", open ? "open" : "locked")));
      }
    }
    const text = (x, y, label, color) => {
      const object = scene.add.text(x, y, label, { fontFamily: '"Press Start 2P", monospace',
        fontSize: overview ? "6px" : "8px", color, resolution: 1 });
      objects.push(object);
    };
    for (const area of layout.areas) {
      text(area.x, area.y, area.id, PALETTE[AREA_COLORS[area.id] || "B"]);
    }
    for (const room of layout.rooms) {
      drawRoom(graphics, room, overview);
      if (!overview) {
        const ink = room.state === "cleared" ? PALETTE.K : PALETTE.W;
        text(room.x + 8, room.y + 5, room.id, ink);
        text(room.x + 8, room.y + 17, room.state, ink);
      }
    }
    animations.render(scene, layout, world.rooms.some(room => room.state === "active") ? partyRoom : null);
    const camera = scene.cameras.main;
    camera.stopFollow();
    camera.setZoom(1);
    camera.setBounds(0, 0, layout.width, layout.height);
    const target = byId.get(partyRoom) || layout.rooms[0];
    if (overview || !target) camera.setScroll(0, 0);
    else {
      const marker = scene.add.zone(target.x + Math.floor(target.width / 2), target.y + Math.floor(target.height / 2));
      objects.push(marker);
      // Snap follows, without pans/tweens. Reduced motion stays completely still.
      if (!reducedMotion) camera.startFollow(marker, true, 1, 1);
      camera.centerOn(marker.x, marker.y);
    }
    const description = browser.document.getElementById("map-rooms");
    if (description) description.textContent = world.rooms.map(room => `${room.area} ${room.id}: ${room.title} — ${room.state}`).join("\n");
  }
  const onState = ({ detail }) => {
    world = worldFromRooms(detail.rooms);
    partyRoom = detail.active_room || detail.party_room || null;
    animations.setState(detail);
    redraw();
  };
  const onEvent = ({ detail }) => {
    world = projectEvent(world, detail);
    animations.onEvent(detail);
    if (["room_entered", "attempt_started", "respawn"].includes(detail.type)) partyRoom = detail.room_id;
    redraw(); // No timeout or animation queue delaying room state.
  };
  browser.addEventListener("specvania:state", onState);
  browser.addEventListener("specvania:event", onEvent);
  const game = new Phaser.Game({
    type: Phaser.CANVAS, parent: host, width: 320, height: 180,
    pixelArt: true, roundPixels: true, backgroundColor: UI_COLORS.background,
    scale: { mode: Phaser.Scale.NONE, zoom: 1 }, audio: { noAudio: true }, banner: false,
    scene: { preload() { preloadTextures(this); }, create() {
      scene = this;
      registerTextures(this);
      this.input.keyboard.on("keydown-M", event => {
        if (event.repeat) return;
        overview = !overview;
        redraw();
      });
      redraw();
    } },
  });
  const resize = () => game.scale.setZoom(integerDisplayZoom(host.clientWidth, browser.innerHeight - host.offsetTop - 24));
  browser.addEventListener("resize", resize);
  resize();
  return { destroy() {
    browser.removeEventListener("specvania:state", onState);
    browser.removeEventListener("specvania:event", onEvent);
    browser.removeEventListener("resize", resize);
    media.removeEventListener("change", motionChange);
    animations.destroy();
    game.destroy(true);
  } };
}
