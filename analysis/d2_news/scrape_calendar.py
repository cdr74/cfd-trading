"""D2 ForexFactory calendar scraper (step 2 of data acquisition).

Authoritative spec: docs/STRATEGY_AUDIT.md Part 2 -> "D2 pre-registration".
Runs ONLY after the timezone gate passed (tz_gate.py, 2026-05-26).

Walks the pre-registration window 2023-05-15 -> 2026-05-14 one calendar week at
a time (~157 weeks), fetching each via cloudscraper (Cloudflare-cleared),
caching raw HTML locally (git-ignored), then flattening every
high-impact + has-forecast event into a single parquet table. Surprise
standardisation and the IS/OOS pooled dataset are the NEXT step (#3) — this
module only acquires and parses raw values.

Design notes:
- Fetch and parse are separate passes. The fetch loop is skip-if-cached, so an
  interrupted run resumes for free; the parse pass globs ALL cached *.html
  (incl. the gate + recon weeks) and dedups events by id.
- `extract_days()` is lifted verbatim from tz_gate.py (single source of truth
  would be nicer, but keeping these two scripts standalone avoids an analysis
  package import dance; if a third consumer appears, promote it to a module).
- `dateline` is a raw UTC epoch (gate-verified). We store the epoch AND a
  derived UTC timestamp; no display-timezone parsing anywhere.

Run:  pip install -e ".[analysis]"   # cloudscraper + pandas
      .venv/bin/python analysis/d2_news/scrape_calendar.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import cloudscraper
import pandas as pd

HERE = Path(__file__).parent
OUT_PARQUET = HERE / "d2_news_events.parquet"
BASE = "https://www.forexfactory.com/calendar?week={week}"

# Pre-registration window (STRATEGY_AUDIT.md Part 2 §2). Fetch every calendar
# week that overlaps it; the window/OOS filtering happens at dataset-build time.
WINDOW_START = date(2023, 5, 15)
WINDOW_END = date(2026, 5, 14)

POLITE_DELAY_S = 6.0


def week_params(start: date, end: date) -> list[str]:
    """One FF `?week=` param per calendar week, Monday-anchored, no overlap.

    FF normalises any in-week date, so the Monday of each week is a clean,
    collision-free key. Format mirrors the recon param `may2.2025`:
    lowercase 3-letter month + day (no leading zero) + `.year`.
    """
    monday = start - timedelta(days=start.weekday())  # Monday of the start week
    out = []
    d = monday
    while d <= end:
        out.append(f"{d.strftime('%b').lower()}{d.day}.{d.year}")
        d += timedelta(days=7)
    return out


def fetch_week(week: str, scraper) -> Path:
    """Fetch a week to its cache file, skip-if-cached. Returns the cache path."""
    cache = HERE / f"week_{week}.html"
    if cache.exists():
        return cache
    time.sleep(POLITE_DELAY_S)
    resp = scraper.get(BASE.format(week=week), timeout=30)
    resp.raise_for_status()
    cache.write_text(resp.text)
    return cache


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


_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}


def parse_value(raw) -> float | None:
    """FF unit-string -> float. '177K'->177000, '0.2%'->0.2, '4.50%'->4.5,
    '-0.8%'->-0.8, '1,234K'->1234000. Empty / non-numeric -> None.

    Percent signs are stripped and the bare number kept: surprise is
    standardised *within* each event (z-score over its own history), so the
    unit is consistent per series and the % is informational only.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "" or s.upper() in {"NA", "N/A", "NAN"}:
        return None
    neg = s.startswith("-")
    s = s.lstrip("+-").lstrip("<>~").strip()
    for ch in (",", "$", "€", "£", "¥", "%"):
        s = s.replace(ch, "")
    s = s.strip()
    mult = 1.0
    if s and s[-1].lower() in _SUFFIX:
        mult = _SUFFIX[s[-1].lower()]
        s = s[:-1]
    if s == "":
        return None
    try:
        val = float(s) * mult
    except ValueError:
        return None
    return -val if neg else val


def parse_all_cached() -> pd.DataFrame:
    """Parse every cached *.html into a deduped event table.

    Filter: impactName == 'high' AND a non-empty forecast (rate decisions
    included — they carry a forecast). Dedup by event id across overlapping
    week files (gate + recon weeks may double-cover a week).
    """
    rows: dict[int, dict] = {}
    for html_path in sorted(HERE.glob("*.html")):
        try:
            days = extract_days(html_path.read_text())
        except Exception as exc:
            print(f"  WARN: skip {html_path.name}: {exc}")
            continue
        for d in days:
            for e in d.get("events", []):
                if e.get("impactName") != "high":
                    continue
                fc_raw = e.get("forecast")
                if fc_raw is None or str(fc_raw).strip() == "":
                    continue
                eid = int(e["id"])
                ts = int(e["dateline"])
                rows[eid] = {
                    "id": eid,
                    "ebaseId": e.get("ebaseId"),
                    "name": e.get("name"),
                    "currency": e.get("currency"),
                    "country": e.get("country"),
                    "impact": e.get("impactName"),
                    "dateline": ts,
                    "datetime_utc": datetime.fromtimestamp(ts, timezone.utc),
                    "actual_raw": e.get("actual"),
                    "forecast_raw": fc_raw,
                    "previous_raw": e.get("previous"),
                    "actual": parse_value(e.get("actual")),
                    "forecast": parse_value(fc_raw),
                    "previous": parse_value(e.get("previous")),
                }
    df = pd.DataFrame(sorted(rows.values(), key=lambda r: r["dateline"]))
    return df


def main() -> int:
    weeks = week_params(WINDOW_START, WINDOW_END)
    print(f"D2 scraper — {len(weeks)} calendar weeks, {WINDOW_START} -> {WINDOW_END}")
    scraper = cloudscraper.create_scraper()
    fetched = cached = 0
    for i, wk in enumerate(weeks, 1):
        existed = (HERE / f"week_{wk}.html").exists()
        try:
            fetch_week(wk, scraper)
        except Exception as exc:
            print(f"  [{i}/{len(weeks)}] {wk}: FETCH ERROR {exc} — aborting (resume re-runs from cache)")
            return 2
        if existed:
            cached += 1
        else:
            fetched += 1
        if i % 10 == 0 or i == len(weeks):
            print(f"  [{i}/{len(weeks)}] fetched={fetched} cached={cached}")
    print(f"fetch done: {fetched} new, {cached} from cache")

    print("parsing all cached weeks -> events table ...")
    df = parse_all_cached()
    df.to_parquet(OUT_PARQUET, index=False)
    print(f"wrote {OUT_PARQUET.name}: {len(df)} high-impact+has-forecast events")
    in_win = df[(df.datetime_utc.dt.date >= WINDOW_START) & (df.datetime_utc.dt.date <= WINDOW_END)]
    print(f"  in pre-reg window: {len(in_win)}")
    print(f"  by currency: {df.currency.value_counts().to_dict()}")
    n_actual = df.actual.notna().sum()
    print(f"  with parsed actual: {n_actual}/{len(df)} ; with parsed forecast: {df.forecast.notna().sum()}/{len(df)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
