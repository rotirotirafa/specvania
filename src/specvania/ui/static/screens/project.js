/** T34 receives the selected project through studio_state; no planning UI here. */
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
    ui.heading("project.worlds", "h2");
    if (!state.worlds?.length) ui.text("p", "project.noWorlds");
    for (const world of state.worlds || []) ui.text("p", "project.world", { world });
    // T25 exposes a count, not bug records. The existing Bestiary shows run bugs.
    ui.text("p", "project.bugs", { count: state.open_bugs || 0 });
  }
  const probe = ui.button("project.probe", () => {
    if (ui.send("probe_party")) { probe.disabled = true; notice.textContent = ui.t("project.probing"); }
  }, !initialized || !["project_open", "world_ready", "finished"].includes(state.phase));
  ui.button("title.settings", () => ui.show("settings"));
  ui.back();
  const results = ui.element("div");
  results.setAttribute("role", "status");
  let rows = [];
  return {
    render() { for (const [node, result] of rows) {
      const key = result.status === "ok" ? "probe.ok" : result.status === "timeout" ? "probe.timeout" : typeof result.status === "number" ? "probe.http" : "probe.connection";
      node.textContent = ui.t(key, { model: result.model, latency: result.latency_ms, status: result.status });
    } },
    probe(value) {
      probe.disabled = !initialized;
      notice.textContent = ui.t("project.probed");
      results.replaceChildren();
      rows = value.map(result => {
        const node = ui.document.createElement("p");
        node.dataset.probe = result.model;
        node.style.color = result.status === "ok" ? ui.palette.G : ui.palette.R;
        results.append(node);
        return [node, result];
      });
      this.render();
    },
  };
}
