/** Render original pixel-kit art to PNG without native tools or npm packages. */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";
import { PALETTE } from "../../../src/specvania/ui/static/pixel/palette.js";
import { SPRITES } from "../../../src/specvania/ui/static/pixel/sprites.js";

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) {
      crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const name = Buffer.from(type, "ascii");
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE(crc32(Buffer.concat([name, data])));
  return Buffer.concat([length, name, data, checksum]);
}

function renderIcon() {
  const frame = SPRITES.mage.idle[0];
  const size = 1024;
  const scale = size / 16; // Integer nearest-neighbour scaling; no smoothing.
  const stride = size * 4 + 1;
  const scanlines = Buffer.alloc(stride * size); // Filter byte 0 for every row.
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const symbol = frame[Math.floor(y / scale)][Math.floor(x / scale)];
      // Transparent pixels still use a palette RGB value (with zero alpha).
      const color = PALETTE[symbol] ?? PALETTE.K;
      const offset = y * stride + 1 + x * 4;
      scanlines[offset] = Number.parseInt(color.slice(1, 3), 16);
      scanlines[offset + 1] = Number.parseInt(color.slice(3, 5), 16);
      scanlines[offset + 2] = Number.parseInt(color.slice(5, 7), 16);
      scanlines[offset + 3] = symbol === "." ? 0 : 255;
    }
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = 8; // 8-bit channels
  header[9] = 6; // RGBA, compression/filter method 0, no interlace
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(scanlines, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// Default is relative to this script, independent of the player's cwd.
const output = process.argv[2] ?? fileURLToPath(new URL("../build/icon.png", import.meta.url));
mkdirSync(dirname(output), { recursive: true });
writeFileSync(output, renderIcon());
