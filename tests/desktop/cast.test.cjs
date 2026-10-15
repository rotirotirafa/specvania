"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { createHash } = require("node:crypto");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const root = path.resolve(__dirname, "../..");
const staticRoot = path.join(root, "src/specvania/ui/static");
const moduleUrl = name => pathToFileURL(path.join(staticRoot, "pixel", name));
const cast = () => import(moduleUrl("cast.js"));
const sheet = () => readFileSync(path.join(staticRoot, "vendor/kenney-tiny-dungeon/tilemap_packed.png"));
const expected = {
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
};

test("vendored_pack_license_and_provenance_are_unchanged_and_noticed", () => {
  assert.equal(createHash("sha256").update(sheet()).digest("hex"),
    "d24e60a41e4ac7a745c0304dfde121143688557f40215f23221c29cfe683825f");
  const licenseBytes = readFileSync(path.join(staticRoot, "vendor/kenney-tiny-dungeon/License.txt"));
  assert.equal(createHash("sha256").update(licenseBytes).digest("hex"),
    "9f574a2f1f636a3db8a0665ba90212f34e2b5e61ecb533d77c05237766374111");
  const license = licenseBytes.toString("utf8");
  for (const phrase of ["Tiny Dungeon (1.0)", "Kenney", "Creative Commons Zero, CC0", "publicdomain/zero/1.0/"]) assert.ok(license.includes(phrase));
  const notices = readFileSync(path.join(root, "THIRD_PARTY_NOTICES.md"), "utf8");
  for (const phrase of ["Kenney Tiny Dungeon", "CC0 1.0", "https://kenney.nl/assets/tiny-dungeon",
    "c109438ab06f65fd80f9b2686a4cf9c7c11dc64444b47333ec71d602f8bb5fc7",
    "d24e60a41e4ac7a745c0304dfde121143688557f40215f23221c29cfe683825f", "License.txt",
    "Phaser", "Press Start 2P", "Electron"]) assert.ok(notices.includes(phrase), phrase);
  assert.doesNotMatch(notices, /No sprites or images from games or asset packs\s+are included/);
});

test("every_cast_entry_resolves_inside_real_png_sheet_and_matches_checked_indices", async () => {
  const { CAST, TILE_SIZE } = await cast();
  assert.equal(TILE_SIZE, 16);
  assert.deepEqual(CAST, expected);
  assert.ok(Object.isFrozen(CAST));
  const png = sheet();
  assert.equal(png.toString("ascii", 12, 16), "IHDR");
  const width = png.readUInt32BE(16), height = png.readUInt32BE(20);
  assert.equal(width, 192); assert.equal(height, 176);
  for (const [name, index] of Object.entries(CAST)) {
    assert.ok(Number.isInteger(index) && index >= 0 && index < width / TILE_SIZE * height / TILE_SIZE, name);
  }
});

test("bug_id_hash_has_fixed_cross_process_choices_and_nonconstant_distribution", async () => {
  const { monsterForBug, stableHash, MONSTERS } = await cast();
  assert.equal(stableHash(""), 2166136261);
  assert.equal(stableHash("hello"), 1335831723);
  // FNV-1a over UTF-16 code units: also pin non-ASCII and surrogate pairs.
  const ids = ["bug-1", "bug-2", "bug-3", "BUG-1", "falha-ç", "🐀", ""];
  const choices = ids.map(monsterForBug);
  assert.deepEqual(choices, ["rat", "spider", "ghost", "ghost", "spider", "bat", "bat"]);
  const { spawnSync } = require("node:child_process");
  const child = spawnSync(process.execPath, ["--input-type=module", "-e",
    `import {monsterForBug} from ${JSON.stringify(moduleUrl("cast.js").href)}; console.log(JSON.stringify(${JSON.stringify(ids)}.map(monsterForBug)));`], { encoding: "utf8" });
  assert.equal(child.status, 0, child.stderr);
  assert.deepEqual(JSON.parse(child.stdout), choices);
  assert.deepEqual(ids.map(monsterForBug), choices);
  const distribution = new Set(Array.from({length: 200}, (_, index) => monsterForBug(`bug-${index}`)));
  assert.deepEqual([...distribution].sort(), [...MONSTERS].sort());
});

test("bosses_cycle_by_numeric_area_and_are_cast_entries", async () => {
  const { bossForArea, CAST } = await cast();
  const choices = Array.from({length: 14}, (_, i) => bossForArea(`A${i + 1}`));
  assert.deepEqual(choices, ["cyclops", "red_crab", "hooded_figure", "cyclops", "red_crab", "hooded_figure", "cyclops", "red_crab", "hooded_figure", "cyclops", "red_crab", "hooded_figure", "cyclops", "red_crab"]);
  assert.deepEqual([...new Set(choices.map(name => CAST[name]))].sort(), [109, 110, 111]);
  for (const invalid of ["", "A0", "area1", "A1x"]) assert.throws(() => bossForArea(invalid), /area/i);
});

function textureScene() {
  const textures = new Map();
  const crops = new Map();
  const source = { sheet: true, width: 192, height: 176 };
  textures.set("kenney-tiny-dungeon", { getSourceImage: () => source, setFilter(value) { this.filter = value; } });
  const manager = {
    exists: key => textures.has(key), get: key => textures.get(key),
    createCanvas(key, width, height) {
      const calls = [];
      const context = { imageSmoothingEnabled: true, drawImage: (...args) => calls.push(args), fillRect() {}, fillStyle: null };
      const texture = { width, height, context, getContext: () => context, refresh() { this.refreshed = true; },
        setFilter(value) { this.filter = value; } };
      textures.set(key, texture); crops.set(key, calls); return texture;
    },
  };
  return { scene: { textures: manager }, textures, crops, source };
}

test("spritesheet_preload_and_all_legacy_keys_keep_nearest_kenney_art", async () => {
  const { preloadTextures, registerTextures, textureKey } = await import(moduleUrl("textures.js"));
  const { CAST, LEGACY_CAST } = await cast();
  const { SPRITES } = await import(moduleUrl("sprites.js"));
  const calls = [];
  preloadTextures({ textures: {exists: () => false}, load: {spritesheet: (...args) => calls.push(args)} });
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "kenney-tiny-dungeon");
  assert.ok(calls[0][1].endsWith("/vendor/kenney-tiny-dungeon/tilemap_packed.png"));
  assert.deepEqual(calls[0][2], {frameWidth: 16, frameHeight: 16, margin: 0, spacing: 0});
  const fake = textureScene();
  globalThis.Phaser = { Textures: { FilterMode: { NEAREST: 0 } } };
  registerTextures(fake.scene);
  const aliases = {...Object.fromEntries(Object.keys(CAST).map(name => [name, {idle: [name], defeated: [name]}])), ...LEGACY_CAST};
  for (const [name, states] of Object.entries(aliases)) for (const [state, frames] of Object.entries(states)) frames.forEach((entry, index) => {
    const key = textureKey(name, state, index), tile = CAST[entry];
    assert.deepEqual(fake.crops.get(key), [[fake.source, (tile % 12) * 16, Math.floor(tile / 12) * 16, 16, 16, 0, 0, 16, 16]], key);
    const texture = fake.textures.get(key);
    assert.equal(texture.filter, 0); assert.equal(texture.context.imageSmoothingEnabled, false); assert.equal(texture.refreshed, true);
  });
  for (const [name, states] of Object.entries(SPRITES)) for (const [state, frames] of Object.entries(states)) frames.forEach((_, i) => assert.ok(fake.textures.has(textureKey(name, state, i))));
  const count = fake.textures.size;
  registerTextures(fake.scene); assert.equal(fake.textures.size, count);
  assert.equal(fake.textures.get("kenney-tiny-dungeon").filter, 0);
  for (const name of ["mage", "warrior", "paladin", "boss", "monster", "door"]) assert.ok(!(name in SPRITES));
  for (const name of ["heart", "coin", "stamina_pip", ...Array.from({length: 14}, (_, i) => `room_a${i + 1}`)]) assert.ok(name in SPRITES);
});

test("icon_script_imports_only_node_builtins_and_the_cast_data", () => {
  const source = readFileSync(path.join(root, "apps/desktop/scripts/make-icon.mjs"), "utf8");
  const imports = [...source.matchAll(/(?:from\s+|import\s*\(|require\s*\()(["'])([^"']+)\1/g)].map(match => match[2]);
  assert.ok(imports.includes("node:zlib"));
  assert.ok(imports.some(name => name.endsWith("pixel/cast.js")));
  assert.ok(imports.every(name => name.startsWith("node:") || name.endsWith("pixel/cast.js")), imports.join(", "));
  assert.doesNotMatch(source, /child_process|execSync|spawn|fetch\s*\(|https?:/);
});

test("gallery_executes_every_cast_entry_at_2x_and_3x_without_smoothing", async () => {
  const { CAST, CREDIT } = await cast();
  const html = readFileSync(path.join(staticRoot, "sprites.html"), "utf8");
  assert.match(html, /image-rendering:\s*pixelated/);
  const scales = [...html.matchAll(/<canvas data-scale="(\d+)" width="(\d+)" height="(\d+)"><\/canvas>/g)].map(match => match.slice(1).map(Number));
  assert.deepEqual(scales, [[2, 32, 32], [3, 48, 48]]);
  const cards = [], preview = [], properties = [];
  const makeCard = () => {
    const heading = {textContent: ""};
    const canvases = scales.map(([scale, width, height]) => {
      const context = {imageSmoothingEnabled: true, calls: [], drawImage(...args) {this.calls.push(args);} };
      return {width, height, dataset: {scale: String(scale)}, context, attrs: {},
        getContext: () => context, setAttribute(name, value) {this.attrs[name] = value;} };
    });
    return {dataset: {}, heading, canvases, querySelector: () => heading, querySelectorAll: () => canvases};
  };
  const elements = {
    credit: {}, status: {}, "cast-gallery": {appendChild: card => cards.push(card)},
    "frame-card": {content: {firstElementChild: {cloneNode: makeCard}}},
  };
  const document = {documentElement: {style: {setProperty: (...args) => properties.push(args)}},
    getElementById: id => {assert.ok(id in elements, id); return elements[id];} };
  const fake = textureScene();
  // Canvas outputs are genuine texture-manager crops; only browser boundaries
  // are faked. Drawing the wrong tile or omitting a card fails these assertions.
  const createCanvas = fake.scene.textures.createCanvas;
  fake.scene.textures.createCanvas = (...args) => {
    const texture = createCanvas(...args);
    texture.getSourceImage = () => texture;
    return texture;
  };
  fake.scene.add = {image(x, y, key) { const image = {x, y, key, origin: null, scale: null,
    setOrigin(value) {this.origin = value; return this;}, setScale(value) {this.scale = value; return this;} };
    preview.push(image); return image; } };
  let gameConfig;
  globalThis.document = document;
  globalThis.Phaser = {VERSION: "3.90.0", CANVAS: 1, Scale: {NONE: 0}, Textures: {FilterMode: {NEAREST: 0}},
    Game: class {constructor(config) {gameConfig = config; config.scene.preload.call(fake.scene); config.scene.create.call(fake.scene);} } };
  globalThis.window = {Phaser: globalThis.Phaser};
  const script = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1]
    .replace(/from "(\.\/[^"\n]+)"/g, (_, file) => `from "${pathToFileURL(path.join(staticRoot, file))}"`);
  await import(`data:text/javascript,${encodeURIComponent(script)}`);
  assert.equal(gameConfig.pixelArt, true); assert.equal(gameConfig.roundPixels, true);
  assert.equal(gameConfig.scale.zoom, 1);
  assert.equal(elements.credit.textContent, CREDIT);
  assert.equal(cards.length, Object.keys(CAST).length);
  assert.deepEqual(cards.map(card => card.dataset.cast), Object.keys(CAST));
  assert.equal(preview.length, cards.length); assert.ok(preview.every(image => image.scale === 2));
  assert.ok(properties.length > 0);
  for (const card of cards) {
    const name = card.dataset.cast, index = CAST[name];
    assert.equal(card.heading.textContent, `${name} · tile ${index}`);
    for (const canvas of card.canvases) {
      assert.equal(canvas.context.imageSmoothingEnabled, false);
      assert.deepEqual(canvas.context.calls, [[fake.textures.get(`${name}:idle:0`), 0, 0, canvas.width, canvas.height]]);
      assert.equal(canvas.attrs["aria-label"], `${name}, tile ${index}, ${canvas.dataset.scale}×`);
    }
  }
});

// Independent PNG fixture writer, including all predictors. It does not use
// the production encoder, so a decoder/encoder sharing a mistake cannot pass.
function fixturePng(colorType, filter = 0, depth = 8, interlace = 0) {
  const { deflateSync } = require("node:zlib");
  const width = 3, height = 5, channels = colorType === 6 ? 4 : 1;
  const rows = Array.from({length: height}, (_, y) => Buffer.from(Array.from({length: width * channels}, (_, x) => colorType === 3 ? (x + y) % 3 : (x * 29 + y * 37) % 256)));
  const raw = Buffer.alloc(height * (width * channels + 1));
  for (let y = 0; y < height; y++) {
    const offset = y * (width * channels + 1); raw[offset] = filter;
    for (let x = 0; x < width * channels; x++) {
      const a = x < channels ? 0 : rows[y][x - channels];
      const b = y === 0 ? 0 : rows[y - 1][x];
      const c = y === 0 || x < channels ? 0 : rows[y - 1][x - channels];
      const p = a + b - c, distances = [Math.abs(p - a), Math.abs(p - b), Math.abs(p - c)];
      const paeth = [a, b, c][distances.indexOf(Math.min(...distances))];
      const prediction = [0, a, b, Math.floor((a + b) / 2), paeth][filter] ?? 0;
      raw[offset + 1 + x] = (rows[y][x] - prediction) & 255;
    }
  }
  const writeChunk = (name, data) => {
    const chunk = Buffer.alloc(data.length + 12); chunk.writeUInt32BE(data.length); chunk.write(name, 4, "ascii"); data.copy(chunk, 8);
    let crc = 0xffffffff;
    for (const byte of chunk.subarray(4, -4)) {
      crc ^= byte;
      for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
    chunk.writeUInt32BE((crc ^ 0xffffffff) >>> 0, chunk.length - 4); return chunk;
  };
  const header = Buffer.alloc(13); header.writeUInt32BE(width); header.writeUInt32BE(height, 4); header[8] = depth; header[9] = colorType; header[12] = interlace;
  const palette = Buffer.from([11, 22, 33, 70, 80, 90, 200, 201, 202]);
  const parts = [Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), writeChunk("IHDR", header)];
  if (colorType === 3) parts.push(writeChunk("PLTE", palette), writeChunk("tRNS", Buffer.from([0, 128])));
  const compressed = deflateSync(raw), split = Math.floor(compressed.length / 2);
  parts.push(writeChunk("IDAT", compressed.subarray(0, split)), writeChunk("IDAT", compressed.subarray(split)), writeChunk("IEND", Buffer.alloc(0)));
  const expected = colorType === 6 ? Buffer.concat(rows) : Buffer.from(rows.flatMap(row => [...row].flatMap(index => [...palette.subarray(index * 3, index * 3 + 3), [0, 128, 255][index]])));
  return {png: Buffer.concat(parts), expected, width, height};
}

test("icon_png_decoder_handles_indexed_transparency_rgba_and_all_five_filters", async () => {
  const { decodePng, encodePng } = await import(pathToFileURL(path.join(root, "apps/desktop/scripts/make-icon.mjs")));
  for (const colorType of [3, 6]) for (let filter = 0; filter <= 4; filter++) {
    const {png, expected, width, height} = fixturePng(colorType, filter);
    assert.deepEqual(decodePng(png), {width, height, pixels: expected}, `type ${colorType}, filter ${filter}`);
    assert.deepEqual(decodePng(encodePng(width, height, expected)), {width, height, pixels: expected});
  }
});

test("icon_png_decoder_fails_clearly_on_unsupported_or_corrupt_input", async () => {
  const { decodePng, renderIcon } = await import(pathToFileURL(path.join(root, "apps/desktop/scripts/make-icon.mjs")));
  for (const [color, depth, interlace] of [[2, 8, 0], [3, 4, 0], [6, 16, 0], [6, 8, 1]]) {
    assert.throws(() => decodePng(fixturePng(color, 0, depth, interlace).png), /Unsupported PNG format/);
  }
  const valid = fixturePng(6).png;
  assert.throws(() => decodePng(Buffer.from("not PNG")), /PNG signature/);
  assert.throws(() => decodePng(valid.subarray(0, -3)), /Truncated PNG/);
  const corrupt = Buffer.from(valid); corrupt[20] ^= 1;
  assert.throws(() => decodePng(corrupt), /PNG CRC/);
  assert.throws(() => decodePng(fixturePng(6, 5).png), /PNG filter/);
  assert.throws(() => renderIcon(valid), /dimensions/);
});
