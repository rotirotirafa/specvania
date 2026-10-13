export function errorScreen(ui, error = {}) {
  const stopped = error.reasonKey === "error.stopped";
  const gone = stopped || error.kind === "connection" || error.reasonKey?.startsWith("error.start");
  ui.heading(stopped ? "error.stoppedHeading" : error.kind === "missingKey" ? "error.missingKey" : error.kind === "connection" ? "error.connection" : "error.title");
  if (error.kind === "missingKey") ui.text("p", "error.keyHelp");
  else if (error.reasonKey) ui.text("p", error.reasonKey);
  else if (error.reason) ui.text("p", "error.reason", {reason: error.reason});
  if (error.logPath) ui.text("p", "error.log", {path: error.logPath});
  if (gone && ui.restart) {
    const restart = ui.button("error.restart", async () => {
      restart.disabled = true;
      try {await ui.restart();} catch {restart.disabled = false; ui.text("p", "error.startFailed");}
    });
  }
  ui.demo();
  ui.button("title.settings", () => ui.show("settings"));
  if (!gone) ui.back();
  return {};
}
