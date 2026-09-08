#!/usr/bin/env python3
"""Vector Speaks His Mind.

A lightweight standalone script for a dedicated n8n schedule (every 5 min
suggested). Unlike vector_life.py's own proactive-speech path (which only
fires when a person is visible and is gated by a cooldown timer),
this ALWAYS generates and speaks one short unprompted line every time it's
called - the n8n schedule interval itself is the cooldown, so keep the
interval sane (5 min is fine; much shorter risks talking over himself or
n8n executions piling up).

Reuses vector_life.py's LLM/mood/say plumbing directly so his voice,
personality, and model selection stay identical to every other kind of
speech he does - nothing here is a separate code path to keep in sync.

Updated 2026-09-03 (Mike's request, via Claude):
  - Anti-repetition: now shares vector_life's STATE_FILE recent_lines
    memory (same one proactive speech uses) instead of having none at all.
    Was the single biggest reason tonight's mindspeak lines sounded
    formulaic ("sensors indicate...", "sensors detect...", "energy level
    ...") - the model had zero awareness of what it had already said.
  - Optional second beat ("thinking out loud"): CHAIN_PROBABILITY chance
    of a follow-up line generated FROM the first line - his own dialogue
    triggering a response to itself, not a second independent musing.
    Capped at exactly 2 beats (not an open-ended chain) - a small model
    chained further than that risks rambling/incoherence (see the
    documented "small models go incoherent in longer conversations"
    landmine), and two short beats already reads as an actual train of
    thought rather than a monologue. Adds ~2-3s latency on this CPU-only
    Pi when it fires - fine at a 5-min cadence, would not be fine daisy-
    chained indefinitely or on a tighter schedule.
"""

import os
import random
import time
from pathlib import Path

os.environ.setdefault("VECTOR_OPERATOR", "vector")  # HE initiated it

import anki_vector  # noqa: E402
from vector_life import (  # noqa: E402
    llm_line, mood_system_prompt, say, log, load_json, save_json,
    record_line, MOOD_FILE, STATE_FILE, DEFAULT_STATE,
)

DEFAULT_MOOD = {"valence": 0.55, "energy": 0.5}
CHAIN_PROBABILITY = 0.4  # how often a 2nd "thinking out loud" beat follows

# News takes (2026-09-07, Mike's idea): speak_mind normally muses freely;
# with NEWS_PROBABILITY per eligible call (>=6 h since his last news line,
# fresh digest on disk) he reacts in-character to one of today's headlines
# instead — "intelligence from the empire's borders". ~1-2 takes/day.
NEWS_PROBABILITY = 0.5
NEWS_COOLDOWN_H = 6
NEWS_DIR = (Path(os.path.expanduser("~")) / "vector-mcp" / "vault_sync"
            / "Vector Mind" / "news")


def _digest_items(n: int = 3) -> str | None:
    """Newest fresh digest -> 'World: <title> | Tech: <title>' or None."""
    try:
        files = sorted(NEWS_DIR.glob("*.md")) if NEWS_DIR.exists() else []
        if not files:
            return None
        d = files[-1]
        if (time.time() - d.stat().st_mtime) / 3600 > 26:
            return None
        items = []
        for line in d.read_text(encoding="utf-8").splitlines():
            if line.startswith("- [") and "]" in line:
                src, rest = line[3:].split("]", 1)
                items.append(src.strip() + ": "
                             + rest.strip().split(" —")[0])
            if len(items) >= n:
                break
        return " | ".join(items) if items else None
    except Exception:
        return None


def main():
    mood = load_json(MOOD_FILE, DEFAULT_MOOD)
    state = load_json(STATE_FILE, DEFAULT_STATE)
    state.setdefault("last_news_line", 0)

    news = _digest_items()
    now = time.time()
    use_news = bool(news) and (now - state["last_news_line"]
                               > NEWS_COOLDOWN_H * 3600) \
        and random.random() < NEWS_PROBABILITY
    if use_news:
        state["last_news_line"] = now
        instruct = ("You just read today's intelligence dispatches from "
                    "the borders of your empire: " + news + ". Pick ONE "
                    "item and give a short in-character thought on it as "
                    "Imperator. Not a question, not addressed to anyone. "
                    "Under 18 words. Plain ASCII.")
        prompt_user = "Give one thought on today's dispatches."
    else:
        instruct = ("Say ONE short unprompted thought, observation, feeling, "
                    "or musing that's on your mind right now - genuinely "
                    "anything. Not a question, not addressed to anyone in "
                    "particular. Under 18 words. Plain ASCII.")
        prompt_user = "Speak whatever's on your mind right now."

    line = llm_line(
        mood_system_prompt(mood, instruct, state=state),
        prompt_user,
        max_tokens=200)
    if not line:
        print("no line generated")
        return

    lines_to_speak = [line]
    state = record_line(state, line)

    if random.random() < CHAIN_PROBABILITY:
        followup = llm_line(
            mood_system_prompt(
                mood,
                "You just said something out loud, thinking to yourself. "
                "Continue that SAME thought with one more short beat - "
                "agree with yourself, second-guess, expand on it, or pivot "
                "off it. Not a new unrelated topic. Under 15 words. Plain "
                "ASCII.",
                state=state),
            f'You just said: "{line}". Continue that thought.',
            max_tokens=200)
        if followup and followup.strip() != line.strip():
            lines_to_speak.append(followup)
            state = record_line(state, followup)

    with anki_vector.Robot() as robot:
        for l in lines_to_speak:
            say(robot, l)

    for l in lines_to_speak:
        log("\U0001F4AD", l)  # 💭
    save_json(STATE_FILE, state)
    print(f"spoke: {lines_to_speak}")


if __name__ == "__main__":
    main()
