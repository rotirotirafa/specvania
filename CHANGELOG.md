# Changelog

All notable changes to specvania are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Release dates remain Unreleased until the owner tags the release.

## [Unreleased]

## [0.3.0] - Unreleased

### Added

- A desktop game that guides a project from idea and Clarify through Save map,
  expedition, approvals, bosses and the end screen.
- A long-lived studio started by the desktop app, with a secure local handshake.
- A castle built from the world: one floor per area and one chamber per room,
  with Kenney Tiny Dungeon art and event-driven party animations.
- A read-only Monaco room panel for the mission, code, diff and review.
- A per-run token budget shown as stamina, with a Rest choice when exhausted.
- English and Brazilian Portuguese for the game and the party's prose.
- Watch demo with offline replay, pause and speeds of 1×, 2× and 4×; the bundled
  demo is synthetic until a real run is recorded.
- Check the party and `specvania doctor --probe` to check configured models
  before an expedition.
- Release automation for the Python CLI on PyPI and unsigned desktop builds
  (macOS arm64/x64 dmg and Linux AppImage) on GitHub Releases.

### Changed

- The desktop game becomes the main surface; the CLI remains available for
  scripting. This builds on the 0.2.0 development history: the SQLite/Postgres
  ledger, features and spec versions, bugs as monsters, the Electron desktop
  window and desktop approvals.
