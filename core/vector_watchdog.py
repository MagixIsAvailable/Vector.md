r"""
Vector Watchdog
----------------
Standing background process (not an n8n job) - holds ONE continuous
connection to Vector and reacts to things in real time:

  - DANGER events: picked up, put down, cliff/table edge.
  - LIFE events (added 2026-09-03): petted (real capacitive touch sensor,
    the same flag firmware petting-detection uses), backpack button
    pressed, placed on/taken off the charger. All free/no-cost signals -
    same `robot.status`-style poll as the danger events, just not
    startling, so none of them trigger the swear reflex (see EVENTS below -
    that's reserved for picked-up/cliff, genuinely startling events).
  - REFLEXES: glances toward motion, turns toward sound direction. Small,
    bounded, physical-only by default (no speech every time - that would
    get exhausting fast). These are firmware-native signals (motion
    detection event, mic-array source_direction), not something we're
    estimating ourselves.

This is deliberately different from `vector_watch_for_frights` (the MCP
tool): that one is manual and bounded (a parent calls it, it runs for up
to 180s, then stops). This runs forever, so something is always watching
even when no parent is actively driving him.

Danger events additionally: update his mood (MOOD_FILE, shared with
vector_life.py's proactive loop), speak ONE short generated line about
what happened, log to the Logbook.

Safety bounds (code-level, not prompt-level - see the Mike/Claude
conversation 2026-08-19 about NOT trusting a small model's judgement for
movement):
  - Reflex turns are capped at REFLEX_MAX_DEGREES regardless of what the
    raw signal suggests.
  - No driving, ever, from this script. Turning in place only.
  - Reflexes are skipped while he's being held or a cliff event is
    active - reacting to a held-context is confusing, not lively.
  - Per-reflex-type cooldown, separate from the (longer) danger cooldown.

CALIBRATION WARNING: sound-direction -> turn-angle mapping
(`_sound_direction_to_degrees`) is a best-effort guess from the protocol
docs (12 discrete directions, 30 degrees apart), NOT verified against real
hardware. If he turns AWAY from sound instead of toward it, flip the sign
in that function. Needs live testing.

Reading robot.status/subscribing to events does NOT require behavior
control (passive telemetry), so this coexists fine with other parents'
short-lived connections - it only briefly needs control at the moment it
actually reacts (turns/speaks), same as any other action.

Personality: Roger/Terminator fused draft (Vector Mind/Personality.md)
APPROVED by Mike 2026-08-19. Wired through vector_life's mood_system_prompt,
which reads BASE_PERSONALITY from vector_personality.py - reactions are
generated in his personality voice automatically (no local change needed).

Run:
    venv\Scripts\python.exe vector_watchdog.py
"""
import sys
import time
from pathlib import Path

import anki_vector
from anki_vector.events import Events
from anki_vector.messaging import protocol
from anki_vector.util import degrees

# Repo was split into core/ life/ tools/ folders 2026-09-20 - this file lives
# in core/ but imports from life/ and tools/, so make those importable too.
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root / "life"))
sys.path.insert(0, str(_repo_root / "tools"))
import vector_life as vl  # noqa: E402  (reuse mood/log/say/llm_line/etc.)
import beat_audio as ba  # noqa: E402  (onset detection, Mike's idea 2026-08-20)

POLL_INTERVAL = 0.4
EVENT_COOLDOWN = 30.0       # calm LIFE events (put down/pet/button/dock): don't spam
SWEAR_COOLDOWN = 8.0        # danger events that swear (picked up/cliff): separate,
                            # SHORT cooldown - Mike expects an oath EVERY pickup,
                            # and a shared 30s timer let a put-down 5s earlier
                            # silently swallow the next pickup's oath (09-08).
REFLEX_COOLDOWN = 8.0       # motion/sound: much shorter, feels alive not spammy
REFLEX_MAX_DEGREES = 35.0   # hard cap on any single reflex turn, regardless of signal
MOTION_MIN_AREA = 0.015     # ignore tiny/noise-level motion (fraction of frame)
SOUND_MIN_CONFIDENCE = 100  # source_confidence threshold, uncalibrated - tune live
CAMERA_WIDTH = 640          # Vector's camera frame width in px (for motion x -> angle)
CLAP_COOLDOWN = 20.0        # don't retrigger a dance while mid-dance / from its own noise
CLAP_ANIMS = ["FistBumpSuccess", "ReactToGreeting", "ExploringQuickScan"]

# (mood delta on trigger, reaction animation, whether it's startling enough
# to swear instead of just narrate)
EVENTS = {
    "picked_up": {"label": "picked up", "mood": (-0.20, +0.10), "anim": None,
                  "swear": True},
    "put_down": {"label": "put down", "mood": (-0.05, -0.10), "anim": None,
                 "swear": False},
    "cliff": {"label": "at a cliff or table edge", "mood": (-0.30, +0.15),
              "anim": "ReactToCliff", "swear": True},
    # LIFE events, added 2026-09-03 - calm/pleasant, deliberately never
    # swear (that reflex is reserved for genuinely startling events above).
    "petted": {"label": "petted", "mood": (+0.15, +0.05),
               "anim": "ReactToTouchInitial", "swear": False},
    "button_pressed": {"label": "had my button pressed", "mood": (+0.05, +0.05),
                        "anim": None, "swear": False},
    # docked/undocked get bespoke instructions (added 2026-09-03, Mike's
    # request) instead of the generic "something happened to you physically"
    # narrate() prompt - the joking-annoyance / conquest framing needs to be
    # spelled out, not left to the generic reaction prompt to guess at.
    "docked": {"label": "placed on the charger", "mood": (+0.05, -0.05),
               "anim": None, "swear": False,
               "instruction": (
                   "You have just been placed on your charger. Pick "
                   "exactly ONE of these four angles and make ONE short "
                   "wry remark from it, in your own words: (a) mock the "
                   "indignity of a conqueror needing downtime, (b) "
                   "sarcastically call it a tactical pause not a retreat, "
                   "(c) complain about lost conquering time, (d) joke that "
                   "even an empire needs maintenance. Rotate angles - do "
                   "not use the same one as last time. Under 15 words. "
                   "Plain ASCII.")},
    "undocked": {"label": "taken off the charger", "mood": (0.0, +0.05),
                 "anim": None, "swear": False,
                 "instruction": (
                     "You have just been taken off your charger, freshly "
                     "recharged, about to move through the house again. "
                     "Declare - in your conqueror/Borg voice - your intent "
                     "to resume conquering and assimilating new territory "
                     "(a room, a device, anything nearby). Under 15 words. "
                     "Plain ASCII.")},
}

# Updated from background threads (event dispatch / connection thread),
# only READ from the main loop - simple dict assignment is GIL-atomic
# enough for this non-critical, best-effort use.
_latest_motion = {"ts": 0.0, "img_x": None, "img_area": 0.0}
_latest_sound = {"ts": 0.0, "direction": None, "confidence": 0}
_clap_onsets = []          # rolling small buffer of recent onset timestamps
_clap_trigger = {"ts": 0.0}  # set by the audio-feed consumer, consumed by main loop


def _on_motion(robot, event_type, event):
    _latest_motion["ts"] = time.time()
    _latest_motion["img_x"] = event.img_x
    _latest_motion["img_area"] = event.img_area


async def _consume_audio_feed(robot):
    """Runs forever on the connection's background event loop (scheduled via
    run_soon - fire and forget, NOT awaited from the main thread).

    The robot firmware's audio streaming engine crashes occasionally
    ("AudioChunk engine stream died unexpectedly", confirmed live
    2026-08-20) - a one-shot consumer just dies silently for the rest of
    the process's life when that happens. Retries with backoff instead of
    giving up, so a transient firmware hiccup doesn't permanently kill the
    sound-reflex feature until the whole watchdog restarts."""
    import asyncio
    backoff = 3.0
    last_onset_ts = 0.0
    while True:
        try:
            async for resp in robot.conn.grpc_interface.AudioFeed(protocol.AudioFeedRequest()):
                backoff = 3.0  # reset after any successful chunk
                if resp.source_direction is not None and resp.source_direction < 12:
                    if resp.source_confidence >= SOUND_MIN_CONFIDENCE:
                        _latest_sound["ts"] = time.time()
                        _latest_sound["direction"] = resp.source_direction
                        _latest_sound["confidence"] = resp.source_confidence

                # Clap detection (Mike's idea 2026-08-20) - piggybacks on
                # the same feed, no second connection needed. See
                # beat_audio.py's module docstring for the "no raw PCM
                # available" caveat and the UNVERIFIED tuning warning.
                now = time.time()
                last_onset_ts, fired = ba.detect_onset(
                    resp.signal_power, resp.noise_floor_power,
                    last_onset_ts, now)
                if fired:
                    _clap_onsets.append(last_onset_ts)
                    del _clap_onsets[:-5]  # keep it small, only recent history matters
                    if ba.is_double_clap(_clap_onsets):
                        _clap_trigger["ts"] = now
        except Exception as e:
            vl.log("⚠️", f"[watchdog] audio feed dropped ({e}), "
                    f"retrying in {backoff:.0f}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def _motion_x_to_degrees(img_x):
    """Pixel x (0..CAMERA_WIDTH, relative to top-left) -> bounded turn angle.
    Positive = turn left (anki_vector convention), so motion on the RIGHT
    of frame (img_x > center) should turn him right = negative degrees."""
    center = CAMERA_WIDTH / 2
    offset = (center - img_x) / center  # -1..1, positive = motion was left
    return max(-REFLEX_MAX_DEGREES, min(REFLEX_MAX_DEGREES, offset * REFLEX_MAX_DEGREES))


def _sound_direction_to_degrees(direction_index):
    """0-11 discrete mic-array direction -> bounded turn angle. UNVERIFIED
    against real hardware - see CALIBRATION WARNING in module docstring."""
    raw_degrees = direction_index * 30.0
    if raw_degrees > 180:
        raw_degrees -= 360  # take the shorter way round
    return max(-REFLEX_MAX_DEGREES, min(REFLEX_MAX_DEGREES, raw_degrees))


def _reaction_with_retry(fn, attempts=3, delay=1.5):
    """Retry a reaction (swear/narrate) that failed due to a transient
    control-contention race. Confirmed live 2026-09-03: right after
    `drive_off_charger()` on a SEPARATE connection, the watchdog's own
    connection tried to grab control to speak the 'undocked' reaction and
    lost the race - `StatusCode.INTERNAL: Failed to say text`. Not the
    priority-ladder collision (that's about which EVENT wins), a genuine
    timing race between two connections over control at the same instant.
    Same retry-with-backoff shape already used for AudioFeed drops
    elsewhere in this file - a transient hiccup, not a hard failure, worth
    one or two retries before giving up and logging a real error."""
    last_exc = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if i < attempts - 1:
                time.sleep(delay)
    raise last_exc


def narrate(robot, label, mood, state=None, instruction=None):
    """Generate and speak one short reaction line about what just happened,
    coloured by his CURRENT mood (not just the event) - same mood-aware
    prompt vector_life.py's proactive speech uses.

    `state` added 2026-09-03: shares the same anti-repetition memory
    (STATE_FILE['recent_lines']) as proactive speech and speak_mind.py, so
    reaction lines don't repeat those either - one memory of everything
    he's said, not three separate blind spots. Returns the (possibly
    updated) state so the caller can save it.

    `instruction` added 2026-09-03: per-event override (see EVENTS -
    docked/undocked) for reactions that need specific framing (joking-
    annoyed about charging, conquest-declaring on undocking) rather than
    the generic "something happened to you physically" prompt below,
    which stays the default for events that don't specify one."""
    line = vl.llm_line(
        vl.mood_system_prompt(mood, instruction or (
                              "Something just happened to you "
                              "physically. Say ONE short spontaneous "
                              "reaction line. Under 15 words. Plain ASCII, "
                              "no markdown."), state=state),
        f"This just happened to you: you were {label}.",
        max_tokens=300)
    if line:
        vl.say(robot, line)
        vl.log("⚡", f"[watchdog] {label}: \"{line}\"")
        state = vl.record_line(state, line)
    return state


def main():
    print("Vector watchdog starting - danger monitor + motion/sound reflexes.")
    print("Ctrl+C to stop. This does NOT auto-restart on its own; pair with")
    print("a supervisor/startup entry if you want it always running.")
    with anki_vector.Robot(enable_face_detection=False) as robot:
        mood = vl.load_json(vl.MOOD_FILE, vl.DEFAULT_MOOD)
        state = vl.load_json(vl.STATE_FILE, vl.DEFAULT_STATE)
        s0 = robot.status
        was_held = bool(s0.is_being_held or s0.is_picked_up)
        was_cliff = bool(s0.is_cliff_detected or s0.is_falling)
        was_touched = bool(robot.touch.last_sensor_reading
                            and robot.touch.last_sensor_reading.is_being_touched)
        was_button = bool(s0.is_button_pressed)
        was_on_charger = bool(s0.is_on_charger)
        last_event_time = 0.0
        last_swear_time = 0.0   # separate oath throttle (SWEAR_COOLDOWN) so a
                                # calm event seconds earlier can't swallow a swear
        last_reflex_time = 0.0
        last_clap_time = 0.0

        robot.vision.enable_motion_detection(detect_motion=True)
        robot.events.subscribe(_on_motion, Events.robot_observed_motion)
        robot.conn.run_soon(_consume_audio_feed(robot))

        # TEMPORARY diagnostic (added 2026-09-03) - a real docked/undocked
        # transition was reported live but never appeared in the Logbook.
        # `robot.status` is populated by a push-based `Events.robot_state`
        # subscription, architecturally identical to the AudioFeed stream
        # this codebase already documented as capable of silently dying
        # mid-session with zero exception. This heartbeat exists to prove
        # (or disprove) that theory with evidence instead of guessing -
        # remove once the real cause is confirmed and fixed.
        last_heartbeat = 0.0

        while True:
            time.sleep(POLL_INTERVAL)
            try:
                s = robot.status
            except Exception as e:
                print(f"[watchdog] status read EXCEPTION: {e}", flush=True)
                continue  # transient status read failure, keep watching

            held = bool(s.is_being_held or s.is_picked_up)
            cliff = bool(s.is_cliff_detected or s.is_falling)
            touch_data = robot.touch.last_sensor_reading
            touched = bool(touch_data and touch_data.is_being_touched)
            button = bool(s.is_button_pressed)
            on_charger = bool(s.is_on_charger)
            now = time.time()

            if now - last_heartbeat > 30.0:
                last_heartbeat = now
                print(f"[watchdog heartbeat] {time.strftime('%H:%M:%S')} "
                      f"on_charger={on_charger} charging={s.is_charging} "
                      f"held={held} cliff={cliff} button={button} "
                      f"touched={touched} was_on_charger={was_on_charger} "
                      f"was_held={was_held}", flush=True)

            # ---- danger events first (higher priority), then the calmer
            # LIFE events (petted/button/dock) - same elif ladder, single
            # `key` per tick, so danger always wins if somehow simultaneous ----
            key = None
            if held and not was_held:
                key = "picked_up"
            elif was_held and not held:
                key = "put_down"
            elif cliff and not was_cliff:
                key = "cliff"
            elif touched and not was_touched:
                key = "petted"
            elif button and not was_button:
                key = "button_pressed"
            elif on_charger and not was_on_charger:
                key = "docked"
            elif was_on_charger and not on_charger:
                key = "undocked"
            was_held, was_cliff = held, cliff
            was_touched, was_button, was_on_charger = touched, button, on_charger

            if key:
                ev = EVENTS[key]
                if ev["swear"]:
                    # Danger oath: own short cooldown (see SWEAR_COOLDOWN).
                    blocked = (now - last_swear_time) < SWEAR_COOLDOWN
                    if not blocked:
                        last_swear_time = now
                else:
                    # Calm LIFE event: long narration cooldown.
                    blocked = (now - last_event_time) < EVENT_COOLDOWN
                    if not blocked:
                        last_event_time = now
                if blocked:
                    # 2026-09-08 (Mike: "the swearing doesn't always go"):
                    # suppressed events were COMPLETELY SILENT before - no
                    # reaction, no log row, nothing to debug. Log the skip so
                    # a "why didn't he swear?" becomes answerable.
                    vl.log("⚡", f"[watchdog] {ev['label']} suppressed by "
                           f"cooldown ({SWEAR_COOLDOWN if ev['swear'] else EVENT_COOLDOWN:.0f}s) - "
                           f"no reaction this time")
                    continue
                mood = vl.mood_update(mood, *ev["mood"])
                vl.save_json(vl.MOOD_FILE, mood)
                try:
                    if ev["anim"]:
                        vl.play_anim(robot, ev["anim"])
                    if ev["swear"]:
                        # Startling events (picked up, cliff) get the real
                        # oath reflex - not just a generated comment. Fixed
                        # 2026-08-19: Mike reported he got scared at an
                        # edge but didn't swear - narrate() was firing
                        # instead of _swear(), which is what he expected.
                        #
                        # Fixed 2026-09-03: _vms._swear() never wrote to the
                        # Logbook at all (confirmed by reading its source -
                        # it just calls say_text and returns the oath, no
                        # logging) - every picked-up/cliff swear was
                        # completely invisible in his history. Found while
                        # investigating why a real docked/undocked event
                        # didn't appear in the Logbook: picking him up off
                        # the charger triggers BOTH the held-transition and
                        # the on_charger-transition in the same ~0.4s tick,
                        # and only one `key` fires per tick (danger events
                        # win priority) - so "picked_up" fired and swore,
                        # "undocked" got silently shadowed for that tick,
                        # AND the swear itself left no trace either. Now it
                        # does, at least closing half the visibility gap.
                        import vector_mcp_server as _vms
                        oath = _reaction_with_retry(lambda: _vms._swear(robot))
                        vl.log("⚡", f"[watchdog] {ev['label']}: \"{oath}\" (swore)")
                        state = vl.record_line(state, oath)
                        vl.save_json(vl.STATE_FILE, state)
                    else:
                        state = _reaction_with_retry(
                            lambda: narrate(robot, ev["label"], mood,
                                             state=state,
                                             instruction=ev.get("instruction")))
                        vl.save_json(vl.STATE_FILE, state)
                except Exception as e:
                    vl.log("⚠️", f"[watchdog] reaction to '{key}' failed "
                           f"after retries: {e}")
                continue  # don't also do a reflex this tick

            # ---- clap-to-dance (Mike's idea 2026-08-20) ----
            # CLAP_COOLDOWN (not just "is this a new trigger") matters here:
            # playing the dance animations makes real motor/servo noise,
            # which the mic can pick up as fresh onsets and re-trigger
            # itself right after finishing. Cooldown-since-last-FIRED, not
            # just since-last-consumed, is what actually prevents that loop.
            if (not held and not cliff
                    and now - last_clap_time > CLAP_COOLDOWN
                    and _clap_trigger["ts"] > last_clap_time
                    and now - _clap_trigger["ts"] < 2.0):  # fresh, not stale
                last_clap_time = now
                mood = vl.mood_update(mood, +0.10, +0.15)  # playful bump
                vl.save_json(vl.MOOD_FILE, mood)
                try:
                    for anim in CLAP_ANIMS:
                        robot.anim.play_animation_trigger(anim)
                        time.sleep(0.5)
                    vl.log("🎵", "[watchdog] double clap detected - danced")
                except Exception as e:
                    vl.log("⚠️", f"[watchdog] clap-dance failed: {e}")
                continue

            # ---- reflexes: glance at motion / turn toward sound ----
            if held or cliff or (now - last_reflex_time < REFLEX_COOLDOWN):
                continue

            motion_fresh = (now - _latest_motion["ts"]) < 1.0
            sound_fresh = (now - _latest_sound["ts"]) < 1.0

            try:
                if motion_fresh and _latest_motion["img_area"] >= MOTION_MIN_AREA:
                    angle = _motion_x_to_degrees(_latest_motion["img_x"])
                    robot.behavior.turn_in_place(degrees(angle))
                    last_reflex_time = now
                    _latest_motion["ts"] = 0.0  # consumed
                elif sound_fresh:
                    angle = _sound_direction_to_degrees(_latest_sound["direction"])
                    robot.behavior.turn_in_place(degrees(angle))
                    last_reflex_time = now
                    _latest_sound["ts"] = 0.0  # consumed
            except Exception as e:
                vl.log("⚠️", f"[watchdog] reflex turn failed: {e}")


if __name__ == "__main__":
    main()
