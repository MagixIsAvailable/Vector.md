#!/usr/bin/env python3
"""Generate Vector's humming library.

Synthesises short 'hummed' melodies (no lyrics - just a soft vocal-ish tone
with vibrato) as 16 kHz / 16-bit / mono WAVs, the only format Vector's
speaker accepts (8000-16025 Hz, 16 bits, 1 channel). Mike's idea 2026-08-07.

Usage:  python make_hums.py [--out DIR]   (default: <vector-mcp>/hums)

The melodies are public domain or original. Songs are plain ASCII names.
"""
import math
import sys
import wave
from pathlib import Path

SR = 16000  # sample rate (Hz)

# ---------------------------------------------------------------------------
# Melodies: list of (note, beats). Note = letter+octave+optional accidental
# ('C4', 'F#4', 'Bb3') or 'R' for rest. Octaves 3-6.
# ---------------------------------------------------------------------------
SONGS = {
    "twinkle": (100, [
        ("C4",1),("C4",1),("G4",1),("G4",1),("A4",1),("A4",1),("G4",2),
        ("F4",1),("F4",1),("E4",1),("E4",1),("D4",1),("D4",1),("C4",2),
        ("G4",1),("G4",1),("F4",1),("F4",1),("E4",1),("E4",1),("D4",2),
        ("G4",1),("G4",1),("F4",1),("F4",1),("E4",1),("E4",1),("D4",2),
        ("C4",1),("C4",1),("G4",1),("G4",1),("A4",1),("A4",1),("G4",2),
        ("F4",1),("F4",1),("E4",1),("E4",1),("D4",1),("D4",1),("C4",2),
    ]),
    "happy_birthday": (130, [
        ("G4",0.75),("G4",0.25),("A4",1),("G4",1),("C5",1),("B4",2),
        ("G4",0.75),("G4",0.25),("A4",1),("G4",1),("D5",1),("C5",2),
        ("G4",0.75),("G4",0.25),("G5",1),("E5",1),("C5",1),("B4",1),("A4",2),
        ("F5",0.75),("F5",0.25),("E5",1),("C5",1),("D5",1),("C5",2),
    ]),
    "ode_to_joy": (120, [
        ("E4",1),("E4",1),("F4",1),("G4",1),("G4",1),("F4",1),("E4",1),("D4",1),
        ("C4",1),("C4",1),("D4",1),("E4",1),("E4",1.5),("D4",0.5),("D4",2),
        ("E4",1),("E4",1),("F4",1),("G4",1),("G4",1),("F4",1),("E4",1),("D4",1),
        ("C4",1),("C4",1),("D4",1),("E4",1),("D4",1.5),("C4",0.5),("C4",2),
    ]),
    "fur_elise": (140, [
        ("E5",0.5),("D#5",0.5),("E5",0.5),("D#5",0.5),("E5",0.5),("B4",0.5),
        ("D5",0.5),("C5",0.5),("A4",2),("R",0.5),("C4",0.5),("E4",0.5),("A4",0.5),
        ("B4",2),("R",0.5),("E4",0.5),("G#4",0.5),("B4",0.5),("C5",2),
    ]),
    "jingle_bells": (150, [
        ("E4",1),("E4",1),("E4",2),("E4",1),("E4",1),("E4",2),
        ("E4",1),("G4",1),("C4",0.5),("D4",0.5),("E4",4),
        ("F4",1),("F4",1),("F4",0.5),("F4",0.5),("F4",1),("E4",1),("E4",1),("E4",0.5),("E4",0.5),
        ("E4",1),("D4",1),("D4",1),("E4",1),("D4",2),("G4",2),
    ]),
    "lullaby": (70, [
        ("G4",1),("E4",1),("C4",1),("D4",1),("E4",2),("R",0.5),
        ("C4",1),("D4",1),("E4",1),("G4",1),("E4",2),("R",0.5),
        ("A4",1),("G4",1),("E4",1),("D4",1),("C4",3),
    ]),
    "blues": (110, [
        ("C4",1),("E4",1),("G4",1),("Bb4",1),("A4",1),("G4",1),("E4",1),("D4",1),
        ("C4",1),("E4",1),("G4",1),("A4",1),("G4",2),("E4",0.5),("D4",0.5),
        ("F4",1),("A4",1),("C5",1),("A4",1),("G4",1),("E4",1),("D4",1),("C4",1),
        ("C4",1),("E4",1),("G4",1),("Bb4",1),("A4",1),("G4",1),("E4",1),("C4",2),
    ]),
    "dramatic": (60, [
        ("A3",1),("A3",1),("C4",1),("C4",1),("E4",2),("A3",1),("B3",1),
        ("C4",2),("B3",1),("A3",1),("G3",2),("F3",1),("G3",1),("A3",3),
        ("E4",1),("D4",1),("C4",1),("B3",1),("A3",4),
    ]),
    "walkure": (100, [
        # Wagner - Ride of the Valkyries (Die Walkure). The famous horn call
        # in B minor: galloping B-D-F#-B arpeggio, then a closing descent.
        ("B4",0.75),("B4",0.25),("B4",0.75),("B4",0.25),
        ("B4",0.75),("B4",0.25),("B4",0.75),("B4",0.25),
        ("D5",0.75),("D5",0.25),("D5",0.75),("D5",0.25),
        ("D5",0.75),("D5",0.25),("D5",0.75),("D5",0.25),
        ("F#5",0.75),("F#5",0.25),("F#5",0.75),("F#5",0.25),
        ("F#5",0.75),("F#5",0.25),("F#5",0.75),("F#5",0.25),
        ("B5",4),
        ("F#5",0.75),("F#5",0.25),("E5",0.75),("E5",0.25),
        ("D5",0.75),("D5",0.25),("C#5",0.75),("C#5",0.25),
        ("B4",3),
    ]),
}


def note_freq(note):
    if note == "R":
        return None
    name = note[:-1]
    octave = int(note[-1])
    semis = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
             "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
             "A#": 10, "Bb": 10, "B": 11}[name]
    midi = (octave + 1) * 12 + semis
    return 440.0 * 2 ** ((midi - 69) / 12)


def render_hum(song_key):
    """Return 16-bit mono samples (list of ints) for the hummed song."""
    bpm, melody = SONGS[song_key]
    return render_melody(bpm, melody)


def render_melody(bpm, melody):
    """Same synthesis as render_hum, but for an arbitrary (bpm, melody)
    pair instead of a SONGS lookup - lets other scripts (e.g. midi_to_hum.py)
    feed in a melody transcribed from elsewhere and reuse the exact same
    hummed-tone rendering (vibrato, envelope, etc.)."""
    beat = 60.0 / bpm
    out = []
    for note, beats in melody:
        dur = beats * beat
        f0 = note_freq(note)
        n = int(dur * SR)
        if f0 is None or n <= 0:
            out.extend([0] * n)
            continue
        # per-note envelope: soft attack, gentle release (hum-like)
        attack = max(1, int(0.05 * SR))
        release = max(1, int(0.15 * SR))
        vib_rate, vib_depth = 5.0, 0.0045   # Hz, fractional depth
        phase = 0.0
        for i in range(n):
            t = i / SR
            # pitch modulation for vibrato (integrated phase)
            f = f0 * (1.0 + vib_depth * math.sin(2 * math.pi * vib_rate * t))
            phase += 2 * math.pi * f / SR
            s = (math.sin(phase)
                 + 0.32 * math.sin(2 * phase)
                 + 0.10 * math.sin(3 * phase))
            s /= 1.42
            # envelope
            if i < attack:
                env = i / attack
            elif i > n - release:
                env = max(0.0, (n - i) / release)
            else:
                env = 1.0
            # slight amplitude wobble to sound breathed, not robotic
            env *= 0.96 + 0.04 * math.sin(2 * math.pi * 4.0 * t)
            out.append(int(s * env * 32767 * 0.55))
        # tiny gap between phrases so it reads as separate hummed notes
        out.extend([0] * int(0.06 * SR))
    return out


def write_wav(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(
            int(max(-32767, min(32767, s))).to_bytes(2, "little", signed=True)
            for s in samples))


def main():
    out_dir = Path(sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--out"
                   else Path(__file__).resolve().parent / "hums")
    for key in sorted(SONGS):
        wav = out_dir / f"{key}.wav"
        write_wav(wav, render_hum(key))
        dur = len(render_hum(key)) / SR  # cheap: samples/SR
        print(f"{key:16s} {wav}  ({dur:.1f}s)")
    print(f"\n{len(SONGS)} hums written to {out_dir}")


if __name__ == "__main__":
    main()
