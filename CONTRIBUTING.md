# Contributing

This is a hobbyist project maintained in spare time, not a company. That
said — issues, ideas, and pull requests are genuinely welcome, especially
from anyone else resurrecting an orphaned Vector 1.0.

## Before opening an issue

1. Check **[DEVLOG.md](./DEVLOG.md)** first. It's the real changelog — every
   bug hit during development, how it was found, and how it was fixed, in
   the order it happened. A lot of "weird" behavior (silent voice pipeline,
   battery volts reading 0.0, control lockups after an interrupted move) is
   already documented there, not a new bug.
2. Work out which layer is affected — see the README's three-tier
   architecture (always-on core / n8n-scheduled layer / MCP operator
   control). Dependencies don't cascade: n8n being down doesn't mean the
   robot is broken, and the MCP server being disconnected doesn't mean
   autonomous behavior stopped. Knowing which tier narrows the search a lot.
3. Open an issue using the **Bug report** or **Feature request** template —
   [Issues tab](../../issues). Include logs where you can (`journalctl -u
   wirepod.service`, `brain-proxy.service`, `vector-watchdog.service`, as
   relevant) and redact your robot's real IP/serial/LAN details if you'd
   rather keep those private; generic placeholders are fine.

## Pull requests

- Keep PRs scoped to one thing — easier to review, easier to revert if
  something regresses on real hardware (this project has no CI running
  against an actual robot, only what the maintainer tests by hand).
- If you touch `vector_personality.py`, `brain_proxy.py`'s system-prompt
  construction, or anything model-facing, please read the "Landmines
  already stepped on" section of the project notes first — several past
  regressions (character consistency breaking under stacked system
  messages, reasoning models returning empty content, hardcoded model
  names silently breaking on an upgrade) came from re-introducing exactly
  these patterns.
- Never commit real credentials, LAN IPs, robot serials, SSH keys, or
  images from `captures/`/`memory/`/`snapshots/` — see `.gitignore`, which
  already blocks all of these categories by pattern. If you're adding a
  new script that reads local paths or network config, prefer an env var
  with a documented default over a hardcoded value, so it works for
  someone else's setup too.
- One tool that *acts* on the robot (drive, teach, change settings) should
  never be reachable from the autonomous conversation brain without an
  explicit human-approval gate — that boundary (`request_capability` being
  ask-only) is a deliberate safety design, not an oversight. PRs that widen
  it need to explain why in the description.

## Reporting a security issue

If you find something that could let the conversation brain (or a remote
caller) act on the robot without going through the approval gate, or that
would leak someone's LAN/credentials from a normal clone-and-run flow,
please open an issue directly rather than a PR with the fix inline —  happy
to discuss privately first if it's sensitive.

## Not sure where something belongs?

If it's actually about wire-pod itself (BLE flashing, pairing, the
voice/intent pipeline) rather than this repo's Python layer, it probably
belongs in [wire-pod's own repo](https://github.com/kercre123/wire-pod)
instead — the issue template links there too.
