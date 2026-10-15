import { createRunningIndicator } from "../hud/running.js";
import { mageDialogue } from "../hud/dialogue.js";

/** Studio-only dialogue. Transient questions are rendered from server snapshots. */
export function ideaScreen(ui, initial = {}, timing = {}) {
  const running = createRunningIndicator(ui.browser || globalThis, () => render(), timing);
  let working = null, started = null;
  const box = mageDialogue(ui); box.tabIndex = -1;
  const content = ui.document.createElement("div"); content.className = "dialogue-content"; box.append(content);
  const actions = ui.document.createElement("footer"); actions.className = "dialogue-actions"; box.append(actions);
  let state = initial, slug = initial.world_slug || null, saved = Boolean(initial.world_slug && ui.savedSlug === initial.world_slug), saving = false;
  let request = null, mode = "", selected = -1, options = [], input = null, start = null;
  let labels = [], error = null, dirty = null, pending = false;
  const canStart = () => saved && (!ui.hasMap || ui.revealedSlug === slug);
  const node = tag => { const item = ui.document.createElement(tag); content.append(item); return item; };
  const text = (tag, key, params = {}) => {
    const item = node(tag); labels.push([item, key, params]); item.textContent = ui.t(key, params); return item;
  };
  const button = (key, action, disabled = false) => {
    const item = text("button", key); actions.append(item); item.type = "button"; item.disabled = disabled;
    item.addEventListener("click", action); return item;
  };
  function render() {
    box.setAttribute("aria-label", ui.t("role.mage"));
    if (options[0]?.parentNode) options[0].parentNode.setAttribute("aria-label", ui.t("idea.options"));
    for (const [item, key, params] of labels) item.textContent = ui.t(key, params);
    if (working) working.textContent = running.text(ui.t("idea.working"), started);
    for (const [index, item] of options.entries()) {
      item.setAttribute("aria-selected", String(index === selected));
      item.style.borderColor = index === selected ? ui.palette.Y : ui.palette.S;
    }
  }
  const field = key => {
    const caption = node("label");
    const title = ui.document.createElement("span"); caption.append(title);
    labels.push([title, key, {}]); title.textContent = ui.t(key);
    const item = ui.document.createElement("textarea"); item.rows = mode === "clarify" ? 2 : 3;
    item.setAttribute("aria-label", ui.t(key)); caption.append(item);
    input = item; return item;
  };
  function sendAnswer() {
    if (pending || !request) return;
    const answer = input.value.trim() || request.options?.[selected] || request.default;
    if (ui.send("clarify_answer", {question_id: request.question_id, answer})) {
      pending = true; for (const item of options) item.disabled = true;
      input.disabled = true; box.focus();
    }
  }
  function draw(next) {
    box.dataset.mode = error ? "error" : dirty ? "dirty" : next;
    box.setAttribute("aria-modal", String(box.dataset.mode !== "world"));
    ui.dialogueLayout(box.dataset.mode);
    const isWorking = next === "working" && !error && !dirty;
    if (isWorking && started === null) started = running.now();
    if (!isWorking) started = null;
    working = null; running.active(isWorking);
    mode = next; labels = []; options = []; input = null; start = null;
    content.replaceChildren(); actions.replaceChildren();
    if (error) {
      text("p", error);
      if (error === "error.missingKey") text("p", "error.keyHelp");
      button("idea.continue", () => {error = null; draw(next);});
    } else if (dirty) {
      text("p", "idea.dirty");
      const paths = node("div"); paths.tabIndex = 0;
      paths.style.maxHeight = "160px"; paths.style.overflow = "auto";
      for (const path of dirty) {
        const item = ui.document.createElement("p");
        item.textContent = ui.t("idea.path", {path}); paths.append(item);
      }
      button("idea.cancel", () => {dirty = null; ui.restoreGame(); draw("world");});
      button("idea.anyway", () => {if (ui.startRun(slug, true)) {dirty = null; pending = true; draw("working");}});
    } else if (next === "idea") {
      text("p", "idea.ask"); const idea = field("idea.field");
      const caption = node("label"); const title = ui.document.createElement("span"); caption.append(title);
      labels.push([title, "idea.skip", {}]); const skip = ui.document.createElement("input");
      skip.type = "checkbox"; caption.append(skip);
      const send = () => {
        const value = idea.value.trim();
        if (!value || pending) return;
        if (ui.send("start_plan", {idea: value, no_clarify: Boolean(skip.checked)})) {
          pending = true; draw("working");
        }
      };
      idea.addEventListener("keydown", event => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {event.preventDefault(); send();}
      });
      button("idea.plan", send); idea.focus();
    } else if (next === "clarify") {
      text("p", "idea.question", {text: request.text});
      const list = node("div"); list.setAttribute("role", "listbox");
      list.setAttribute("aria-label", ui.t("idea.options"));
      const values = request.options || [];
      selected = values.indexOf(request.default);
      if (selected < 0 && values.length) selected = 0;
      options = values.map((value, index) => {
        const item = ui.document.createElement("button"); item.type = "button";
        item.textContent = ui.t("idea.option", {option: value});
        labels.push([item, "idea.option", {option: value}]);
        item.setAttribute("role", "option");
        item.addEventListener("click", () => {selected = index; input.value = ""; render(); item.focus();});
        list.append(item); return item;
      });
      field("idea.answer");
      text("p", "idea.default", {answer: request.default});
      const accept = button("idea.accept", sendAnswer);
      (options[selected] || input || accept).focus(); render();
    } else if (next === "world") {
      text("p", "idea.ready", {world: slug});
      button("idea.save", () => {if (!saving && ui.send("save_world", {slug})) {saving = true; draw("world");}}, saving);
      start = button("idea.start", () => {
        if (canStart() && ui.startRun(slug)) {pending = true; draw("working");}
      }, !canStart());
    } else working = text("p", "idea.working");
    render();
    if (next !== "idea" || error || dirty) {
      const first = options[selected] || input || Array.from(actions.children).find(item => item.tagName.toLowerCase() === "button" && !item.disabled);
      (first || box).focus();
    }
  }
  const keydown = event => {
    if (mode !== "clarify" || error || dirty || pending || event.isComposing) return;
    if (["ArrowDown", "ArrowUp"].includes(event.key) && event.target !== input && options.length) {
      event.preventDefault(); selected = (selected + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
      input.value = ""; render(); options[selected].focus();
    } else if (event.key === "Enter" && !event.shiftKey) {event.preventDefault(); sendAnswer();}
  };
  box.addEventListener("keydown", keydown);
  function update(value) {
    state = value;
    if (value.world_slug && value.world_slug !== slug) {slug = value.world_slug; saved = false;}
    if (value.error) {saving = false; error = studioErrorKey(value.error); pending = false; draw(value.phase === "project_open" ? "idea" : mode || "idea"); return;}
    if (value.phase === "world_ready") {
      if (saving || ui.savedSlug === slug) {saved = true; saving = false;}
      pending = false; request = null;
      if (mode !== "world" || start?.disabled !== !canStart()) draw("world");
    } else if (value.phase === "planning") {
      const question = value.clarify_request;
      if (question && question.question_id !== request?.question_id) {
        request = question; pending = false; draw("clarify");
      } else if (!question) {request = null; pending = false; if (mode !== "working") draw("working");}
    }
  }
  const onEvent = ({detail}) => {
    if (working && (detail.type === "clarify_answer" || detail.role === "mage" && ["model_requested", "model_call", "fallback"].includes(detail.type))) {
      const ts = Date.parse(detail.ts);
      started = Number.isFinite(ts) ? ts : running.now(); render();
    }
  };
  ui.browser?.addEventListener("specvania:event", onEvent);
  draw(initial.world_slug ? "world" : "idea"); update(initial);
  return {render() {render(); if (input) input.setAttribute("aria-label", ui.t(mode === "clarify" ? "idea.answer" : "idea.field"));}, update,
    previewReady() {if (mode === "world" && !error && !dirty) draw("world");},
    reject(value) {
      pending = false; saving = false;
      if (value.dirty_paths?.length && slug) dirty = value.dirty_paths;
      else error = studioErrorKey(value.reason);
      draw(state.phase === "world_ready" ? "world" : request ? "clarify" : "idea");
    }, destroy() {running.destroy(); ui.browser?.removeEventListener("specvania:event", onEvent); box.removeEventListener("keydown", keydown);},
  };
}

/** Server diagnostics are never echoed; known failures get localized guidance. */
function studioErrorKey(reason = "") {
  if (/API key missing/.test(reason)) return "error.missingKey";
  if (/lint/.test(reason)) return "idea.lintError";
  if (/Provider|provider/.test(reason)) return "idea.providerError";
  if (/phase|unavailable/.test(reason)) return "idea.unavailable";
  return "idea.error";
}
