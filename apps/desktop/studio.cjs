"use strict";

const { readFileSync, mkdirSync, writeFileSync, renameSync } = require("node:fs");
const path = require("node:path");

function shellLaunch({ executable = "specvania", env = process.env, platform = process.platform }) {
  const quoted = "'" + executable.replaceAll("'", "'\\''") + "'";
  return { command: env.SHELL || (platform === "darwin" ? "/bin/zsh" : "/bin/bash"), args: ["-lic", `exec ${quoted} studio`] };
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
/** Local error UI uses the same dictionaries/screens even when Python cannot start.
 * T38 packaging must include this static tree next to the desktop resources.
 */
function errorPage(reasonKey, logPath) {
  const root = path.resolve(__dirname, "../../src/specvania/ui/static");
  const read = file => readFileSync(path.join(root, file), "utf8");
  const nonce = require("node:crypto").randomBytes(16).toString("hex");
  const json = value => JSON.stringify(value).replaceAll("<", "\\u003c");
  const modules = ["i18n/index.js", "pixel/palette.js", "pixel/cast.js", "screens/project.js", "screens/settings.js", "screens/error.js", "screens/title.js"];
  // Flatten only our import/export declarations; no remote module or eval.
  const code = modules.map(file => (file === "pixel/cast.js" ? read(file).replace(/export const SHEET_URL = .*;/, `const SHEET_URL = ${json("data:image/png;base64," + readFileSync(path.join(root, "vendor/kenney-tiny-dungeon/tilemap_packed.png")).toString("base64"))};`) : read(file)).replace(/^import .*;$/gm, "").replace(/^export /gm, "")).join("\n");
  const reason = JSON.parse(read("i18n/en.json"))[reasonKey] || reasonKey;
  const escape = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  const palette = read("pixel/palette.js").match(/"K": "(#[a-f0-9]+)"/)[1];
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline'; font-src data:; img-src data:"><title>specvania</title><style>@font-face {font-family: "Press Start 2P"; src: url(data:font/ttf;base64,${readFileSync(path.join(root, "vendor/fonts/PressStart2P-Regular.ttf")).toString("base64")});} html, body {margin:0; min-height:100%; background:${palette};} #desktop-title-bar {position:fixed; inset:0 0 auto; height:32px; background:${palette}; -webkit-app-region:drag;}</style></head><body><header id="desktop-title-bar"></header><section id="screen-host"><p>${escape(reason)}</p><p>${escape(logPath)}</p></section><script nonce="${nonce}">
const en = ${read("i18n/en.json")}; const ptBR = ${read("i18n/pt-BR.json")};
${code}
mountScreens({host: document.getElementById("screen-host"), browser: window, startup: ${json({reasonKey, logPath})}});
</script></body></html>`;
}
function launchStudio({ spawn, env = process.env, platform = process.platform, executable = "specvania", timer = globalThis, logPath, log, open, error }) {
  const launch = shellLaunch({ executable, env, platform });
  const lifecycle = { child: null, handshake: null, exited: false, quitting: false };
  let pending = "", receivedBytes = 0, settled = false, timeout;
  const fail = (reason) => {
    if (settled) return;
    settled = true; timer.clearTimeout(timeout);
    error(errorPage(reason, logPath), {kind: "failed", reasonKey: reason, logPath});
    if (!lifecycle.exited) lifecycle.child?.kill();
  };
  try {
    lifecycle.child = spawn(launch.command, launch.args, { env: { ...env }, stdio: ["ignore", "pipe", "pipe"] });
  } catch { fail("error.startFailed"); return lifecycle; }
  const child = lifecycle.child;
  timeout = timer.setTimeout(() => fail("error.startTimeout"), 20000);
  child.stderr.on("data", log);
  child.on("error", () => fail("error.startFailed"));
  child.on("exit", () => {
    lifecycle.exited = true;
    if (lifecycle.quitting) return;
    if (lifecycle.handshake) error(errorPage("error.stopped", logPath), {kind: "failed", reasonKey: "error.stopped", logPath});
    else fail("error.startExited");
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
          Object.keys(value).sort().join(",") !== "control_token,port,view_token") {
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
module.exports = { shellLaunch, launchStudio, quitStudio, readSettings, errorPage, connectControl, readAppSettings, writeSettings };
