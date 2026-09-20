#!/usr/bin/env python3
"""Minimal bridge script: speaks the given text through Vector's own TTS,
using the proven-working Python anki_vector SDK connection (same cert/guid
used successfully by vector_mcp_server.py all day). Invoked by wire-pod's
patched KGSim() as a workaround for the Go SDK's broken auth against this
Escape-Pod-flashed robot (401 Unauthorized on every robot.Conn.* call,
root cause not fully identified despite extensive investigation - GUID,
client cert, ALPN, and connection-exhaustion theories all ruled out).

Usage: speak_text.py "text to say"
"""
import sys
import anki_vector


def main():
    if len(sys.argv) < 2:
        print("no text provided", file=sys.stderr)
        return 1
    text = sys.argv[1]
    if not text.strip():
        return 0
    with anki_vector.Robot() as robot:
        robot.behavior.say_text(text)
    print("spoke: " + text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
