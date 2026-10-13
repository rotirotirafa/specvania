"use strict";

const { contextBridge, ipcRenderer } = require("electron");

function exposeDesktop() {
  const token = ipcRenderer.sendSync("specvania:control-token") || "";
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
