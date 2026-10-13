import { t, onLanguageChange } from "../i18n/index.js";
import { UI_COLORS } from "../pixel/palette.js";
import { projectEvent } from "../map/scene.js";
import { worldFromRooms } from "../map/layout.js";

export function languageForPath(path) {
  const extension = path.split(".").at(-1).toLowerCase();
  return ({py: "python", js: "javascript", mjs: "javascript", cjs: "javascript", ts: "typescript", tsx: "typescript",
    json: "json", md: "markdown", markdown: "markdown", toml: "ini", ini: "ini", yml: "yaml", yaml: "yaml",
    html: "html", htm: "html", css: "css", sh: "shell", bash: "shell", zsh: "shell"})[extension] || "plaintext";
}

/** RoomCode carries unified patches for commit/working rooms. Preserve both
 * sides and mark omitted context; never present patch excerpts as full files. */
export function diffSides(diff, translate = t) {
  const original = [], modified = [];
  let hunk = false, oldEnd = 1, newEnd = 1;
  for (const line of (diff || "").split("\n")) {
    const header = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(line);
    if (header) {
      if (+header[1] > oldEnd) original.push(translate("panel.omitted"));
      if (+header[3] > newEnd) modified.push(translate("panel.omitted"));
      oldEnd = +header[1] + +(header[2] ?? 1);
      newEnd = +header[3] + +(header[4] ?? 1);
      hunk = true;
    } else if (hunk && line.startsWith("-")) original.push(line.slice(1));
    else if (hunk && line.startsWith("+")) modified.push(line.slice(1));
    else if (hunk && line.startsWith(" ")) { original.push(line.slice(1)); modified.push(line.slice(1)); }
    else if (line.startsWith("diff --git")) hunk = false;
  }
  return { original: original.join("\n"), modified: modified.join("\n") };
}

/** One closure owns AMD loading; all asset/worker URLs resolve on this origin.
 * No service contributions or workers are included in the trimmed entry point. */
export function createMonacoLoader(browser = window, document = browser.document) {
  let loading;
  return function load() {
    if (loading) return loading;
    loading = new Promise((resolve, reject) => {
      const environment = {
        getWorkerUrl: () => new URL("/vendor/monaco/worker.js", browser.location.href).href,
        // 0.57 wraps getWorkerUrl in a blob module unless getWorker is supplied.
        // Wait for the local wrapper's handshake before Monaco sends requests.
        getWorker: () => new Promise((resolveWorker, rejectWorker) => {
          const worker = new browser.Worker(environment.getWorkerUrl());
          worker.onerror = rejectWorker;
          worker.onmessage = event => {
            if (event.data.type === "vscode-worker-ready") {
              worker.onmessage = null;
              resolveWorker(worker);
            }
          };
        }),
      };
      browser.MonacoEnvironment = environment;
      const start = () => {
        browser.require.config({ paths: { vs: "/vendor/monaco/vs" }, "vs/nls": { availableLanguages: { "*": "en" } } });
        browser.require(["vs/editor/editor.main"], () => resolve(browser.monaco), reject);
      };
      if (browser.require?.config) start();
      else {
        const script = document.createElement("script");
        script.src = new URL("/vendor/monaco/vs/loader.js", browser.location.href).href;
        script.onload = start;
        script.onerror = reject;
        document.head.append(script);
      }
    }).catch(error => { loading = undefined; throw error; });
    return loading;
  };
}

export function mountRoomPanel(host, browser = window, { loadMonaco, fetchCode } = {}) {
  const document = browser.document;
  function styleControl(node) {
    Object.assign(node.style, { font: "inherit", color: UI_COLORS.text, backgroundColor: UI_COLORS.surface,
      border: `2px solid ${UI_COLORS.border}`, padding: "6px", cursor: "pointer" });
    return node;
  }
  const load = loadMonaco || createMonacoLoader(browser, document);
  const fetchRoom = fetchCode || (id => browser.fetch(`/api/rooms/${encodeURIComponent(id)}/code`, { credentials: "same-origin" }));
  const panel = document.createElement("section");
  panel.id = "room-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  Object.assign(panel.style, { position: "fixed", top: "0", right: "0", width: "60%", height: "100%", zIndex: "10",
    background: UI_COLORS.background, color: UI_COLORS.text, borderLeft: `4px solid ${UI_COLORS.border}`,
    boxSizing: "border-box", padding: "12px", display: "flex", flexDirection: "column", transform: "translateX(100%)",
    visibility: "hidden" });
  const heading = document.createElement("h2"), closeButton = document.createElement("button"), tabs = document.createElement("nav"), content = document.createElement("div");
  heading.id = "room-panel-title";
  panel.setAttribute("aria-labelledby", heading.id);
  content.id = "room-panel-content";
  content.setAttribute("role", "tabpanel");
  content.tabIndex = 0;
  Object.assign(content.style, { overflow: "auto", flex: "1", minHeight: "0" });
  styleControl(closeButton);
  Object.assign(tabs.style, { display: "flex", gap: "4px", flexWrap: "wrap", margin: "8px 0" });
  panel.append(heading, closeButton, tabs, content);
  document.body.append(panel);
  const chooser = document.createElement("details"), chooserLabel = document.createElement("summary"), roomButtons = document.createElement("div");
  chooser.append(chooserLabel, roomButtons);
  host.append(chooser);
  tabs.setAttribute("role", "tablist");
  let state = { rooms: [] }, selected, data, errorReason, tab = "mission", fileIndex = 0, generation = 0, editorGeneration = 0, previousFocus;
  let editor, models = [], requestPending = false;
  const reviews = new Map(), gates = new Map();
  const buttons = new Map();
  for (const name of ["mission", "code", "diff", "review"]) {
    const button = styleControl(document.createElement("button"));
    button.id = `room-panel-${name}`;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", content.id);
    button.addEventListener("click", () => selectTab(name));
    button.addEventListener("keydown", event => {
      const names = [...buttons.keys()], index = names.indexOf(name);
      let next;
      if (event.key === "ArrowRight") next = names[(index + 1) % names.length];
      if (event.key === "ArrowLeft") next = names[(index + names.length - 1) % names.length];
      if (event.key === "Home") next = names[0];
      if (event.key === "End") next = names.at(-1);
      if (next) { event.preventDefault(); selectTab(next); buttons.get(next).focus(); }
    });
    tabs.append(button); buttons.set(name, button);
  }
  function clearEditor() {
    editorGeneration++;
    editor?.dispose(); editor = undefined;
    for (const model of models) model.dispose();
    models = [];
  }
  function text(tag, value) {
    const node = document.createElement(tag); node.textContent = value; content.append(node); return node;
  }
  function selectTab(name) { tab = name; render(); }
  function room() { return state.rooms?.find(room => room.id === selected); }
  function renderLabels() {
    chooserLabel.textContent = t("panel.choose");
    closeButton.textContent = t("panel.close");
    panel.style.transition = browser.matchMedia?.("(prefers-reduced-motion: reduce)").matches || browser.specvaniaDesktop?.settings?.reducedMotion ? "none" : "transform 160ms";
    for (const [name, button] of buttons) {
      button.textContent = t(`panel.${name}`);
      button.setAttribute("aria-selected", String(name === tab));
      button.tabIndex = name === tab ? 0 : -1;
      button.style.borderColor = name === tab ? UI_COLORS.accent : UI_COLORS.border;
    }
    heading.textContent = t("panel.title", { id: selected || "", title: room()?.title || "" });
    content.setAttribute("aria-labelledby", buttons.get(tab).id);
  }
  function renderRooms() {
    roomButtons.replaceChildren();
    for (const room of state.rooms || []) {
      const button = styleControl(document.createElement("button"));
      button.textContent = t("panel.room", { id: room.id, title: room.title, state: t(`room.${room.state}`) });
      button.addEventListener("click", () => open(room.id));
      roomButtons.append(button);
    }
  }
  async function renderEditor(file, container, diff) {
    const version = editorGeneration;
    try {
      const monaco = await load();
      if (version !== editorGeneration || !selected) return;
      monaco.editor.defineTheme("specvania", { base: "vs-dark", inherit: true, rules: [], colors: {
        "editor.background": UI_COLORS.background, "editor.foreground": UI_COLORS.text,
        "editorLineNumber.foreground": UI_COLORS.muted, "editor.selectionBackground": UI_COLORS.surface,
        "diffEditor.insertedLineBackground": UI_COLORS.surface, "diffEditor.removedLineBackground": UI_COLORS.surface,
      } });
      const sides = diffSides(file.diff);
      const language = languageForPath(file.path);
      const options = { readOnly: true, domReadOnly: true, automaticLayout: true, theme: "specvania", contextmenu: false,
        minimap: { enabled: false }, quickSuggestions: false, suggestOnTriggerCharacters: false,
        wordBasedSuggestions: "off", parameterHints: { enabled: false }, accessibilitySupport: "on",
        readOnlyMessage: { value: t("panel.readOnly") } };
      if (diff) {
        models = [monaco.editor.createModel(sides.original, language), monaco.editor.createModel(sides.modified, language)];
        editor = monaco.editor.createDiffEditor(container, { ...options, originalEditable: false, renderSideBySide: true, useInlineViewWhenSpaceIsLimited: false });
        editor.setModel({ original: models[0], modified: models[1] });
      } else {
        models = [monaco.editor.createModel(file.content ?? sides.modified, language)];
        editor = monaco.editor.create(container, { ...options, model: models[0] });
      }
    } catch {
      if (version === editorGeneration && selected) text("p", t("panel.editorError"));
    }
  }
  function render() {
    renderLabels();
    if (!selected) return;
    clearEditor(); content.replaceChildren();
    text("p", t("panel.state", { state: t(`room.${room()?.state || "locked"}`) }));
    if (errorReason) { text("p", t("panel.unavailable", { reason: friendlyReason(errorReason) })); if (tab === "code" || tab === "diff") return; }
    if (data?.truncated) text("p", t("panel.truncated"));
    const review = reviews.get(selected) || data?.review;
    if (tab === "mission") {
      const items = review?.checklist || data?.checklist || room()?.checklist || [];
      if (!items.length) text("p", t(requestPending ? "panel.loading" : "panel.noChecklist"));
      for (const item of items) text("p", t("panel.item", { state: t(item.ok === true ? "panel.ok" : item.ok === false ? "panel.failed" : "panel.pending"), item: item.item || item.text, note: item.note || "" }));
    } else if (tab === "review") {
      text("h3", t("panel.paladin"));
      text("p", review ? t("panel.verdict", { verdict: t(`panel.${review.verdict}`) }) : t("panel.noReview"));
      if (review) text("p", review.feedback);
      else if (room()?.feedback) text("p", room().feedback);
      const results = gates.get(selected) || data?.gates || [];
      if (!results.length) text("p", t("panel.noGates"));
      for (const gate of results) text("pre", t("panel.gate", { command: gate.command, code: gate.exit_code, output: gate.output_summary }));
    } else {
      if (!data) { text("p", t("panel.loading")); return; }
      if (!data.files?.length) { text("p", t("panel.noFiles")); return; }
      const select = styleControl(document.createElement("select"));
      select.style.width = "100%";
      select.setAttribute("aria-label", t("panel.files"));
      for (const [index, file] of data.files.entries()) {
        const option = document.createElement("option"); option.value = String(index); option.textContent = file.path; select.append(option);
      }
      fileIndex = Math.min(fileIndex, data.files.length - 1);
      select.value = String(fileIndex);
      select.addEventListener("change", () => { fileIndex = Number(select.value); render(); content.querySelector?.("select")?.focus(); });
      content.append(select);
      const file = data.files[fileIndex];
      const isDiff = tab === "diff" && data.kind !== "files" && file.diff != null;
      if (tab === "diff" && data.kind === "files") text("p", t("panel.filesNote"));
      if (file.diff != null) text("p", t("panel.excerpts"));
      if (file.truncated) text("p", t("panel.truncated"));
      if (file.diff != null && !/^@@ /m.test(file.diff)) { text("pre", file.diff); return; }
      const container = document.createElement("div");
      Object.assign(container.style, { height: "65vh", minHeight: "200px" });
      content.append(container);
      renderEditor(file, container, isDiff);
    }
  }
  async function refresh() {
    const version = ++generation, id = selected;
    requestPending = true; render();
    try {
      const response = await fetchRoom(id);
      const result = await response.json();
      if (version !== generation || id !== selected) return;
      if (!response.ok) errorReason = result.detail || "panel.fetchError";
      else { data = result; errorReason = undefined; }
    } catch {
      if (version === generation) errorReason = "panel.fetchError";
    } finally {
      if (version === generation) { requestPending = false; render(); }
    }
  }
  function friendlyReason(reason) {
    if (reason === "Repository unavailable for this view.") return t("panel.noRepository");
    if (reason === "Room unavailable in the current world.") return t("panel.noRoom");
    return reason === "panel.fetchError" ? t(reason) : reason || t("panel.fetchError");
  }
  function open(id) {
    if (!selected) previousFocus = document.activeElement;
    selected = id; data = undefined; errorReason = undefined; fileIndex = 0; tab = "mission";
    panel.style.visibility = "visible"; panel.style.transform = "translateX(0)";
    render(); closeButton.focus(); return refresh();
  }
  function close() {
    selected = undefined; generation++; clearEditor();
    panel.style.transform = "translateX(100%)"; panel.style.visibility = "hidden";
    const castle = document.getElementById("world-map");
    if (previousFocus && previousFocus !== document.body && previousFocus.isConnected !== false) previousFocus.focus?.();
    else { if (castle) castle.tabIndex = 0; castle?.focus?.(); }
  }
  closeButton.addEventListener("click", close);
  function keydown(event) {
    if (!selected) return;
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); }
    if (event.key === "Tab") {
      const focusable = [...panel.querySelectorAll?.("button, select, [tabindex]") || []].filter(node => node.tabIndex >= 0);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  }
  function selection(event) { open(event.detail.room_id); }
  function stateChanged(event) {
    if (state.run_id !== event.detail.run_id) { reviews.clear(); gates.clear(); }
    state = event.detail; renderRooms();
    if (state.dialogue?.type === "review") reviews.set(state.dialogue.room_id, state.dialogue);
    if (selected) refresh();
  }
  function eventReceived({ detail: event }) {
    if (event.type === "world_loaded" || ["room_entered", "attempt_started", "respawn", "room_cleared", "room_failed", "room_skipped"].includes(event.type)) {
      state = { ...state, ...projectEvent(worldFromRooms(state.rooms || []), event) }; renderRooms();
    }
    if (event.type === "run_started") { reviews.clear(); gates.clear(); }
    if (event.type === "attempt_started") { gates.set(event.room_id, []); reviews.delete(event.room_id); }
    if (event.type === "review") reviews.set(event.room_id, event);
    if (event.type === "gate_result") gates.set(event.room_id, [...(gates.get(event.room_id) || []), event]);
    if (selected && event.room_id === selected) {
      if (["action", "room_cleared", "attempt_started", "room_entered"].includes(event.type)) refresh();
      else render();
    }
  }
  browser.addEventListener("specvania:room-selected", selection);
  browser.addEventListener("specvania:state", stateChanged);
  browser.addEventListener("specvania:event", eventReceived);
  browser.addEventListener("keydown", keydown, true);
  const unsubscribe = onLanguageChange(() => { renderRooms(); render(); });
  renderLabels();
  return { open, close, panel, destroy() {
    close(); unsubscribe(); panel.remove(); chooser.remove();
    browser.removeEventListener("specvania:room-selected", selection);
    browser.removeEventListener("specvania:state", stateChanged);
    browser.removeEventListener("specvania:event", eventReceived);
    browser.removeEventListener("keydown", keydown, true);
  } };
}
