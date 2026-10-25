import { t } from "../i18n/index.js";
import { mountDialogueBox } from "./dialogue-box.js";

/** Compatibility mount: approvals now share the castle dialogue box. */
export function mountApproval(socket, {window = globalThis.window, document = window.document} = {}) {
  const box = mountDialogueBox(socket, {browser:window, document});
  let replay = false;
  function notify(event) {
    const desktop = window.specvaniaDesktop;
    if (!desktop) return;
    if (event.type === "approval_requested") {
      desktop.notify?.(t("notify.approval"), [event.command, t("approval.cwd", {cwd: event.cwd})].join("\n").slice(0, 500));
    } else if (event.type === "review" && event.verdict === "fail") {
      desktop.notify?.(t("notify.review"), t("notify.result", {id: event.room_id, result: event.feedback}).slice(0, 500));
    } else if (event.type === "boss_result") {
      desktop.notify?.(t("notify.boss"), t("notify.result", {id: event.area_id, result: t(event.passed ? "result.passed" : "result.failed")}));
    }
    // Native focus belongs to BrowserWindow, not document.hasFocus().
  }

  const message = ({data}) => {const value = JSON.parse(data);
    if (["state", "studio_state"].includes(value.kind)) replay = Boolean(value.replay);
    if (!replay && value.kind === "event") notify(value.event);
  };
  socket.addEventListener("message", message);
  return {destroy() {box.destroy(); socket.removeEventListener?.("message", message);}};
}
