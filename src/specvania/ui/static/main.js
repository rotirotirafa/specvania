import { t, onLanguageChange, i18n, bindDocument, connectLanguage } from "./i18n/index.js";
// T14 transport; subsequent Map rooms project these state/event notifications.
import { UI_COLORS } from "./pixel/palette.js";
import { mountApproval } from "./hud/approval.js";

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
  mountApproval(socket);
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
