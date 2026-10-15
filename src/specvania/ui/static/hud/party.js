import { t, onLanguageChange, setLanguage, i18n } from "../i18n/index.js";
import { createRunningIndicator } from "./running.js";
import { textureKey } from "../pixel/textures.js";
import { UI_COLORS, PALETTE } from "../pixel/palette.js";

const ROLES = Object.freeze(["mage", "warrior", "paladin"]);

/** Event → status table (asserted independently in tests/ui/hud.mjs):
 * room_entered → Warrior thinking, Mage/Paladin idle (resume from a pause)
 * attempt_started / respawn / tool_refused → Warrior thinking
 * model_requested / model_call / fallback → event.role thinking
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
 * model_requested marks each provider attempt after limiter/backoff waits.
 * Only model_call contributes call, token, latency and coin metrics.
 * Everything else preserves status; older streams keep the same mapping.
 */
export function eventStatuses(event) {
  switch (event.type) {
    case "room_entered": return {warrior: "thinking", mage: "idle", paladin: "idle"};
    case "attempt_started": case "respawn": case "tool_refused": return {warrior: "thinking"};
    case "model_requested": case "model_call": case "fallback": return {[event.role]: "thinking"};
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
  return { run_id: state.run_id || null, lives: state.lives || 3, prices: [...(state.prices || [])],
    token_budget: state.token_budget ?? null,
    tokens_spent: state.tokens_spent ?? (state.models || []).reduce((sum, model) => sum + model.tokens_in + model.tokens_out, 0),
    room: currentRoom || null, attempt: room?.attempt || 1,
    failed: room?.failed_attempts ?? Math.max(0, (room?.attempt || 1) - 1 + (room?.state === "failed" ? 1 : 0)),
    models: (state.models || []).map(model => ({...model})),
    members: ROLES.map(role => {
      const member = (state.party || []).find(member => member.role === role);
      const remaining = member?.status === "cooldown" ? member.cooldown_seconds || 0 : 0;
      const duration = member?.cooldown_duration || remaining;
      return {role, model: member?.model || t("party.noModel"), status: member?.status || "idle",
        tokens_spent: state.tokens_spent_by_role?.[role] || 0,
        thinkingSince: member?.status === "thinking" ? now : null,
        start: now - (duration - remaining) * 1000, duration, until: now + remaining * 1000};
    }) };
}

export function projectParty(state, event, now = 0) {
  if (event.type === "run_started") return partyFromState(event, now);
  const next = {...state, members: state.members.map(member => ({...member})), models: state.models.map(model => ({...model}))};
  const statuses = eventStatuses(event);
  for (const member of next.members) {
    if (statuses[member.role]) {
      member.status = statuses[member.role];
      member.thinkingSince = member.status === "thinking" ? (Number.isFinite(Date.parse(event.ts)) ? Date.parse(event.ts) : now) : null;
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
    const spent = (event.tokens_in || 0) + (event.tokens_out || 0);
    next.tokens_spent += spent;
    const member = next.members.find(member => member.role === event.role);
    if (member) member.tokens_spent += spent;
    let model = next.models.find(model => model.model === event.model);
    if (!model) { model = {model: event.model, tokens_in: 0, tokens_out: 0}; next.models.push(model); }
    model.tokens_in += event.tokens_in || 0; model.tokens_out += event.tokens_out || 0;
  }
  if (event.type === "stamina_restored") next.token_budget = event.token_budget;
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
  const stamina = state.token_budget === null ? 100
    : Math.round(100 * Math.max(0, 1 - state.tokens_spent / state.token_budget));
  const staminaText = state.token_budget === null ? t("party.tokens", {spent: state.tokens_spent})
    : t("party.budget", {spent: state.tokens_spent, budget: state.token_budget});
  return {lives: Math.max(0, state.lives - state.failed), maximum: state.lives, stamina,
    staminaText,
    members: state.members.map(member => {
      const countdown = Math.max(0, Math.ceil((member.until - now) / 1000));
      const model = state.models.find(model => model.model === member.model);
      const input = model?.tokens_in || 0, output = model?.tokens_out || 0;
      const price = state.prices.find(price => price.model === member.model && /^https?:\/\//.test(price.source_url || ""));
      const metrics = price ? t("party.coins", {value: ((input * Number(price.input_per_mtok) + output * Number(price.output_per_mtok)) / 1000000).toFixed(6), currency: price.currency})
        : t("party.tokens", {spent: input + output});
      return {...member, thinkingSince: member.status === "cooldown" && !countdown ? member.until : member.thinkingSince, countdown, metrics, source: price?.source_url, priced: Boolean(price),
        status: member.status === "cooldown" && !countdown ? "thinking" : member.status};
    })};
}

/** Palette-only DOM panels; textContent keeps recorded summaries inert. */
export function panel(document, id, title) {
  const element = document.createElement("section"); element.id = id;
  element.setAttribute("aria-label", title);
  Object.assign(element.style, {fontFamily: '"Press Start 2P", monospace', fontSize: "8px", lineHeight: "1.6",
    color: UI_COLORS.text, backgroundColor: UI_COLORS.surface, border: `2px solid ${UI_COLORS.border}`,
    padding: "6px", margin: "6px 0", overflowWrap: "normal", wordBreak: "normal"});
  return element;
}
export function label(document, tag, text) {
  const element = document.createElement(tag); element.textContent = text; return element;
}

export function mountParty(host, browser = window, timing = {}) {
  const document = browser.document;
  const element = panel(document, "party-hud", t("party.title")); host.append(element);
  const running = createRunningIndicator(browser, () => {now = running.now(); render();}, timing);
  let now = running.now(), state = partyFromState({}, now), frame;
  let textures;
  const switcher = document.createElement("nav");
  const buttons = ["pt-BR", "en"].map(language => {
    const button = document.createElement("button"); button.type = "button";
    Object.assign(button.style, { font: "inherit", color: UI_COLORS.text, backgroundColor: UI_COLORS.background,
      border: `2px solid ${UI_COLORS.accent}`, padding: "6px", cursor: "pointer" });
    button.addEventListener("click", () => setLanguage(language, {manual: true}));
    if (language === "en") switcher.append(label(document, "span", t("language.separator")));
    switcher.append(button); return button;
  });
  function render() {
    const view = partyView(state, now);
    running.active(view.members.some(member => member.status === "thinking"));
    element.setAttribute("aria-label", t("party.title"));
    switcher.setAttribute("aria-label", t("language.title"));
    for (const [index, key] of ["language.pt", "language.en"].entries()) {
      buttons[index].textContent = t(key);
      buttons[index].setAttribute("aria-pressed", i18n.language === (index ? "en" : "pt-BR"));
    }
    element.replaceChildren(switcher);
    const hearts = label(document, "p", t("party.hearts", {full: "♥".repeat(view.lives), empty: "♡".repeat(view.maximum - view.lives)}));
    hearts.style.color = PALETTE.R; hearts.setAttribute("aria-label", t("party.lives", view));
    element.append(hearts, label(document, "p", state.token_budget === null ? view.staminaText : t("party.staminaValue", {value: view.staminaText})));
    if (state.token_budget !== null) {
      const meter = document.createElement("div");
      meter.setAttribute("role", "progressbar"); meter.setAttribute("aria-label", t("party.stamina"));
      for (const [name, value] of Object.entries({"aria-valuemin": 0, "aria-valuemax": state.token_budget,
        "aria-valuenow": Math.min(state.tokens_spent, state.token_budget), "aria-valuetext": view.staminaText})) meter.setAttribute(name, value);
      Object.assign(meter.style, {height: "8px", border: `2px solid ${UI_COLORS.accent}`, backgroundColor: UI_COLORS.background});
      const fill = document.createElement("div"); Object.assign(fill.style, {width: `${100 - view.stamina}%`, height: "100%", backgroundColor: PALETTE.G}); meter.append(fill);
      element.append(meter);
    }
    for (const member of view.members) {
      const row = document.createElement("div");
      Object.assign(row.style, {display: "grid", gridTemplateColumns: "32px minmax(0, 1fr)", gap: "6px", marginTop: "8px"});
      row.dataset.role = member.role;
      row.style.backgroundColor = member.status === "thinking" ? UI_COLORS.background : "";
      const portrait = document.createElement("canvas"); portrait.width = 32; portrait.height = 32;
      portrait.setAttribute("role", "img"); portrait.setAttribute("aria-label", t(`role.${member.role}`));
      portrait.style.imageRendering = "pixelated";
      const source = textures?.get(textureKey(member.role, "idle"))?.getSourceImage();
      const context = portrait.getContext?.("2d");
      if (source && context) { context.imageSmoothingEnabled = false; context.drawImage(source, 0, 0, 16, 16, 0, 0, 32, 32); }
      const details = document.createElement("div");
      const name = label(document, "p", member.model); name.title = member.model;
      Object.assign(name.style, {whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", margin: "2px 0"});
      details.append(label(document, "strong", t(`role.${member.role}`)), name,
        label(document, "p", member.status === "thinking" ? running.text(t("status.thinking"), member.thinkingSince) : member.countdown ? t("party.countdown", {status: t(`status.${member.status}`), seconds: member.countdown}) : t(`status.${member.status}`)),
        label(document, "p", t("party.spent", {spent: member.tokens_spent})));
      if (member.priced) {
        const source = label(document, "small", t("party.sourceLabel"));
        source.title = t("party.source", {url: member.source});
        source.setAttribute("aria-label", source.title);
        details.append(label(document, "p", t("party.modelCoins", {value: member.metrics})), source);
      }
      row.append(portrait, details); element.append(row);
    }
  }
  const onTextures = ({detail}) => { textures = detail; render(); };
  browser.addEventListener("specvania:textures-ready", onTextures);
  const unsubscribe = onLanguageChange(render);
  function schedule() {
    if (frame !== undefined) browser.cancelAnimationFrame(frame);
    frame = undefined;
    if (state.members.some(member => member.until > now)) frame = browser.requestAnimationFrame(tick);
  }
  function tick() { now = running.now(); frame = undefined; render(); schedule(); }
  const onState = ({detail}) => {
    running.playback(detail.replay); now = running.now();
    const restored = partyFromState(detail, now);
    if (restored.run_id === state.run_id) for (const member of restored.members) {
      const previous = state.members.find(value => value.role === member.role);
      if (member.status === "thinking" && previous?.status === "thinking" && member.model === previous.model) member.thinkingSince = previous.thinkingSince;
    }
    state = restored; render(); schedule();
  };
  const onEvent = ({detail}) => {
    const shift = running.event(detail);
    // A replay snapshot may precede its first recorded timestamp. Carry waits
    // learned from that snapshot into the recording's time coordinates.
    if (shift) state = {...state, members: state.members.map(member => ({...member,
      thinkingSince: member.thinkingSince === null ? null : member.thinkingSince + shift,
      start: member.start + shift, until: member.until + shift}))};
    now = running.now(); state = projectParty(state, detail, now); render(); schedule();
  };
  browser.addEventListener("specvania:state", onState); browser.addEventListener("specvania:event", onEvent);
  const onStudio = ({detail}) => {running.playback(detail.replay); now = running.now(); render();};
  browser.addEventListener("specvania:studio-state", onStudio);
  render();
  return {destroy() {
    running.destroy();
    browser.removeEventListener("specvania:studio-state", onStudio);
    unsubscribe();
    browser.removeEventListener("specvania:textures-ready", onTextures);
    if (frame !== undefined) browser.cancelAnimationFrame(frame);
    browser.removeEventListener("specvania:state", onState); browser.removeEventListener("specvania:event", onEvent); element.remove();
  }};
}
