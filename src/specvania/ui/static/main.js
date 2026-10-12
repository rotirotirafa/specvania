// T14 transport; subsequent Map rooms project these state/event notifications.
import { UI_COLORS } from "./pixel/palette.js";
import { mountApproval } from "./hud/approval.js";

if (window.specvaniaDesktop) {
  const font = document.createElement("style");
  font.textContent = `@font-face { font-family: SpecvaniaPixel; src: url("./vendor/fonts/PressStart2P-Regular.ttf"); }`;
  document.head.append(font);
  const titleBar = document.createElement("header");
  titleBar.id = "desktop-title-bar";
  titleBar.textContent = "specvania";
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

const viewToken = new URLSearchParams(window.location.search).get("view_token");
const connection = document.getElementById("connection");
const runState = document.getElementById("run-state");
const socketUrl = new URL("/ws", window.location.href);
socketUrl.protocol = "ws:";
socketUrl.searchParams.set("view_token", viewToken || "");
const socket = new WebSocket(socketUrl);
mountApproval(socket);

socket.addEventListener("open", () => {
  connection.textContent = "Watching the run";
});
socket.addEventListener("message", ({ data }) => {
  const message = JSON.parse(data);
  if (message.kind === "state") {
    runState.textContent = `Run: ${message.state.status}`;
    window.dispatchEvent(new CustomEvent("specvania:state", { detail: message.state }));
  } else if (message.kind === "event") {
    const event = message.event;
    runState.textContent = `Event ${event.seq}: ${event.type}`;
    window.dispatchEvent(new CustomEvent("specvania:event", { detail: event }));
  }
});
socket.addEventListener("close", () => {
  connection.textContent = "Run connection closed";
});
socket.addEventListener("error", () => {
  connection.textContent = "Run connection unavailable";
});
