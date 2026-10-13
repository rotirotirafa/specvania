import { t } from "../i18n/index.js";
import { expeditionDialog } from "./pause.js";

/** Metric projection uses the same model/room event rules as run/state.py.
 * A reconnect seeds it from RunState; only newer sequence numbers are folded.
 * Time is model latency, the time reported in summary.md, not wall-clock time.
 */
function project(state, event) {
  if (event.type === "run_started") state = {rooms: [], models: [], prices: event.prices || [], status: "running"};
  state = {...state, run_id: event.run_id || state.run_id, seq: event.seq || state.seq};
  if (event.type === "world_loaded") state.rooms = event.rooms.map(room => ({...room, state: "locked"}));
  if (["model_call", "fallback"].includes(event.type)) {
    const id = event.type === "fallback" ? event.from_model : event.model;
    const models = new Map((state.models || []).map(model => [model.model, {...model}]));
    const model = models.get(id) || {model: id, calls: 0, tokens_in: 0, tokens_out: 0, latency: 0, fallbacks: 0};
    if (event.type === "fallback") model.fallbacks++;
    else {model.calls++; model.tokens_in += event.tokens_in; model.tokens_out += event.tokens_out; model.latency += event.latency;}
    models.set(id, model); state.models = [...models.values()];
  }
  const roomState = {room_cleared: "cleared", room_failed: "failed", room_skipped: "skipped", room_entered: "active", attempt_started: "active", respawn: "active"}[event.type];
  if (roomState) {
    const rooms = new Map((state.rooms || []).map(room => [room.id, room]));
    rooms.set(event.room_id, {...(rooms.get(event.room_id) || {id: event.room_id}), state: roomState,
      ...(event.type === "room_cleared" ? {commit: event.commit} : {})}); state.rooms = [...rooms.values()];
  }
  if (event.type === "run_finished") state.status = event.status;
  return state;
}

/** Decimal reference rates stay exact even below one millionth of a coin. */
function coins(price, model) {
  const rates = [price.input_per_mtok, price.output_per_mtok].map(value => {
    const [mantissa, exponent = "0"] = String(value).toLowerCase().split("e");
    const [whole, fraction = ""] = mantissa.split(".");
    return {amount: BigInt(whole + fraction), scale: fraction.length - Number(exponent)};
  });
  const scale = Math.max(0, ...rates.map(rate => rate.scale));
  const amounts = rates.map(rate => rate.amount * 10n ** BigInt(scale - rate.scale));
  const sum = amounts[0] * BigInt(model.tokens_in || 0) + amounts[1] * BigInt(model.tokens_out || 0);
  const digits = String(sum).padStart(scale + 7, "0"), places = scale + 6;
  const fraction = digits.slice(-places).replace(/0+$/, "");
  return digits.slice(0, -places) + (fraction ? `.${fraction}` : "");
}

export function mountEnd(socket, {browser = window} = {}) {
  const dialog = expeditionDialog("end", {browser});
  let state = {rooms: [], models: [], prices: []};
  function show() {
    const snapshot = state;
    dialog.open("end.title", (content, actions, ui) => {
      ui.text(content, "h2", () => t("end.title"));
      ui.text(content, "p", () => t("end.run", {run: snapshot.run_id, status: t(`status.${snapshot.status}`)}));
      const grid = browser.document.createElement("div"); grid.className = "end-metrics"; content.append(grid);
      const sum = field => (snapshot.models || []).reduce((total, model) => total + (model[field] || 0), 0);
      const metrics = {cleared: snapshot.rooms.filter(room => room.state === "cleared").length,
        failed: snapshot.rooms.filter(room => room.state === "failed").length,
        skipped: snapshot.rooms.filter(room => room.state === "skipped").length,
        tokens_in: sum("tokens_in"), tokens_out: sum("tokens_out"), calls: sum("calls"), fallbacks: sum("fallbacks"),
        latency: Number(sum("latency").toPrecision(6))};
      for (const [metric, value] of Object.entries(metrics)) {
        const cell = browser.document.createElement("div"); grid.append(cell);
        ui.text(cell, "p", () => t(`end.${metric}`));
        const number = ui.text(cell, "strong", () => t("end.number", {value})); number.dataset.metric = metric;
      }
      for (const model of snapshot.models || []) {
        const price = (snapshot.prices || []).find(price => price.model === model.model && /^https?:\/\//.test(price.source_url || ""));
        if (price) ui.text(content, "p", () => t("end.coins", {model: model.model, value: coins(price, model), currency: price.currency,
          source: price.source_url, date: price.checked_on || ""}));
      }
      const commits = browser.document.createElement("div"); commits.className = "end-commits"; commits.hidden = true; content.append(commits);
      const cleared = snapshot.rooms.filter(room => room.state === "cleared" && room.commit);
      if (!cleared.length) ui.text(commits, "p", () => t("end.noCommits"));
      const commitButtons = cleared.map(room => ui.button(commits, "end.commit", () => {
        dialog.close(); browser.dispatchEvent(new (browser.CustomEvent || globalThis.CustomEvent)("specvania:room-selected", {detail: {room_id: room.id}}));
      }, {room: room.id, commit: room.commit.slice(0, 7)}));
      ui.button(actions, "end.commits", () => {commits.hidden = !commits.hidden; if (!commits.hidden) commitButtons[0]?.focus();});
      ui.button(actions, "end.back", () => dialog.close());
    }, {onEscape: () => dialog.close()});
  }
  const message = ({data}) => {
    const value = JSON.parse(data);
    if (value.kind === "state") {
      state = {...value.state, rooms: value.state.rooms || [], models: value.state.models || [], prices: value.state.prices || []};
      if (["finished", "aborted"].includes(state.status)) show(); else dialog.close();
    } else if (value.kind === "event") {
      const event = value.event;
      if (event.run_id && event.run_id !== state.run_id) {state = {rooms: [], models: [], prices: []}; dialog.close();}
      if (event.seq && state.seq && event.seq <= state.seq) return;
      state = project(state, event);
      if (event.type === "run_finished") show();
      else if (["run_started", "world_loaded"].includes(event.type)) dialog.close();
    }
  };
  socket.addEventListener("message", message);
  return {destroy() {socket.removeEventListener?.("message", message); dialog.destroy();}};
}
