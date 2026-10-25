import { t, onLanguageChange, i18n } from "../i18n/index.js";
import { narrate, renderLine } from "../play/narrator.js";
import { mapKey } from "../play/keys.js";
import { CAST, TILE_SIZE, SHEET_URL } from "../pixel/cast.js";
import { UI_COLORS, PALETTE } from "../pixel/palette.js";
import { partyFromState, projectParty, partyView } from "./party.js";
import { createRunningIndicator } from "./running.js";

export function portrait(document, role, scale = 2) {
  const node = document.createElement("span"), size = TILE_SIZE * scale;
  const cast = CAST[role] === undefined ? "banner" : role;
  node.dataset.cast = cast; node.setAttribute("aria-hidden", "true");
  Object.assign(node.style, {display:"inline-block", width:`${size}px`, height:`${size}px`, flexShrink:"0",
    imageRendering:"pixelated", backgroundImage:`url("${SHEET_URL}")`, backgroundRepeat:"no-repeat",
    backgroundSize:`${192 * scale}px ${176 * scale}px`, backgroundPosition:`${-(CAST[cast] % 12) * size}px ${-Math.floor(CAST[cast] / 12) * size}px`});
  return node;
}

/** Shared shell for the castle and the Mage's Clarify dialogue. */
export function createDialogueBox(document, box = document.createElement("section")) {
  box.className = "dialogue-box";
  box.setAttribute("role", "status"); box.setAttribute("aria-live", "polite"); box.tabIndex = -1;
  Object.assign(box.style, {boxSizing:"border-box", color:UI_COLORS.text, backgroundColor:UI_COLORS.surface,
    border:`3px solid ${UI_COLORS.border}`, padding:"10px", fontFamily:'"Press Start 2P", monospace',
    fontSize:"10px", lineHeight:"1.7", minWidth:"0", overflow:"hidden", overflowWrap:"anywhere"});
  return box;
}
const editable = node => /^(input|textarea|select)$/i.test(node?.tagName || "") || node?.isContentEditable;

/** One renderer, one castle key listener. The socket is the existing transport;
 * all shortcut and choice clicks pass through the pure T42 key mapping. */
export function mountDialogueBox(socket, {browser = window, document = browser.document, host = document.body, timing = {}} = {}) {
  const box = createDialogueBox(document); box.id = "dialogue-box";
  Object.assign(box.style, {position:"fixed", left:"12px", right:"12px", bottom:"12px", height:"auto", minHeight:"104px", maxHeight:"124px", padding:"8px", fontSize:"9px", lineHeight:"14px", zIndex:"4",
    display:"flex", flexDirection:"column"}); host.append(box);
  const header = document.createElement("header"), body = document.createElement("div"), actions = document.createElement("nav"), thinking = document.createElement("p");
  Object.assign(header.style, {display:"flex", alignItems:"center", gap:"10px", flexShrink:"0"});
  Object.assign(body.style, {minHeight:"0", overflow:"auto", flex:"0 1 auto", maxHeight:"50px", whiteSpace:"pre-wrap"}); body.tabIndex = 0;
  Object.assign(actions.style, {display:"flex", flexWrap:"wrap", gap:"6px", flexShrink:"1", minHeight:"0", overflow:"auto"});
  Object.assign(thinking.style, {minHeight:"14px", margin:"4px 0 0", flexShrink:"0", whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis"});
  box.append(header, body, actions, thinking);
  let studio = {}, state = {}, events = [], approvalRequest = null, narrator = narrate(), selected = 0;
  let signature = "", turnId = "", pending = false, connected = true, reason = null, reasonLabel = null, answer = null, overlay = null;
  let buttons = [], selectedNodes = [];
  const sequences = new Map();
  const token = () => browser.specvaniaDesktop?.controlToken?.() || "";
  const running = createRunningIndicator(browser, renderThinking, timing);
  let party = partyFromState({}, running.now());
  let measuredHeight = -1;
  function measure() {
    const width = Math.max(1, (browser.innerWidth || 960) - 46);
    const chars = Math.max(1, Math.floor(width / 9));
    const lines = (body.children[0]?.textContent || "").split("\n").reduce((sum, line) => sum + Math.max(1, Math.ceil(line.length / chars)), 0);
    // DOM geometry is authoritative. The text estimate also supports hosts
    // without layout (the Node DOM), before the first ResizeObserver delivery.
    const estimate = narrator.turn ? Math.min(Math.floor((browser.innerHeight || 540) * .45), 90 + lines * 14 + buttons.length * 30) : Math.max(104, Math.min(124, 90 + Math.min(3, lines) * 14));
    const height = box.hidden ? 0 : Math.ceil(box.getBoundingClientRect?.().height || estimate);
    box.dataset.height = String(height);
    if (height === measuredHeight) return;
    measuredHeight = height;
    browser.dispatchEvent?.(new (browser.CustomEvent || globalThis.CustomEvent)("specvania:dialogue-size", {detail:{height, bottom:height ? height + 24 : 0}}));
  }
  const sizeObserver = browser.ResizeObserver ? new browser.ResizeObserver(measure) : null;
  sizeObserver?.observe(box);
  browser.addEventListener("resize", measure);
  function renderThinking() {
    const member = partyView(party, running.now()).members.find(member => member.status === "thinking");
    running.active(Boolean(member));
    thinking.textContent = member ? t("box.thinking", {role:t(`role.${member.role}`), text:running.text(t("status.thinking"), member.thinkingSince), model:member.model}) : "";
    measure();
  }
  function closeOverlay() {if (!overlay) return; overlay.remove(); overlay = null; buttons[2]?.focus();}
  function send(control) {
    if (pending || !connected || socket.readyState !== 1 || !token()) return false;
    socket.send(JSON.stringify(control)); pending = true; enable(); return true;
  }
  function enable() {
    for (const [index, button] of buttons.entries()) button.disabled = index !== 2 || narrator.turn?.kind !== "approval" ? pending || !connected || !token() : false;
    if (reason) reason.disabled = pending || !connected;
    if (answer) answer.disabled = pending || !connected || !token();
  }
  function dispatch(result) {
    if (!result) return false;
    if (result.control) send(result.control);
    else if (result.selection !== undefined) {
      selected = result.selection; narrator.turn.selected = selected;
      for (const [i, node] of selectedNodes.entries()) node.setAttribute("aria-selected", String(i === selected));
      selectedNodes[selected]?.focus();
    } else if (result.action === "deny-reason") {
      reason.hidden = reasonLabel.hidden = false; reason.focus();
    } else if (result.action === "view-command") {
      closeOverlay(); overlay = document.createElement("section"); overlay.id = "command-overlay";
      overlay.setAttribute("role", "dialog"); overlay.setAttribute("aria-label", t("play.viewCommand")); overlay.tabIndex = -1;
      Object.assign(overlay.style, {position:"fixed", inset:"100px 24px 224px", zIndex:"9", backgroundColor:UI_COLORS.surface,
        color:UI_COLORS.text, border:`3px solid ${UI_COLORS.accent}`, padding:"12px", overflow:"auto", whiteSpace:"pre-wrap", overflowWrap:"anywhere"});
      const full = document.createElement("pre"); full.textContent = result.command;
      Object.assign(full.style, {whiteSpace:"pre-wrap", overflowWrap:"anywhere"});
      const cwd = document.createElement("p"); cwd.textContent = t("approval.cwd", {cwd:result.cwd});
      const close = document.createElement("button"); close.textContent = t("box.close"); close.addEventListener("click", closeOverlay);
      overlay.append(full, cwd, close); document.body.append(overlay); close.focus();
    } else if (result.action === "close" && overlay) closeOverlay();
    else browser.dispatchEvent?.(new browser.CustomEvent("specvania:play-action", {detail:result}));
    return true;
  }
  function key(key, target = {}) {return dispatch(mapKey({key, target}, narrator, {controlToken:token()}));}
  function deny() {
    if (!reason || reason.value.length > 200) return;
    const control = {kind:"approval_answer", request_id:narrator.turn.request_id, answer:false, control_token:token()};
    if (reason.value.trim()) control.reason = reason.value.trim();
    send(control);
  }
  function button(label, action) {
    const node = document.createElement("button"); node.type = "button"; node.textContent = label;
    Object.assign(node.style, {font:"inherit", color:UI_COLORS.text, backgroundColor:UI_COLORS.background,
      border:`2px solid ${UI_COLORS.border}`, padding:"5px", cursor:"pointer"});
    node.addEventListener("click", () => {if (!node.disabled) {action(); measure();}}); actions.append(node); buttons.push(node); return node;
  }
  function render() {
    const previousTurn = turnId, focused = document.activeElement;
    const draft = reason?.value || "", draftAnswer = answer?.value || "";
    const editingReason = focused === reason, editingAnswer = focused === answer;
    narrator = narrate({studio, state, events, approvalRequest, controlToken:token(), screen:"castle"});
    const turn = narrator.turn;
    turnId = turn ? `${turn.kind}:${turn.request_id || turn.question_id || turn.reason}` : "";
    const changed = previousTurn !== turnId;
    if (changed) {pending = false; selected = turn?.selected || 0; closeOverlay();}
    if (turn) turn.selected = selected;
    box.hidden = Boolean(["idle", "project_open", "planning", "world_ready"].includes(studio.phase) && !narrator.replay);
    // Selection and draft edits belong to this viewer, not to a new snapshot.
    const nextSignature = JSON.stringify({...narrator, turn:turn ? {...turn, selected:0} : null}) + connected + i18n.language;
    if (!changed && signature === nextSignature) {enable(); renderThinking(); return;}
    signature = nextSignature;
    box.style.maxHeight = turn ? "45vh" : "124px";
    body.style.maxHeight = turn ? "none" : "50px";
    body.style.flex = turn ? "1 1 auto" : "0 1 auto";
    header.replaceChildren(); body.replaceChildren(); actions.replaceChildren(); buttons = []; selectedNodes = [];
    reason = reasonLabel = answer = null;
    box.style.borderColor = turn ? PALETTE.Y : UI_COLORS.border;
    box.dataset.turn = turn?.kind || "";
    const speaker = narrator.speaker || "narrator", name = document.createElement("strong");
    name.textContent = t(turn ? "box.yourTurn" : speaker === "narrator" ? "play.narrator" : `role.${speaker}`);
    header.append(portrait(document, speaker), name);
    if (turn) {const roleName = document.createElement("span"); roleName.textContent = t(speaker === "narrator" ? "play.narrator" : `role.${speaker}`); header.append(roleName);}
    const line = document.createElement("p"); line.style.margin = "4px 0";
    line.textContent = turn?.kind === "approval" ? t("approval.title") : renderLine(narrator.line, t) || t("dialogue.awaiting"); body.append(line);
    if (turn?.kind === "approval") {
      const command = document.createElement("p"); command.textContent = turn.command;
      Object.assign(command.style, {whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis", margin:"4px 0"}); body.append(command);
      const cwd = document.createElement("p"); cwd.textContent = t("approval.cwd", {cwd:turn.cwd}); cwd.style.margin = "4px 0"; body.append(cwd);
      for (const [index, choice] of turn.choices.entries()) button(t("box.choice", {key:["A", "N", "V"][index], label:renderLine(choice.label, t)}), () => key(["A", "N", "V"][index]));
      reasonLabel = document.createElement("label"); reasonLabel.textContent = t("approval.reason"); reasonLabel.htmlFor = "approval-denial-reason";
      reason = document.createElement("input"); reason.id = reasonLabel.htmlFor; reason.type = "text"; reason.maxLength = 200;
      reason.value = changed ? "" : draft; reason.hidden = reasonLabel.hidden = changed || !editingReason && !draft;
      reason.setAttribute("aria-label", t("approval.reason"));
      actions.append(reasonLabel, reason); const submit = button(t("box.denySubmit"), deny); submit.hidden = reason.hidden;
      // N reveals the complete reason editor, including its explicit submit.
      const reveal = buttons[1]; reveal.addEventListener("click", () => {submit.hidden = false;});
      reason.addEventListener("keydown", event => {if (event.key === "Enter" && !event.isComposing) {event.preventDefault(); deny();}});
    } else if (turn) {
      if (turn.kind === "pause") for (const choice of turn.choices) {
        const hint = document.createElement("p"); hint.textContent = t(`pause.${choice.id}Help`); body.append(hint);
      }
      for (const [i, choice] of turn.choices.entries()) {
        const node = button(t("box.choice", {key:i + 1, label:renderLine(choice.label, t)}), () => key(String(i + 1)));
        node.setAttribute("aria-selected", String(i === selected)); selectedNodes.push(node);
        node.addEventListener("focus", () => {
          selected = i; turn.selected = i;
          for (const [index, item] of selectedNodes.entries()) item.setAttribute("aria-selected", String(index === i));
        });
      }
      if (turn.choices.length) {const hint = document.createElement("small"); hint.textContent = t("box.pickKeys"); actions.append(hint);}
      if (turn.kind === "clarify") {
        answer = document.createElement("textarea"); answer.value = changed ? "" : draftAnswer;
        answer.setAttribute("aria-label", t("idea.answer")); actions.append(answer);
        button(t("idea.accept"), () => send({kind:"clarify_answer", question_id:turn.question_id,
          answer:answer.value.trim() || turn.choices[selected]?.value || turn.default, control_token:token()}));
      }
    }
    if (turn && !token()) {const hint = document.createElement("p"); hint.textContent = t("approval.watch"); body.append(hint);}
    enable(); renderThinking();
    if (editingReason && reason && !changed) reason.focus();
    else if (editingAnswer && answer && !changed) answer.focus();
    else if (changed && (!editable(focused) || !turn && (editingReason || editingAnswer))) {
      if (turn) (buttons.find(node => !node.disabled) || box).focus();
      else {const castle = document.getElementById?.("world-map"); castle?.focus?.();}
    }
  }
  const message = ({data}) => {
    const value = JSON.parse(data);
    if (value.kind === "state") {
      const snapshot = value.state || {};
      if (!value.replay && snapshot.run_id && Number.isInteger(snapshot.seq) && snapshot.seq < (sequences.get(snapshot.run_id) || 0)) {
        // The transient prompt belongs to the live broker, independently of
        // the older folded run snapshot. Later matching answers still win.
        if (value.approval_request) {approvalRequest = value.approval_request; render();}
        return;
      }
      if (snapshot.run_id && Number.isInteger(snapshot.seq)) sequences.set(snapshot.run_id, snapshot.seq);
      if (!connected) pending = false;
      connected = true; state = value.state || {}; events = []; approvalRequest = value.approval_request || null;
      studio = {...studio, ...(value.phase ? value : {}), replay:value.replay || null};
      running.playback(value.replay);
      const restored = partyFromState(state, running.now());
      if (restored.run_id === party.run_id) for (const member of restored.members) {
        const previous = party.members.find(value => value.role === member.role);
        if (member.status === "thinking" && previous?.status === "thinking" && member.model === previous.model) member.thinkingSince = previous.thinkingSince;
      }
      party = restored;
    } else if (value.kind === "studio_state") {studio = value; running.playback(value.replay);}
    else if (value.kind === "event") {
      const event = value.event;
      if (event.run_id && Number.isInteger(event.seq) && !studio.replay) sequences.set(event.run_id, Math.max(sequences.get(event.run_id) || 0, event.seq));
      const shift = running.event(event);
      if (shift) party = {...party, members:party.members.map(member => ({...member, thinkingSince:member.thinkingSince === null ? null : member.thinkingSince + shift}))};
      party = projectParty(party, event, running.now()); events.push(event);
      if (event.type === "approval_requested") approvalRequest = value.approval_request || event;
      if (event.type === "approval_answered" && approvalRequest?.request_id === event.request_id) approvalRequest = null;
    } else if (value.kind === "approval_rejected") {pending = false; enable(); return;}
    else return;
    render();
  };
  const disconnected = () => {connected = false; enable();};
  const keydown = event => {
    const screen = document.getElementById?.("screen-host");
    if (box.hidden || screen && !screen.hidden || event.defaultPrevented) return;
    if (event.key === "Escape" && !overlay && document.getElementById?.("party-drawer")?.hidden === false) return;
    const result = mapKey(event, narrator, {controlToken:token()});
    if (result && dispatch(result)) {event.preventDefault(); if (result.action === "deny-reason") buttons[3].hidden = false; measure();}
  };
  browser.addEventListener("keydown", keydown);
  socket.addEventListener("message", message); socket.addEventListener("close", disconnected);
  const unsubscribe = onLanguageChange(render); render();
  return {box, get narrator() {return narrator;}, destroy() {sizeObserver?.disconnect(); browser.removeEventListener("resize", measure); closeOverlay(); running.destroy(); unsubscribe(); box.remove();
    browser.removeEventListener("keydown", keydown); socket.removeEventListener?.("message", message); socket.removeEventListener?.("close", disconnected);}};
}
