#!/usr/bin/env python3
"""Vector Proactive Speech -> now the Life Engine.

Thin wrapper so the existing n8n "Vector Proactive Speech" workflow keeps
running unchanged. All logic moved to `vector_life.py` (Echo, 2026-08-07):
proactive speech PLUS idle humming, greetings, mood, curiosity, reminders,
battery watchdog, TV comments, ambient animations, Hue mood.

Old implementation preserved in `proactive.py.bak.20260807`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vector_life import main  # noqa: E402

if __name__ == "__main__":
    main()
