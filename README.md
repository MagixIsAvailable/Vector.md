# Vector MCP — give an abandoned Anki Vector a local LLM brain

[![Buy Me a Coffee](https://img.shields.io/badge/buy%20me%20a-coffee-FFDD00?logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/magixb)

Resurrect an Anki Vector 1.0 robot (orphaned when Anki went bankrupt in
2019) using [wire-pod](https://github.com/kercre123/wire-pod), then give
him a real local AI brain — no cloud, no subscriptions, no Anki servers,
no monthly fee, nothing phoning home. He runs on a $80 Raspberry Pi and
talks entirely offline.

**New to this?** You don't need to be a robotics expert. You do need to be
comfortable typing commands into a terminal and not panicking when
something doesn't work the first time — this is a real hobbyist project,
not a polished app. Budget a full weekend for your first setup, most of
which is patience during the wire-pod flashing step, not this repo's fault.

Built in one very long night, then rebuilt properly over the following
weeks. Full blow-by-blow of every bug and how it was found and fixed:
**[DEVLOG.md](./DEVLOG.md)**. Every script explained: **[docs/scripts.md](./docs/scripts.md)**.

## Is this for me?

✅ You have (or can get cheap/secondhand) an **Anki Vector 1.0** robot —
Anki went bankrupt in 2019, these show up used/thrifted for very little
✅ You're willing to spend real time flashing custom firmware (the fiddly
part, not really this project's fault — it's a BLE flashing process)
✅ You have a **brain host** to run the software on — a Raspberry Pi 5
(cheap, low-power, meant to stay on 24/7) or just your existing desktop/
laptop (this was actually built and tested on a Windows desktop with an
RTX 3090 *first*, then ported to a Pi once it worked — the Pi's only
advantage is being cheap enough to leave running all the time; see
[Hardware](#hardware) below for both paths)
✅ You're okay with a small local AI (think "occasionally says something
slightly odd," not GPT-4-level polish) in exchange for zero ongoing cost
and zero cloud dependency

❌ Not for you if you want a plug-and-play experience — this is DIY
❌ Not for you if you need the Vector 2.0 (untested here, may differ)

## What you get

- 🗣️ **Conversational brain** — a small local LLM (via [Ollama](https://ollama.com),
  runs entirely on the Pi's CPU, no GPU needed) answers anything Vector's
  asked, spoken aloud through his own text-to-speech
- 👀 **Vision** — camera capture + YOLO object detection, with results
  synced into an Obsidian vault as a real knowledge graph
- 🧠 **Persistent memory** — sightings, teachings, and a daily diary written
  by the robot himself (via a second local LLM pass)
- 🎓 **Voice teaching** — add new spoken Q&A pairs to his native intent
  system on the fly
- 🙋 **Self-limiting agency** — his conversation brain can *ask* for new
  abilities (logged, needs human approval) but can never grant itself
  anything
- 🤖 **Multi-agent safe** — every action is logged with WHO did it (which
  human, which AI, which automation), eye colour signals who's driving
- ⏰ **Autonomous scheduling** — n8n drives the Life Engine
  (`vector_life.py`): proactive speech, idle humming, greetings, mood,
  curiosity scans, reminders, battery watchdog, ambient animations; plus
  diary/reflection workflows
- 🎵 **Humming** — nine synthesized hums played through his speaker,
  regenerate more with `make_hums.py`

## Project scope

This is **not** a from-scratch robotics project — it stands entirely on
the wire-pod community's excellent work reverse-engineering Vector's
protocol. What this project adds on top:

1. A **Python MCP server** (`vector_mcp_server.py`) wrapping the Vector SDK
   as tools any MCP-capable LLM client (Claude Code, or any other MCP
   client) can call
2. A **local LLM brain proxy** (`brain_proxy.py`) that gives wire-pod's
   knowledge-graph feature real function-calling, a request/approval loop,
   and correct SSE streaming (see DEVLOG for why that took real debugging)
3. **Perception & memory**: YOLO detection, an Obsidian-vault knowledge
   graph, diary/reflection generation
4. **Multi-agent infrastructure**: an operator logbook so multiple humans
   and AI agents can safely share one robot

## Hardware

| Component | Spec | Status |
|---|---|---|
| Robot | Anki Vector 1.0, flashed to Escape Pod firmware | required; 2.0 untested |
| Brain host | **Raspberry Pi 5, 8GB RAM** | ✅ the config this README's install steps and Known Issues are written against — runs wire-pod + Ollama + the brain proxy + n8n + the watchdog all at once, with real memory pressure already visible at 8GB (don't go smaller). Cheap enough to leave running 24/7, which is the only reason it's the recommended target. |
| Brain host, alternative | **Any desktop/laptop (Windows/Linux/Mac)** | ✅ actually how this project started — built and run for weeks on a Windows desktop with an RTX 3090 before ever touching a Pi. Nothing in the code is Pi-specific (`vector_life.py` explicitly branches on `os.name` for platform differences); the Pi is a deployment choice for 24/7 uptime at low power draw, not a requirement. If your desktop is already on most of the time anyway, skip the Pi entirely and run everything here instead. |
| Brain host, alternatives (Pi) | Pi 5 4GB, Pi 4 (any RAM) | ⚠️ untested — probably too tight for the full stack together, might work if you run n8n elsewhere instead |
| LLM | `qwen2.5:3b-instruct` via [Ollama](https://ollama.com) | ~2.4s/reply, ~6 tok/s on the Pi 5's CPU, no GPU needed. On a desktop with a real GPU you're not limited to this — the original build ran a 20B model via LM Studio on an RTX 3090 with no issues; the small CPU-only model is a Pi-specific concession, not a hard project limit. |
| Vision | YOLO11-nano via `ultralytics` | CPU is fine, ~100ms/frame |
| Automation | [n8n](https://n8n.io) (self-hosted) | can run on the Pi itself, on your desktop, or a separate machine entirely |
| Knowledge base | Obsidian (optional) | any Markdown-writing target works; this is just where we chose to put it |

**A note on the LLM choice**: small local models are genuinely limited —
don't expect ChatGPT-level conversation. `qwen2.5:3b-instruct` was chosen
specifically because it *reliably replies at all* on CPU-only ARM
hardware — that's a Pi constraint, not a project constraint. If you're
running the desktop path with a real GPU, feel free to point `brain_proxy.py`
at something bigger via Ollama or LM Studio; just watch out for reasoning
models silently returning empty replies (see
[Known Issues](#known-issues-the-ones-that-will-bite-you-too)) regardless
of which host you're on.

## Architecture

```
Vector (robot) <--BLE/WiFi--> wire-pod (local server, replaces Anki cloud)
                                   |
                          knowledge-graph endpoint
                                   |
                            brain_proxy.py  <---->  Ollama (local LLM, on-device)
                                   |                      ^
                        (tool-calling: request_capability) |
                                   |                      |
                            /run/<name> HTTP bridge -------+
                                   ^  (daily / weekly / babysitter /
                                   |   proactive / mindspeak)
                                   |
                            n8n (Schedule Trigger -> HTTP Request node)
                                   |
              vector_watchdog.py  (always-on, connects directly, no n8n)

vector_mcp_server.py  <--MCP (stdio/HTTP)-->  your AI agent of choice
        |
   controls robot directly via wire-pod's Vector SDK fork
```

**Three tiers, three different dependency stories — worth understanding
before you assume something is required when it isn't, or optional when
it isn't:**

1. **Always-on core — no dependency on anything else here.**
   `brain_proxy.py` (conversation) and `vector_watchdog.py` (pickup/cliff/
   motion/sound reflexes) are real daemons that connect to Vector directly
   and just run. Kill n8n, kill the MCP server, doesn't matter — these two
   keep working exactly as before.
2. **Scheduled/proactive layer — needs n8n.** Idle humming, greetings,
   mood shifts, curiosity scans, reminders, the battery watchdog schedule,
   diary/reflection, and daily/weekly self-improvement passes are **not**
   background loops — `vector_life.py`/`proactive.py`/`self_improve.py`/
   `battery_babysitter.py`/`speak_mind.py` run once and exit each time.
   Something has to trigger them on a schedule, and that's n8n's whole job
   here. No n8n running = none of this fires, ever, even though the
   scripts themselves are fine.
3. **Operator/dev control — optional, human-in-the-loop only.**
   `vector_mcp_server.py` exists purely so an AI agent (Claude Code, or
   any other MCP client) can help *you* drive him, inspect his state, or
   change his code interactively. Vector's own personality/reflexes don't
   need an agent connected at all — MCP is a control surface for humans +
   AI collaborating on the project, not part of his runtime.

**Why an HTTP bridge instead of n8n just running the scripts directly**:
on this build n8n's "Execute Command" node is disabled (a real security
hardening choice, not a bug) — n8n workflows can't shell out to arbitrary
commands. So `brain_proxy.py` exposes a small allow-listed
`GET/POST /run/<name>` endpoint (`daily`, `weekly`, `babysitter`,
`proactive`, `mindspeak` — a fixed dict, nothing dynamic/injectable) that
runs the matching script and returns its stdout/stderr as JSON. n8n's
Schedule Trigger nodes just call that endpoint over plain HTTP instead of
executing anything themselves. If you're setting this up yourself and
want a different security posture, you could re-enable Execute Command
instead and skip the HTTP layer — this pattern exists because of a
specific hardening decision made here, not because it's the only way.

## Installation

Take this step by step — don't skip ahead, each one assumes the last
actually worked.

### 1. Flash Vector to wire-pod (budget 1-2 hours, the fiddly step)

Follow [wire-pod's own installation guide](https://github.com/kercre123/wire-pod/wiki/Installation).
You'll put the robot in recovery mode and flash "Escape Pod" (ep) firmware
over Bluetooth. **Expect this to not work first try** — Bluetooth stack
quirks are common, not a sign you did something wrong. See DEVLOG for a
real account of what went wrong here the first time.

### 2. Set up your brain host — Raspberry Pi, or just your desktop

**Pick one:**
- **Raspberry Pi** (recommended if you want this running 24/7 without
  tying up your main computer) — fresh Raspberry Pi OS (64-bit) install,
  SSH enabled.
- **Your existing desktop/laptop** (Windows/Linux/Mac, GPU optional) —
  this is genuinely how the project was first built and run for weeks,
  the Pi came later purely for 24/7 uptime at low power. Nothing below is
  Pi-specific; just run it where you already are. If you're on Windows,
  the same `pip`/`ollama` commands work in PowerShell — swap `venv/bin/`
  for `venv\Scripts\` and skip the Linux install script for Ollama in
  favor of the [Windows installer](https://ollama.com/download/windows).

Either way, install [Ollama](https://ollama.com/download) and pull the
model (or point `VECTOR_BRAIN_MODEL` at something bigger later if you're
on a GPU-equipped desktop — see [Hardware](#hardware)):
```
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b-instruct
```

### 3. Install the Python SDK fork

The **PyPI `anki_vector` package does not work** on modern Python (asyncio
and protobuf incompatibilities). Use the community fork instead:

```
python3 -m venv venv
venv/bin/pip install "git+https://github.com/kercre123/wirepod-vector-python-sdk.git"
venv/bin/python -m anki_vector.configure
```

### 4. Install this project's dependencies

```
venv/bin/pip install mcp ultralytics flask
```

### 5. Run the brain proxy and point wire-pod at it

```
venv/bin/python brain_proxy.py
```
In wire-pod's web UI, set the knowledge-graph provider to "custom" with
endpoint `http://localhost:8590/v1`.

### 6. Set up n8n (needed for anything proactive/autonomous — see [Architecture](#architecture))

Without this step, Vector will still talk when spoken to (`brain_proxy.py`)
and still react to being picked up/dropped (`vector_watchdog.py`) — but
he'll never say anything *unprompted*, no idle humming, no curiosity, no
diary. That all depends on n8n actually running and triggering things.

Install n8n on the Pi (or any machine that can reach the Pi over HTTP —
doesn't have to be co-located):
```
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/master/install.sh | bash
nvm install --lts
npm install -g n8n
```

Run it once manually first to confirm it starts (`n8n start`, check
`http://<pi-ip>:5678` loads in a browser), then make it survive reboots
as a **user-level** systemd service (no `sudo`, works fine on a
single-user Pi):
```
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/n8n.service <<'EOF'
[Unit]
Description=n8n
After=network-online.target

[Service]
Type=simple
Environment=PATH=/home/<you>/.nvm/versions/node/<version>/bin:/usr/local/bin:/usr/bin:/bin
WorkingDirectory=/home/<you>
ExecStart=/home/<you>/.nvm/versions/node/<version>/bin/n8n start
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now n8n.service
loginctl enable-linger $USER   # lets it run even when you're not SSH'd in
```

Then, in n8n's web UI (`http://<pi-ip>:5678`), build one workflow per
script you want scheduled — each is just two nodes:
**Schedule Trigger** (pick an interval, e.g. every 10 min for proactive
speech, every 5 min for mindspeak, daily/weekly for self-improve) →
**HTTP Request** node calling `GET http://localhost:8590/run/<name>`
where `<name>` is one of `daily`, `weekly`, `babysitter`, `proactive`,
`mindspeak`. That's the entire integration — no credentials, no extra
n8n nodes needed, since `brain_proxy.py` already does the real work and
just returns JSON.

Same `systemctl --user` + `loginctl enable-linger` pattern is worth using
for `brain_proxy.py` and `vector_watchdog.py` too if you want the whole
stack to survive a reboot without you needing to SSH in and restart
things by hand — see the unit files this build actually uses in
[docs/systemd/](./docs/systemd/) as a starting template.

**Running on Linux but not a Pi** (a spare desktop/server, e.g. Ubuntu
Server on a mini PC): use the exact same `systemctl --user` instructions
above, not the Windows section below — "Pi" here just means "a systemd
Linux host," nothing Pi-specific about it.

**On a Windows desktop instead of a Pi**: there's no systemd, so use
**Task Scheduler** to run each script "at log on" instead (this is
exactly how the original Windows-desktop build kept things running before
the Pi migration) — Task Scheduler → Create Task → Trigger: "At log on" →
Action: start `venv\Scripts\python.exe` with the script path as an
argument, one task per always-on script (`brain_proxy.py`,
`vector_watchdog.py`, and n8n itself if you installed it locally too). No
`Restart=on-failure` equivalent out of the box — if that matters to you,
wrap the python call in a small retry loop, or just accept that a crash
needs a manual restart on the desktop path (that was the actual tradeoff
made originally; systemd's auto-restart was one of the real reasons the
Pi ended up being worth the migration).

### 7. Register the MCP server (optional — human/AI operator control, not required for Vector to function)

From whatever machine runs your AI agent (doesn't have to be the Pi):

```
claude mcp add vector --env VECTOR_OPERATOR=<yourname> -- ssh -i <key> <user>@<pi-ip> <pi-venv-path>/python3 <pi-path>/vector_mcp_server.py
```

(Or run the agent directly on the Pi and skip the SSH wrapper — the SSH
bridge is only there because this build's agent runs on a separate
Windows machine.)

### 8. Say hi

Ask your agent to call `vector_say` with a test phrase, or just talk to
Vector out loud once wire-pod's voice pipeline confirms it started (look
for `wire-pod started successfully!` in its logs — the webserver
responding is *not* proof the voice pipeline itself is running, a common
early confusion, see Known Issues).

Before you get much further, skim [Configuration](#configuration--what-to-actually-edit-for-your-own-setup)
below — it's the one place listing every setting worth personalizing
(your name for the activity log, which model, an optional Obsidian vault
path, etc.) instead of grepping through the scripts to find them.

## Configuration — what to actually edit for your own setup

The install steps above get the stack *running*; this section is what to
personalize so it's running as *your* Vector, not a demo. Everything here
is an environment variable with a working default, not a hardcoded value
you need to hunt through source files for — set the ones you care about
before launching `brain_proxy.py` / `vector_watchdog.py` / `vector_mcp_server.py`
(export them in your shell profile, or add them to whichever systemd unit
/ Task Scheduler action starts each script — see [docs/systemd/](./docs/systemd/)
for where that goes in a unit file).

**Robot pairing itself is not one of these** — that's handled once by
`venv/bin/python -m anki_vector.configure` (step 3 above), which writes
`~/.anki_vector/sdk_config.ini`. The variables below are everything *on
top of* that.

| Variable | Used by | Default | What it's for |
|---|---|---|---|
| `VECTOR_ROBOT_NAME` | `vector_mcp_server.py` (dashboard) | `Vector` | Cosmetic — shows up in the `vector_dashboard` tool's output. Purely a label, doesn't affect the actual SDK connection. |
| `VECTOR_ROBOT_IP` | `vector_mcp_server.py` (dashboard) | `<set VECTOR_ROBOT_IP>` | Same as above — display only. The real connection uses `sdk_config.ini`, not this. |
| `VECTOR_ROBOT_SERIAL` | `vector_mcp_server.py` (dashboard) | `<set VECTOR_ROBOT_SERIAL>` | Same as above — display only. |
| `VECTOR_OPERATOR` | all three always-on scripts | `unknown` (`vector` inside `brain_proxy.py` specifically, since those requests are his own voice) | **Set this to your own name/handle.** Every logged action records who did it — this is how the multi-agent logging design (see DEVLOG.md) tells "Mike drove him" from "the n8n schedule did" apart. |
| `VECTOR_VAULT_DIR` | `vector_mcp_server.py`, `vector_life.py`, `self_improve.py` | tries the real desktop vault path, falls back to `~/vector-mcp/vault_sync/Vector Mind` | Only matters if you're syncing his diary/dashboard/memory writes into your own Obsidian vault. Point it at that vault's folder if you want that; the fallback staging folder works fine standalone if you don't. |
| `VECTOR_BRAIN_URL` | `brain_proxy.py`, `vector_life.py`, `vector_mcp_server.py` | `http://localhost:11434` | Where Ollama (or LM Studio, if you're on the original desktop-first path) is listening. Only change this if your brain host isn't the same machine, or you're on a non-default port. |
| `VECTOR_BRAIN_MODEL` | `brain_proxy.py`, `vector_mcp_server.py` | `qwen2.5:3b-instruct` | Which model to use as the conversation brain. Change this once you've pulled a different model — see [Hardware](#hardware) for what your GPU/CPU can actually run. |
| `VECTOR_PORT` | `brain_proxy.py` | `8590` | The port wire-pod's knowledge-graph endpoint should point at. Only change if `8590` is already taken on your host. |
| `VECTOR_CUBE_MODEL` | `vector_mcp_server.py` | `~/vector-mcp/cube_dataset/runs/cube/weights/best.pt` | Path to a fine-tuned cube-detection YOLO model, if you've trained one. Falls back to the stock COCO model otherwise — totally optional. |
| `VECTOR_TV_IP` | `vector_mcp_server.py`, `vector_life.py` | `<set VECTOR_TV_IP>` | Only needed for the optional Fire TV control feature (ADB over WiFi). Leave unset if you don't have/want that. |
| `VECTOR_ADB` | `vector_mcp_server.py` | auto-detected via `PATH` | Only needed if `adb` isn't already on your system `PATH` and you're using the Fire TV feature. |

**Not yet an env var, still a plain source edit if you want to change
it**: `vector_personality.py`'s `BASE_PERSONALITY` (his actual character —
edit this file directly if you want a different personality than the one
shipped here; it's deliberately centralized in this one file and imported
by `vector_life.py`, `vector_watchdog.py`, and `brain_proxy.py` so a
personality change only needs editing once) and `news_digest.py`'s RSS
feed list (hardcoded, since it's a personal preference list, not
per-install config).

## Known issues (the ones that'll bite you too)

The short version — full diagnosis and fixes are in **DEVLOG.md**:

- **Reasoning models return empty replies.** They spend the whole token
  budget on invisible `reasoning_content` and never actually answer.
  Confirmed live testing a bigger "upgrade" model here: 100% timeout rate
  across every test prompt, zero successful replies. Stick to a plain
  instruction model.
- **wire-pod always requests `stream: true`.** If you build a proxy in
  front of it, you MUST reply with real SSE or wire-pod's client crashes
  with an opaque `Stream error: error, <nil>` — 100% failure rate, no
  useful error message.
- **wire-pod's custom-intent API is 1-indexed**, not 0-indexed, for
  edit/delete operations.
- **wire-pod's Lua sandbox has exactly 4 functions.** No movement functions
  exist — route movement-like voice commands to real built-in intents.
- **wire-pod's webserver responding does not mean the voice pipeline is
  running.** The two are separate; a robot that drives/sees/speaks fine
  via direct SDK calls but never answers spoken questions usually means
  the knowledge-graph endpoint, STT language model, or a certificate
  config flag was never actually set — check wire-pod's own logs for the
  real startup sequence, not just "is the webserver up."
- **A corporate/consumer antivirus doing TLS inspection** (Norton, in this
  build) will MITM the robot's connection. Fix is a dual-trust certificate
  bundle — see DEVLOG.
- **Never hardcode an LLM model name** in a script that outlives the model
  you tested with. It breaks silently the next time you swap models.
- **`drive_straight` silently no-ops while Vector is docked** — it returns
  OK in under a second with zero movement. Check `robot.status.is_on_charger`
  and call `drive_off_charger()` first if needed.
- **`vector_watchdog.py` will crash-loop forever (harmlessly) if Vector is
  simply offline** — no battery, WiFi drop, screen asleep, whatever. It's
  not a bug: it can't do anything without a live connection, so it fails
  fast and `systemd` retries it every ~10s indefinitely. Seeing hundreds
  or thousands of restarts in `systemctl --user status` doesn't
  necessarily mean anything is broken in the code — check whether the
  robot itself is actually reachable first (`ping <robot-ip>`) before
  assuming it's a real bug.
- **`journalctl` can rotate crash logs out from under you** if a service
  is restarting every few seconds — by the time you run
  `journalctl --user -u <service>` separately it may already say
  "No journal files were found" even though `systemctl status` just
  showed you log lines seconds ago. If that happens, stop the service and
  run the script directly in the foreground once
  (`venv/bin/python3 whatever.py`) to catch the real traceback instead of
  chasing the journal.
- **n8n's "Execute Command" node may be disabled** on hardened instances
  — if your workflows can't shell out, don't fight it; add a small
  allow-listed HTTP endpoint to a service you already have running
  instead (see `brain_proxy.py`'s `/run/<name>` route) and call that from
  an HTTP Request node.

**Hit something not listed here or in DEVLOG.md?** [Open an
issue](../../issues/new/choose) — bug reports and feature ideas both
welcome, see [CONTRIBUTING.md](./CONTRIBUTING.md) for what's useful to
include.

## Credits

- [wire-pod](https://github.com/kercre123/wire-pod) by kercre123 and
  contributors — the entire local-server foundation this stands on
- [wirepod-vector-python-sdk](https://github.com/kercre123/wirepod-vector-python-sdk) —
  the SDK fork that makes any of this possible on modern Python
- [Model Context Protocol](https://modelcontextprotocol.io) — Anthropic
- [Ultralytics YOLO](https://github.com/ultralytics/ultralytics)
- [Ollama](https://ollama.com) — local model runtime

## Contributing

Issues, ideas, and PRs are welcome — see [CONTRIBUTING.md](./CONTRIBUTING.md)
for what to check first and how the three-tier architecture affects where
a fix or feature should live.

## Support

If this helped you resurrect your own Vector, consider [buying me a
coffee](https://buymeacoffee.com/magixb) — everything here stays free and
open either way, it just helps keep the lights on for more of this.

## License

MIT — do whatever you want with it, robot resurrections should be free.
See [LICENSE](./LICENSE) for the one carve-out (the vendored Anki SDK
under `wirepod-sdk-src/` keeps its own Apache 2.0 terms).
