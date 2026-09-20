#!/usr/bin/env python3
"""Import a standard MIDI file as a new Vector hum.

Mike's idea 2026-08-20: instead of hand-encoding melodies note-by-note
(what make_hums.py's SONGS dict does), parse a real .mid file with mido
(pure-Python, no C deps, Pi-friendly) and feed the extracted melody into
make_hums.render_melody() - the exact same hummed-tone synthesis already
used for the built-in library. Reuses ~all of the existing rendering code;
only the "listen and understand a melody" half is new here.

Monophonic reduction: MIDI files are usually polyphonic (chords, multiple
tracks). Vector can only hum one note at a time, so we:
  1. pick the track with the most note_on events (usually the lead/melody
     track in a simple file - a heuristic, not guaranteed correct for
     complex arrangements)
  2. within that track, if notes overlap, keep only the highest pitch
     sounding at any instant (a common, simple melody-extraction rule)

Usage:
    venv\\Scripts\\python.exe midi_to_hum.py song.mid --name my_song
    venv\\Scripts\\python.exe midi_to_hum.py song.mid --name my_song --max-notes 120
"""
import argparse
import sys
from pathlib import Path

import mido

from make_hums import render_melody, write_wav, SR  # noqa: E402  (reuse synthesis)

# MIDI note number -> our note-name format ('C4', 'F#4', ...). Reverse of
# make_hums.note_freq's semitone table. MIDI octave numbering follows the
# common convention where MIDI 60 = C4 (middle C) - matches note_freq's
# A4=440Hz/MIDI69 assumption, so no offset correction needed.
_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_note_to_name(n):
    octave = n // 12 - 1
    return f"{_NAMES[n % 12]}{octave}"


def extract_melody(path, max_notes=200):
    """Return (bpm, [(note_name_or_'R', beats), ...]) from a MIDI file."""
    mid = mido.MidiFile(path)

    # Tempo: first set_tempo meta message found anywhere (simple files
    # usually have one global tempo). Default 500000 us/beat = 120 BPM if
    # none present, per the MIDI spec's own default.
    tempo_us = 500000
    for track in mid.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                tempo_us = msg.tempo
                break
        if tempo_us != 500000:
            break
    bpm = round(60_000_000 / tempo_us, 2)

    # Pick the track with the most note_on(velocity>0) events as "melody".
    best_track, best_count = None, -1
    for track in mid.tracks:
        count = sum(1 for m in track if m.type == "note_on" and m.velocity > 0)
        if count > best_count:
            best_track, best_count = track, count
    if best_track is None or best_count == 0:
        raise ValueError("no note_on events found in this MIDI file")

    # Walk the track in absolute ticks, building a list of (start, end,
    # note) intervals, then reduce overlaps to "highest note wins".
    events = []  # (abs_tick, type, note)
    t = 0
    for msg in best_track:
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            events.append((t, "on", msg.note))
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            events.append((t, "off", msg.note))

    ticks_per_beat = mid.ticks_per_beat or 480
    active = {}  # note -> start_tick
    intervals = []  # (start_tick, end_tick, note)
    for tick, kind, note in sorted(events, key=lambda e: e[0]):
        if kind == "on":
            active[note] = tick
        else:
            start = active.pop(note, None)
            if start is not None and tick > start:
                intervals.append((start, tick, note))

    if not intervals:
        raise ValueError("MIDI track had no complete note on/off pairs")

    # Monophonic reduction: at each point in time, keep only the highest
    # note. Simple sweep - build the "melody line" by choosing, at every
    # interval boundary, whichever note is highest among those active.
    intervals.sort(key=lambda iv: iv[0])
    boundaries = sorted({b for iv in intervals for b in (iv[0], iv[1])})
    melody_line = []  # (start, end, note)
    for i in range(len(boundaries) - 1):
        seg_start, seg_end = boundaries[i], boundaries[i + 1]
        covering = [iv[2] for iv in intervals if iv[0] <= seg_start < iv[1]]
        if covering:
            melody_line.append((seg_start, seg_end, max(covering)))

    # Merge consecutive segments that are the same note into one.
    merged = []
    for start, end, note in melody_line:
        if merged and merged[-1][2] == note and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end, note)
        else:
            merged.append((start, end, note))

    if len(merged) > max_notes:
        raise ValueError(
            f"melody has {len(merged)} notes, over the --max-notes cap of "
            f"{max_notes} (Vector's speaker/playback is short-form - pass "
            f"--max-notes higher if you really want the whole thing, or "
            f"trim the MIDI file to a shorter excerpt first)")

    # Convert ticks -> beats (quarter notes), quantize lightly to 1/16th so
    # tiny timing jitter in the source file doesn't produce ugly fractions.
    melody = []
    prev_end_beats = 0.0
    for start, end, note in merged:
        start_beats = start / ticks_per_beat
        end_beats = end / ticks_per_beat
        gap = start_beats - prev_end_beats
        if gap > 1 / 32:  # a real rest, not just rounding noise
            melody.append(("R", round(gap * 16) / 16))
        dur = round((end_beats - start_beats) * 16) / 16
        melody.append((midi_note_to_name(note), max(dur, 1 / 16)))
        prev_end_beats = end_beats

    return bpm, melody


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("midi_path", help="path to a .mid file")
    ap.add_argument("--name", required=True,
                     help="hum name to save as (written to hums/<name>.wav)")
    ap.add_argument("--max-notes", type=int, default=200)
    ap.add_argument("--out", default=None,
                     help="output dir (default: <vector-mcp>/hums)")
    args = ap.parse_args()

    bpm, melody = extract_melody(args.midi_path, max_notes=args.max_notes)
    samples = render_melody(bpm, melody)
    out_dir = Path(args.out) if args.out else Path(__file__).resolve().parent / "hums"
    wav_path = out_dir / f"{args.name}.wav"
    write_wav(wav_path, samples)
    dur = len(samples) / SR
    print(f"{args.name}: {len(melody)} notes/rests, {bpm} BPM, "
          f"{dur:.1f}s -> {wav_path}")


if __name__ == "__main__":
    main()
