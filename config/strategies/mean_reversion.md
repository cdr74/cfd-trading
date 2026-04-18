# Mean Reversion Strategy

**Style:** Range-bound. Enter against an overextended move, targeting a return to the mean.

---

## When to Apply This Strategy

Apply mean reversion when:
- Price has moved sharply away from its recent average (last 20–30 bars) and shows signs of stalling or reversing
- ATR is normal or contracting — the move is losing momentum
- Sentiment is heavily skewed in the direction of the move (>65% of clients positioned with the trend) — this is a contrarian signal in your favour
- The instrument is ranging overall with identifiable support and resistance levels

Do not apply mean reversion when:
- A strong, expanding trend is in place — fading a genuine trend is high risk
- ATR is expanding into the move — momentum is increasing, not exhausting
- The overextension is driven by a news event or data release — fundamentals may sustain the move

---

## Entry Logic

- **Entry type:** LIMIT at or near the overextended extreme, or MARKET if a reversal candle has already formed
- **Direction:** Counter to the recent sharp move — LONG if price has dropped sharply, SHORT if it has spiked sharply
- **Confirmation:** Look for at least one reversal signal — stall candle, wick rejection, or sentiment extreme — before entering

---

## Stop Loss

- Place the stop beyond the extreme of the overextended move — if fading a spike high, stop goes above that high
- Express as a percentage from entry — must be within strategy YAML `max_pct`
- This strategy uses a **hard stop only** — trailing stop is disabled in the strategy config

---

## Trailing Stop

Trailing stop is disabled for mean reversion. The trade has a fixed target and a fixed stop — do not propose a trailing stop for this strategy.

---

## Take Profit

- Target the mean (recent 20-bar average) or the nearest significant support/resistance level on the other side
- Minimum R:R ratio as defined in strategy YAML — mean reversion requires a tighter stop and a clear target
- The take profit is fixed at entry. Do not propose dynamic adjustment.

---

## Reasoning Guidance

In your `signal_basis`, describe:
- How far price has moved from its recent mean and over how many bars
- What reversal signal, if any, is visible (stall, wick, sentiment extreme)
- Where the natural mean or target level sits

In your `contra_indicators`, address:
- Could this be the start of a genuine trend rather than an overextension?
- What is the news/event risk? Is there a catalyst that might sustain the move?
- Is the spread wide enough to eat significantly into the expected mean reversion distance?
