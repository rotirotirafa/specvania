import { t, i18n, setLanguage, onLanguageChange } from "../i18n/index.js";
import { UI_COLORS, PALETTE } from "../pixel/palette.js";
import { CREDIT, CAST, TILE_SIZE, SHEET_URL } from "../pixel/cast.js";
import { ideaScreen } from "./idea.js";
import { projectScreen } from "./project.js";
import { settingsScreen } from "./settings.js";
import { errorScreen } from "./error.js";

/** One viewer-owned screen controller. T34 may call startRun; T37 supplies watchDemo. */
export function mountScreens({host, browser = window, socket, startup, watchDemo = null}) {
  const document = browser.document;
  const desktop = browser.specvaniaDesktop;
  let screen = "title", project = {}, lastError = {}, labels = [], view = {}, destroyed = false;
  let settings = {language: i18n.language, tokenBudget: 1000000, specvaniaPath: "specvania", reducedMotion: false};
  let pending = Promise.resolve(), syncing = false, studio = false;
  const css = document.createElement("style");
  css.textContent = `#screen-host { position: fixed; inset: 32px 0 0; z-index: 3; overflow: auto; box-sizing: border-box; background: ${UI_COLORS.background}; color: ${UI_COLORS.text}; padding: 16px 24px; font: 12px/1.8 "Press Start 2P", monospace; display: flex; align-items: center; justify-content: center; }
    #screen-host[hidden] { display: none; }
    #screen-host .screen-panel { width: 720px; max-width: 100%; margin: auto; text-align: center; }
    #screen-host button, #screen-host input, #screen-host select { box-sizing: border-box; font: inherit; color: ${UI_COLORS.text}; background: ${UI_COLORS.surface}; border: 2px solid ${UI_COLORS.border}; padding: 12px; }
    #screen-host button { cursor: pointer; min-height: 48px; margin: 6px; } #screen-host button:disabled { opacity: 0.6; cursor: default; }
    #screen-host :focus-visible { outline: 3px solid ${UI_COLORS.accent}; outline-offset: 2px; }
    #screen-host label { display: grid; grid-template-columns: 1fr 1fr; align-items: center; gap: 16px; text-align: left; margin: 12px 0; }
    #screen-host input, #screen-host select { width: 100%; min-width: 0; } #screen-host input[type=checkbox] { width: 24px; height: 24px; }
    #screen-host h1 { color: ${UI_COLORS.accent}; font-size: 28px; line-height: 1.4; margin: 0 0 16px; }
    #screen-host[data-screen=title] h1 { font-size: 36px; margin-bottom: 8px; }
    #screen-host h2 { font-size: 16px; } #screen-host p { overflow-wrap: anywhere; }
    #screen-host .title-castle { position: relative; margin: 0 auto 12px; }
    #screen-host .title-menu { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    #screen-host .title-menu button { margin: 0; min-height: 52px; }
    #screen-host .title-footer { margin-top: 16px; font-size: 8px; } #screen-host .title-footer p { margin: 4px 0 0; }
    #screen-host .title-footer button { min-height: 32px; padding: 8px 16px; margin: 0 6px; }`;
  document.head.append(css);
  let savingSlug = null, savedSlug = null, revealedSlug = null;
  const send = (kind, fields = {}) => {
    const token = desktop?.controlToken?.();
    if (!token || !socket || socket.readyState !== 1) return false;
    socket.send(JSON.stringify({kind, ...fields, control_token: token}));
    if (kind === "save_world") savingSlug = fields.slug;
    return true;
  };
  const label = (node, key, params = {}) => { labels.push([node, key, params]); node.textContent = t(key, params); };
  let container = host;
  const element = tag => { const node = document.createElement(tag); container.append(node); return node; };
  const underlying = new Map();
  function isolate(active) {
    for (const id of ["world-map", "castle-hud"]) {
      const node = document.getElementById?.(id);
      if (!node) continue;
      if (active) {
        if (!underlying.has(node)) underlying.set(node, [node.getAttribute("inert"), node.getAttribute("aria-hidden")]);
        node.setAttribute("inert", ""); node.setAttribute("aria-hidden", "true");
      }
    }
    if (!active) {
      for (const [node, values] of underlying) for (const [index, key] of ["inert", "aria-hidden"].entries()) {
        if (values[index] === null) node.removeAttribute(key); else node.setAttribute(key, values[index]);
      }
      underlying.clear();
    }
  }
  const controls = () => {
    const walk = node => Array.from(node.children).flatMap(child => [child, ...walk(child)]);
    return walk(host).filter(node => (["button", "input", "select", "textarea"].includes(node.tagName.toLowerCase()) || node.tabIndex === 0) && !node.disabled && !node.hidden);
  };
  function restoreGame() {
    isolate(false);
    const hud = document.getElementById?.("castle-hud");
    if (hud && studio) hud.hidden = false;
  }
  function dialogueLayout(mode) {
    host.dataset.dialogue = mode;
    browser.dispatchEvent(new browser.CustomEvent("specvania:dialogue-layout", {detail: {height: mode === "world" ? 72 : 0}}));
  }
  function close() {host.hidden = true; restoreGame(); if (studio) dialogueLayout("");}
  let unavailable = Boolean(startup);
  const back = () => show(unavailable ? "error" : "title");
  function titleArt() {
    const motif = element("div"); motif.className = "title-castle"; motif.setAttribute("aria-hidden", "true");
    const scale = 3, size = TILE_SIZE * scale;
    motif.style.width = `${11 * size}px`; motif.style.height = `${3 * size}px`;
    const tile = (name, x, y) => {
      const node = document.createElement("span"); node.dataset.cast = name;
      Object.assign(node.style, {position: "absolute", width: `${size}px`, height: `${size}px`,
        left: `${x * size}px`, top: `${y * size}px`, imageRendering: "pixelated",
        backgroundImage: `url("${SHEET_URL}")`, backgroundRepeat: "no-repeat",
        backgroundSize: `${192 * scale}px ${176 * scale}px`,
        backgroundPosition: `${-(CAST[name] % 12) * size}px ${-Math.floor(CAST[name] / 12) * size}px`});
      motif.append(node);
    };
    for (let x = 0; x < 11; x++) {tile("back_wall", x, 0); tile("back_wall", x, 1); tile("floor_stones", x, 2);}
    tile("door_closed", 8, 1); tile("banner", 2, 0);
    for (const [index, role] of ["mage", "warrior", "paladin"].entries()) tile(role, 3 + index * 2, 1);
  }
  const text = (tag, key, params) => { const node = element(tag); label(node, key, params); return node; };
  const button = (key, action, disabled = false) => {
    const node = text("button", key); node.type = "button"; node.disabled = disabled; node.addEventListener("click", action); return node;
  };
  const applySettings = patch => {
    pending = pending.catch(() => {}).then(async () => {
      const next = {...settings, ...patch};
      if (!Number.isSafeInteger(next.tokenBudget) || next.tokenBudget <= 0 || !["en", "pt-BR"].includes(next.language) || typeof next.specvaniaPath !== "string" || !next.specvaniaPath.trim() || next.specvaniaPath.includes("\0")) throw new Error();
      settings = desktop?.settings?.set ? await desktop.settings.set(next) : next;
      syncing = true; setLanguage(settings.language); syncing = false;
      if (studio) send("set_language", {language: settings.language});
      browser.dispatchEvent(new browser.CustomEvent("specvania:settings", {detail: settings}));
      view.render?.();
      return settings;
    });
    return pending;
  };
  const chooseFolder = async () => {
    show("project");
    try { const path = await desktop?.pickFolder?.(); if (path) send("open_project", {path}); }
    catch { show("project", t("project.pickerFailed")); }
  };
  const demo = () => {
    const available = !unavailable && socket?.readyState === 1 && Boolean(desktop?.controlToken?.());
    const node = button("title.demo", () => {
      if (watchDemo) {close(); watchDemo();}
      else if (send("open_replay")) close();
    }, !available);
    return node;
  };
  const ui = {document, t, restoreGame, dialogueLayout, palette: PALETTE, label, element, text, button, send, chooseFolder, demo,
    heading: (key, tag = "h1") => text(tag, key), back: () => button("screen.back", back), show, applySettings,
    restart: desktop?.studio?.restart ? () => desktop.studio.restart() : null,
    startRun: (slug, allowDirty = false) => send("start_run", {slug, allow_dirty: allowDirty, token_budget: settings.tokenBudget}),
    get savedSlug() {return savedSlug;},
    get revealedSlug() {return revealedSlug;},
    hasMap: Boolean(browser.Phaser),
    get canControl() {return Boolean(desktop?.controlToken?.());},
    get settings() {return settings;}};
  function render() {
    for (const [node, key, params] of labels) node.textContent = t(key, params);
    for (const node of controls()) if (node.title) node.title = t("title.demoPending");
    view.render?.();
  }
  function show(next, reason = "") {
    if (destroyed || !studio && !startup || next === "idea" && !ui.canControl) return;
    view.destroy?.();
    screen = next; labels = []; host.hidden = false; host.replaceChildren(); isolate(true);
    host.dataset.screen = next; container = host;
    container = element("div"); container.className = "screen-panel";
    if (next === "idea") view = ideaScreen(ui, reason ? {...project, world_slug: reason} : project);
    else if (next === "project") view = projectScreen(ui, project, reason);
    else if (next === "settings") view = settingsScreen(ui);
    else if (next === "error") view = errorScreen(ui, lastError);
    else {
      text("h1", "app.name");
      titleArt();
      const panel = container; container = element("nav"); container.className = "title-menu";
      button("title.new", chooseFolder, !desktop?.pickFolder || !socket || socket.readyState !== 1);
      button("title.open", chooseFolder, !desktop?.pickFolder || !socket || socket.readyState !== 1);
      demo();
      button("title.settings", () => show("settings"));
      container = panel; container = element("footer"); container.className = "title-footer";
      for (const [language, key] of [["pt-BR", "language.pt"], ["en", "language.en"]]) {
        if (language === "en") text("span", "language.separator");
        button(key, () => {applySettings({language}).catch(() => showError({reasonKey:"settings.invalid"}));});
      }
      text("p", "title.credit", {credit: CREDIT});
      view = {};
    }
    const first = controls()[0];
    if (!["settings", "idea"].includes(next)) first?.focus();
  }
  function showError(error) {
    if (!studio && !startup) return;
    lastError = error; unavailable = error.kind === "connection" || Boolean(error.reasonKey?.startsWith("error.start")) || error.reasonKey === "error.stopped"; show("error");
  }
  const keydown = event => {
    if (host.hidden) return;
    if (event.key === "Tab") {
      const nodes = controls(); const index = nodes.indexOf(document.activeElement);
      event.preventDefault();
      nodes[(index + (event.shiftKey ? -1 : 1) + nodes.length) % nodes.length]?.focus();
    } else if (event.key === "Escape" && !["title", "idea"].includes(screen)) {event.preventDefault(); back();}
  };
  const message = ({data}) => {
    const value = JSON.parse(data);
    if (value.kind === "studio_state" || value.kind === "state" && value.phase) {
      if (!studio) {studio = true; send("set_language", {language: settings.language}); show("title");}
      if (value.kind === "studio_state") project = value;
      if (["running", "finished", "replaying"].includes(value.phase)) {
        view.destroy?.(); view = {}; labels = []; close(); host.replaceChildren(); screen = "project"; return;
      }
      if (value.phase === "idle") {project = {}; show("title"); return;}
      if (value.kind === "studio_state") {
        if (value.phase === "project_open") savedSlug = revealedSlug = null;
        if (value.phase === "world_ready" && savingSlug === value.world_slug) {savedSlug = savingSlug; savingSlug = null;}
        if (screen === "idea") view.update?.(value);
        else if (desktop?.controlToken?.() && ["planning", "world_ready"].includes(value.phase)) show("idea");
        else if (desktop?.controlToken?.() && value.project?.initialized && value.error) show("idea");
        else if (value.error) showError({reason: value.error});
        else if (screen === "project" || screen === "title" && value.project) show("project");
      }
    } else if (value.kind === "probe_result" && screen === "project") view.probe?.(value.results);
    else if (value.kind === "approval_rejected") {
      savingSlug = null;
      if (screen === "idea" && !host.hidden) view.reject?.(value);
      else if (value.reason.includes("API key missing")) showError({kind:"missingKey"});
      else if (screen === "project") show("project", value.reason.includes("git work tree") ? t("project.outsideGit") : value.reason);
    }
  };
  const revealed = ({detail}) => {revealedSlug = detail.slug; view.previewReady?.();};
  browser.addEventListener("specvania:world-revealed", revealed);
  const opened = () => {if (screen === "title") show("title");};
  const closed = () => showError({kind:"connection"});
  host.addEventListener("keydown", keydown);
  socket?.addEventListener("open", opened);
  socket?.addEventListener("message", message); socket?.addEventListener("close", closed); socket?.addEventListener("error", closed);
  const unsubscribe = onLanguageChange(({language, manual}) => {
    if (!syncing) settings = {...settings, language};
    render();
    // HUD language buttons already use connectLanguage for studio control.
    if (manual && desktop?.settings?.set) desktop.settings.set({language}).catch(() => {});
  });
  // Replay and live watch views stay interactive until the server identifies a studio.
  close();
  if (startup) showError(startup);
  const ready = Promise.resolve(desktop?.settings?.get?.()).then(saved => {
    if (destroyed) return;
    if (saved) {settings = {...settings, ...saved}; syncing = true; setLanguage(settings.language); syncing = false;}
    browser.dispatchEvent(new browser.CustomEvent("specvania:settings", {detail: settings}));
    if (startup) showError(startup);
    else if (screen === "title") show("title");
  }).catch(() => showError({reasonKey:"settings.invalid"}));
  return { ready, showError, close, showTitle() {project = {}; show("title");}, startRun(slug, allowDirty = false) {return send("start_run", {slug, allow_dirty: allowDirty, token_budget: settings.tokenBudget});},
    destroy() {view.destroy?.(); close(); destroyed = true; browser.removeEventListener("specvania:world-revealed", revealed); unsubscribe(); css.remove(); host.removeEventListener("keydown", keydown);
      socket?.removeEventListener("open", opened);
      socket?.removeEventListener("message", message); socket?.removeEventListener("close", closed); socket?.removeEventListener("error", closed);},
  };
}
