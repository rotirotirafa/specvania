import { t, onLanguageChange } from "../i18n/index.js";
import { UI_COLORS } from "../pixel/palette.js";

/** One projection for both shells. Only the desktop preload supplies control. */
export function mountApproval(socket, { window = globalThis.window, document = globalThis.document } = {}) {
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
    panel = document.createElement("section");
    panel.id = "approval-choice";
    panel.setAttribute("role", token ? "dialog" : "status");
    const accessible = () => panel.setAttribute("aria-label", t("approval.label"));
    accessible(); bindings.push(accessible);
    Object.assign(panel.style, {
      fontFamily: '"Press Start 2P", monospace', fontSize: "10px", lineHeight: "1.8",
      color: UI_COLORS.text, backgroundColor: UI_COLORS.surface,
      border: `4px solid ${UI_COLORS.border}`, padding: "16px", margin: "16px 0",
      overflowWrap: "anywhere", boxShadow: `4px 4px ${UI_COLORS.background}`,
    });
    panel.append(text("h2", () => t("approval.title")), text("pre", request.command), text("p", () => t("approval.cwd", {cwd: request.cwd})));
    status = text("p", waitingHint);
    panel.append(status);
    if (token && connected) {
      for (const [label, answer] of [["approval.allow", true], ["approval.deny", false]]) {
        const button = text("button", () => t(label));
        button.type = "button";
        Object.assign(button.style, {
          fontFamily: "inherit", fontSize: "inherit", color: UI_COLORS.text,
          backgroundColor: UI_COLORS.background, border: `2px solid ${UI_COLORS.accent}`,
          padding: "12px", marginRight: "12px", cursor: "pointer", webkitAppRegion: "no-drag",
        });
        button.addEventListener("click", () => {
          if (!connected || socket.readyState !== 1 || button.disabled) return;
          disable();
          updateStatus(() => t("approval.resolving"));
          socket.send(JSON.stringify({ kind: "approval_answer", request_id: requestId, answer, control_token: token }));
        });
        choices.push(button);
        panel.append(button);
      }
    }
    document.body.append(panel);
    // Deny is the safe initial keyboard choice; browser watch never steals focus.
    choices[1]?.focus();
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

  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.kind === "state" && message.approval_request) {
      show(message.approval_request);
    } else if (message.kind === "event") {
      const event = message.event;
      if (event.type === "approval_requested") show(message.approval_request || event);
      if (event.type === "approval_answered" && event.request_id === requestId) {
        panel.remove();
        bindings = []; statusText = null;
        choices = [];
        requestId = undefined;
        status = text("p", () => t("approval.result", {answer: t(event.answer ? "approval.approved" : "approval.denied"), responder: t(`responder.${event.responder}`)}));
        status.setAttribute("role", "status");
        panel = status;
        document.body.append(panel);
      }
      notify(event);
    } else if (message.kind === "approval_rejected" && status) {
      disable();
      updateStatus(waitingHint);
    }
  });
  socket.addEventListener("close", () => {
    connected = false;
    disable();
    if (status) updateStatus(() => t("connection.closed"));
  });
  return { destroy() { unsubscribe(); panel?.remove(); } };
}
