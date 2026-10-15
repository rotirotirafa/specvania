"use strict";

// Build time only. This module is never loaded by the packaged application.
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { createHash } = require("node:crypto");
const { spawnSync } = require("node:child_process");
const { pipeline } = require("node:stream/promises");
const pins = require("../runtime.json");
const appVersion = require("../package.json").version;

class RuntimeBuildError extends Error {}

async function download(url, destination, redirects = 0) {
  if (redirects > 8) throw new RuntimeBuildError("Too many runtime download redirects.");
  if (new URL(url).protocol !== "https:") throw new RuntimeBuildError("Runtime downloads require HTTPS.");
  const response = await new Promise((resolve, reject) => {
    const request = require("node:https").get(url, resolve);
    request.on("error", reject);
    request.setTimeout(120000, () => request.destroy(new RuntimeBuildError("Runtime download timed out.")));
  });
  if ([301, 302, 303, 307, 308].includes(response.statusCode)) {
    response.resume();
    if (!response.headers.location) throw new RuntimeBuildError("Runtime redirect has no location.");
    return download(new URL(response.headers.location, url).href, destination, redirects + 1);
  }
  if (response.statusCode !== 200) {
    response.resume();
    throw new RuntimeBuildError(`Runtime download failed (HTTP ${response.statusCode}).`);
  }
  await pipeline(response, fs.createWriteStream(destination, {flags: "wx"}));
}

async function verify(archive, pin) {
  const hash = createHash("sha256");
  for await (const chunk of fs.createReadStream(archive)) hash.update(chunk);
  if (hash.digest("hex") !== pin.sha256) throw new RuntimeBuildError("Runtime archive SHA-256 differs from the pin.");
}

function run(command, args, env) {
  const result = spawnSync(command, args, {env, encoding: "utf8", maxBuffer: 16 * 1024 * 1024});
  if (result.error || result.status !== 0) {
    throw new RuntimeBuildError(`Runtime build command failed: ${path.basename(command)} ${args.join(" ")}\n${result.stderr || result.error?.message || ""}`);
  }
  return result.stdout.trim();
}

async function bundleRuntime(options) {
  const {target, wheel, requirements, env = process.env} = options;
  const data = options.pins || pins;
  const pin = data.targets[target];
  if (!pin) throw new RuntimeBuildError("Unknown runtime target; use darwin-arm64, darwin-x64 or linux-x64.");
  if (!wheel || !wheel.endsWith(".whl") || !fs.statSync(wheel, {throwIfNoEntry: false})?.isFile()) {
    throw new RuntimeBuildError("--wheel must name the freshly built specvania wheel.");
  }
  if (!requirements || !fs.statSync(requirements, {throwIfNoEntry: false})?.isFile()) {
    throw new RuntimeBuildError("--requirements must name the frozen uv.lock export (without dev dependencies or extras).");
  }
  // A global user cache is outside every checkout; output stays in ignored build/.
  const cache = options.cache || path.join(os.homedir(), ".cache/specvania/runtimes");
  const output = path.resolve(options.output || path.join(__dirname, "../build/runtime", target));
  let archive = options.archive;
  if (!archive) {
    fs.mkdirSync(cache, {recursive: true});
    archive = path.join(cache, pin.file);
    if (fs.existsSync(archive)) {
      try {await verify(archive, pin);} catch (error) {fs.rmSync(archive, {force: true}); throw error;}
    } else {
      const partial = path.join(cache, `${pin.file}.${process.pid}.part`);
      try {
        await (options.download || download)(`https://github.com/astral-sh/python-build-standalone/releases/download/${data.release}/${pin.file.replaceAll("+", "%2B")}`, partial);
        await verify(partial, pin);
        fs.renameSync(partial, archive);
      } catch (error) {fs.rmSync(partial, {force: true}); throw error;}
    }
  }
  await verify(archive, pin);
  fs.mkdirSync(path.dirname(output), {recursive: true});
  const staging = fs.mkdtempSync(path.join(path.dirname(output), ".runtime-"));
  try {
    run("tar", ["-xzf", path.resolve(archive), "-C", staging], env);
    const unpacked = path.join(staging, "python");
    if (!fs.existsSync(path.join(unpacked, "bin/python3")) || !fs.existsSync(path.join(unpacked, "lib"))) {
      throw new RuntimeBuildError("Runtime archive must contain python/bin/python3 and python/lib.");
    }
    fs.rmSync(output, {recursive: true, force: true});
    fs.renameSync(unpacked, output);
    const python = path.join(output, "bin/python3");
    // pip may be absent from an install-only distribution; ensurepip is offline.
    run(python, ["-I", "-m", "ensurepip", "--upgrade"], env);
    run(python, ["-I", "-m", "pip", "install", "--no-deps", "-r", path.resolve(requirements), path.resolve(wheel)], env);
    run(python, ["-I", "-m", "compileall", "-q", output], env);
    const actual = run(python, ["-I", "-B", "-m", "specvania", "--version"], env);
    if (actual !== appVersion) throw new RuntimeBuildError(`Bundled specvania version ${actual} differs from app version ${appVersion}.`);
    return output;
  } catch (error) {
    fs.rmSync(output, {recursive: true, force: true});
    throw error;
  } finally {fs.rmSync(staging, {recursive: true, force: true});}
}

function parseArgs(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 2) {
    const flag = argv[index];
    if (!["--target", "--wheel", "--requirements", "--archive", "--output", "--cache"].includes(flag) || !argv[index + 1] || argv[index + 1].startsWith("--")) {
      throw new RuntimeBuildError("Usage: bundle-runtime.cjs --target darwin-arm64|darwin-x64|linux-x64 --wheel <path> --requirements <frozen export> [--archive <path>] [--output <path>] [--cache <path>]");
    }
    options[flag.slice(2)] = argv[index + 1];
  }
  return options;
}

if (require.main === module) {
  Promise.resolve().then(() => bundleRuntime(parseArgs(process.argv.slice(2)))).then(output => {
    process.stdout.write(`Bundled runtime: ${output}\n`);
  }).catch(error => {process.stderr.write(`${error.message}\n`); process.exitCode = 1;});
}
module.exports = {bundleRuntime, RuntimeBuildError};
