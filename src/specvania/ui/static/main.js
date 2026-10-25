import { t, onLanguageChange, i18n, bindDocument, connectLanguage } from "./i18n/index.js";
import { connectSocket } from "./transport.js";
// T14 transport; subsequent Map rooms project these state/event notifications.
import { UI_COLORS } from "./pixel/palette.js";
import { mountScreens } from "./screens/title.js";
import { mountChips, mountReplayControls } from "./hud/chips.js";

import { mountMap } from "./map/scene.js";
import { mountRoomPanel } from "./hud/room-panel.js";

// External module boot keeps the game compatible with script-src 'self'.
const hudHost = document.getElementById("castle-hud");
// Keep transport and desktop controls available if the canvas library fails.
if (window.Phaser) {
  for (const [name, color] of Object.entries(UI_COLORS)) {
    document.documentElement.style.setProperty(`--${name}`, color);
  }

  mountMap(window.Phaser, document.getElementById("world-map"));
  mountRoomPanel(hudHost);
}

i18n.initialize(window);
bindDocument(document);

if (window.specvaniaDesktop) {
  const font = document.createElement("style");
  font.textContent = `@font-face { font-family: SpecvaniaPixel; src: url("./vendor/fonts/PressStart2P-Regular.ttf"); }
    #desktop-title-bar :is(button, a, input, select, textarea, [role=button], [contenteditable=true]) { -webkit-app-region: no-drag; }`;
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
    paddingLeft: window.specvaniaDesktop.platform === "darwin" ? "80px" : "8px",
    boxSizing: "border-box",
    height: "32px",
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
  let replayFromStart = false;
  let replaying = false;
  const appliedSequences = new Map();
  const rememberSequence = value => {
    if (value.run_id && Number.isInteger(value.seq)) {
      appliedSequences.set(value.run_id, Math.max(appliedSequences.get(value.run_id) || 0, value.seq));
    }
  };
  const socket = connectSocket(socketUrl, {shouldReconnect: () => !replayFromStart});
  const screenHost = document.getElementById("screen-host");
  let screens;
  const surface = document.getElementById("world-map") || screenHost || hudHost;
  if (surface) mountReplayControls(socket, {browser:window, back:() => screens?.showTitle()});
  const chips = surface ? mountChips(socket, {browser:window}) : null;
  screens = screenHost ? mountScreens({host: screenHost, browser: window, socket}) : null;
  // T34 mounts its Start expedition button by listening for this controller.
  if (screens) window.dispatchEvent(new CustomEvent("specvania:screens-ready", {detail: screens}));
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
    if (message.kind === "state") replayFromStart = Boolean(message.replay && !message.phase);
    if (message.kind === "state" || message.kind === "studio_state") replaying = Boolean(message.replay);
    current = message.kind === "state" || message.kind === "studio_state" || message.kind === "event" ? message : current;
    if (message.kind === "studio_state") {
      window.dispatchEvent(new CustomEvent("specvania:studio-state", {detail: message}));
    } else if (message.kind === "state") {
      if (message.phase) {
        window.dispatchEvent(new CustomEvent("specvania:studio-state", { detail: message }));
      }
      if (message.state?.run_id || message.replay) {
        const stale = !replaying && message.state.seq < (appliedSequences.get(message.state.run_id) || 0);
        if (!stale) {
          if (!replaying) rememberSequence(message.state);
          window.dispatchEvent(new CustomEvent("specvania:state", { detail: {...message.state, replay: message.replay || null} }));
        }
      }
    } else if (message.kind === "event") {
      const event = message.event;
      if (!replaying) rememberSequence(event);
      window.dispatchEvent(new CustomEvent("specvania:event", { detail: event }));
    }
    chips?.render();
    render();
  });
  const disconnected = () => {
    connectionKey = socket.reconnecting ? "connection.reconnecting" : "connection.closed"; render();
  };
  socket.addEventListener("close", disconnected);
  socket.addEventListener("error", disconnected);
}
connectRun();

if (document.createElement && document.head) {
const playStyle = document.createElement("style");
playStyle.textContent = `#screen-host[data-screen=project], #screen-host[data-screen=idea] {padding-top:64px;}
  #screen-host[data-screen=idea] {right:0;}
  #party-drawer[hidden], #play-chips[hidden], #replay-bar[hidden], #dialogue-box[hidden] {display:none;}
  #party-drawer main {position:static; width:auto;}
  #dialogue-box button:focus-visible, #play-chips button:focus-visible {outline:3px solid ${UI_COLORS.accent};}
  #dialogue-box input {min-width:0; max-width:100%;}
  .expedition-dialog, .expedition-dialog[data-screen=end] {top:var(--castle-top, 76px); bottom:var(--castle-bottom, 128px);}
  .boss-dialog .expedition-content {flex:1 1 auto; min-height:0; overflow:auto;}
  #replay-bar button {margin:0; padding:4px;}
  #replay-bar p {max-width:40vw; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  #replay-bar {max-width:none; border:0; padding:0;}
`;
document.head.append(playStyle);
}
