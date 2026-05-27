# TODO — CFD Trading System

**Implementation phases 0–10: COMPLETE.** Project scaffold, storage layer,
broker wrapper, risk preflight, strategy loader, rule-engine monitor, MCP
tools/server, GitHub Actions CI, container deployment + MCP wiring,
scan/analysis improvements, and the backtesting framework were all built and
unit-tested (329 unit tests). Per-phase checklist detail is preserved in git
history — this file no longer tracks it.

> **The strategy audit is CLOSED (Phase A kill-criterion, 2026-05-18).**
> No strategy has a validated edge: `mean_reversion` DROPPED (non-viable on
> retail CFD); `momentum` & `ORB` UNVALIDATED (no edge survived
> Deflated-Sharpe / out-of-sample). Authoritative record + the next-phase
> strategy debate: **`docs/STRATEGY_AUDIT.md`**. Reproducibility:
> `analysis/audit_archive/`. **No new strategy is built before that debate
> concludes (discuss-before-implement).**

> **Update 2026-05-26 — D2 (news-proximity drift) KILLED.** The full build ran
> end to end — TZ gate PASSED (5 NFP anchors, both DST states) → 157-week
> ForexFactory scrape (1966 events) → prior-only expanding-σ standardization
> (no look-ahead, |z|≥1.0) → pooled backtest over a pre-registered {1,3,5 h}
> horizon grid (918 trades/horizon). **Fails all gates by structural margin:**
> OOS SR̂ −0.038…−0.028, DSR P≈0, best net +0.55 bps vs ~2.68 bps cost hurdle,
> CI lower negative at every horizon; the {1,3,5 h} profile is a dead flat-line,
> not a plateau. Introspection clean (no bug; fade also negative = no edge
> either way). 4th family with no validated post-cost edge; both Part 2 forks
> (D3, D2) died at the M15 intraday cost-tax. Code retained as reusable
> event-driven infrastructure (`analysis/d2_news/`). Verdict + numbers:
> `docs/STRATEGY_AUDIT.md` Part 2 → "D2 — Run + verdict" and
> `analysis/d2_news/d2_run_2026-05-26.md`. **No active coding thread** — open
> agenda (forks B/C/D, D1) is discuss-before-implement.


The forward agenda lives in `docs/STRATEGY_AUDIT.md` Part 2 (threads D1–D5,
carried-forward guardrails, the D1 cost anchor), **not here**. The items below
are infrastructure carried forward independent of any strategy choice.

---

## Deferred (v2+) — infrastructure only

- [ ] Persistent monitor daemon — survives Claude Code session end
- [ ] AutoGate — replace ManualGate with automated approval + circuit breaker
- [ ] Alpha Vantage MCP for macro context
- [ ] Proactive monitor alert if top-ranked asset changes
- [ ] Web UI / dashboard for trade history
- [ ] Migrate SQLite → Postgres (RDS) for AWS deployment

> New *strategies* (e.g. breakout, sentiment) are intentionally **not** listed
> here — they are gated behind the `docs/STRATEGY_AUDIT.md` Part 2 debate, not
> a backlog item.

### Broker / Instrument Generalization Refactor

See `docs/SYSTEM_DESIGN.md` §3.9. The logic layer (preflight, strategy,
storage, monitor rules) is already generic; the tools layer and monitor I/O
are coupled to Capital.com response shapes.

- [ ] `BrokerClient` Protocol + normalized types in `broker/protocol.py`
      (`Position`, `OHLCBar`, `AccountInfo`, `Sentiment`, `OrderRequest`,
      `ExecutionResult`)
- [ ] Adapter wrapping `CapitalClient` → normalized types
- [ ] Refactor `session_tools.py`, `scan_tools.py`, `trade_tools.py` and
      `monitor/monitor.py` I/O to the normalized types only
- [ ] Move Capital.com execution quirks into the adapter (create→confirm
      two-step; stop_distance vs stop_level for trailing stops)
- [ ] `get_sentiment()` returns `None` when unavailable; tools handle absence
- [ ] LONG/SHORT vs BUY/SELL — use LONG/SHORT throughout; adapter translates
