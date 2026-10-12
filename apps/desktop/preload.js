"use strict";

const { contextBridge, ipcRenderer } = require("electron");

function exposeDesktop() {
  const index = process.argv.indexOf("--control-token");
  const token = index === -1 ? "" : (process.argv[index + 1] || "");
  contextBridge.exposeInMainWorld("specvaniaDesktop", {
    isDesktop: true,
    controlToken: () => token,
    notify: (title, body) => {
      if (typeof title === "string" && typeof body === "string") {
        ipcRenderer.send("specvania:notify", { title, body });
      }
    },
  });
}

exposeDesktop();
