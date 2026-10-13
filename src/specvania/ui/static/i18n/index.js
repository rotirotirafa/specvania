import en from "./en.json" with { type: "json" };
import ptBR from "./pt-BR.json" with { type: "json" };

const dictionaries = Object.freeze({ en: Object.freeze(en), "pt-BR": Object.freeze(ptBR) });
const valid = language => language === "en" || language === "pt-BR";
const osLanguage = language => String(language || "").toLowerCase().startsWith("pt") ? "pt-BR" : "en";

/** Viewer-owned state lives in this service closure, never in mutable globals. */
export function createI18n(browser = globalThis.window, catalog = dictionaries) {
  let language = "en";
  const subscribers = new Set();
  function initialize(viewer = browser) {
    browser = viewer;
    let saved, setting;
    try { saved = browser?.localStorage?.getItem("specvania.language"); } catch { /* Private/storage-disabled viewers work too. */ }
    try { setting = browser?.specvaniaDesktop?.settings?.language; } catch { /* Older preload versions have no settings. */ }
    setLanguage(valid(saved) ? saved : valid(setting) ? setting : osLanguage(browser?.navigator?.language), { persist: false });
  }
  function t(key, params = {}) {
    const translated = catalog[language]?.[key];
    if (translated === undefined) browser?.console?.warn?.(`Missing translation: ${language}/${key}`);
    const template = translated ?? catalog.en[key] ?? key;
    // One pass: parameter text is inert, including markup and other placeholders.
    return template.replace(/\{(\w+)\}/g, (placeholder, name) => String(params[name] ?? placeholder));
  }
  function setLanguage(next, { persist = true, manual = false } = {}) {
    if (!valid(next)) return false;
    if (persist) {
      try { browser?.localStorage?.setItem("specvania.language", next); } catch { /* Storage is optional. */ }
    }
    const changed = language !== next;
    language = next;
    if (changed || manual) for (const subscriber of subscribers) subscriber({ language, manual });
    return true;
  }
  function subscribe(subscriber) { subscribers.add(subscriber); return () => subscribers.delete(subscriber); }
  initialize();
  return Object.freeze({ t, setLanguage, subscribe, initialize, get language() { return language; } });
}

export const i18n = createI18n();
export const t = (key, params) => i18n.t(key, params);
export const setLanguage = (language, options) => i18n.setLanguage(language, options);
export const onLanguageChange = subscriber => i18n.subscribe(subscriber);

/** Static labels and accessible names use text nodes/attributes, never HTML. */
export function bindDocument(document, service = i18n) {
  const render = () => {
    if (document.documentElement) document.documentElement.lang = service.language;
    for (const node of document.querySelectorAll?.("[data-i18n]") || []) node.textContent = service.t(node.dataset.i18n);
    for (const node of document.querySelectorAll?.("[data-i18n-aria]") || []) node.setAttribute("aria-label", service.t(node.dataset.i18nAria));
  };
  render();
  return service.subscribe(render);
}

/** The watch-only viewer never sends control. Studio reports win without echo. */
export function connectLanguage(socket, browser = window, service = i18n) {
  let studio = false;
  const unsubscribe = service.subscribe(({ language, manual }) => {
    if (!manual || !studio || socket.readyState !== 1) return;
    const token = browser.specvaniaDesktop?.controlToken?.();
    if (token) socket.send(JSON.stringify({ kind: "set_language", language, control_token: token }));
  });
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.kind === "studio_state") {
      studio = true;
      if (valid(message.language)) service.setLanguage(message.language);
    }
  });
  return unsubscribe;
}
