import { t, onLanguageChange, i18n, bindDocument, connectLanguage } from "./i18n/index.js";
// T14 transport; subsequent Map rooms project these state/event notifications.
import { UI_COLORS } from "./pixel/palette.js";
import { mountScreens } from "./screens/title.js";
import { mountApproval } from "./hud/approval.js";
import { mountPause } from "./screens/pause.js";
import { mountBoss } from "./screens/boss.js";
import { mountEnd } from "./screens/end.js";

import { mountMap } from "./map/scene.js";
import { mountParty } from "./hud/party.js";
import { mountBestiary } from "./hud/bestiary.js";
import { mountDialogue } from "./hud/dialogue.js";
import { mountRoomPanel } from "./hud/room-panel.js";

// External module boot keeps the game compatible with script-src 'self'.
const hudHost = document.getElementById("castle-hud");
// Keep transport and desktop controls available if the canvas library fails.
if (window.Phaser) {
  for (const [name, color] of Object.entries(UI_COLORS)) {
    document.documentElement.style.setProperty(`--${name}`, color);
  }

  mountParty(hudHost);
  mountMap(window.Phaser, document.getElementById("world-map"));
  mountBestiary(hudHost);
  mountDialogue(hudHost);
  mountRoomPanel(hudHost);
}

i18n.initialize(window);
bindDocument(document);

if (window.specvaniaDesktop) {
  const font = document.createElement("style");
  font.textContent = `@font-face { font-family: SpecvaniaPixel; src: url("./vendor/fonts/PressStart2P-Regular.ttf"); }`;
  document.head.append(font);
  const titleBar = document.createElement("header");
  titleBar.id = "desktop-title-bar";
  titleBar.textContent = t("app.name");
  Object.assign(titleBar.style, {
    webkitAppRegion: "drag",
    userSelect: "none",
    fontFamily: "SpecvaniaPixel, monospace",
    fontSize: "8px",
    padding: "8px",
    color: UI_COLORS.text,
    backgroundColor: UI_COLORS.surface,
    borderBottom: `2px solid ${UI_COLORS.border}`,
  });
  document.body.prepend(titleBar);
}

function connectRun() {
  const viewToken = new URLSearchParams(window.location.search).get("view_token");
  const connection = document.getElementById("connection");
  const runState = document.getElementById("run-state");
  const socketUrl = new URL("/ws", window.location.href);
  socketUrl.protocol = "ws:";
  socketUrl.searchParams.set("view_token", viewToken || "");
  const socket = new WebSocket(socketUrl);
  const screenHost = document.getElementById("screen-host");
  const screens = screenHost ? mountScreens({host: screenHost, browser: window, socket}) : null;
  // T34 mounts its Start expedition button by listening for this controller.
  if (screens) window.dispatchEvent(new CustomEvent("specvania:screens-ready", {detail: screens}));
  mountApproval(socket);
  mountPause(socket);
  mountBoss(socket);
  mountEnd(socket);
  connectLanguage(socket);
  let connectionKey = "connection.connecting", current;
  function render() {
    connection.textContent = t(connectionKey);
    if (!current) return;
    if (current.kind === "studio_state" || current.phase) runState.textContent = t("studio.status", {status: t(`status.${current.phase}`)});
    else if (current.kind === "state") runState.textContent = t("run.status", {status: t(`status.${current.state.status}`)});
    else runState.textContent = t("run.event", {seq: current.event.seq, type: t(`event.${current.event.type}`)});
  }
  onLanguageChange(render);
  render();

  socket.addEventListener("open", () => {
    connectionKey = "connection.watching"; render();
  });
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    current = message.kind === "state" || message.kind === "studio_state" || message.kind === "event" ? message : current;
    if (message.kind === "studio_state") {
      window.dispatchEvent(new CustomEvent("specvania:studio-state", {detail: message}));
    } else if (message.kind === "state") {
      if (message.phase) {
        window.dispatchEvent(new CustomEvent("specvania:studio-state", { detail: message }));
      }
      if (message.state?.run_id) {
        window.dispatchEvent(new CustomEvent("specvania:state", { detail: message.state }));
      }
    } else if (message.kind === "event") {
      const event = message.event;
      window.dispatchEvent(new CustomEvent("specvania:event", { detail: event }));
    }
    render();
  });
  socket.addEventListener("close", () => {
    connectionKey = "connection.closed"; render();
  });
  socket.addEventListener("error", () => {
    connectionKey = "connection.unavailable"; render();
  });
}
connectRun();
