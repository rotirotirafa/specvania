import { approvalResult } from "./dialogue.js";
import { mountApproval } from "./approval.js";
import { pauseReason } from "../screens/pause.js";
import { mountBoss } from "../screens/boss.js";
import { mountEnd } from "../screens/end.js";
import { t, onLanguageChange } from "../i18n/index.js";
import { deriveStep } from "../play/step.js";
import { partyFromState, projectParty, partyView, mountParty } from "./party.js";
import { mountBestiary } from "./bestiary.js";
import { portrait } from "./dialogue-box.js";
import { UI_COLORS, PALETTE } from "../pixel/palette.js";

/** Chips occupy their own top strip. Optional actions appear only with a handler. */
export function mountChips(socket, {browser = window, host = browser.document.body, handlers = {}} = {}) {
  const document = browser.document, bar = document.createElement("nav"); bar.id = "play-chips";
  Object.assign(bar.style, {position:"fixed", top:"36px", left:"12px", right:"auto", height:"32px", width:"max-content", display:"flex",
    alignItems:"center", gap:"8px", zIndex:"7", fontFamily:'"Press Start 2P", monospace', fontSize:"8px"});
  const step = document.createElement("span"), partyChip = document.createElement("button"), right = document.createElement("span");
  partyChip.type = "button"; partyChip.id = "party-chip"; right.style.marginLeft = "auto";
  const style = node => Object.assign(node.style, {font:"inherit", color:UI_COLORS.text, backgroundColor:UI_COLORS.surface,
    border:`2px solid ${UI_COLORS.border}`, padding:"4px", minWidth:"0"});
  style(step); style(partyChip); style(right);
  Object.assign(right.style, {position:"fixed", right:"12px", top:"36px"});
  bar.append(step, partyChip, right); host.append(bar);
  const drawer = document.createElement("aside"); drawer.id = "party-drawer"; drawer.hidden = true;
  drawer.setAttribute("aria-label", t("party.title"));
  Object.assign(drawer.style, {position:"fixed", zIndex:"6", top:"88px", right:"12px", width:"280px", bottom:"220px",
    overflow:"auto", padding:"8px", boxSizing:"border-box", backgroundColor:UI_COLORS.surface, border:`2px solid ${UI_COLORS.border}`});
  host.append(drawer);
  const mounts = [mountParty(drawer, browser), mountBestiary(drawer, browser)];
  const hud = document.getElementById?.("castle-hud");
  const placement = hud ? {parent:hud.parentNode || hud.parent, next:hud.nextSibling} : null;
  if (hud) {drawer.append(hud); Object.assign(hud.style, {position:"static", width:"auto", padding:"0", maxHeight:"none", border:"0", background:"transparent"});}
  let studio = {}, state = {}, events = [], party = partyFromState({}), replay = null;
  let bottom = Number(document.getElementById?.("dialogue-box")?.dataset.height || 104) + 24, lastInsets = "";
  function layout() {
    const width = browser.innerWidth || 960;
    const playback = document.getElementById?.("replay-bar");
    const replayWidth = playback?.getBoundingClientRect?.().width || 710;
    const chipWidth = bar.getBoundingClientRect?.().width || Math.max(280, step.textContent.length * 8 + 112);
    const together = replay && replayWidth + 8 + chipWidth <= width - 24;
    const top = replay && !together ? 72 : 36;
    bar.style.top = `${top}px`; bar.style.left = `${together ? 12 + replayWidth + 8 : 12}px`;
    const inset = top + 32 + 8;
    bar.dataset.bottom = String(inset);
    drawer.style.top = `${inset}px`; drawer.style.bottom = `${bottom}px`;
    const detail = {top:inset, bottom};
    document.documentElement?.style?.setProperty?.("--castle-top", `${inset}px`);
    document.documentElement?.style?.setProperty?.("--castle-bottom", `${bottom}px`);
    if (lastInsets === JSON.stringify(detail)) return;
    lastInsets = JSON.stringify(detail);
    browser.dispatchEvent?.(new (browser.CustomEvent || globalThis.CustomEvent)("specvania:castle-insets", {detail}));
  }
  const dialogueSize = ({detail}) => {bottom = detail.bottom; layout();};
  browser.addEventListener("specvania:dialogue-size", dialogueSize);
  browser.addEventListener("specvania:replay-layout", layout);
  browser.addEventListener("resize", layout);
  const sizeObserver = browser.ResizeObserver ? new browser.ResizeObserver(layout) : null;
  sizeObserver?.observe(bar);
  function toggle(force) {drawer.hidden = force === undefined ? !drawer.hidden : !force; partyChip.setAttribute("aria-expanded", String(!drawer.hidden)); if (drawer.hidden) partyChip.focus();}
  partyChip.addEventListener("click", () => toggle());
  partyChip.addEventListener("keydown", event => {if (["Enter", " "].includes(event.key)) {event.preventDefault(); toggle();}});
  const screen = () => {const node = document.getElementById?.("screen-host"); return node && !node.hidden ? node.dataset.screen : "castle";};
  function render() {
    const current = screen(), projection = deriveStep({studio, state, events, screen:!replay && (studio.phase === "finished" || ["finished", "aborted"].includes(state.status)) ? "end" : current});
    step.textContent = t("box.step", {number:projection.number, total:projection.total, step:t(projection.labelKey)}) + (projection.roomId ? t("box.room", {room:projection.roomId}) : "");
    partyChip.hidden = !["castle", "idea"].includes(current) || !["running", "finished", "replaying"].includes(studio.phase) && current !== "castle";
    bar.hidden = !["castle", "project", "idea"].includes(current);

    partyChip.replaceChildren(); partyChip.setAttribute("aria-label", t("party.title"));
    const view = partyView(party), hearts = document.createElement("span");
    for (const member of view.members) {
      const icon = portrait(document, member.role, 1); partyChip.append(icon);
      if (member.status === "waiting for player") {const wait = document.createElement("span"); wait.textContent = t("box.wait"); wait.dataset.role = member.role; wait.style.color = PALETTE.Y; partyChip.append(wait);}
    }
    const full = Math.ceil(view.stamina / 100 * 3); hearts.textContent = t("party.hearts", {full:"♥".repeat(full), empty:"♡".repeat(3 - full)});
    hearts.setAttribute("aria-label", t("party.staminaValue", {value:view.staminaText})); partyChip.append(hearts);
    right.replaceChildren();
    for (const [name, key] of [["help", "box.help"], ["mute", "box.mute"]]) if (typeof handlers[name] === "function") {
      const button = document.createElement("button"); style(button); button.textContent = t(key); button.addEventListener("click", handlers[name]); right.append(button);
    }
    right.hidden = !right.children.length;
    partyChip.setAttribute("aria-expanded", String(!drawer.hidden));
    layout();
  }
  const message = ({data}) => {
    const value = JSON.parse(data);
    if (value.kind === "studio_state") {studio = value; replay = value.replay || null;}
    else if (value.kind === "state") {state = value.state || {}; events = []; party = partyFromState(state); studio = {...studio, ...(value.phase ? value : {}), replay:value.replay || null}; replay = value.replay || null;}
    else if (value.kind === "event") {events.push(value.event); party = projectParty(party, value.event);}
    render();
  };
  const keydown = event => {
    if (event.defaultPrevented || /^(input|textarea|select)$/i.test(event.target?.tagName || "") || event.target?.isContentEditable || event.repeat) return;
    if (event.key === "Escape" && !drawer.hidden) {event.preventDefault(); toggle(false);}

  };
  browser.addEventListener("keydown", keydown); socket.addEventListener("message", message);
  const observer = browser.MutationObserver ? new browser.MutationObserver(render) : null;
  const screenHost = document.getElementById?.("screen-host"); if (screenHost) observer?.observe(screenHost, {attributes:true, attributeFilter:["hidden", "data-screen"]});
  const unsubscribe = onLanguageChange(render); render();
  return {bar, drawer, partyChip, register(name, handler) {handlers = {...handlers, [name]:handler}; render();}, render,
    destroy() {sizeObserver?.disconnect(); browser.removeEventListener("specvania:dialogue-size", dialogueSize); browser.removeEventListener("specvania:replay-layout", layout); browser.removeEventListener("resize", layout); observer?.disconnect(); unsubscribe(); for (const mount of mounts) mount.destroy();
      if (hud && placement?.parent) placement.parent.append(hud);
      bar.remove(); drawer.remove(); browser.removeEventListener("keydown", keydown); socket.removeEventListener?.("message", message);}};
}

/** Existing replay transport controls, wired to the shared narrator box. */
export function mountReplayControls(socket, {browser = window, back = () => {}} = {}) {
  const document = browser.document || globalThis.document;
  let replay = null, pause = null, receipt = null, controlled = false;
  let mounts = [];
  // Forward only the screen capabilities; Window's contextBridge properties
  // are immutable, so a Proxy cannot substitute a read-only desktop capability.
  const viewer = {
    document, performance:browser.performance, ResizeObserver:browser.ResizeObserver,
    get innerWidth() {return browser.innerWidth;}, get innerHeight() {return browser.innerHeight;},
    setInterval:browser.setInterval?.bind(browser), clearInterval:browser.clearInterval?.bind(browser),
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
    mounts = [mountApproval(source, {window: viewer, document}), mountBoss(source, {browser: viewer}), mountEnd(source, {browser: viewer})];
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
  Object.assign(bar.style, {height:"32px", width:"710px", maxWidth:"calc(100vw - 24px)", boxSizing:"border-box", fontSize:"8px", top:"36px", display:"flex", flexWrap:"nowrap", alignItems:"center", gap:"6px", color: UI_COLORS.text, backgroundColor: UI_COLORS.surface, borderColor: UI_COLORS.border});
  Object.assign(summary.style, {maxWidth:"360px", minWidth:"0", flex:"1 1 auto", margin:"0", whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis"});
  for (const node of [...buttons.map(button => button.node), toggle, exit]) Object.assign(node.style, {whiteSpace:"nowrap", flexShrink:"0"});
  const panels = () => Array.from(document.body.children)
    .filter(node => !node.hidden && node.getAttribute("role") === "dialog" && node.dataset.screen);
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
    const top = Math.max(Number(document.getElementById("play-chips")?.dataset.bottom || 76), Math.ceil(bar.getBoundingClientRect?.().bottom || 68) + 8);
    for (const panel of panels()) {
      // Keep expedition panels below both playback controls and chips.
      placements.set(panel, {top: panel.style.top || "", maxHeight: panel.style.maxHeight || "", modal: panel.getAttribute("aria-modal")});
      panel.style.top = `${top}px`;
      panel.setAttribute("aria-modal", "false"); // Playback controls remain part of the watch view.
    }
  }
  function resizeLayout() {
    browser.dispatchEvent?.(new (browser.CustomEvent || globalThis.CustomEvent)("specvania:replay-layout", {detail:{}}));
    layoutDialogs();
  }
  browser.addEventListener("specvania:castle-insets", layoutDialogs);
  const resizeObserver = browser.ResizeObserver ? new browser.ResizeObserver(resizeLayout) : null;
  resizeObserver?.observe(bar);
  browser.addEventListener("resize", resizeLayout);
  function render() {
    bar.setAttribute("aria-label", t("replay.title"));
    bar.title = t("replay.keys");
    summary.textContent = receipt ? receipt() : t(replay?.finished ? "replay.finished" : "replay.watching", {name: replay?.name || ""});
    summary.title = summary.textContent;
    summary.setAttribute("aria-label", summary.textContent);
    for (const {node, speed} of buttons) {
      node.textContent = t("replay.speed", {speed}); node.disabled = !canControl();
      node.setAttribute("aria-pressed", String(replay?.speed === speed));
    }
    toggle.textContent = t(replay?.paused ? "replay.resume" : "replay.pause");
    toggle.disabled = !canControl() || Boolean(replay?.finished);
    exit.textContent = t("replay.back"); exit.disabled = !canControl();
    layoutDialogs();
  }
  function recorded(event) {
    if (event.type === "clarify_question") receipt = null;
    else if (event.type === "clarify_answer") receipt = () => t("replay.answer", {answer:event.answer});
    if (event.type === "run_paused") pause = event;
    else if (pause) {
      const choice = {room_skipped: "skip", attempt_started: "retry", stamina_restored: "rest", boss_started: "retry", run_finished: "abort"}[event.type];
      if (choice) {const reason = pause.reason; receipt = () => t("replay.choice", {choice: t(`pause.${choice}`), reason: pauseReason(reason)}); pause = null;}
    }
    if (event.type === "approval_answered") receipt = () => approvalResult(event);
    if (event.type === "run_started") {receipt = () => t("replay.feature", {feature: event.feature.replaceAll("-", String.fromCharCode(32))});}
    render();
  }
  const message = ({data}) => {
    const value = JSON.parse(data);
    if (value.kind === "state" || value.kind === "studio_state") {
      const next = value.replay || null;
      controlled = value.phase === "replaying";
      if (Boolean(next) !== Boolean(replay)) {
        const wasReplay = Boolean(replay); replay = next; pause = receipt = null;
        mountExpedition();
        if (wasReplay) {
          browser.dispatchEvent(new (browser.CustomEvent || globalThis.CustomEvent)("specvania:state", {detail: {rooms: [], party: [], bugs: [], bosses: []}}));
          back();
        }
      } else replay = next;
      bar.hidden = !replay;
      if (replay?.finished) receipt = null;
      if (replay) {
        browser.dispatchEvent(new (browser.CustomEvent || globalThis.CustomEvent)("specvania:replay-speed", {detail: {speed: replay.speed}}));
        // Reconnect restores a pending or answered recorded dialogue.
        if (value.kind === "state" && replay.dialogue) recorded(replay.dialogue);
        render();
      }
    } else if (replay && value.kind === "event") recorded(value.event);
    if (!replay || value.kind !== "studio_state") forward("message", {data});
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
    else if (event.key === "Escape" && !document.getElementById("command-overlay") && document.getElementById("party-drawer")?.hidden !== false) {event.preventDefault(); send("close_replay");}
  };
  // Capture Tab before the expedition dialog's local focus trap.
  browser.addEventListener("keydown", keydown, true);
  const unsubscribe = onLanguageChange(render);
  return {destroy() {resizeObserver?.disconnect(); browser.removeEventListener("resize", resizeLayout); browser.removeEventListener("specvania:castle-insets", layoutDialogs); restoreLayout();
    for (const mount of mounts) mount.destroy(); bar.remove(); unsubscribe();
    socket.removeEventListener?.("message", message); socket.removeEventListener?.("close", disconnected); browser.removeEventListener("keydown", keydown, true);}};
}
