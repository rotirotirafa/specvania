"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const desktop = path.resolve(__dirname, "../../apps/desktop");
const config = JSON.parse(fs.readFileSync(path.join(desktop, "electron-builder.yml")));
const hook = () => require(path.join(desktop, "scripts/verify-runtime.cjs"));
const version = JSON.parse(fs.readFileSync(path.join(desktop, "package.json"))).version;

function fixture(t, platform) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "sv-after-pack with spaces-"));
  t.after(() => fs.rmSync(directory, {recursive: true, force: true}));
  const appOutDir = path.join(directory, "packed");
  const resources = platform === "darwin" ? path.join(appOutDir, "spec vania.app/Contents/Resources") : path.join(appOutDir, "resources");
  const python = path.join(resources, "runtime/bin/python3");
  const context = {electronPlatformName: platform, appOutDir, packager: {appInfo: {productFilename: "spec vania"}}};
  const log = path.join(directory, "argv.log");
  const present = (actual = version) => {
    fs.mkdirSync(path.dirname(python), {recursive: true});
    // Replace only the Python executable boundary, retaining real filesystem
    // permission checks and subprocess execution in the packaging hook.
    fs.writeFileSync(python, `#!/bin/sh\nprintf '%s\\n' "$@" > '${log}'\nprintf '%s\\n' '${actual}'\n`, {mode: 0o755});
  };
  return {context, python, log, present};
}

for (const platform of ["darwin", "linux"]) {
  test(`packaging_rejects_missing_runtime_${platform}`, t => {
    const f = fixture(t, platform);
    assert.throws(() => hook()(f.context), /runtime.*missing|runtime.*executable/i);
  });
  test(`packaging_accepts_executable_matching_runtime_${platform}`, t => {
    const f = fixture(t, platform); f.present();
    assert.doesNotThrow(() => hook()(f.context));
    assert.deepEqual(fs.readFileSync(f.log, "utf8").trim().split("\n"), ["-I", "-B", "-m", "specvania", "--version"]);
  });
  test(`packaging_rejects_nonexecutable_runtime_${platform}`, t => {
    const f = fixture(t, platform); f.present(); fs.chmodSync(f.python, 0o644);
    assert.throws(() => hook()(f.context), /runtime.*executable/i);
    assert.equal(fs.existsSync(f.log), false);
  });
  test(`packaging_rejects_broken_python_${platform}`, t => {
    const f = fixture(t, platform); f.present();
    fs.writeFileSync(f.python, "#!/bin/sh\nexit 1\n");
    assert.throws(() => hook()(f.context), /runtime.*version|runtime.*failed/i);
  });
  test(`packaging_rejects_wrong_app_version_${platform}`, t => {
    const f = fixture(t, platform); f.present("9.9.9");
    assert.throws(() => hook()(f.context), /runtime.*version/i);
  });
  test(`packaging_rejects_broken_python_symlink_${platform}`, t => {
    const f = fixture(t, platform); fs.mkdirSync(path.dirname(f.python), {recursive: true});
    fs.symlinkSync("missing-python3.13", f.python);
    assert.throws(() => hook()(f.context), /runtime.*missing|runtime.*executable/i);
  });
}

test("builder_uses_requested_arch_without_expanding_to_other_arches_and_checks_after_pack", () => {
  assert.deepEqual(config.mac.target, ["dmg"]);
  assert.deepEqual(config.linux.target, ["AppImage"]);
  assert.equal(config.afterPack, "scripts/verify-runtime.cjs");
  assert.equal(typeof require(path.join(desktop, config.afterPack)), "function");
});
