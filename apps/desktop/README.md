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

The Python launcher finds this location automatically. The app loads a URL
provided by the Python launcher; it needs the local UI server running.

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

After installing the pinned dependencies, run in `apps/desktop/`:

```sh
npm run make-icon
npm run dist:mac
npm run dist:linux
```

Build macOS dmg installers on macOS (arm64 and x64), and the x64 Linux AppImage
on Linux. For one Mac architecture, append `-- --arm64` or `-- --x64` to
`npm run dist:mac`. Both build scripts regenerate the icon and disable publishing.
`electron-builder.yml` uses `identity: null` for macOS; release CI also disables
automatic signing identity discovery. Signing and notarization are outside v0.2.

`scripts/make-icon.mjs` imports `SPRITES.mage.idle[0]` and `PALETTE` from the pixel
kit and renders a 1024×1024 RGBA PNG using integer nearest-neighbour scaling
(16×16 at 64×). Only Node built-ins encode it, including zlib compression and
CRC32. Generated `build/icon.png` is gitignored by the repository's `build/`
rule; no raster source is committed. The builder converts the PNG into the
platform icon. The app includes only `main.js`, `preload.js`, the generated icon,
and the package metadata and Electron runtime (including its license notices)
that electron-builder supplies. The Python web app is served separately.

Output stays in gitignored `apps/desktop/dist/`:
`specvania-<version>-arm64.dmg`, `specvania-<version>-x64.dmg`, and
`specvania-<version>-x64.AppImage`. The GitHub workflow runs only when the owner
pushes a `desktop-v*` tag, builds each artifact in a separate matrix job, then
attaches all three to one GitHub Release. The tag names the release; artifact
versions come from `package.json`. CI must be verified after the owner pushes
the tag; offline checks do not prove installer builds or native-window behavior.
