/** Render the Kenney Mage tile to PNG using only Node built-ins. */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync, inflateSync } from "node:zlib";
import { CAST, TILE_SIZE, SHEET_URL } from "../../../src/specvania/ui/static/pixel/cast.js";

const SIGNATURE = Object.freeze([137, 80, 78, 71, 13, 10, 26, 10]);

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

function paeth(left, up, corner) {
  const prediction = left + up - corner;
  const a = Math.abs(prediction - left), b = Math.abs(prediction - up), c = Math.abs(prediction - corner);
  return a <= b && a <= c ? left : b <= c ? up : corner;
}

/** Minimal non-interlaced PNG decoder: 8-bit indexed (PLTE/tRNS) or RGBA.
 * Support all five PNG scanline filters. Other formats fail explicitly.
 */
export function decodePng(png) {
  if (!png.subarray(0, 8).equals(Buffer.from(SIGNATURE))) throw new Error("Invalid PNG signature");
  let header, palette, transparency;
  let ended = false;
  const compressed = [];
  let offset = 8;
  while (offset < png.length) {
    if (offset + 12 > png.length) throw new Error("Truncated PNG chunk");
    const length = png.readUInt32BE(offset);
    const end = offset + 8 + length;
    if (end + 4 > png.length) throw new Error("Truncated PNG chunk data");
    const type = png.toString("ascii", offset + 4, offset + 8);
    const data = png.subarray(offset + 8, end);
    if (crc32(png.subarray(offset + 4, end)) !== png.readUInt32BE(end)) throw new Error(`Invalid PNG CRC: ${type}`);
    if (offset === 8 && type !== "IHDR") throw new Error("PNG must start with IHDR");
    if (type === "IHDR") {
      if (header || length !== 13) throw new Error("Invalid PNG IHDR");
      header = data;
    } else if (type === "PLTE") palette = data;
    else if (type === "tRNS") transparency = data;
    else if (type === "IDAT") compressed.push(data);
    else if (type === "IEND") {
      if (length !== 0 || end + 4 !== png.length) throw new Error("Invalid PNG IEND");
      ended = true;
    }
    offset = end + 4;
  }
  if (!header || !ended || !compressed.length) throw new Error("PNG is missing IHDR, IDAT or IEND");
  const width = header.readUInt32BE(0), height = header.readUInt32BE(4);
  const depth = header[8], colorType = header[9];
  if (!width || !height || width * height > 16777216) throw new Error("Invalid or oversized PNG dimensions");
  if (depth !== 8 || ![3, 6].includes(colorType) || header[10] || header[11] || header[12]) {
    throw new Error(`Unsupported PNG format: bit depth ${depth}, color type ${colorType}, interlace ${header[12]}; expected non-interlaced 8-bit indexed or RGBA`);
  }
  if (colorType === 3 && (!palette || !palette.length || palette.length % 3 || palette.length > 768)) throw new Error("Indexed PNG requires a valid PLTE");
  const channels = colorType === 6 ? 4 : 1;
  const stride = width * channels;
  const raw = inflateSync(Buffer.concat(compressed), { maxOutputLength: (stride + 1) * height });
  if (raw.length !== (stride + 1) * height) throw new Error("Invalid PNG scanline length");
  const unfiltered = Buffer.alloc(stride * height);
  for (let y = 0; y < height; y++) {
    const row = y * stride, input = y * (stride + 1), filter = raw[input];
    if (filter > 4) throw new Error(`Unsupported PNG filter: ${filter}`);
    for (let x = 0; x < stride; x++) {
      const left = x >= channels ? unfiltered[row + x - channels] : 0;
      const up = y ? unfiltered[row + x - stride] : 0;
      const corner = y && x >= channels ? unfiltered[row + x - stride - channels] : 0;
      const predictor = [0, left, up, Math.floor((left + up) / 2), paeth(left, up, corner)][filter];
      unfiltered[row + x] = (raw[input + 1 + x] + predictor) & 255;
    }
  }
  const pixels = Buffer.alloc(width * height * 4);
  if (colorType === 6) unfiltered.copy(pixels);
  else for (let i = 0; i < unfiltered.length; i++) {
    const index = unfiltered[i];
    if (index * 3 + 2 >= palette.length) throw new Error(`PNG palette index out of range: ${index}`);
    palette.copy(pixels, i * 4, index * 3, index * 3 + 3);
    pixels[i * 4 + 3] = transparency?.[index] ?? 255;
  }
  return { width, height, pixels };
}

export function encodePng(width, height, pixels) {
  if (pixels.length !== width * height * 4) throw new Error("RGBA pixel length does not match icon dimensions");
  const stride = width * 4 + 1;
  const scanlines = Buffer.alloc(stride * height); // Filter 0 on each row.
  for (let y = 0; y < height; y++) pixels.copy(scanlines, y * stride + 1, y * width * 4, (y + 1) * width * 4);
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4);
  header[8] = 8; header[9] = 6;
  return Buffer.concat([Buffer.from(SIGNATURE), chunk("IHDR", header), chunk("IDAT", deflateSync(scanlines, { level: 9 })), chunk("IEND", Buffer.alloc(0))]);
}

export function renderIcon(png = readFileSync(fileURLToPath(SHEET_URL))) {
  const sheet = decodePng(png);
  if (sheet.width % TILE_SIZE || sheet.height % TILE_SIZE) throw new Error("Kenney sheet dimensions must be multiples of 16");
  const columns = sheet.width / TILE_SIZE;
  const tile = CAST.mage;
  if (tile >= columns * sheet.height / TILE_SIZE) throw new Error("Mage tile is outside the Kenney sheet");
  const tileX = (tile % columns) * TILE_SIZE, tileY = Math.floor(tile / columns) * TILE_SIZE;
  const size = 1024, scale = size / TILE_SIZE;
  const pixels = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const source = ((tileY + Math.floor(y / scale)) * sheet.width + tileX + Math.floor(x / scale)) * 4;
    sheet.pixels.copy(pixels, (y * size + x) * 4, source, source + 4);
  }
  return encodePng(size, size, pixels);
}

// Imports are side-effect free for decoder tests. CLI output is cwd-independent.
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const output = process.argv[2] ?? fileURLToPath(new URL("../build/icon.png", import.meta.url));
    const icon = renderIcon();
    mkdirSync(dirname(output), { recursive: true });
    writeFileSync(output, icon);
  } catch (error) {
    console.error(`Could not generate Kenney Mage icon: ${error.message}`);
    process.exitCode = 1;
  }
}
