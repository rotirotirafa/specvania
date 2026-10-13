import { UI_COLORS, PALETTE } from "../pixel/palette.js";
import { panel, label } from "./party.js";

export function bestiaryFromState(state) {
  return {feature: state.feature || null, bugs: (state.bugs || []).map(bug => ({...bug})),
    bosses: (state.bosses || []).map(boss => ({...boss, scenarios:[...boss.scenarios], results:[...boss.results]}))};
}
export function openBugs(state) {
  return state.bugs.filter(bug => bug.status === "open" && (!bug.feature || !state.feature || bug.feature === state.feature));
}
export function bossSegments(boss) {
  return [...new Set(boss.scenarios)].map(scenario => ({scenario,
    alive: !boss.results.find(result => result.scenario === scenario)?.passed}));
}
export function projectBestiary(state, event) {
  if (event.type === "run_started") return bestiaryFromState({feature:event.feature});
  const next = bestiaryFromState(state);
  if (event.type === "spec_written") next.feature = event.feature;
  if (event.type === "bug_opened") {
    next.bugs = next.bugs.filter(bug => bug.bug_id !== event.bug_id);
    next.bugs.push({...event, status:"open"});
  } else if (event.type === "bug_closed") {
    next.bugs = next.bugs.map(bug => bug.bug_id === event.bug_id ? {...bug,status:event.status} : bug);
  } else if (event.type === "world_loaded") {
    const areas = event.areas?.length ? event.areas : [...new Map(event.rooms.map(room => [room.area,{id:room.area,boss_ids:room.boss_ids || []}])).values()];
    for (const area of areas) if (!next.bosses.some(boss => boss.area_id === area.id)) {
      next.bosses.push({area_id:area.id,scenarios:[...area.boss_ids],results:[]});
    }
  } else if (event.type === "boss_started") {
    next.bosses = next.bosses.filter(boss => boss.area_id !== event.area_id);
    next.bosses.push({area_id:event.area_id,scenarios:[...event.scenarios],results:[]});
  } else if (event.type === "boss_scenario") {
    const boss = next.bosses.find(boss => boss.area_id === event.area_id);
    if (boss && boss.scenarios.includes(event.scenario)) {
      boss.results = boss.results.filter(result => result.scenario !== event.scenario);
      boss.results.push({...event});
    }
  }
  return next;
}

export function mountBestiary(host, browser = window) {
  const document = browser.document, element = panel(document,"bestiary-hud","Bestiary and bosses");
  let state = bestiaryFromState({}); host.append(element);
  function render() {
    element.replaceChildren(label(document,"h2","Bestiary"));
    const bugs = openBugs(state);
    if (!bugs.length) element.append(label(document,"p","No open bugs in this feature."));
    for (const bug of bugs) {
      element.append(label(document,"p",`${bug.bug_id} · ${bug.room_id || "no room"} · ${bug.title}\n${bug.body || ""}`));
    }
    for (const boss of state.bosses) {
      const segments = bossSegments(boss); if (!segments.length) continue;
      const alive = segments.filter(segment => segment.alive).length;
      element.append(label(document,"h3",`${boss.area_id} boss · HP ${alive}/${segments.length}`));
      const meter = document.createElement("div"); meter.setAttribute("role","meter");
      meter.setAttribute("aria-label",`${boss.area_id} boss HP`);
      for (const [name,value] of Object.entries({"aria-valuemin":0,"aria-valuemax":segments.length,"aria-valuenow":alive})) meter.setAttribute(name,value);
      Object.assign(meter.style,{display:"flex",gap:"4px"});
      for (const segment of segments) {
        const pip = label(document,"span",`${segment.alive ? "■" : "□"} ${segment.scenario}`);
        pip.style.color = segment.alive ? PALETTE.R : UI_COLORS.muted;
        pip.setAttribute("aria-label",`${segment.scenario}: ${segment.alive ? "remaining" : "passed"}`); meter.append(pip);
      }
      element.append(meter);
    }
  }
  const onState = ({detail}) => { state = bestiaryFromState(detail); render(); };
  const onEvent = ({detail}) => { state = projectBestiary(state,detail); render(); };
  browser.addEventListener("specvania:state",onState); browser.addEventListener("specvania:event",onEvent); render();
  return {destroy() {
    browser.removeEventListener("specvania:state",onState); browser.removeEventListener("specvania:event",onEvent); element.remove();
  }};
}
