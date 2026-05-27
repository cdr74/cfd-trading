"""D2 timezone-validation gate (pre-registered first kill checkpoint).

Authoritative spec: docs/STRATEGY_AUDIT.md Part 2 -> "D2 pre-registration".

Purpose: confirm the ForexFactory `dateline` (raw UTC epoch) decodes to the
correct wall-clock US release time across the full window AND across a DST
boundary in BOTH directions. The definitive test is not "epoch == 12:30/13:30
UTC" (that bakes in the season) but: decode epoch -> UTC -> America/New_York
and assert it equals the event's known fixed local release time (08:30 ET for
NFP / CPI). If that holds in summer (EDT, -> 12:30 UTC) and winter (EST, ->
13:30 UTC), the epoch provably carries the season; DST is handled at source.

FAIL on any anchor = D2 is data-blocked, no scraper, no build.

Run:  .venv/bin/python analysis/d2_news/tz_gate.py
Network: cloudscraper (Cloudflare-cleared); weeks cached to *.html (git-ignored).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import cloudscraper

HERE = Path(__file__).parent
ET = ZoneInfo("America/New_York")
BASE = "https://www.forexfactory.com/calendar?week={week}"

# Anchor weeks: spread across the pre-registration window (2023-05 .. 2026-05),
# deliberately spanning BOTH DST states. `expect_local` is the known fixed
# Eastern wall-clock release time for the named event.
#   - NFP "Non-Farm Employment Change": 08:30 ET, first Friday.
# Winter/EST weeks (-> 13:30 UTC) are the new information this gate adds on top
# of the already-passed summer anchor (may2.2025 -> 12:30 UTC).
ANCHORS = [
    # week param,   event name substring,            expected local (ET), season
    ("jun2.2023",  "Non-Farm Employment Change", (8, 30), "EDT/summer"),
    ("jan5.2024",  "Non-Farm Employment Change", (8, 30), "EST/winter"),
    ("jul5.2024",  "Non-Farm Employment Change", (8, 30), "EDT/summer"),
    ("jan9.2026",  "Non-Farm Employment Change", (8, 30), "EST/winter"),
]

POLITE_DELAY_S = 6.0


def fetch_week(week: str, scraper) -> str:
    """Fetch a calendar week, skip-if-cached. Returns raw HTML."""
    cache = HERE / f"week_{week}.html"
    if cache.exists():
        return cache.read_text()
    url = BASE.format(week=week)
    time.sleep(POLITE_DELAY_S)
    resp = scraper.get(url, timeout=30)
    resp.raise_for_status()
    cache.write_text(resp.text)
    return resp.text


def extract_days(html: str) -> list[dict]:
    """Bracket-balance the `days:` array out of calendarComponentStates[1]."""
    m = re.search(r"calendarComponentStates\[1\]\s*=\s*\{", html)
    if not m:
        raise ValueError("calendarComponentStates[1] assignment not found")
    didx = html.index("days:", m.end() - 1)
    arr_start = html.index("[", didx)
    depth = 0
    for i in range(arr_start, len(html)):
        c = html[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return json.loads(html[arr_start : i + 1])
    raise ValueError("unterminated days array")


def find_anchor(days: list[dict], name_sub: str) -> dict | None:
    for d in days:
        for e in d.get("events", []):
            if name_sub in e.get("name", "") and e.get("currency") == "USD":
                return e
    return None


def main() -> int:
    scraper = cloudscraper.create_scraper()
    rows = []
    all_pass = True
    for week, name_sub, (eh, em), season in ANCHORS:
        try:
            html = fetch_week(week, scraper)
            days = extract_days(html)
        except Exception as exc:  # network / parse failure is itself a FAIL signal
            rows.append((week, season, "FETCH/PARSE ERROR", str(exc)[:60], False))
            all_pass = False
            continue
        ev = find_anchor(days, name_sub)
        if ev is None:
            rows.append((week, season, "anchor not in week", "—", False))
            all_pass = False
            continue
        ts = int(ev["dateline"])
        utc_dt = datetime.fromtimestamp(ts, timezone.utc)
        et_dt = utc_dt.astimezone(ET)
        ok = (et_dt.hour, et_dt.minute) == (eh, em)
        all_pass &= ok
        rows.append(
            (
                week,
                season,
                utc_dt.strftime("%Y-%m-%d %H:%M UTC"),
                f"{et_dt.strftime('%H:%M %Z')} (want {eh:02d}:{em:02d} ET)",
                ok,
            )
        )

    w = max(len(r[0]) for r in rows)
    print(f"\nD2 TIMEZONE GATE — anchor = NFP 08:30 ET (decode epoch, verify in America/New_York)\n")
    for week, season, utc_s, et_s, ok in rows:
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {week:<{w}}  {season:<11}  {utc_s:<20}  ->  {et_s}")
    print()
    # The already-recorded summer anchor (may2.2025 -> 12:30 UTC) is the 5th.
    print("  [PASS] may2.2025 (prior recon, on record: 2025-05-02 12:30 UTC = 08:30 EDT)")
    print()
    if all_pass:
        print("GATE PASSED — DST handled at source across both seasons. Cleared to build the scraper.")
        return 0
    print("GATE FAILED — D2 is data-blocked. Do NOT build the scraper.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
