# D2 — News-proximity drift: data acquisition (ForexFactory)

Working directory for the **D2** strategy thread's calendar data. Authoritative
spec + kill protocol: **`docs/STRATEGY_AUDIT.md` Part 2 → "D2 — News-proximity
drift: pre-registration"**. This README tracks only the data-acquisition
sub-task; do not duplicate the pre-registration here.

## Status — D2 KILLED 2026-05-26 (no validated edge)

Full build ran clean through all 4 steps; **strategy fails all gates by
structural margin** across the {1,3,5 h} grid. Authoritative verdict +
rationale: `docs/STRATEGY_AUDIT.md` Part 2 → "D2 — Run + verdict". Numeric run
record: `d2_run_2026-05-26.md`. Pipeline (all retained as reusable event-driven
infrastructure):

| step | script | output |
|---|---|---|
| TZ gate | `tz_gate.py` | PASSED (5 NFP anchors, both DST states) |
| scrape | `scrape_calendar.py` | `d2_news_events.parquet` (1966 events) |
| standardise | `build_dataset.py` | `d2_signals.parquet` (1136 candidates) |
| backtest | `run_backtest.py` | `d2_run_2026-05-26.parquet` (918 trades/horizon) |
| gates | `d2_analyze.py` | reuses `audit/d3_analyze.py`; FAIL all horizons |

OOS: SR̂ −0.038…−0.028, DSR P≈0, best net +0.55 bps vs ~2.68 hurdle, CI lower
negative everywhere. Construction verified clean (no bug; fade also negative).
`week_*.html` git-ignored.

## Recon (2026-05-22)

A single-week reconnaissance fetch was run to characterise access, data shape,
and timezone **before** committing to a full scraper. All four recon questions
answered:

1. **Access — cloudscraper, no browser driver.** Plain `requests` is
   Cloudflare-blocked (HTTP 403, "Just a moment" managed challenge).
   `cloudscraper` clears it and returns the full page (HTTP 200, ~450 KB).
   Playwright is therefore **not** needed (the agreed escalation rung was not
   reached). `cloudscraper` will need adding to project deps when the scraper
   module lands.
2. **Data shape.** Events are embedded in a JS assignment
   `calendarComponentStates[1] = { days: [ … ] }`. The outer wrapper has
   unquoted keys, but the `days` value is **valid JSON** — extract it by
   bracket-balancing from `days:` and `json.loads` it. Each event carries
   `name`, `ebaseId`, `currency`, `impactName` (`high`/`medium`/`low`),
   `dateline`, `actual`, `forecast`, `previous`.
3. **Timezone — solved at the source.** `dateline` is a **raw UTC epoch**, not
   a display-formatted time, so there is no FF display-timezone to misparse.
   This is what makes the pre-registration's first kill checkpoint passable.
4. **Values + bonus.** `actual`/`forecast`/`previous` are **unit-strings**
   (`'177K'`, `'0.2%'`, `'4.50%'`) → the build needs a units→float parser.
   **Rate decisions carry a `forecast`** (e.g. `Federal Funds Rate`
   F=`4.50%`) — the exact gap that disqualified the MT5 built-in calendar, so
   rate-decision surprise is usable here.

### Timezone gate — PASSED (2026-05-26)

The pre-registered first kill checkpoint. `tz_gate.py` decodes each anchor's
`dateline` epoch → UTC → `America/New_York` and asserts NFP lands on 08:30 ET
exactly — the definitive DST test (no hardcoded 12:30/13:30, so the season must
be carried by the data itself). 5 NFP anchors across the window, both DST
states:

| Week (NFP) | Season | epoch → UTC | → ET |
|------------|--------|-------------|------|
| jun2.2023  | EDT    | 12:30 UTC   | 08:30 ✓ |
| jan5.2024  | EST    | 13:30 UTC   | 08:30 ✓ |
| jul5.2024  | EDT    | 12:30 UTC   | 08:30 ✓ |
| may2.2025  | EDT    | 12:30 UTC   | 08:30 ✓ (recon) |
| jan9.2026  | EST    | 13:30 UTC   | 08:30 ✓ |

Both winter/EST weeks resolve to 13:30 UTC, both summer to 12:30 UTC → DST is
provably carried by the epoch in both directions. **Gate cleared; build
proceeds.** Re-run: `.venv/bin/python analysis/d2_news/tz_gate.py`.

## Next steps (in order; do not skip the gate)

1. ~~**Finish the timezone gate**~~ — DONE 2026-05-26, PASSED (see above).
2. ~~**Build the scraper**~~ — DONE 2026-05-26. `scrape_calendar.py` fetched all
   157 weeks → `d2_news_events.parquet`: **1966** high-impact + has-forecast
   events, full window (2023-05-15 → 2026-05-14), ~99% parsed (1941/1966).
   Mapped to the universe (USD/EUR/GBP/JPY) = **1356** (IS 821 / OOS 535 at the
   2025-01-21 boundary). JPY is thin (19 events). The 25 unparsed `actual`s are
   genuinely non-scalar — 24 BoE MPC vote-split strings (`0-0-9`) + 1 Fed
   nomination `Pass` — and must be **excluded** at standardisation (they carry a
   forecast string but no numeric surprise).
2b. **(original step 2 text, for reference)** — `cloudscraper`, weekly `?week=mmmd.yyyy` fetches
   (~157 weeks for 2023-05 → 2026-05), polite delay + skip-if-cached. Extract
   the `days` array, flatten events, filter to high-impact + has-forecast,
   parse unit-strings → float, write parquet here.
3. ~~**Surprise standardisation + pooled dataset**~~ — DONE 2026-05-26.
   `build_dataset.py` → `d2_signals.parquet`. Prior-only expanding sigma
   (warmup≥8, no look-ahead; user decision), |z|≥1.0 pinned. 970 warmup-eligible
   standardized events → **267 armed** (28%) → **1136 candidate (event,instrument)
   signals** (IS 538 / **OOS 598**). OOS ceiling 598 ≫ the ~100 pooled-OOS floor,
   so depth clears the precondition. Pool leans USD (101 candidates per USD-mapped
   instrument; DE40 thin at 11 OOS — EUR-only leg). Candidates are an upper bound:
   final N drops at engine step 4 (events lacking a valid reaction/entry bar).
4. **Wire onto the rebuilt engine** per the pre-registration — direction = sign
   of first post-release M15 bar, enter next bar, hold ~2–4 h (freeze count),
   shared deterministic exit + cost, parity test, then run vs the §6 gates.

## Files / conventions

- Raw scraped HTML (`*.html`) is **git-ignored** — bulky third-party content,
  regenerable. The recon cache `recon_week_may2.2025.html` is local-only.
- Built datasets will be parquet (same convention as `analysis/audit_archive/`).
