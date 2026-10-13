"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { pathToFileURL } = require("node:url");
const root = path.resolve(__dirname, "../../src/specvania/ui/static");
const load = () => import(pathToFileURL(path.join(root, "hud/room-panel.js")));
const settle = async () => { await Promise.resolve(); await Promise.resolve(); };
class Element {
  constructor(tag, document) { Object.assign(this, { tagName: tag, document, style: {}, children: [], attrs: {}, listeners: {}, tabIndex: 0, textContent: "" }); }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); }
  setAttribute(name, value) { this.attrs[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  focus() { this.document.activeElement = this; }
  click() { return this.listeners.click?.({}); }
  querySelectorAll() { return flatten(this).filter(node => ["button", "select"].includes(node.tagName) || node.attrs.role === "tabpanel"); }
  querySelector(tag) { return flatten(this).find(node => node.tagName === tag); }
  set innerHTML(value) { throw Error(`HTML injection: ${value}`); }
}
const flatten = node => [node, ...node.children.flatMap(flatten)];
const text = node => flatten(node).map(node => node.textContent).join("\n");
const patch = "diff --git a/code.py b/code.py\n--- a/code.py\n+++ b/code.py\n@@ -1,2 +1,2 @@\n-import old\n+import new\n print('<script>inert</script>')\n";
function harness(desktop = false) {
  const listeners = {}, keys = {}, calls = [], models = [], editors = [];
  const document = { activeElement: undefined, addEventListener: (name, callback, capture = false) => keys[name] = { callback, capture }, removeEventListener: name => delete keys[name] };
  document.createElement = tag => new Element(tag, document);
  document.body = document.createElement("body"); document.head = document.createElement("head");
  const host = document.createElement("main"), castle = document.createElement("div"); castle.id = "world-map";
  document.body.append(host, castle); document.activeElement = document.body;
  document.getElementById = id => flatten(document.body).find(node => node.id === id);
  const browser = { document, location: { href: "http://127.0.0.1:8765/?view_token=watch" },
    addEventListener: (name, callback, capture = false) => listeners[name] = { callback, capture }, removeEventListener: name => delete listeners[name],
    matchMedia: () => ({ matches: false }), ...(desktop ? { specvaniaDesktop: { isDesktop: true } } : {}) };
  function create(container, options, diff = false) {
    const editor = { options, container, disposed: false, dispose() { this.disposed = true; },
      setModel(pair) { this.pair = pair; }, type(value, side = "modified") {
        // Mimic Monaco's edit path: both sides respect readOnly/domReadOnly.
        if (!this.options.readOnly && !this.options.domReadOnly && (!diff || this.options.originalEditable)) {
          const model = diff ? this.pair[side] : this.options.model; model.value += value;
        }
      } };
    editors.push(editor); return editor;
  }
  const monaco = { editor: {
    defineTheme() {}, createModel(value, language) { const model = { value, language, disposed: false, dispose() { this.disposed = true; }, getValue() { return this.value; } }; models.push(model); return model; },
    create: (container, options) => create(container, options), createDiffEditor: (container, options) => create(container, options, true),
  } };
  const responses = { T01: { room_id: "T01", kind: "commit", files: [{ path: "code.py", diff: patch }], checklist: [{ item: "Read code", ok: true, note: "Covered" }], review: { verdict: "pass", feedback: "Cleared feedback", checklist: [{ item: "Read code", ok: true, note: "Covered" }] }, gates: [{ command: "pytest", exit_code: 0, output_summary: "passed" }] },
    T02: { room_id: "T02", kind: "working", files: [{ path: "code.ts", diff: patch }], checklist: [{ item: "Active mission", ok: false }], review: { verdict: "fail", feedback: "Active feedback" }, gates: [{ command: "ruff", exit_code: 1, output_summary: "failure" }] },
    T03: { room_id: "T03", kind: "files", files: [{ path: "config.toml", content: "[project]\nname='inert <script>'" }], checklist: [{ item: "Locked mission", ok: null }] } };
  const fetchCode = async id => { calls.push(id); return { ok: true, json: async () => responses[id] }; };
  return { browser, document, host, castle, editors, models, monaco, calls, responses, fetchCode, windowKeydown: () => listeners.keydown,
    emit: (type, detail) => listeners[type]?.callback({ detail }), key: (key, extra = {}) => {
      const event = { key, target: document.activeElement, defaultPrevented: false, stopped: false,
        preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...extra };
      // Window capture precedes document capture and Monaco's target handler.
      if (listeners.keydown?.capture) listeners.keydown.callback(event);
      if (!event.stopped && keys.keydown?.capture) keys.keydown.callback(event);
      if (!event.stopped) event.target?.listeners.keydown?.(event);
      if (!event.stopped && keys.keydown && !keys.keydown.capture) keys.keydown.callback(event);
      if (!event.stopped && listeners.keydown && !listeners.keydown.capture) listeners.keydown.callback(event);
      return event;
    } };
}
const rooms = [{ id: "T01", title: "Cleared room", state: "cleared" }, { id: "T02", title: "Active room", state: "active" }, { id: "T03", title: "Locked room", state: "locked" }];
const button = (panel, label) => flatten(panel).find(node => node.tagName === "button" && node.textContent === label);

test("four_tabs_render_cleared_active_locked_rooms_in_both_watch_shells", async () => {
  const { mountRoomPanel } = await load();
  for (const desktop of [false, true]) {
    const h = harness(desktop), panel = mountRoomPanel(h.host, h.browser, { loadMonaco: async () => h.monaco, fetchCode: h.fetchCode });
    h.emit("specvania:state", { run_id: "run", rooms });
    assert.equal(flatten(h.host).filter(node => node.tagName === "button").length, 3);
    for (const room of rooms) {
      await panel.open(room.id); await settle();
      assert.match(text(panel.panel), new RegExp(room.state));
      const mission = h.responses[room.id].checklist[0];
      assert.ok(text(panel.panel).includes(mission.item));
      assert.match(text(panel.panel), room.state === "cleared" ? /Passed · Read code/ : room.state === "active" ? /Failed · Active mission/ : /Pending · Locked mission/);
      for (const tab of ["Mission", "Code", "Diff", "Review"]) assert.ok(button(panel.panel, tab));
      button(panel.panel, "Code").click(); await settle();
      assert.equal(h.models.at(-1).language, room.state === "locked" ? "ini" : room.state === "active" ? "typescript" : "python");
      assert.ok(h.models.at(-1).value.includes(room.state === "locked" ? "inert <script>" : "import new"));
      button(panel.panel, "Diff").click(); await settle();
      if (room.state === "locked") assert.match(text(panel.panel), /declared file content/);
      else { assert.equal(h.editors.at(-1).options.renderSideBySide, true); assert.equal(h.editors.at(-1).options.useInlineViewWhenSpaceIsLimited, false); assert.match(h.editors.at(-1).pair.original.value, /import old/); assert.match(h.editors.at(-1).pair.modified.value, /import new/); }
      button(panel.panel, "Review").click();
      if (room.state === "locked") { assert.match(text(panel.panel), /has not reviewed/); assert.match(text(panel.panel), /No gate results/); }
      else { assert.ok(text(panel.panel).includes(h.responses[room.id].review.feedback)); assert.ok(text(panel.panel).includes(h.responses[room.id].gates[0].command)); assert.ok(text(panel.panel).includes(`exit ${h.responses[room.id].gates[0].exit_code}`)); }
    }
    assert.equal(panel.panel.style.width, "60%");
    const { UI_COLORS } = await import(pathToFileURL(path.join(root, "pixel/palette.js")));
    for (const control of flatten(panel.panel).filter(node => node.tagName === "button")) {
      assert.equal(control.style.color, UI_COLORS.text); assert.equal(control.style.backgroundColor, UI_COLORS.surface);
    }
    panel.destroy();
  }
});

test("read_only_edit_attempts_leave_code_and_both_diff_models_unchanged_and_dispose", async () => {
  const { mountRoomPanel } = await load(); const h = harness();
  const panel = mountRoomPanel(h.host, h.browser, { loadMonaco: async () => h.monaco, fetchCode: h.fetchCode });
  h.emit("specvania:state", { rooms }); await panel.open("T01");
  for (const tab of ["Code", "Diff"]) {
    button(panel.panel, tab).click(); await settle();
    const editor = h.editors.at(-1), before = h.models.map(model => model.getValue());
    assert.equal(editor.options.readOnly, true); assert.equal(editor.options.domReadOnly, true);
    assert.equal(editor.options.quickSuggestions, false); assert.equal(editor.options.suggestOnTriggerCharacters, false);
    editor.type("intrusion"); editor.type("intrusion", "original");
    assert.deepEqual(h.models.map(model => model.getValue()), before);
    if (tab === "Diff") assert.equal(editor.options.originalEditable, false);
  }
  panel.close(); assert.ok(h.editors.every(editor => editor.disposed)); assert.ok(h.models.every(model => model.disposed)); panel.destroy();
});

test("room_selection_escape_focus_trap_keyboard_tabs_language_switch_and_reduced_motion", async () => {
  const { mountRoomPanel } = await load(), { setLanguage } = await import(pathToFileURL(path.join(root, "i18n/index.js")));
  const h = harness(); h.browser.matchMedia = () => ({ matches: true });
  const panel = mountRoomPanel(h.host, h.browser, { loadMonaco: async () => h.monaco, fetchCode: h.fetchCode });
  h.emit("specvania:state", { rooms }); h.emit("specvania:room-selected", { room_id: "T01" }); await settle(); await settle();
  assert.deepEqual(h.calls, ["T01"]); assert.equal(h.document.activeElement, button(panel.panel, "Close room panel"));
  assert.equal(panel.panel.style.transition, "none");
  const mission = button(panel.panel, "Mission"); mission.focus(); mission.listeners.keydown({ key: "ArrowRight", preventDefault() {} }); await settle();
  assert.equal(h.document.activeElement, button(panel.panel, "Code")); assert.equal(h.document.activeElement.attrs["aria-selected"], "true");
  setLanguage("pt-BR"); assert.ok(button(panel.panel, "Código")); setLanguage("en");
  const last = h.document.getElementById("room-panel-content").querySelector("select"); last.focus(); h.key("Tab");
  assert.equal(h.document.activeElement, button(panel.panel, "Close room panel"));
  h.key("Escape"); assert.equal(panel.panel.style.visibility, "hidden"); assert.equal(h.document.activeElement, h.castle);
  const launcher = flatten(h.host).find(node => node.tagName === "button"); launcher.focus(); await panel.open("T03"); panel.close(); assert.equal(h.document.activeElement, launcher);
  panel.destroy();
});

test("friendly_404_no_html_injection_live_review_gates_and_stale_fetch_is_ignored", async () => {
  const { mountRoomPanel } = await load(); const h = harness(); let resolveFirst;
  const panel = mountRoomPanel(h.host, h.browser, { loadMonaco: async () => h.monaco,
    fetchCode: id => id === "T01" ? new Promise(resolve => resolveFirst = resolve) : h.fetchCode(id) });
  h.emit("specvania:state", { rooms }); const first = panel.open("T01"); await panel.open("T03");
  resolveFirst({ ok: false, json: async () => ({ detail: "obsolete" }) }); await first;
  assert.match(text(panel.panel), /Locked mission/); assert.doesNotMatch(text(panel.panel), /obsolete/);
  h.emit("specvania:event", { type: "review", room_id: "T03", verdict: "fail", feedback: "<script>inert</script>", checklist: [{ item: "Locked mission", ok: false }] });
  h.emit("specvania:event", { type: "gate_result", room_id: "T03", command: "node", exit_code: 3, output_summary: "<img>inert</img>" });
  button(panel.panel, "Review").click(); assert.match(text(panel.panel), /<script>inert<\/script>/); assert.match(text(panel.panel), /exit 3/);
  panel.destroy();
  const missing = mountRoomPanel(h.host, h.browser, { fetchCode: async () => ({ ok: false, json: async () => ({ detail: "Repository unavailable for this view." }) }) });
  await missing.open("T03"); assert.match(text(missing.panel), /Exported replays contain events only/);
  const { setLanguage } = await import(pathToFileURL(path.join(root, "i18n/index.js")));
  setLanguage("pt-BR"); assert.match(text(missing.panel), /Replays exportados contêm apenas eventos/);
  setLanguage("en"); missing.destroy();
});

test("escape_closes_panel_from_editor_focus_before_monaco_and_leaves_other_inputs_alone", async () => {
  const { mountRoomPanel } = await load();
  for (const desktop of [false, true]) {
    const h = harness(desktop), panel = mountRoomPanel(h.host, h.browser, { loadMonaco: async () => h.monaco, fetchCode: h.fetchCode });
    assert.equal(h.windowKeydown()?.capture, true, "Escape must precede document/editor key handlers");
    h.emit("specvania:state", { rooms });
    for (const room of rooms) for (const tab of ["Code", "Diff"]) {
      await panel.open(room.id); button(panel.panel, tab).click(); await settle();
      const editor = h.editors.at(-1), input = h.document.createElement("textarea");
      let editorKeys = 0;
      input.addEventListener("keydown", event => { editorKeys++; event.stopPropagation(); });
      editor.container.append(input); input.focus();
      h.key("ArrowRight"); assert.equal(editorKeys, 1);
      const event = h.key("Escape");
      assert.equal(editorKeys, 1); assert.equal(event.defaultPrevented, true);
      assert.equal(panel.panel.style.visibility, "hidden");
      assert.equal(h.document.activeElement, h.castle); assert.equal(editor.disposed, true);
    }
    const other = h.document.createElement("input"); h.host.append(other); other.focus();
    let inputKeys = 0; other.addEventListener("keydown", () => inputKeys++);
    assert.equal(h.key("Escape").defaultPrevented, false); assert.equal(inputKeys, 1);
    assert.equal(h.document.activeElement, other);
    panel.destroy(); other.focus();
    assert.equal(h.key("Escape").defaultPrevented, false); assert.equal(inputKeys, 2);
  }
});

test("language_extensions_and_unified_patch_add_delete_multiple_hunks_no_newline", async () => {
  const { languageForPath, diffSides } = await load();
  for (const [extension, language] of Object.entries({ py: "python", js: "javascript", ts: "typescript", json: "json", md: "markdown", toml: "ini", yaml: "yaml", html: "html", css: "css", sh: "shell", unknown: "plaintext" })) assert.equal(languageForPath(`file.${extension}`), language);
  assert.deepEqual(diffSides("@@ -0,0 +1,1 @@\n+new\n\\ No newline at end of file"), { original: "", modified: "new" });
  assert.deepEqual(diffSides("@@ -1,1 +0,0 @@\n-old\n"), { original: "old", modified: "" });
  const multi = diffSides("@@ -3 +3 @@\n-a\n+b\n@@ -10 +10 @@\n-c\n+d\n");
  assert.match(multi.original, /omitted.*\na\n.*omitted.*\nc/); assert.match(multi.modified, /omitted.*\nb\n.*omitted.*\nd/);
});

function walk(directory) { return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => entry.isDirectory() ? walk(path.join(directory, entry.name)) : [path.join(directory, entry.name)]); }
test("vendored_AMD_viewer_graph_does_not_request_optional_contribution_services", () => {
  const vendor = path.join(root, "vendor/monaco");
  const serviceIds = ["IOutlineModelService", "ICodeLensCache", "IInlayHintsCache", "treeViewsDndService", "ISuggestMemories", "actionWidgetService"];
  // These decorators are constructor dependencies of the optional IDE
  // contributions. Excluding their module prevents requests even before
  // options like codeLens.enabled can take effect.
  for (const file of walk(vendor).filter(file => file.endsWith(".js"))) {
    const source = fs.readFileSync(file, "utf8");
    for (const id of serviceIds) assert.ok(!source.includes(`createDecorator("${id}")`), `${path.relative(vendor, file)} can request ${id}`);
  }
});
test("vendor_pinned_license_size_languages_and_AMD_dependency_graph_are_complete", () => {
  const vendor = path.join(root, "vendor/monaco"), files = walk(vendor);
  assert.ok(files.reduce((size, file) => size + fs.statSync(file).size, 0) <= 8_000_000);
  assert.match(fs.readFileSync(path.join(vendor, "LICENSE"), "utf8"), /MIT/);
  const notices = fs.readFileSync(path.join(root, "../../../../THIRD_PARTY_NOTICES.md"), "utf8");
  assert.match(notices, /0\.57\.0/); assert.match(notices, /3ea1712fbacd3290cf4751007e3a5b57cc279767607812d4c3e57925fb0b05c2/);
  const contribution = fs.readFileSync(path.join(vendor, "vs/basic-languages/monaco.contribution.js"), "utf8");
  const ids = [...contribution.matchAll(/id:"(\w+)"/g)].map(match => match[1]);
  assert.deepEqual(ids.sort(), ["python", "javascript", "typescript", "json", "markdown", "ini", "yaml", "html", "css", "shell"].sort());
  for (const file of files) {
    assert.doesNotMatch(file, /\/language\/|(?:css|html|json|ts)\.worker|nls\/lang/);
    if (!file.endsWith(".js") || path.basename(file) === "loader.js") continue;
    const source = fs.readFileSync(file, "utf8");
    const define = /^define\("([^"]+)",\s*(\[.*?\])/.exec(source);
    if (define) for (const dependency of JSON.parse(define[2])) {
      if (["exports", "require", "module"].includes(dependency)) continue;
      const moduleId = dependency.replace(/!$/, "");
      const destination = moduleId.startsWith(".") ? path.resolve(path.dirname(file), moduleId + ".js") : path.join(vendor, moduleId + ".js");
      assert.ok(fs.existsSync(destination), `${file} -> ${dependency}`);
    }
  }
});

test("offline_loader_requests_same_origin_vendor_scripts_css_font_worker_and_no_services", async () => {
  const { createMonacoLoader } = await load(); const h = harness(); const urls = [], required = [], configurations = [];
  h.document.head.append = node => {
    urls.push(node.src || node.href);
    if (node.tagName === "script") {
      h.browser.require = Object.assign((dependencies, resolve) => { required.push(...dependencies); resolve(); }, { config: options => configurations.push(options) });
      node.onload();
    }
  };
  h.document.head.appendChild = node => h.document.head.append(node);
  h.browser.monaco = h.monaco;
  const loader = createMonacoLoader(h.browser, h.document);
  assert.equal(await loader(), h.monaco); assert.equal(await loader(), h.monaco); assert.equal(urls.length, 1);
  assert.deepEqual(required, ["vs/editor/editor.main"]); assert.equal(configurations[0].paths.vs, "/vendor/monaco/vs");
  urls.push(h.browser.MonacoEnvironment.getWorkerUrl("", "editorWorkerService"));
  h.browser.Worker = class {
    constructor(url) { urls.push(url); queueMicrotask(() => this.onmessage({ data: { type: "vscode-worker-ready" } })); }
  };
  const workerInstance = await h.browser.MonacoEnvironment.getWorker("", "editorWorkerService");
  assert.ok(workerInstance instanceof h.browser.Worker);
  const vendor = path.join(root, "vendor/monaco"), modulePath = path.join(vendor, "vs/editor/editor.main.js");
  const entry = fs.readFileSync(modulePath, "utf8");
  let entryDependencies;
  vm.runInNewContext(entry, { globalThis: {}, document: h.document, define(name, dependencies, factory) { entryDependencies = dependencies; factory({}, { toUrl: name => `/vendor/monaco/${name}` }, {}, {}); } });
  assert.ok(!entryDependencies.some(id => /(?:worker|contribution-[\w-]+|index-)/.test(id)));
  const css = fs.readFileSync(path.join(vendor, "vs/editor/editor.main.css"), "utf8");
  for (const match of css.matchAll(/url\(([^)]+)\)/g)) {
    const value = match[1].replace(/^['"]|['"]$/g, "");
    if (value.startsWith("data:image/")) continue;
    urls.push(new URL(value, `${h.browser.location.href.split("?")[0]}vendor/monaco/vs/editor/editor.main.css`).href);
  }
  assert.ok(urls.some(url => url.endsWith("codicon.ttf")));
  for (const url of urls) { const parsed = new URL(url, h.browser.location.href); assert.equal(parsed.origin, new URL(h.browser.location.href).origin); assert.ok(parsed.pathname.startsWith("/vendor/monaco/"), parsed.href); }
  const worker = fs.readFileSync(path.join(vendor, "worker.js"), "utf8");
  assert.match(worker, /importScripts\("\.\/vs\/assets\/editor\.worker-[\w-]+\.js"\)/); assert.doesNotMatch(worker + entry, /Blob|createObjectURL|data:|blob:/);
});

test("default_fetch_uses_view_cookie_only_and_late_editor_load_cannot_reopen_closed_panel", async () => {
  const { mountRoomPanel } = await load(); const h = harness(true), requests = [];
  h.browser.fetch = async (url, options) => { requests.push({ url, options }); return { ok: true, json: async () => h.responses.T01 }; };
  let resolveMonaco;
  const panel = mountRoomPanel(h.host, h.browser, { loadMonaco: () => new Promise(resolve => resolveMonaco = resolve) });
  h.emit("specvania:state", { rooms }); await panel.open("T01");
  assert.deepEqual(requests, [{ url: "/api/rooms/T01/code", options: { credentials: "same-origin" } }]);
  button(panel.panel, "Code").click(); panel.close(); resolveMonaco(h.monaco); await settle();
  assert.equal(h.editors.length, 0); assert.equal(h.models.length, 0); assert.equal(panel.panel.style.visibility, "hidden");
  panel.destroy();
});

test("panel_mission_and_keyboard_room_list_follow_the_castle_door_projection", async () => {
  const { mountRoomPanel } = await load(); const h = harness();
  const panel = mountRoomPanel(h.host, h.browser, { loadMonaco: async () => h.monaco, fetchCode: h.fetchCode });
  const world = [{ ...rooms[0], state: "active", area: "A1", depends_on: [] }, { ...rooms[2], area: "A1", depends_on: ["T01"] }];
  h.emit("specvania:event", { type: "world_loaded", rooms: world });
  await panel.open("T03"); assert.match(text(panel.panel), /Room: locked/);
  h.emit("specvania:event", { type: "room_cleared", room_id: "T01" });
  button(panel.panel, "Mission").click(); assert.match(text(panel.panel), /Room: open/);
  assert.match(text(h.host), /T03 · Locked room · open/);
  panel.destroy();
});
