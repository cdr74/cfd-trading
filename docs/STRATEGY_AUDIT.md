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

### D1 anchor — the cost reality (quantified)

The "is it winnable" question is not qualitative. From the 19,061-trade
re-baseline (`analysis/audit_archive/trades_M15.parquet`, `spread_at_entry`),
per trade, in **bps of price**:

| class | round-trip cost | 3× viability hurdle | observed net/trade | gap to hurdle |
|---|--:|--:|--:|--:|
| Index (DE40/US500/UK100) | 0.75 | **2.26** | **−0.29** | ~2.5 short |
| FX (EUR/GBP/USD/JPY) | 0.83 | **2.50** | **−0.82** | ~3.3 short |
| Commodity (GOLD) | 1.28 | **3.84** | **−2.73** | ~6.6 short |

Per instrument (cost / 3×-hurdle / observed-net, bps): DE40 0.49 / 1.47 / −0.40
· US500 0.89 / 2.66 / −0.21 · UK100 1.02 / 3.05 / −0.30 · USDJPY 0.67 / 2.00 /
−0.23 · GBPUSD 0.77 / 2.32 / −0.85 · EURUSD 0.90 / 2.70 / −1.03 · EURGBP 1.17 /
3.51 / −1.51 · GOLD 1.16 / 3.49 / −2.85.

**Read:** to be deploy-viable a strategy must net **≥ ~2.3–3.8 bps per trade
after cost** (the Phase A3 3× gate). The observed mechanical net was **negative
in every class** — short of the bar by ~2.5–6.6 bps/trade. This is a
structural gap, not a near miss. **D1 decision rule:** any forward proposal
must demonstrate, *post-cost and post-deflation* (DSR + min-sample, §4), a
per-trade edge clearing this hurdle for its instrument class — or it changes
the premise (venue/cost/passive-fill) per D1. Lower-cost classes (indices) set
the lowest bar and are where any retail-CFD attempt is least hopeless.

### Carried-forward constraints & non-goals (guardrails — do not re-litigate)

Settled by prior decisions; restated here so the debate does not reopen them.
Each may only be revisited if D1 explicitly changes the premise.

- **Crypto CFDs — OUT.** Retail CFD crypto spread is 10–50× native exchange
  cost; the wrapper is pure friction.
- **M1/M5 as a primary signal timeframe — OUT.** Round-trip cost is fixed per
  trade; smaller bars compound the spread tax — the D1 anchor above is exactly
  why.
- **Tick charts / tick-volume / RVOL — OUT.** CFD tick volume is broker-
  internal, not exchange volume; not a real signal.
- **LLM as a raw-signal generator ("State Vector / Narrative Synthesis", the
  `New_Strat.md` pivot) — OUT.** Claude stays an interactive co-pilot over
  pre-computed signals (D4), never the signal source. No raw tick/OHLC fed to
  Claude; no NLP/sentiment scoring of news text in v1.
- **H4-only as a standalone primary timeframe — OUT** (too coarse; ~2 index
  bars/session, no mid-trade reactivity). Allowed only as an *additional*
  higher anchor above a finer trigger.
- **Walk-forward / Monte-Carlo robustness — NOT YET.** Premature until
  something clears DSR + min-sample + the 3× cost gate in-sample; do not
  robustness-test a non-edge.
- **No new strategy is coded before this debate concludes** (the
  discuss-before-implement guardrail; restated from the Part 2 preamble).
- **Audit-killed strategies are not silently revived.** mean_reversion, and
  the EMA-crossover / z-MR / ORB family *as implemented*, return only via an
  explicit new thesis that addresses the cost/horizon findings (§3, §8) — not
  a re-run with tweaked parameters.

### D3 detail — research backlog & literature synthesis

External-literature cross-check (DeepSeek scan + the §3 literature gate).
**Caveat on every item: futures/ETF/unverified results, none re-costed for
retail CFD — directional only, must clear the D1 cost anchor + DSR/min-sample.**

- **BR1 — ORB volatility-regime conditioning (read-only, cheap).** Lundström
  (2020): ORB profitability is volatility-state-conditional (~150–200 bps/day
  high-vs-low-vol spread; S&P 500 / crude futures). We never sliced ORB by
  entry-vol regime. Slice ORB net + expectancy by ATR%-at-entry quantile ×
  session; if it holds → an ORB entry/size filter.
- **BR2 — ORB stop-design cross-check (read-only).** Our OR-width stop = the
  literature's "opposite side of range" primary (corroborated). Its ATR
  alternative is an *initial* stop — distinct from the ATR-*trailing* already
  tested and reverted (catalog §13). Test a vol-scaled *initial* ORB stop;
  constraint: ORB drawdown tolerance is part of its (unvalidated) structure.
- **BR3 — Zarattini/Barbon/Aziz intraday-momentum variant (post-audit
  new-strategy candidate).** SPY 2007–2024, 19.6% ann., Sharpe 1.33. NOT
  EMA-crossover — open/VWAP noise-band intraday *continuation*, hold-to-close,
  dynamic trailing — structurally the ORB family on the index (SPY≈US500,
  where our only positive cell sat). Best-evidenced new form in the scan; must
  clear the D1 hurdle and carries the BR4 tension.
- **BR4 — Dynamic vs fixed-at-entry trailing A/B (read-only).** Literature
  uses volatility-recomputed (dynamic) trailing; our rebuild fixed
  ATR₁₄@entry×1.5 for live↔backtest parity. Quantify the parity-vs-edge
  trade-off before any engine change.
- **BR5 — AI/ML & 1-min LQP: NO action, already rejected.** LinkedIn
  6,473%/7.7-Sharpe = the unverifiable claim our fidelity rule rejects; 1-min
  LQP collides with the M1 non-goal; LLM-as-signal = the rejected
  `New_Strat.md` pivot. Recorded only to prevent re-litigation.
- **Data gap:** Lundström's crude-oil ORB is untestable here — XBRUSD is
  M1-only. Any future broadening fetch should add an oil instrument.

**Literature synthesis (the §3 gate, with sources).** Consensus: **indices
mean-revert, FX trends** — the "MR for FX" prior was wrong. Carver's horizon
taxonomy: mean reversion works at <30 min (best 4–8 min) or >2 yr; the
**1 h–2 yr band is trend territory** — our M15 / ~1–2 h holds sat squarely in
it. Intraday short-term reversal survives costs **only** with large-cap +
institutional **passive-fill** execution and has decayed to ≈0 recently
(Blitz et al. 2024) — we are retail, spread-taking, market-order: the opposite
regime. Selection/Deflated-Sharpe (Bailey & López de Prado) explains why our
residual cells were noise. Sources: Quantpedia *Short-Term Reversal*; Carver,
qoppac.blogspot 2025-03; Bailey & López de Prado, *Deflated Sharpe Ratio*;
QuantStart *intraday MR pairs*.

---

## Reproducibility

`cfd-trading/analysis/audit_archive/` holds the regenerator scripts
(`a2_slicing.py`, `a3b_zexit_sim.py`, `a3b_stopcap_sim.py`,
`build_trade_inspection_nb.py`), the trade dataset (`trades_M15.parquet`,
19,061 trades) and the sweep CSVs — enough to re-derive every number above.
Heatmaps and the 150-chart inspection notebook were deleted (large, derivable
from the parquet via the archived scripts). See the archive `README.md` for
the exact re-run command.
