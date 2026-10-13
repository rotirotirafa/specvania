import { t, onLanguageChange } from "../i18n/index.js";
import { expeditionDialog } from "../screens/pause.js";

/** One projection for both shells. Only the desktop preload supplies control. */
export function mountApproval(socket, { window = globalThis.window, document = globalThis.document } = {}) {
  const dialog = expeditionDialog("approval-choice", {browser: window, document});
  let panel;
  let requestId;
  let choices = [];
  let status;
  let connected = true;
  let bindings = [];
  let statusText;
  const waitingHint = () => window.specvaniaDesktop?.controlToken?.()
    ? t("approval.waiting")
    : t("approval.watch");

  const text = (tag, value) => {
    const element = document.createElement(tag);
    const render = () => { element.textContent = typeof value === "function" ? value() : value; };
    render(); bindings.push(render);
    return element;
  };
  const updateStatus = value => { statusText = value; status.textContent = value(); };
  const unsubscribe = onLanguageChange(() => {
    for (const render of bindings) render();
    if (status && statusText) status.textContent = statusText();
  });
  const disable = () => { for (const button of choices) button.disabled = true; };

  function show(request) {
    panel?.remove();
    bindings = []; statusText = null;
    requestId = request.request_id;
    choices = [];
    const token = window.specvaniaDesktop?.controlToken?.() || "";
    panel = dialog.open("approval.label", (content, actions, ui) => {
      ui.portrait(content, "warrior");
      content.append(text("h2", () => t("approval.title")), text("pre", () => t("approval.command", {command: request.command})), text("p", () => t("approval.cwd", {cwd: request.cwd})));
      status = text("p", waitingHint); content.append(status);
      if (token && connected) {
        for (const [label, answer] of [["approval.allow", true], ["approval.deny", false]]) {
          const button = ui.button(actions, label, () => respond(answer));
          choices.push(button);
        }
      }
    }, {onEscape: () => respond(false), initial: 1});
    function respond(answer) {
      if (!token || !connected || socket.readyState !== 1 || choices.some(button => button.disabled)) return;
      disable(); updateStatus(() => t("approval.resolving"));
      socket.send(JSON.stringify({ kind: "approval_answer", request_id: requestId, answer, control_token: token }));
    }
  }

  function notify(event) {
    const desktop = window.specvaniaDesktop;
    if (!desktop) return;
    if (event.type === "approval_requested") {
      desktop.notify(t("notify.approval"), [event.command, t("approval.cwd", {cwd: event.cwd})].join("\n").slice(0, 500));
    } else if (event.type === "review" && event.verdict === "fail") {
      desktop.notify(t("notify.review"), t("notify.result", {id: event.room_id, result: event.feedback}).slice(0, 500));
    } else if (event.type === "boss_result") {
      desktop.notify(t("notify.boss"), t("notify.result", {id: event.area_id, result: t(event.passed ? "result.passed" : "result.failed")}));
    }
    // Native focus belongs to BrowserWindow, not document.hasFocus().
  }

  const message = ({ data }) => {
    const message = JSON.parse(data);
    if (message.kind === "state" && message.approval_request) {
      show(message.approval_request);
    } else if (message.kind === "event") {
      const event = message.event;
      if (event.type === "approval_requested") show(message.approval_request || event);
      if (event.type === "approval_answered" && event.request_id === requestId) {
        dialog.close();
        panel?.remove();
        bindings = []; statusText = null;
        choices = [];
        requestId = undefined;
        status = text("p", () => t("approval.result", {answer: t(event.answer ? "approval.approved" : "approval.denied"), responder: t(`responder.${event.responder}`)}));
        status.setAttribute("role", "status");
        panel = status;
        (document.getElementById?.("castle-hud") || document.body).append(panel);
      }
      notify(event);
    } else if (message.kind === "approval_rejected" && status) {
      disable();
      updateStatus(waitingHint);
    }
  };
  socket.addEventListener("message", message);
  const disconnected = () => {
    connected = false;
    disable();
    if (status) updateStatus(() => t("connection.closed"));
  };
  socket.addEventListener("close", disconnected);
  return { destroy() { unsubscribe(); dialog.destroy(); panel?.remove(); socket.removeEventListener?.("message", message); socket.removeEventListener?.("close", disconnected); } };
}
