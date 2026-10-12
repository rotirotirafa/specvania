# Synthetic demo

This is a **synthetic**, hand-built event stream, not a recorded provider run.
The world, model ids, token counts, reviews, commit hashes and bug are fictional.
No code was executed and no model was called to produce these events.

Run `specvania replay demo` (optionally `--speed 1`, `--speed 2` or `--speed 4`).
It needs no config, API key, git repository or ledger. All assets are local;
the UI uses only the loopback server. Default auto mode opens the desktop app
when installed, otherwise a browser tab with a one-line notice.

The two areas demonstrate locked, open, active, cleared, failed and skipped rooms.
The party encounters a cooldown and fallback, respawns after a rejected review,
and passes an area's boss fight. A skipped room opens a bug (a monster for the
Bestiary); the player later closes it as wontfix. Its dependent door stays locked,
and the player aborts the run. Recorded gaps are capped at two seconds by the
replay server before the chosen speed is applied.

T23 supplies a real recorded run separately.
