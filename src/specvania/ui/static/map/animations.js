import { bossForArea, monsterForBug } from "../pixel/cast.js";
import { PALETTE } from "../pixel/palette.js";
import { textureKey } from "../pixel/textures.js";
import { bestiaryFromState, projectBestiary, openBugs } from "../hud/bestiary.js";

/* Event → animation (ANIMATION_DISPATCH is the sole dispatch map).
 * | Event | Animation |
 * | room_entered | walk |
 * | action | swing |
 * | model_call | plan |
 * | clarify_question | plan |
 * | clarify_answer | plan |
 * | spec_written | plan |
 * | review | shield |
 * | respawn | respawn |
 * | cooldown | rest |
 * | boss_started | arena |
 * | boss_scenario | hit |
 * | boss_result | bossResult |
 * | world_loaded | reveal |
 * | bug_closed | defeat |
 * plan applies only to Mage model calls; hit only to passed scenarios.
 * Failed review adds shake + a lost HUD heart on the map. Non-cooldown
 * events end rest for their role (or the whole party for unscoped events).
 */
export const ANIMATION_DISPATCH = Object.freeze({
  room_entered: "walk", action: "swing", model_call: "plan",
  clarify_question: "plan", clarify_answer: "plan", spec_written: "plan",
  review: "shield", respawn: "respawn", cooldown: "rest", boss_started: "arena",
  boss_scenario: "hit", boss_result: "bossResult", world_loaded: "reveal", bug_closed: "defeat",
});
export const QUEUE_CAP = 4; // Includes the running job; bounded per actor.
const ROLES = Object.freeze(["mage", "warrior", "paladin"]);
const ink = symbol => Number.parseInt(PALETTE[symbol].slice(1), 16);
const point = (x, y) => ({ x: Math.round(x), y: Math.round(y) });
const position = (sprite, p) => {
  if (!sprite) return;
  Object.assign(sprite, point(p.x, p.y)); sprite.setPosition?.(sprite.x, sprite.y);
};
const opacity = (sprite, value) => { if (sprite) { sprite.alpha = value; sprite.setAlpha?.(value); } };
const visible = (sprite, value) => sprite?.setVisible?.(value);

/** Door thresholds and stair points come directly from T30, in pixel units.
 * Actors regroup in chambers; all cross the same door/stair thresholds.
 * Stair paths describe floor surfaces,
 * hence the -8px adjustment to place the character's centre above the floor.
 */
export function walkingPath(layout, source, destination, offset = 40) {
  if (!source || !destination) return [];
  const path = [], append = (p, formation = 0) => path.push(point(p.x + formation, p.y - 8));
  if (source.area !== destination.area) {
    const from = layout.areas.findIndex(area => area.id === source.area);
    const to = layout.areas.findIndex(area => area.id === destination.area);
    const direction = Math.sign(to - from);
    for (let i = from; i !== to; i += direction) {
      const floor = layout.areas[i];
      append({ x: floor.boss.x + 40, y: floor.boss.y + 32 }, offset - 40);
      const stair = layout.stairs[direction > 0 ? i : i - 1];
      for (const p of direction > 0 ? stair.points : [...stair.points].reverse()) append(p);
    }
    const arena = layout.areas[to].boss;
    append({ x: arena.x + 40, y: arena.y + 32 }, offset - 40);
    source = arena;
  }
  const doors = layout.doors.filter(door => door.area === destination.area);
  const forward = source.x < destination.x;
  for (const door of forward ? doors : [...doors].reverse()) {
    if (door.x >= Math.min(source.x, destination.x) && door.x <= Math.max(source.x, destination.x)) {
      for (const p of forward ? door.points : [...door.points].reverse()) append(p);
    }
  }
  path.push(point(destination.x + offset, destination.y + 24));
  return path;
}

/** Per-mount state only. No timers, image files or alternate replay projection.
 * Jobs contain only visual steps. State is applied at dispatch, never deferred
 * behind a tween. Saturation settles oldest jobs in order, including callbacks.
 * Every effect is owned by its cue and destroyed when that work settles.
 * Reduced motion preserves projected state without retaining transient art.
 */
export function createMapAnimations(browser = window, onMotionChange = () => {}) {
  const media = browser.matchMedia("(prefers-reduced-motion: reduce)");
  let mediaReduced = media.matches, disposed = false;
  let setting = false;
  try { setting = JSON.parse(browser.localStorage?.getItem("specvania.settings") || "null")?.reducedMotion === true; } catch { /* Storage may be unavailable in a browser tab. */ }
  let scene = null, layout = null, location = null, bestiary = bestiaryFromState({}), speed = 1;
  const actors = new Map(), revealed = new Set();
  let failedReviews = 0, pendingSnapshot = null;
  const reduced = () => mediaReduced || setting;

  function actor(id, name, target) {
    let value = actors.get(id);
    if (!value) {
      value = { id, name, target, sprite: null, graphics: null, equipment: null,
        queue: [], cue: null, cueVersion: 0, resting: false, heart: null, heartLost: false,
        closing: false, result: null, hits: 0 };
      actors.set(id, value);
    }
    return value;
  }
  function paint(a, phase = 1) {
    if (!a.sprite) return;
    const { x, y } = a.sprite;
    const effect = !reduced() && a.sprite.visible !== false;
    const equipment = effect && (a.cue === "swing" || a.cue === "shield");
    if (equipment && !a.equipment) a.equipment = scene.add.image(x, y,
      textureKey(a.cue === "swing" ? "sword_long" : "shield_round", "idle"));
    if (!equipment) { a.equipment?.destroy(); a.equipment = null; }
    const pixels = effect && ["plan", "rest", "hit", "escape"].includes(a.cue);
    if (pixels && !a.graphics) a.graphics = scene.add.graphics();
    if (!pixels) { a.graphics?.destroy(); a.graphics = null; }
    a.graphics?.clear?.();
    const rect = (px, py, w, h, color) => a.graphics.fillStyle(ink(color), 1)
      .fillRect(Math.round(x + px), Math.round(y + py), w, h);
    visible(a.equipment, a.cue === "swing" || a.cue === "shield");
    if (a.equipment) {
      position(a.equipment, point(x + 10 + (a.cue === "swing" ? Math.sin(phase * Math.PI) * 8 : 0),
        y - Math.sin(phase * Math.PI) * (a.cue === "swing" ? 10 : 4)));
    }
    if (pixels && a.cue === "plan") {
      for (const [px, py] of [[-10, -12], [8, -16], [13, -5]]) {
        const lift = Math.round(Math.sin(phase * Math.PI) * 3);
        rect(px - 1, py - lift, 3, 1, "Y"); rect(px, py - 1 - lift, 1, 3, "W");
      }
    }
    if (pixels && a.cue === "rest" && a.resting) {
      // Three tiny z glyphs, pixel primitives rather than text or new art.
      for (const [px, py] of [[-4, -13], [2, -17], [8, -21]]) {
        const lift = Math.round(Math.sin(phase * Math.PI) * 2);
        rect(px, py - lift, 3, 1, "W"); rect(px + 1, py + 1 - lift, 1, 1, "W"); rect(px, py + 2 - lift, 3, 1, "W");
      }
    }
    if (pixels && a.cue === "hit") {
      rect(-8, -8, 16, 2, "W"); rect(-2, -11, 4, 22, "Y");
      opacity(a.graphics, reduced() ? 1 : 0.4 + Math.abs(Math.cos(phase * Math.PI * 3)) * 0.6);
    } else {
      opacity(a.graphics, 1);
    }
    if (pixels && a.cue === "escape") {
      rect(-7, 5, 4, 2, "S"); rect(1, 2, 4, 2, "W"); rect(7, -1, 4, 2, "S");
    }
    if (a.heart) {
      position(a.heart, point(x, y - 18 - Math.sin(phase * Math.PI / 2) * 10));
    }
  }
  function finish(a) {
    const job = a.queue.shift();
    if (!job) return;
    if (job.tween) { job.tween.stop?.(); scene?.tweens?.killTweensOf(job.proxy); }
    job.done = true; job.end();
  }
  function settleActor(a) { while (a.queue.length) finish(a); paint(a); }
  function settle() { for (const a of actors.values()) settleActor(a); }
  function start(a) {
    const job = a.queue[0];
    if (!job || job.started || !scene) return;
    job.started = true;
    if (reduced() || !scene.tweens) { finish(a); start(a); return; }
    let index = 0;
    const next = () => {
      if (job.done) return;
      const step = job.steps[index++];
      if (!step) { finish(a); start(a); return; }
      job.proxy = { t: 0 };
      step.begin?.();
      job.tween = scene.tweens.add({ targets: job.proxy, t: 1,
        duration: step.duration / speed, ease: "Linear",
        onUpdate: () => { if (!job.done) step.update(job.proxy.t); },
        onComplete: () => { if (!job.done) { step.update(1); next(); } } });
    };
    next();
  }
  function enqueue(a, steps, end) {
    if (speed >= 2) settleActor(a);
    while (a.queue.length >= QUEUE_CAP) finish(a);
    a.queue.push({ steps, end, started: false, done: false });
    start(a);
  }
  function pulse(a, cue, duration = 320, update = () => {}, cleanup = () => {}) {
    const version = ++a.cueVersion;
    a.cue = cue; paint(a, 0);
    enqueue(a, [{ duration, update: t => { update(t); paint(a, t); } }], () => {
      update(1);
      cleanup(a.cueVersion === version);
      if (a.cueVersion === version) a.cue = null;
      paint(a);
    });
  }
  function move(a, path, hop = true) {
    if (!path.length) return;
    settleActor(a);
    clearHeart(a); a.cue = null; a.cueVersion++; a.resting = false; paint(a);
    a.target = path.at(-1);
    let from;
    const steps = path.map(to => ({ duration: 120 + Math.max(0, ROLES.indexOf(a.id)) * 20,
      begin: () => { from = point(a.sprite?.x ?? to.x, a.sprite?.y ?? to.y); },
      update: t => {
        position(a.sprite, point(from.x + (to.x - from.x) * t,
          from.y + (to.y - from.y) * t - (hop ? Math.sin(t * Math.PI) * 4 : 0)));
        paint(a);
      } }));
    const target = a.target;
    enqueue(a, steps, () => { position(a.sprite, target); paint(a); });
  }
  function discard(a) {
    settleActor(a);
    for (const object of [a.sprite, a.graphics, a.equipment, a.heart]) object?.destroy();
    actors.delete(a.id);
  }
  function reset() {
    for (const a of [...actors.values()]) discard(a);
    revealed.clear(); location = null; failedReviews = 0; pendingSnapshot = null;
  }
  function clearHeart(a) { a.heart?.destroy(); a.heart = null; a.heartLost = false; }
  function partyAt(destination, path = false) {
    for (const [index, role] of ROLES.entries()) {
      const a = actors.get(role); if (!a) continue;
      clearHeart(a); a.cue = null; a.cueVersion++;
      const offset = 24 + index * 16;
      if (path) move(a, walkingPath(layout, location, destination, offset));
      else { a.target = point(destination.x + offset, destination.y + 24); position(a.sprite, a.target); }
      paint(a);
    }
    location = destination;
  }
  function revealGraphics(a, count) {
    if (!layout) return;
    a.graphics?.clear?.();
    const rooms = layout.rooms;
    for (let i = 0; i < rooms.length; i++) {
      const room = rooms[i];
      if (i < count || revealed.has(room.id)) revealed.add(room.id);
      else a.graphics?.fillStyle(ink("K"), 1).fillRect(room.x, room.y, room.width, room.height);
    }
  }
  function attach(a) {
    if (a.sprite || !scene) return;
    a.sprite = scene.add.image(a.target.x, a.target.y, textureKey(a.name, "idle"));
    if (a.heartLost) a.heart = scene.add.image(a.target.x, a.target.y - 28, textureKey("heart", "idle"));
    paint(a);
    if (a.result) {
      if (a.result === "escaped") position(a.sprite, point(a.target.x + 32, a.target.y));
      visible(a.sprite, false);
    }
  }
  function restoreSnapshot() {
    if (!pendingSnapshot) return;
    const snapshot = pendingSnapshot; pendingSnapshot = null;
    for (const room of layout.rooms) revealed.add(room.id);
    for (const member of snapshot.party || []) {
      const a = actors.get(member.role); if (a) a.resting = member.status === "cooldown";
    }
    for (const boss of snapshot.bosses || []) {
      const a = actors.get(`boss:${boss.area_id}`); if (!a) continue;
      a.hits = boss.results.filter(result => result.passed).length;
      if (typeof boss.passed === "boolean") {
        a.result = boss.passed ? "defeated" : "escaped";
        a.cue = boss.passed ? null : "escape";
        if (!boss.passed) position(a.sprite, point(a.target.x + 32, a.target.y));
        visible(a.sprite, false);
      }
    }
  }
  function render(currentScene, currentLayout, activeRoom) {
    scene = currentScene; layout = currentLayout;
    const destination = layout.rooms.find(room => room.id === activeRoom);
    const home = destination || layout.rooms[0];
    if (home) for (const [index, role] of ROLES.entries()) attach(actor(role, role, point(home.x + 24 + index * 16, home.y + 24)));
    if (!location && home) location = home;
    for (const floor of layout.areas) {
      const boss = actor(`boss:${floor.id}`, bossForArea(floor.id), point(floor.boss.x + 40, floor.boss.y + 24));
      attach(boss);
    }
    for (const bug of openBugs(bestiary)) {
      const room = layout.rooms.find(room => room.id === bug.room_id); if (!room) continue;
      const siblings = openBugs(bestiary).filter(other => other.room_id === room.id).sort((a, b) => a.bug_id.localeCompare(b.bug_id));
      const index = siblings.findIndex(other => other.bug_id === bug.bug_id);
      const id = `monster:${bug.bug_id}`;
      if (actors.get(id)?.closing) discard(actors.get(id));
      const a = actor(id, monsterForBug(bug.bug_id), point(room.x + room.width - 24 - Math.min(index, 2) * 16, room.y + 24));
      attach(a);
    }
    restoreSnapshot();
    for (const a of actors.values()) {
      if (a.id === "castle" && !a.graphics) a.graphics = scene.add.graphics();
      paint(a); start(a);
      for (const object of [a.sprite, a.equipment, a.graphics, a.heart]) if (object) scene.children?.bringToTop(object);
    }
    const castle = actors.get("castle");
    if (castle) { revealGraphics(castle, revealed.size); scene.children?.bringToTop(castle.graphics); }
    return actors.get("warrior")?.sprite || null;
  }
  const handlers = {
    walk(event) { const room = layout?.rooms.find(room => room.id === event.room_id); if (room) partyAt(room, true); },
    swing() { const a = actors.get("warrior"); if (a) pulse(a, "swing"); },
    plan(event) {
      if (event.type === "model_call" && event.role !== "mage") return;
      const a = actors.get("mage"); if (a) pulse(a, "plan", 500);
    },
    shield(event) {
      const paladin = actors.get("paladin"); if (paladin) pulse(paladin, "shield");
      if (event.verdict !== "fail") return;
      failedReviews++;
      const warrior = actors.get("warrior"); if (!warrior) return;
      clearHeart(warrior);
      warrior.heartLost = true;
      if (scene) warrior.heart = scene.add.image(warrior.sprite.x, warrior.sprite.y - 18, textureKey("heart", "idle"));
      const heart = warrior.heart;
      const origin = point(warrior.target.x, warrior.target.y);
      pulse(warrior, "shake", 400, t => position(warrior.sprite, point(origin.x + Math.sin(t * Math.PI * 6) * 3, origin.y)), current => {
        heart?.destroy();
        if (current || (heart && warrior.heart === heart)) clearHeart(warrior);
      });
    },
    respawn(event) {
      const room = layout?.rooms.find(room => room.id === event.room_id); if (!room) return;
      const a = actors.get("warrior"); if (!a) return;
      clearHeart(a); a.cue = null; a.cueVersion++;
      move(a, [point(room.x + 8, room.y + 24)], false); location = room;
    },
    rest(event) { const a = actors.get(event.role); if (a) { a.resting = true; pulse(a, "rest", 240); } },
    arena(event) {
      const floor = layout?.areas.find(area => area.id === event.area_id); if (!floor) return;
      const boss = actors.get(`boss:${event.area_id}`); settleActor(boss);
      boss.result = null; boss.hits = 0; boss.cue = null; boss.cueVersion++;
      position(boss.sprite, boss.target); visible(boss.sprite, true);
      partyAt(floor.boss, true);
    },
    hit(event) {
      const a = actors.get(`boss:${event.area_id}`); if (!a || !event.passed) return;
      a.hits++; pulse(a, "hit", 220);
    },
    bossResult(event) {
      const a = actors.get(`boss:${event.area_id}`); if (!a) return;
      a.result = event.passed ? "defeated" : "escaped";
      a.cueVersion++;
      a.cue = event.passed ? "hit" : "escape"; paint(a);
      const origin = a.target;
      enqueue(a, [{ duration: 450, update: t => {
        if (event.passed) opacity(a.sprite, 1 - t);
        else position(a.sprite, point(origin.x + t * 32, origin.y));
        paint(a, t);
      } }], () => {
        if (!event.passed) position(a.sprite, point(origin.x + 32, origin.y));
        visible(a.sprite, false); opacity(a.sprite, 1); a.cue = event.passed ? null : "escape"; paint(a);
      });
    },
    reveal() {
      const a = actor("castle", "", point(0, 0));
      if (scene) a.graphics = scene.add.graphics();
      const rooms = layout?.rooms || [];
      revealGraphics(a, 0);
      enqueue(a, rooms.map((_, index) => ({ duration: 90, update: t => { if (t === 1) revealGraphics(a, index + 1); } })),
        () => revealGraphics(a, rooms.length));
    },
    defeat(event) {
      const a = actors.get(`monster:${event.bug_id}`); if (!a) return;
      a.closing = true;
      a.sprite?.setTexture?.(textureKey(a.name, "defeated"));
      enqueue(a, [{ duration: 400, update: t => opacity(a.sprite, 1 - t) }], () => {
        a.sprite?.destroy(); a.graphics?.destroy(); actors.delete(a.id);
      });
    },
  };
  const motionChange = event => { mediaReduced = event.matches; if (reduced()) settle(); onMotionChange(); };
  media.addEventListener("change", motionChange);
  const api = {
    render,
    onEvent(event, currentLayout = layout, activeRoom = null) {
      if (event.type === "run_started" || event.type === "world_loaded") reset();
      bestiary = projectBestiary(bestiary, event); layout = currentLayout;
      if (scene && layout) render(scene, layout, activeRoom);
      // Before Phaser create, descriptors preserve state and bounded jobs too.
      if (!scene && layout) {
        const home = layout.rooms.find(room => room.id === activeRoom) || layout.rooms[0];
        if (home) {
          for (const [index, role] of ROLES.entries()) actor(role, role, point(home.x + 24 + index * 16, home.y + 24));
          location ||= home;
        }
        for (const floor of layout.areas) actor(`boss:${floor.id}`, bossForArea(floor.id), point(floor.boss.x + 40, floor.boss.y + 24));
        restoreSnapshot();
      }
      if (event.room_id && !revealed.has(event.room_id)) {
        const castle = actors.get("castle"); if (castle) settleActor(castle);
      }
      if (event.type !== "cooldown") {
        const role = event.role || ({ action: "warrior", review: "paladin", respawn: "warrior",
          clarify_question: "mage", clarify_answer: "mage", spec_written: "mage" })[event.type];
        for (const a of actors.values()) if (!role || a.id === role) { a.resting = false; paint(a); }
      }
      const handler = ANIMATION_DISPATCH[event.type];
      if (handler) handlers[handler](event);
    },
    setState(snapshot, currentLayout = layout) {
      reset(); bestiary = bestiaryFromState(snapshot);
      layout = currentLayout; pendingSnapshot = snapshot;
      failedReviews = (snapshot.rooms || []).reduce((sum, room) => sum + (room.failed_attempts || 0), 0);
      if (scene && layout) render(scene, layout, snapshot.active_room || snapshot.party_room);
    },
    setReducedMotion(value) { if (disposed) return; setting = value === true; if (reduced()) settle(); onMotionChange(); },
    setReplaySpeed(value) { speed = [1, 2, 4].includes(value) ? value : 1; if (speed >= 2) settle(); },
    isReducedMotion: reduced,
    settle,
    // Read-only diagnostics for tests and replay UI; no mutable actors escape.
    inspect() { return { failedReviews, revealed: [...revealed], actors: [...actors.values()].map(a => ({
      id: a.id, target: { ...a.target }, position: a.sprite ? point(a.sprite.x, a.sprite.y) : null,
      queueLength: a.queue.length, cue: a.cue, resting: a.resting, heart: a.heartLost,
      result: a.result, hits: a.hits, closing: a.closing,
    })) }; },
    destroy() { disposed = true; reset(); media.removeEventListener("change", motionChange); },
  };
  // T33 can supply persisted desktop settings without coupling to Electron.
  const persisted = browser.specvaniaDesktop?.settings?.get?.();
  if (persisted?.then) persisted.then(value => api.setReducedMotion(value?.reducedMotion === true)).catch(() => {});
  else if (persisted) api.setReducedMotion(persisted.reducedMotion === true);
  return api;
}
