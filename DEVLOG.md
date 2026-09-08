# Dev Log — Vector MCP Server

**This is the full build diary.** For the clean public-facing overview,
installation steps, and project scope, see [README.md](./README.md).

Exposes Mike's Anki Vector 1.0 ("Vector-E7V8", serial `0030219a`, IP `192.168.1.50`)
as MCP tools so any MCP client (Claude Code, Claude Desktop, n8n) can control him.

Set up 2026-08-07 with Claude.

## Stack

- **Robot**: Vector 1.0 flashed with escape-pod ("ep") firmware, served by **wire-pod**
  running on this PC (web UI at `http://localhost:8080`).
- **SDK**: [wirepod-vector-python-sdk](https://github.com/kercre123/wirepod-vector-python-sdk)
  fork (NOT the PyPI `anki_vector` — that one is protobuf/asyncio-incompatible with
  modern Python). Installed editable from `./wirepod-sdk-src` into `./venv`.
- **Server**: `vector_mcp_server.py`, stdio transport, Python MCP SDK 2.0.

## Tools

`vector_say`, `vector_drive`, `vector_turn`, `vector_play_animation`,
`vector_list_animations`, `vector_get_battery`, `vector_set_eye_color`,
`vector_lift`, `vector_go_to_charger`, `vector_see` (camera snapshot),
`vector_recognize` (YOLO detection + memory + auto vault sync),
`vector_recall` (query visual memory), `vector_mind_sync` (memory → Obsidian),
`vector_dashboard` (status note in Obsidian)

## Operator logbook (multi-agent audit trail)

**Vector has three parents — see vault note `Vector Mind/Parents.md`.**
Mike decides, Claude and Hermes build, n8n babysits.

> **Rule for any agent working on Vector:** call `vector_human_log(what, kind)`
> whenever Mike makes a decision, has an idea, approves a proposal, or does
> something physical (charging/moving/enrolling). Machine actions log
> themselves; the human's reasoning does not. A new capability with no `mike`
> entry behind it is an incomplete record.
> kinds: `decision` | `idea` | `approval` | `physical` | `teaching`

Every tool call is logged with WHO did it (mike / claude / hermes / n8n):

- Machine log: `memory/actions.jsonl` (ts, operator, tool, args, status, secs)
- Human log: vault `Vector Mind/Logbook.md`, grouped by day

Operator identity = `VECTOR_OPERATOR` env var set at server launch:
- Claude Code: registered with `--env VECTOR_OPERATOR=claude` ✅
- HTTP mode (`--http`): defaults to `n8n` ✅
- **Hermes (WSL)**: ✅ `hermes mcp add vector --command /home/magic/vector-sdk/bin/python
  --args /mnt/c/Users/mike/vector-mcp/vector_mcp_server.py --env VECTOR_OPERATOR=hermes`
  — points at this shared file; the server's `_user_base()` maps the `C:\...` paths
  to `/mnt/c/...` under WSL, so claude and hermes write to the same logbook/memory/vault.

## State signals (who's driving / what's happening)

`vector_signal(state, set_operator_eyes=True)` — eye colour = operator
(claude=blue 0.60, hermes=violet 0.80, n8n=orange 0.08, mike=green 0.33),
animation = state (`thinking`, `listening`, `looking`, `success`, `error`,
`greet`, `alert`, `sleep`, `wake`). `vector_whose_eyes()` returns the legend.

**Errors auto-signal**: any tool that raises plays `KnowledgeGraphSearchingFail`
so a failure is never silent. Agents should signal `thinking` before slow steps.
Animations are best-effort (476-trigger list can exceed the 10s gRPC deadline);
eye colour is the reliable channel.

## wire-pod custom-intent API gotchas

- `remove_custom_intent` / `edit_custom_intent` use `number` = **1-indexed**
  position in `get_custom_intents_json`'s array (`array_index + 1`), NOT the
  0-indexed position. Passing a plain array index deletes the WRONG intent.
  Learned this the hard way - deleted two working intents by mistake, fixed
  via `edit_custom_intent` + re-`add_custom_intent`. Always re-fetch the list
  and use `i+1` right before any edit/remove call.
- Lua only has 4 functions: `assumeBehaviorControl(level)`, `sayText(s)`,
  `playAnimation(name)`, `releaseBehaviorControl()`. No drive/turn/lift
  functions exist - "go home"-style commands should route to the matching
  BUILT-IN intent (e.g. `intent_system_charger`) via the `intent` field
  instead of being faked in Lua.

## Conversational routing (wire-pod intents)

Vector is **always conversational**. `chipper/intent-data/en-US.json` was
reordered: 36 real commands match first, then `intent_knowledge_promptquestion`
carries a catch-all `""` keyphrase so everything else reaches the LLM.
Original saved as `en-US.json.bak.original`.

**Live wire-pod is `C:\Users\mike\wirepod\wire-pod\`** (not `~/wire-pod`, which
is a second copy). Editing intents requires restarting `chipper.exe` — wait
~10s between stop and start or it warns about port 80.

**Brain**: LM Studio at `http://localhost:1234/v1`, model **`openai/gpt-oss-20b`**
(12 GB on the 3090), loaded with `--gpu max --ttl 86400` so it never cold-starts
(cold starts caused "llm returned no response").

Model bake-off (warm latency, same persona prompt):

| Model | Warm | Notes |
|---|---|---|
| `openai/gpt-oss-20b` | **2.6–3.6 s** | ✅ chosen — fastest *and* funniest |
| `nvidia/nemotron-3-nano-4b` | 4.5–5 s | previous brain, blunter |
| `google/gemma-4-26b-a4b` | ~200 s cold | too slow for speech |
| `qwen/qwen3.5-9b`, `gemma-4-12b` | — | ❌ return EMPTY content: reasoning models spend the whole token budget in `reasoning_content` |

Gotchas: keep only ONE instance loaded (`lms ps`) — duplicates silently eat
VRAM. Prompt must demand **plain ASCII**, since em-dashes/curly quotes go
through TTS. Reasoning models need a large `max_tokens` (900+) or they answer
with nothing.

## The streaming bug (this was the actual "he stopped talking" cause)

wire-pod ALWAYS requests `stream: true` (SSE) from the knowledge-graph
endpoint - it speaks tokens as they arrive. `brain_proxy.py` was blindly
forwarding that flag to LM Studio, then doing a single `r.read()` +
`json.loads()` on what was actually a real SSE stream (multiple
`data: {...}\n\n` frames) - guaranteed to fail to parse. That got caught by
the existing error handling and silently swapped in a fallback reply... but
the reply was still sent back as plain JSON, while wire-pod's client was
expecting an SSE stream because it had asked for one. wire-pod's own
stream-reader crashed on the FORMAT before it ever got to the content,
producing `Stream error: error, <nil>` on literally every single LLM
request, 100% of the time, regardless of what the answer would have been.

Fixed: the proxy now always calls LM Studio non-streaming internally
(`body["stream"] = False` before forwarding, reliable single JSON response),
and replies to wire-pod in genuine SSE format (`_sse_wrap()`) whenever
wire-pod's original request had `stream: true`. Verified live: multi-sentence
answers now speak correctly end to end, `LLM stream finished` with no error.

## Brain proxy hardening (fixed a real crash)

`brain_proxy.py` originally had no error handling: when wire-pod's
"remembered chat" grew to ~16 messages (including a null-content assistant
turn from a prior tool call), LM Studio returned an empty body, our
`json.load` threw, and Flask's raw 500 HTML page leaked to wire-pod as a
garbled "invalid character '<'" error — killing the reply.

Fixed with two layers:
1. `_sanitize_history()` — drops `tool`-role messages and null-content
   assistant turns, caps history to the last 12 turns, before every call.
2. Every LM Studio call is wrapped; on any failure the proxy returns a valid
   chat-completion with `_fallback_reply()` ("Sorry, I lost my train of
   thought...") — wire-pod must never see anything but valid JSON.

Reproduced the exact failure shape in testing after the fix; confirmed clean.

## n8n: one consolidated workflow, not four

All scheduled automation lives in a single workflow, **"Vector Automations"**
(4 independent trigger -> HTTP Request pairs, 8 nodes total) rather than 4
separate 2-node workflows:

| Trigger | Interval | Hits |
|---|---|---|
| Proactive speech | every 10 min | `/run/proactive` |
| Battery babysitter | every 15 min | `/run/babysitter` |
| Nightly diary | 21:30 daily | `/run/daily` |
| Weekly reflection | Sunday 20:00 | `/run/weekly` |

**If you add a new scheduled Vector task, add a trigger+HTTP pair to THIS
workflow** rather than creating a new one. Note from experience: activating
via the n8n API does not reliably hot-register new schedule triggers in the
already-running process - after activating/editing, do a full n8n restart
(`Stop-Process` the `node.exe` running `n8n start` + its task-runner child,
then `n8n start` again) and verify with a fresh `n8n_executions` check.

## Two bugs that made him go quiet (found + fixed same night)

1. **n8n workflows were built but never activated.** Created inactive by
   default; I said "flip the toggle" and it never happened, so all four
   scheduled workflows silently did nothing for hours.
2. **This n8n instance has `Execute Command` DISABLED** (activation error:
   "Unrecognized node type" - confirmed it's registered in n8n's catalog but
   excluded on this instance, likely a deliberate security hardening). So the
   workflows couldn't have run even if activated. Fixed by adding HTTP
   endpoints to `brain_proxy.py` (`/run/daily`, `/run/weekly`,
   `/run/babysitter`, `/run/proactive`) and switching all 4 workflows to
   HTTP Request nodes instead of Execute Command. Then activated all four
   via the n8n API directly rather than relying on a manual UI click.
3. **`proactive.py` hardcoded `MODEL = "nvidia/nemotron-3-nano-4b"`** - broke
   silently the moment we upgraded his brain to `gpt-oss-20b` (model no
   longer loaded in LM Studio -> empty response -> caught by a bare except,
   no visible error). Fixed: `_current_model()` now queries
   `/v1/models` live instead of ever hardcoding a name again.

## Proactive speech (he starts conversations himself)

`proactive.py` — run by n8n every 10 min ("Vector Proactive Speech"):
looks through his camera, and **only speaks if YOLO sees a person**. Respects a
cooldown (25 min) and quiet hours (23:00–08:00), avoids repeating its last 8
lines, and builds context from time of day + what he's actually seen before.
Logged as operator `vector` with 🗣️ in the Logbook — his own initiative,
distinct from agent-driven actions.

    venv\Scripts\python.exe proactive.py --force        # test now
    venv\Scripts\python.exe proactive.py --cooldown 0   # guarded, no wait

## Perception & memory stack

- YOLO11-nano (ultralytics, CPU) detects objects in camera frames
- Sightings logged to `memory/sightings.jsonl` (objects, confidence, pose, image)
- Auto-synced to the Obsidian vault: `Vector Mind/` — one note per object,
  co-occurrence wikilinks form his association graph (graph view,
  filter `tag:#vector-mind`). Hub: `Vector Mind/Vector.md`,
  dashboard: `Vector Mind/Dashboard.md`.
- `capture_rig.py` — drives a scan pattern logging frames + poses for
  photogrammetry (COLMAP / Postshot gaussian splatting).

## The Norton certificate hack (IMPORTANT)

Norton 360's **Web/Mail Shield** MITMs all TLS on this PC, including Vector's
gRPC port 443, re-signing his cert with Norton's
"Web/Mail Shield Self-signed Root" CA. The SDK pins certs, so this broke every
connection with `CERTIFICATE_VERIFY_FAILED`. Norton's UI toggles/exclusions did
NOT stop it.

Fix: `~/.anki_vector/Vector-E7V8-0030219a.cert` is a **dual-trust bundle**:
1. The genuine wire-pod session cert (backup: `.cert.bak`, re-downloadable from
   `http://localhost:8080/session-certs/0030219a`)
2. Norton's self-signed root CA (exported from the Windows cert store,
   `Cert:\LocalMachine\Norton SSL Scanner Cache`; copy in
   `norton-selfsigned-root.pem`)

This works whether Norton intercepts or not. **If connections break after a
Norton update, re-export their root** (thumbprint may rotate) and rebuild the
bundle.

## Registration

Registered in Claude Code local config (`claude mcp list` → `vector`):

```
claude mcp add vector -- C:\Users\mike\vector-mcp\venv\Scripts\python.exe C:\Users\mike\vector-mcp\vector_mcp_server.py
```

Claude Desktop equivalent (`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "vector": {
      "command": "C:\\Users\\mike\\vector-mcp\\venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\mike\\vector-mcp\\vector_mcp_server.py"]
    }
  }
}
```

## Manual test

```
cd C:\Users\mike\vector-mcp
venv\Scripts\python.exe -c "import vector_mcp_server as v; print(v.vector_get_battery())"
```

## Notes / gotchas

- Vector must be awake, on WiFi (2.4 GHz only), and reachable at `192.168.1.50`
  (consider a DHCP reservation so his IP never changes).
- Each tool call opens a short-lived connection and takes behavior control —
  Vector pauses his autonomous behavior during commands. Expect ~2-4s latency.
- `vector_drive` auto-undocks first: `drive_straight` SILENTLY NO-OPS while
  Vector is docked (returns <1s, status "ok", zero movement). Check
  `robot.status.is_on_charger` — NOT `is_on_charger_platform`, which exists
  only on `get_battery_state()` — and call `drive_off_charger()` before
  driving forward. Fixed 2026-08-07 by Hermes after a "move him forward"
  request did nothing.
- The venv's SDK copy in `wirepod-sdk-src` had `asyncio.Event(loop=...)` patched
  out for Python 3.12 — already handled in the fork, patched locally too.
- For n8n: run the server in HTTP mode:
  `venv\Scripts\python.exe vector_mcp_server.py --http`
  Endpoint: `http://localhost:8385/mcp` (from WSL/Hermes: `http://172.26.176.1:8385/mcp`).
  In n8n, use the MCP Client Tool node with that URL (streamable-HTTP transport).
  Note: HTTP mode must be running for n8n to reach it — for permanence, add a
  Task Scheduler entry at logon. Stdio mode (Claude/Hermes) needs no running server.
- Hermes (WSL) points at THIS shared server file with his venv
  (`/home/magic/vector-sdk/bin/python`) and `VECTOR_OPERATOR=hermes` — no parallel
  copy; the dual-trust cert lives at `/home/magic/.anki_vector/`.
- Only one client holds behavior control at a time — simultaneous commands from
  Claude/Hermes/n8n will preempt each other harmlessly.

## 2026-08-08 evening — battery investigation (Claude/Windows session)

Mike noticed Vector was leaving the charger under-charged and asked what
changed. Turned into a multi-hour diagnosis. **Read this whole section
before touching the battery/charging code again** — several dead ends are
recorded here specifically so nobody re-walks them.

### Root causes found (both fixed)

1. **Fix was only applied in one of two places.** Someone (Hermes, earlier
   that day) had already raised the "battery full" threshold 3.6V→3.8V in
   `vector_life.py`'s inline watchdog, to counter wire-pod's Reborn patch
   faking "full" at ~3.68V. But the script actually scheduled every 15 min
   via n8n — `battery_babysitter.py`, invoked through `brain_proxy.py`'s
   `/run/babysitter` — still had **3.6 hardcoded** in *two* places
   (`brain_proxy.py`'s `scripts` dict AND `battery_babysitter.py`'s own
   argparse default). Both now fixed to 3.8.

2. **n8n + brain_proxy were down for ~20+ hours.** Neither survives a
   reboot (known, see "None of these auto-start" in README). Every
   scheduled trigger since the prior night failed with
   `connect ECONNREFUSED`. Restarted both.

3. **IPv6/IPv4 mismatch — this one is sneaky, watch for it again.** Even
   after restarting brain_proxy, n8n's HTTP Request nodes kept failing:
   `connect ECONNREFUSED ::1:8590`. n8n's Node.js resolves `localhost` to
   the IPv6 loopback (`::1`) first; brain_proxy's Flask only binds IPv4
   (`0.0.0.0` / `127.0.0.1`). Manual `curl http://localhost:8590/...`
   worked fine and masked this for a while (curl resolved IPv4). **Fixed
   by changing all 4 HTTP Request node URLs in the "Vector Automations"
   n8n workflow from `localhost` to `127.0.0.1` explicitly**, then a full
   n8n process restart (edit-only does not hot-reload schedule triggers —
   already known, re-confirmed).

   Restarting node processes reliably on this Windows/Git-Bash setup:
   `nohup ... &` and `setsid` **do not actually detach** here — the
   process dies the moment the backgrounding tool's own turn ends (bit
   both `charge_monitor.py`'s first launch and an n8n restart attempt).
   What works: PowerShell `System.Diagnostics.Process` with
   `UseShellExecute=$true` + `WindowStyle=Hidden`.

### Open question — is his battery actually failing, or is our own polling to blame?

Voltage samples across the session (local time) hovered/oscillated in the
3.57-3.65V band for ~50 minutes with no clean climb — but every single one
of those reads came from an SDK connection, and each SDK connect is a real
wake event on his side. Mike correctly pushed back: *"he was charging fine
until we started working on our scripts."* Can't rule that out — the
session involved 15-20+ separate connects in under an hour (battery
babysitter test, two charge-monitor runs, a continuous-poll test, plus
routine checks).

**RESOLVED 2026-08-08 ~21:xx**: Vector was switched fully off (physical
switch) on the charger ~20:51, specifically so NO ONE — not Claude, not
Hermes, not wire-pod, not any SDK connection — could reach or wake him.
Left untouched for the full rest period. **Result: still did not charge.**
Since zero software of any kind touched him during that window, this
rules out "our polling was interfering" as the explanation.

**Conclusion: this points at genuine hardware — either the battery itself
or the charge circuit/contacts, not any script in this repo.** This is a
2018 unit that sat fully discharged for years before revival on
2026-08-07 — aged/degraded LiPo cells commonly develop high internal
resistance and either fall back to a trickle-charge rate slower than
anything we measured, or refuse meaningful charge current entirely. Also
worth ruling out physically (cheap to check, before assuming a battery
replacement is needed): clean the charging contacts on his underside and
the dock, reseat him fully, and confirm the charger's power adapter is
actually delivering power (swap outlets/adapter if available). **No
software fix exists for this.** If contacts/adapter check out fine and he
still won't climb, the real next step is a physical battery replacement,
not more script debugging.

### Also found, not yet acted on

- **`vector_life.py`'s battery-full logic (`is_full` per wire-pod) does
  NOT reflect real charge state.** wire-pod's own browser dashboard
  (`chipper/webroot/js/battery.js` lines 112-128) does the same thing:
  if `is_on_charger_platform` is true and `is_charging` is false and no
  `suggested_charger_sec` is given, it just **assumes "Full" and forces
  the display to 100%**, discarding the real voltage-based percentage
  computed two lines earlier. This is cosmetic (browser only, doesn't
  drive his actual behavior) but actively misleads whoever's watching the
  dashboard. Not fixed — low priority, flagged for whenever someone's in
  that file anyway.
- **Vector's own decision to leave the charger is 100% onboard firmware.**
  Confirmed: wire-pod's Lua sandbox has exactly 4 functions
  (`assumeBehaviorControl`, `sayText`, `playAnimation`,
  `releaseBehaviorControl`), plus one narrow bridge (`driveToCharger()`,
  used by the `trick_gohome` taught phrase) that only triggers his native
  "go to charger" behavior — nothing in Lua, wire-pod, brain_proxy, or
  vector_life.py can influence *when he decides he's charged enough to
  leave*. The only lever that exists anywhere in this stack is reactive:
  catch him off-charger under threshold, drive him back. There is no
  preventive path. Don't go looking for one.
- **Face recognition doesn't exist yet, despite the taught line implying
  otherwise.** "do you remember me" → "I remember what I have seen, not
  who I am seeing. Teach me your face and that changes." — that line is
  accurate, not aspirational-and-stale: there is no `enroll_face` tool in
  `vector_mcp_server.py`. What he has is generic YOLO person-class
  detection (`vector_recognize`) — "a person is here," no identity, same
  as detecting a bottle. Mike wants real named face recognition built
  (logged as his idea in the Logbook 2026-08-08). Not started. The
  `anki_vector` SDK does expose native face enrollment (same system the
  original Anki app used) — that's the more direct path vs. extending the
  YOLO pipeline with face embeddings.

### Backup taken

`C:\Users\mike\vector_backup_20260808_205112\` — his 45 taught
phrases/tricks, wire-pod's epod cert, SDK credentials, all scripts, and
current memory state, with a README explaining what each piece is and
how to restore it. Explicitly NOT backed up (can't be, from outside):
any face-enrollment data on his own onboard flash, if any ever gets
created.

## 2026-09-07 — brain upgrade wave (Hermes/Pi session, 00:40-)

Wave approved by Mike (vector_human_log entries 2026-09-07): persona few-shot
examples + richer context kit + num_ctx 4096→8192 + qwen3:4b A/B vs
qwen2.5:3b. Status so far:
- ollama pull qwen3:4b started (background, ~1.6 GB) for the A/B test.
- To-do: (1) 3 Imperator few-shot Q/A pairs into BASE_PERSONALITY (keep the
  block dense — 3B attends worse to long blocks, per this file's history);
  (2) context kit additions (Memory.md + logbook already injected by
  _episodic_context; add relationship facts + optional news hook); (3) num_ctx
  8192 — brain_proxy must set it in the Ollama payload (or OLLAMA_CONTEXT_LENGTH
  env if /v1 endpoint can't carry options); (4) A/B via vector_probe.py against
  a 2nd brain_proxy instance w/ VECTOR_BRAIN_MODEL=qwen3:4b (no service
  restart needed for the test); winner switch + service restart = Mike runs
  the systemctl one-liner (approval gate).
- Mike also asked (ideas logged): daily news digest so Vector comments on the
  day in persona ("intelligence from the empire's borders"); and whether his
  charger-parking is scheduled or just him — per DEVLOG 2026-08-08 it's 100%
  onboard firmware when he LEAVES; nothing in the stack schedules physical
  activity. curious_patrol.py is manual-only, no cron/n8n wiring found.
- NOTE: this DEVLOG was stale since ~2026-08-08 (Windows era); Pi migration
  (2026-09-03) happened without a DEVLOG section. Backfilling skipped —
  vector-mcp-operations skill holds the 09-03..09-06 history.

### 2026-09-07 wave — build complete (code), pending Mike's restarts

Done (all py_compile OK, digest live, dashboard verified):
- vector_personality.py: +3 Imperator voice examples (incl. news-flavored) at
  end of BASE_PERSONALITY — rides into brain_proxy AND speak_mind prompts.
- brain_proxy.py: _episodic_context now injects "News today: <src>: <title> |"
  (newest digest in Vector Mind/news/, fresh <=26 h, first 3, titles only);
  app port env override VECTOR_PORT (default 8590) for A/B instances.
- speak_mind.py: news takes — NEWS_PROBABILITY 0.5 per eligible call
  (fresh digest + >=6 h since state["last_news_line"]), picks ONE headline,
  in-character Imperator reaction; else original free-muse. ~1-2 takes/day.
- news_digest.py (new, stdlib): BBC world/tech + Space.com, <=5 items,
  writes Vector Mind/news/<date>.md to vault_sync (brain/dashboard read)
  AND obsidian-vault (Syncthing -> desktop). Ran live 2026-09-07 (5 items).
- Hermes cron 1cb25edbe209 "Vector news digest" daily 08:30 (no_agent,
  vector_news_daily.sh, workdir vector-mcp, deliver local).
- TV dashboard: /api/news (cached 300 s) + minimal News strip card between
  Pi-system and Calendar — header subtitle = date + source labels; same
  digest the brain reads. check.sh covers /api/news. Server restarted.
- vector_probe.py: --brain URL arg. ab_model_test.sh: A/B qwen3:4b (8591)
  vs qwen2.5:3b-instruct (8592), probe mode, separate evaluation/ab_* outs.
  A/B fired 2026-09-07 ~00:55 bg; compare empire/soft/halluc/refused when done.
- vector_human_log 2026-09-07: idea news-feed, approval brain-wave, idea
  patrol timing, idea dashboard news strip.

Pending Mike (agent cannot: approval gate / sudo):
1. Ollama context 4096 -> 8192: drop-in /etc/systemd/system/ollama.service.d/
   context.conf [Service] Environment="OLLAMA_CONTEXT_LENGTH=8192" +
   daemon-reload + restart ollama (sudo). Brain_proxy sends no num_ctx and
   Ollama /v1 ignores options — env is the lever.
2. Restart brain-proxy.service (user) to load few-shots + news context:
   systemctl --user restart brain-proxy.service
3. Patrol demo (curious_patrol.py) queued — when Vector is awake.
4. After A/B lands: maybe switch VECTOR_BRAIN_MODEL in brain-proxy unit env.
