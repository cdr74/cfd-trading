# Strategy Audit — Findings, Conclusion & Next-Phase Strategy Debate

**Date closed:** 2026-05-18
**Status:** Phase A audit CLOSED — kill-criterion triggered. Pivoting to a
fundamental strategy debate (Part 2).
**Supersedes:** the workspace-root `AUDIT_PLAN.md` and the `audit/` working
folder (both removed). This is the single git-managed record.
**Reproducibility:** scripts + dataset archived in
`cfd-trading/analysis/audit_archive/` (see its `README.md`).

---

## Part 1 — Audit Record (closed)

### 1. Context & method

Post-Phase-10 audit of three mechanical strategies (EMA-crossover momentum,
z-score mean-reversion, opening-range breakout) on retail Capital.com CFDs.

- **Engine rebuilt 2026-05-15** to share ONE deterministic exit path with the
  live monitor (hard stop → ATR trailing → TP → signal-exit → time-exit; global
  21:00 UTC session close; live↔backtest parity test). The prior audit was
  invalidated (engine never passed `session_end_time`; intraday time-exit never
  fired) and deleted.
- **Momentum trigger fixed**: the EMA-gap filter was evaluated at the crossover
  bar (EMAs ≈coincident) → 44 trades/3yr. Replaced with a pending-crossover +
  confirmation window at M30 → 1,770 trades.
- **Clean fidelity-true re-baseline:** 19,061 trades, 8 instruments × 3
  strategies, 2023-05-16 → 2026-05-14 (~3 yr); momentum M30, MR/ORB M15.
  Invariants verified (0 negative holds, 0 cross-UTC-day holds).
- **Fidelity discipline:** every reported number traces to the rebuilt engine;
  baseline re-strikes were checked to reproduce the authoritative attribution
  before any slice was trusted.

### 2. Re-baseline headline (mechanical floor)

| strategy | n | win% | net (pts) |
|---|--:|--:|--:|
| mean_reversion | 11,745 | 61.9 | **−6,883** |
| momentum | 1,770 | 39.3 | **−987** |
| orb | 5,546 | 36.9 | **+2,738** (only positive; index-concentrated) |

FX nets ≈ 0 in price points (sub-pip) — FX read via win%/expectancy, not points.

### 3. Mean-reversion — Option-1 program → DROPPED

**Loss attribution (fidelity-true):** the z-midline signal exit is the *profit
center* — 10,762 trades, **+10,475 pts**. 100% of MR's loss is two tails:
**76 index hard-stops (−13,247)** + **899 never-reverted time/session exits
(−7,361)**. Two hypothesised levers were falsified: a z-extension invalidation
exit (z mean-reverts as price runs — only 5.4% of losers reach z-ext ≥1.0) and
a "+4,260 persistence lower bound" (reproduced at only +531 in-harness; MR
losers are tiny, loss-R p50 −0.077).

**Option-1 repair program (read-only, non-overfit, structurally motivated):**

| step | change | MR net |
|---|---|--:|
| baseline (all 8) | — | −6,883 |
| 1 | drop GOLD → FX+indices universe | −5,213 |
| 2 | global ATR₁₄@entry×2.5 stop-cap (no per-instrument fit — DE40's 23-trade tail ruled statistically irrelevant) | −2,180 |
| 3 | drop NY-session entries (never-reverters 72.7% NY vs 7.0% of winners; 10.4× lift) | **−1,269** |

82% of the loss removed — but MR stays net-negative, far from the 3×
expectancy-to-cost gate.

**Literature gate (decisive, confirmatory):** (i) consensus is indices
mean-revert, **FX trends** — the "MR for FX" prior is wrong; (ii) MR works at
<30-min or >2-yr horizons; the 1h–2yr band (our M15 ~1–2 h holds) is *trend*
territory (Carver); (iii) intraday short-term reversal survives costs **only**
with large-cap + institutional **passive-fill** execution and has decayed to
≈0 recently (Blitz 2024) — we are retail, spread-taking, market-order: the
opposite regime; (iv) the residual edge sits in exactly the small-sample,
multiple-tested cells the Deflated-Sharpe theorem warns about.

**VERDICT: mean_reversion is non-viable — DROPPED.** The literature *confirms*
(does not rescue) the kill-criterion.

### 4. Standing methodological gate — Deflated-Sharpe / min-sample

Adopted 2026-05-18 (Bailey & López de Prado). No cell is treated as an edge on
raw net P&L: deflate for multiple testing (~100+ instrument×session×strategy×
param trials), treat any **< ~100-trade cell as unproven**, require the 3×
cost gate and ideally out-of-sample/live (MTRL). Applies to every strategy
including survivors.

### 5. ORB — DSR re-evaluation → NOT a validated edge

ORB indices-only: n=1,687, **+3,019** — but **69% is one cell, DE40-London**
(n=720, +2,074); top 3 cells = 89%; 3 of 6 positive cells are <100 trades.

Method: Deflated Sharpe N=120 (selection-adjusted benchmark SR₀=0.282 from the
empirical cross-cell Sharpe dispersion) + 10k bootstrap + OOS time-split.

| ≥100-trade cell | per-trade SR | DSR P(SR>SR₀) | bootstrap 95% CI | 3× cost |
|---|--:|--:|---|---|
| DE40-London | 0.028 | 0.000 FAIL | [−4.79, +10.55] straddles 0 | 3.88 PASS |
| UK100-London | 0.037 | 0.000 FAIL | [−4.21, +7.41] straddles 0 | 2.59 FAIL |
| US500-Overlap | 0.006 | 0.000 FAIL | [−1.69, +2.10] straddles 0 | 1.31 FAIL |

OOS split (DE40-London, sole cost-gate survivor): train +3.36/trade (SR 0.052)
→ holdout +2.34 (**SR 0.018**), ~50–65% decay. UK100-London has zero train
trades (all data 2025+ — un-OOS-testable). **ORB's +2,738 is a selection
artifact dominated by one regime-fragile cell — not a validated edge.**

### 6. Momentum — status

−987 on a now-working trigger (74% hard-stop exits). An edge/cost problem, not
an exit-geometry one; never independently rescued. **Unvalidated.**

### 7. Conclusion — Phase A kill-criterion TRIGGERED

MR dropped, momentum net-negative, ORB unvalidated → **no validated positive
cell at any cost level across all three strategies.** Per the pre-accepted
kill-criterion: stop pushing traditional mechanical strategies on this
retail-CFD environment; do **not** proceed to timeframe reformulation
(former Phase C) on the assumption it alone fixes this. Revisit whether
traditional retail-CFD mechanical edge is winnable here at all → Part 2.

### 8. Transferable learnings

- **Cost/execution regime is decisive.** Retail-CFD fixed spread tax +
  market-order/spread-taking + intraday horizon is structurally hostile to the
  z-MR / EMA-crossover / ORB family as implemented. Small intraday edges are
  eaten by friction (the recurring audit theme, now quantified).
- **Literature steer.** Index intraday *continuation from the open*,
  volatility-conditioned, time-exited at the close, is the best-evidenced
  family (Lundström ORB; Zarattini/Barbon/Aziz SPY intraday momentum; Carver
  horizons) — but its documented profitability assumes institutional
  passive-fill / low-cost conditions we do not have. Directional only, never
  re-costed for retail CFD.
- **Method.** Shared deterministic exit path (live↔backtest parity);
  fidelity-true re-strikes; Deflated-Sharpe + min-sample + OOS; honest,
  pre-accepted kill-criteria. Keep these for any future strategy work.

---

## Part 2 — Strategy Debate (NEXT PHASE — open agenda)

The pivot. No new strategy is coded before this debate concludes (discuss
before implementing). Threads:

- **D1 — Is a traditional mechanical edge winnable on retail CFD at all?**
  Decide explicitly: accept that the cost/execution environment is the binding
  constraint, and what (if anything) changes the premise (different broker /
  instrument class / non-CFD venue / passive-fill capability).
- **D2 — News-proximity / event-driven (was audit A4).** Post-news drift as a
  distinct strategy class: ForexFactory historical calendar, timezone
  validation, surprise-threshold entries, hours-not-seconds horizon. Was
  deferred as low-priority for the dropped strategies; now a first-class
  debate topic.
- **D3 — Literature-led direction.** Volatility-conditioned index intraday
  continuation; feasibility under our costs; the research backlog (ORB
  volatility-regime conditioning; ORB stop-design cross-check; a Zarattini-
  style intraday-momentum variant on indices; dynamic vs fixed-at-entry
  trailing). All carry the BR-caveat: futures/ETF results, not re-costed for
  retail CFD.
- **D4 — Claude co-pilot, re-scoped (former Phase E).** With no validated
  mechanical base, redefine what signals a co-pilot would review and whether
  that role is still meaningful.
- **D5 — AI/ML & sub-30-min/multi-year horizons:** explicitly out unless D1
  changes the premise (M1/M5 primary TF and LLM-as-signal were already
  rejected; Carver's viable MR bands need passive-fill or multi-year holds).

---

## Reproducibility

`cfd-trading/analysis/audit_archive/` holds the regenerator scripts
(`a2_slicing.py`, `a3b_zexit_sim.py`, `a3b_stopcap_sim.py`,
`build_trade_inspection_nb.py`), the trade dataset (`trades_M15.parquet`,
19,061 trades) and the sweep CSVs — enough to re-derive every number above.
Heatmaps and the 150-chart inspection notebook were deleted (large, derivable
from the parquet via the archived scripts). See the archive `README.md` for
the exact re-run command.
