import { UI_COLORS, PALETTE } from "../pixel/palette.js";

const ROLES = Object.freeze(["mage", "warrior", "paladin"]);

/** Event → status table (asserted independently in tests/ui/hud.mjs):
 * room_entered → Warrior thinking, Mage/Paladin idle (resume from a pause)
 * attempt_started / respawn / tool_refused → Warrior thinking
 * model_call / fallback → event.role thinking
 * cooldown → event.role cooldown; timer expiry → thinking
 * action → Warrior acting (finish → idle)
 * gate_result → Warrior idle, Paladin thinking
 * review → Warrior idle, Paladin idle
 * room_cleared / room_failed / room_skipped → Warrior idle
 * approval_requested / approval_answered → Warrior waiting for player / acting
 * clarify_question / clarify_answer / spec_written → Mage waiting / thinking / idle
 * boss_started → Paladin thinking, Mage/Warrior idle (resume from a pause)
 * boss_scenario / boss_result → Paladin acting / idle
 * run_paused / run_finished → all waiting for player / idle
 * Everything else preserves status. No provider request-start event exists;
 * thinking begins at the room/attempt boundary or a completed model_call.
 */
export function eventStatuses(event) {
  switch (event.type) {
    case "room_entered": return {warrior: "thinking", mage: "idle", paladin: "idle"};
    case "attempt_started": case "respawn": case "tool_refused": return {warrior: "thinking"};
    case "model_call": case "fallback": return {[event.role]: "thinking"};
    case "cooldown": return {[event.role]: "cooldown"};
    case "action": return {warrior: event.action === "finish" ? "idle" : "acting"};
    case "gate_result": return {warrior: "idle", paladin: "thinking"};
    case "review": return {warrior: "idle", paladin: "idle"};
    case "room_cleared": case "room_failed": case "room_skipped": return {warrior: "idle"};
    case "approval_requested": return {warrior: "waiting for player"};
    case "approval_answered": return {warrior: "acting"};
    case "clarify_question": return {mage: "waiting for player"};
    case "clarify_answer": return {mage: "thinking"};
    case "spec_written": return {mage: "idle"};
    case "boss_started": return {paladin: "thinking", mage: "idle", warrior: "idle"};
    case "boss_scenario": return {paladin: "acting"};
    case "boss_result": return {paladin: "idle"};
    case "run_paused": return Object.fromEntries(ROLES.map(role => [role, "waiting for player"]));
    case "run_finished": return Object.fromEntries(ROLES.map(role => [role, "idle"]));
    default: return {};
  }
}

/** Snapshot data comes from T03; per-model counters stay identical on reconnect.
 * The display explicitly labels metrics as those of the current model, including
 * shared models, rather than inventing role attribution absent from the fold.
 */
export function partyFromState(state, now = 0) {
  const currentRoom = state.party_room || state.active_room;
  const room = (state.rooms || []).find(room => room.id === currentRoom);
  return { lives: state.lives || 3, prices: [...(state.prices || [])],
    room: currentRoom || null, attempt: room?.attempt || 1,
    failed: room?.failed_attempts ?? Math.max(0, (room?.attempt || 1) - 1 + (room?.state === "failed" ? 1 : 0)),
    models: (state.models || []).map(model => ({...model})),
    members: ROLES.map(role => {
      const member = (state.party || []).find(member => member.role === role);
      const remaining = member?.status === "cooldown" ? member.cooldown_seconds || 0 : 0;
      const duration = member?.cooldown_duration || remaining;
      return {role, model: member?.model || "—", status: member?.status || "idle",
        start: now - (duration - remaining) * 1000, duration, until: now + remaining * 1000};
    }) };
}

export function projectParty(state, event, now = 0) {
  if (event.type === "run_started") return {...partyFromState({}, now), lives: event.lives || 3, prices: [...(event.prices || [])]};
  const next = {...state, members: state.members.map(member => ({...member})), models: state.models.map(model => ({...model}))};
  const statuses = eventStatuses(event);
  for (const member of next.members) {
    if (statuses[member.role]) {
      member.status = statuses[member.role];
      member.until = now; member.duration = 0;
    }
    if (event.role === member.role) {
      if (event.type === "fallback") member.model = event.to_model;
      else if (event.model) member.model = event.model;
      if (event.type === "cooldown") {
        member.start = now; member.duration = event.seconds;
        member.until = now + event.seconds * 1000;
      }
    }
  }
  if (event.type === "model_call") {
    let model = next.models.find(model => model.model === event.model);
    if (!model) { model = {model: event.model, tokens_in: 0, tokens_out: 0}; next.models.push(model); }
    model.tokens_in += event.tokens_in || 0; model.tokens_out += event.tokens_out || 0;
  }
  if (event.type === "room_entered" && event.room_id !== next.room) {
    next.room = event.room_id; next.attempt = 1; next.failed = 0;
  }
  if (event.type === "attempt_started" || event.type === "respawn") {
    if (event.room_id !== next.room || event.attempt === 1) next.failed = 0;
    next.room = event.room_id; next.attempt = event.attempt;
    next.failed = Math.max(next.failed, event.attempt - 1);
  }
  if (event.type === "room_failed" || (event.type === "review" && event.verdict === "fail")) {
    next.failed = Math.max(next.failed, next.attempt);
  }
  return next;
}

export function partyView(state, now = 0) {
  // Limiter availability is absent from the event catalog. Deterministic estimate:
  // shared stamina = 0 at cooldown receipt, linear recovery to 100 at expiry;
  // overlapping cooldowns take the lowest recovery. Never claim real RPM budget.
  const stamina = Math.round(100 * Math.min(1, ...state.members.filter(member => member.until > now)
    .map(member => Math.max(0, (now - member.start) / (member.duration * 1000)))));
  return {lives: Math.max(0, state.lives - state.failed), maximum: state.lives, stamina,
    members: state.members.map(member => {
      const countdown = Math.max(0, Math.ceil((member.until - now) / 1000));
      const model = state.models.find(model => model.model === member.model);
      const input = model?.tokens_in || 0, output = model?.tokens_out || 0;
      const price = state.prices.find(price => price.model === member.model);
      const metrics = price ? `${((input * Number(price.input_per_mtok) + output * Number(price.output_per_mtok)) / 1000000).toFixed(6)} ${price.currency} coins`
        : `${input + output} tokens`;
      return {...member, countdown, metrics,
        status: member.status === "cooldown" && !countdown ? "thinking" : member.status};
    })};
}

/** Palette-only DOM panels; textContent keeps recorded summaries inert. */
export function panel(document, id, title) {
  const element = document.createElement("section"); element.id = id;
  element.setAttribute("aria-label", title);
  Object.assign(element.style, {fontFamily: '"Press Start 2P", monospace', fontSize: "10px", lineHeight: "1.8",
    color: UI_COLORS.text, backgroundColor: UI_COLORS.surface, border: `2px solid ${UI_COLORS.border}`,
    padding: "12px", margin: "12px 0", overflowWrap: "anywhere"});
  return element;
}
export function label(document, tag, text) {
  const element = document.createElement(tag); element.textContent = text; return element;
}

export function mountParty(host, browser = window) {
  const document = browser.document;
  const element = panel(document, "party-hud", "Party"); host.append(element);
  // Browser animation frames are the UI Clock. Inject the browser for fake time;
  // no wall-clock timestamps or waits are used in projections.
  let now = browser.performance.now(), state = partyFromState({}, now), frame;
  function render() {
    const view = partyView(state, now);
    element.replaceChildren(label(document, "h2", "Party"));
    const columns = document.createElement("div");
    Object.assign(columns.style, {display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: "12px"});
    for (const member of view.members) {
      const column = document.createElement("div");
      column.append(label(document, "h3", member.role[0].toUpperCase() + member.role.slice(1)),
        label(document, "p", member.model), label(document, "p", member.status + (member.countdown ? ` · ${member.countdown}s` : "")));
      const meter = document.createElement("div");
      meter.setAttribute("role", "progressbar"); meter.setAttribute("aria-label", "Estimated shared stamina (cooldown recovery)");
      for (const [name, value] of Object.entries({"aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": view.stamina})) meter.setAttribute(name, value);
      Object.assign(meter.style, {height: "8px", border: `2px solid ${UI_COLORS.accent}`, backgroundColor: UI_COLORS.background});
      const fill = document.createElement("div"); Object.assign(fill.style, {width: `${view.stamina}%`,height: "100%",backgroundColor: PALETTE.G}); meter.append(fill);
      column.append(meter, label(document, "small", "Stamina · estimated"), label(document, "p", `Model: ${member.metrics}`));
      const hearts = label(document, "p", "♥".repeat(view.lives) + "♡".repeat(view.maximum - view.lives));
      hearts.style.color = PALETTE.R; hearts.setAttribute("aria-label", `Lives: ${view.lives} of ${view.maximum}`);
      column.append(hearts); columns.append(column);
    }
    element.append(columns);
  }
  function schedule() {
    if (frame !== undefined) browser.cancelAnimationFrame(frame);
    frame = undefined;
    if (state.members.some(member => member.until > now)) frame = browser.requestAnimationFrame(tick);
  }
  function tick(timestamp) { now = timestamp; frame = undefined; render(); schedule(); }
  const onState = ({detail}) => { now = browser.performance.now(); state = partyFromState(detail, now); render(); schedule(); };
  const onEvent = ({detail}) => { now = browser.performance.now(); state = projectParty(state, detail, now); render(); schedule(); };
  browser.addEventListener("specvania:state", onState); browser.addEventListener("specvania:event", onEvent);
  render();
  return {destroy() {
    if (frame !== undefined) browser.cancelAnimationFrame(frame);
    browser.removeEventListener("specvania:state", onState); browser.removeEventListener("specvania:event", onEvent); element.remove();
  }};
}
