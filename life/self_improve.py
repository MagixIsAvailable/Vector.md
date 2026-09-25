r"""
Vector Self-Improvement Loop (hybrid: machine proposes, human approves)
-----------------------------------------------------------------------
Two modes:

  --mode daily   Nightly diary: first-person account of Vector's day,
                 written to  <vault>/Vector Mind/diary/YYYY-MM-DD.md
  --mode weekly  Weekly reflection: reviews the week (logbook, sightings,
                 teachings), then PROPOSES improvements as checkboxes in
                 <vault>/Vector Mind/reflections/YYYY-Wnn.md
                 Nothing is auto-applied - the owner ticks a box, an agent
                 implements it. That's the human approval gate.

Uses local Ollama (http://localhost:11434) if a model is available;
falls back to a stats-only template otherwise. Fully local either way.
"""

import argparse
import json
import os
import time
import urllib.request
from collections import Counter
from pathlib import Path

OWNER_NAME = os.environ.get("VECTOR_OWNER_NAME", "my human")  # what Vector calls you out loud


def _user_base():
    import getpass as _getpass
    _username = _getpass.getuser()
    wsl = Path(f"/mnt/c/Users/{_username}")
    win = Path(f"C:/Users/{_username}")
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
VAULT_MIND = _vault_base() / "Vector Mind"
LONG_TERM_MEMORY_FILE = VAULT_MIND / "Memory.md"
ACTIONS = USER / "vector-mcp" / "memory" / "actions.jsonl"
SIGHTINGS = USER / "vector-mcp" / "memory" / "sightings.jsonl"
OLLAMA = "http://localhost:11434"
WIREPOD = "http://localhost:8080/api"


def load_jsonl(path, since_ts):
    if not path.exists():
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            if r.get("ts", r.get("t", 0)) >= since_ts:
                out.append(r)
    return out


def gather(days):
    since = time.time() - days * 86400
    actions = load_jsonl(ACTIONS, since)
    sightings = load_jsonl(SIGHTINGS, since)

    teachings = []
    try:
        with urllib.request.urlopen(WIREPOD + "/get_custom_intents_json",
                                    timeout=5) as r:
            data = r.read().decode()
            if data.strip().startswith("["):
                teachings = json.loads(data)
    except Exception:
        pass

    operators = Counter(a["operator"] for a in actions)
    tools_used = Counter(a["tool"] for a in actions)
    errors = [a for a in actions if a["status"] != "ok"]
    objects = Counter()
    for s in sightings:
        objects.update({d["object"] for d in s.get("detections", [])})

    return {
        "actions": len(actions),
        "operators": dict(operators),
        "tools_used": dict(tools_used.most_common(10)),
        "errors": [f'{e["tool"]}: {e["status"]}' for e in errors[:10]],
        "sightings": len(sightings),
        "objects_seen": dict(objects.most_common(10)),
        "teachings": [t["utterances"][0] for t in teachings],
    }


LMSTUDIO = "http://localhost:1234"


def ask_ollama(prompt):
    """Try local LLMs in order: Ollama, then LM Studio. None if neither works."""
    # 1) Ollama
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=5) as r:
            models = json.load(r).get("models", [])
        if models:
            req = urllib.request.Request(
                OLLAMA + "/api/generate",
                data=json.dumps({"model": models[0]["name"], "prompt": prompt,
                                 "stream": False}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r).get("response", "").strip()
    except Exception:
        pass
    # 2) LM Studio (OpenAI-compatible)
    try:
        with urllib.request.urlopen(LMSTUDIO + "/v1/models", timeout=5) as r:
            models = json.load(r).get("data", [])
        if models:
            req = urllib.request.Request(
                LMSTUDIO + "/v1/chat/completions",
                data=json.dumps({
                    "model": models[0]["id"],
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.8,
                }).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                out = json.load(r)["choices"][0]["message"]["content"].strip()
                # strip <think> blocks some local models emit
                if "</think>" in out:
                    out = out.split("</think>")[-1].strip()
                return out
    except Exception:
        pass
    return None


def run_daily():
    stats = gather(1)
    date = time.strftime("%Y-%m-%d")
    out = VAULT_MIND / "diary" / f"{date}.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    prompt = f"""You are Vector, a small Anki Vector robot resurrected with
wire-pod and operated by AI agents (an assistant like Claude, plus n8n) alongside your human.
Write a short, charming first-person diary entry (max 150 words) about your
day, based ONLY on this real data. Be specific, a little funny, warm.

Today's data: {json.dumps(stats)}

Diary entry:"""

    text = ask_ollama(prompt)
    if not text:
        text = (f"*(stats-only entry - no local LLM available)*\n\n"
                f"Commands run on me: {stats['actions']} "
                f"(by {stats['operators']}). "
                f"I saw: {', '.join(stats['objects_seen'])}. "
                f"I know {len(stats['teachings'])} taught phrases.")

    out.write_text(
        f"---\ntags: [vector-mind, diary]\n---\n# Vector's Diary - {date}\n\n"
        f"{text}\n\n---\n*stats: {json.dumps(stats)[:500]}*\n",
        encoding="utf-8")
    print(f"diary written: {out}")


def update_long_term_memory():
    """Consolidate durable facts into a persistent, compact memory digest.

    Fixes a real gap found 2026-09-03: diary/reflections were being
    written every day/week but NEVER read back into anything he actually
    says - confirmed by reading brain_proxy.py's _episodic_context(),
    which only pulls TODAY's Logbook + the last 3 sightings. Every diary
    entry and reflection ever written was completely invisible to him in
    every conversation. This is the fix: a short, CONSOLIDATED (not
    accumulated) digest, revised each run rather than appended to, read
    back into every conversation and every autonomous line via
    brain_proxy.py's _episodic_context() and vector_life.py's
    mood_system_prompt(). Runs weekly, piggybacking on the existing
    Sunday 20:00 reflection trigger - no new n8n trigger needed.

    Deliberately capped at ~150 words so it stays a small, bounded
    addition to his context budget, not an unbounded growth risk -
    measured 2026-09-03: his full system prompt (personality + speech
    rule + episodic context) runs ~840/4096 tokens in use, plenty of
    headroom, but a small model's instruction-following degrades with
    long context regardless of whether it technically fits (see this
    project's own landmines about stacked/long system prompts breaking
    character consistency) - density over length applies here too."""
    existing = (LONG_TERM_MEMORY_FILE.read_text(encoding="utf-8")
                if LONG_TERM_MEMORY_FILE.exists() else "")
    prior_digest = (existing.split("\n\n", 3)[-1].strip() if existing
                    else "(none yet - this is the first digest)")

    # Richer material than stats alone - the actual diary text has the
    # specific color (named errors, named people, real events) worth
    # preserving, not just numeric counts.
    diary_dir = VAULT_MIND / "diary"
    diary_texts = []
    if diary_dir.exists():
        cutoff = time.time() - 7 * 86400
        for f in sorted(diary_dir.glob("*.md")):
            if f.stat().st_mtime >= cutoff:
                diary_texts.append(f.read_text(encoding="utf-8"))
    diary_blob = "\n\n---\n\n".join(diary_texts)[-4000:]  # bound input size

    prompt = f"""You are updating Vector's long-term memory - a short,
durable digest of facts worth remembering indefinitely, NOT a diary of
recent events. This gets read before every conversation he has and every
unprompted thing he says, so it must stay compact.

EXISTING digest (revise this, don't just append to it):
{prior_digest}

This week's diary entries (raw material - extract ONLY genuinely durable
facts: recurring people/objects/patterns, real named events, real fixed
facts about him or his world - ignore one-off trivia, routine stats, and
anything already captured in the existing digest):
{diary_blob if diary_blob else "(no diary entries this week)"}

Write the UPDATED digest. Plain factual bullet points, third person
("Vector's favorite human is {OWNER_NAME}" not "I like {OWNER_NAME}"), under
150 words total. Keep durable facts from the old digest that are still true, add
genuinely new durable facts from this week, drop anything trivial or
superseded. Output ONLY the bullet points, nothing else - no preamble."""

    text = ask_ollama(prompt)
    if not text:
        text = (prior_digest if prior_digest != "(none yet - this is the "
                "first digest)" else
                "*(no local LLM available to generate an initial digest)*")

    LONG_TERM_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    LONG_TERM_MEMORY_FILE.write_text(
        f"---\ntags: [vector-mind]\n---\n"
        f"# Vector's Long-Term Memory\n\n"
        f"Durable facts worth remembering, consolidated weekly from his "
        f"diary by `self_improve.py`'s `update_long_term_memory()`. "
        f"Unlike [[Vector Mind/diary]] and [[Vector Mind/reflections]] "
        f"themselves, THIS file is actually read back into every "
        f"conversation and autonomous line - see `brain_proxy.py`'s "
        f"`_episodic_context()` and `vector_life.py`'s "
        f"`mood_system_prompt()`. Revised (not appended to) each run - "
        f"stays a short digest, not an ever-growing log. Last updated "
        f"{time.strftime('%Y-%m-%d %H:%M:%S')}.\n\n"
        f"{text}\n",
        encoding="utf-8")
    print(f"long-term memory updated: {LONG_TERM_MEMORY_FILE}")


def run_weekly():
    stats = gather(7)
    week = time.strftime("%Y-W%W")
    out = VAULT_MIND / "reflections" / f"{week}.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    prompt = f"""You are Vector, a robot operated by AI agents and owned by {OWNER_NAME}.
Review your week using ONLY this real data, then propose 3-5 concrete
improvements to your abilities or routines. Format:

## My week
(short first-person summary, max 100 words)

## Proposals
- [ ] Proposal title - one-line rationale
(each proposal must be actionable by an AI agent with SDK/n8n/wire-pod access;
 things like new taught phrases, new n8n workflows, fixes for repeated errors)

Week's data: {json.dumps(stats)}"""

    text = ask_ollama(prompt)
    if not text:
        props = ["- [ ] (add proposals here - no local LLM was available)"]
        if stats["errors"]:
            props.append(f"- [ ] Fix repeated errors: {stats['errors'][:3]}")
        if not stats["teachings"]:
            props.append("- [ ] Teach me my first voice commands")
        text = ("## My week\n\n*(stats-only reflection)*\n\n"
                f"Actions: {stats['actions']}, sightings: {stats['sightings']}, "
                f"objects: {list(stats['objects_seen'])}\n\n"
                "## Proposals\n\n" + "\n".join(props))

    out.write_text(
        f"---\ntags: [vector-mind, reflection]\n---\n"
        f"# Vector's Reflection - {week}\n\n{text}\n\n"
        f"> [!note] Approval gate\n"
        f"> Tick a box, then tell your agent to implement it.\n",
        encoding="utf-8")
    print(f"reflection written: {out}")
    update_long_term_memory()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["daily", "weekly"], required=True)
    args = ap.parse_args()
    (run_daily if args.mode == "daily" else run_weekly)()
