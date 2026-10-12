import { PALETTE } from "./palette.js";
import { SPRITES } from "./sprites.js";

/** Stable keys shared by the gallery and later map/HUD scenes. */
export function textureKey(name, state, index = 0) {
  return `${name}:${state}:${index}`;
}

/** Register every original frame once per game's shared texture manager.
 * Call from a Phaser scene's create(). No file loader or network is involved.
 * Drawing single pixels and using NEAREST keeps integer scales sharp in both
 * Phaser renderers. Repeated calls from other scenes preserve existing textures.
 */
export function registerTextures(scene) {
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
            if (color === undefined) {
              throw new Error(`Unknown palette symbol in ${key}: ${pixel}`);
            }
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
