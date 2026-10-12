# specvania desktop

This optional Electron shell loads the same web app as a browser tab. Python and
the local UI server remain responsible for the run; Electron contains no engine.

For development, install the pinned Electron dependency with `npm install` in
this directory, start a local specvania UI server, and pass its view URL:

```sh
npm --prefix apps/desktop start -- --url 'http://127.0.0.1:<port>/?view_token=<view-token>'
```

Once T18 ships, `specvania replay demo` supplies a running map for this visual
check. The shell requires an HTTP URL on `127.0.0.1`. It opens one frameless 16:9
window, at least 960×540, with a pixel title bar you can drag. Use your operating
system's close shortcut (for example Alt+F4 or Cmd+Q) to close the window.

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
`window.specvaniaDesktop.isDesktop`, `controlToken()` and `notify(title, body)`;
it does not expose Node, Electron or arbitrary IPC. Navigation and redirects stay
on the UI server origin; HTTP(S) `window.open` links go to the system browser.
Approval controls and event-triggered notifications belong to T20; installers
and release builds belong to T21.

Offline checks need only Node and npm, even without Electron or `node_modules`:

```sh
npm --prefix apps/desktop run check
```

The check syntax-validates `main.js` and `preload.js`, then runs the Node built-in
test runner against a stubbed Electron boundary and the web app's title bar.
