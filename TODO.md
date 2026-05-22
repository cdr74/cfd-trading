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

> **Update 2026-05-22 — D2 is the active thread.** Fork A (D3/BR3) KILLED
> 2026-05-21. **D2 (news-proximity drift) pre-registration is LOCKED** and its
> data-feasibility recon PASSED: ForexFactory historical scrape via
> `cloudscraper`, per-event `dateline` = UTC epoch (timezone gate first anchor
> cleared — NFP → 12:30 UTC). **Next:** finish the tz-gate anchors (incl. a
> winter/EST week) → build the FF scraper → pooled standardized-surprise
> backtest. Full spec: `docs/STRATEGY_AUDIT.md` Part 2; data sub-task:
> `analysis/d2_news/README.md`.


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
