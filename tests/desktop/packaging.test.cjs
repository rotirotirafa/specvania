"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync, mkdtempSync, rmSync, mkdirSync, copyFileSync, writeFileSync, existsSync } = require("node:fs");
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

test("icon_cli_generates_deterministic_valid_png_from_kenney_mage", async () => {
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
    const { CAST } = await import(pathToFileURL(path.join(root, "src/specvania/ui/static/pixel/cast.js")));
    // Independent decoding of this exact vendored indexed sheet. Every sheet
    // row uses PNG filter 0; never derive expected pixels from the icon script.
    const sheet = readFileSync(path.join(root, "src/specvania/ui/static/vendor/kenney-tiny-dungeon/tilemap_packed.png"));
    assert.equal(sheet[24], 8); assert.equal(sheet[25], 3); assert.equal(sheet[28], 0);
    const width = sheet.readUInt32BE(16), height = sheet.readUInt32BE(20);
    const idat = []; let palette, transparency, offset = 8;
    while (offset < sheet.length) {
      const length = sheet.readUInt32BE(offset), type = sheet.toString("ascii", offset + 4, offset + 8);
      const data = sheet.subarray(offset + 8, offset + 8 + length);
      if (type === "PLTE") palette = data;
      if (type === "tRNS") transparency = data;
      if (type === "IDAT") idat.push(data);
      offset += length + 12;
    }
    const indices = inflateSync(Buffer.concat(idat));
    assert.equal(indices.length, height * (width + 1));
    for (let y = 0; y < height; y++) assert.equal(indices[y * (width + 1)], 0);
    const tileX = (CAST.mage % (width / 16)) * 16, tileY = Math.floor(CAST.mage / (width / 16)) * 16;
    let opaque = 0;
    let transparent = 0;
    for (let y = 0; y < 1024; y++) {
      const row = y * (1024 * 4 + 1);
      assert.equal(decoded.pixels[row], 0);
      for (let x = 0; x < 1024; x++) {
        const sourceX = tileX + Math.floor(x / 64), sourceY = tileY + Math.floor(y / 64);
        const index = indices[sourceY * (width + 1) + 1 + sourceX];
        const pixel = decoded.pixels.subarray(row + 1 + x * 4, row + 5 + x * 4);
        assert.deepEqual(pixel.subarray(0, 3), palette.subarray(index * 3, index * 3 + 3));
        assert.equal(pixel[3], transparency[index] ?? 255);
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
  assert.deepEqual(config.files, ["main.js", "preload.js", "studio.cjs", "build/icon.png", {
    from: "../../src/specvania/ui/static", to: "static",
    filter: ["i18n/**", "pixel/palette.js", "pixel/cast.js", "screens/**", "vendor/fonts/**", "vendor/kenney-tiny-dungeon/**"],
  }]);
  assert.deepEqual(config.mac.target, ["dmg"]);
  assert.equal(config.mac.identity, null);
  assert.equal(config.mac.icon, "build/icon.png");
  assert.equal(config.linux.icon, "build/icon.png");
  assert.equal(config.linux.executableName, "specvania-desktop");
  assert.deepEqual(config.linux.target, ["AppImage"]);
  assert.equal(config.artifactName, "specvania-${version}-${arch}.${ext}");
  assert.equal(config.appImage?.artifactName, "specvania-${version}-x64.${ext}");
  for (const workflow of ["ci.yml", "release.yml"]) {
    const matrix = json(path.join(root, ".github/workflows", workflow)).jobs["build-desktop"].strategy.matrix.include;
    for (const entry of matrix.filter(entry => entry.platform === "linux")) {
      assert.equal(entry.arch, "x64");
    }
  }
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
      "src/specvania/ui/static/pixel/cast.js",
      "src/specvania/ui/static/vendor/kenney-tiny-dungeon/tilemap_packed.png",
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
  assert.equal(existsSync(path.join(root, ".github/workflows/desktop-release.yml")), false);
  const workflow = json(path.join(root, ".github/workflows/release.yml"));
  assert.deepEqual(workflow.on, { push: { tags: ["v*.*.*"] }, workflow_dispatch: {} });
  const build = workflow.jobs["build-desktop"];
  assert.equal(build.needs, "check");
  assert.equal(build["runs-on"], "${{ matrix.os }}");
  assert.deepEqual(build.strategy.matrix.include, [
    { os: "macos-15", platform: "mac", arch: "arm64", extension: "dmg", target: "darwin-arm64" },
    { os: "macos-15-intel", platform: "mac", arch: "x64", extension: "dmg", target: "darwin-x64" },
    { os: "ubuntu-latest", platform: "linux", arch: "x64", extension: "AppImage", target: "linux-x64" },
  ]);
  assert.equal(build.env.CSC_IDENTITY_AUTO_DISCOVERY, "false");
  assert.equal(build.defaults.run["working-directory"], "apps/desktop");
  const commands = build.steps.filter((step) => step.run).map((step) => step.run);
  assert.equal(commands.length, 7);
  assert.deepEqual(commands.slice(0, 6), ["uv build --wheel", "mkdir -p apps/desktop/build\nuv export --frozen --no-dev --no-emit-project --no-hashes --output-file apps/desktop/build/runtime-requirements.txt", "npm run bundle-runtime -- --target ${{ matrix.target }} --wheel ../../dist/*.whl --requirements build/runtime-requirements.txt", "npm ci", "npm run make-icon", "./node_modules/.bin/electron-builder --${{ matrix.platform }} --${{ matrix.arch }} --publish never"]);
  assert.equal(build.steps.find(step => step.name === "Check compressed dmg size").if, "matrix.platform == 'mac'");
  const upload = build.steps.find((step) => step.uses?.startsWith("actions/upload-artifact@"));
  assert.equal(upload.with.path, "apps/desktop/dist/specvania-*-${{ matrix.arch }}.${{ matrix.extension }}");
  assert.equal(upload.with["if-no-files-found"], "error");
  assert.equal(upload.with.name, "desktop-${{ matrix.platform }}-${{ matrix.arch }}");
  const release = workflow.jobs.release;
  assert.deepEqual(release.needs, ["check", "build-python", "publish-pypi", "build-desktop"]);
  assert.equal(release.if, "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')");
  assert.deepEqual(release.permissions, { contents: "write" });
  const download = release.steps.find((step) => step.uses?.startsWith("actions/download-artifact@") && step.with.pattern === "desktop-*");
  assert.equal(download.with.path, "artifacts");
  assert.equal(download.with["merge-multiple"], true);
  const checksums = release.steps.find((step) => step.name === "Generate SHA256SUMS");
  for (const pattern of ["specvania-*-arm64.dmg", "specvania-*-x64.dmg", "specvania-*-x64.AppImage"]) {
    assert.ok(checksums.run.includes(pattern), pattern);
  }
  const publish = release.steps.find((step) => step.name === "Create GitHub Release");
  assert.ok(release.steps.indexOf(checksums) < release.steps.indexOf(publish));
  assert.equal(publish.env.GITHUB_TOKEN, "${{ secrets.GITHUB_TOKEN }}");
  assert.equal(publish.run, 'gh release create "$GITHUB_REF_NAME" --verify-tag --title "$GITHUB_REF_NAME" --notes-file release-notes.md artifacts/*.tar.gz artifacts/*.whl artifacts/*.dmg artifacts/*.AppImage artifacts/SHA256SUMS');
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

test("builder_ships_target_runtime_outside_asar_and_compresses_dmg", () => {
  const config = json(path.join(desktop, "electron-builder.yml"));
  assert.deepEqual(config.mac.extraResources, [{from: "build/runtime/darwin-${arch}", to: "runtime", filter: ["**/*"]}]);
  assert.deepEqual(config.linux.extraResources, [{from: "build/runtime/linux-${arch}", to: "runtime", filter: ["**/*"]}]);
  assert.equal(config.dmg.format, "UDZO");
  const pkg = json(path.join(desktop, "package.json"));
  assert.equal(pkg.scripts["bundle-runtime"], "node scripts/bundle-runtime.cjs");
  assert.match(readFileSync(path.join(root, ".gitignore"), "utf8"), /^apps\/desktop\/build\/runtime\/$/m);
});

test("notices_identify_bundled_python_build_scripts_and_package_licenses", () => {
  const notices = readFileSync(path.join(root, "THIRD_PARTY_NOTICES.md")), text = notices.toString();
  assert.match(text, /CPython[\s\S]*PSF License/);
  assert.match(text, /python-build-standalone[\s\S]*MPL-2\.0/);
  assert.match(text, /bundled Python packages retain their own licenses/i);
});
