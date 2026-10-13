"use strict";

const { readFileSync } = require("node:fs");
const path = require("node:path");

function shellLaunch({ executable = "specvania", env = process.env, platform = process.platform }) {
  const quoted = "'" + executable.replaceAll("'", "'\\''") + "'";
  return { command: env.SHELL || (platform === "darwin" ? "/bin/zsh" : "/bin/bash"), args: ["-lic", `exec ${quoted} studio`] };
}
function readSettings(userData) {
  try {
    const settings = JSON.parse(readFileSync(path.join(userData, "settings.json"), "utf8"));
    return typeof settings.specvaniaPath === "string" && settings.specvaniaPath.trim() && !settings.specvaniaPath.includes("\0")
      ? settings.specvaniaPath : "specvania";
  } catch { return "specvania"; }
}
function errorPage(reason, logPath) {
  const escape = (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  return `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>specvania</title><h1>Cannot start studio</h1><p>${escape(reason)}</p><p>Log: ${escape(logPath)}</p>`;
}
function launchStudio({ spawn, env = process.env, platform = process.platform, executable = "specvania", timer = globalThis, logPath, log, open, error }) {
  const launch = shellLaunch({ executable, env, platform });
  const lifecycle = { child: null, handshake: null, exited: false, quitting: false };
  let pending = "", receivedBytes = 0, settled = false, timeout;
  const fail = (reason) => {
    if (settled) return;
    settled = true; timer.clearTimeout(timeout);
    error(errorPage(reason, logPath));
    if (!lifecycle.exited) lifecycle.child?.kill();
  };
  try {
    lifecycle.child = spawn(launch.command, launch.args, { env: { ...env }, stdio: ["ignore", "pipe", "pipe"] });
  } catch { fail("Studio could not be started."); return lifecycle; }
  const child = lifecycle.child;
  timeout = timer.setTimeout(() => fail("Studio did not respond within 20 seconds."), 20000);
  child.stderr.on("data", log);
  child.on("error", () => fail("Studio could not be started."));
  child.on("exit", () => {
    lifecycle.exited = true;
    if (lifecycle.quitting) return;
    if (lifecycle.handshake) error(errorPage("Studio stopped.", logPath));
    else fail("Studio exited before the handshake.");
  });
  child.stdout.on("data", (data) => {
    if (settled) return;
    receivedBytes += Buffer.byteLength(data);
    if (receivedBytes > 16384) { fail("Studio sent an invalid handshake."); return; }
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
        fail("Studio sent an invalid handshake."); return;
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
module.exports = { shellLaunch, launchStudio, quitStudio, readSettings, errorPage, connectControl };
