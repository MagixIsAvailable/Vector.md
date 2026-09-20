#!/usr/bin/env python3
"""Daily news digest for Vector + TV dashboard.

Fetches a handful of headlines (world / tech / space), writes a compact
markdown digest to the brain's news folder (vault_sync staging, read by
brain_proxy context + dashboard /api/news) AND to the real synced vault
(desktop visibility via Syncthing).

Deliberately small (<=5 items, one line each): this codebase learned a 3B
model attends worse to long blocks. Run daily via a scheduled cron (08:30) and
by hand whenever a fresh digest is wanted.

Stdlib only. Never raises hard on a feed: sources that fail are skipped;
if every source fails the script exits 2 without touching existing digests
(the brain/dashboard freshness gate keeps using the last good one).
"""
from __future__ import annotations

import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

UA = {"User-Agent": "Mozilla/5.0 (Vector-imperial-news-digest/1.0)"}
MAX_ITEMS = 5
FRESH_H = 26  # digest considered stale after this (older = brain drops it)

# (label, url, max_from_feed) — order matters, world first
FEEDS = [
    ("World", "http://feeds.bbci.co.uk/news/world/rss.xml", 2),
    ("Tech", "http://feeds.bbci.co.uk/news/technology/rss.xml", 2),
    ("Space", "https://www.space.com/feeds/all", 1),
]

HOME = Path.home()
OUT_DIRS = [
    HOME / "vector-mcp" / "vault_sync" / "Vector Mind" / "news",   # brain reads this
    HOME / "obsidian-vault" / "Vector Mind" / "news",              # synced to desktop
]

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _fetch(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        print(f"  skip {url}: {e}", flush=True)
        return None


def _clean(s: str, limit: int = 140) -> str:
    s = _WS.sub(" ", _TAG.sub(" ", s or "")).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _items(feed: tuple) -> list[tuple[str, str]]:
    label, url, n = feed
    body = _fetch(url)
    if not body:
        return []
    try:
        root = ET.fromstring(body)
        out = []
        seen = set()
        for it in root.iter("item"):
            title = _clean(it.findtext("title") or "", 110)
            if not title or title in seen:
                continue
            seen.add(title)
            desc = _clean(it.findtext("description") or "", 140)
            out.append((label, title, desc))
            if len(out) >= n:
                break
        return out
    except Exception as e:  # noqa: BLE001
        print(f"  parse {url}: {e}", flush=True)
        return []


def main() -> int:
    today = datetime.now(ZoneInfo("Europe/London")).date().isoformat()
    picked: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for feed in FEEDS:
        for label, title, desc in _items(feed):
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            picked.append((label, title, desc))
            if len(picked) >= MAX_ITEMS:
                break
        if len(picked) >= MAX_ITEMS:
            break
    if not picked:
        print(f"{today}: no news from any source — leaving digests untouched",
              flush=True)
        return 2

    lines = [f"# News {today}", ""]
    lines += [f"- [{label}] {title} — {desc}" if desc
              else f"- [{label}] {title}"
              for label, title, desc in picked]
    text = "\n".join(lines) + "\n"
    wrote = 0
    for d in OUT_DIRS:
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{today}.md").write_text(text, encoding="utf-8")
            wrote += 1
            print(f"wrote {d / (today + '.md')}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  write failed {d}: {e}", flush=True)
    print(f"{today}: {len(picked)} items", flush=True)
    return 0 if wrote else 3


if __name__ == "__main__":
    sys.exit(main())
