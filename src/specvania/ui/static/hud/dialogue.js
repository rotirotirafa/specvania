import { t, onLanguageChange } from "../i18n/index.js";
import { panel, label } from "./party.js";
import { CAST, SHEET_URL, TILE_SIZE } from "../pixel/cast.js";

export function dialogueFor(event, control = false) {
  if (!event) return null;
  if (event.type === "action") return {speaker: t("role.warrior"), text: [event.thought, t("dialogue.action", {action: event.action, args: event.args_summary})].filter(Boolean).join("\n")};
  if (event.type === "review") return {speaker: t("role.paladin"), text: event.feedback || ""};
  if (event.type === "approval_answered") return {speaker: t("role.warrior"), text: t("approval.result", {answer: t(event.answer ? "approval.approved" : "approval.denied"), responder: t(`responder.${event.responder}`)})};
  if (event.type === "approval_requested") return {speaker: t("role.warrior"), text: t("approval.request", {command: event.command, cwd: t("approval.cwd", {cwd: event.cwd}), waiting: t(control ? "approval.waiting" : "approval.watch")})};
  return null;
}

export function mountDialogue(host, browser = window) {
  const document = browser.document, element = panel(document, "dialogue-hud", t("dialogue.title"));
  element.setAttribute("role", "status"); element.style.whiteSpace = "pre-wrap";
  host.append(element);
  let latest = null;
  function show(event) {
    const control = event?.type === "approval_requested" && Boolean(browser.specvaniaDesktop?.controlToken?.());
    const message = dialogueFor(event, control);
    if (!message) return;
    latest = event;
    element.replaceChildren(label(document, "h2", message.speaker), label(document, "p", t("dialogue.text", {text: message.text})));
  }
  const onState = ({detail}) => {
    element.replaceChildren(label(document, "p", t("dialogue.awaiting")));
    latest = null; show(detail.dialogue);
  };
  const onEvent = ({detail}) => {
    if (detail.type === "approval_answered" && latest?.type === "approval_requested" && detail.request_id === latest.request_id) {
      show(detail);
    } else show(detail);
  };
  const unsubscribe = onLanguageChange(() => {
    element.setAttribute("aria-label", t("dialogue.title"));
    if (latest) show(latest); else onState({detail:{}});
  });
  onState({detail:{}});
  browser.addEventListener("specvania:state", onState); browser.addEventListener("specvania:event", onEvent);
  return {destroy() {
    unsubscribe();
    browser.removeEventListener("specvania:state", onState); browser.removeEventListener("specvania:event", onEvent); element.remove();
  }};
}

/** The studio uses the HUD's RPG dialogue style with the Mage's cast portrait. */
export function mageDialogue(ui) {
  const box = ui.element("section"); box.className = "mage-dialogue";
  box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true");
  box.setAttribute("aria-label", ui.t("role.mage"));
  const header = ui.document.createElement("header"); header.className = "dialogue-speaker"; box.append(header);
  const portrait = ui.document.createElement("span");
  portrait.dataset.cast = "mage"; portrait.setAttribute("aria-hidden", "true");
  const size = TILE_SIZE * 2;
  Object.assign(portrait.style, {display: "block", width: `${size}px`, height: `${size}px`,
    imageRendering: "pixelated", backgroundImage: `url("${SHEET_URL}")`,
    backgroundSize: `${192 * 2}px ${176 * 2}px`,
    backgroundPosition: `${-(CAST.mage % 12) * size}px ${-Math.floor(CAST.mage / 12) * size}px`});
  header.append(portrait);
  const speaker = ui.document.createElement("h2"); ui.label(speaker, "role.mage"); header.append(speaker);
  return box;
}
