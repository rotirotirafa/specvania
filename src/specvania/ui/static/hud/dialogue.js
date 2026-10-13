import { panel, label } from "./party.js";

export function dialogueFor(event, control = false) {
  if (!event) return null;
  if (event.type === "action") return {speaker: "Warrior", text: [event.thought, `${event.action}: ${event.args_summary}`].filter(Boolean).join("\n")};
  if (event.type === "review") return {speaker: "Paladin", text: event.feedback || ""};
  if (event.type === "approval_answered") return {speaker: "Warrior", text: `${event.answer ? "approved" : "denied"} in ${event.responder}`};
  if (event.type === "approval_requested") return {speaker: "Warrior", text: `${event.command}\ncwd: ${event.cwd}\nwaiting for player${control ? "" : " — answer in the terminal or desktop window"}`};
  return null;
}

export function mountDialogue(host, browser = window) {
  const document = browser.document, element = panel(document, "dialogue-hud", "Dialogue");
  element.setAttribute("role", "status"); element.style.whiteSpace = "pre-wrap";
  host.append(element);
  let latest = null;
  function show(event) {
    const control = event?.type === "approval_requested" && Boolean(browser.specvaniaDesktop?.controlToken?.());
    const message = dialogueFor(event, control);
    if (!message) return;
    latest = event;
    element.replaceChildren(label(document, "h2", message.speaker), label(document, "p", `› ${message.text}`));
  }
  const onState = ({detail}) => {
    element.replaceChildren(label(document, "p", "The party awaits its next room."));
    show(detail.dialogue);
  };
  const onEvent = ({detail}) => {
    if (detail.type === "approval_answered" && latest?.type === "approval_requested" && detail.request_id === latest.request_id) {
      element.replaceChildren(label(document, "h2", "Warrior"), label(document, "p", `${detail.answer ? "approved" : "denied"} in ${detail.responder}`));
    } else show(detail);
  };
  onState({detail:{}});
  browser.addEventListener("specvania:state", onState); browser.addEventListener("specvania:event", onEvent);
  return {destroy() {
    browser.removeEventListener("specvania:state", onState); browser.removeEventListener("specvania:event", onEvent); element.remove();
  }};
}
