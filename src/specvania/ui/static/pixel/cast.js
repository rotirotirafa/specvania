/** Kenney Tiny Dungeon 1.0 (CC0), packed 16×16 tiles, row-major.
 * This is the single source of tile indices for the gallery, map and app icon.
 */
export const TILE_SIZE = 16;
export const SHEET_KEY = "kenney-tiny-dungeon";
export const SHEET_URL = new URL("../vendor/kenney-tiny-dungeon/tilemap_packed.png", import.meta.url).href;
export const CREDIT = "Art: Kenney Tiny Dungeon · CC0 1.0";

export const CAST = Object.freeze({
  mage: 84, warrior: 87, paladin: 96,
  sword_long: 104, shield_round: 101,
  slime: 108, bat: 120, ghost: 121, spider: 122, rat: 123,
  cyclops: 109, red_crab: 110, hooded_figure: 111,
  back_wall: 40, wall_left: 57, wall_middle: 58, wall_right: 59,
  wall_top_left: 36, wall_top_middle: 37, wall_top_right: 38,
  floor_plain: 48, floor_speckled: 49, floor_stones: 42,
  door_closed: 45, door_double_left: 46, door_double_right: 47,
  door_open_left: 21, door_open_middle: 22, door_open_right: 23,
  stairs: 39, banner: 29, chest_closed: 89, chest_closed_alternate: 90,
  chest_open: 91, floor_grate: 41, wall_grate: 28,
});
export const MONSTERS = Object.freeze(["slime", "bat", "ghost", "spider", "rat"]);
export const BOSSES = Object.freeze(["cyclops", "red_crab", "hooded_figure"]);

/** FNV-1a, unsigned 32-bit, over JavaScript UTF-16 code units in order.
 * Math.imul fixes overflow to 32 bits; >>> 0 makes the result unsigned.
 * No locale, randomness, runtime seed or platform encoding is involved.
 * Do not change this algorithm/order: a persisted bug id keeps its monster.
 */
export function stableHash(id) {
  let hash = 2166136261;
  for (let i = 0; i < id.length; i++) {
    hash = Math.imul(hash ^ id.charCodeAt(i), 16777619) >>> 0;
  }
  return hash;
}

export function monsterForBug(bugId) {
  return MONSTERS[stableHash(bugId) % MONSTERS.length];
}

/** Areas A1, A2, A3 use the three bosses in order; repeat for later floors. */
export function bossForArea(areaId) {
  if (!/^A[1-9]\d*$/.test(areaId)) throw new Error(`Invalid area id: ${areaId}`);
  const number = Number(areaId.slice(1));
  if (!Number.isSafeInteger(number)) throw new Error(`Invalid area id: ${areaId}`);
  return BOSSES[(number - 1) % BOSSES.length];
}

function freezeAliases(aliases) {
  for (const states of Object.values(aliases)) {
    for (const frames of Object.values(states)) Object.freeze(frames);
    Object.freeze(states);
  }
  return Object.freeze(aliases);
}

/** T16–T18 compatibility: the pack has one pose per character, so existing
 * idle/walk/act/rest keys resolve to that pose until T31 supplies tweens.
 * Defeat uses the same tile and the existing fade. No invented pack frames.
 */
export const LEGACY_CAST = freezeAliases({
  "mage": {"idle": ["mage", "mage"], "walk": ["mage", "mage"], "act": ["mage", "mage"], "rest": ["mage"]},
  "warrior": {"idle": ["warrior", "warrior"], "walk": ["warrior", "warrior"], "act": ["warrior", "warrior"], "rest": ["warrior"]},
  "paladin": {"idle": ["paladin", "paladin"], "walk": ["paladin", "paladin"], "act": ["paladin", "paladin"], "rest": ["paladin"]},
  "boss": {"idle": ["cyclops"], "defeated": ["cyclops"]},
  "monster": {"idle": ["slime"], "defeated": ["slime"]},
  "door": {"locked": ["door_closed"], "open": ["door_open_middle"]}
});
