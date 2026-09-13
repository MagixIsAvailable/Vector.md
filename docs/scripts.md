# Every script, explained

One-paragraph summary of every script in this repo, grouped by how it
actually runs. If you're new here, start with the **Always-on daemons** —
those three are the actual "always alive" part; everything else is either
triggered on a schedule (n8n, see the [main README's Architecture
section](../README.md#architecture)) or a one-off tool you run by hand.

## Always-on daemons (systemd services, see [docs/systemd/](./systemd/))

- **`brain_proxy.py`** — sits between wire-pod and Ollama. Gives Vector's
  conversation brain real function-calling (`request_capability` so he can
  *ask* for new abilities instead of hacking around missing ones, plus a
  read-only `look` tool for camera+YOLO perception) and correct SSE
  streaming (wire-pod always requests `stream: true`). Also exposes the
  `/run/<name>` HTTP bridge n8n calls for scheduled scripts.
- **`vector_watchdog.py`** — the one script that holds a single continuous
  robot connection and reacts in real time: picked up / put down / cliff
  edge (danger reflexes), petted / backpack button (life events), motion/
  sound-triggered turns, clap-to-dance. Not an n8n job — this has to be a
  standing process to react within milliseconds, not on a poll interval.
- **`vector_mcp_server.py`** — exposes Vector as MCP tools (`vector_say`,
  `vector_drive`, `vector_recall`, etc.) for any MCP-compatible AI agent.
  Optional / operator-control only — see the Architecture section for why
  this one is different from the other two.

## Scheduled via n8n (each is a one-shot script, not a loop — see [Installation step 6](../README.md#6-set-up-n8n-needed-for-anything-proactiveautonomous--see-architecture))

- **`vector_life.py`** — the Life Engine. Every tick (~5-10 min) decides
  one or more of: proactive speech, idle humming, greetings, mood update,
  curiosity scans, reminders, battery watchdog check, ambient animations,
  diary/reflection triggers. This is most of what makes Vector feel
  "alive" rather than purely reactive.
- **`proactive.py`** — thin backwards-compatible wrapper so an existing
  n8n workflow name still works; just calls `vector_life.py`'s `main()`.
- **`speak_mind.py`** — standalone, always speaks one short unprompted
  line every time it's called (unlike `vector_life.py`'s proactive path,
  which is gated by cooldown + person-visible checks). The n8n schedule
  interval itself is the cooldown — keep it sane (5 min suggested).
- **`self_improve.py`** — two modes. `--mode daily`: first-person diary
  entry written to the vault. `--mode weekly`: reviews the week's logs and
  proposes improvements as checkboxes for a human to approve — machine
  proposes, human approves, never self-applies.
- **`battery_babysitter.py`** — checks battery via an observer connection
  (no behavior control needed); sends Vector home to charge if voltage is
  low and he's not already charging. Outputs JSON so n8n can branch on it.
- **`vec_discover.py`** — revives a "discovery" loop: grabs a frame, runs
  YOLO, diffs the labels against everything he's ever seen. A genuinely
  new label gets saved, logged to memory, and announced aloud in character.
- **`news_digest.py`** — fetches a handful of headlines, writes a compact
  digest (kept short deliberately — small local models attend worse to
  long context blocks) for the brain's context and a dashboard endpoint.
- **`charge_monitor.py`** — samples battery voltage every N minutes to
  csv, to determine whether he's *actually* charging vs. what
  `is_charging` claims (a single SDK-connect reading isn't reliable —
  connecting wakes him and sags the pack).
- **`curious_patrol.py`** — extends native curiosity past "cube only"
  (his firmware's real limit): drives short legs, runs YOLO recognition
  after each one so he reacts to whatever's actually in view.

## One-off tools you run yourself

- **`make_hums.py`** — generates Vector's humming library (short, no-lyric
  vocal-ish tones with vibrato) as 16kHz/16-bit/mono WAVs — the only
  format his speaker accepts.
- **`midi_to_hum.py`** — imports a real `.mid` file and feeds the
  extracted melody into `make_hums.py`'s renderer, instead of hand-coding
  notes one at a time.
- **`beat_audio.py`** — shared onset/tempo detection from Vector's
  AudioFeed (used for clap-to-trigger and beat-synced dancing). Works
  within a real SDK limitation: `AudioFeed` only exposes pre-processed
  fields, never raw PCM.
- **`capture_rig.py`** — drives Vector through a scan pattern (rotate,
  capture, advance, repeat) logging camera frames + pose, for feeding
  into COLMAP/Postshot/nerfstudio for a 3D Gaussian-splat reconstruction
  of a room.
- **`play_all_animations.py`** — plays every one of his 476 animation
  triggers back-to-back once, holding one continuous connection (cheaper
  than grab/release per animation), logging OK/FAIL per trigger so a run
  can resume from where it left off. Built for a full-library recording
  session.
- **`vector_animation_tour.py`** — similar idea, documents/records each
  trigger individually with a CSV manifest for syncing recordings up
  afterward.
- **`vector_probe.py`** — a fixed rubric of prompts run against the live
  brain via a no-side-effects header (no mood/speech-log/tool-call
  writes), scored deterministically for persona drift.
- **`vector_stats.py`** — deterministic evaluation stats from the raw
  memory logs: speech volume/vocabulary/repetition, action counts/error
  rates/latency, persona lexicon drift watch.
- **`speak_text.py`** — minimal bridge: speaks arbitrary text through
  Vector's TTS using the known-working Python SDK connection. Exists as a
  workaround for wire-pod's Go-side auth failing against Escape-Pod
  firmware in this build.
- **`vector_personality.py`** — not a runnable script, the single
  code-side source of truth for Vector's approved personality text,
  imported by `vector_life.py`, `vector_watchdog.py`, and `brain_proxy.py`
  so it only needs editing in one place.
