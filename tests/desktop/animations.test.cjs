"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const root = path.resolve(__dirname, "../..");
const staticRoot = path.join(root, "src/specvania/ui/static");
const load = file => import(pathToFileURL(path.join(staticRoot, file)));
const demo = readFileSync(path.join(root, "src/specvania/examples/demo/events.jsonl"), "utf8").trim().split("\n").map(JSON.parse);
const worldEvent = demo.find(event => event.type === "world_loaded");

function harness({ mediaReduced = false, stored, desktop } = {}) {
  const objects = [], calls = [], listeners = new Set(), liveTweens = new Set(), positions = [];
  const object = (kind, x = 0, y = 0, key) => {
    const item = { kind, x, y, key, visible: true, alpha: 1, destroyed: false, commands: [],
      setPosition(x, y) { assert.ok(Number.isInteger(x) && Number.isInteger(y)); this.x = x; this.y = y; positions.push([x, y]); return this; },
      setTexture(key) { this.key = key; return this; }, setVisible(value) { this.visible = value; return this; },
      setAlpha(value) { this.alpha = value; return this; }, destroy() { this.destroyed = true; },
      clear() { this.commands = []; return this; },
      fillStyle(color, alpha) { this.color = color; this.opacity = alpha; return this; },
      fillRect(x, y, w, h) {
        assert.ok([x, y, w, h].every(Number.isInteger));
        if (w <= 22 && h <= 22) this.effectPixels = true;
        this.commands.push({ x, y, w, h, color: this.color, alpha: this.opacity }); return this;
      } };
    objects.push(item); return item;
  };
  const media = { matches: mediaReduced, addEventListener(_, fn) { listeners.add(fn); }, removeEventListener(_, fn) { listeners.delete(fn); } };
  const browser = { matchMedia: () => media, localStorage: { getItem: () => stored }, specvaniaDesktop: desktop };
  const scene = { add: { image: (x, y, key) => object("image", x, y, key), graphics: () => object("graphics") },
    children: { bringToTop(item) { const index = objects.indexOf(item); objects.splice(index, 1); objects.push(item); } },
    tweens: {
      add(config) {
        const tween = { config, stopped: false, stop() { this.stopped = true; liveTweens.delete(this); } };
        calls.push(tween); liveTweens.add(tween); return tween;
      },
      killTweensOf(target) { for (const tween of [...liveTweens]) if (tween.config.targets === target) tween.stop(); },
    },
  };
  function completeOne() {
    const tween = liveTweens.values().next().value;
    if (!tween) return false;
    const { config } = tween;
    for (const t of [0.13, 0.37, 0.71, 1]) {
      config.targets.t = t; config.onUpdate();
      for (const item of objects.filter(item => !item.destroyed)) assert.ok(Number.isInteger(item.x) && Number.isInteger(item.y));
    }
    liveTweens.delete(tween); config.onComplete(); return true;
  }
  return { browser, scene, calls, objects, positions, liveTweens, listeners, completeOne,
    drain() { let count = 0; while (completeOne()) assert.ok(++count < 20000, "Finite tween work"); },
    reduce(value) { media.matches = value; for (const fn of listeners) fn({ matches: value }); },
    live: () => objects.filter(item => !item.destroyed && item.visible),
  };
}
function transientEffects(h) {
  return h.objects.filter(item => !item.destroyed && (
    /^(heart|sword_long|shield_round):/.test(item.key || "") ||
    (item.kind === "graphics" && item.effectPixels)));
}
function assertEffectsAreActive(h) {
  const actors = h.animations.inspect().actors;
  const active = cue => actors.filter(a => a.cue === cue && a.queueLength > 0).length;
  const effects = transientEffects(h);
  for (const [name, cue] of [["heart", "shake"], ["sword_long", "swing"], ["shield_round", "shield"]]) {
    assert.ok(effects.filter(item => item.key?.startsWith(`${name}:`)).length <= active(cue), `${name} belongs to an active effect`);
  }
  assert.ok(effects.filter(item => item.kind === "graphics").length <=
    actors.filter(a => ["plan", "rest", "hit", "escape"].includes(a.cue) && a.queueLength > 0).length,
    "Only active effects own graphics");
}
async function setup(options) {
  const { createMapAnimations, QUEUE_CAP, ANIMATION_DISPATCH, walkingPath } = await load("map/animations.js");
  const { layoutWorld, worldFromRooms } = await load("map/layout.js");
  const { projectEvent } = await load("map/scene.js");
  const h = harness(options), animations = createMapAnimations(h.browser);
  let world = worldFromRooms(worldEvent.rooms, worldEvent.areas, worldEvent.doors), room = "T01";
  const layout = () => layoutWorld(world);
  animations.render(h.scene, layout(), room);
  const emit = event => {
    world = projectEvent(world, event);
    if (["room_entered", "attempt_started", "respawn"].includes(event.type)) room = event.room_id;
    animations.onEvent(event, layout(), room); animations.render(h.scene, layout(), room);
    for (const actor of animations.inspect().actors) assert.ok(actor.queueLength <= QUEUE_CAP, `${event.type}/${actor.id}`);
  };
  return { ...h, animations, emit, layout, QUEUE_CAP, ANIMATION_DISPATCH, walkingPath,
    actor: id => animations.inspect().actors.find(actor => actor.id === id),
  };
}

const examples = {
  room_entered: { room_id: "T02" }, action: { room_id: "T01" }, model_call: { role: "mage" },
  clarify_question: {}, clarify_answer: {}, spec_written: { feature: "synthetic-demo" },
  review: { room_id: "T01", verdict: "pass" }, respawn: { room_id: "T01" }, cooldown: { role: "warrior" },
  boss_started: { area_id: "A1", scenarios: ["S1", "S2"] }, boss_scenario: { area_id: "A1", scenario: "S1", passed: true },
  boss_result: { area_id: "A1", passed: true }, world_loaded: worldEvent,
  bug_closed: { bug_id: "bug-1", status: "fixed" },
};
const expectedCue = { action: ["warrior", "swing"], model_call: ["mage", "plan"],
  clarify_question: ["mage", "plan"], clarify_answer: ["mage", "plan"], spec_written: ["mage", "plan"],
  review: ["paladin", "shield"], boss_scenario: ["boss:A1", "hit"] };

test("dispatch_table_matches_single_map_and_each_event_triggers_real_visual_work", async t => {
  const { ANIMATION_DISPATCH } = await load("map/animations.js");
  const source = readFileSync(path.join(staticRoot, "map/animations.js"), "utf8");
  const comment = source.slice(source.indexOf("/* Event"), source.indexOf("*/"));
  const rows = [...comment.matchAll(/\| (\w+) \| (\w+) \|/g)].slice(1).map(match => [match[1], match[2]]);
  assert.deepEqual(Object.fromEntries(rows), ANIMATION_DISPATCH);
  assert.deepEqual(Object.keys(ANIMATION_DISPATCH).sort(), Object.keys(examples).sort());
  for (const [type] of Object.entries(ANIMATION_DISPATCH)) await t.test(type, async () => {
    const h = await setup();
    if (type === "bug_closed") h.emit({ type: "bug_opened", bug_id: "bug-1", room_id: "T01" });
    const before = h.calls.length;
    h.emit({ type, ...examples[type] });
    assert.ok(h.calls.length > before, `${type} starts a tween`);
    if (expectedCue[type]) assert.equal(h.actor(expectedCue[type][0]).cue, expectedCue[type][1]);
    if (type === "cooldown") {
      assert.equal(h.actor("warrior").resting, true);
      assert.ok(h.live().filter(item => item.kind === "graphics").some(item => item.commands.length === 9));
    }
    if (type === "boss_scenario") assert.equal(h.actor("boss:A1").hits, 1);
    if (type === "boss_result") assert.equal(h.actor("boss:A1").result, "defeated");
    if (type === "bug_closed") assert.equal(h.actor("monster:bug-1").closing, true);
    if (type === "world_loaded") {
      assert.equal(h.animations.inspect().revealed.length, 0);
      h.completeOne(); assert.deepEqual(h.animations.inspect().revealed, [h.layout().rooms[0].id]);
    }
    h.drain();
    if (type === "respawn") assert.deepEqual(h.actor("warrior").position, { x: h.layout().rooms[0].x + 8, y: h.layout().rooms[0].y + 24 });
    if (type === "world_loaded") assert.equal(h.animations.inspect().revealed.length, h.layout().rooms.length);
    if (type === "bug_closed") assert.equal(h.actor("monster:bug-1"), undefined);
    h.animations.destroy(); assert.equal(h.listeners.size, 0); assert.equal(h.liveTweens.size, 0);
  });
});

test("warrior_sword_paladin_shield_and_mage_sparkles_use_reviewed_art_and_palette_pixels", async () => {
  const h = await setup();
  const { CAST } = await load("pixel/cast.js"), { PALETTE } = await load("pixel/palette.js");
  assert.equal(CAST.sword_long, 104); assert.equal(CAST.shield_round, 101);
  h.emit({ type: "action", room_id: "T01" });
  const sword = h.live().find(item => item.key === "sword_long:idle:0"); assert.ok(sword);
  const initial = [sword.x, sword.y];
  h.calls.at(-1).config.targets.t = 0.5; h.calls.at(-1).config.onUpdate();
  assert.notDeepEqual([sword.x, sword.y], initial, "Sword swings along a pixel arc");
  h.emit({ type: "review", verdict: "pass", room_id: "T01" });
  assert.ok(h.live().some(item => item.key === "shield_round:idle:0"));
  h.emit({ type: "model_call", role: "mage" });
  const sparkles = h.live().find(item => item.kind === "graphics" && item.commands.length === 6);
  assert.ok(sparkles); assert.ok(sparkles.commands.every(pixel => [PALETTE.Y, PALETTE.W].map(color => parseInt(color.slice(1), 16)).includes(pixel.color)));
  h.drain();
  const count = h.calls.length;
  h.emit({ type: "model_call", role: "warrior" }); h.emit({ type: "boss_scenario", area_id: "A1", scenario: "S2", passed: false });
  assert.equal(h.calls.length, count); assert.equal(h.actor("boss:A1").hits, 0);
  const gallery = readFileSync(path.join(staticRoot, "sprites.html"), "utf8");
  assert.match(gallery, /Object.entries\(CAST\)/); assert.match(gallery, /sword_long/); assert.match(gallery, /shield_round/);
  h.animations.destroy();
});

test("failed_review_shakes_and_pops_existing_hud_heart_respawn_returns_to_entrance", async () => {
  const h = await setup(); h.emit({ type: "review", room_id: "T01", verdict: "fail" });
  const heart = h.live().find(item => item.key === "heart:idle:0"); assert.ok(heart);
  assert.equal(h.animations.inspect().failedReviews, 1);
  assert.equal(h.actor("warrior").cue, "shake");
  const warrior = h.live().find(item => item.key === "warrior:idle:0"), original = warrior.x;
  const shake = h.calls.find(call => call.config.duration === 400);
  shake.config.targets.t = 0.13; shake.config.onUpdate(); assert.notEqual(warrior.x, original);
  h.drain(); assert.ok(heart.y < warrior.y - 18, "Lost heart pops above the character on the map");
  assert.ok(heart.destroyed, "Pop completion destroys the heart, even without a respawn");
  h.emit({ type: "respawn", room_id: "T01" }); h.drain();
  assert.ok(heart.destroyed); assert.equal(warrior.x, h.layout().rooms[0].x + 8);
  assert.equal(warrior.y, h.layout().rooms[0].y + 24);
  h.animations.destroy();
});

test("walking_hops_through_doors_and_arena_stairs_next_floor_with_integer_positions", async () => {
  const h = await setup();
  const source = h.layout().rooms.find(room => room.id === "T01"), next = h.layout().rooms.find(room => room.id === "T02");
  const path1 = h.walkingPath(h.layout(), source, next);
  const door = h.layout().doors.find(door => door.dst === next.id);
  assert.deepEqual(path1.slice(0, 2), door.points.map(p => ({ x: p.x, y: p.y - 8 })));
  h.emit({ type: "room_entered", room_id: "T02" });
  const hop = h.calls[1]; hop.config.targets.t = 0.5; hop.config.onUpdate();
  assert.equal(h.actor("warrior").position.y, source.y + 20, "Hop rises above the floor");
  h.drain();
  h.emit({ type: "boss_started", area_id: "A1", scenarios: ["S1"] }); h.drain();
  const arena = h.layout().areas[0].boss, downstairs = h.layout().rooms.find(room => room.id === "T03");
  const path2 = h.walkingPath(h.layout(), arena, downstairs);
  const stair = h.layout().stairs[0];
  assert.deepEqual(path2.slice(1, 5), stair.points.map(p => ({ x: p.x, y: p.y - 8 })));
  for (const offset of [24, 40, 56]) {
    assert.deepEqual(h.walkingPath(h.layout(), source, next, offset).slice(0, 2), door.points.map(p => ({ x: p.x, y: p.y - 8 })));
    assert.deepEqual(h.walkingPath(h.layout(), arena, downstairs, offset).slice(1, 5), stair.points.map(p => ({ x: p.x, y: p.y - 8 })));
  }
  h.positions.length = 0; h.emit({ type: "room_entered", room_id: "T03" }); h.drain();
  for (const p of path2) assert.ok(h.positions.some(([x, y]) => x === p.x && y === p.y), `Warrior visits ${JSON.stringify(p)}`);
  assert.deepEqual(h.actor("warrior").position, { x: downstairs.x + 40, y: downstairs.y + 24 });
  assert.ok(h.positions.every(p => p.every(Number.isInteger)));
  h.animations.destroy();
});

test("cooldown_state_persists_but_zzz_is_destroyed_on_completion", async () => {
  const h = await setup(); h.emit({ type: "cooldown", role: "warrior" }); h.drain();
  assert.equal(h.actor("warrior").resting, true);
  assert.equal(transientEffects(h).length, 0);
  h.emit({ type: "model_call", role: "mage" }); assert.equal(h.actor("warrior").resting, true);
  h.emit({ type: "cooldown", role: "warrior" }); assert.equal(h.actor("warrior").resting, true);
  h.emit({ type: "action", room_id: "T01" }); assert.equal(h.actor("warrior").resting, false);
  h.emit({ type: "cooldown", role: "paladin" }); h.emit({ type: "run_finished" });
  assert.equal(h.actor("paladin").resting, false); h.animations.destroy();
});

test("actor_movement_and_queue_cap_destroy_displaced_effects", async () => {
  const h = await setup();
  h.emit({ type: "review", verdict: "fail" });
  const heart = h.objects.find(item => item.key === "heart:idle:0");
  for (let i = 0; i < h.QUEUE_CAP; i++) h.emit({ type: "action" });
  assert.ok(heart.destroyed, "Dropping the heart job at the queue cap destroys its sprite");
  h.emit({ type: "model_call", role: "mage" });
  h.emit({ type: "cooldown", role: "paladin" });
  const effects = transientEffects(h); assert.ok(effects.length > 0);
  h.emit({ type: "room_entered", room_id: "T02" });
  assert.ok(effects.every(item => item.destroyed), "Movement destroys the previous actor effects immediately");
  assert.equal(transientEffects(h).length, 0); h.drain();
  h.animations.destroy();
});

test("boss_hits_each_passed_scenario_then_defeats_or_escapes_and_monster_choice_is_preserved", async () => {
  for (const passed of [true, false]) {
    const h = await setup();
    h.emit({ type: "boss_started", area_id: "A1", scenarios: ["S1", "S2"] }); h.drain();
    for (const scenario of ["S1", "S2"]) { h.emit({ type: "boss_scenario", area_id: "A1", scenario, passed: true }); h.drain(); }
    assert.equal(h.actor("boss:A1").hits, 2);
    const boss = h.live().find(item => item.key === "cyclops:idle:0");
    const x = boss.x; h.emit({ type: "boss_result", area_id: "A1", passed }); h.drain();
    assert.equal(boss.visible, false); assert.equal(h.actor("boss:A1").result, passed ? "defeated" : "escaped");
    if (!passed) { assert.equal(boss.x, x + 32); assert.equal(h.actor("boss:A1").cue, "escape"); }
    h.emit({ type: "bug_opened", bug_id: "bug-1", room_id: "T01" });
    const { monsterForBug } = await load("pixel/cast.js");
    const monster = h.live().find(item => item.key === `${monsterForBug("bug-1")}:idle:0`); assert.ok(monster);
    h.emit({ type: "bug_closed", bug_id: "bug-1", status: "wontfix" });
    assert.equal(monster.key, `${monsterForBug("bug-1")}:defeated:0`); h.drain(); assert.ok(monster.destroyed);
    h.animations.destroy();
  }
});

for (const mode of ["media", "setting", "persisted", "desktop"]) {
  test(`reduced_motion_${mode}_disables_all_dispatch_tweens_and_preserves_visible_state`, async () => {
    const h = await setup({ mediaReduced: mode === "media", stored: mode === "persisted" ? '{"reducedMotion":true}' : undefined,
      desktop: mode === "desktop" ? { settings: { get: () => ({ reducedMotion: true }) } } : undefined });
    if (mode === "setting") h.animations.setReducedMotion(true);
    assert.equal(h.animations.isReducedMotion(), true);
    for (const [type] of Object.entries(h.ANIMATION_DISPATCH)) {
      if (type === "bug_closed") h.emit({ type: "bug_opened", bug_id: "bug-1", room_id: "T01" });
      h.emit({ type, ...examples[type] });
      for (const a of h.animations.inspect().actors) assert.equal(a.queueLength, 0);
    }
    assert.equal(h.calls.length, 0);
    assert.equal(h.animations.inspect().revealed.length, h.layout().rooms.length);
    assert.equal(h.actor("monster:bug-1"), undefined);
    h.emit({ type: "room_entered", room_id: "T02" });
    const room = h.layout().rooms.find(room => room.id === "T02");
    assert.deepEqual(h.actor("warrior").position, { x: room.x + 40, y: room.y + 24 });
    h.emit({ type: "review", verdict: "fail", room_id: "T02" });
    assert.equal(h.actor("warrior").heart, false);
    assert.equal(h.animations.inspect().failedReviews, 1);
    assert.equal(transientEffects(h).length, 0, "Reduced motion settles and destroys effects");
    h.emit({ type: "respawn", room_id: "T02" }); assert.equal(h.actor("warrior").position.x, room.x + 8);
    for (const passed of [true, false]) {
      h.emit({ type: "boss_started", area_id: "A1", scenarios: ["S1"] });
      h.emit({ type: "boss_scenario", area_id: "A1", scenario: "S1", passed: true });
      assert.equal(h.actor("boss:A1").hits, 1);
      h.emit({ type: "boss_result", area_id: "A1", passed });
      assert.equal(h.actor("boss:A1").result, passed ? "defeated" : "escaped");
      const boss = h.objects.find(item => item.key === "cyclops:idle:0" && !item.destroyed);
      assert.equal(boss.visible, false);
      if (!passed) assert.equal(h.actor("boss:A1").cue, "escape");
    }
    assert.equal(h.calls.length, 0); h.animations.destroy();
  });
}

test("enabling_reduced_motion_settles_inflight_and_queued_state_and_system_preference_wins", async () => {
  const h = await setup();
  h.emit({ type: "world_loaded", ...worldEvent }); h.emit({ type: "room_entered", room_id: "T02" });
  h.emit({ type: "review", verdict: "fail" }); h.emit({ type: "bug_opened", bug_id: "bug-1", room_id: "T01" });
  h.emit({ type: "bug_closed", bug_id: "bug-1", status: "fixed" }); h.emit({ type: "boss_result", area_id: "A1", passed: true });
  assert.ok(h.liveTweens.size > 0); h.reduce(true);
  assert.equal(h.liveTweens.size, 0); assert.ok(h.animations.inspect().actors.every(a => a.queueLength === 0));
  assert.equal(h.actor("monster:bug-1"), undefined); assert.equal(h.actor("warrior").heart, false);
  assert.equal(transientEffects(h).length, 0);
  assert.equal(h.animations.inspect().revealed.length, h.layout().rooms.length);
  const count = h.calls.length; h.animations.setReducedMotion(false); h.emit({ type: "action" }); assert.equal(h.calls.length, count);
  h.reduce(false); h.emit({ type: "action" }); assert.ok(h.calls.length > count);
  h.animations.setReducedMotion(true); assert.equal(h.liveTweens.size, 0); h.animations.destroy();
});

test("demo_at_4x_and_1x_has_bounded_queues_no_leftover_effects_and_identical_final_state", async () => {
  const finals = [];
  for (const speed of [1, 4]) {
    const h = await setup(); h.animations.setReplaySpeed(speed);
    for (const event of demo) {
      h.emit(event);
      assertEffectsAreActive(h);
      assert.ok(h.liveTweens.size <= h.animations.inspect().actors.length, "At most one active tween per actor");
      if (speed === 1) h.drain(); // Normal playback gives each event its visual time.
      if (speed === 4) assert.ok(h.animations.inspect().actors.every(a => a.queueLength <= 1));
    }
    h.drain(); finals.push(h.animations.inspect());
    assert.equal(transientEffects(h).length, 0, `No effects remain after ${speed}x replay`);
    assert.equal(h.animations.inspect().failedReviews, 2);
    assert.equal(h.actor("boss:A1").result, "defeated"); assert.equal(h.actor("boss:A1").hits, 2);
    assert.equal(h.actor("monster:demo-bell-wheel"), undefined); h.animations.destroy();
  }
  assert.deepEqual(finals[1], finals[0]);
});

test("burst_queue_never_grows_with_event_count_and_speed_changes_fast_forward_old_motion", async () => {
  const h = await setup();
  for (let i = 0; i < 1000; i++) {
    h.emit({ type: "action" }); h.emit({ type: "review", verdict: "fail" });
    assert.ok(h.animations.inspect().actors.every(a => a.queueLength <= h.QUEUE_CAP));
    assert.ok(h.liveTweens.size <= h.animations.inspect().actors.length);
    assert.equal(h.live().filter(item => item.key === "heart:idle:0").length, 1);
  }
  assert.equal(h.animations.inspect().failedReviews, 1000);
  h.animations.setReplaySpeed(4); assert.equal(h.liveTweens.size, 0);
  assert.equal(transientEffects(h).length, 0, "Fast-forward destroys every effect");
  assert.ok(h.animations.inspect().actors.every(a => a.queueLength === 0));
  h.emit({ type: "respawn", room_id: "T01" }); h.drain();
  assert.equal(h.actor("warrior").position.x, h.layout().rooms[0].x + 8); h.animations.destroy();
});

test("events_before_phaser_create_keep_state_and_destroy_settled_effects_without_unbounded_pending_work", async () => {
  const { createMapAnimations, QUEUE_CAP } = await load("map/animations.js");
  const { layoutWorld, worldFromRooms } = await load("map/layout.js");
  const layout = layoutWorld(worldFromRooms(worldEvent.rooms, worldEvent.areas, worldEvent.doors));
  for (const reduced of [false, true]) {
    const h = harness({ mediaReduced: reduced }), animations = createMapAnimations(h.browser);
    animations.onEvent(worldEvent, layout);
    for (let i = 0; i < 20; i++) animations.onEvent({ type: "review", room_id: "T01", verdict: "fail" }, layout, "T01");
    assert.ok(animations.inspect().actors.every(a => a.queueLength <= QUEUE_CAP));
    animations.render(h.scene, layout, "T01"); h.drain();
    assert.equal(animations.inspect().failedReviews, 20);
    assert.equal(transientEffects(h).length, 0);
    assert.equal(animations.inspect().revealed.length, layout.rooms.length);
    if (reduced) assert.equal(h.calls.length, 0);
    animations.destroy();
  }
});

test("reconnect_snapshot_before_create_restores_boss_result_and_rest_without_tweens", async () => {
  const { createMapAnimations } = await load("map/animations.js");
  const { layoutWorld, worldFromRooms } = await load("map/layout.js");
  const layout = layoutWorld(worldFromRooms(worldEvent.rooms, worldEvent.areas, worldEvent.doors));
  for (const beforeCreate of [false, true]) {
    const h = harness(), animations = createMapAnimations(h.browser);
    if (!beforeCreate) animations.render(h.scene, layout, "T01");
    animations.setState({ rooms: layout.rooms, party_room: "T03", party: [{ role: "warrior", status: "cooldown" }],
      bosses: [{ area_id: "A1", scenarios: ["S1"], results: [{ scenario: "S1", passed: true }], passed: false }] }, layout);
    // A newer event must win over the pending snapshot before create.
    if (beforeCreate) animations.onEvent({ type: "model_call", role: "warrior" }, layout, "T03");
    animations.render(h.scene, layout, "T03");
    const state = animations.inspect(), boss = state.actors.find(a => a.id === "boss:A1");
    assert.equal(boss.hits, 1); assert.equal(boss.result, "escaped");
    assert.equal(boss.position.x, layout.areas[0].boss.x + 40 + 32);
    assert.equal(h.live().filter(item => item.key === "cyclops:idle:0").length, 0);
    assert.equal(state.actors.find(a => a.id === "warrior").resting, !beforeCreate);
    assert.equal(state.revealed.length, layout.rooms.length);
    assert.equal(h.calls.length, 0); animations.destroy();
  }
});

test("world_reveal_order_is_sequential_and_room_events_cannot_wait_behind_reveal", async () => {
  const h = await setup(); h.emit(worldEvent);
  const reveal = () => h.animations.inspect().revealed;
  for (let i = 0; i < h.layout().rooms.length; i++) {
    assert.deepEqual(reveal(), h.layout().rooms.slice(0, i).map(room => room.id));
    h.completeOne();
  }
  assert.deepEqual(reveal(), h.layout().rooms.map(room => room.id));
  h.emit(worldEvent); h.emit({ type: "room_entered", room_id: "T05" });
  assert.ok(reveal().includes("T05"), "Active chamber cannot remain masked by reveal motion");
  h.animations.destroy();
});

test("normal_transient_cues_end_and_closed_monster_reopening_cannot_be_removed_by_old_tween", async () => {
  const h = await setup();
  for (const event of [{ type: "action" }, { type: "model_call", role: "mage" }, { type: "review", verdict: "pass" }]) h.emit(event);
  h.drain();
  for (const id of ["warrior", "mage", "paladin"]) assert.equal(h.actor(id).cue, null);
  assert.ok(!h.live().some(item => item.key === "sword_long:idle:0" || item.key === "shield_round:idle:0"));
  assert.equal(transientEffects(h).length, 0, "Effects are destroyed, including hidden equipment and empty graphics");
  h.emit({ type: "bug_opened", bug_id: "bug-1", room_id: "T01" });
  h.emit({ type: "bug_closed", bug_id: "bug-1", status: "fixed" });
  h.emit({ type: "bug_opened", bug_id: "bug-1", room_id: "T01" }); h.drain();
  assert.ok(h.actor("monster:bug-1")); assert.equal(h.actor("monster:bug-1").closing, false);
  h.animations.destroy();
});

const phaserSource = readFileSync(path.join(staticRoot, "vendor/phaser.min.js"), "utf8");
function cameraMethod(name) {
  const match = phaserSource.match(new RegExp(`${name}:(function\\([^)]*\\)\\{[^}]*\\})`));
  assert.ok(match, `Pinned Phaser camera method ${name}`);
  return Function("r", `return (${match[1]})`)((value, min, max) => Math.max(min, Math.min(max, value)));
}
function mountedHarness(h, { width = 960, height = 540, overlayWidth = 0 } = {}) {
  const handlers = new Map(), keys = new Map(); let config;
  const hud = overlayWidth ? { hidden: false, getBoundingClientRect() { return { width: this.hidden ? 0 : overlayWidth }; } } : null;
  const camera = { width, height, zoom: 1, following: null, scrollX: 0, scrollY: 0,
    midPoint: { set(x, y) { Object.assign(this, { x, y }); } },
    lerp: { set() {} }, followOffset: { set(x, y) { Object.assign(this, { x, y }); } },
    _bounds: { setTo(x, y, width, height) { Object.assign(this, { x, y, width, height }); } },
    get displayWidth() { return this.width / this.zoom; }, get displayHeight() { return this.height / this.zoom; },
    clampX: cameraMethod("clampX"), clampY: cameraMethod("clampY"),
    centerOnX: cameraMethod("centerOnX"), centerOnY: cameraMethod("centerOnY"),
    setBounds: cameraMethod("setBounds"), centerOn(x, y) { this.centerOnX(x); this.centerOnY(y); },
    setZoom(zoom) { this.zoom = zoom; }, stopFollow() { this.following = null; },
    startFollow(...args) { this.following = args[0]; cameraMethod("startFollow").apply(this, args); },
    update() {
      if (this.following) this.startFollow(this.following, true, 1, 1, this.followOffset.x, this.followOffset.y);
      this.scrollX = this.clampX(Math.round(this.scrollX)); this.scrollY = this.clampY(Math.round(this.scrollY));
    },
  };
  Object.assign(h.browser, { innerWidth: width, innerHeight: height,
    document: { getElementById: id => id === "castle-hud" ? hud : {} },
    addEventListener(type, fn) { handlers.set(type, fn); }, removeEventListener(type) { handlers.delete(type); } });
  Object.assign(h.scene, { textures: { exists: () => true }, input: { keyboard: { on: (type, fn) => keys.set(type, fn) } },
    cameras: { main: camera } });
  h.scene.add.text = (x, y) => h.scene.add.image(x, y, "label");
  h.scene.add.zone = (x, y) => Object.assign(h.scene.add.image(x, y, "zone"), { setInteractive() {}, on() {} });
  const Phaser = { CANVAS: 1, Scale: { NONE: 0 }, Game: class {
    constructor(value) { config = value; this.scale = { resize(w, h) { camera.width = w; camera.height = h; } }; } destroy() {}
  } };
  return { Phaser, camera, hud, keys, create: () => config.scene.create.call(h.scene), emit: (type, detail) => handlers.get(type)({ detail }), handlers };
}

test("follow_camera_keeps_last_floor_boss_arena_and_party_clear_of_dom_hud_at_both_window_sizes", async () => {
  const { mountMap } = await load("map/scene.js");
  const { layoutWorld, worldFromRooms } = await load("map/layout.js");
  const layout = layoutWorld(worldFromRooms(worldEvent.rooms, worldEvent.areas, worldEvent.doors));
  for (const [width, height, overlayWidth] of [[960, 540, 260], [1280, 720, 360], [960, 540, 0], [1280, 720, 0]]) {
    const h = harness(), mounted = mountedHarness(h, { width, height, overlayWidth });
    const map = mountMap(mounted.Phaser, { clientWidth: width }, h.browser);
    mounted.emit("specvania:event", worldEvent); mounted.create(); h.drain();
    const camera = mounted.camera;
    mounted.emit("specvania:event", { type: "boss_started", area_id: "A2", scenarios: ["S3"] }); h.drain(); camera.update();
    const screenX = x => (x - camera.scrollX - width / 2) * camera.zoom + width / 2;
    const arena = layout.areas.at(-1).boss;
    assert.ok(screenX(arena.x) >= 0, "Arena starts inside the visible canvas");
    assert.ok(screenX(arena.x + arena.width) <= width - overlayWidth, "Arena ends before the HUD");
    for (const role of ["mage", "warrior", "paladin"]) {
      const actor = h.live().find(item => item.key === `${role}:idle:0`);
      assert.ok(screenX(actor.x - 8) >= 0 && screenX(actor.x + 8) <= width - overlayWidth, `${role} fully visible`);
    }
    assert.ok(Number.isInteger(camera.scrollX) && Number.isInteger(camera.scrollY));
    assert.equal(camera.zoom, width === 960 ? 3 : 4);
    if (!overlayWidth) {
      assert.equal(camera._bounds.width, Math.max(layout.width, width / camera.zoom));
      assert.equal(camera.followOffset.x, 0);
    }
    mounted.keys.get("keydown-M")({ repeat: false }); camera.update();
    const visibleWidth = width - (mounted.hud?.getBoundingClientRect().width || 0);
    assert.ok(Math.abs((screenX(0) + screenX(layout.width)) / 2 - visibleWidth / 2) <= camera.zoom);
    // Returning from the overview must measure the panel after showing it.
    mounted.keys.get("keydown-M")({ repeat: false }); camera.update();
    assert.ok(screenX(arena.x + arena.width) <= width - overlayWidth);
    mounted.emit("specvania:settings", { reducedMotion: true }); camera.update();
    assert.ok(screenX(arena.x + arena.width) <= width - overlayWidth);
    map.destroy();
  }
});

test("mounted_scene_wires_reduced_setting_speed_and_persistent_boss_results_across_redraw", async () => {
  const { mountMap } = await load("map/scene.js");
  const h = harness(), mounted = mountedHarness(h), map = mountMap(mounted.Phaser, { clientWidth: 960 }, h.browser);
  mounted.emit("specvania:event", worldEvent); mounted.create(); h.drain();
  mounted.emit("specvania:event", { type: "room_entered", room_id: "T01" }); h.drain();
  assert.ok(h.scene.cameras.main.following);
  mounted.emit("specvania:settings", { reducedMotion: true }); assert.equal(h.scene.cameras.main.following, null);
  const count = h.calls.length; mounted.emit("specvania:event", { type: "action", room_id: "T01" }); assert.equal(h.calls.length, count);
  map.setReducedMotion(false); mounted.emit("specvania:event", { type: "action", room_id: "T01" }); assert.ok(h.calls.length > count);
  mounted.emit("specvania:replay-speed", { speed: 4 }); assert.equal(h.liveTweens.size, 0);
  mounted.emit("specvania:event", { type: "action", room_id: "T01" }); assert.equal(h.calls.at(-1).config.duration, 80);
  map.setReplaySpeed(1); h.drain();
  mounted.emit("specvania:event", { type: "boss_result", area_id: "A1", passed: true }); h.drain();
  mounted.emit("specvania:event", { type: "gate_result", room_id: "T01" });
  assert.ok(!h.live().some(item => item.key === "cyclops:idle:0"), "Redraw cannot recreate defeated boss");
  mounted.emit("specvania:event", { type: "bug_opened", bug_id: "bug-1", room_id: "T01" });
  mounted.emit("specvania:event", { type: "bug_closed", bug_id: "bug-1", status: "fixed" }); h.drain();
  mounted.emit("specvania:event", { type: "action", room_id: "T01" });
  assert.ok(!h.live().some(item => item.key?.startsWith("rat:")));
  map.destroy(); assert.equal(mounted.handlers.size, 0); assert.equal(h.liveTweens.size, 0);
});
