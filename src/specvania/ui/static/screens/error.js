export function errorScreen(ui, error = {}) {
  const stopped = error.reasonKey === "error.stopped";
  const gone = stopped || error.kind === "connection" || error.reasonKey?.startsWith("error.start");
  const heading = ui.heading(stopped ? "error.stoppedHeading" : error.kind === "missingKey" ? "error.missingKey" : error.kind === "connection" ? "error.connection" : "error.title");
  heading.id = "error-heading";
  const content = ui.element("div"); content.className = "error-content"; content.tabIndex = 0;
  content.setAttribute("role", "region"); content.setAttribute("aria-labelledby", heading.id);
  const text = (key, params) => ui.text("p", key, params, content);
  if (error.kind === "missingKey") text("error.keyHelp");
  else if (error.reasonKey) text(error.reasonKey);
  else if (error.reason) text("error.reason", {reason: error.reason});
  if (error.reasonKey === "error.startCliMissing") {
    text("error.cliInstall");
    text("error.cliClone");
    text("error.cliKey");
    text("error.cliDemo");
  }
  if (error.logPath) text("error.log", {path: error.logPath});
  const actions = ui.element("footer"); actions.className = "error-actions";
  const button = (key, action) => ui.button(key, action, false, actions);
  if (gone && ui.restart) {
    const restart = button("error.restart", async () => {
      restart.disabled = true;
      try {await ui.restart();} catch {restart.disabled = false; text("error.startFailed");}
    });
  }
  ui.demo(actions);
  button("title.settings", () => ui.show("settings"));
  if (!gone) ui.back(actions);
  return {};
}
