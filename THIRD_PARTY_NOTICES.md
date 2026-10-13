# Third-party notices

Art is vendored from Kenney Tiny Dungeon (CC0). The pack,
Phaser runtime and pixel font below are vendored, unmodified and loaded locally.
Monaco is trimmed as described below.
Source URLs describe provenance; the web app does not request them.
The desktop first-run screen also ships the pack and font with their licenses;
it works before the separately installed Python CLI can start.

## Kenney Tiny Dungeon

- Name: Kenney Tiny Dungeon
- Version: **1.0**
- Author: Kenney
- Source: https://kenney.nl/assets/tiny-dungeon
- Archive: `kenney_tiny-dungeon.zip`
- Archive SHA-256: `c109438ab06f65fd80f9b2686a4cf9c7c11dc64444b47333ec71d602f8bb5fc7`
- File: `src/specvania/ui/static/vendor/kenney-tiny-dungeon/tilemap_packed.png`
  (the archive's `Tilemap/tilemap_packed.png`, 192×176, 12×11 tiles, 16×16 pixels)
- Sheet SHA-256: `d24e60a41e4ac7a745c0304dfde121143688557f40215f23221c29cfe683825f`
- License: **CC0 1.0**; the unmodified pack notice is shipped as
  `src/specvania/ui/static/vendor/kenney-tiny-dungeon/License.txt`
- Credit: `Art: Kenney Tiny Dungeon · CC0 1.0` (optional under CC0, shown in the gallery)

## Phaser

- Name: Phaser
- Version: **3.90.0** (checked at the gallery's startup and pinned with script integrity)
- Source: https://registry.npmjs.org/phaser/-/phaser-3.90.0.tgz
- Upstream: https://github.com/phaserjs/phaser/tree/v3.90.0
- File: `src/specvania/ui/static/vendor/phaser.min.js` (`package/dist/phaser.min.js`)
- SHA-256: `e92ddef111ba42e92d316979c732311757093688ea1810591cb7aa2858eba7a7`
- License: **MIT**, reproduced below from the npm package's `LICENSE.md`
- Copyright (c) 2024 Richard Davey, Phaser Studio Inc.

### The MIT License (MIT)

Copyright (c) 2024 Richard Davey, Phaser Studio Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Press Start 2P

- Name: Press Start 2P, regular
- Version: **3.000**, from the vendored TTF's name-table version record
- Source: https://github.com/google/fonts/tree/main/ofl/pressstart2p
- Font file: `src/specvania/ui/static/vendor/fonts/PressStart2P-Regular.ttf`
- SHA-256: `034c77f1f05ec89421e4a63f0e3a4ca1ecf852cc6d2bf611f126f275728e017d`
- License: **SIL Open Font License, Version 1.1**; full text is shipped in
  `src/specvania/ui/static/vendor/fonts/OFL.txt`
- Copyright 2012 The Press Start 2P Project Authors (cody@zone38.net), with
  Reserved Font Name "Press Start 2P".

The font and its OFL notice are distributed together without modification.

## Electron desktop runtime

- Name: Electron
- Version: **44.7.0**, pinned in `apps/desktop/package.json`
- License: **MIT**; upstream license:
  https://github.com/electron/electron/blob/v44.7.0/LICENSE
- Electron also bundles Chromium, Node.js and other third-party components with
  their own licenses. Their complete bundled notices are shipped in the app as
  `LICENSES.chromium.html`, alongside the Electron `LICENSE` supplied with the
  runtime. On macOS, inspect the app bundle's runtime files;
  on Linux, extract the AppImage and inspect its runtime files. These notices
  remain in the packaged Electron distribution.

## electron-builder (build tool)

- Name: electron-builder
- Version: **26.15.3**, pinned as a desktop dev dependency; not application code
- License: **MIT**; upstream license:
  https://github.com/electron-userland/electron-builder/blob/v26.15.3/LICENSE

The desktop icon is generated from the Mage tile (84) of the Kenney Tiny
Dungeon sheet above using only Node built-ins; no additional art is included.

## Monaco editor

- Version: **0.57.0**
- Source: https://registry.npmjs.org/monaco-editor/-/monaco-editor-0.57.0.tgz
- Tarball SHA-256: `3ea1712fbacd3290cf4751007e3a5b57cc279767607812d4c3e57925fb0b05c2`
- License: **MIT**; full upstream `LICENSE` and `ThirdPartyNotices.txt` are in
  `src/specvania/ui/static/vendor/monaco/`.
- Offline reproduction: `.venv/bin/python apps/desktop/scripts/trim-monaco.py`
  from the pinned package's unpacked `min/vs`, `LICENSE` and
  `ThirdPartyNotices.txt` in `.monaco-src/`.
- Trimmed: language services, LSP facade, css/html/json/ts worker modules and
  assets, unused basic languages, non-English NLS and development files.
  The AMD entry point retains core exports and editor contributions, without
  service registration or the upstream blob-worker override. Basic languages:
  Python, JavaScript, TypeScript, Markdown, YAML, HTML, CSS, shell and `ini`
  (TOML fallback). JSON uses a small Monarch tokenizer without a service.
  English NLS remains embedded in the core. The codicon font is extracted
  unchanged from the CSS to a same-origin file. The plain diff editor worker
  is unchanged and started by a same-origin wrapper with its ready handshake.
