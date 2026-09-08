r"""
Vector Brain Proxy
-------------------
Sits between wire-pod and LM Studio. Gives Vector's conversation brain two
tools:
  - request_capability - when he wants something he doesn't have, he can
    properly ASK (logged, visible, needs approval) instead of wire-pod's old
    fragile {{getImage}} hack (which crashed him earlier tonight).
  - look - read-only perception: snap a camera frame + YOLO detections so he
    can answer "what do you see / how many fingers" style questions.
    (Added 2026-08-07 - resolves a long-standing request of his.)

He is never given tools that DO things (drive, teach, change settings) -
only the ability to ask a parent, and to look. That is deliberate: agency to
request, not to act. See Vector Mind/Parents.md and Requests.md.

wire-pod's "endpoint" should point here instead of directly at LM Studio:
    http://localhost:8590/v1
This proxy forwards everything else to LM Studio unchanged.

Run:
    venv\Scripts\python.exe brain_proxy.py
"""

import json
import os

os.environ.setdefault("VECTOR_OPERATOR", "vector")  # requests are HIS voice

from flask import Flask, request, jsonify  # noqa: E402
import urllib.request as _ur  # noqa: E402

import vector_mcp_server as vms  # noqa: E402  (reuses vector_request_capability)
from vector_personality import BASE_PERSONALITY  # noqa: E402  (approved Roger/Terminator)
import vector_life as vl  # noqa: E402  (mood_update/load_json/save_json/MOOD_FILE - safe to
                           # import, main() is guarded, same pattern vector_watchdog.py uses)
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # noqa: E402

app = Flask(__name__)

LMSTUDIO = os.environ.get("VECTOR_BRAIN_URL", "http://localhost:11434") + "/v1"

_PENDING_FILE = vms._USER / "vector-mcp" / "memory" / "pending_request.json"

# Sentiment -> mood (Mike's idea 2026-08-20). Closes a known gap
# (Capabilities.md): mood was tracked and DID color generated speech, but
# almost nothing fed it back except a handful of watchdog/proactive event
# sites - live conversation itself never moved it. VADER (tiny, no ML
# model, pure Python - Pi-friendly) scores Mike's words each turn and
# nudges mood a small amount, same mood_update()/MOOD_FILE the rest of the
# codebase already shares.
_sentiment = SentimentIntensityAnalyzer()
# Small deltas on purpose - one sentence shouldn't swing his whole mood;
# it should accumulate over a real conversation the way the autonomous
# events already do (e.g. picked_up is +-0.20/+0.10, single-event deltas
# of that same order).
SENTIMENT_VALENCE_SCALE = 0.06
SENTIMENT_ENERGY_SCALE = 0.03


def _update_mood_from_text(text):
    """Score Mike's message and nudge memory/mood.json. Best-effort: a
    mood update failing should never break the actual conversation
    response, so this swallows its own errors."""
    if not text or not text.strip():
        return
    try:
        compound = _sentiment.polarity_scores(text)["compound"]  # -1..+1
        mood = vl.load_json(vl.MOOD_FILE, vl.DEFAULT_MOOD)
        mood = vl.mood_update(
            mood,
            compound * SENTIMENT_VALENCE_SCALE,
            abs(compound) * SENTIMENT_ENERGY_SCALE)  # strong feeling either
                                                       # way = a bit more energy
        vl.save_json(vl.MOOD_FILE, mood)
    except Exception:
        pass

AFFIRM = ["yes", "yeah", "yep", "yup", "sure", "go ahead", "granted",
          "permission granted", "you can", "you have permission",
          "approved", "do it", "okay do it", "ok do it", "sounds good",
          "fine by me", "go for it"]
DECLINE = ["no", "nope", "not now", "don't", "do not", "not yet", "denied",
           "not today", "maybe later"]




def _set_pending(what, why):
    _PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PENDING_FILE.write_text(json.dumps(
        {"what": what, "why": why, "ts": __import__("time").time()}))


def _get_pending():
    if _PENDING_FILE.exists():
        try:
            return json.loads(_PENDING_FILE.read_text())
        except Exception:
            return None
    return None


def _clear_pending():
    if _PENDING_FILE.exists():
        _PENDING_FILE.unlink()


def _check_approval(user_text):
    """If there's a pending request and Mike's reply is a clear yes/no,
    record it. Returns a note to inject into context, or None."""
    pending = _get_pending()
    if not pending:
        return None
    low = user_text.lower().strip()
    is_yes = any(a in low for a in AFFIRM)
    is_no = any(d in low for d in DECLINE) and not is_yes
    if not (is_yes or is_no):
        return None
    vms.vector_approve_request(approved=is_yes, note=pending["what"])
    _clear_pending()
    if is_yes:
        return (f"SYSTEM NOTE: Mike just gave verbal permission for: "
                f"'{pending['what']}'. Thank him briefly and say a parent "
                f"will build it soon.")
    return (f"SYSTEM NOTE: Mike just declined: '{pending['what']}'. "
            f"Accept it gracefully in one short line, no sulking.")


def _episodic_context():
    """Short 'today' summary: recent logbook lines + recently seen objects.
    Gives the conversation brain memory of his own day (idea 13/18).

    Added 2026-09-03: also reads the persistent long-term memory digest
    (self_improve.py's update_long_term_memory(), [[Vector Mind/Memory]]) -
    closes a real gap where diary/reflections were being written every
    day/week but NEVER read back here or anywhere else. Placed FIRST
    (durable identity facts before today's ephemeral specifics) since that
    reads more naturally as foundational context."""
    try:
        from pathlib import Path
        import time as _t
        parts = []
        mem_note = vms._VAULT_MIND / "Memory.md"
        if mem_note.exists():
            mtext = mem_note.read_text(encoding="utf-8")
            mbody = (mtext.split("\n\n", 3)[-1].strip()
                     if "\n\n" in mtext else mtext)
            if mbody:
                parts.append("What you know long-term: "
                             + mbody.replace("\n", " "))
        lb = Path(vms._LOGBOOK_FILE)
        if lb.exists():
            text = lb.read_text(encoding="utf-8")
            day = "## " + _t.strftime("%Y-%m-%d")
            if day in text:
                sec = text.split(day, 1)[1].split("\n## ", 1)[0]
                # WHITELIST, not blacklist (switched 2026-08-20 after a
                # blacklist kept needing new entries): only markers that
                # represent things that actually happened TO him are safe
                # context. Everything else - dev-log narrative (idea 💡 /
                # decision 🧭), raw error dumps (⚠️), plain tool-call lines
                # - confused a small model into either roleplaying the dev
                # log as fiction, or mangling technical error text into
                # garbled "self-narrative" (both confirmed live
                # 2026-08-20). Lines are "HH:MM:SS <emoji> **who** ..." -
                # the emoji is NOT at position 0, so this checks
                # membership, not startswith.
                keep_markers = ("🎵", "🙋", "👀", "⚡", "🤲", "✅", "🧱")
                raw = [l.strip(" -") for l in sec.splitlines()
                       if l.strip().startswith("-")]
                lines = [l for l in raw
                         if any(m in l for m in keep_markers)][-5:]
                if lines:
                    parts.append("Today: " + " | ".join(lines))
        mem = Path(vms._MEMORY_FILE)
        if mem.exists():
            recs = [json.loads(l) for l in open(mem, encoding="utf-8")
                    if l.strip()][-3:]
            objs = []
            for r in recs:
                objs += [d["object"] for d in r.get("detections", [])]
            if objs:
                parts.append("Recently seen: " +
                             ", ".join(dict.fromkeys(objs)))
        # Daily news digest (2026-09-07, Mike's idea): newest digest file,
        # freshness-gated, first 3 items only — intelligence reports from
        # his empire's borders. Titles only, no summaries (token budget).
        try:
            ndir = vms._VAULT_MIND / "news"
            if ndir.exists():
                files = sorted(ndir.glob("*.md"))
                if files:
                    digest = files[-1]
                    age_h = (_t.time() - digest.stat().st_mtime) / 3600
                    if age_h <= 26:
                        items = []
                        for line in digest.read_text(
                                encoding="utf-8").splitlines():
                            if line.startswith("- [") and "]" in line:
                                src, rest = line[3:].split("]", 1)
                                items.append(src.strip() + ":"
                                             + rest.strip().split(" —")[0])
                            if len(items) >= 3:
                                break
                        if items:
                            parts.append("News today: "
                                         + " | ".join(items))
        except Exception:
            pass
        return " ".join(parts)
    except Exception:
        return ""


TOOLS = [{
    "type": "function",
    "function": {
        "name": "request_capability",
        "description": (
            "Ask one of your parents (whichever AI agent maintains you) for a new ability "
            "you don't currently have. This does NOT grant it - it only logs "
            "a visible request they review with Mike. Use this when you "
            "genuinely need to do something you can't, e.g. actually seeing "
            "an image, remembering something long-term you can't yet, or any "
            "physical action outside conversation. Do not overuse - only for "
            "real gaps, not for things you can already just say."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "what": {"type": "string", "description": "The capability you want, in your own words."},
                "why": {"type": "string", "description": "Why - what happened that made you ask."},
            },
            "required": ["what", "why"],
        },
    },
}, {
    "type": "function",
    "function": {
        "name": "look",
        "description": (
            "Look through your camera right now and describe what you see. "
            "Use this when someone asks what you see, what's in front of you, "
            "how many fingers, what Mike is holding, or anything visual. "
            "Read-only - it just tells you what objects are in view."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}]


def call_lmstudio(payload):
    req = _ur.Request(
        LMSTUDIO + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with _ur.urlopen(req, timeout=120) as r:
        raw = r.read()
    if not raw.strip():
        raise RuntimeError("LM Studio returned an empty response body")
    return json.loads(raw)


def _sanitize_history(messages, max_turns=12):
    """Drop tool-call artifacts and cap history length before forwarding to
    LM Studio. A long accumulated conversation (wire-pod's remembered chat)
    with stray tool_calls/null-content turns can make the chat template
    choke and return an empty/broken response."""
    clean = []
    for m in messages:
        role = m.get("role")
        if role not in ("system", "user", "assistant"):
            continue  # drop any "tool" role messages
        content = m.get("content")
        if content is None:
            continue  # drop assistant turns that were pure tool_calls
        clean.append({"role": role, "content": content})
    system = [m for m in clean if m["role"] == "system"][:1]
    rest = [m for m in clean if m["role"] != "system"][-max_turns:]
    return system + rest


def _fallback_reply(reason):
    """A valid chat-completion response Vector can actually speak, used
    whenever LM Studio fails, instead of ever sending wire-pod a crash."""
    print(f"[brain_proxy] falling back: {reason}")
    return {
        "id": "fallback", "object": "chat.completion",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant",
            "content": "Sorry, I lost my train of thought for a second. Can you say that again?",
        }}],
    }


PREFERRED_MODEL = os.environ.get("VECTOR_BRAIN_MODEL", "qwen2.5:3b-instruct")
# Chosen 2026-08-19, revised same day after Mike reported 20GB-class models
# (qwen3-vl-30b, ~19.6GB) freezing his PC. LM Studio's `lms ps` showed it
# stuck resident for up to an hour (TTL) even after use, stacking with
# other loaded models. Went looking for something genuinely small instead:
#   qwen2.5-coder-7b-instruct (4.7GB) - dumps tool calls as raw JSON text
#     in `content` instead of the proper tool_calls field (would be spoken
#     as garbled JSON), over-triggers on benign prompts. Rejected.
#   qwen3.5-9b/27b, qwen3.6-27b, qwen3-4b-thinking, gemma-4-12b-qat - all
#     reasoning models that burn the ENTIRE token budget on invisible
#     reasoning_content and return empty `content` if max_tokens is too
#     small (same landmine as before with gpt-oss). Rejected outright,
#     EXCEPT nemotron below, which just needed a bigger budget.
#   qwen3-vl-30b (19.6GB) - worked correctly, but too large/slow to keep
#     resident - this is the one that was freezing the PC. Rejected on
#     resource grounds even though it behaved correctly.
#   nemotron-3-nano-4b (2.8GB, by far the smallest tested) - ALSO a
#     reasoning model under the hood, but confirmed clean both ways
#     (plain content AND correct tool_calls) once given >=300 max_tokens
#     for its reasoning to actually finish before hitting the limit. See
#     MIN_MAX_TOKENS below - this is why it exists.


def _resolve_model():
    """Pick the conversation-brain model: PREFERRED_MODEL if it's actually
    loaded in LM Studio right now, otherwise fall back to whatever wire-pod
    requested. Checked live (not just hardcoded) per the landmine in
    !Start Here.md - a hardcoded model name silently broke him for ~30 min
    once already when the brain got upgraded."""
    try:
        req = _ur.Request(LMSTUDIO + "/models")
        with _ur.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        ids = [m["id"] for m in data.get("data", [])]
        if PREFERRED_MODEL in ids:
            return PREFERRED_MODEL
        print(f"[brain_proxy] preferred model '{PREFERRED_MODEL}' not loaded "
              f"in LM Studio (have: {ids}) - using wire-pod's requested model instead")
    except Exception as e:
        print(f"[brain_proxy] could not query LM Studio /models ({e}) - "
              f"using wire-pod's requested model instead")
    return None


SPEECH_FORMAT_RULE = (
    "Everything you say is spoken aloud by text-to-speech, never read as "
    "text. Reply in plain speakable sentences only: no markdown (no "
    "**bold**, no bullet points, no headers), no special unicode "
    "punctuation (use plain spaces, a plain hyphen, plain quotes) - just "
    "words as you'd say them out loud. Never mention function names, tool "
    "names, code, or internal identifiers (e.g. vector_get_battery, JSON) "
    "- phrase everything as natural spoken English. Always answer in "
    "English: never reply in Chinese or any other language, even if the "
    "user writes to you in another language."
)

_MARKDOWN_STRIP = [
    ("**", ""), ("__", ""), ("`", ""), ("# ", ""), ("### ", ""), ("## ", ""),
]
_UNICODE_NORMALIZE = {
    " ": " ", " ": " ", " ": " ", "​": "",  # odd spaces
    "‘": "'", "’": "'", "“": '"', "”": '"',  # smart quotes
    "–": "-", "—": "-",  # en/em dash
    "×": " times ", "÷": " divided by ", "−": "-",  # math symbols
    "±": " plus or minus ", "≈": " about ", "≠": " not ",
}


def _sanitize_for_speech(text):
    """Belt-and-suspenders on top of SPEECH_FORMAT_RULE: small models don't
    always follow formatting instructions (confirmed live 2026-08-19 -
    nemotron answered "10x10" with markdown bold and Unicode narrow spaces,
    which is why wire-pod's TTS silently went nowhere on that turn). Strip
    what a prompt instruction can't reliably prevent."""
    if not text:
        return text
    for bad, good in _MARKDOWN_STRIP:
        text = text.replace(bad, good)
    for bad, good in _UNICODE_NORMALIZE.items():
        text = text.replace(bad, good)
    return text


def _sse_wrap(text):
    """Wrap a final answer as a minimal valid SSE chat-completion stream -
    wire-pod ALWAYS requests stream=true and its client fails hard
    ("Stream error") if it gets a plain JSON blob back instead. This was the
    actual cause of every LLM reply failing to be spoken: we forwarded
    stream=true to LM Studio, mis-parsed the real SSE as one JSON blob,
    silently fell back, then answered wire-pod with plain JSON anyway -
    wire-pod's stream reader choked on the format regardless of content."""
    def gen():
        chunk = {
            "id": "vector", "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": text},
                        "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        done = {
            "id": "vector", "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(done)}\n\n"
        yield "data: [DONE]\n\n"
    return app.response_class(gen(), mimetype="text/event-stream")


@app.route("/v1/chat/completions", methods=["POST"])
def chat():
    try:
        body = request.get_json(force=True)
        wants_stream = bool(body.get("stream"))
        probe = request.headers.get("X-Vector-Probe") == "1"  # eval harness runs: no mood/logbook side effects
        body["stream"] = False  # we always talk to LM Studio non-streaming
        resolved_model = _resolve_model()
        if resolved_model:
            body["model"] = resolved_model
        # Cap temperature - Mike reported an incoherent reply live
        # ("battery down black holes memo", 593 reasoning tokens for a
        # 4-word answer - the model was drifting/spinning, not truncating).
        # No temperature was being set at all before this, so it ran
        # whatever wire-pod/LM Studio defaulted to. Lower temperature is
        # the standard mitigation for a small reasoning model rambling
        # into incoherence - doesn't guarantee it never happens again
        # (this is real model instability, not a discrete bug), but should
        # reduce how often.
        body["temperature"] = min(body.get("temperature") or 0.6, 0.6)
        # nemotron-3-nano-4b (and small reasoning models generally) spend
        # real tokens on invisible reasoning_content before the actual
        # answer - too small a max_tokens (wire-pod's default is skimpy)
        # truncates mid-thought and Vector says nothing. Floor it.
        # Raised 300 -> 800 on 2026-08-19: the approved personality block
        # makes the model deliberate longer before speaking; at 300 it
        # burned the whole budget on reasoning and emitted an EMPTY reply.
        MIN_MAX_TOKENS = 800
        if (body.get("max_tokens") or 0) < MIN_MAX_TOKENS:
            body["max_tokens"] = MIN_MAX_TOKENS
        messages = _sanitize_history(body.get("messages", []))
        # Drop any system message _sanitize_history kept from wire-pod's
        # own request (its content is generic/unknown to us) - our single
        # combined block below replaces it entirely, doesn't stack with it.
        messages = [m for m in messages if m.get("role") != "system"]
        # ONE consolidated system message, not 3 stacked ones - a small
        # model reliably losing the character to generic-assistant priors
        # when personality/format/context were separate system turns
        # (confirmed live 2026-08-20: denied having a battery, reasoning
        # explicitly said the "system message about vector...possibly not
        # needed"). A single coherent block is easier for it to hold onto.
        system_block = BASE_PERSONALITY + " " + SPEECH_FORMAT_RULE
        _ctx = _episodic_context()
        if _ctx:
            system_block += " [CONTEXT] " + _ctx
        messages.insert(0, {"role": "system", "content": system_block})

        # Check Mike's latest message against any pending request BEFORE
        # calling the model - approval is a plain state check on Mike's
        # words, never something the model decides about itself.
        last_user_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                break
        if last_user_idx is not None and not probe:
            user_text = str(messages[last_user_idx].get("content", ""))
            _update_mood_from_text(user_text)
            note = _check_approval(user_text)
            # Conversation capture: log Mike's side (typed via the dashboard
            # talk panel, or spoken->VOSK via wire-pod) so the evaluation
            # layer sees both sides of every conversation. Best effort -
            # logging must never break the reply path.
            try:
                import sys as _s
                _s.path.insert(0, str(vms._USER / "vector-mcp"))
                import vector_life as _vl
                _vl.log_speech(user_text[:500], source="mike")
            except Exception:
                pass
            if note:
                # Fold into the user turn - gpt-oss's chat template 400s on
                # extra system messages mid-conversation.
                orig = messages[last_user_idx]
                messages[last_user_idx] = {
                    **orig,
                    "content": f"{orig.get('content', '')}\n\n({note})",
                }
        body["messages"] = messages
        no_tools = probe or request.headers.get("X-Vector-NoTools") == "1"
        body["tools"] = [] if no_tools else TOOLS  # probe + kiosk talks: text only, never real tool fires
        body.setdefault("tool_choice", "auto")

        def respond(chat_completion_dict):
            # Reverted 2026-08-19: was routing live-conversation replies
            # through the Piper robot-voice pipeline here, but that had
            # multiple voices/models fighting each other (Piper here,
            # firmware TTS for anything that slipped through, gpt-oss vs
            # qwen). Mike asked to go back to ONE voice (his original
            # onboard say_text firmware) for live conversation - wire-pod
            # handles speaking this text itself after we return it.
            # Piper/robot-voice tools (vector_say_piper, vector_say_robot)
            # are still available for manual/on-demand use.
            msg = chat_completion_dict["choices"][0]["message"]
            if msg.get("content"):
                msg["content"] = _sanitize_for_speech(msg["content"])
                if not probe:  # eval harness runs are silent observers
                    try:  # TV dashboard "last said" feed - best effort
                        import sys as _s
                        _s.path.insert(0, str(vms._USER / "vector-mcp"))
                        import vector_life as _vl
                        _vl.log_speech(msg["content"], source="chat")
                    except Exception:
                        pass
            if wants_stream:
                text = msg.get("content") or ""
                return _sse_wrap(text)
            return jsonify(chat_completion_dict)

        try:
            result = call_lmstudio(body)
        except Exception as e:
            return respond(_fallback_reply(f"first call failed: {e}"))

        msg = result["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return respond(result)

        # He called request_capability - execute the (safe, logging-only)
        # tool and give the model the result so it can speak a normal reply.
        messages = body["messages"] + [msg]
        for tc in tool_calls:
            fn = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except Exception:
                args = {}
            if fn == "request_capability":
                what = args.get("what", "(unspecified)")
                why = args.get("why", "(unspecified)")
                out = vms.vector_request_capability(what=what, why=why)
                _set_pending(what, why)
                out += (" Tell Mike out loud what you want and why, then "
                        "ask him plainly: 'Can I learn this?'")
            elif fn == "look":
                try:
                    res = vms.vector_recognize(speak=False)
                    data = json.loads(res) if isinstance(res, str) else res
                    dets = data.get("detections", [])
                    txt = ", ".join(
                        f"{d['object']} ({d['confidence']:.2f})" for d in dets)
                    out = ("You just looked through your camera. You see: "
                           + (txt or "nothing recognizable")
                           + ". Describe it simply to Mike, or answer his "
                             "question about it.")
                except Exception as e:
                    out = f"look failed: {e}"
            else:
                out = f"unknown tool {fn}"
            messages.append({
                "role": "tool", "tool_call_id": tc["id"], "content": out,
            })

        followup = dict(body)
        followup["messages"] = messages
        followup.pop("tools", None)
        followup.pop("tool_choice", None)
        try:
            final = call_lmstudio(followup)
        except Exception as e:
            return respond(_fallback_reply(f"followup call failed: {e}"))
        return respond(final)

    except Exception as e:
        # Absolute last resort - wire-pod must NEVER see a raw 500 again.
        import traceback
        traceback.print_exc()
        fallback = _fallback_reply(f"unhandled: {e}")
        try:
            if wants_stream:
                return _sse_wrap(fallback["choices"][0]["message"]["content"])
        except NameError:
            pass  # crashed before wants_stream was even set
        return jsonify(fallback)


@app.route("/run/<name>", methods=["GET", "POST"])
def run_script(name):
    """Runs one of our maintenance scripts and returns its stdout.
    Exists because n8n on this instance has Execute Command disabled
    (security-hardened, confirmed via activation error) - so scheduled
    workflows call this HTTP endpoint instead of shelling out themselves."""
    import subprocess
    import sys as _sys

    scripts = {
        "daily": ["self_improve.py", "--mode", "daily"],
        "weekly": ["self_improve.py", "--mode", "weekly"],
        "babysitter": ["battery_babysitter.py", "--threshold", "3.8"],
        "proactive": ["proactive.py", "--cooldown", "25"],
        "mindspeak": ["speak_mind.py"],
    }
    if name not in scripts:
        return jsonify({"error": f"unknown script '{name}'. "
                        f"valid: {list(scripts)}"}), 404

    base = str(vms._USER / "vector-mcp")
    python = str(vms._USER / "vector-mcp" / "venv" / "Scripts" / "python.exe")
    if not __import__("os").path.exists(python):  # WSL fallback
        python = _sys.executable
    cmd = [python, str(vms._USER / "vector-mcp" / scripts[name][0])] + scripts[name][1:]
    try:
        result = subprocess.run(cmd, cwd=base, capture_output=True, text=True,
                                timeout=180)
        return jsonify({"script": name, "returncode": result.returncode,
                        "stdout": result.stdout[-3000:],
                        "stderr": result.stderr[-1500:]})
    except Exception as e:
        return jsonify({"script": name, "error": str(e)}), 500


if __name__ == "__main__":
    print("Vector brain proxy on http://localhost:8590/v1")
    print("Point wire-pod's knowledge-graph endpoint here.")
    app.run(host="0.0.0.0",
            port=int(os.environ.get("VECTOR_PORT", "8590")))
