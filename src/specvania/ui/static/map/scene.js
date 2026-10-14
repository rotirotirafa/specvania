import { t, onLanguageChange } from "../i18n/index.js";
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
  if (!appearances[state]) throw new Error(t("error.roomState", {state}));
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
  let world = worldFromRooms([]), scene = null, overview = false, partyRoom = null, activeArena = null;
  let width = browser.innerWidth || host.clientWidth || 960;
  let height = browser.innerHeight || 540;
  const objects = [];
  const animations = createMapAnimations(browser, () => redraw());
  let scrollBounds = null, overviewScroll = null, drag = null, dragged = false;
  const previousTabIndex = host.tabIndex;
  host.tabIndex = 0;
  const editable = target => /^(INPUT|TEXTAREA|SELECT)$/.test(target?.tagName) || target?.isContentEditable;
  const canScroll = () => scrollBounds && host.getAttribute?.("inert") == null;
  function scrollTo(x, y) {
    if (!scrollBounds) return;
    const {minX, maxX, maxY} = scrollBounds;
    overviewScroll = {x: Math.max(minX, Math.min(maxX, Math.round(x))),
      y: Math.max(0, Math.min(maxY, Math.round(y)))};
    scene.cameras.main.scrollX = overviewScroll.x;
    scene.cameras.main.scrollY = overviewScroll.y;
  }
  const onWheel = event => {
    if (!canScroll()) return;
    event.preventDefault();
    const unit = event.deltaMode === 1 ? TILE_SIZE : event.deltaMode === 2 ? height : 1;
    scrollTo(overviewScroll.x + (event.deltaX || 0) * unit, overviewScroll.y + (event.deltaY || 0) * unit);
  };
  const onScrollKey = event => {
    const focused = browser.document.activeElement;
    if (!canScroll() || editable(event.target) || !(focused === host || host.contains?.(focused))) return;
    const page = Math.max(TILE_SIZE, height - 2 * TILE_SIZE);
    const steps = {ArrowLeft: [-TILE_SIZE, 0], ArrowRight: [TILE_SIZE, 0],
      ArrowUp: [0, -TILE_SIZE], ArrowDown: [0, TILE_SIZE], PageUp: [0, -page], PageDown: [0, page]};
    const step = steps[event.key];
    if (!step) return;
    event.preventDefault();
    scrollTo(overviewScroll.x + step[0], overviewScroll.y + step[1]);
  };
  const onDragStart = event => {
    if (!canScroll() || event.button !== 0) return;
    host.focus?.({preventScroll: true});
    event.target?.setPointerCapture?.(event.pointerId);
    dragged = false;
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY,
      scrollX: overviewScroll.x, scrollY: overviewScroll.y, moved: false};
  };
  const onDragMove = event => {
    if (!canScroll() || !drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    drag.moved ||= Math.abs(dx) > 3 || Math.abs(dy) > 3;
    if (drag.moved) {dragged = true; scrollTo(drag.scrollX - dx, drag.scrollY - dy);}
  };
  const onDragEnd = event => {if (drag?.id === event.pointerId) drag = null;};
  host.addEventListener?.("wheel", onWheel, {passive: false});
  host.addEventListener?.("keydown", onScrollKey);
  host.addEventListener?.("pointerdown", onDragStart);
  browser.addEventListener("pointermove", onDragMove);
  browser.addEventListener("pointerup", onDragEnd);
  browser.addEventListener("pointercancel", onDragEnd);

  function redraw() {
    if (!scene) return;
    scene.cameras.main.stopFollow();
    for (const object of objects.splice(0)) object.destroy();
    const preview = Boolean(studioWorld && barHeight > 0);
    const layout = layoutWorld(world, overview || preview);
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
    if (!layout.rooms.length) {
      for (let x = 32; x < 192; x += TILE_SIZE) {
        tile(x, 32, "wall_top_middle"); tile(x, 48, "back_wall"); tile(x, 64, "floor_stones");
      }
      tile(144, 48, "door_double_left"); tile(160, 48, "door_double_right");
      tile(80, 48, "mage"); tile(48, 32, "banner");
    }
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
      const select = () => browser.dispatchEvent(new browser.CustomEvent("specvania:room-selected", { detail: { room_id: room.id } }));
      // Phaser's window listeners also deliver presses/releases on DOM overlays.
      zone.on("pointerdown", pointer => {if (!scrollBounds && pointer.downElement === game.canvas) select();});
      // In a scrollable overview wait for release, so dragging a chamber does
      // not open its panel. Keep the result after DOM pointerup: Phaser's
      // compatibility mouseup (or touchend) can arrive after that event.
      zone.on("pointerup", pointer => {if (scrollBounds && !dragged && pointer.downElement === game.canvas) select();});
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
    const arena = layout.areas.find(area => area.id === activeArena)?.boss;
    const camera = scene.cameras.main;
    const hud = browser.document.getElementById("castle-hud");
    if (hud) hud.hidden = overview; // M exposes the entire castle, including the stairwell.
    const overlayWidth = Math.min(width - 1, hud?.getBoundingClientRect?.().width || 0);
    const visibleWidth = width - overlayWidth;
    const viewport = preview ? {x: 16, y: 32, width: Math.max(1, width - overlayWidth - 32),
      height: Math.max(1, height - 32 - barHeight - 16)} : {x: 0, y: 0, width, height};
    const frame = preview && (layout.width > viewport.width || layout.height > viewport.height)
      && layout.areas.length ? layout.areas[0] : {x: 0, y: 0, width: layout.width, height: layout.height};
    camera.setViewport?.(viewport.x, viewport.y, viewport.width, viewport.height);
    camera.roundPixels = true;
    const zoom = castleViewZoom(preview ? viewport.width : overview ? visibleWidth : width, viewport.height, preview ? frame : layout, overview || preview);
    camera.setZoom(zoom);
    // A small overview needs negative bounds so Phaser does not clamp its
    // centered scroll to the top-left. Labels share the castle's coordinates.
    const viewWidth = viewport.width / zoom, viewHeight = viewport.height / zoom;
    const coveredWidth = preview ? 0 : Math.ceil(overlayWidth / zoom);
    const offset = preview ? 0 : Math.round(overlayWidth / (2 * zoom));
    camera.setBounds(overview || preview ? frame.x + Math.min(0, (frame.width - viewWidth) / 2) : 0,
      overview || preview ? frame.y + Math.min(0, (frame.height - viewHeight) / 2) : 0,
      Math.max(layout.width, viewWidth) + coveredWidth, Math.max(layout.height, viewHeight));
    const scrollable = overview && !preview && !arena
      && (layout.width > visibleWidth || layout.height > viewport.height);
    if (scrollable) {
      // At zoom 1 a tall castle starts at its top. Narrow castles stay centered;
      // wide ones can reach the right wall to the left of the measured HUD.
      const integerWidth = Math.floor(visibleWidth);
      const minX = Math.min(0, Math.round((layout.width - integerWidth) / 2));
      const maxX = Math.max(minX, layout.width - integerWidth);
      scrollBounds = {minX, maxX, maxY: Math.max(0, layout.height - viewport.height)};
      camera.setBounds(minX, 0, viewport.width + maxX - minX, Math.max(layout.height, viewport.height));
      const position = overviewScroll || {x: Math.round((layout.width - integerWidth) / 2), y: 0};
      scrollTo(position.x, position.y);
    } else {
      scrollBounds = overviewScroll = drag = null;
      dragged = false;
    }
    if (arena) {
      camera.setViewport?.(0, 0, width, height);
      camera.setZoom(Math.max(1, Math.min(4, Math.floor(width / (arena.width + 64)), Math.floor(height / 128))));
      // Center the real T30 arena below the HP header and above Continue.
      camera.setBounds(-width, -height, layout.width + width * 2, layout.height + height * 2);
      camera.centerOn(arena.x + Math.floor(arena.width / 2), arena.y + 24);
    } else if (!scrollable && (overview || preview || !layout.rooms.length)) camera.centerOn(Math.floor(frame.x + frame.width / 2) + offset, Math.floor(frame.y + frame.height / 2));
    else if (!scrollable) {
      const target = party || { x: layout.rooms[0].x + 40, y: layout.rooms[0].y + 24 };
      // Phaser subtracts its follow offset from the target before centering.
      // The HUD's screen width becomes world units at the integer zoom.
      if (party && !animations.isReducedMotion()) camera.startFollow(party, true, 1, 1, -offset || 0, 0);
      camera.centerOn(Math.round(target.x) + offset, Math.round(target.y));
    }
    const description = browser.document.getElementById("map-rooms");
    if (description) description.textContent = world.rooms.map(room => t("map.room", {area: room.area, id: room.id, title: room.title, state: t(`room.${room.state}`)})).join("\n");
  }
  const onExpedition = () => {overview = false; redraw();};
  browser.addEventListener("specvania:expedition-dialog", onExpedition);
  const onBossScreen = ({detail}) => {activeArena = detail.active ? detail.area_id : null; overview = false; redraw();};
  browser.addEventListener("specvania:boss-screen", onBossScreen);
  let studioWorld = null, revealedWorld = null, barHeight = 72;
  const onDialogueLayout = ({detail}) => {barHeight = detail.height; redraw();};
  browser.addEventListener("specvania:dialogue-layout", onDialogueLayout);
  function revealReady() {
    if (!studioWorld || revealedWorld === studioWorld) return;
    if (animations.inspect().revealed.length !== world.rooms.length) return;
    revealedWorld = studioWorld;
    browser.dispatchEvent(new browser.CustomEvent("specvania:world-revealed", {detail: {slug: studioWorld}}));
  }
  const onStudio = ({ detail }) => {
    if (["running", "finished"].includes(detail.phase)) {
      overview = false; studioWorld = null; redraw();
    } else if (detail.phase === "world_ready" && detail.world && detail.world_slug !== studioWorld) {
      studioWorld = detail.world_slug;
      world = worldFromRooms(detail.world.rooms, detail.world.areas, detail.world.doors);
      partyRoom = null; overview = false;
      animations.onEvent({type: "world_loaded", ...detail.world}, layoutWorld(world, overview)); redraw(); revealReady();
    } else if (detail.phase === "project_open") {
      studioWorld = revealedWorld = null; world = worldFromRooms([]); partyRoom = null; overview = false;
      animations.setState({rooms: []}, layoutWorld(world)); redraw();
    }
  };
  browser.addEventListener("specvania:studio-state", onStudio);
  const onState = ({ detail }) => {
    world = worldFromRooms(detail.rooms, detail.areas, detail.doors);
    partyRoom = detail.active_room || detail.party_room || null;
    animations.setState(detail, layoutWorld(world, overview)); redraw();
  };
  const onEvent = ({ detail }) => {
    world = projectEvent(world, detail);
    if (detail.type === "world_loaded") {partyRoom = null; overview = false; studioWorld = null;}
    if (["room_entered", "attempt_started", "respawn"].includes(detail.type)) partyRoom = detail.room_id;
    animations.onEvent(detail, layoutWorld(world, overview), partyRoom);
    redraw(); // No timeout or animation queue delaying chamber state.
  };
  browser.addEventListener("specvania:state", onState);
  browser.addEventListener("specvania:event", onEvent);
  const unsubscribe = onLanguageChange(redraw);
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
      if (browser.CustomEvent) browser.dispatchEvent?.(new browser.CustomEvent("specvania:textures-ready", {detail: this.textures}));
      this.input.keyboard.on("keydown-M", event => {
        if (host.getAttribute?.("inert") != null || event.repeat || /^(INPUT|TEXTAREA|SELECT)$/.test(event.target?.tagName) || event.target?.isContentEditable) return;
        overview = !overview; overviewScroll = drag = null; redraw();
      });
      redraw(); revealReady();
    }, update() { revealReady(); } },
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
    host.removeEventListener?.("wheel", onWheel);
    host.removeEventListener?.("keydown", onScrollKey);
    host.removeEventListener?.("pointerdown", onDragStart);
    host.tabIndex = previousTabIndex;
    browser.removeEventListener("pointermove", onDragMove);
    browser.removeEventListener("pointerup", onDragEnd);
    browser.removeEventListener("pointercancel", onDragEnd);
    browser.removeEventListener("specvania:expedition-dialog", onExpedition);
    browser.removeEventListener("specvania:boss-screen", onBossScreen);
    browser.removeEventListener("specvania:dialogue-layout", onDialogueLayout);
    browser.removeEventListener("specvania:studio-state", onStudio);
    browser.removeEventListener("specvania:state", onState);
    browser.removeEventListener("specvania:event", onEvent);
    browser.removeEventListener("resize", resize);
    browser.removeEventListener("specvania:settings", onSettings);
    browser.removeEventListener("specvania:replay-speed", onSpeed);
    unsubscribe(); animations.destroy(); game.destroy(true);
  } };
}
