export function settingsScreen(ui) {
  ui.heading("title.settings");
  const field = (key, tag, type, value, changed) => {
    const label = ui.element("label");
    const caption = ui.document.createElement("span");
    ui.label(caption, key); label.append(caption);
    const input = ui.document.createElement(tag);
    if (type) input.type = type;
    input.value = value;
    input.addEventListener("change", () => changed(input));
    label.append(input);
    return input;
  };
  const status = ui.element("p");
  let statusKey = "";
  const renderStatus = () => {status.textContent = statusKey ? ui.t(statusKey) : "";};
  status.setAttribute("role", "status");
  const save = patch => {
    statusKey = ""; renderStatus();
    return ui.applySettings(patch).then(() => {statusKey = "settings.saved"; renderStatus();}).catch(() => {statusKey = "settings.invalid"; renderStatus();});
  };
  const language = field("settings.language", "select", "", ui.settings.language, node => save({language: node.value}));
  for (const [value, key] of [["pt-BR", "language.pt"], ["en", "language.en"]]) {
    const option = ui.document.createElement("option"); option.value = value; ui.label(option, key); language.append(option);
  }
  language.value = ui.settings.language;
  const budget = field("settings.budget", "input", "number", ui.settings.tokenBudget, node => {
    const tokenBudget = Number(node.value);
    if (!Number.isSafeInteger(tokenBudget) || tokenBudget <= 0) { statusKey = "settings.invalid"; renderStatus(); return; }
    save({ tokenBudget });
  });
  budget.min = "1"; budget.max = String(Number.MAX_SAFE_INTEGER); budget.step = "1";
  const specvaniaPath = field("settings.path", "input", "text", ui.settings.specvaniaPath, node => save({specvaniaPath: node.value}));
  ui.text("p", "settings.nextStart");
  ui.button("runtime.useDefault", () => save({specvaniaPath: "specvania"}));
  const runtimeLabel = ui.element("p");
  const runtimePath = ui.element("p");
  let activeRuntime = {kind: "path"};
  let destroyed = false;
  const renderRuntime = () => {
    const kind = ["bundled", "custom", "path"].includes(activeRuntime.kind) ? activeRuntime.kind : "path";
    runtimeLabel.textContent = ui.t(`runtime.${kind}`);
    runtimePath.textContent = kind === "custom" ? activeRuntime.executable || "" : "";
  };
  renderRuntime();
  Promise.resolve(ui.browser.specvaniaDesktop?.studio?.get?.()).then(status => {
    if (destroyed) return;
    activeRuntime = status?.runtime || activeRuntime; renderRuntime();
  }).catch(() => {});

  const motion = field("settings.motion", "input", "checkbox", "", node => save({reducedMotion: node.checked}));
  motion.checked = ui.settings.reducedMotion;
  ui.back();
  language.focus();
  return { render() { language.value = ui.settings.language; specvaniaPath.value = ui.settings.specvaniaPath; renderStatus(); renderRuntime(); }, destroy() {destroyed = true;} };
}
