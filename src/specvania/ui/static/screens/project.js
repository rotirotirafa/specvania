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
    render() {
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
