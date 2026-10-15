"use strict";

// electron-builder calls this after extraResources has been copied, before an
// installer can be emitted. Missing copy sources must never yield a usable build.
const fs = require("node:fs");
const path = require("node:path");
const {spawnSync} = require("node:child_process");
const appVersion = require("../package.json").version;

class RuntimePackagingError extends Error {}

module.exports = function verifyRuntime({electronPlatformName, appOutDir, packager}) {
  let resources;
  if (electronPlatformName === "darwin") {
    resources = path.join(appOutDir, `${packager.appInfo.productFilename}.app`, "Contents/Resources");
  } else if (electronPlatformName === "linux") {
    resources = path.join(appOutDir, "resources");
  } else {
    throw new RuntimePackagingError("Runtime packaging supports macOS and Linux only.");
  }
  const python = path.join(resources, "runtime/bin/python3");
  try {
    if (!fs.statSync(python).isFile()) throw new Error();
    fs.accessSync(python, fs.constants.X_OK);
  } catch {
    throw new RuntimePackagingError(`Packaged runtime is missing or not executable: ${python}`);
  }
  const checked = spawnSync(python, ["-I", "-B", "-m", "specvania", "--version"], {
    encoding: "utf8", timeout: 30000, maxBuffer: 1024 * 1024,
  });
  if (checked.error || checked.status !== 0 || checked.stdout.trim() !== appVersion) {
    throw new RuntimePackagingError(`Packaged runtime failed the specvania version check (expected ${appVersion}): ${python}`);
  }
};
