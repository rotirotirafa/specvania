import { t, onLanguageChange } from "../i18n/index.js";
import { UI_COLORS } from "../pixel/palette.js";
import { mageDialogue } from "../hud/dialogue.js";
import { mountApproval } from "../hud/approval.js";
import { mountPause, pauseReason } from "./pause.js";
import { mountBoss } from "./boss.js";
import { mountEnd } from "./end.js";

/** Same expedition projections, with a watch-only shell while replay is active.
 * Recorded dialogs never have a responder or notify the desktop. Playback
 * controls remain outside every modal's inert subtree and focus trap.
 */
export function mountReplay(socket, {browser = window, back = () => {}} = {}) {
  const document = browser.document || globalThis.document;
  let replay = null, pending = null, pause = null, receipt = null, controlled = false;
  let mounts = [], bindings = [], mageMode = "";
  // Forward only the screen capabilities; Window's contextBridge properties
  // are immutable, so a Proxy cannot substitute a read-only desktop capability.
  const viewer = {
    document,
    CustomEvent: browser.CustomEvent || globalThis.CustomEvent,
    addEventListener(...args) {return browser.addEventListener?.(...args);},
    removeEventListener(...args) {return browser.removeEventListener?.(...args);},
    dispatchEvent(event) {return browser.dispatchEvent(event);},
    matchMedia(query) {return browser.matchMedia?.(query);},
    get localStorage() {return browser.localStorage;},
    get specvaniaDesktop() {
      const desktop = browser.specvaniaDesktop;
      if (!replay || !desktop) return desktop;
      return {settings: {get: () => desktop.settings?.get?.()}, notify() {}};
    },
  };
  const listeners = new Map();
  const source = {
    get readyState() {return socket.readyState;},
    get reconnecting() {return socket.reconnecting;},
    send(data) {socket.send(data);},
    addEventListener(kind, callback) {const list = listeners.get(kind) || []; list.push(callback); listeners.set(kind, list);},
    removeEventListener(kind, callback) {listeners.set(kind, (listeners.get(kind) || []).filter(fn => fn !== callback));},
  };
  const forward = (kind, value) => {for (const fn of [...(listeners.get(kind) || [])]) fn(value);};
  const mountExpedition = () => {
    for (const mount of mounts) mount.destroy();
    mounts = [mountApproval(source, {window: viewer, document}), mountPause(source, {browser: viewer}),
      mountBoss(source, {browser: viewer}), mountEnd(source, {browser: viewer})];
  };
  mountExpedition();
  const bar = document.createElement("nav"); bar.id = "replay-bar"; bar.hidden = true;
  const summary = document.createElement("p"); summary.setAttribute("role", "status");
  bar.append(summary);
  const canControl = () => controlled && Boolean(browser.specvaniaDesktop?.controlToken?.()) && socket.readyState === 1;
  const send = (kind, fields = {}) => {
    if (!replay || !canControl()) return false;
    socket.send(JSON.stringify({kind, ...fields, control_token: browser.specvaniaDesktop.controlToken()}));
    return true;
  };
  const buttons = [1, 2, 4].map(speed => {
    const node = document.createElement("button"); node.type = "button";
    node.addEventListener("click", () => send("replay_speed", {speed})); bar.append(node);
    return {node, speed};
  });
  const toggle = document.createElement("button"), exit = document.createElement("button");
  toggle.type = exit.type = "button";
  toggle.addEventListener("click", () => send("replay_pause", {paused: !replay?.paused}));
  exit.addEventListener("click", () => send("close_replay"));
  bar.append(toggle, exit); document.body.append(bar);
  // The bar uses the shared palette even when Phaser is unavailable.
  Object.assign(bar.style, {color: UI_COLORS.text, backgroundColor: UI_COLORS.surface, borderColor: UI_COLORS.border});
  const panels = () => Array.from(document.body.children)
    .filter(node => !node.hidden && node.getAttribute("role") === "dialog" && (node === pending || node.dataset.screen));
  const placements = new Map();
  function restoreLayout() {
    for (const [panel, {top, maxHeight, modal}] of placements) {
      Object.assign(panel.style, {top, maxHeight});
      if (modal == null) panel.removeAttribute("aria-modal"); else panel.setAttribute("aria-modal", modal);
    }
    placements.clear();
  }
  function layoutDialogs() {
    restoreLayout();
    if (!replay || bar.hidden) return;
    const top = Math.ceil(bar.getBoundingClientRect?.().bottom || 0) + 8;
    for (const panel of panels()) {
      // The Mage stays bottom-aligned; the expedition panels keep their
      // scrollable content and bottom actions in the remaining viewport.
      placements.set(panel, {top: panel.style.top || "", maxHeight: panel.style.maxHeight || "", modal: panel.getAttribute("aria-modal")});
      if (panel === pending) panel.style.maxHeight = `${Math.max(0, browser.innerHeight - top - 16)}px`;
      else panel.style.top = `${top}px`;
      panel.setAttribute("aria-modal", "false"); // Playback controls remain part of the watch view.
    }
  }
  const resizeObserver = browser.ResizeObserver ? new browser.ResizeObserver(layoutDialogs) : null;
  resizeObserver?.observe(bar);
  browser.addEventListener("resize", layoutDialogs);
  function readonlyHints() {
    if (!replay) return;
    for (const [id, key] of [["approval-choice", "approval.watch"], ["pause", "pause.watch"]]) {
      const content = document.getElementById(id)?.children[0];
      if (!content) continue;
      // Live watch hints refer to a real player; recorded dialogs have none.
      for (const node of content.children) if (node.textContent === t(key)) node.hidden = true;
      let caption = Array.from(content.children).find(node => node.dataset.replayReadonly);
      if (!caption) {caption = document.createElement("p"); caption.dataset.replayReadonly = true; content.append(caption);}
      caption.textContent = t("replay.readonly");
    }
  }
  function render() {
    bar.setAttribute("aria-label", t("replay.title"));
    bar.title = t("replay.keys");
    summary.textContent = receipt ? receipt() : t(replay?.finished ? "replay.finished" : "replay.watching", {name: replay?.name || ""});
    for (const {node, speed} of buttons) {
      node.textContent = t("replay.speed", {speed}); node.disabled = !canControl();
      node.setAttribute("aria-pressed", String(replay?.speed === speed));
    }
    toggle.textContent = t(replay?.paused ? "replay.resume" : "replay.pause");
    toggle.disabled = !canControl() || Boolean(replay?.finished);
    exit.textContent = t("replay.back"); exit.disabled = !canControl();
    for (const fn of bindings) fn();
    readonlyHints();
    layoutDialogs();
  }
  function closeMage() {pending?.remove(); pending = null; bindings = []; mageMode = "";}
  function mage(event) {
    closeMage();
    const ui = {document, t, label(node, key) {const fn = () => {node.textContent = t(key);}; fn(); bindings.push(fn);},
      element(tag) {const node = document.createElement(tag); document.body.append(node); return node;}};
    pending = mageDialogue(ui); pending.id = "replay-dialogue";
    pending.setAttribute("aria-modal", "false"); pending.tabIndex = 0;
    const content = document.createElement("div"); content.className = "dialogue-content"; content.tabIndex = 0; pending.append(content);
    const text = (key, params) => {
      const node = document.createElement("p"), fn = () => {node.textContent = t(key, params);};
      fn(); bindings.push(fn); content.append(node); return node;
    };
    mageMode = event.type;
    if (event.type === "run_started") {
      text("replay.feature", {feature: event.feature.replaceAll("-", String.fromCharCode(32))});
      text("replay.readonly");
    } else if (event.type === "clarify_question") {
      text("idea.question", {text: event.question});
      for (const option of event.options || []) {
        const node = text("idea.option", {option});
        node.setAttribute("aria-selected", String(option === event.default));
      }
      text("idea.default", {answer: event.default}); text("replay.readonly");
    } else {
      text("replay.answer", {answer: event.answer});
    }
  }
  function recorded(event) {
    if (event.type === "clarify_question") {receipt = null; mage(event);}
    else if (event.type === "clarify_answer") {
      receipt = () => t("replay.answer", {answer: event.answer}); mage(event);
    } else if (pending && (mageMode === "clarify_answer" || event.type === "world_loaded")) closeMage();
    if (event.type === "run_paused") pause = event;
    else if (pause) {
      const choice = {room_skipped: "skip", attempt_started: "retry", stamina_restored: "rest", boss_started: "retry", run_finished: "abort"}[event.type];
      if (choice) {const reason = pause.reason; receipt = () => t("replay.choice", {choice: t(`pause.${choice}`), reason: pauseReason(reason)}); pause = null;}
    }
    if (event.type === "approval_answered") receipt = () => t("approval.result", {answer: t(event.answer ? "approval.approved" : "approval.denied"), responder: t(`responder.${event.responder}`)});
    if (event.type === "run_started") {receipt = () => t("replay.feature", {feature: event.feature.replaceAll("-", String.fromCharCode(32))}); if (!pending) mage(event);}
    render();
  }
  const message = ({data}) => {
    const value = JSON.parse(data);
    if (value.kind === "state" || value.kind === "studio_state") {
      const next = value.replay || null;
      controlled = value.phase === "replaying";
      if (Boolean(next) !== Boolean(replay)) {
        const wasReplay = Boolean(replay); replay = next; closeMage(); pause = receipt = null;
        mountExpedition();
        if (replay?.feature) mage({type: "run_started", feature: replay.feature});
        if (wasReplay) {
          browser.dispatchEvent(new browser.CustomEvent("specvania:state", {detail: {rooms: [], party: [], bugs: [], bosses: []}}));
          back();
        }
      } else replay = next;
      bar.hidden = !replay;
      if (replay?.finished) receipt = null;
      if (replay) {
        browser.dispatchEvent(new browser.CustomEvent("specvania:replay-speed", {detail: {speed: replay.speed}}));
        // Reconnect restores a pending or answered recorded dialogue.
        if (value.kind === "state" && replay.dialogue) recorded(replay.dialogue);
        render();
      }
    } else if (replay && value.kind === "event") recorded(value.event);
    if (!replay || value.kind !== "studio_state") forward("message", {data});
    readonlyHints();
    layoutDialogs();
  };
  // Register before the expedition listeners so readonly mode is set first.
  // mounts are reset on the first replay snapshot, before any replay event.
  socket.addEventListener("message", message);
  const disconnected = () => {forward("close", {}); render();};
  socket.addEventListener("close", disconnected);
  const keydown = event => {
    if (!replay || event.repeat || /^(INPUT|TEXTAREA|SELECT)$/i.test(event.target?.tagName) || event.target?.isContentEditable) return;
    if (event.key === "Tab" && panels().length) {
      const walk = node => Array.from(node.children || []).filter(child => !child.hidden)
        .flatMap(child => [child, ...walk(child)]);
      const controls = [...panels().flatMap(walk), ...walk(bar)].filter(node => !node.disabled &&
        (node.tagName.toLowerCase() === "button" || node.tabIndex === 0));
      if (!controls.length) return;
      event.preventDefault(); event.stopPropagation();
      const index = controls.indexOf(document.activeElement);
      controls[(index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length].focus();
    }
    else if (["1", "2", "4"].includes(event.key)) {event.preventDefault(); send("replay_speed", {speed: Number(event.key)});}
    else if (event.key.toLowerCase() === "p" || event.key.codePointAt(0) === 32 && event.target?.tagName?.toLowerCase() !== "button") {event.preventDefault(); send("replay_pause", {paused: !replay.paused});}
    else if (event.key === "Escape") {event.preventDefault(); send("close_replay");}
  };
  // Capture Tab before the expedition dialog's local focus trap.
  browser.addEventListener("keydown", keydown, true);
  const unsubscribe = onLanguageChange(render);
  return {destroy() {resizeObserver?.disconnect(); browser.removeEventListener("resize", layoutDialogs); restoreLayout();
    for (const mount of mounts) mount.destroy(); closeMage(); bar.remove(); unsubscribe();
    socket.removeEventListener?.("message", message); socket.removeEventListener?.("close", disconnected); browser.removeEventListener("keydown", keydown, true);}};
}
