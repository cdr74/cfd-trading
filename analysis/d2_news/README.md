# D2 — News-proximity drift: data acquisition (ForexFactory)

Working directory for the **D2** strategy thread's calendar data. Authoritative
spec + kill protocol: **`docs/STRATEGY_AUDIT.md` Part 2 → "D2 — News-proximity
drift: pre-registration"**. This README tracks only the data-acquisition
sub-task; do not duplicate the pre-registration here.

## Status — recon complete, scraper not yet built (2026-05-22)

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

### Timezone gate — first anchor PASSED

`Non-Farm Employment Change` for the week of 2025-05-02 decoded to
**2025-05-02 12:30:00 UTC** — exactly correct (8:30 ET in May = EDT/UTC-4 =
12:30 UTC). The full gate requires ≥5 anchors across the window incl. a
winter/EST week (NFP should then be 13:30 UTC) to confirm DST is handled by the
epoch. **Not yet complete.**

## Next steps (in order; do not skip the gate)

1. **Finish the timezone gate** — fetch ~4 more spread-out weeks (incl. one
   winter/EST week), confirm each anchor decodes correctly. This is the
   pre-registered first kill checkpoint; failure = D2 data-blocked, no build.
2. **Build the scraper** — `cloudscraper`, weekly `?week=mmmd.yyyy` fetches
   (~157 weeks for 2023-05 → 2026-05), polite delay + skip-if-cached. Extract
   the `days` array, flatten events, filter to high-impact + has-forecast,
   parse unit-strings → float, write parquet here.
3. **Surprise standardisation + pooled dataset**, then wire onto the rebuilt
   engine per the pre-registration.

## Files / conventions

- Raw scraped HTML (`*.html`) is **git-ignored** — bulky third-party content,
  regenerable. The recon cache `recon_week_may2.2025.html` is local-only.
- Built datasets will be parquet (same convention as `analysis/audit_archive/`).
