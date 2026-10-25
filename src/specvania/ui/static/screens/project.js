/** Desktop-only key entry. The input travels solely over the narrow IPC bridge. */
export function apiKeyControls(ui, {always = false, missing = false} = {}) {
  const api = ui.browser.specvaniaDesktop?.apiKey;
  if (!api) return {render() {}, destroy() {}};
  const group = ui.element("section");
  const status = ui.document.createElement("p"); status.setAttribute("role", "status"); group.append(status);
  const field = ui.document.createElement("label"); group.append(field);
  const caption = ui.document.createElement("span"); ui.label(caption, "apiKey.paste"); field.append(caption);
  const input = ui.document.createElement("input");
  input.type = "password"; input.autocomplete = "off";
  input.spellcheck = false; input.setAttribute("autocapitalize", "off"); field.append(input);
  let stored = false, encryption = true, editing = false, busy = false, destroyed = false, reason = "";
  const notice = ui.document.createElement("p"); notice.setAttribute("role", "status"); group.append(notice);
  const render = () => {
    const source = ui.apiKeyStatus || {source: "none", env_name: "NVIDIA_API_KEY"};
    status.textContent = ui.t(stored ? "apiKey.stored" : source.source === "env" ? "apiKey.shell" : "apiKey.none", {env: source.env_name});
    const offer = always || missing || source.source === "none";
    const entry = offer && (!stored || editing);
    input.hidden = field.hidden = !entry; save.hidden = !entry;
    // The shared label display:grid rule overrides the browser's hidden style.
    field.style.display = entry ? "" : "none";
    cancel.hidden = !entry || !stored || !editing;
    replace.hidden = remove.hidden = !offer || !stored || editing;
    for (const node of [input, save, cancel, replace, remove]) node.disabled = busy;
    save.disabled = busy || !encryption;
    notice.textContent = ui.t(reason || (!encryption ? "apiKey.encryptionUnavailable" : "apiKey.empty"));
  };
  const perform = async action => {
    if (busy) return;
    busy = true; reason = ""; render();
    try {
      const result = await action();
      if (!result?.ok) {
        const allowed = ["apiKey.invalid", "apiKey.encryptionUnavailable", "apiKey.storeFailed", "apiKey.busy"];
        reason = allowed.includes(result?.reason) ? result.reason : "apiKey.storeFailed";
      } else {
        const state = await api.status(); stored = state.stored; encryption = state.encryption; editing = false;
      }
    } catch { reason = "apiKey.storeFailed"; }
    finally {busy = false; if (!destroyed) render();}
  };
  const save = ui.button("apiKey.save", () => {
    const value = input.value; input.value = "";
    perform(() => api.set(value));
  }, false, group);
  const cancel = ui.button("apiKey.cancel", () => {editing = false; input.value = ""; reason = ""; render(); replace.focus();}, false, group);
  const replace = ui.button("apiKey.replace", () => {editing = true; input.value = ""; render(); input.focus();}, false, group);
  const remove = ui.button("apiKey.remove", () => {input.value = ""; perform(() => api.remove());}, false, group);
  render();
  Promise.resolve(api.status()).then(state => {
    if (destroyed) return;
    stored = state.stored; encryption = state.encryption; render();
  }).catch(() => {if (!destroyed) {reason = "apiKey.storeFailed"; render();}});
  return {render, destroy() {destroyed = true; input.value = "";}};
}

/** Project entrance to the Mage and to existing worlds. */
export function projectScreen(ui, state = {}, reason = "") {
  ui.heading("project.title");
  const notice = ui.text("p", "project.pick");
  notice.setAttribute("role", "status");
  if (reason) ui.text("p", "project.reason", { reason });
  if (state.project) ui.text("p", "project.path", { path: state.project.path });
  if (state.project && !state.project.initialized) ui.button("project.init", () => ui.send("init_project"));
  ui.button("project.choose", ui.chooseFolder);
  const initialized = state.project?.initialized;
  if (initialized) {
    ui.button("idea.new", () => ui.show("idea"), !ui.canControl);
    ui.heading("project.worlds", "h2");
    if (!state.worlds?.length) ui.text("p", "project.noWorlds");
    for (const world of state.worlds || []) {
      const choose = ui.button("project.world", () => ui.show("idea", world), !ui.canControl);
      ui.label(choose, "project.world", {world});
    }
    // T25 exposes a count, not bug records. The existing Bestiary shows run bugs.
    ui.text("p", "project.bugs", { count: state.open_bugs || 0 });
  }
  const apiKey = apiKeyControls(ui, {missing: Boolean(reason)});
  const allowed = initialized && ["project_open", "world_ready", "finished"].includes(state.phase);
  let probing = null;
  const probe = ui.button("project.probe", () => {
    if (ui.send("probe_party")) { probing = true; probe.disabled = true; notice.textContent = ui.t("project.probing"); }
  }, !allowed);
  ui.button("title.settings", () => ui.show("settings"));
  ui.back();
  const results = ui.element("div");
  results.setAttribute("role", "status");
  let rows = [];
  return {
    destroy() {apiKey.destroy();},
    render() {
      apiKey.render();
      if (probing !== null) notice.textContent = ui.t(probing ? "project.probing" : "project.probed");
      for (const [node, result] of rows) {
        const key = result.status === undefined ? "probe.checking" : result.status === "ok" ? "probe.ok" : result.status === "timeout" ? "probe.timeout" : typeof result.status === "number" ? "probe.http" : "probe.connection";
        const latency = new Intl.NumberFormat(ui.settings.language, {minimumFractionDigits: 1, maximumFractionDigits: 1}).format(result.latency_ms / 1000);
        node.textContent = ui.t(key, { model: result.model, latency, status: result.status });
        node.style.color = result.status === undefined ? ui.palette.Y : result.status === "ok" ? ui.palette.G : ui.palette.R;
      }
    },
    probe(value, models = [], done = true) {
      probing = !done;
      probe.disabled = !allowed || probing;
      const order = models.length ? models : value.map(result => result.model);
      if (order.length !== rows.length || order.some((model, index) => model !== rows[index][0].dataset.probe)) {
        results.replaceChildren();
        rows = order.map(model => {
          const node = ui.document.createElement("p");
          node.dataset.probe = model;
          results.append(node);
          return [node, {model}];
        });
      }
      for (const row of rows) row[1] = value.find(result => result.model === row[0].dataset.probe) || {model: row[0].dataset.probe};
      this.render();
    },
  };
}
