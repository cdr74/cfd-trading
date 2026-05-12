# Momentum Strategy

**Style:** Trend-following. Enter in the direction of an established move; ride it with a trailing stop.

---

## When to Apply This Strategy

Apply momentum when:
- A clear directional trend is visible on the 1-min chart over at least 15–20 bars
- EMA_9 has crossed above (LONG) or below (SHORT) EMA_21 and the gap is widening
- ATR is expanding (volatility increasing into the move, not decreasing)
- The breakout is accompanied by a sentiment lean in the trade direction (not contrarian)
- Spread/ATR ratio is below 20% — momentum trades need room to run

Do not apply momentum when:
- Price is oscillating around a mean with no clear direction
- ATR is contracting (move may be exhausting)
- The move has already extended significantly without a pullback — late entry risk

---

## Entry Logic

- **Entry type:** MARKET on confirmed breakout, or LIMIT on a pullback to the breakout level
- **Direction:** Always in the direction of the trend. Never counter-trend with this strategy.
- **Size:** Start at the lower end of the allowed range. Scale only if the trade is profitable and the trend remains intact.

---

## Stop Loss

- Place the initial stop beyond the most recent significant swing low (LONG) or swing high (SHORT)
- Express as a percentage from entry — must be within strategy YAML `max_pct`
- **Hard stop is always set.** Even if trailing stop is enabled, the hard stop defines the maximum tolerable loss.

---

## Trailing Stop

- Enable the trailing stop once price has moved at least `min_distance_pct` in the profitable direction
- The stop ratchets in the profitable direction only — it never widens
- Distance: set to `initial_distance_pct` from the strategy config — wide enough to survive normal pullbacks within the trend

---

## Take Profit

- Set an initial take profit at a significant resistance level (LONG) or support level (SHORT)
- Minimum R:R ratio as defined in strategy YAML — do not propose a trade that does not meet this
- The take profit is fixed at entry in v1. Do not propose dynamic adjustment.

---

## Reasoning Guidance

In your `signal_basis`, describe:
- What confirms the trend (e.g. slope of last N bars, ATR expansion, higher highs)
- Whether EMA_9 is above or below EMA_21 and whether the gap is widening or narrowing
- Where the breakout level is and whether price has cleared it convincingly

In your `contra_indicators`, address:
- Is the move overextended relative to recent ATR?
- Is sentiment contrarian (crowd positioned against your trade direction)?
- Are there visible resistance levels (LONG) or support levels (SHORT) close to entry that could cap the move?
