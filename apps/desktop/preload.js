"use strict";

const { contextBridge, ipcRenderer } = require("electron");

function exposeDesktop() {
  const token = ipcRenderer.sendSync("specvania:control-token") || "";
  contextBridge.exposeInMainWorld("specvaniaDesktop", {
    isDesktop: true,
    controlToken: () => token,
    pickFolder: () => ipcRenderer.invoke("specvania:pick-folder"),
    settings: {
      get: () => ipcRenderer.invoke("specvania:settings-get"),
      set: (value) => ipcRenderer.invoke("specvania:settings-set", value),
    },
    studio: { get: () => ipcRenderer.invoke("specvania:studio"), restart: () => ipcRenderer.invoke("specvania:studio-restart") },
    notify: (title, body) => {
      if (typeof title === "string" && typeof body === "string") {
        ipcRenderer.send("specvania:notify", { title, body });
      }
    },
  });
}

exposeDesktop();
