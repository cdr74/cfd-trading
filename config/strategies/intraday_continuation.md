# Volatility-Band Intraday Continuation (D3/BR3) Strategy

> **STATUS: KILLED 2026-05-21.** This strategy failed its pre-registered
> OOS gates (DSR P=0.000, 95% CI straddles 0, net per-trade below the 3×
> cost hurdle). Full verdict + run artefacts: `docs/STRATEGY_AUDIT.md`
> Part 2 → "Task #7 — Run + verdict". **Do not deploy. Do not propose
> trades against this strategy.** The YAML + this MD are retained as
> historical record; the implementation was correct (the kill is on
> OOS performance, not a code defect). The trail-mode dispatch
> infrastructure (`trailing_stop.mode: dynamic_chandelier`, `current_atr`
> plumbing) is reusable by future strategies.


**Style:** Pooled intraday momentum continuation across US500, DE40, UK100 — Zarattini-inspired, generalised to M15 retail CFDs (not a replication).

**Research basis:** Zarattini, Barbon & Aziz (2024) — *Beat the Market: An Effective Intraday Momentum Strategy*. SPY 2007–2024, Sharpe 1.33. Structural edge: a session-open volatility band rejection predicts continuation toward the daily close. **BR-caveat:** the cited result is SPY (futures/ETF) at 1–5 min and was never re-costed for retail CFD — our test is M15 on retail-CFD indices, *inspired by* not a replication of the paper. Must clear the STRATEGY_AUDIT D1 cost anchor + Deflated-Sharpe / min-sample.

---

## When to Apply This Strategy

Apply intraday_continuation when:
- The instrument is one of: **US500, DE40, UK100** (pooled — evaluated as ONE strategy, never per-cell)
- It is during the primary session for the instrument (US500: 14:30 UTC; DE40/UK100: 08:00 UTC)
- A session bar has already closed outside `session_open ± 1.0·ATR₁₄` — the breach direction is the entry direction
- Round-trip cost ≤ ~0.75 bps (Index D1 anchor — below this the trade has a chance to clear the 3× hurdle)

Do not apply intraday_continuation when:
- The instrument is anything other than US500/DE40/UK100 (the spec is pooled-indices only)
- The session has already produced an entry today — at most one entry per session per instrument
- A major economic release is scheduled within 15 minutes (band breach may be release noise, not directional flow)
- It is after the daily 21:00 UTC session-close window — the time-exit is about to flatten

---

## Entry Logic

- **Entry type:** MARKET at the next M15 bar's open, immediately after a session bar closes outside the band `open ± 1.0·ATR₁₄(closed bars)`
- **Direction:** Strictly in the breach direction — long on upper breach, short on lower breach
- **One trade per session per instrument** — the first breach direction wins; no reversals, no scaling, no second entries

---

## Stop Loss

- Initial hard stop placed at the **dynamic Chandelier level at entry**: `entry ∓ 1.5·ATR₁₄(entry)`
- The dynamic Chandelier trail then manages it from there — per-bar recomputed, peak-anchored, MAY loosen on volatility expansion (literature-faithful, distinct from momentum's ratchet-only fixed-ATR trail)
- `default_pct: 5.0%` in YAML is a structural backstop; the Chandelier level is always tighter

---

## Take Profit

- **None.** "Hold to close" is the literature-faithful design — exits are governed entirely by:
  1. Hard stop (initial Chandelier level)
  2. Dynamic Chandelier trail (each completed bar)
  3. Time-exit at 21:00 UTC session close (30 min buffer)

There is no profit target. Do not propose one.

---

## Reasoning Guidance

In your `signal_basis`, describe:
- The session-open price, the ATR₁₄ at the time of breach, and the band width
- Which bar closed outside the band (timestamp + close vs band)
- The current ATR — it sets the initial stop distance via Chandelier
- The instrument's contribution to the pooled sample (this is one of 3 indices)

In your `contra_indicators`, address:
- Is the breach happening within 15 min of a scheduled release? (noise contamination)
- Is the ATR unusually compressed or expanded vs the trailing 5-session median? (regime risk)
- Is the breach close marginal (just past the band) or decisive (>1.5× band width)? Marginal closes are weaker continuation signals

In your `risk_check`, confirm:
- The strategy is being applied to US500, DE40, or UK100 only (any other epic = wrong strategy)
- The session is the instrument's primary session window
- No existing intraday_continuation position is open for this instrument today
