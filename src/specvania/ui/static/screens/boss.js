import { t } from "../i18n/index.js";
import { bossForArea } from "../pixel/cast.js";
import { expeditionDialog } from "./pause.js";

/** The arena itself and its hit/defeat/escape cues remain T30/T31's scene. */
export function mountBoss(socket, {browser = window} = {}) {
  const dialog = expeditionDialog("boss", {browser});
  const media = browser.matchMedia?.("(prefers-reduced-motion: reduce)");
  let setting = false;
  try {setting = JSON.parse(browser.localStorage?.getItem("specvania.settings") || "null")?.reducedMotion === true;} catch { /* Unavailable browser storage. */ }
  const motion = () => {if (dialog.root) dialog.root.dataset.transition = String(!media?.matches && !setting);};
  const settings = ({detail}) => {setting = detail.reducedMotion === true; motion();};
  browser.addEventListener?.("specvania:settings", settings); media?.addEventListener("change", motion);
  Promise.resolve(browser.specvaniaDesktop?.settings?.get?.()).then(value => {if (value) {setting = value.reducedMotion === true; motion();}}).catch(() => {});
  let fight = null, segments = new Map(), hp = null, result = null, continueButton = null;
  const signal = active => browser.dispatchEvent(new (browser.CustomEvent || globalThis.CustomEvent)("specvania:boss-screen", {detail: {active, area_id: fight?.area_id}}));
  function close() {if (fight) signal(false); dialog.close(); fight = null; segments.clear();}
  function refresh() {
    if (!fight) return;
    const passed = new Set((fight.results || []).filter(row => row.passed && fight.scenarios.includes(row.scenario)).map(row => row.scenario));
    for (const [scenario, segment] of segments) segment.dataset.passed = String(passed.has(scenario));
    hp.setAttribute("aria-valuenow", String(fight.scenarios.length - passed.size));
    continueButton.hidden = typeof fight.passed !== "boolean";
  }
  function show(value) {
    close(); fight = {...value, results: value.results || []};
    dialog.open("boss.title", (content, actions, ui) => {
      ui.portrait(content, bossForArea(fight.area_id));
      ui.text(content, "h2", () => t("boss.area", {area: fight.area_id}));
      hp = browser.document.createElement("div"); hp.className = "boss-hp"; hp.setAttribute("role", "meter");
      hp.setAttribute("aria-valuemin", "0"); hp.setAttribute("aria-valuemax", String(fight.scenarios.length));
      ui.text(content, "p", () => {hp.setAttribute("aria-label", t("boss.arenaHP")); return t("boss.arenaHP");});
      content.append(hp);
      for (const scenario of fight.scenarios) {
        const segment = ui.text(hp, "span", () => t("boss.arenaScenario", {scenario}));
        segment.dataset.scenario = scenario; segments.set(scenario, segment);
      }
      result = ui.text(content, "p", () => t(typeof fight.passed === "boolean" ? fight.passed ? "boss.defeated" : "boss.escaped" : "boss.fighting"));
      result.setAttribute("role", "status");
      continueButton = ui.button(actions, "idea.continue", close);
      continueButton.hidden = typeof fight.passed !== "boolean";
    }, {onEscape: () => {if (typeof fight?.passed === "boolean") close();}});
    dialog.root.className = "expedition-dialog boss-dialog";
    // Transparent panel frames the real castle arena, never substitutes new art.
    dialog.root.style.backgroundColor = "transparent";
    refresh(); motion(); signal(true);
  }
  const message = ({data}) => {
    const value = JSON.parse(data), event = value.event;
    if (value.kind === "state") {
      const latest = value.state.bosses?.at(-1);
      if (latest && value.state.status === "running" && latest.passed == null) show(latest); else close();
    } else if (value.kind === "event") {
      if (event.type === "boss_started") show(event);
      else if (fight && event.area_id === fight.area_id && event.type === "boss_scenario") {
        fight.results = [...fight.results.filter(row => row.scenario !== event.scenario), event]; refresh();
      } else if (fight && event.area_id === fight.area_id && event.type === "boss_result") {
        fight.passed = event.passed;
        result.textContent = t(event.passed ? "boss.defeated" : "boss.escaped"); refresh(); continueButton.focus();
      } else if (["run_paused", "run_finished", "run_started", "world_loaded", "room_entered"].includes(event.type)) close();
    }
  };
  socket.addEventListener("message", message);
  return {destroy() {socket.removeEventListener?.("message", message); close(); dialog.destroy(); browser.removeEventListener?.("specvania:settings", settings); media?.removeEventListener("change", motion);}};
}
