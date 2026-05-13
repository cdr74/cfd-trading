# Opening Range Breakout (ORB) Strategy

**Style:** Session breakout. The first 15 minutes of the primary session define a range; trade the breakout above or below it.

**Research basis:** Zarattini & Aziz (2024) — Sharpe > 1.5 on US equity index futures. Structural edge: the opening bar captures order flow imbalance at session start; the subsequent breakout signals institutional direction for the session.

---

## When to Apply This Strategy

Apply ORB when:
- It is within the first 3 hours after the primary session open for this instrument
- Price has broken cleanly above the session high (LONG) or below the session low (SHORT) of the first 15-min bar
- The breakout bar closes beyond the OR level (confirmation — not just a wick)
- Volume on the breakout bar is above recent average (momentum behind the move)
- Spread/ATR ratio is below 15% — breakout trades need clear momentum to overcome cost

Do not apply ORB when:
- It is more than 3 hours after the session open — the structural edge decays significantly
- Price is oscillating around the OR boundaries (false breakout environment)
- A major economic release is scheduled within 30 minutes (pre-release noise contaminates the OR)

---

## Entry Logic

- **Entry type:** MARKET on confirmed break above OR high (LONG) or below OR low (SHORT)
- **One trade per session** — once the first breakout direction is committed, do not reverse or add in the opposite direction
- **Direction:** Strictly in the breakout direction. No counter-trend ORB trades.

---

## Stop Loss

- Ideal: place stop at the opposite side of the Opening Range (OR low for LONG, OR high for SHORT)
- The OR width is your natural risk unit — if OR width exceeds `max_pct` from YAML, skip the trade
- Express as a percentage from entry within YAML `max_pct: 2.0%`

---

## Take Profit

- Initial target: 2× the OR width from entry (min R:R 2.0 per YAML)
- Common ORB targets: previous session high/low, round numbers, key ATR extensions
- Allow trailing stop to run the trade once price exceeds the initial TP level

---

## Reasoning Guidance

In your `signal_basis`, describe:
- What the Opening Range was (OR high, OR low, width in points)
- Whether the breakout bar closed beyond the OR level or only wicked through
- Volume context on the breakout bar

In your `contra_indicators`, address:
- Is the breakout happening more than 2 hours after the open? (edge decay)
- Are there key resistance levels (LONG) or support levels (SHORT) immediately above/below entry?
- Is the OR unusually wide (large gap open) — wide ORs produce lower-quality breakouts
