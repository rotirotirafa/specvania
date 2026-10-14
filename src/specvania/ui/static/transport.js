/** Close/error reach consumers with reconnecting already set: main shows retry
 * status, title ignores transient drops, approval/pause disable until a fresh
 * state, and replay forwards the drop and refreshes its controls. Language keeps
 * its message listener and accepts the new studio snapshot without echoing it.
 * CLI replay is per-connection playback, so main opts out of retries; studio
 * replay restores its cursor (including finished) and can safely reconnect.
 */
export function connectSocket(url, {WebSocketImpl = WebSocket, timer = globalThis, shouldReconnect = () => true} = {}) {
  const listeners = new Map();
  let socket, timeout = null, delay = 1000, stopped = false, reconnecting = false, lastClose = null;
  const emit = (kind, event) => {for (const fn of [...(listeners.get(kind) || [])]) fn(event);};
  function connect() {
    timeout = null; lastClose = null;
    const current = socket = new WebSocketImpl(url);
    let active = true;
    current.addEventListener("open", event => {
      if (!active || stopped) return;
      delay = 1000; reconnecting = false; emit("open", event);
    });
    current.addEventListener("message", event => {
      if (active && !stopped) emit("message", event);
    });
    current.addEventListener("error", event => {
      if (!active || stopped) return;
      reconnecting = shouldReconnect(); emit("error", event);
    });
    current.addEventListener("close", event => {
      if (!active) return;
      active = false; lastClose = event;
      reconnecting = !stopped && event.code !== 1008 && shouldReconnect();
      if (reconnecting) {
        timeout = timer.setTimeout(connect, delay);
        delay = Math.min(delay * 2, 10000);
      } else stopped = true;
      emit("close", event);
    });
  }
  connect();
  return {
    get readyState() {return socket.readyState;},
    get reconnecting() {return reconnecting;},
    addEventListener(kind, callback) {
      if (!listeners.has(kind)) listeners.set(kind, new Set());
      listeners.get(kind).add(callback);
    },
    removeEventListener(kind, callback) {listeners.get(kind)?.delete(callback);},
    send(data) {socket.send(data);},
    close(code, reason) {
      if (stopped) return;
      stopped = true; reconnecting = false;
      if (timeout !== null) {timer.clearTimeout(timeout); timeout = null;}
      if (lastClose) emit("close", lastClose);
      else socket.close(code, reason);
    },
  };
}
