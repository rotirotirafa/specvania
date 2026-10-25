"use strict";

const { readFileSync, mkdirSync, writeFileSync, renameSync, existsSync } = require("node:fs");
const path = require("node:path");
const HANDSHAKE_TIMEOUT_MS = 60000;

/** Ciphertext has its own file; status never decrypts or returns credentials. */
function createApiKeyStore({userData, safeStorage, fs = require("node:fs")}) {
  const file = path.join(userData, "api-key.enc");
  const encryption = () => Boolean(safeStorage?.isEncryptionAvailable()) && safeStorage.getSelectedStorageBackend?.() !== "basic_text";
  const status = () => ({stored: fs.existsSync(file), encryption: encryption()});
  return {
    status,
    set(value) {
      if (typeof value !== "string") throw new Error("apiKey.invalid");
      value = value.trim();
      if (!/^[\x21-\x7e]{1,512}$/.test(value)) throw new Error("apiKey.invalid");
      if (!encryption()) throw new Error("apiKey.encryptionUnavailable");
      const temporary = file + "." + require("node:crypto").randomBytes(16).toString("hex") + ".tmp";
      try {
        const ciphertext = safeStorage.encryptString(value);
        fs.mkdirSync(userData, {recursive: true, mode: 0o700});
        fs.writeFileSync(temporary, ciphertext, {mode: 0o600, flag: "wx"});
        fs.renameSync(temporary, file);
      } catch { throw new Error("apiKey.storeFailed"); }
      finally { try { fs.unlinkSync(temporary); } catch { /* Renamed or never created. */ } }
    },
    remove() {
      try { fs.unlinkSync(file); }
      catch (error) { if (error.code !== "ENOENT") throw new Error("apiKey.storeFailed"); }
    },
    forSpawn() {
      if (!fs.existsSync(file) || !encryption()) return "";
      try { return safeStorage.decryptString(fs.readFileSync(file)); }
      catch { throw new Error("apiKey.readFailed"); }
    },
  };
}

function resolveStudio({ executable = "specvania", resourcesPath = process.resourcesPath, exists = existsSync } = {}) {
  if (executable && executable !== "specvania") return {kind: "custom", executable, args: ["studio"]};
  const python = resourcesPath && path.join(resourcesPath, "runtime/bin/python3");
  if (python && exists(python)) return {kind: "bundled", executable: python, args: ["-I", "-B", "-m", "specvania", "studio"]};
  return {kind: "path", executable: "specvania", args: ["studio"]};
}
function shellLaunch({ env = process.env, platform = process.platform, ...options }) {
  const runtime = resolveStudio(options);
  const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
  // Fixed module flags need no quoting; paths are always one shell argument.
  return { command: env.SHELL || (platform === "darwin" ? "/bin/zsh" : "/bin/bash"), args: ["-lic", `exec ${quote(runtime.executable)} ${runtime.args.join(" ")}`] };
}
function validateSettings(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid settings.");
  const allowed = ["language", "tokenBudget", "specvaniaPath", "reducedMotion"];
  if (Object.keys(value).some(key => !allowed.includes(key))) throw new Error("Invalid settings.");
  if ("language" in value && !["en", "pt-BR"].includes(value.language) ||
      "tokenBudget" in value && (!Number.isSafeInteger(value.tokenBudget) || value.tokenBudget <= 0) ||
      "specvaniaPath" in value && (typeof value.specvaniaPath !== "string" || !value.specvaniaPath.trim() || value.specvaniaPath.length > 4096 || value.specvaniaPath.includes("\0")) ||
      "reducedMotion" in value && typeof value.reducedMotion !== "boolean") throw new Error("Invalid settings.");
  return value;
}
function readAppSettings(userData, language = "en") {
  const defaults = { language, tokenBudget: 1000000, specvaniaPath: "specvania", reducedMotion: false };
  try { return { ...defaults, ...validateSettings(JSON.parse(readFileSync(path.join(userData, "settings.json"), "utf8"))) }; }
  catch { return defaults; }
}
function writeSettings(userData, patch, language = "en") {
  const next = { ...readAppSettings(userData, language), ...validateSettings(patch) };
  mkdirSync(userData, { recursive: true });
  const temporary = path.join(userData, "settings.json.tmp");
  writeFileSync(temporary, JSON.stringify(next), { mode: 0o600 });
  renameSync(temporary, path.join(userData, "settings.json"));
  return next;
}
function readSettings(userData) { return readAppSettings(userData).specvaniaPath; }
function errorPage(reasonKey, logPath, runtime = {kind: "path"}) { return localPage({reasonKey, logPath, runtime}); }
function startingPage() { return localPage({kind: "starting"}); }
/** Local startup UI uses the same dictionaries/screens even when Python cannot start.
 * T38 packages the required static files inside the desktop app.
 */
function localPage(startup) {
  const {reasonKey = "starting.title", logPath = ""} = startup;
  const bundled = path.join(__dirname, "static");
  const root = existsSync(bundled) ? bundled : path.resolve(__dirname, "../../src/specvania/ui/static");
  const read = file => readFileSync(path.join(root, file), "utf8");
  const nonce = require("node:crypto").randomBytes(16).toString("hex");
  const json = value => JSON.stringify(value).replaceAll("<", "\\u003c");
  const modules = ["i18n/index.js", "pixel/palette.js", "pixel/cast.js", "screens/project.js", "screens/settings.js", "screens/error.js", "screens/starting.js", "screens/title.js"];
  // Flatten only our import/export declarations; no remote module or eval.
  const code = modules.map(file => (file === "pixel/cast.js" ? read(file).replace(/export const SHEET_URL = .*;/, `const SHEET_URL = ${json("data:image/png;base64," + readFileSync(path.join(root, "vendor/kenney-tiny-dungeon/tilemap_packed.png")).toString("base64"))};`) : read(file)).replace(/^import .*;$/gm, "").replace(/^export /gm, "")).join("\n");
  const reason = JSON.parse(read("i18n/en.json"))[reasonKey] || reasonKey;
  const escape = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  const palette = read("pixel/palette.js").match(/"K": "(#[a-f0-9]+)"/)[1];
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline'; font-src data:; img-src data:"><title>specvania</title><style>@font-face {font-family: "Press Start 2P"; src: url(data:font/ttf;base64,${readFileSync(path.join(root, "vendor/fonts/PressStart2P-Regular.ttf")).toString("base64")});} html, body {margin:0; min-height:100%; background:${palette};} #desktop-title-bar {position:fixed; inset:0 0 auto; height:32px; background:${palette}; -webkit-app-region:drag;}</style></head><body><header id="desktop-title-bar"></header><section id="screen-host"><p>${escape(reason)}</p><p>${escape(logPath)}</p></section><script nonce="${nonce}">
const en = ${read("i18n/en.json")}; const ptBR = ${read("i18n/pt-BR.json")};
${code}
mountScreens({host: document.getElementById("screen-host"), browser: window, startup: ${json(startup)}});
</script></body></html>`;
}
function launchStudio({ spawn, env = process.env, platform = process.platform, executable = "specvania", resourcesPath = process.resourcesPath, exists = existsSync, timer = globalThis, logPath, log, open, error, apiKeyForSpawn = () => "" }) {
  const runtime = resolveStudio({executable, resourcesPath, exists});
  const launch = shellLaunch({ executable, resourcesPath, exists, env, platform });
  const lifecycle = { child: null, handshake: null, exited: false, quitting: false };
  let pending = "", receivedBytes = 0, settled = false, timeout;
  const fail = (reason) => {
    if (settled) return;
    settled = true; timer.clearTimeout(timeout);
    error(errorPage(reason, logPath, runtime), {kind: "failed", reasonKey: reason, logPath, runtime});
    if (!lifecycle.exited) lifecycle.child?.kill();
  };
  let appKey;
  try { appKey = apiKeyForSpawn(); }
  catch { fail("apiKey.readFailed"); return lifecycle; }
  const childEnv = { ...env };
  if (appKey) childEnv.SPECVANIA_API_KEY = appKey;
  const secrets = [...new Set([appKey, childEnv.SPECVANIA_API_KEY, childEnv.NVIDIA_API_KEY]
    .filter(Boolean).flatMap(value => [value, JSON.stringify(value).slice(1, -1)]))]
    .sort((left, right) => right.length - left.length);
  // Buffer whole lines so a secret split between chunks cannot reach the log.
  // Oversized diagnostics are discarded rather than persisting an unsafe prefix.
  let logLine = "", discardedLine = false;
  const writeLog = () => {
    if (!discardedLine) {
      let safe = logLine;
      for (const secret of secrets) safe = safe.replaceAll(secret, "[redacted]");
      log(safe);
    }
    logLine = ""; discardedLine = false;
  };
  const logData = data => {
    for (const part of data.toString().split(/(?<=\n)/)) {
      logLine += part;
      if (logLine.length > 16384) { discardedLine = true; logLine = ""; }
      if (part.endsWith("\n")) writeLog();
    }
  };
  try {
    lifecycle.child = spawn(launch.command, launch.args, { env: childEnv, stdio: ["ignore", "pipe", "pipe"] });
  } catch { fail("error.startFailed"); return lifecycle; }
  const child = lifecycle.child;
  timeout = timer.setTimeout(() => fail("error.startTimeout"), HANDSHAKE_TIMEOUT_MS);
  // Keep only enough text to detect a split shell diagnostic; never reflect
  // stderr (which may contain unrelated paths) into the error page or status.
  let diagnostic = "", commandMissing = false;
  child.stderr.on("data", data => {
    if (!settled) {
      const text = diagnostic + data.toString();
      commandMissing ||= /command not found/i.test(text);
      diagnostic = text.slice(-32);
    }
    logData(data);
  });
  child.on("error", () => fail("error.startFailed"));
  child.on("exit", code => {
    if (logLine) writeLog();
    lifecycle.exited = true;
    if (lifecycle.quitting) return;
    if (lifecycle.handshake) error(errorPage("error.stopped", logPath, runtime), {kind: "failed", reasonKey: "error.stopped", logPath, runtime});
    else fail(code === 127 || code === 126 || commandMissing ? "error.startCliMissing" : "error.startExited");
  });
  child.stdout.on("data", (data) => {
    if (settled) return;
    receivedBytes += Buffer.byteLength(data);
    if (receivedBytes > 16384) { fail("error.startInvalid"); return; }
    pending += data.toString();
    while (!settled) {
      const end = pending.indexOf("\n");
      if (end === -1) return;
      const line = pending.slice(0, end);
      pending = pending.slice(end + 1);
      let value;
      try { value = JSON.parse(line); } catch { continue; }
      if (!value || !Number.isInteger(value.port) || value.port < 1 || value.port > 65535 ||
          typeof value.view_token !== "string" || !value.view_token.trim() ||
          typeof value.control_token !== "string" || !value.control_token.trim() || value.view_token === value.control_token ||
          Object.keys(value).sort().join(",") !== "control_token,port,view_token" ||
          secrets.some(secret => JSON.stringify(value).includes(secret))) {
        fail("error.startInvalid"); return;
      }
      settled = true; timer.clearTimeout(timeout); pending = "";
      lifecycle.handshake = value;
      const url = new URL(`http://127.0.0.1:${value.port}/`);
      url.searchParams.set("view_token", value.view_token);
      open(url.href, value.control_token);
    }
  });
  return lifecycle;
}
function quitStudio(lifecycle, { connect, timer = globalThis }) {
  if (!lifecycle?.child || lifecycle.exited) return Promise.resolve();
  lifecycle.quitting = true;
  return new Promise((resolve) => {
    let socket;
    const finish = () => { timer.clearTimeout(timeout); socket?.close?.(); resolve(); };
    const timeout = timer.setTimeout(() => { lifecycle.child.kill(); finish(); }, 5000);
    lifecycle.child.once("exit", finish);
    const value = lifecycle.handshake;
    if (!value) return;
    const origin = `http://127.0.0.1:${value.port}`;
    const url = new URL(`/ws`, origin); url.protocol = "ws:"; url.searchParams.set("view_token", value.view_token);
    try {
      socket = connect(url.href, origin);
      socket.on("error", () => {});
      socket.once("open", () => {
        try { socket.send(JSON.stringify({ kind: "shutdown", control_token: value.control_token })); } catch { /* kill deadline still applies */ }
      });
    } catch { /* kill deadline still applies */ }
  });
}
// A one-message WebSocket client using Node's HTTP upgrade and masking. Keeping
// it here avoids a ws dependency and lets main supply the mandatory Origin.
function connectControl(value, origin, request = require("node:http").request) {
  const { EventEmitter } = require("node:events");
  const { randomBytes, createHash } = require("node:crypto");
  const url = new URL(value);
  const key = randomBytes(16).toString("base64");
  const client = new EventEmitter();
  let transport;
  const upgrade = request({ hostname: url.hostname, port: url.port, path: url.pathname + url.search,
    headers: { Origin: origin, Upgrade: "websocket", Connection: "Upgrade", "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": key } });
  client.close = () => { upgrade.destroy(); transport?.destroy(); };
  client.send = (text) => {
    const payload = Buffer.from(text);
    const mask = randomBytes(4);
    const header = Buffer.alloc(payload.length < 126 ? 2 : 4);
    header[0] = 0x81;
    header[1] = 0x80 | (payload.length < 126 ? payload.length : 126);
    if (payload.length >= 126) header.writeUInt16BE(payload.length, 2);
    for (let index = 0; index < payload.length; index++) payload[index] ^= mask[index % 4];
    transport.write(Buffer.concat([header, mask, payload]));
  };
  upgrade.on("error", () => client.emit("error", new Error("Studio shutdown connection failed.")));
  upgrade.on("response", () => { client.close(); client.emit("error", new Error("Studio shutdown connection refused.")); });
  upgrade.on("upgrade", (response, socket) => {
    const accept = createHash("sha1").update(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest("base64");
    if (response.headers["sec-websocket-accept"] !== accept) { socket.destroy(); return; }
    transport = socket;
    socket.on("error", () => client.emit("error", new Error("Studio shutdown connection failed.")));
    client.emit("open");
  });
  upgrade.end();
  return client;
}
module.exports = { createApiKeyStore, resolveStudio, HANDSHAKE_TIMEOUT_MS, shellLaunch, launchStudio, quitStudio, readSettings, errorPage, startingPage, connectControl, readAppSettings, writeSettings };
