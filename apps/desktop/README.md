# specvania desktop

This optional Electron shell loads the same web app as a browser tab. Python and
the local UI server remain responsible for the run; Electron contains no engine.

For development, install the pinned Electron dependency with `npm install` in
this directory, start a local specvania UI server, and pass its view URL:

```sh
npm --prefix apps/desktop start -- --url 'http://127.0.0.1:<port>/?view_token=<view-token>'
```

Once T18 ships, `specvania replay demo` supplies a running map for this visual
check. The shell requires an HTTP URL on `127.0.0.1`. It opens one freely resizable
window, at least 960×540, with native minimize, maximize/full-screen and close
controls. On macOS the traffic lights sit in the draggable pixel title bar;
Windows and Linux use a native frame. Double-click the macOS title bar to zoom.

The View menu offers Actual size, Zoom in, Zoom out and Full screen. Use
Cmd/Ctrl + `0`, `=` or `-` to change page zoom. Window size, position,
maximized/full-screen state and page zoom are remembered in
`desktop-window.json` in Electron's user-data directory. A saved position on a
disconnected display falls back to a centered 960×540 window. This file contains
no tokens. Packaged builds omit reload and developer tools from the menu.

The Python launcher selects `desktop.path` in config, then `SPECVANIA_DESKTOP`,
then `specvania-desktop` on PATH, then `/Applications/specvania.app` on macOS.
An executable on PATH can be a wrapper that forwards its arguments to Electron.
For example, a development wrapper can execute `electron /absolute/path/to/apps/desktop "$@"`.
`auto` prefers this app and prints a one-line notice when it opens a browser
instead. `desktop` reports an installation/configuration error if it cannot find
the app. Command rooms call `specvania.cli.launch_ui` after the server has a port;
T19 does not implement the plan, run or replay commands.

The control token is passed separately with `--control-token` by the Python
launcher, then privately through Electron's preload arguments. It is never put
in a URL or logged. View-only launches can omit it. The preload exposes only
`window.specvaniaDesktop.isDesktop`, `platform`, `controlToken()`,
`notify(title, body)`, `pickFolder()`, `settings` and `studio`;
it does not expose Node, Electron or arbitrary IPC. Navigation and redirects stay
on the UI server origin; HTTP(S) `window.open` links go to the system browser.
Approval controls and event-triggered notifications belong to T20.

Offline checks need only Node and npm, even without Electron or `node_modules`:

```sh
npm --prefix apps/desktop run check
```

The check syntax-validates `main.js` and `preload.js`, then runs the Node built-in
test runner against a stubbed Electron boundary and the web app's title bar.
It also validates packaging and the generated icon with Node built-ins only.

## Install a release

The desktop installers are **unsigned**. macOS builds are **not notarized**;
Gatekeeper may block the first launch. Download the dmg matching your Mac's
architecture (`arm64` for Apple Silicon, `x64` for Intel), open it, and drag
`specvania.app` to `/Applications/specvania.app`. In Finder, right-click the app
and choose **Open**, then confirm **Open** in the Gatekeeper dialog. For a build
you trust, you can alternatively remove its quarantine attribute:

```sh
xattr -dr com.apple.quarantine "/Applications/specvania.app"
```

The Python launcher finds this location automatically. Opening the app directly
starts its bundled Python studio through your login shell and opens the title
screen. No separate Python, uv or specvania installation is needed. Settings can
select a custom specvania executable; development without a bundled runtime
falls back to specvania on PATH.

On Linux, download the x64 AppImage and make it executable (substitute its
actual version):

```sh
chmod +x specvania-0.1.0-x64.AppImage
mkdir -p "$HOME/.local/bin"
mv specvania-0.1.0-x64.AppImage "$HOME/.local/bin/"
export PATH="$HOME/.local/bin:$PATH"
```

The launcher finds an executable named `specvania-*.AppImage` in PATH directories,
in PATH order (filename order within each directory). `specvania-desktop` on
PATH takes priority over AppImages; config `desktop.path` and `SPECVANIA_DESKTOP`
still take priority over both. You can also set either override to the full
AppImage path. AppImage requires a host with FUSE support; on a host without it,
extract with `./specvania-0.1.0-x64.AppImage --appimage-extract` and point
`SPECVANIA_DESKTOP` to the extracted `squashfs-root/AppRun` executable.

## Build and release

Build one target at a time on its native platform and architecture. From the
repository root, install the pinned desktop dependencies and build the wheel:

```sh
npm --prefix apps/desktop ci
uv build --wheel
cd apps/desktop
npm run runtime:requirements
npm run bundle-runtime -- --target darwin-arm64 --wheel ../../dist/*.whl --requirements build/runtime-requirements.txt --archive /path/to/pinned-arm64.tar.gz
npm run dist:mac -- --arm64
```

`runtime:requirements` exports `uv.lock` without development dependencies or the
Postgres extra. `bundle-runtime` installs the freshly built wheel and those
locked dependencies, precompiles bytecode and verifies the app version. Keep
exactly one freshly built wheel in `dist/` for the wildcard above.

`--archive` accepts a local copy of the matching pinned archive from
`runtime.json`, with the same mandatory SHA-256 verification. Omit it to download
the archive into `~/.cache/specvania/runtimes`. A local archive avoids only that
download; for offline dependency installation also supply cached wheels with
`PIP_NO_INDEX=1 PIP_FIND_LINKS=/path/to/wheels`. Preparing Node dependencies and
Electron's build caches is a separate build-time step. Nothing is downloaded
when the installed app starts.

For Intel macOS, change the runtime target to `darwin-x64`, provide its matching
archive and use `npm run dist:mac -- --x64`. For Linux x64, use `linux-x64`, its
archive and `npm run dist:linux -- --x64`. Runtime output stays in gitignored
`build/runtime/<target>/`. The CLI architecture flag selects only that installer;
the configuration does not expand it to other architectures.

Both installer scripts regenerate the icon and disable publishing. The builder's
`afterPack` hook rejects a missing or non-executable packaged Python, or a runtime
that cannot print the matching specvania version. It checks the copied runtime
inside the app, so a missing extraResources source cannot silently produce an
installer without Python. The macOS dmg uses compressed UDZO format; workflows
reject dmg files at or above 250 MB. `identity: null` and disabled signing identity
discovery keep builds unsigned; signing and notarization remain outside scope.

`scripts/make-icon.mjs` generates `build/icon.png` from the vendored Kenney Mage
tile using Node built-ins. The icon and runtime output are gitignored. The app
ships the Electron shell, local startup screens, Python runtime and installed
specvania wheel with its dependencies and vendored assets. At launch the app
runs `runtime/bin/python3 -I -B -m specvania studio` through the login shell,
without relying on pip's build-machine console-script shebangs.

Output stays in gitignored `apps/desktop/dist/`:
`specvania-<version>-arm64.dmg`, `specvania-<version>-x64.dmg`, or
`specvania-<version>-x64.AppImage`, depending on the selected target. Release
workflow tags are `vX.Y.Z`; CI pull requests build the same artifacts without
publishing. Each matrix job builds one runtime and one installer. Actual hosted
builds and native-window behavior still require the reviewer/owner checks.
