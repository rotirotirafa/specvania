// T14 transport; subsequent Map rooms project these state/event notifications.
const viewToken = new URLSearchParams(window.location.search).get("view_token");
const connection = document.getElementById("connection");
const runState = document.getElementById("run-state");
const socketUrl = new URL("/ws", window.location.href);
socketUrl.protocol = "ws:";
socketUrl.searchParams.set("view_token", viewToken || "");
const socket = new WebSocket(socketUrl);

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
