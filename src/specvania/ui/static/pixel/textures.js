import { PALETTE } from "./palette.js";
import { SPRITES } from "./sprites.js";
import { CAST, LEGACY_CAST, TILE_SIZE, SHEET_KEY, SHEET_URL } from "./cast.js";

/** Stable keys shared by the gallery and map/HUD scenes. */
export function textureKey(name, state, index = 0) {
  return `${name}:${state}:${index}`;
}

/** Call in preload(): Phaser fetches only the vendored, same-origin sheet. */
export function preloadTextures(scene) {
  if (scene.textures.exists(SHEET_KEY)) return;
  scene.load.spritesheet(SHEET_KEY, SHEET_URL, {
    frameWidth: TILE_SIZE, frameHeight: TILE_SIZE, margin: 0, spacing: 0,
  });
}

/** Call in create(). Register named tile textures and the old map's keys once.
 * Canvas aliases preserve callers that use add.image(key) without a frame.
 * Crops use source pixels without scaling; all filtering remains NEAREST.
 * Legacy HUD and area tiles stay until their replacing rooms are implemented.
 */
export function registerTextures(scene) {
  if (!scene.textures.exists(SHEET_KEY)) throw new Error("Kenney sheet is missing; call preloadTextures in preload()");
  const sheet = scene.textures.get?.(SHEET_KEY);
  if (sheet) sheet.setFilter(Phaser.Textures.FilterMode.NEAREST);

  function tileTexture(name, state, index, entry) {
    const key = textureKey(name, state, index);
    if (scene.textures.exists(key)) return;
    const tile = CAST[entry];
    if (tile === undefined) throw new Error(`Unknown cast entry: ${entry}`);
    const source = sheet.getSourceImage();
    const columns = source.width / TILE_SIZE;
    const texture = scene.textures.createCanvas(key, TILE_SIZE, TILE_SIZE);
    if (!texture) throw new Error(`Could not create tile texture: ${key}`);
    const context = texture.getContext();
    context.imageSmoothingEnabled = false;
    context.drawImage(source, (tile % columns) * TILE_SIZE, Math.floor(tile / columns) * TILE_SIZE,
      TILE_SIZE, TILE_SIZE, 0, 0, TILE_SIZE, TILE_SIZE);
    texture.refresh();
    texture.setFilter(Phaser.Textures.FilterMode.NEAREST);
  }
  for (const name of Object.keys(CAST)) {
    tileTexture(name, "idle", 0, name);
    tileTexture(name, "defeated", 0, name);
  }
  for (const [name, states] of Object.entries(LEGACY_CAST)) {
    for (const [state, frames] of Object.entries(states)) {
      frames.forEach((entry, index) => tileTexture(name, state, index, entry));
    }
  }
  for (const [name, states] of Object.entries(SPRITES)) {
    for (const [state, frames] of Object.entries(states)) {
      frames.forEach((frame, index) => {
        const key = textureKey(name, state, index);
        if (scene.textures.exists(key)) return;
        if (frame.length !== 16 || frame.some((row) => row.length !== 16)) {
          throw new Error(`Invalid 16x16 sprite frame: ${key}`);
        }
        const texture = scene.textures.createCanvas(key, 16, 16);
        if (!texture) throw new Error(`Could not create sprite texture: ${key}`);
        const context = texture.getContext();
        context.imageSmoothingEnabled = false;
        frame.forEach((row, y) => {
          [...row].forEach((pixel, x) => {
            const color = PALETTE[pixel];
            if (color === undefined) throw new Error(`Unknown palette symbol in ${key}: ${pixel}`);
            if (color === null) return;
            context.fillStyle = color;
            context.fillRect(x, y, 1, 1);
          });
        });
        texture.refresh();
        texture.setFilter(Phaser.Textures.FilterMode.NEAREST);
      });
    }
  }
}
