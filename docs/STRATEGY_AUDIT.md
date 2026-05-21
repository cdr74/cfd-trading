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

## Part 2 — Strategy Debate (D3 closed 2026-05-21; D1/D2/D4 open)

The pivot. Fork A (D3/BR3 — Zarattini-inspired volatility-band intraday
continuation) was tested and **killed 2026-05-21** (see "Task #7 — Run +
verdict" below). The remaining threads stay open as the next-phase agenda.
Discuss before implementing; no new strategy is coded before the relevant
thread concludes.

- **D1 — Is a traditional mechanical edge winnable on retail CFD at all?**
  Decide explicitly: accept that the cost/execution environment is the binding
  constraint, and what (if anything) changes the premise (different broker /
  instrument class / non-CFD venue / passive-fill capability). **Status:**
  open; D3's failure reinforces the question but doesn't answer it.
- **D2 — News-proximity / event-driven (was audit A4).** Post-news drift as a
  distinct strategy class: ForexFactory historical calendar, timezone
  validation, surprise-threshold entries, hours-not-seconds horizon. Was
  deferred behind D3; **now unblocked but unrun.**
- **D3 — Literature-led direction (Zarattini-inspired volatility-band
  intraday continuation, BR3 variant).** **CLOSED 2026-05-21 — KILLED on
  pre-registered OOS gates.** Full record: "Task #7 — Run + verdict" below.
- **D4 — Claude co-pilot, re-scoped (former Phase E).** With no validated
  mechanical base, redefine what signals a co-pilot would review and whether
  that role is still meaningful. **Status:** open and now more salient — if
  no mechanical thread (D1/D2) clears, D4 is the remaining deploy claim.
- **D5 — AI/ML & sub-30-min/multi-year horizons:** explicitly out unless D1
  changes the premise (M1/M5 primary TF and LLM-as-signal were already
  rejected; Carver's viable MR bands need passive-fill or multi-year holds).

### Decisions (2026-05-19) — Fork A committed; D3/BR3 first

**Cost premise re-tested and CLOSED** (supersedes any "is 0.75 bps too high"
doubt). Re-derived from the 19,061-trade archive (`trades_M15.parquet`),
mid-to-mid vs fill-to-fill; reconciles exactly with the §2/§7 per-class nets
(Index −0.29 / FX −0.82 / GOLD −2.73):

- Realized round-trip cost = **exactly one quoted `spread_at_entry`**: Index
  0.753 / FX 0.835 / Commodity 1.280 bps. 0.75 is the *realized* blended cost
  across the actual entry timestamps, **not** a padded assumption; the ~0.6 bps
  headline *understates* what these trades paid (fills cluster at wider-spread
  session opens / volatile bars).
- **Kill-criterion survives at ZERO friction.** Gross mid-to-mid edge is
  negative or ≈0 in **7 of 9** class×strategy cells. Cutting cost cannot rescue
  a frictionless-negative strategy; the 3× multiplier never bound the verdict.
- Only positive *frictionless* signals: Index-ORB (+1.92 gross / +1.17 net —
  already killed on DSR/bootstrap/OOS in §5, **not** on cost) and FX
  mean-reversion (+0.236 gross, n=6,152), which pays only at round-trip cost
  **<~0.24 bps**. → This *quantifies* fork C's threshold (institutional/ECN,
  ~3× tighter than retail CFD); it does **not** reopen the audit.

**Decision 1 — Fork A committed (horizon attack, in CFD).** Rationale: since
zero cost does not rescue the strategies, the only lever that changes the gross
number is a *bigger per-trade move*. Forks B/C/D remain on record as fallbacks
(C now carries the quantified <~0.24 bps target).

**Decision 2 — D3/BR3 first; D2 deferred.** Open the Zarattini/Barbon/Aziz
intraday-continuation variant (BR3) on the existing rebuilt engine first — it
sits on the only family with both a real frictionless edge and ample sample
(Index-ORB), is the best-evidenced new form in the scan, and needs no new
external data. D2 (news-surprise drift) is deferred until D3 clears or fails
the D1 anchor + DSR/min-sample. D2 blockers recorded for when it reopens:
(i) it **must** be built as one *pooled standardized-surprise* strategy — never
per-event cells (per-cell ≈10–12 trades/3 yr = the §5 thin-cell trap);
(ii) hard external-data gate — a fidelity-true, timezone-validated historical
calendar with actual + consensus forecast over 2023-05→2026-05 × the
8-instrument universe, or D2 is blocked the way XBRUSD blocked Lundström's oil.

**D3/BR3 spec — RESOLVED (2026-05-19), debate concluded:**

- **Universe:** pooled US500 + DE40 + UK100 as **one** strategy — never
  per-cell (the §5 / D2 thin-cell discipline). A *generalization* of Zarattini
  (single-instrument SPY), not a replication.
- **Timeframe:** **M15** (M1/M5 hard non-goal; Zarattini's native 1–5 min is
  off the table). This is a Zarattini-*inspired* test, not a replication — the
  BR-caveat compounds (un-re-costed for CFD **and** coarser TF). Label results
  accordingly.
- **Entry:** first session breach of `open ± k_entry·ATR₁₄` (σ from the
  engine's existing ATR₁₄ primitive — one volatility primitive,
  fidelity-clean), direction = breach side. **k_entry = 1.0** pinned
  2026-05-21 (see Progress).
- **Exit:** hard stop → **dynamic, bar-recomputed ATR trailing** → no TP →
  time-exit → global 21:00 UTC session close ("hold to close" = trail +
  session-close, no fixed TP — faithful to Zarattini's trail/close design).
- **Dynamic trailing built on BOTH sides — full parity.** The shared
  deterministic exit path is re-architected for bar-recomputed trailing AND the
  live `monitor/monitor.py` is updated to mirror it; the live↔backtest parity
  test is extended and must re-pass before any D3 result is trusted. A
  backtest-only dynamic exit was explicitly rejected as fidelity-false (it
  reproduces the §1 error that invalidated and deleted the prior audit).
- **DSR/min-sample pre-registered before the run** (standing gate): declared N,
  SR₀ from cross-cell Sharpe dispersion (the §5 method), 3× cost gate at the
  Index D1 anchor (US500 2.66 / DE40 1.47 / UK100 3.05 bps/trade), 10k
  bootstrap CI, OOS time-split, pre-committed kill-criterion. No post-hoc
  cell mining.

**Gate now moves from debate to design-docs-before-code (CLAUDE.md §1).**
The strategy debate has concluded. Before any implementation: (a) a
parity-preserving *dynamic-trailing architecture* sub-design (deterministic
bar-recomputed trail executed identically by backtest and live monitor — the
`how`, itself a §1 "always interactive" design decision), (b) update
`docs/SYSTEM_DESIGN.md` (exit-path architecture) and
`docs/CFD_STRATEGY_CATALOG.md` (the D3/BR3 algorithm), (c) implement both
sides + extend the parity test + unit tests, (d) pre-register DSR, then run.
Risk accepted by explicit decision: production exit logic is modified before
D3 has shown any edge.

**Progress (2026-05-19).** (a) Sub-design CONCLUDED: dynamic **Chandelier**
trail — long `max_high_since_entry − 1.5·ATR₁₄(closed bars)`, short symmetric;
recomputed every completed M15 bar from closed bars only; stateless-replay;
*may loosen* on vol expansion (literature-faithful); trigger logic unchanged;
one shared pure function + golden-trace parity test extended to the full
per-bar stop series. (b) Design docs updated: `SYSTEM_DESIGN.md` §3.7.1 +
§3.10, `CFD_STRATEGY_CATALOG.md` §14 (S6) + references.

**k_entry decision (2026-05-21).** Pinned **k_entry = 1.0** (single value,
no sweep — zero new free parameter beyond the existing trail multiple).
Forks considered: 0.5 (noise-dominant; ≈ "trade open direction"), **1.0**
(Zarattini-family heuristic translated onto ATR₁₄(M15); balanced N), 1.5
(stronger signal, fewer trades, still safe for DSR), 2.0 (pooled N ≈ 600
borderline for DSR with bootstrap CI + OOS split). 1.0 chosen as the
balance point: largest N consistent with a real volatility filter, no
parameter search ⇒ no DSR multiplicity penalty for entry tuning. Recorded
in `CFD_STRATEGY_CATALOG.md` §14.

**Implementation done (2026-05-21) — tasks #3-5.**
- `IntradayContinuationSignalState` + `chandelier_stop()` pure function in
  `strategy/signal_engine.py` (shared module — engine + monitor parity by
  construction).
- `evaluate_position` gained `current_atr` kwarg + `trailing_stop.mode`
  dispatch (`fixed_atr` | `dynamic_chandelier` | `fixed_pct`); momentum.yaml
  set to `fixed_atr` explicitly. SYSTEM_DESIGN §3.7.1 carries the new
  dispatch table.
- `monitor/monitor.py` registers the class, wires per-epic session_open
  from `backtest/sessions.py`, and passes `current_atr = signal_state.atr`
  each cycle.
- `backtest/engine.py` registers the strategy, passes per-epic session
  kwargs, captures `Trade.stop_history` per in-trade bar, and skips
  evaluation on the entry bar (semantic alignment with live monitor — a
  latent asymmetry the Chandelier parity check surfaced).
- Tests: 15 new in `test_intraday_continuation.py` + 2 new in
  `test_parity.py::TestChandelierTrailParity` (engine ≡ monitor per-bar
  AND engine ≡ closed-form `chandelier_stop()` formula). Full suite: **346
  unit tests pass** (329 prior + 17 new). No regressions.

#### Task #6 — DSR pre-registration (locked 2026-05-21, pre-run)

**This section is the pre-registration.** It freezes the methodology, the
numeric thresholds, AND the kill protocol BEFORE the D3 backtest is run.
No post-hoc adjustment. If a later finding requires methodology change, the
existing run is invalidated and re-pre-registration is needed.

Authority: extends [[deflated-sharpe-min-sample]] (the standing gate) +
§4 + §5 ORB precedent. The four open methodology forks were debated
2026-05-21; chosen options are recorded here.

**1. Data window & split.**
- **Window:** 2023-05-21 → 2026-05-21 (3 yrs; same re-baseline as the audit,
  `analysis/audit_archive/`).
- **Universe:** pooled **US500 + DE40**. Bars: M15 (the strategy
  resolution; one shared vol primitive — ATR₁₄ Wilder on closed bars).
- **OOS split:** **67/33 by calendar time. Boundary: 2025-01-21.** IS =
  2023-05-21 → 2025-01-21 (≈20 mo); OOS = 2025-01-21 → 2026-05-21 (≈16 mo).
  Matches §5 ORB DSR re-evaluation precedent. Expected pooled N ≈ 1,100
  over the full window → IS ≈ 730, OOS ≈ 370. Both well above the
  100-trade min-sample floor (Bailey/LdP). (Lower than the original 1,700
  estimate because UK100 has been dropped — see amendment note below.)

> **Pre-run amendment (2026-05-21, pre-run).** The originally locked
> universe was pooled US500+DE40+UK100. Pre-run data audit found UK100 M15
> depth is structurally limited (~10 months, 2025-07-09 → 2026-05-14) by
> MT5 / Capital.com demo history — same root cause as §5 ORB's "UK100-London
> has zero train trades (all data 2025+ — un-OOS-testable)". UK100 cannot
> contribute to the IS window (entirely after the IS boundary). To preserve
> the IS=OOS-universe symmetry that the DSR / kill-criterion comparison
> requires, **UK100 is dropped from the run.** The pooled universe is
> US500 + DE40 only. Pooled cost re-derives (D1 anchor: US500 0.89, DE40
> 0.49 bps; N-weighted at run-time, expected ≈ 0.70 bps → 3× hurdle ≈
> 2.10 bps). K = 121 (the trial count is per-strategy, unchanged by the
> universe reduction). The amendment is recorded BEFORE the run; no result
> is invalidated because none exists yet. The pre-pivot intent — "pooled
> indices, never per-cell" — is preserved; the pool is just smaller than
> originally hoped.

**2. The metric & the gates (each computed separately in IS and in OOS).**
- **Per-trade Sharpe** SR̂ = mean(net_pnl_pts) / std(net_pnl_pts) where
  net_pnl_pts is the spread-cost-included PnL per trade (the engine's
  fidelity-true cost-included number; the `pnl_points` field on `Trade`).
- **Deflated Sharpe** DSR P(SR > SR₀) per Bailey/LdP. Trials count K is
  the audit's prior count incremented by 1 for D3 (the standing-gate
  convention): **K = 121.** SR₀ recomputed from the same cross-cell Sharpe
  dispersion as §5 (σ̂ ≈ 0.282/√(2 ln 120) ≈ 0.090 implied) → **SR₀ ≈ 0.283
  for K=121** (Bailey approximation; one decimal place's worth of drift
  from the ORB run's 0.282 — within the rounding of the underlying
  estimator). The script that recomputes SR₀ from the audit archive is the
  source of truth at run-time; this number is the expected value.
- **Bootstrap 95% CI** on SR̂ via **moving-block bootstrap, block length
  20 trades, 10,000 resamples.** Block length ≈ 1 week of pooled signals;
  matches §5 precedent.
- **3× cost gate:** **pooled N-weighted average cost** across the
  in-scope indices (per-trade `spread_at_entry` × 2; the same cost the
  engine subtracts at entry+exit). D1 anchor (per-index, the in-scope two
  after the UK100 amendment): US500 0.89, DE40 0.49 bps/trade →
  pooled-N-weighted estimate ≈ 0.70 bps. **Hurdle: net/trade ≥ 3 × pooled
  cost ≈ 2.10 bps.** Pooled cost AND pooled net are computed from realized
  N at run-time; threshold is 3× the realized pooled cost.
- **Min-sample:** pooled N (IS and OOS each) must be ≥ 100 trades.

**3. Pass/Fail per window (IS, OOS):**
| Gate | Pass condition |
|---|---|
| Min-sample | pooled N ≥ 100 |
| DSR | P(SR̂ > SR₀) ≥ 0.95 |
| Bootstrap CI | 95% CI lower bound > 0 |
| Cost gate | net/trade ≥ 3 × pooled cost (≈ 2.26 bps) |

**4. Kill-criterion (pre-committed, with mandatory introspection).**
**D3 dies** if **OOS fails ANY of the four gates** — the strictest
combination, mirroring the Phase A kill that closed the audit. **BUT**
between the result and the kill declaration there is a **non-optional
introspection step**, motivated by the deleted prior audit (the time-exit
defect that invalidated every previous result before being caught):

> Before declaring D3 dead — or alive — we must inspect for implementation
> errors. The kill protocol is:
>
> 1. Run the backtest.
> 2. **Pre-kill introspection (mandatory).** Visually and statistically
>    inspect at minimum:
>    - Equity curve (pooled and per-index)
>    - PnL distribution (trade histogram; tails)
>    - Trade sample — at least 20 random trades, manually verified against
>      the bar series (entry timing, stop placement, trail behaviour,
>      exit reason)
>    - Per-instrument N + win-rate + cost-net split
>    - Drawdown periods (any single trade or week dominating the result?)
>    - **Parity test passes on the run data** (re-run
>      `tests/unit/test_parity.py` after the backtest if any engine code
>      changed during the run)
>    - Sanity check vs the literature expected range (Zarattini SPY
>      reported Sharpe 1.33 at 1-min, *un-re-costed*; on M15 retail CFD
>      we expect substantially lower realized Sharpe — anything wildly
>      outside say 0.05–1.5 needs explanation)
> 3. **If introspection finds a bug:** fix it → re-run → re-introspect.
>    The pre-fix run is invalidated. No partial kill on bugged data.
> 4. **If introspection clears AND OOS fails ≥1 gate:** D3 is dead.
>    Record findings in this doc; proceed to fallback (Forks B/C/D
>    per the 2026-05-19 decisions).
> 5. **If introspection clears AND OOS clears all 4 gates:** D3 is a
>    candidate edge. Proceed to walk-forward / MC robustness (the
>    audit's §3 future-test deferred items) and live MTRL before any
>    deploy claim.

This introspection step is the lesson from the deleted prior audit. Cost:
a few hours of manual review per run. Value: the only protection against
silently shipping a result driven by an engine bug.

**5. What is NOT in this pre-registration (and stays out).**
- Walk-forward optimization. WFO needs something to optimize; D3 has zero
  free parameters in-scope (k_entry, k_trail, session_open all pre-pinned).
  Future-defer per §3.
- Per-cell slicing. The pooled metric IS the metric. Per-instrument
  numbers are reported for diagnosis only; they do not gate.
- Live deploy. No D3 demo or live trading until D3 clears OOS AND
  walk-forward AND MTRL — a multi-step gate, the first step of which is
  this run.

**6. Reproducibility.**
- Backtest entry point: `analysis/audit_archive/` re-baseline scripts +
  the rebuilt engine (`backtest/engine.py`) with `intraday_continuation`
  registered.
- All inputs (data window, k_entry, k_trail, OOS boundary, K, SR₀,
  bootstrap block, gate thresholds) frozen in this section.
- Output: a `audit/d3_run_<date>.md` report linked from this section,
  containing the four gates × two windows table, the introspection
  findings, and the kill/keep decision per §4 above.

Next: (e) run (#7) — only after this pre-registration is reviewed by the
user (1 final eyes-on before locking).

#### Task #7 — Run + verdict (2026-05-21): D3 KILLED

**Run.** Pre-registered backtest executed 2026-05-21. Universe pooled
US500+DE40 (UK100 dropped per the amendment); 2023-05-21 → 2026-05-21 at
M15; 1,468 trades total. Artefacts:
- `audit/d3_run_2026-05-21.parquet` — trades log
- `audit/d3_analyze.py` — DSR + bootstrap + cost-gate + diagnostics
- `audit/d3_run_2026-05-21.md` — gate results + introspection findings
- `audit/d3_trade_inspection.ipynb` — 120 chart visual deep-dive
  (`audit/build_d3_inspection_nb.py` regenerates)

**Four gates × two windows:**

| Window | N | SR̂ | DSR P | 95% CI (block 20, 10k) | Net (bps) | Hurdle (bps) | Verdict |
|---|---:|---:|---:|---|---:|---:|---|
| IS  (2023-05 → 2025-01) | 786 | −0.007 | 0.000 | [−0.072, +0.071] | **−0.578** | 2.389 | min ✓, DSR ✗, CI ✗, cost ✗ |
| OOS (2025-01 → 2026-05) | 674 | +0.054 | 0.000 | [−0.020, +0.117] | +1.302 | 1.814 | min ✓, DSR ✗, CI ✗, cost ✗ |

OOS fails 3 of 4 gates. Per the pre-registered kill criterion: **kill**.

**Pre-kill introspection complete (2026-05-21).** Per the mandatory
introspection step, the run was reviewed before applying the kill rule.
Findings:

- **Trail behaviour confirmed correct.** Per-bar parity test
  (`tests/unit/test_parity.py::TestChandelierTrailParity`) passed on the
  run data: engine ≡ live monitor per-bar AND engine ≡ closed-form
  `chandelier_stop()` formula. No implementation bug detected.
- **Entry distribution sane.** US500 entries concentrate in 14:00-15:00
  UTC (83% in first hour after session open); DE40 in 08:00-09:00 UTC
  (89% in first 2 hours). Strategy is firing on the spec-intended
  early-session breach, not drifting elsewhere.
- **One bookkeeping bug found and fixed in the analysis (not the
  engine):** the `d3_analyze.py` cost calc initially doubled
  `spread_at_entry` (the engine already deducts full round-trip in
  `pnl_points`). After fix, realized pooled cost (≈0.6-0.8 bps) and 3×
  hurdle (≈1.8-2.4 bps) align with the audit's D1 anchor — confirming
  the run's cost model.
- **Decisive smell — the exit-reason asymmetry:**

  | reason | count | share | mean net (bps) | sum net (bps) |
  |---|---:|---:|---:|---:|
  | Hard stop (Chandelier trail) | 1,411 | 96.6% | **−1.44** | −2,027 |
  | Session close (no bar at threshold) | 49 | 3.4% | **+50.0** | +2,450 |

  The strategy's positive PnL share is **entirely the rare 3.4%** of
  trades that exit via a day-rollover fallback (the engine's "no bar
  fell in that day's close window" hack, closing at the prior bar's
  close). Chandelier-trail exits LOSE on aggregate. The trail is
  structurally acting as a profit-cap on the 96.6%, and the win side
  comes only from trades the trail FAILED to catch. This is consistent
  whether the 3.4% are realistic fills or a backtest-fill artifact —
  either way, the trail mechanism is not the source of any edge.

**Kill verdict (2026-05-21):**

**D3/BR3 (intraday_continuation) is dead at the M15 / retail-CFD level.**
The signal+trail family fails OOS on three of four pre-registered gates
by structural margin (SR̂ ≈ 0 in IS; would need ~5× to clear DSR). The
audit's D1 cost-anchor prediction is confirmed for the literature-led
best-evidenced family. Adding a hold-to-close variant (drop the trail)
was considered as a single defensible second test, but was rejected by
the user: (a) it would be another trial against the same data window
(DSR multiplicity cost), (b) the strategy still needs to clear the same
gates, and (c) the broader audit conclusion — retail-CFD mechanical
intraday edge is structurally hostile — is more strongly reinforced by
moving to the next-phase forks than by chasing a narrower variant of an
already-failed family. **No further D3 variants.**

**Status of D3 next-phase forks (per the 2026-05-19 decisions):**
- **Fork A (horizon-attack, in CFD) — CLOSED.** D3/BR3 was the
  literature-led test. It failed; the family is killed on this venue.
- **Fork B (different horizon — multi-day / swing in CFD) — open agenda.**
- **Fork C (lower-cost venue / asset class — e.g. crypto-native exchange,
  futures, FX ECN) — open agenda.**
- **Fork D (Claude-as-co-pilot only, no new mechanical signal) — open
  agenda.** Aligned with [[project-claude-role]]: Claude reviews
  pre-calculated signals; if no mechanical edge survives the audit, the
  system's deploy claim shrinks to "interactive trading assistant"
  rather than "autonomous mechanical edge".

**Transferable learnings from D3 (extending §8):**
1. **Pooled "never per-cell" is the right discipline** but raises the
   bar: an instrument with a different data window (UK100) cannot be
   silently included in a pooled IS/OOS comparison. The pre-run
   amendment to drop UK100 was structurally forced; pre-registration
   only protects against post-hoc bias if it acknowledges data limits.
2. **Per-bar parity is worth the engineering cost.** Surfaced a latent
   engine-vs-monitor asymmetry on the entry bar that prior fixtures had
   masked. The fix shipped as part of D3 implementation; the trail-mode
   dispatch infrastructure (`trailing_stop.mode`, `current_atr` kwarg)
   is reusable by future strategies (still in production code).
3. **The Chandelier-as-profit-cap pattern** — 96.6% trail-exits lose,
   3.4% non-trail exits win huge — is a *general* warning sign for any
   future strategy that pairs a continuation signal with a tight
   volatility trail on a friction-heavy venue. Tight trail + small edge
   + spread tax = trail catches the wiggle and crystallises the cost.
4. **Pre-kill introspection caught the cost-calc bug** in the analysis
   script. The protocol's value is empirical, not theoretical.
5. **Methodology held.** No parameter mining, no post-hoc cell rescue,
   no "let's just try k_trail=2.5" detour. The kill verdict applies to
   a single pre-registered run with all parameters frozen — the cleanest
   possible kill record.

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
