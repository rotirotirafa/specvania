"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { createHash } = require("node:crypto");
const { spawnSync } = require("node:child_process");
const { EventEmitter } = require("node:events");
const desktop = path.resolve(__dirname, "../../apps/desktop");
const builder = () => require(path.join(desktop, "scripts/bundle-runtime.cjs"));
const pins = () => JSON.parse(fs.readFileSync(path.join(desktop, "runtime.json")));
const sha = file => createHash("sha256").update(fs.readFileSync(file)).digest("hex");

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sv-runtime with spaces-"));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  const python = path.join(dir, "source/python/bin/python3");
  fs.mkdirSync(path.dirname(python), {recursive: true});
  // Only Python/package installation is fake. Hashing, extraction, layout,
  // permissions, CLI parsing and subprocess failures exercise real code.
  fs.writeFileSync(python, `#!/bin/sh\nprintf '%s\\n' "$*" >> "$RUNTIME_TEST_LOG"\ncase "$*" in\n  *--version) printf '%s\\n' "$RUNTIME_TEST_VERSION";;\n  *) exit 0;;\nesac\n`, {mode: 0o755});
  fs.mkdirSync(path.join(dir, "source/python/lib"));
  fs.writeFileSync(path.join(dir, "source/python/lib/fixture.py"), "pass\n");
  const archive = path.join(dir, "fixture.tar.gz");
  const tar = spawnSync("tar", ["-czf", archive, "-C", path.join(dir, "source"), "python"]);
  assert.equal(tar.status, 0, tar.stderr.toString());
  const wheel = path.join(dir, "specvania-0.3.0-py3-none-any.whl"); fs.writeFileSync(wheel, "wheel boundary fixture");
  const requirements = path.join(dir, "runtime requirements.txt"); fs.writeFileSync(requirements, "typer==0.26.8\n");
  const version = JSON.parse(fs.readFileSync(path.join(desktop, "package.json"))).version;
  const pin = {...pins(), targets: {"darwin-arm64": {...pins().targets["darwin-arm64"], sha256: sha(archive)}}};
  const log = path.join(dir, "python-calls.log");
  return {dir, archive, log, options: {target: "darwin-arm64", wheel, requirements, archive,
    output: path.join(dir, "build/runtime"), cache: path.join(dir, "cache"), pins: pin,
    env: {PATH: process.env.PATH, RUNTIME_TEST_LOG: log, RUNTIME_TEST_VERSION: version}}};
}

test("runtime_rejects_bad_archive_before_extraction_or_install", async t => {
  const f = fixture(t); fs.appendFileSync(f.archive, "tampered");
  await assert.rejects(builder().bundleRuntime(f.options), /SHA-256/);
  assert.equal(fs.existsSync(f.options.output), false);
  assert.equal(fs.existsSync(f.log), false);
});

test("runtime_bad_download_is_deleted_and_never_cached", async t => {
  const f = fixture(t); let url;
  await assert.rejects(builder().bundleRuntime({...f.options, archive: undefined,
    download: async (value, destination) => {url = value; fs.writeFileSync(destination, "bad download");}}), /SHA-256/);
  assert.equal(url, `https://github.com/astral-sh/python-build-standalone/releases/download/${pins().release}/${pins().targets["darwin-arm64"].file.replaceAll("+", "%2B")}`);
  assert.deepEqual(fs.readdirSync(f.options.cache), []);
  assert.equal(fs.existsSync(f.options.output), false);
});

test("runtime_unpacks_python_at_root_installs_locked_wheel_precompiles_and_checks_version", async t => {
  const f = fixture(t); await builder().bundleRuntime(f.options);
  assert.equal(fs.existsSync(path.join(f.options.output, "bin/python3")), true);
  assert.equal(fs.existsSync(path.join(f.options.output, "lib/fixture.py")), true);
  assert.equal(fs.existsSync(path.join(f.options.output, "python")), false);
  const calls = fs.readFileSync(f.log, "utf8").trim().split("\n");
  assert.ok(calls.some(line => line.includes(`-m pip install --no-deps -r ${f.options.requirements} ${f.options.wheel}`)));
  assert.ok(!calls.join("\n").includes("postgres"));
  const compile = calls.findIndex(line => line.includes("-m compileall -q"));
  assert.ok(compile > calls.findIndex(line => line.includes("-m pip install")));
  assert.equal(calls.at(-1), "-I -B -m specvania --version");
});

test("runtime_version_mismatch_removes_unshippable_output", async t => {
  const f = fixture(t);
  await assert.rejects(builder().bundleRuntime({...f.options, env: {...f.options.env, RUNTIME_TEST_VERSION: "9.9.9"}}), /version.*9\.9\.9/i);
  assert.equal(fs.existsSync(f.options.output), false);
});

test("runtime_requires_wheel_requirements_and_known_target_without_network", async t => {
  const f = fixture(t);
  for (const patch of [{wheel: undefined}, {requirements: undefined}, {target: "win32-x64"}]) {
    await assert.rejects(builder().bundleRuntime({...f.options, ...patch}), /wheel|requirements|target/i);
  }
  const cli = spawnSync(process.execPath, [path.join(desktop, "scripts/bundle-runtime.cjs"), "--target", "darwin-arm64"], {encoding: "utf8"});
  assert.notEqual(cli.status, 0); assert.match(cli.stderr, /wheel/i);
});

test("studio_resolution_custom_then_bundle_then_PATH_and_legacy_default", () => {
  const {resolveStudio} = require(path.join(desktop, "studio.cjs"));
  const resourcesPath = "/Applications/spec vania.app/Contents/Resources";
  const python = resourcesPath + "/runtime/bin/python3";
  for (const executable of [undefined, "specvania"]) {
    assert.deepEqual(resolveStudio({executable, resourcesPath, exists: file => file === python}),
      {kind: "bundled", executable: python, args: ["-I", "-B", "-m", "specvania", "studio"]});
  }
  assert.deepEqual(resolveStudio({executable: "/custom path/specvania", resourcesPath, exists: () => true}),
    {kind: "custom", executable: "/custom path/specvania", args: ["studio"]});
  assert.deepEqual(resolveStudio({resourcesPath, exists: () => false}), {kind: "path", executable: "specvania", args: ["studio"]});
});

test("bundled_studio_shell_quotes_spaces_and_ignores_python_environment_without_secrets_in_argv", () => {
  const {shellLaunch, launchStudio} = require(path.join(desktop, "studio.cjs"));
  const resourcesPath = "/Applications/spec '$(touch injected)' vania.app/Contents/Resources";
  const launch = shellLaunch({resourcesPath, exists: () => true, env: {SHELL: "/bin/bash"}});
  assert.equal(launch.command, "/bin/bash"); assert.equal(launch.args[0], "-lic");
  const parsed = spawnSync("/bin/bash", ["-c", launch.args[1].replace(/^exec /, "printf '%s\\n' ")], {encoding: "utf8"});
  assert.equal(parsed.status, 0);
  assert.equal(parsed.stdout, `${resourcesPath}/runtime/bin/python3\n-I\n-B\n-m\nspecvania\nstudio\n`);
  const env = {SHELL: "/bin/bash", NVIDIA_API_KEY: "fake-api-secret", PYTHONPATH: "/hostile/python", PATH: "/user/git/bin"};
  const child = new EventEmitter(); child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
  let spawned;
  launchStudio({resourcesPath, exists: () => true, env, spawn: (...args) => {spawned = args; return child;},
    timer: {setTimeout() {}, clearTimeout() {}}, log() {}, logPath: "/safe/log", error() {}, open() {}});
  assert.ok(!JSON.stringify(spawned.slice(0, 2)).includes(env.NVIDIA_API_KEY));
  assert.equal(spawned[2].env.PATH, env.PATH);
  child.stdout.emit("data", JSON.stringify({port: 8765, view_token: "fake-view", control_token: "fake-control"}) + "\n");
  for (const secret of ["fake-view", "fake-control"]) assert.ok(!JSON.stringify(spawned).includes(secret));
});

test("runtime_valid_cache_is_reused_without_download", async t => {
  const f = fixture(t); fs.mkdirSync(f.options.cache);
  fs.copyFileSync(f.archive, path.join(f.options.cache, f.options.pins.targets[f.options.target].file));
  await builder().bundleRuntime({...f.options, archive: undefined, download: () => assert.fail("cache must be offline")});
  assert.equal(fs.existsSync(path.join(f.options.output, "bin/python3")), true);
});

test("runtime_refuses_missing_python_layout", async t => {
  const f = fixture(t); const empty = path.join(f.dir, "empty"); fs.mkdirSync(empty);
  const tar = spawnSync("tar", ["-czf", f.archive, "-C", empty, "."]); assert.equal(tar.status, 0);
  f.options.pins.targets[f.options.target].sha256 = sha(f.archive);
  await assert.rejects(builder().bundleRuntime(f.options), /python\/bin\/python3/);
  assert.equal(fs.existsSync(f.options.output), false);
});

test("runtime_install_failure_stops_before_version_check_and_removes_output", async t => {
  const f = fixture(t);
  const python = path.join(f.dir, "source/python/bin/python3");
  // Replace the fake program with one that reports an installation failure.
  fs.writeFileSync(python, "#!/bin/sh\necho 'fixture install failed' >&2\nexit 1\n", {mode: 0o755});
  spawnSync("tar", ["-czf", f.archive, "-C", path.join(f.dir, "source"), "python"]);
  f.options.pins.targets[f.options.target].sha256 = sha(f.archive);
  await assert.rejects(builder().bundleRuntime(f.options), /fixture install failed/);
  assert.equal(fs.existsSync(f.options.output), false);
});

for (const legacy of [false, true]) test(`main_uses_resources_runtime_without_CLI_and_reports_it_to_settings_legacy_${legacy}`, async t => {
  const vm = require("node:vm");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sv-bundled-main-"));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  const userData = path.join(dir, "userData"), resourcesPath = path.join(dir, "spec vania.app/Contents/Resources");
  fs.mkdirSync(path.join(resourcesPath, "runtime/bin"), {recursive: true});
  fs.writeFileSync(path.join(resourcesPath, "runtime/bin/python3"), "fixture");
  fs.mkdirSync(userData);
  if (legacy) fs.writeFileSync(path.join(userData, "settings.json"), JSON.stringify({specvaniaPath: "specvania"}));
  const app = new EventEmitter(); app.whenReady = () => Promise.resolve(); app.getPath = () => userData; app.exit = () => assert.fail("main failed"); app.quit = () => {};
  const handlers = new Map(), ipcMain = new EventEmitter(); ipcMain.handle = (name, fn) => handlers.set(name, fn);
  const pages = [], spawned = [];
  const child = new EventEmitter(); child.stdout = new EventEmitter(); child.stderr = new EventEmitter(); child.kill = () => {};
  let window;
  class BrowserWindow extends EventEmitter {
    constructor() {super(); window = this; this.webContents = new EventEmitter(); this.webContents.setWindowOpenHandler = () => {}; this.webContents.setZoomLevel = () => {};}
    loadURL(url) {pages.push(url); return Promise.resolve();}
  }
  const studio = require(path.join(desktop, "studio.cjs"));
  vm.runInNewContext(fs.readFileSync(path.join(desktop, "main.js"), "utf8"), {
    __dirname: desktop, URL, console, process: {argv: ["electron", "."], platform: "darwin", resourcesPath},
    require(name) {
      if (name === "electron") return {app, BrowserWindow, ipcMain, Notification: {}, shell: {},
        Menu: {buildFromTemplate: v => v, setApplicationMenu() {}}, screen: {getAllDisplays: () => []}};
      if (name === "node:child_process") return {spawn: (...args) => {spawned.push(args); return child;}};
      if (name.endsWith("studio.cjs")) return {...studio, launchStudio: options => studio.launchStudio({...options,
        env: {SHELL: "/bin/zsh", PATH: "/no-python-or-specvania", NVIDIA_API_KEY: "fake-key"},
        timer: {setTimeout() {}, clearTimeout() {}}})};
      return require(name);
    },
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(spawned[0][0], "/bin/zsh");
  assert.deepEqual(spawned[0][1], ["-lic", `exec '${resourcesPath}/runtime/bin/python3' -I -B -m specvania studio`]);
  child.stdout.emit("data", JSON.stringify({port: 8765, view_token: "fake-view", control_token: "fake-control"}) + "\n");
  assert.equal(pages.at(-1), "http://127.0.0.1:8765/?view_token=fake-view");
  const trusted = {sender: window.webContents, senderFrame: {url: pages.at(-1)}};
  assert.equal(handlers.get("specvania:studio")(trusted).runtime.kind, "bundled");
  for (const secret of ["fake-key", "fake-view", "fake-control"]) assert.ok(!JSON.stringify(spawned[0].slice(0, 2)).includes(secret));
  child.emit("exit", 1);
  const failedFrame = {sender: window.webContents, senderFrame: {url: pages.at(-1)}};
  assert.equal(handlers.get("specvania:studio")(failedFrame).runtime.kind, "bundled");
});

test("runtime_corrupted_cached_archive_is_deleted_without_extraction", async t => {
  const f = fixture(t); fs.mkdirSync(f.options.cache);
  const cached = path.join(f.options.cache, f.options.pins.targets[f.options.target].file);
  fs.writeFileSync(cached, "corrupted archive");
  await assert.rejects(builder().bundleRuntime({...f.options, archive: undefined, download: () => assert.fail("bad cache must fail closed")}), /SHA-256/);
  assert.equal(fs.existsSync(cached), false);
  assert.equal(fs.existsSync(f.options.output), false);
});
