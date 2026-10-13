"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const vm = require("node:vm");
const {EventEmitter} = require("node:events");
const {pathToFileURL} = require("node:url");
const desktop = path.resolve(__dirname, "../../apps/desktop");
const staticRoot = path.resolve(desktop, "../../src/specvania/ui/static");
const {launchStudio, errorPage} = require(path.join(desktop, "studio.cjs"));
const flat = node => [node, ...node.children.flatMap(flat)];
const content = node => flat(node).map(node => node.textContent).join("\n");
const tick = () => new Promise(resolve => setImmediate(resolve));

function launch() {
  const child = new EventEmitter();
  child.stdout = new EventEmitter(); child.stderr = new EventEmitter(); child.kill = () => {};
  const errors = [], opened = [];
  launchStudio({spawn: () => child, env: {SHELL: "/bin/sh"}, executable: "/configured/specvania",
    timer: {setTimeout() {return 1;}, clearTimeout() {}}, logPath: "/safe/studio.log", log() {},
    open: (...args) => opened.push(args), error: (page, status) => errors.push({page, status})});
  return {child, errors, opened};
}
for (const [name, code, chunks] of [
  ["exit_127", 127, []],
  ["configured_executable_not_runnable", 126, []],
  ["shell_command_not_found", 1, ["/unrelated/profile: command not found: /private/other\n"]],
  ["chunked_shell_diagnostic", 1, ["/unrelated/profile: command not ", "found: /private/other\n"]],
]) {
  test(`first_run_detects_${name}_without_echoing_shell_paths`, () => {
    const h = launch();
    for (const chunk of chunks) h.child.stderr.emit("data", Buffer.from(chunk));
    h.child.emit("exit", code);
    assert.equal(h.errors.length, 1); assert.deepEqual(h.opened, []);
    assert.equal(h.errors[0].status.reasonKey, "error.startCliMissing");
    for (const secret of ["/unrelated/profile", "/private/other"]) {
      assert.ok(!JSON.stringify(h.errors).includes(secret));
    }
    assert.equal(h.errors[0].status.logPath, "/safe/studio.log");
  });
}
test("ordinary_start_failure_and_post_handshake_exit_keep_T33_reasons", () => {
  const ordinary = launch(); ordinary.child.stderr.emit("data", "Traceback\n"); ordinary.child.emit("exit", 1);
  assert.equal(ordinary.errors[0].status.reasonKey, "error.startExited");
  const started = launch();
  started.child.stdout.emit("data", JSON.stringify({port: 8765, view_token: "test-view", control_token: "test-control"}) + "\n");
  started.child.stderr.emit("data", "command not found\n"); started.child.emit("exit", 127);
  assert.equal(started.errors[0].status.reasonKey, "error.stopped");
  assert.equal(started.opened.length, 1);
});

class Element {
  constructor(tag, document) {Object.assign(this, {tagName: tag, document, children: [], style: {}, attrs: {}, dataset: {}, listeners: {}, textContent: ""});}
  append(...nodes) {for (const node of nodes) {node.parent = this; this.children.push(node);}}
  replaceChildren(...nodes) {this.children = []; this.append(...nodes);}
  setAttribute(key, value) {this.attrs[key] = String(value);}
  getAttribute(key) {return this.attrs[key] ?? null;}
  removeAttribute(key) {delete this.attrs[key];}
  addEventListener(key, fn) {(this.listeners[key] ||= []).push(fn);}
  removeEventListener() {}
  dispatch(type, event = {}) {for (const fn of this.listeners[type] || []) fn(event);}
  click() {if (!this.disabled) this.dispatch("click");}
  focus() {this.document.activeElement = this;}
  set innerHTML(_) {assert.fail("Screen must use inert text nodes");}
  remove() {}
}
function renderer(language) {
  const doc = {createElement: tag => new Element(tag, doc)};
  doc.head = new Element("head", doc); const host = new Element("section", doc);
  doc.getElementById = id => id === "screen-host" ? host : null;
  const restarts = [], sent = [], events = new EventEmitter(), intervals = new Map();
  let intervalId = 0;
  const browser = {document: doc, navigator: {language}, CustomEvent: class {constructor(type, options) {this.type = type; this.detail = options.detail;}},
    performance: {now: () => 0},
    setInterval(fn, ms) {const id = ++intervalId; intervals.set(id, {fn, ms}); return id;},
    clearInterval(id) {intervals.delete(id);},
    addEventListener: events.on.bind(events), removeEventListener: events.off.bind(events), dispatchEvent: event => events.emit(event.type, event),
    specvaniaDesktop: {isDesktop: true, controlToken: () => "test-control",
      settings: {get: async () => ({language}), set: async value => value},
      studio: {restart: async () => restarts.push(true)}}};
  const socket = new EventEmitter(); socket.readyState = 1;
  socket.addEventListener = socket.on.bind(socket); socket.removeEventListener = socket.off.bind(socket);
  socket.send = data => sent.push(JSON.parse(data));
  return {doc, host, browser, socket, restarts, sent, intervals,
    button: label => flat(host).find(n => n.tagName === "button" && n.textContent === label)};
}
function executePage(page, h) {
  const script = page.match(/<script nonce="[a-f0-9]+">([\s\S]*?)<\/script>/)[1];
  return vm.runInNewContext(script, {window: h.browser, document: h.doc, globalThis: {window: h.browser}});
}
for (const language of ["en", "pt-BR"]) {
  test(`first_run_layout_fits_viewport_with_heading_and_actions_outside_scroll_content_${language}`, async () => {
    const h = renderer(language);
    const dictionary = JSON.parse(fs.readFileSync(path.join(staticRoot, `i18n/${language}.json`)));
    const page = errorPage("error.startCliMissing", "/safe/" + "long-directory/".repeat(40) + "studio.log");
    const screens = executePage(page, h);
    try {
      await screens.ready;
      const panel = h.host.children[0], [heading, body, actions] = panel.children;
      assert.equal(panel.children.length, 3);
      assert.equal(heading.tagName, "h1"); assert.equal(heading.textContent, dictionary["error.title"]);
      assert.equal(body.className, "error-content"); assert.equal(body.tabIndex, 0);
      assert.equal(body.getAttribute("role"), "region");
      assert.equal(body.getAttribute("aria-labelledby"), heading.id);
      assert.equal(actions.className, "error-actions"); assert.equal(actions.parent, panel);
      assert.ok(!flat(body).includes(heading)); assert.ok(!flat(body).includes(actions));
      for (const paragraph of flat(panel).filter(n => n.tagName === "p")) assert.equal(paragraph.parent, body);
      for (const button of flat(panel).filter(n => n.tagName === "button")) assert.equal(button.parent, actions);
      assert.deepEqual(actions.children.map(n => n.textContent), ["error.restart", "title.demo", "title.settings"].map(key => dictionary[key]));
      assert.equal(h.button(dictionary["title.demo"]).disabled, true);
      assert.ok(content(body).includes(dictionary["error.cliDemo"]));
      const rule = selector => {
        const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const match = content(h.doc.head).match(new RegExp(escaped + "\\s*\\{([^}]+)\\}"));
        assert.ok(match, selector); return match[1];
      };
      const hostStyle = rule("#screen-host"), shellStyle = rule("#screen-host[data-screen=error] .screen-panel");
      assert.match(page, /#desktop-title-bar\s*\{[^}]*height:32px;[^}]*-webkit-app-region:drag/);
      assert.match(hostStyle, /inset:\s*32px 0 0/); assert.match(hostStyle, /padding:\s*16px 24px/);
      assert.match(rule("#screen-host[data-screen=error]"), /overflow:\s*hidden/);
      assert.match(shellStyle, /box-sizing:\s*border-box/); assert.match(shellStyle, /display:\s*flex/);
      assert.match(shellStyle, /flex-direction:\s*column/); assert.match(shellStyle, /max-height:\s*calc\(100vh - 64px\)/);
      assert.match(shellStyle, /min-height:\s*0/); assert.match(shellStyle, /overflow:\s*hidden/);
      assert.match(rule("#screen-host .error-content"), /min-height:\s*0/);
      assert.match(rule("#screen-host .error-content"), /flex:\s*1 1 auto/);
      assert.match(rule("#screen-host .error-content"), /overflow:\s*auto/);
      const actionsStyle = rule("#screen-host .error-actions");
      assert.match(actionsStyle, /flex-shrink:\s*0/); assert.match(actionsStyle, /display:\s*flex/);
      assert.match(actionsStyle, /flex-wrap:\s*wrap/);
      const headingStyle = rule("#screen-host[data-screen=error] h1");
      assert.match(headingStyle, /flex-shrink:\s*0/);
      const fontSize = Number(headingStyle.match(/font-size:\s*(\d+)px/)[1]);
      const panelWidth = Number(rule("#screen-host .screen-panel").match(/width:\s*(\d+)px/)[1]);
      assert.match(rule("#screen-host .screen-panel"), /max-width:\s*100%/);
      // Press Start 2P advances one em per glyph. Check the translated title
      // against the actual CSS size rather than only checking for a small font.
      for (const [width, height] of [[960, 540], [1280, 720], [1920, 1080]]) {
        assert.ok(Array.from(heading.textContent).length * fontSize <= Math.min(panelWidth, width - 48), `${language} heading fits at ${width}`);
        const panelHeight = height - 64, top = 32 + 16;
        assert.ok(top + panelHeight <= height - 16, `Panel leaves the title bar and bottom margin at ${height}`);
      }
      const restart = h.button(dictionary["error.restart"]);
      assert.equal(h.doc.activeElement, restart);
      h.host.dispatch("keydown", {key: "Tab", preventDefault() {}});
      assert.equal(h.doc.activeElement, h.button(dictionary["title.settings"]));
      h.host.dispatch("keydown", {key: "Tab", preventDefault() {}});
      assert.equal(h.doc.activeElement, body, "Keyboard can reach the scroll area");
      h.browser.specvaniaDesktop.studio.restart = async () => {throw new Error("test restart failure");};
      restart.click(); await tick();
      assert.equal(restart.disabled, false);
      assert.ok(content(body).includes(dictionary["error.startFailed"]));
      assert.equal(panel.children.length, 3, "Restart errors stay inside the scroll area");
    } finally {screens.destroy();}
  });
  test(`first_run_screen_explains_install_key_restart_and_demo_prerequisite_${language}`, async () => {
    const h = renderer(language);
    const dictionary = JSON.parse(fs.readFileSync(path.join(staticRoot, `i18n/${language}.json`)));
    executePage(errorPage("error.startCliMissing", "/safe/studio.log"), h); await tick();
    for (const key of ["error.startCliMissing", "error.cliInstall", "error.cliClone", "error.cliKey", "error.cliDemo"]) {
      assert.ok(content(h.host).includes(dictionary[key]), key);
    }
    for (const term of ["uv tool install --compile-bytecode specvania", "uv tool install --compile-bytecode .", "PyPI", "NVIDIA_API_KEY", "~/.zshrc"]) assert.ok(content(h.host).includes(term), term);
    assert.match(dictionary["error.cliDemo"], language === "en" ? /No API key/ : /não precisa de chave de API/);
    assert.equal(h.button(dictionary["title.demo"]).disabled, true);
    const restart = h.button(dictionary["error.restart"]);
    assert.equal(restart.disabled, false);
    assert.equal(h.doc.activeElement, restart);
    restart.click(); await tick(); assert.deepEqual(h.restarts, [true]);
    assert.deepEqual(h.sent, []);
    h.button(dictionary["title.settings"]).click();
    assert.ok(content(h.host).includes(dictionary["title.settings"]));
  });
  test(`missing_key_keeps_Watch_demo_enabled_and_sends_open_replay_${language}`, async () => {
    const h = renderer(language);
    const {mountScreens} = await import(pathToFileURL(path.join(staticRoot, "screens/title.js")));
    const screens = mountScreens({host: h.host, browser: h.browser, socket: h.socket});
    await screens.ready;
    try {
      h.socket.emit("message", {data: JSON.stringify({kind: "state", phase: "idle", state: {run_id: null}})});
      h.socket.emit("message", {data: JSON.stringify({kind: "approval_rejected", reason: "API key missing; check the configured environment variable."})});
      const dictionary = JSON.parse(fs.readFileSync(path.join(staticRoot, `i18n/${language}.json`)));
      const demo = h.button(dictionary["title.demo"]);
      assert.equal(demo.disabled, false); demo.click();
      assert.deepEqual(h.sent.at(-1), {kind: "open_replay", control_token: "test-control"});
    } finally {screens.destroy();}
  });
}

test("packaged_first_run_page_uses_bundled_assets_without_source_checkout", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "specvania-first-run-"));
  try {
    fs.copyFileSync(path.join(desktop, "studio.cjs"), path.join(directory, "studio.cjs"));
    const config = JSON.parse(fs.readFileSync(path.join(desktop, "electron-builder.yml")));
    const resources = config.files.find(file => typeof file === "object" && file.to === "static");
    assert.ok(resources, "Package the starting and error screens before the CLI can run");
    const source = path.resolve(desktop, resources.from);
    for (const pattern of resources.filter) {
      const relative = pattern.replace(/\/\*\*$/, "");
      const target = path.join(directory, resources.to, relative);
      fs.mkdirSync(path.dirname(target), {recursive: true});
      fs.cpSync(path.join(source, relative), target, {recursive: true});
    }
    // Every module/data file localPage loads must survive the builder's filters,
    // including the starting screen used before the first CLI handshake.
    for (const relative of [
      "i18n/index.js", "i18n/en.json", "i18n/pt-BR.json",
      "pixel/palette.js", "pixel/cast.js",
      "screens/project.js", "screens/settings.js", "screens/error.js",
      "screens/starting.js", "screens/title.js",
      "vendor/fonts/PressStart2P-Regular.ttf", "vendor/fonts/OFL.txt",
      "vendor/kenney-tiny-dungeon/tilemap_packed.png", "vendor/kenney-tiny-dungeon/License.txt",
    ]) {
      assert.deepEqual(fs.readFileSync(path.join(directory, "static", relative)), fs.readFileSync(path.join(staticRoot, relative)), relative);
    }
    const packaged = require(path.join(directory, "studio.cjs"));
    const page = packaged.errorPage("error.startCliMissing", "/safe/studio.log");
    const starting = packaged.startingPage();
    for (const local of [starting, page]) {
      assert.match(local, /font\/ttf;base64,/); assert.match(local, /data:image\/png;base64,/);
      assert.doesNotMatch(local, /<script[^>]+src=|<iframe|unsafe-eval/);
    }
    for (const language of ["en", "pt-BR"]) {
      const h = renderer(language), dictionary = JSON.parse(fs.readFileSync(path.join(staticRoot, `i18n/${language}.json`)));
      const progress = executePage(starting, h);
      await progress.ready;
      assert.equal(h.host.dataset.screen, "starting");
      assert.ok(content(h.host).includes(dictionary["starting.title"]));
      assert.equal(h.intervals.size, 1);
      progress.destroy(); assert.equal(h.intervals.size, 0);
      const failure = executePage(page, h);
      try {
        await failure.ready;
        assert.equal(h.host.dataset.screen, "error");
        assert.ok(content(h.host).includes(dictionary["error.startCliMissing"]));
        assert.ok(h.button(dictionary["error.restart"]));
        assert.equal(h.button(dictionary["title.demo"]).disabled, true);
        assert.match(content(h.host), /uv tool install --compile-bytecode/);
      } finally {failure.destroy();}
    }
  } finally {fs.rmSync(directory, {recursive: true, force: true});}
});
