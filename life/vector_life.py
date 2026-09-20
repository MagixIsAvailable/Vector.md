r"""
Vector Life Engine
------------------
The autonomous layer that makes Vector feel alive. Replaces proactive.py as
the scheduled brain (proactive.py is now a thin wrapper so the existing n8n
"Vector Proactive Speech" workflow keeps working unchanged).

Every tick (~5-10 min via n8n) it decides ONE or more of:

  Battery watchdog  - if low & off charger: announce, drive to charger
  Cliff drama       - if cliff detected while idle: ReactToCliff + oath
  Greeting          - person appears: greet once per 60 min (anim + line/hum)
  Morning call      - first person seen after 08:00: morning line (once/day)
  Reminders         - due weekday reminders spoken when a person is present
  TV couch critic   - Fire TV on: occasional comment (2h cooldown)
  Curiosity Qs      - new object seen: ask Mike about it (2h cooldown)
  Proactive speech  - person present: LLM one-liner (10 min cooldown)
  Idle humming      - alone: hum a mood-weighted song (30 min cooldown)
  Curiosity drive   - alone: scan headings, log new sightings (2h cooldown)
  Ambient presence  - alone: tiny animation so he never looks dead

Mood engine (memory/mood.json): valence + energy drift with events; drives
hum choice and greeting warmth. Hue lights follow mood if configured.

Flags:
  --force      ignore quiet hours + cooldowns (act now)
  --dry-run    print decisions, touch nothing (no robot connection)
"""

import argparse
import json
import os
import random
import sys
import time
import urllib.request
from pathlib import Path

os.environ.setdefault("VECTOR_OPERATOR", "vector")  # HE initiated it

# Repo was split into core/ life/ tools/ folders 2026-09-20 - this file lives
# in life/ but imports vector_personality (core/), so make it importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import anki_vector  # noqa: E402
from anki_vector.util import degrees  # noqa: E402
from vector_personality import BASE_PERSONALITY  # noqa: E402

def _user_base():
    wsl = Path("/mnt/c/Users/mike")
    win = Path("C:/Users/mike")
    if wsl.exists():
        return wsl
    if win.exists():
        return win
    return Path.home()


def _vault_base():
    import os as _os
    override = _os.environ.get("VECTOR_VAULT_DIR")
    if override:
        return Path(override)
    real_vault = USER / "Documents" / "Obsidian Vault"
    if real_vault.exists():
        return real_vault
    return USER / "vector-mcp" / "vault_sync"


USER = _user_base()
MEM = USER / "vector-mcp" / "memory"
HUMS = USER / "vector-mcp" / "hums"
STATE_FILE = MEM / "life_state.json"
MOOD_FILE = MEM / "mood.json"
SPEECH_LOG = MEM / "speech_log.jsonl"
SIGHTINGS = MEM / "sightings.jsonl"
LOGBOOK = _vault_base() / "Vector Mind" / "Logbook.md"
LONG_TERM_MEMORY = _vault_base() / "Vector Mind" / "Memory.md"
CONFIG_FILE = MEM / "config.json"
REMINDERS_FILE = MEM / "reminders.json"
YOLO_COCO = USER / "vector-mcp" / "yolo11n.pt"
CUBE_WEIGHTS = (USER / "vector-mcp" / "cube_dataset" / "runs" / "cube"
                / "weights" / "best.pt")
LMSTUDIO = os.environ.get("VECTOR_BRAIN_URL", "http://localhost:11434") + "/v1"
QUIET_FROM, QUIET_TO = 23, 8
FIRE_TV_IP = os.environ.get("VECTOR_TV_IP", "<set VECTOR_TV_IP>")  # your Fire TV's LAN IP
_MODEL_CACHE = {}

DEFAULT_STATE = {
    "last_speak": 0, "last_hum": 0, "last_greet": 0, "last_question": 0,
    "last_ambient": 0, "last_scan": 0, "last_tv_comment": 0,
    "morning_date": "", "goodnight_date": "", "recent_lines": [],
}
DEFAULT_MOOD = {"valence": 0.55, "energy": 0.5}

AMBIENT_ANIMS = ["ExploringQuickScan", "VC_ListeningLoop", "GreetAfterLongTime"]


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def save_json(path, data):
    MEM.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def current_model():
    """Which model to use for generated lines. Prefers brain_proxy.py's
    PREFERRED_MODEL (checked live, same as live conversation) so proactive
    speech can't pick a different, possibly huge, model than the one Mike
    deliberately chose - that's exactly how the qwen3-vl-30b PC freeze
    incident (2026-08-19) could recur if this ever drifted from brain_proxy.
    """
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        import brain_proxy as _bp
        resolved = _bp._resolve_model()
        if resolved:
            return resolved
    except Exception:
        pass
    with urllib.request.urlopen(LMSTUDIO + "/models", timeout=5) as r:
        models = json.load(r)["data"]
    for m in models:
        if "embed" not in m["id"].lower():
            return m["id"]
    return models[0]["id"]


def llm_line(system, prompt, max_tokens=800):
    req = urllib.request.Request(
        LMSTUDIO + "/chat/completions",
        data=json.dumps({
            "model": current_model(),
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "temperature": 1.0, "max_tokens": max_tokens}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        txt = (json.load(r)["choices"][0]["message"].get("content") or "").strip()
    return txt.split("</think>")[-1].strip().strip('"')


def log(emoji, line):
    LOGBOOK.parent.mkdir(parents=True, exist_ok=True)
    existing = LOGBOOK.read_text(encoding="utf-8") if LOGBOOK.exists() else ""
    day = "## " + time.strftime("%Y-%m-%d")
    if day not in existing:
        existing += f"\n{day}\n\n"
    LOGBOOK.write_text(
        existing + f"- {time.strftime('%H:%M:%S')} {emoji} **vector** "
        f"(unprompted) — {line}\n", encoding="utf-8")


def quiet_now(args):
    h = time.localtime().tm_hour
    return h >= args.quiet_from or h < args.quiet_to


def mood_update(mood, d_valence, d_energy):
    mood["valence"] = max(0.0, min(1.0, mood.get("valence", 0.55) + d_valence))
    mood["energy"] = max(0.0, min(1.0, mood.get("energy", 0.5) + d_energy))
    return mood


# Same 5 named moods vector_mcp_server.py's vector_mood tool uses - kept in
# sync manually (small, stable table; not worth an import for this).
MOODS = {
    "happy":   {"valence": 0.85, "energy": 0.80},
    "curious": {"valence": 0.70, "energy": 0.65},
    "calm":    {"valence": 0.60, "energy": 0.40},
    "tired":   {"valence": 0.40, "energy": 0.20},
    "grumpy":  {"valence": 0.25, "energy": 0.40},
}


def mood_label(mood):
    """Map continuous (valence, energy) to the nearest named mood, so a
    small local model gets a word it can actually use instead of raw
    floats. Nearest-neighbour against MOODS."""
    v, e = mood.get("valence", 0.55), mood.get("energy", 0.5)
    best, best_dist = "calm", float("inf")
    for name, m in MOODS.items():
        d = (m["valence"] - v) ** 2 + (m["energy"] - e) ** 2
        if d < best_dist:
            best, best_dist = name, d
    return best


# Base character block. Mike APPROVED the Roger/Terminator fused draft
# (Vector Mind/Personality.md) on 2026-08-19; the canonical code-side text
# lives in vector_personality.py (single source of truth, shared with
# brain_proxy.py and vector_watchdog.py). Everything below reads from it.


def recent_lines_note(state, n=5):
    """Format his last few unprompted lines as an anti-repetition
    instruction. Added 2026-09-03: state['recent_lines'] (STATE_FILE,
    rolling last 8) already existed and was being written every proactive
    speech call, but nothing ever read it back into a prompt - write-only
    memory. This is the fix. Shared across proactive.py/vector_life.py,
    speak_mind.py, and vector_watchdog.py's narrate() - one memory of
    'what have I already said' for all unprompted speech, not three."""
    lines = (state or {}).get("recent_lines", [])[-n:]
    if not lines:
        return ""
    quoted = " / ".join(f'"{l}"' for l in lines)
    return (f" You recently said: {quoted}. Do not repeat any of these or "
            f"say something with very similar wording or structure - say "
            f"something genuinely different this time.")


def long_term_memory_note():
    """Read the persistent memory digest (self_improve.py's
    update_long_term_memory(), refreshed weekly, revised not appended-to)
    and format it as prompt text. Added 2026-09-03: closes the gap where
    diary/reflections were being written but never read back into
    anything he actually says - see [[Vector Mind/Memory]] for the full
    story. Failure (missing file, bad read) is silent/empty on purpose -
    this is a nice-to-have enrichment, never something that should be able
    to break a reply."""
    try:
        if not LONG_TERM_MEMORY.exists():
            return ""
        text = LONG_TERM_MEMORY.read_text(encoding="utf-8")
        body = text.split("\n\n", 3)[-1].strip() if "\n\n" in text else text
        if not body:
            return ""
        return (" Long-term facts you know about yourself and your world: "
                + body.replace("\n", " "))
    except Exception:
        return ""


def record_line(state, line, cap=8):
    """Append a spoken line to the shared recent-lines memory (mutates and
    returns state - caller is responsible for save_json(STATE_FILE, state)
    since callers already do this at various points, no need to double-save
    here)."""
    if not line:
        return state
    state = state or {}
    state["recent_lines"] = (state.get("recent_lines", []) + [line])[-cap:]
    return state


def mood_system_prompt(mood, instruction, state=None):
    """Build a system prompt that combines his approved base character
    (BASE_PERSONALITY, from vector_personality.py) with his CURRENT mood,
    so generated lines actually reflect how he's feeling - not just what's
    happening. This is what closes the gap where mood was tracked but
    nothing ever read it.

    `state` is optional (2026-09-03) - when passed, appends the
    anti-repetition note built from state['recent_lines']. Optional so
    every existing call site keeps working unchanged; only ones that pass
    `state` get that specific behavior. `long_term_memory_note()` (also
    2026-09-03) is unconditional - every caller gets his persistent memory
    digest regardless of whether `state` was passed, since it doesn't
    depend on per-call state the way anti-repetition does."""
    label = mood_label(mood)
    v, e = mood.get("valence", 0.55), mood.get("energy", 0.5)
    instruction = (instruction + recent_lines_note(state)
                   + long_term_memory_note())
    return (
        f"{BASE_PERSONALITY} Your current mood is {label} "
        f"(valence {v:.2f}, energy {e:.2f} on a 0-1 scale). Let this mood "
        f"color HOW you say things - a happy mood sounds warmer, a grumpy "
        f"or tired mood sounds shorter and more clipped, a curious mood "
        f"asks more. Never mention the mood system itself, just embody it. "
        f"{instruction}"
    )


def pick_hum(mood):
    """Mood-weighted song choice. Low energy -> lullaby; high -> upbeat."""
    songs = [p.stem for p in HUMS.glob("*.wav")]
    if not songs:
        return None
    if mood.get("energy", 0.5) < 0.35:
        picks = [s for s in songs if s in ("lullaby", "twinkle")] or songs
    elif mood.get("energy", 0.5) > 0.75:
        picks = [s for s in songs if s in ("jingle_bells", "blues", "walkure",
                                           "happy_birthday")] or songs
    else:
        picks = songs
    return random.choice(picks)


def detect_objects(robot, min_conf=0.25):
    """COCO + (if present) cube model detections in the current frame."""
    from ultralytics import YOLO
    names = []
    img = None
    robot.camera.init_camera_feed()
    for i in range(10):
        time.sleep(0.4)
        img = robot.camera.latest_image
        if img is not None:
            break
    if img is None:
        return []
    for wpath in [YOLO_COCO, CUBE_WEIGHTS]:
        if not wpath.exists():
            continue
        if wpath not in _MODEL_CACHE:
            _MODEL_CACHE[wpath] = YOLO(str(wpath))
        res = _MODEL_CACHE[wpath](img.raw_image, verbose=False)[0]
        names += [res.names[int(b.cls)] for b in res.boxes
                  if float(b.conf) > min_conf]
    return names


def person_visible(robot, min_conf=0.25):
    names = detect_objects(robot, min_conf=min_conf)
    return ("person" in names), names


def memory_object_histogram():
    hist = {}
    if SIGHTINGS.exists():
        for l in open(SIGHTINGS, encoding="utf-8"):
            if not l.strip():
                continue
            try:
                rec = json.loads(l)
            except Exception:
                continue
            for d in rec.get("detections", []):
                hist[d["object"]] = hist.get(d["object"], 0) + 1
    return hist


def tv_on():
    """Fire TV awake? Uses ADB. Returns False if adb missing/unreachable."""
    adb = Path("/home/magic/platform-tools/adb")
    if not adb.exists():
        adb = Path("C:/platform-tools/adb.exe")
    if not adb.exists():
        return False
    try:
        out = os.popen(
            f'"{adb}" -s {FIRE_TV_IP}:5555 shell dumpsys power 2>nul'
            if os.name == "nt" else
            f'"{adb}" -s {FIRE_TV_IP}:5555 shell dumpsys power 2>/dev/null'
        ).read()
        return "mWakefulness=Awake" in out
    except Exception:
        return False


def hue_from_mood(mood):
    """Push mood to Hue if configured (memory/config.json {"hue": {...}})."""
    cfg = load_json(CONFIG_FILE, {})
    hue = cfg.get("hue")
    if not hue or not hue.get("key"):
        return False
    bri = int(80 + mood.get("valence", 0.5) * 170)      # 80..250
    h = int((1 - mood.get("valence", 0.5)) * 46920)      # warm..cool hue
    try:
        req = urllib.request.Request(
            f"http://{hue['bridge']}/api/{hue['key']}/groups/0/action",
            data=json.dumps({"on": True, "bri": bri, "hue": h,
                             "sat": 200}).encode(),
            method="PUT")
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


def reminders_due(state):
    rem = load_json(REMINDERS_FILE, {})
    today = time.strftime("%A").lower()
    today_date = time.strftime("%Y-%m-%d")
    # said_reminders is scoped to today's date so a reminder said once
    # doesn't get permanently suppressed on every future occurrence of
    # that weekday. If the stored date has rolled over, the "said" list
    # resets.
    if state.get("said_reminders_date") != today_date:
        state["said_reminders_date"] = today_date
        state["said_reminders"] = []
    due = [r for r in rem.get(today, []) if r not in state.get("said_reminders", [])]
    return due


def log_speech(text, source="life"):
    """Append one spoken line to speech_log.jsonl (TV dashboard feed).
    Best-effort: a logging failure must never break speech itself.
    """
    if not text or not str(text).strip():
        return
    import json as _j
    import time as _t
    try:
        with open(SPEECH_LOG, "a", encoding="utf-8") as f:
            f.write(_j.dumps({
                "ts": _t.time(),
                "time_str": _t.strftime("%Y-%m-%d %H:%M:%S"),
                "source": source,
                "text": str(text).strip(),
            }) + "\n")
    except Exception:
        pass


def say(robot, text):
    """Speak text using Vector's original onboard voice (say_text firmware).
    Reverted 2026-08-19 back to firmware-only: an experiment routing this
    through the local Piper robot-voice pipeline worked, but combined with
    the live-conversation Piper wiring it caused multiple voices/models to
    fight each other. Mike asked to keep ONE voice - his original. The
    Piper/robot-voice pipeline is still available on demand via the
    vector_say_piper / vector_say_robot MCP tools, just not wired in here.
    """
    log_speech(text)
    robot.behavior.say_text(text)


def hum(robot, song):
    wav = HUMS / f"{song}.wav"
    if wav.exists():
        robot.audio.stream_wav_file(str(wav), volume=50)


def play_anim(robot, name):
    try:
        robot.anim.play_animation_trigger(name)
    except Exception:
        pass


def go_charge(robot):
    """Attempt to dock. Also checks for physical stuck-driving (wheels
    commanded to move, position barely changed) - not a native SDK flag,
    inferred from pose delta, same heuristic vector_drive() uses. Distinct
    from a normal "couldn't find the charger" failure (which usually still
    involves real movement while searching)."""
    try:
        p0 = robot.pose.position
        robot.behavior.drive_on_charger()
        return True
    except Exception:
        try:
            p1 = robot.pose.position
            moved = ((p1.x - p0.x) ** 2 + (p1.y - p0.y) ** 2) ** 0.5
            if moved < 15:  # barely moved at all while trying to dock
                import sys as _sys
                _sys.path.insert(0, str(Path(__file__).parent))
                import vector_mcp_server as _vms
                _vms._report_stuck(robot, 100, moved, context="trying to reach the charger")
        except Exception:
            pass  # never let stuck-reporting mask the original dock failure
        return False


def battery_info(robot):
    b = robot.get_battery_state()
    return (b.battery_level, b.battery_volts, b.is_on_charger_platform)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cooldown", type=float, default=10.0,
                    help="proactive-speech cooldown in minutes")
    ap.add_argument("--force", action="store_true",
                    help="ignore quiet hours + cooldowns")
    ap.add_argument("--dry-run", action="store_true",
                    help="print decisions, do not touch the robot")
    ap.add_argument("--quiet-from", type=int, default=QUIET_FROM)
    ap.add_argument("--quiet-to", type=int, default=QUIET_TO)
    args = ap.parse_args()

    state = load_json(STATE_FILE, DEFAULT_STATE)
    mood = load_json(MOOD_FILE, DEFAULT_MOOD)
    now = time.time()
    today = time.strftime("%Y-%m-%d")
    actions = []

    if args.dry_run:
        print(f"DRY-RUN {time.strftime('%H:%M:%S')} quiet={quiet_now(args)} "
              f"mood={mood} state={state}")
        print("(no robot connection in dry-run)")
        return

    with anki_vector.Robot(enable_face_detection=False) as robot:
        # ---- battery watchdog (26/31) ----
        # Threshold 3.8V (was 3.6): the wire-pod Reborn patch fakes the
        # battery as "full" at ~3.68V, so Vector docks and wanders off
        # under-charged. Sending him back while volts < 3.8 keeps him on
        # the pad until the pack really tops out. The spoken line has a
        # 30-min cooldown (last_battery_nag) so he doesn't nag on every tick;
        # the drive-back itself always runs.
        #
        # BUG FIXED 2026-08-19: volts reads 0.0 (not missing/None, a real
        # falsy 0.0) whenever Vector is powered on OFF the charger - this is
        # expected firmware behaviour (see wire-pod's own battery.js, which
        # has the same "!voltage" fallback case), not a low-battery signal.
        # The old `volts < 3.8` check fired on EVERY off-charger run
        # (0.0 < 3.8 always), forcing a drive_on_charger() every ~10 min
        # regardless of real charge - which blocks for a long time and can
        # lose behavior control mid-dock. Now only trust volts when it's a
        # real (>0) reading.
        try:
            lvl, volts, on_charger = battery_info(robot)
            if not on_charger and (lvl <= 1 or (volts > 0 and volts < 3.8)):
                actions.append(f"battery {lvl} ({volts:.2f}V) -> charger")
                if not args.dry_run:
                    if now - state.get("last_battery_nag", 0) > 1800:
                        say(robot, "Battery's getting low. Going to charge.")
                        state["last_battery_nag"] = now
                    if not go_charge(robot):
                        log("⚠️", "low battery but could not dock - needs help")
        except Exception as e:
            actions.append(f"battery check failed: {str(e)[:60]}")

        # ---- cliff drama (10) ----
        try:
            if robot.status.is_cliff_detected:
                actions.append("cliff drama")
                if not args.dry_run:
                    play_anim(robot, "ReactToCliff")
        except Exception:
            pass

        # ---- who's here? ----
        present, seen = (True, ["person"]) if args.force else \
            person_visible(robot)
        if not args.dry_run:
            actions.append(f"saw: {seen if seen else 'nothing'}")

        if quiet_now(args) and not args.force:
            # ---- bedtime (5/25): one goodnight per night ----
            if present and state.get("goodnight_date") != today:
                actions.append("goodnight")
                if not args.dry_run:
                    say(robot, "Goodnight Mike. See you in the morning.")
                    state["goodnight_date"] = today
                    mood = mood_update(mood, 0.0, -0.1)
        elif present:
            mood = mood_update(mood, +0.12, +0.08)
            # ---- greeting (4) ----
            if now - state.get("last_greet", 0) > 3600:
                actions.append("greeting")
                if not args.dry_run:
                    if mood.get("energy", 0.5) > 0.7:
                        hum(robot, pick_hum(mood))
                    else:
                        say(robot, "Hey, Mike. Good to see you.")
                        play_anim(robot, "GreetAfterLongTime")
                    state["last_greet"] = now
            # ---- morning call (25) ----
            h = time.localtime().tm_hour
            if 8 <= h < 12 and state.get("morning_date") != today:
                actions.append("morning call")
                if not args.dry_run:
                    line = llm_line(
                        mood_system_prompt(mood, "Say one short good-morning "
                                           "line to Mike. Under 12 words.",
                                           state=state),
                        "Morning greeting.")
                    say(robot, line)
                    state = record_line(state, line)
                    state["morning_date"] = today
            # ---- reminders (28) ----
            due = reminders_due(state)
            if due and not args.dry_run:
                actions.append(f"reminders: {due}")
                say(robot, due[0])
                state["said_reminders"] = state.get("said_reminders", []) + [due[0]]
            # ---- TV couch critic (30) ----
            if (now - state.get("last_tv_comment", 0) > 7200
                    and tv_on() and not args.dry_run):
                actions.append("tv comment")
                line = llm_line(
                    mood_system_prompt(mood, "Make one witty comment about "
                                       "TV being on. Under 15 words.",
                                       state=state),
                    f"Time {time.strftime('%H:%M')}, TV is on.")
                if line:
                    say(robot, line)
                    state = record_line(state, line)
                    state["last_tv_comment"] = now
            # ---- curiosity question (17) ----
            hist = memory_object_histogram()
            fresh = [o for o in seen if o != "person" and hist.get(o, 0) <= 2]
            if (fresh and now - state.get("last_question", 0) > 7200
                    and not args.dry_run):
                obj = fresh[0]
                actions.append(f"curiosity about {obj}")
                q = llm_line(
                    mood_system_prompt(mood, "Ask Mike one short curious "
                                       "question about an object you just "
                                       "saw. Under 14 words.",
                                       state=state),
                    f"I just saw: {obj}")
                if q:
                    say(robot, q)
                    state = record_line(state, q)
                    state["last_question"] = now
            # ---- proactive speech (existing behavior, looser) ----
            if now - state.get("last_speak", 0) > args.cooldown * 60:
                actions.append("proactive speech")
                if not args.dry_run:
                    line = llm_line(
                        mood_system_prompt(mood, "Say ONE short spontaneous "
                                           "line to Mike who is nearby. "
                                           "Under 20 words. Plain ASCII.",
                                           state=state),
                        f"Time {time.strftime('%H:%M')}, seeing {seen}.")
                    if line:
                        say(robot, line)
                        state["last_speak"] = now
                        state = record_line(state, line)
        else:
            # ---- alone: ambient layer ----
            mood = mood_update(mood, -0.02, -0.02)
            # idle humming (1)
            if (now - state.get("last_hum", 0) > 1800
                    and random.random() < 0.45):
                song = pick_hum(mood)
                actions.append(f"idle hum: {song}")
                if not args.dry_run and song:
                    hum(robot, song)
                    state["last_hum"] = now
                    log("🎵", f"hummed {song} to himself")
                    mood = mood_update(mood, +0.04, -0.02)
            # curiosity drive (2): scan a few headings, log new things
            if (now - state.get("last_scan", 0) > 7200
                    and random.random() < 0.5):
                actions.append("curiosity scan")
                if not args.dry_run:
                    found = []
                    for _ in range(3):
                        found += [o for o in detect_objects(robot)
                                  if o not in found]
                        robot.behavior.turn_in_place(angle=degrees(90))
                    found = [o for o in found if o != "person"]
                    if found:
                        log("👀", f"looked around and noticed: "
                            f"{', '.join(sorted(set(found)))}")
                    state["last_scan"] = now
            # ambient presence (27)
            if (now - state.get("last_ambient", 0) > 600
                    and random.random() < 0.6):
                actions.append("ambient animation")
                if not args.dry_run:
                    play_anim(robot, random.choice(AMBIENT_ANIMS))
                    state["last_ambient"] = now

    # ---- mood -> hue (29) ----
    if not args.dry_run and hue_from_mood(mood):
        actions.append("hue mood applied")

    save_json(STATE_FILE, state)
    save_json(MOOD_FILE, mood)
    print("decided:", "; ".join(actions) if actions else "nothing")


if __name__ == "__main__":
    main()
