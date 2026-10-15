import { i18n, t } from "../i18n/index.js";

/** A local UI clock and ticker; recorded time advances at playback speed. */
export function createRunningIndicator(browser, render, {clock, timer = browser} = {}) {
  if (!clock) {
    const origin = Date.now() - (browser.performance?.now?.() || 0);
    clock = {now: () => browser.performance ? origin + browser.performance.now() : Date.now()};
  }
  const media = browser.matchMedia?.("(prefers-reduced-motion: reduce)");
  let setting = false, interval = null, destroyed = false;
  let replay = false, awaitingEvent = false, rate = 1, recorded = null, anchored = clock.now();
  try {setting = JSON.parse(browser.localStorage?.getItem("specvania.settings") || "null")?.reducedMotion === true;} catch { /* Optional storage. */ }
  const now = () => replay && recorded !== null ? recorded + (clock.now() - anchored) * rate : clock.now();
  const changed = () => {if (!destroyed) render();};
  const settings = ({detail}) => {setting = detail.reducedMotion === true; changed();};
  const motion = () => changed();
  browser.addEventListener?.("specvania:settings", settings);
  media?.addEventListener?.("change", motion);
  const saved = browser.specvaniaDesktop?.settings?.get?.();
  const restore = value => {if (!destroyed && value) {setting = value.reducedMotion === true; changed();}};
  if (saved?.then) saved.then(restore).catch(() => {}); else if (saved) setting = saved.reducedMotion === true;
  return {
    now,
    event(event) {
      const ts = Date.parse(event.ts);
      if (!replay || !Number.isFinite(ts)) return 0;
      const shift = awaitingEvent ? ts - now() : 0;
      recorded = ts; anchored = clock.now(); awaitingEvent = false;
      return shift;
    },
    playback(value) {
      const current = now();
      if (Boolean(value) !== replay) {
        recorded = value ? current : null; awaitingEvent = Boolean(value);
      } else if (replay) recorded = current;
      replay = Boolean(value); anchored = clock.now();
      rate = value?.paused || value?.finished ? 0 : value?.speed || 1;
    },
    text(text, started) {
      const elapsed = Math.max(0, now() - started);
      return t("running.wait", {text, dots: setting || media?.matches ? t("running.ellipsis") : ".".repeat(1 + Math.floor(elapsed / 500) % 3),
        seconds: new Intl.NumberFormat(i18n.language).format(Math.floor(elapsed / 1000))});
    },
    active(value) {
      if (value && interval === null && timer.setInterval) interval = timer.setInterval(changed, 500);
      if (!value && interval !== null) {timer.clearInterval(interval); interval = null;}
    },
    destroy() {
      destroyed = true;
      if (interval !== null) timer.clearInterval(interval);
      browser.removeEventListener?.("specvania:settings", settings);
      media?.removeEventListener?.("change", motion);
    },
  };
}
