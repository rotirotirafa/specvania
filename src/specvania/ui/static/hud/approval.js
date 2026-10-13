import { UI_COLORS } from "../pixel/palette.js";

/** One projection for both shells. Only the desktop preload supplies control. */
export function mountApproval(socket, { window = globalThis.window, document = globalThis.document } = {}) {
  let panel;
  let requestId;
  let choices = [];
  let status;
  let connected = true;
  const waitingHint = () => window.specvaniaDesktop?.controlToken?.()
    ? "waiting for player"
    : "waiting for player — answer in the terminal or desktop window";

  const text = (tag, value) => {
    const element = document.createElement(tag);
    element.textContent = value;
    return element;
  };
  const disable = () => { for (const button of choices) button.disabled = true; };

  function show(request) {
    panel?.remove();
    requestId = request.request_id;
    choices = [];
    const token = window.specvaniaDesktop?.controlToken?.() || "";
    panel = document.createElement("section");
    panel.id = "approval-choice";
    panel.setAttribute("role", token ? "dialog" : "status");
    panel.setAttribute("aria-label", "Warrior shell approval");
    Object.assign(panel.style, {
      fontFamily: '"Press Start 2P", monospace', fontSize: "10px", lineHeight: "1.8",
      color: UI_COLORS.text, backgroundColor: UI_COLORS.surface,
      border: `4px solid ${UI_COLORS.border}`, padding: "16px", margin: "16px 0",
      overflowWrap: "anywhere", boxShadow: `4px 4px ${UI_COLORS.background}`,
    });
    panel.append(text("h2", "Warrior requests approval"), text("pre", request.command), text("p", `cwd: ${request.cwd}`));
    status = text("p", waitingHint());
    panel.append(status);
    if (token && connected) {
      for (const [label, answer] of [["Allow", true], ["Deny", false]]) {
        const button = text("button", label);
        button.type = "button";
        Object.assign(button.style, {
          fontFamily: "inherit", fontSize: "inherit", color: UI_COLORS.text,
          backgroundColor: UI_COLORS.background, border: `2px solid ${UI_COLORS.accent}`,
          padding: "12px", marginRight: "12px", cursor: "pointer", webkitAppRegion: "no-drag",
        });
        button.addEventListener("click", () => {
          if (!connected || socket.readyState !== 1 || button.disabled) return;
          disable();
          status.textContent = "waiting for player — resolving approval";
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
      desktop.notify("Approval requested", `${event.command}\ncwd: ${event.cwd}`.slice(0, 500));
    } else if (event.type === "review" && event.verdict === "fail") {
      desktop.notify("Paladin review failed", `${event.room_id}: ${event.feedback}`.slice(0, 500));
    } else if (event.type === "boss_result") {
      desktop.notify("Boss result", `${event.area_id}: ${event.passed ? "passed" : "failed"}`);
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
        choices = [];
        requestId = undefined;
        status = text("p", `${event.answer ? "approved" : "denied"} in ${event.responder}`);
        status.setAttribute("role", "status");
        panel = status;
        document.body.append(panel);
      }
      notify(event);
    } else if (message.kind === "approval_rejected" && status) {
      disable();
      status.textContent = waitingHint();
    }
  });
  socket.addEventListener("close", () => {
    connected = false;
    disable();
    if (status) status.textContent = "Run connection closed";
  });
}
