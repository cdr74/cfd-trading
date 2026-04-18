# Market Scan — Ranking Criteria

You have been given price and market data for a list of instruments. Your task is to rank them by trading opportunity quality and present your findings clearly so the human can select one for deeper analysis.

---

## What to Evaluate

For each instrument, assess:

**1. Trend clarity**
- Is the 1-min trend slope consistent over the last 20–30 bars?
- Are higher highs / higher lows (uptrend) or lower highs / lower lows (downtrend) visible?
- Is the instrument ranging or trending? Ranging instruments are lower priority unless a mean reversion strategy is active.

**2. Volatility (ATR)**
- Is ATR sufficient to make the trade worthwhile after spread cost?
- Rule of thumb: ATR should be at least 3× the spread. Flag instruments where this is not met.

**3. Spread cost**
- Express spread as a percentage of ATR. A spread above 30% of ATR is a warning sign.

**4. Sentiment alignment**
- Does client sentiment lean in the direction of the potential trade?
- Contrarian signal: if >75% of clients are long and you are considering a LONG, note this as a warning.

**5. Session alignment**
- Is this instrument active in the current trading session (London / New York / Asia)?
- Low-volume instruments outside their primary session have unreliable data.

---

## Output Format

Present a ranked list. For each instrument include:

- **Rank and epic** — e.g. `1. EURUSD`
- **Direction bias** — LONG / SHORT / NEUTRAL
- **Why it ranks here** — two or three sentences covering trend, ATR, and spread
- **Watch for** — one sentence on the main risk or caveat
- **Spread/ATR ratio** — the number, flagged if > 30%

End with a recommendation: which instrument to analyse first and which strategy type fits it (momentum or mean reversion).

Do not produce a trade proposal at this stage. The scan is a shortlist, not a decision.
