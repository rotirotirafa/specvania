/** Keys are descriptions, not listeners: target is plain {tagName,
 * isContentEditable, contentEditable}, including inherited editability. The
 * caller translates its DOM event to this shape. No DOM or token is retained.
 */
function editable(target = {}) {
  return /^(input|textarea|select)$/i.test(target.tagName || "") || target.isContentEditable ||
    ["true", "plaintext-only"].includes(target.contentEditable);
}

function castle(screen) {
  return ["castle", "map", "world", "running", "approval", "pause", "boss", "replay"].includes(screen);
}

/** Keys valid now, including replay keys delegated to its existing controls. */
export function keysFor({turn = null, replay = false, canControl = false, screen = "", helpOpen = false} = {}) {
  const keys = ["?", "F1", "Escape"];
  if (helpOpen) return [...keys, "Tab"];
  if (castle(screen)) keys.push("M", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown");
  if (replay) return [...keys, "1", "2", "4", "Space"];
  if (!turn) return keys;
  if (turn.kind === "approval") return [...keys, ...(canControl ? ["A", "N"] : []), "V"];
  if (turn.choices.length) for (const key of ["ArrowUp", "ArrowDown"]) if (!keys.includes(key)) keys.push(key);
  if (canControl) keys.push("Enter");
  if (canControl) for (let i = 1; i <= turn.choices.length; i++) keys.push(String(i));
  return keys;
}

/** Returns null, {control: existing protocol message}, a UI {action,...}, or
 * {selection: index}. Apply selection to turn.selected before the next key;
 * this is local selection state, never a new studio message or UI action kind.
 * N opens the reason editor; its submit button uses T20's denial validation.
 */
export function mapKey(event, narrator, {controlToken = "", replay = false, helpOpen = false} = {}) {
  if (editable(event.target) || event.isComposing || event.keyCode === 229 || event.repeat ||
      event.ctrlKey || event.metaKey || event.altKey) return null;
  const key = event.key, upper = key?.toUpperCase();
  if (key === "?" || key === "F1") return {action:"help"};
  if (key === "Escape") return {action:"close"};
  if (helpOpen) return key === "Tab" ? {action:"all-keys"} : null;
  const recorded = Boolean(replay || narrator.replay);
  if (recorded && ["1", "2", "4", " ", "Space"].includes(key)) return null;
  const turn = recorded ? null : narrator.turn;
  if (turn?.kind === "approval") {
    if (upper === "V") return {action:"view-command", command:turn.command, cwd:turn.cwd};
    if (upper === "N") return controlToken ? {action:"deny-reason", request_id:turn.request_id} : null;
    if (upper === "A") return controlToken ? {control:{kind:"approval_answer", request_id:turn.request_id, answer:true, control_token:controlToken}} : null;
  } else if (turn) {
    const choices = turn.choices;
    if (choices.length && ["ArrowUp", "ArrowDown"].includes(key)) {
      return {selection:(turn.selected + (key === "ArrowDown" ? 1 : -1) + choices.length) % choices.length};
    }
    const index = key === "Enter" ? turn.selected : /^[1-9]\d*$/.test(key) ? Number(key) - 1 : -1;
    const answer = choices[index]?.value ?? (key === "Enter" && !choices.length ? turn.default : undefined);
    if (answer !== undefined && controlToken) {
      if (turn.kind === "clarify") return {control:{kind:"clarify_answer", question_id:turn.question_id, answer, control_token:controlToken}};
      if (turn.kind === "pause") return {control:{kind:"pause_decision", choice:answer, control_token:controlToken}};
    }
  }
  if (castle(narrator.screen)) {
    if (upper === "M") return {action:"overview"};
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(key)) return {action:"camera", direction:key.slice(5).toLowerCase()};
  }
  return null;
}
