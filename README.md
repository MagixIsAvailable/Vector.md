# Vector MCP — give an abandoned Anki Vector a local LLM brain

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
✅ You have or can get a **Raspberry Pi 5 (8GB)** — this is the only
config actually tested, see [Hardware](#hardware) below
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
| Brain host | **Raspberry Pi 5, 8GB RAM** | ✅ the only config actually verified — runs wire-pod + Ollama + the brain proxy + n8n + the watchdog all at once, with real memory pressure already visible at 8GB (don't go smaller) |
| Brain host, alternatives | Pi 5 4GB, Pi 4 (any RAM) | ⚠️ untested — probably too tight for the full stack together, might work if you run n8n elsewhere instead |
| LLM | `qwen2.5:3b-instruct` via [Ollama](https://ollama.com) | ~2.4s/reply, ~6 tok/s on the Pi 5's CPU — no GPU involved anywhere in this build |
| Vision | YOLO11-nano via `ultralytics` | CPU is fine, ~100ms/frame |
| Automation | [n8n](https://n8n.io) (self-hosted) | can run on the Pi itself or a separate machine |
| Knowledge base | Obsidian (optional) | any Markdown-writing target works; this is just where we chose to put it |

**A note on the LLM choice**: small local models are genuinely limited —
don't expect ChatGPT-level conversation. `qwen2.5:3b-instruct` was chosen
specifically because it *reliably replies at all* on CPU-only ARM
hardware. Bigger "smarter" models we tried either need a GPU we didn't
want to require, or turned out to be reasoning models that silently return
empty replies (see [Known Issues](#known-issues-the-ones-that-will-bite-you-too)).

## Architecture

```
Vector (robot) <--BLE/WiFi--> wire-pod (local server, replaces Anki cloud)
                                   |
                          knowledge-graph endpoint
                                   |
                            brain_proxy.py  <---->  Ollama (local LLM, on-device)
                                   |
                        (tool-calling: request_capability)

vector_mcp_server.py  <--MCP (stdio/HTTP)-->  your AI agent of choice / n8n
        |
   controls robot directly via wire-pod's Vector SDK fork
```

## Installation

Take this step by step — don't skip ahead, each one assumes the last
actually worked.

### 1. Flash Vector to wire-pod (budget 1-2 hours, the fiddly step)

Follow [wire-pod's own installation guide](https://github.com/kercre123/wire-pod/wiki/Installation).
You'll put the robot in recovery mode and flash "Escape Pod" (ep) firmware
over Bluetooth. **Expect this to not work first try** — Bluetooth stack
quirks are common, not a sign you did something wrong. See DEVLOG for a
real account of what went wrong here the first time.

### 2. Set up your Raspberry Pi

Fresh Raspberry Pi OS (64-bit) install, SSH enabled. Install
[Ollama](https://ollama.com/download/linux) and pull the model:
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

### 6. Register the MCP server

From whatever machine runs your AI agent (doesn't have to be the Pi):

```
claude mcp add vector --env VECTOR_OPERATOR=<yourname> -- ssh -i <key> <user>@<pi-ip> <pi-venv-path>/python3 <pi-path>/vector_mcp_server.py
```

(Or run the agent directly on the Pi and skip the SSH wrapper — the SSH
bridge is only there because this build's agent runs on a separate
Windows machine.)

### 7. Say hi

Ask your agent to call `vector_say` with a test phrase, or just talk to
Vector out loud once wire-pod's voice pipeline confirms it started (look
for `wire-pod started successfully!` in its logs — the webserver
responding is *not* proof the voice pipeline itself is running, a common
early confusion, see Known Issues).

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

## Credits

- [wire-pod](https://github.com/kercre123/wire-pod) by kercre123 and
  contributors — the entire local-server foundation this stands on
- [wirepod-vector-python-sdk](https://github.com/kercre123/wirepod-vector-python-sdk) —
  the SDK fork that makes any of this possible on modern Python
- [Model Context Protocol](https://modelcontextprotocol.io) — Anthropic
- [Ultralytics YOLO](https://github.com/ultralytics/ultralytics)
- [Ollama](https://ollama.com) — local model runtime

## Support

If this helped you resurrect your own Vector, consider [buying me a
coffee](#) — everything here stays free and open either way, it just
helps keep the lights on for more of this.

## License

MIT — do whatever you want with it, robot resurrections should be free.
See [LICENSE](./LICENSE) for the one carve-out (the vendored Anki SDK
under `wirepod-sdk-src/` keeps its own Apache 2.0 terms).
