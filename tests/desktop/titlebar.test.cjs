"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { pathToFileURL } = require("node:url");
const path = require("node:path");

test("pixel_title_bar_appears_only_in_desktop_and_does_not_read_control_token", async () => {
  const main = pathToFileURL(path.resolve(__dirname, "../../src/specvania/ui/static/main.js"));
  for (const isDesktop of [false, true]) {
    const elements = [];
    const connection = {};
    const runState = {};
    const listeners = {};
    global.window = {
      location: { href: "http://127.0.0.1:8765/?view_token=watch", search: "?view_token=watch" },
      dispatchEvent() {},
      ...(isDesktop ? { specvaniaDesktop: { isDesktop: true, controlToken() { assert.fail("title bar requested control token"); } } } : {}),
    };
    global.document = {
      getElementById(id) { return id === "connection" ? connection : id === "run-state" ? runState : null; },
      createElement(tag) { return { tag, style: {}, setAttribute(name, value) { this[name] = value; } }; },
      body: { prepend(element) { elements.push(element); } },
      head: { append(element) { elements.push(element); } },
    };
    global.WebSocket = class {
      constructor(url) { assert.equal(url.toString(), "ws://127.0.0.1:8765/ws?view_token=watch"); }
      addEventListener(name, listener) { listeners[name] = listener; }
    };
    try {
      await import(`${main.href}?desktop=${isDesktop}`);
      const bars = elements.filter((element) => element.id === "desktop-title-bar");
      assert.equal(bars.length, isDesktop ? 1 : 0);
      if (isDesktop) {
        const bar = bars[0];
        assert.equal(bar.textContent, "specvania");
        assert.equal(bar.style.webkitAppRegion, "drag");
        assert.match(bar.style.fontFamily, /pixel|Press/i);
        assert.ok(bar.style.borderBottom);
      }
      listeners.open();
      assert.equal(connection.textContent, "Watching the run");
    } finally {
      delete global.window;
      delete global.document;
      delete global.WebSocket;
    }
  }
});
