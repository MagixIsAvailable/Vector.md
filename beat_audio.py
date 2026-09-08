"""Shared onset/tempo detection from Vector's AudioFeed.

Mike's idea 2026-08-20 (clap-to-trigger + beat-synced dancing). Important
constraint discovered while building this: the SDK's AudioFeed does NOT
expose raw PCM samples - only pre-processed fields (confirmed live via the
protobuf descriptor):

    robot_time_stamp, group_id, signal_power, direction_strengths,
    source_direction, source_confidence, noise_floor_power

So real pitch-tracking from Vector's own mic isn't possible with this SDK
(that's still only realistic by capturing audio elsewhere, e.g. a PC mic,
if that gets built later). What IS available - signal_power vs
noise_floor_power - is exactly what a simple percussive-onset detector
needs: a clap or a drum/bass beat is a sudden spike in signal_power above
the ambient noise floor. This module turns that stream into onset
timestamps and, from those, a rough BPM estimate.

**UNVERIFIED against real hardware yet** (same caveat vector_watchdog.py
already carries for source_direction) - signal_power's actual scale/units
aren't documented anywhere Mike or previous agents found; ONSET_RATIO
below is a guess and will likely need live tuning. Test with
vector_dance_to_beat and adjust ONSET_RATIO if it triggers on everything
or nothing.
"""
import time

# signal_power must exceed noise_floor_power by this multiplicative ratio
# to count as an onset. >1.0 required; start conservative, loosen if real
# claps/beats aren't triggering.
ONSET_RATIO = 1.8

# minimum seconds between two onsets for them to count as separate events
# (avoids one loud transient being counted many times across chunks).
REFRACTORY_S = 0.12

# BPM must land in this range after octave-correction (typical music tempo
# range) - keeps a stray double/half-tempo estimate from being absurd.
BPM_MIN, BPM_MAX = 60.0, 180.0


def detect_onset(signal_power, noise_floor_power, last_onset_ts, now=None):
    """Stateless-ish onset check for ONE AudioFeed sample. Caller keeps
    last_onset_ts across calls (refractory period). Returns the new
    last_onset_ts (unchanged if this sample wasn't an onset) and whether
    this sample fired.

    noise_floor_power can be 0 early in a session before the firmware has
    established a floor - guard against divide-by-zero by treating that as
    "no floor yet, don't fire" rather than crashing or false-triggering on
    absolute silence.
    """
    now = now if now is not None else time.time()
    if noise_floor_power <= 0:
        return last_onset_ts, False
    if signal_power < noise_floor_power * ONSET_RATIO:
        return last_onset_ts, False
    if now - last_onset_ts < REFRACTORY_S:
        return last_onset_ts, False
    return now, True


def is_double_clap(onset_times, window=(0.15, 1.0)):
    """True if the two most recent onsets in onset_times are spaced within
    `window` seconds of each other - a deliberate clap-clap, not just one
    loud sound or a continuous noisy stretch (music, talking)."""
    if len(onset_times) < 2:
        return False
    gap = onset_times[-1] - onset_times[-2]
    return window[0] <= gap <= window[1]


def estimate_bpm(onset_times):
    """Rough tempo estimate from a list of onset timestamps (seconds).
    Uses the median inter-onset interval (robust to a few missed/extra
    onsets) and octave-corrects it into BPM_MIN..BPM_MAX by doubling or
    halving, since a raw IOI can just as easily reflect a half or double
    tempo. Returns None if there's not enough data to say anything."""
    if len(onset_times) < 4:
        return None
    iois = sorted(t2 - t1 for t1, t2 in zip(onset_times, onset_times[1:])
                  if t2 > t1)
    if not iois:
        return None
    mid = len(iois) // 2
    median_ioi = (iois[mid] if len(iois) % 2 else
                  (iois[mid - 1] + iois[mid]) / 2)
    if median_ioi <= 0:
        return None
    bpm = 60.0 / median_ioi
    while bpm < BPM_MIN:
        bpm *= 2
    while bpm > BPM_MAX:
        bpm /= 2
    return round(bpm, 1)


async def sample_audio_feed(robot, seconds, on_sample=None):
    """Consume AudioFeed for `seconds` and return the list of onset
    timestamps observed (relative to time.time(), like the rest of the
    codebase uses for _latest_sound/_latest_motion).

    on_sample, if given, is called (signal_power, noise_floor_power, ts)
    for every raw sample - lets a caller (e.g. the watchdog) fold this into
    its own longer-lived onset history instead of just this one window.

    Uses the SAME retry-on-firmware-crash pattern as
    vector_watchdog._consume_audio_feed (AudioFeed is confirmed to die
    mid-stream sometimes) - a crash here just means we stop collecting
    onsets for the rest of this call, not that the whole tool call fails.
    """
    import asyncio
    from anki_vector.messaging import protocol

    onsets = []
    last_onset_ts = 0.0
    deadline = time.time() + seconds
    try:
        async for resp in robot.conn.grpc_interface.AudioFeed(
                protocol.AudioFeedRequest()):
            now = time.time()
            if on_sample:
                on_sample(resp.signal_power, resp.noise_floor_power, now)
            last_onset_ts, fired = detect_onset(
                resp.signal_power, resp.noise_floor_power, last_onset_ts, now)
            if fired:
                onsets.append(last_onset_ts)
            if now >= deadline:
                break
    except Exception:
        pass  # best-effort - return whatever we collected before it died
    return onsets
