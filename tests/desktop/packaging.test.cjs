"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync, mkdtempSync, rmSync, mkdirSync, copyFileSync, writeFileSync } = require("node:fs");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { spawnSync } = require("node:child_process");
const { inflateSync } = require("node:zlib");

const root = path.resolve(__dirname, "../..");
const desktop = path.join(root, "apps/desktop");
const json = (file) => JSON.parse(readFileSync(file, "utf8"));

// Independent PNG decoder/checksum: a changed signature, chunk, CRC, filter,
// scale or sprite pixel must fail before the icon can count as evidence.
function decodePng(png) {
  assert.deepEqual(png.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  const chunks = [];
  let offset = 8;
  while (offset < png.length) {
    const length = png.readUInt32BE(offset);
    const type = png.toString("ascii", offset + 4, offset + 8);
    const body = png.subarray(offset + 8, offset + 8 + length);
    assert.equal(body.length, length);
    let crc = 0xffffffff;
    for (const byte of png.subarray(offset + 4, offset + 8 + length)) {
      crc ^= byte;
      for (let bit = 0; bit < 8; bit++) {
        crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
      }
    }
    assert.equal(png.readUInt32BE(offset + 8 + length), (crc ^ 0xffffffff) >>> 0);
    chunks.push({ type, body });
    offset += length + 12;
  }
  assert.equal(offset, png.length);
  assert.deepEqual(chunks.map(({ type }) => type), ["IHDR", "IDAT", "IEND"]);
  const header = chunks[0].body;
  assert.equal(header.length, 13);
  assert.deepEqual([...header.subarray(8)], [8, 6, 0, 0, 0]); // RGBA, no interlace
  assert.equal(chunks[2].body.length, 0);
  return {
    width: header.readUInt32BE(0), height: header.readUInt32BE(4),
    pixels: inflateSync(chunks[1].body),
  };
}

test("icon_cli_generates_deterministic_valid_png_from_pixel_kit", async () => {
  const directory = mkdtempSync(path.join(tmpdir(), "specvania-icon-"));
  try {
    const output = path.join(directory, "resources/icon.png");
    const script = path.join(desktop, "scripts/make-icon.mjs");
    const generate = () => {
      const result = spawnSync(process.execPath, [script, output], { cwd: directory, encoding: "utf8" });
      assert.equal(result.status, 0, result.stdout + result.stderr);
      return readFileSync(output);
    };
    const first = generate();
    assert.deepEqual(generate(), first);
    const decoded = decodePng(first);
    assert.equal(decoded.width, 1024);
    assert.equal(decoded.height, 1024);
    assert.equal(decoded.pixels.length, 1024 * (1024 * 4 + 1));
    const { SPRITES } = await import(pathToFileURL(path.join(root, "src/specvania/ui/static/pixel/sprites.js")));
    const { PALETTE } = await import(pathToFileURL(path.join(root, "src/specvania/ui/static/pixel/palette.js")));
    const frame = SPRITES.mage.idle[0];
    const colors = new Set(Object.values(PALETTE).filter(Boolean));
    let opaque = 0;
    let transparent = 0;
    for (let y = 0; y < 1024; y++) {
      const row = y * (1024 * 4 + 1);
      assert.equal(decoded.pixels[row], 0);
      for (let x = 0; x < 1024; x++) {
        const symbol = frame[Math.floor(y / 64)][Math.floor(x / 64)];
        const pixel = decoded.pixels.subarray(row + 1 + x * 4, row + 5 + x * 4);
        const rgb = `#${pixel.subarray(0, 3).toString("hex")}`;
        assert.ok(colors.has(rgb), rgb);
        assert.equal(rgb, PALETTE[symbol] ?? PALETTE.K);
        assert.equal(pixel[3], symbol === "." ? 0 : 255);
        if (pixel[3]) opaque++; else transparent++;
      }
    }
    assert.ok(opaque > 0 && transparent > 0);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("builder_packages_only_shell_and_generated_icon_for_unsigned_dmg_and_appimage", () => {
  const config = json(path.join(desktop, "electron-builder.yml"));
  assert.equal(config.appId, "specvania");
  assert.equal(config.productName, "specvania");
  assert.deepEqual(config.directories, { output: "dist", buildResources: "build" });
  assert.deepEqual(config.files, ["main.js", "preload.js", "studio.cjs", "build/icon.png"]);
  assert.deepEqual(config.mac.target, [{ target: "dmg", arch: ["arm64", "x64"] }]);
  assert.equal(config.mac.identity, null);
  assert.equal(config.mac.icon, "build/icon.png");
  assert.equal(config.linux.icon, "build/icon.png");
  assert.equal(config.linux.executableName, "specvania-desktop");
  assert.deepEqual(config.linux.target, [{ target: "AppImage", arch: ["x64"] }]);
  assert.equal(config.artifactName, "specvania-${version}-${arch}.${ext}");
  const pkg = json(path.join(desktop, "package.json"));
  assert.deepEqual(pkg.devDependencies, { electron: "44.7.0", "electron-builder": "26.15.3" });
  assert.ok(!pkg.dependencies || Object.keys(pkg.dependencies).length === 0);
  assert.equal(pkg.scripts["make-icon"], "node scripts/make-icon.mjs");
  for (const platform of ["mac", "linux"]) {
    assert.equal(pkg.scripts[`dist:${platform}`], `npm run make-icon && electron-builder --${platform} --publish never`);
  }
  assert.match(readFileSync(path.join(root, ".gitignore"), "utf8"), /^build\/$/m);
});

test("build_scripts_generate_icon_and_forward_platform_arch_without_publishing", () => {
  const directory = mkdtempSync(path.join(tmpdir(), "specvania-build-"));
  try {
    // Run the real npm scripts and icon generator in an isolated checkout.
    // Only the external builder boundary is replaced; no download or install.
    for (const file of [
      "apps/desktop/package.json", "apps/desktop/scripts/make-icon.mjs",
      "src/specvania/ui/static/pixel/sprites.js", "src/specvania/ui/static/pixel/palette.js",
    ]) {
      const target = path.join(directory, file);
      mkdirSync(path.dirname(target), { recursive: true });
      copyFileSync(path.join(root, file), target);
    }
    const bin = path.join(directory, "bin");
    mkdirSync(bin);
    const record = path.join(directory, "builder-args.json");
    writeFileSync(path.join(bin, "electron-builder"), `#!/usr/bin/env node
const fs = require("node:fs");
const png = fs.readFileSync("build/icon.png");
if (png.readUInt32BE(16) !== 1024) process.exit(1);
fs.writeFileSync(process.env.SPECVANIA_BUILD_RECORD, JSON.stringify(process.argv.slice(2)));
`, { mode: 0o755 });
    for (const [platform, arch] of [["mac", "arm64"], ["mac", "x64"], ["linux", "x64"]]) {
      const app = path.join(directory, "apps/desktop");
      rmSync(path.join(app, "build"), { recursive: true, force: true });
      const result = spawnSync("npm", ["--prefix", app, "run", `dist:${platform}`, "--", `--${arch}`], {
        encoding: "utf8",
        env: { ...process.env, PATH: `${bin}${path.delimiter}${process.env.PATH}`, SPECVANIA_BUILD_RECORD: record },
      });
      assert.equal(result.status, 0, result.stdout + result.stderr);
      assert.deepEqual(json(record), [`--${platform}`, "--publish", "never", `--${arch}`]);
      assert.equal(decodePng(readFileSync(path.join(app, "build/icon.png"))).width, 1024);
    }
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("release_workflow_builds_each_platform_then_uploads_all_release_artifacts", () => {
  const workflow = json(path.join(root, ".github/workflows/desktop-release.yml"));
  assert.deepEqual(workflow.on, { push: { tags: ["desktop-v*"] } });
  const build = workflow.jobs.build;
  assert.equal(build["runs-on"], "${{ matrix.os }}");
  assert.deepEqual(build.strategy.matrix.include, [
    { os: "macos-latest", platform: "mac", arch: "arm64", extension: "dmg" },
    { os: "macos-latest", platform: "mac", arch: "x64", extension: "dmg" },
    { os: "ubuntu-latest", platform: "linux", arch: "x64", extension: "AppImage" },
  ]);
  assert.equal(build.env.CSC_IDENTITY_AUTO_DISCOVERY, "false");
  assert.equal(build.defaults.run["working-directory"], "apps/desktop");
  const commands = build.steps.filter((step) => step.run).map((step) => step.run);
  assert.deepEqual(commands, ["npm ci", "npm run check", "npm run make-icon", "npm run dist:${{ matrix.platform }} -- --${{ matrix.arch }}"]);
  const upload = build.steps.find((step) => step.uses === "actions/upload-artifact@v4");
  assert.equal(upload.with.path, "apps/desktop/dist/specvania-*-${{ matrix.arch }}.${{ matrix.extension }}");
  assert.equal(upload.with["if-no-files-found"], "error");
  assert.equal(upload.with.name, "desktop-${{ matrix.platform }}-${{ matrix.arch }}");
  const release = workflow.jobs.release;
  assert.equal(release.needs, "build");
  assert.deepEqual(release.permissions, { contents: "write" });
  const download = release.steps.find((step) => step.uses === "actions/download-artifact@v4");
  assert.equal(download.with.path, "artifacts");
  assert.equal(download.with["merge-multiple"], true);
  const publish = release.steps.find((step) => step.uses === "softprops/action-gh-release@v2");
  assert.equal(publish.with.files, "artifacts/specvania-*.dmg\nartifacts/specvania-*.AppImage");
  assert.equal(publish.with.fail_on_unmatched_files, true);
});

test("desktop_readme_documents_unsigned_installation_and_discovery", () => {
  const readme = readFileSync(path.join(desktop, "README.md"), "utf8");
  for (const phrase of ["unsigned", "not notarized", "Gatekeeper", "right-click", "Open", "/Applications/specvania.app", 'xattr -dr com.apple.quarantine "/Applications/specvania.app"', "chmod +x specvania-", "AppImage", "PATH", "build/icon.png", "gitignored", "npm run dist:mac", "npm run dist:linux"]) {
    assert.ok(readme.includes(phrase), phrase);
  }
});

test("third_party_notices_reference_electron_license_and_bundled_notices", () => {
  const notices = readFileSync(path.join(root, "THIRD_PARTY_NOTICES.md"), "utf8");
  for (const phrase of ["Electron", "44.7.0", "MIT", "https://github.com/electron/electron/blob/v44.7.0/LICENSE", "LICENSES.chromium.html", "electron-builder", "26.15.3"]) {
    assert.ok(notices.includes(phrase), phrase);
  }
});
