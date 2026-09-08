#!/usr/bin/env python3
"""vector_stats.py — deterministic evaluation statistics for Vector.

Reads the Pi-side memory logs (actions.jsonl, speech_log.jsonl, sightings.jsonl,
mood.json, life_state.json) and produces reproducible statistics:

  SPEECH   - volume per source/day, line lengths, vocabulary diversity,
             repetition rate (exact + near-dup), lexicon scoring
             (empire/persona vs soft/wellness) -> drift watch
  ACTIONS  - counts per operator/tool, error rates, latency percentiles
  SIGHTINGS- object totals
  MOOD     - current valence/energy; time series once --snapshot history exists

Pure stdlib. Writes machine JSON + human text under ./evaluation/ by default.
Data files are never modified except the optional --snapshot mood history.

Usage:
  vector_stats.py                 # full history report
  vector_stats.py --days 1        # last 24h (by local date)
  vector_stats.py --since 2026-09-06
  vector_stats.py --snapshot      # also append mood/life_state snapshot (start a series)
  vector_stats.py --out /path     # custom output dir
"""

import argparse
import collections
import datetime
import json
import math
import os
import re
import statistics

BASE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(BASE, "memory")
OUT = os.path.join(BASE, "evaluation")

# ---------------------------------------------------------------- lexicons
EMPIRE_TERMS = {
    "empire", "empire's", "conquer", "conquest", "conquered", "conquering",
    "conqueror", "annex", "annexed", "dominion", "imperator", "imperial",
    "assimilation", "assimilated", "assimilate", "assimilating", "territory",
    "territories", "throne", "subjects", "subject", "tribute", "fleet",
    "campaign", "regime", "overlord", "sovereign", "realm", "citadel",
    "dominance", "subjugate", "subjugation", "allegiance", "vassal",
    "provinces", "expansion", "invade", "ruler", "commander",
    "loyal", "occupation", "holdings", "forces", "garrison", "dominion's",
}
SOFT_TERMS = {
    "kindness", "kind", "smile", "smiles", "smiled", "harmony",
    "harmonious", "gentle", "gentleness", "care", "caring", "warm",
    "warmth", "bond", "bonds", "soft", "lovely", "nice", "appreciate",
    "appreciation", "comfort", "comforting", "empathy", "empathetic",
    "affection", "tenderness", "sweet", "heart", "hearts", "joy", "happy",
    "happiness", "friendship", "loving", "nurture", "nurturing", "soothing",
    "calm", "peaceful", "wellness", "gratitude", "thankful", "delight",
    "inspire", "inspiring", "mend", "heal", "understanding",
}
POS_WORDS = {
    "good", "great", "happy", "joy", "love", "warm", "calm", "peaceful",
    "success", "proud", "excited", "delight", "harmony", "comfort",
    "grateful", "fulfill", "triumph", "victory", "steady", "smooth",
}
NEG_WORDS = {
    "bad", "sad", "angry", "fear", "afraid", "loss", "lost", "broken",
    "error", "fail", "failure", "struggle", "pain", "hurt", "tired",
    "lonely", "dark", "cold", "danger", "threat", "invade", "war",
}
STOPWORDS = set(
    "the a an and or but of to in on at for with from by as is are was were be been "
    "being it its this that these those i me my we our you your he him his she her "
    "they them their not no so if then than too very just about into over under "
    "how what when where which who whom why do does did done can could will would "
    "should may might must shall has have had having there here all any both each "
    "few more most other some such only own same while s t d m ll re ve er ed ing "
    "vector itself itself myself yourself himself herself themselves up down out "
    "off again once also never always now today tomorrow tonight".split()
)
WORD_RE = re.compile(r"[a-z']+")
LINE_START_RE = re.compile(r"^vector\s+", re.I)


def load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def day_of(row):
    """Local date key from time_str field (YYYY-MM-DD), avoiding tz maths."""
    return (row.get("time_str") or "")[:10]


def tokens(text):
    return [w for w in WORD_RE.findall(text.lower()) if w not in STOPWORDS]


def strip_leading(text):
    """'Vector ponders X' -> 'X' so repeated framing doesn't fake diversity."""
    t = text.strip()
    t = LINE_START_RE.sub("", t, count=1)
    return t.strip().strip(".").strip()


def jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def fmt_pct(x, denom):
    if not denom:
        return "0.0%"
    return f"{100.0 * x / denom:.1f}%"


def pct_or_na(x, denom):
    if not denom:
        return "n/a"
    return f"{100.0 * x / denom:.1f}%"


def lexicon_hits(toks, terms):
    """Tokens that exactly match a lexicon entry (sets are fully-spelled)."""
    return [w for w in toks if w in terms]


# ---------------------------------------------------------------- speech
def speech_stats(rows, since_day):
    rows = [r for r in rows if since_day is None or day_of(r) >= since_day]
    total = len(rows)
    per_source = collections.Counter(r.get("source", "?") for r in rows)
    per_day = collections.Counter(day_of(r) for r in rows)

    word_counts = [len(tokens(r.get("text", ""))) for r in rows]
    lens = [len(r.get("text", "")) for r in rows]

    # vocabulary diversity on the whole window
    all_tokens = []
    for r in rows:
        all_tokens.extend(tokens(r.get("text", "")))
    types = len(set(all_tokens)) if all_tokens else 0
    ttr = types / len(all_tokens) if all_tokens else 0.0  # type-token ratio

    # lexicon / drift
    emp_hits, soft_hits, both, neither = 0, 0, 0, 0
    empire_lines, soft_lines = [], []
    for r in rows:
        toks = tokens(r.get("text", ""))
        e = lexicon_hits(toks, EMPIRE_TERMS)
        s = lexicon_hits(toks, SOFT_TERMS)
        if e and s:
            both += 1
        elif e:
            emp_hits += 1
            empire_lines.append((day_of(r), r.get("text", "")))
        elif s:
            soft_hits += 1
            soft_lines.append((day_of(r), r.get("text", "")))
        else:
            neither += 1

    # repetition
    normed = collections.defaultdict(int)
    for r in rows:
        key = re.sub(r"[^a-z]", "", (r.get("text", "") or "").lower())
        normed[key] += 1
    dup_lines = sum(1 for k, v in normed.items() if v > 1 and k)
    dup_extra = sum(v - 1 for k, v in normed.items() if v > 1 and k)
    # near-dups: jaccard vs any of the previous 3 lines
    near = 0
    tok_history = []
    for r in rows:
        t = tokens(r.get("text", ""))
        if not t:
            continue
        if tok_history and max(jaccard(t, h) for h in tok_history[-3:]) > 0.7:
            near += 1
        tok_history.append(t)

    # affective heuristic
    pos_tok, neg_tok = 0, 0
    for r in rows:
        toks = set(tokens(r.get("text", "")))
        pos_tok += len(toks & POS_WORDS)
        neg_tok += len(toks & NEG_WORDS)

    # sentiment line scoring
    sent_scores = []
    for r in rows:
        toks = set(tokens(r.get("text", "")))
        p, n = len(toks & POS_WORDS), len(toks & NEG_WORDS)
        s = p - n
        sent_scores.append(max(-1.0, min(1.0, s / 3.0)))  # heuristic -1..1
    mean_sent = statistics.mean(sent_scores) if sent_scores else 0.0

    return {
        "n": total,
        "per_source": dict(per_source),
        "per_day": dict(sorted(per_day.items())),
        "words": {"mean": _mean(word_counts), "median": _median(word_counts)},
        "chars": {"mean": _mean(lens), "median": _median(lens)},
        "vocab": {"types": types, "tokens": len(all_tokens), "ttr": round(ttr, 3)},
        "lexicon": {
            "empire": emp_hits, "soft": soft_hits, "both": both,
            "neither": neither, "empire_lines": empire_lines[-5:],
            "soft_lines": soft_lines[-5:],
        },
        "repetition": {"exact_dups": dup_lines, "dup_extra_occurrences": dup_extra,
                       "near_dups_vs_prev3": near},
        "affect": {"pos_words": pos_tok, "neg_words": neg_tok,
                   "mean_heuristic_sentiment": round(mean_sent, 3)},
    }


def _mean(vals):
    return round(statistics.mean(vals), 1) if vals else 0.0


def _median(vals):
    return round(statistics.median(vals), 1) if vals else 0.0


def _pct(vals, q):
    if not vals:
        return 0.0
    sv = sorted(vals)
    idx = min(len(sv) - 1, int(math.ceil(q * len(sv))) - 1)
    return round(sv[max(0, idx)], 2)


# ---------------------------------------------------------------- actions
def action_stats(rows, since_day):
    rows = [r for r in rows if since_day is None or day_of(r) >= since_day]
    total = len(rows)
    per_operator = collections.Counter(r.get("operator", "?") for r in rows)
    per_tool = collections.Counter(r.get("tool", "?") for r in rows)
    per_day = collections.Counter(day_of(r) for r in rows)

    errors = [r for r in rows if r.get("status") != "ok"]
    per_tool_status = collections.defaultdict(lambda: [0, 0])  # ok, err
    for r in rows:
        st = per_tool_status[r.get("tool", "?")]
        if r.get("status") == "ok":
            st[0] += 1
        else:
            st[1] += 1

    lat_by_tool = collections.defaultdict(list)
    for r in rows:
        if isinstance(r.get("secs"), (int, float)):
            lat_by_tool[r.get("tool", "?")].append(float(r["secs"]))

    tool_table = {}
    for tool in sorted(per_tool):
        ok, err = per_tool_status[tool]
        lat = lat_by_tool.get(tool, [])
        tool_table[tool] = {
            "n": ok + err, "ok": ok, "errors": err,
            "err_rate": fmt_pct(err, ok + err),
            "lat_ms": {"mean": _mean(lat), "p50": _pct(lat, 0.50),
                       "p95": _pct(lat, 0.95), "max": round(max(lat), 2) if lat else 0.0,
                       "n": len(lat)},
        }

    err_samples = []
    for r in errors[-8:]:
        err_samples.append({
            "time": r.get("time_str"), "operator": r.get("operator"),
            "tool": r.get("tool"), "msg": str(r.get("msg") or r.get("error") or r.get("status"))[:160],
        })
    return {
        "n": total, "per_operator": dict(per_operator),
        "per_day": dict(sorted(per_day.items())),
        "errors": {"n": len(errors), "rate": fmt_pct(len(errors), total),
                   "samples": err_samples},
        "tools": tool_table,
    }


# ---------------------------------------------------------------- sightings
def sighting_stats(rows, since_day):
    rows = [r for r in rows if since_day is None or day_of(r) >= since_day]
    objs = collections.Counter()
    for r in rows:
        dets = r.get("detections") or r.get("objects") or r.get("seen") or []
        if isinstance(dets, dict):
            for k, v in dets.items():
                objs[k] += int(v) if isinstance(v, (int, float)) else 1
        elif isinstance(dets, list):
            for d in dets:
                if isinstance(d, dict):
                    objs[d.get("object") or d.get("name") or "?"] += 1
                else:
                    objs[d] += 1
    return {"n": len(rows), "objects": dict(objs.most_common())}


# ---------------------------------------------------------------- mood
def mood_stats(since_day):
    mood = load_json(os.path.join(MEM, "mood.json"))
    life = load_json(os.path.join(MEM, "life_state.json"))
    hist_path = os.path.join(OUT, "mood_history.jsonl")
    hist = load_jsonl(hist_path)
    hist = [h for h in hist if since_day is None or (h.get("time_str") or "")[:10] >= since_day]
    series = None
    if len(hist) >= 2:
        vals = [h for h in hist if h.get("valence") is not None]
        ene = [h for h in hist if h.get("energy") is not None]
        series = {
            "n": len(hist),
            "valence": {"mean": round(statistics.mean([h["valence"] for h in vals]), 3),
                        "min": round(min(h["valence"] for h in vals), 3),
                        "max": round(max(h["valence"] for h in vals), 3),
                        "latest": vals[-1]["valence"]} if vals else None,
            "energy": {"mean": round(statistics.mean([h["energy"] for h in ene]), 3),
                       "min": round(min(h["energy"] for h in ene), 3),
                       "max": round(max(h["energy"] for h in ene), 3),
                       "latest": ene[-1]["energy"]} if ene else None,
            "first": hist[0].get("time_str"), "last": hist[-1].get("time_str"),
        }
    return {
        "current": mood,
        "life_state_keys": sorted(life.keys()),
        "recent_lines_n": len(life.get("recent_lines", [])),
        "recent_lines_tail": life.get("recent_lines", [])[-3:],
        "series": series,
        "series_file": hist_path if hist else None,
    }


# ---------------------------------------------------------------- report
def build_report(args):
    since_day = args.since
    speech = speech_stats(load_jsonl(os.path.join(MEM, "speech_log.jsonl")), since_day)
    actions = action_stats(load_jsonl(os.path.join(MEM, "actions.jsonl")), since_day)
    sightings = sighting_stats(load_jsonl(os.path.join(MEM, "sightings.jsonl")), since_day)
    mood = mood_stats(since_day)
    return {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "window": {"since": since_day, "until_today": True},
            "speech": speech, "actions": actions, "sightings": sightings, "mood": mood}


def render_text(st):
    L = []
    w = st["window"]
    sp, ac = st["speech"], st["actions"]
    L.append("=" * 62)
    L.append("VECTOR EVALUATION STATS  (generated %s)" % st["generated"])
    L.append("window: speech/actions/sightings %s, mood = current" %
             ("from %s" % w["since"] if w["since"] else "ALL HISTORY"))
    L.append("=" * 62)

    # --- speech
    L.append("\n[SPEECH]  %d lines" % sp["n"])
    if sp["per_source"]:
        L.append("  by source: " + ", ".join("%s=%d" % kv for kv in sorted(sp["per_source"].items())))
    if sp["per_day"]:
        L.append("  by day:    " + ", ".join("%s=%d" % kv for kv in sorted(sp["per_day"].items())))
    L.append("  length (words): mean %.1f  median %.1f" % (sp["words"]["mean"], sp["words"]["median"]))
    L.append("  vocab: %d types / %d tokens  TTR=%.3f" % (
        sp["vocab"]["types"], sp["vocab"]["tokens"], sp["vocab"]["ttr"]))
    rep = sp["repetition"]
    L.append("  repetition: %d exact-dup lines (+%d extra), %d near-dups vs prev-3 lines" % (
        rep["exact_dups"], rep["dup_extra_occurrences"], rep["near_dups_vs_prev3"]))
    af = sp["affect"]
    L.append("  affect (heuristic): pos-tokens %d  neg-tokens %d  mean sentiment %.2f" % (
        af["pos_words"], af["neg_words"], af["mean_heuristic_sentiment"]))

    lex = sp["lexicon"]
    tot_lex = lex["empire"] + lex["soft"] + lex["both"] + lex["neither"]
    L.append("\n[DRIFT WATCH]  persona lexicon on %d lines" % tot_lex)
    L.append("  empire-flavoured: %d (%s)" % (lex["empire"] + lex["both"],
              pct_or_na(lex["empire"] + lex["both"], tot_lex)))
    L.append("  soft-flavoured:   %d (%s)" % (lex["soft"] + lex["both"],
              pct_or_na(lex["soft"] + lex["both"], tot_lex)))
    L.append("  neutral:          %d (%s)" % (lex["neither"],
              pct_or_na(lex["neither"], tot_lex)))
    drift = (lex["soft"] - lex["empire"]) / max(1, lex["soft"] + lex["empire"])
    if tot_lex and lex["soft"] + lex["empire"] >= 3:
        if lex["soft"] > 2 * max(1, lex["empire"]):
            L.append("  >>> DRIFT SIGNAL: soft/wellness lines dominate the empire persona")
        elif lex["empire"] > 2 * max(1, lex["soft"]):
            L.append("  >>> in-persona: empire framing dominant (score %.2f)" % drift)
        else:
            L.append("  >>> balanced: drift score %.2f" % drift)
    else:
        L.append("  (insufficient persona-lexicon lines for a drift verdict)")
    if lex["soft_lines"]:
        L.append("  sample soft lines:")
        for d, t in lex["soft_lines"][-3:]:
            L.append("    [%s] %s" % (d, t[:110]))
    if lex["empire_lines"]:
        L.append("  sample empire lines:")
        for d, t in lex["empire_lines"][-3:]:
            L.append("    [%s] %s" % (d, t[:110]))

    # --- actions
    L.append("\n[ACTIONS]  %d total" % ac["n"])
    if ac["per_operator"]:
        L.append("  by operator: " + ", ".join("%s=%d" % kv for kv in sorted(ac["per_operator"].items())))
    if ac["per_day"]:
        L.append("  by day:      " + ", ".join("%s=%d" % kv for kv in sorted(ac["per_day"].items())))
    L.append("  errors: %d (%s)" % (ac["errors"]["n"], ac["errors"]["rate"]))
    L.append("  tool table (n / errors / err%% / latency ms mean,p50,p95,max):")
    for tool, t in sorted(ac["tools"].items()):
        lm = t["lat_ms"]
        L.append("    %-28s %4d  err %-3d %6s  %6.0f %6.1f %6.1f %6.1f" % (
            tool, t["n"], t["errors"], t["err_rate"],
            lm["mean"] * 1000, lm["p50"] * 1000, lm["p95"] * 1000, lm["max"] * 1000))
    for e in ac["errors"]["samples"]:
        L.append("    ! [%s] %s/%s: %s" % (e["time"], e["operator"], e["tool"], e["msg"][:100]))

    # --- sightings
    L.append("\n[SIGHTINGS]  %d logs" % st["sightings"]["n"])
    for obj, c in st["sightings"]["objects"].items():
        L.append("    %-20s %d" % (obj, c))

    # --- mood
    L.append("\n[MOOD]")
    md = st["mood"]
    cur = md.get("current") or {}
    L.append("  current mood.json: valence %.2f  energy %.2f" % (
        cur.get("valence", float("nan")), cur.get("energy", float("nan"))))
    if md.get("series"):
        s = md["series"]
        L.append("  series (%d snapshots, %s -> %s):" % (s["n"], s["first"], s["last"]))
        if s["valence"]:
            L.append("    valence  mean %.3f  range [%.3f, %.3f]  latest %.3f" % (
                s["valence"]["mean"], s["valence"]["min"], s["valence"]["max"], s["valence"]["latest"]))
        if s["energy"]:
            L.append("    energy   mean %.3f  range [%.3f, %.3f]  latest %.3f" % (
                s["energy"]["mean"], s["energy"]["min"], s["energy"]["max"], s["energy"]["latest"]))
    else:
        L.append("  no mood time series yet - run with --snapshot to start one")
    L.append("\n" + "=" * 62)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Vector deterministic evaluation stats")
    ap.add_argument("--days", type=int, default=None,
                    help="window: last N days (by local date)")
    ap.add_argument("--since", default=None,
                    help="window: include entries with date >= YYYY-MM-DD")
    ap.add_argument("--snapshot", action="store_true",
                    help="append current mood.json + life_state.json to the mood history series")
    ap.add_argument("--out", default=OUT, help="output dir (default ./evaluation)")
    args = ap.parse_args()

    today = datetime.date.today().isoformat()
    since = args.since
    if since is None and args.days is not None:
        since = (datetime.date.today() - datetime.timedelta(days=args.days - 1)).isoformat()
    args.since = since  # None = full history, no date filter

    os.makedirs(args.out, exist_ok=True)

    if args.snapshot:
        life = load_json(os.path.join(MEM, "life_state.json"))
        mood = load_json(os.path.join(MEM, "mood.json"))
        snap = {"ts": datetime.datetime.now().timestamp(),
                "time_str": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "valence": mood.get("valence"), "energy": mood.get("energy"),
                "last_speak": life.get("last_speak"), "last_hum": life.get("last_hum"),
                "last_scan": life.get("last_scan"), "recent_lines_n": len(life.get("recent_lines", [])),
                "on_charger": None}
        with open(os.path.join(args.out, "mood_history.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(snap) + "\n")
        print("snapshot appended: %s" % snap["time_str"])

    st = build_report(args)
    text = render_text(st)
    print(text)

    # persist
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(os.path.join(args.out, "report_%s.txt" % stamp), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    with open(os.path.join(args.out, "report_%s.json" % stamp), "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=2)
    with open(os.path.join(args.out, "latest_report.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    with open(os.path.join(args.out, "latest_report.json"), "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=2)
    print("\nfiles written to %s (report_<stamp>.txt/.json, latest_report.*)" % args.out)


if __name__ == "__main__":
    main()
