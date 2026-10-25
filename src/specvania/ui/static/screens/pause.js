import { t, onLanguageChange } from "../i18n/index.js";
import { UI_COLORS } from "../pixel/palette.js";
import { CAST, TILE_SIZE, SHEET_URL } from "../pixel/cast.js";

/** Shared T33-style isolation for expedition dialogs, also in watch views.
 * Each mount owns its listeners and labels. Opening a dialog settles the prior
 * one before saving the castle's focus/attributes, so overlays never nest.
 */
export function expeditionDialog(name, {browser, document = browser.document}) {
  let root = null, previousFocus = null, bindings = [], escape = null;
  const underlying = new Map();
  const signal = active => {
    const EventClass = browser.CustomEvent || globalThis.CustomEvent;
    if (EventClass) browser.dispatchEvent?.(new EventClass("specvania:expedition-dialog", {detail: {name, active}}));
  };
  const nodes = () => {
    const walk = node => Array.from(node.children || []).filter(child => !child.hidden).flatMap(child => [child, ...walk(child)]);
    return root ? walk(root).filter(node => !node.disabled && !node.hidden &&
      (node.tagName.toLowerCase() === "button" || node.tabIndex === 0)) : [];
  };
  function close() {
    if (!root) return;
    root.remove(); root = null; bindings = []; escape = null;
    for (const [node, values] of underlying) for (const [index, key] of ["inert", "aria-hidden"].entries()) {
      if (values[index] == null) node.removeAttribute?.(key); else node.setAttribute(key, values[index]);
    }
    underlying.clear();
    const hud = document.getElementById?.("castle-hud");
    if (hud) hud.hidden = false;
    if (previousFocus && previousFocus !== document.body && previousFocus.isConnected !== false) previousFocus.focus?.();
    else {
      const castle = document.getElementById?.("world-map");
      if (castle) {castle.tabIndex = 0; castle.focus?.();}
    }
    previousFocus = null;
    signal(false);
  }
  const otherDialog = ({detail}) => {if (detail.active !== false && detail.name !== name) close();};
  browser.addEventListener?.("specvania:expedition-dialog", otherDialog);
  const unsubscribe = onLanguageChange(() => {for (const render of bindings) render();});
  const api = {
    get root() {return root;},
    close,
    text(parent, tag, value) {
      const node = document.createElement(tag);
      const render = () => {node.textContent = value();};
      render(); bindings.push(render); parent.append(node); return node;
    },
    portrait(parent, cast) {
      const node = document.createElement("span"), size = TILE_SIZE * 2;
      node.dataset.cast = cast; node.setAttribute("aria-hidden", "true");
      Object.assign(node.style, {display: "block", width: `${size}px`, height: `${size}px`, flexShrink: "0",
        imageRendering: "pixelated", backgroundImage: `url("${SHEET_URL}")`, backgroundRepeat: "no-repeat",
        backgroundSize: "384px 352px", backgroundPosition: `${-(CAST[cast] % 12) * size}px ${-Math.floor(CAST[cast] / 12) * size}px`});
      parent.append(node); return node;
    },
    button(parent, key, action, params = {}) {
      const node = api.text(parent, "button", () => t(key, params)); node.type = "button";
      node.addEventListener("click", () => {if (!node.disabled) action();}); return node;
    },
    disable() {for (const node of nodes()) if (node.tagName.toLowerCase() === "button") node.disabled = true;},
    open(titleKey, build, {onEscape = null, initial = 0} = {}) {
      close();
      signal(true);
      previousFocus = document.activeElement;
      for (const id of ["world-map", "castle-hud", "screen-host"]) {
        const node = document.getElementById?.(id);
        if (!node) continue;
        underlying.set(node, [node.getAttribute?.("inert"), node.getAttribute?.("aria-hidden")]);
        node.setAttribute("inert", ""); node.setAttribute("aria-hidden", "true");
      }
      root = document.createElement("section"); root.id = name; root.className = "expedition-dialog";
      root.dataset.screen = name; root.tabIndex = -1;
      root.setAttribute("role", "dialog"); root.setAttribute("aria-modal", "true");
      const label = () => root?.setAttribute("aria-label", t(titleKey)); label(); bindings.push(label);
      // Inline palette styling also covers transport-only pages without Phaser.
      Object.assign(root.style, {fontFamily: '"Press Start 2P", monospace', fontSize: "10px", lineHeight: "1.8",
        color: UI_COLORS.text, backgroundColor: UI_COLORS.surface, border: `4px solid ${UI_COLORS.border}`});
      const content = document.createElement("div"); content.className = "expedition-content"; content.tabIndex = 0;
      const actions = document.createElement("nav"); actions.className = "expedition-actions";
      root.append(content, actions); build(content, actions, api); escape = onEscape;
      root.addEventListener("keydown", event => {
        const controls = nodes();
        if (event.key === "Escape") {event.preventDefault(); escape?.();}
        else if (["Tab", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
          if (event.key !== "Tab" && (document.activeElement === content || document.activeElement?.tagName.toLowerCase() === "input")) return;
          event.preventDefault();
          const navigable = event.key === "Tab" ? controls : controls.filter(node => node.tagName.toLowerCase() === "button");
          const index = navigable.indexOf(document.activeElement);
          const backward = event.key === "Tab" ? event.shiftKey : ["ArrowLeft", "ArrowUp"].includes(event.key);
          navigable[(index + (backward ? -1 : 1) + navigable.length) % navigable.length]?.focus();
        } else if (event.key === "Enter" && controls.includes(document.activeElement) && document.activeElement.tagName.toLowerCase() === "button") {
          event.preventDefault(); document.activeElement.click();
        }
      });
      document.body.append(root); (nodes().filter(node => node.tagName.toLowerCase() === "button")[initial] || root).focus(); return root;
    },
    destroy() {close(); unsubscribe(); browser.removeEventListener?.("specvania:expedition-dialog", otherDialog);},
  };
  return api;
}

/** Translate engine templates; leave model feedback and unfamiliar reasons intact. */
export function pauseReason(reason) {
  if (reason === "stamina exhausted") return t("pause.stamina");
  if (/^out of lives$/i.test(reason)) return t("pause.lives");
  if (reason === "model chain exhausted") return t("pause.chain");
  if (reason === "boss failed") return t("pause.boss");
  let match = /^Out of lives in (T\d+): ([\s\S]*)$/.exec(reason);
  if (match) return t("pause.livesDetail", {room: match[1], feedback: match[2]});
  match = /^(mage|warrior|paladin) model chain exhausted; retry or change the chain\.$/.exec(reason);
  if (match) return t("pause.chainDetail", {role: t(`role.${match[1]}`)});
  match = /^Boss (A\d+): ([\s\S]*)$/.exec(reason);
  if (match) return t("pause.bossDetail", {area: match[1], feedback: match[2]});
  return t("pause.reason", {reason});
}

/** Pauses are rendered by mountDialogueBox; keep the old mount as wiring compatibility. */
export function mountPause() {return {destroy() {}};}
