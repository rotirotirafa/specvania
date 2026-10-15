import { keysFor } from "./keys.js";

// Lines are translation descriptors. Model prose/paths are inert parameters;
// nested descriptors allow reusable wording without baking in a language.
const text = (key, params = {}) => ({key, params});
const join = (first, second) => first ? text("play.join", {first, second}) : second;

/** A renderer may use its own i18n service; this helper performs no I/O. */
export function renderLine(line, t) {
  if (!line) return "";
  return t(line.key, Object.fromEntries(Object.entries(line.params).map(([key, value]) =>
    [key, value && typeof value === "object" && value.key ? renderLine(value, t) : value])));
}

function pauseReason(reason = "") {
  const known = {"stamina exhausted":"pause.stamina", "out of lives":"pause.lives", "model chain exhausted":"pause.chain", "boss failed":"pause.boss"};
  if (Object.hasOwn(known, reason.toLowerCase())) return text(known[reason.toLowerCase()]);
  let match = /^Out of lives in (T\d+): ([\s\S]*)$/.exec(reason);
  if (match) return text("pause.livesDetail", {room:match[1], feedback:match[2]});
  match = /^(mage|warrior|paladin) model chain exhausted; retry or change the chain\.$/.exec(reason);
  if (match) return text("pause.chainDetail", {role:text(`role.${match[1]}`)});
  match = /^Boss (A\d+): ([\s\S]*)$/.exec(reason);
  if (match) return text("pause.bossDetail", {area:match[1], feedback:match[2]});
  return text("pause.reason", {reason});
}

function approvalTurn(event) {
  return {kind:"approval", request_id:event.request_id, command:event.command, cwd:event.cwd,
    choices:[{id:"approve", label:text("approval.allow")}, {id:"deny", label:text("approval.deny")}, {id:"view", label:text("play.viewCommand")}], selected:0};
}
function clarifyTurn(event) {
  const options = event.options || [];
  return {kind:"clarify", question_id:event.question_id, question:event.text ?? event.question, default:event.default,
    choices:options.map(value => ({id:value, value, label:text("idea.option", {option:value})})),
    selected:Math.max(0, options.indexOf(event.default))};
}
function pauseTurn(reason) {
  const ids = reason === "stamina exhausted" ? ["rest", "abort"] : ["retry", "skip", "abort"];
  return {kind:"pause", reason, choices:ids.map(id => ({id, value:id === "rest" ? "retry" : id, label:text(`pause.${id}`)})), selected:0};
}

function eventLine(event, replay) {
  const e = event, result = text(e.passed ? "result.passed" : "result.failed");
  switch (e.type) {
    case "action": {
      const known = ["read_file", "list_dir", "search", "write_file", "edit_file", "run_shell", "finish"].includes(e.action);
      const action = e.action === "finish" ? text("dialogue.finished") : known ? text(`play.action.${e.action}`) : text("play.action.unknown", {action:e.action});
      const line = e.args_summary ? text("dialogue.action", {action, args:e.args_summary}) : action;
      return ["warrior", e.thought ? join(text("idea.question", {text:e.thought}), line) : line];
    }
    case "review": return ["paladin", text("idea.question", {text:e.feedback || ""})];
    case "approval_requested": return ["warrior", text("approval.request", {command:e.command, cwd:text("approval.cwd", {cwd:e.cwd}), waiting:text(replay ? "replay.readonly" : "approval.waiting")})];
    case "approval_answered": {
      const responder = ["terminal", "desktop window"].includes(e.responder) ? text(`responder.${e.responder}`) : e.responder;
      return ["warrior", !e.answer && e.reason ? text("approval.deniedReason", {responder, reason:e.reason}) :
        text("approval.result", {answer:text(e.answer ? "approval.approved" : "approval.denied"), responder})];
    }
    case "clarify_question": return ["mage", text("idea.question", {text:e.question ?? e.text})];
    case "clarify_answer": return ["mage", text(replay ? "replay.answer" : "play.answer", {answer:e.answer})];
    case "gate_result": return ["paladin", text("panel.gate", {command:e.command, code:e.exit_code, output:e.output_summary})];
    case "bug_opened": return ["narrator", text("play.bugOpened", {id:e.bug_id, title:e.title, body:e.body || ""})];
    case "bug_closed": return ["narrator", text("play.bugClosed", {id:e.bug_id, status:text(e.status === "wontfix" ? "play.wontfix" : "play.fixed")})];
    case "boss_scenario": return ["paladin", text("play.bossScenario", {area:e.area_id, scenario:e.scenario, result, reason:e.reason})];
    case "boss_started": return ["paladin", text("boss.area", {area:e.area_id})];
    case "boss_result": return ["paladin", text("play.bossResult", {area:e.area_id, result})];
    case "room_cleared": return ["narrator", text("play.roomCleared", {room:e.room_id, commit:e.commit})];
    case "room_failed": case "room_skipped": return ["narrator", text(e.type === "room_failed" ? "play.roomFailed" : "play.roomSkipped", {room:e.room_id, reason:e.reason})];
    case "run_paused": return ["narrator", text("play.runPaused", {reason:pauseReason(e.reason)})];
    case "run_finished": return ["narrator", text("play.runFinished", {status:text(e.status === "aborted" ? "status.aborted" : "status.finished")})];
    case "stamina_restored": return ["narrator", text("play.staminaRestored", {budget:e.token_budget})];
    case "run_started": return ["narrator", text("play.runStarted", {feature:e.feature})];
    case "spec_written": return ["mage", text("idea.ready", {world:e.feature})];
    case "room_entered": return ["warrior", text("play.roomEntered", {room:e.room_id})];
    case "attempt_started": return ["warrior", text("play.attemptStarted", {room:e.room_id, attempt:e.attempt})];
    case "respawn": return ["paladin", text("play.respawn", {room:e.room_id, attempt:e.attempt, feedback:e.feedback})];
    case "tool_refused": return ["warrior", text("play.toolRefused", {tool:e.tool, reason:e.reason})];
    case "cooldown": return [e.role, text("play.cooldown", {seconds:e.seconds, reason:e.reason})];
    case "fallback": return [e.role, text("play.fallback", {model:e.to_model, reason:e.reason})];
    // model_requested/model_call and world_loaded are metadata, not real prose.
    default: return null;
  }
}

/** Fold a snapshot and its subsequent ordered events. studio is studio_state,
 * state is RunState, approvalRequest is the envelope's full transient command.
 * For reconnect, pass studio.replay (ReplayStatus), including its dialogue.
 * No control secret is returned. Live watch views keep the pending choices,
 * with keys that answer them advertised only when controlToken is present.
 */
export function narrate({studio = {}, state = {}, approvalRequest = null, events = [], replay = false, controlToken = "", screen = ""} = {}) {
  const recorded = Boolean(replay || studio.replay || studio.phase === "replaying");
  let speaker = null, line = null, turn = null, paused = null;
  function apply(event) {
    const next = eventLine(event, recorded);
    if (next) [speaker, line] = next;
    if (paused && event.type !== "run_paused") {
      const choice = {attempt_started:"retry", room_skipped:"skip", stamina_restored:"rest", boss_started:"retry", run_finished:"abort"}[event.type];
      if (recorded && choice) {
        speaker = "narrator";
        line = join(text("replay.choice", {choice:text(`pause.${choice}`), reason:pauseReason(paused)}), line);
      }
    }
    if (["run_started", "world_loaded", "stamina_restored", "attempt_started", "room_entered", "room_skipped", "boss_started", "run_finished"].includes(event.type)) {
      paused = null;
      if (turn?.kind === "pause" || event.type === "run_finished" || event.type === "run_started") turn = null;
    }
    if (event.type === "approval_requested") turn = approvalTurn(event);
    else if (event.type === "approval_answered" && turn?.kind === "approval" && event.request_id === turn.request_id) turn = null;
    else if (event.type === "clarify_question") turn = clarifyTurn(event);
    else if (event.type === "clarify_answer" && turn?.kind === "clarify" && event.question_id === turn.question_id) turn = null;
    else if (event.type === "spec_written" && turn?.kind === "clarify") turn = null;
    else if (event.type === "run_paused") {paused = event.reason; turn = pauseTurn(event.reason);}
  }
  if (state.dialogue) apply(state.dialogue);
  if (studio.replay?.dialogue) apply(studio.replay.dialogue);
  if (studio.replay?.stream === "plan" && state.feature) apply({type:"spec_written", feature:state.feature});
  if (approvalRequest) apply({type:"approval_requested", ...approvalRequest});
  if (studio.clarify_request) apply({type:"clarify_question", ...studio.clarify_request});
  if (studio.pause_request != null || state.status === "paused") apply({type:"run_paused", reason:studio.pause_request ?? state.reason ?? ""});
  if (studio.phase === "finished" || ["finished", "aborted"].includes(state.status)) apply({type:"run_finished", status:state.status});
  for (const event of events) {
    // Prefer the transient full prompt over the ledger's bounded summary.
    apply(event.type === "approval_requested" && approvalRequest?.request_id === event.request_id ? {...event, ...approvalRequest} : event);
  }
  if (recorded) turn = null;
  const canControl = Boolean(controlToken) && !recorded;
  return {speaker, line, turn, replay:recorded, screen, keys:keysFor({turn, replay:recorded, canControl, screen})};
}
