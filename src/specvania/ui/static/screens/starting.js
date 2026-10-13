/** Local progress while the login shell and studio prepare the handshake. */
export function startingScreen(ui, browser) {
  const started = browser.performance.now();
  ui.heading("starting.title");
  const elapsed = ui.element("p");
  const hint = ui.text("p", "starting.firstStart");
  function render() {
    const seconds = Math.floor((browser.performance.now() - started) / 1000);
    elapsed.textContent = ui.t("starting.elapsed", {seconds});
    hint.hidden = seconds < 10;
  }
  render();
  const interval = browser.setInterval(render, 1000);
  return {render, destroy() {browser.clearInterval(interval);}};
}
