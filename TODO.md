# TODO — CFD Trading System v1

Implementation plan for v1 as defined in README.md. Work through phases in order — each phase depends on the one before it. Update status as work progresses.

Status markers: `[ ]` not started · `[~]` in progress · `[x]` done

---

## Phase 0 — Project Scaffold ✓

- [x] Directory structure created (`config/`, `src/cfd_trading/`, `tests/`, `data/`)
- [x] `pyproject.toml` — dependencies declared, package configured
- [x] `.gitignore` — data/, .env, .db files excluded
- [x] `.env.example` — all required environment variables documented
- [x] `config/risk.yaml` — global hard limits
- [x] `config/watchlist.yaml` — asset universe
- [x] `config/strategies/momentum.yaml` + `momentum.md` (stub)
- [x] `config/strategies/mean_reversion.yaml` + `mean_reversion.md` (stub)
- [x] `config/strategies/_base.md` (stub)
- [x] `config/strategies/scan.md` (stub)
- [x] All Python module stubs created
- [x] `README.md` — full architecture and design decisions
- [x] `CLAUDE.md` — collaboration guide
- [x] `TODO.md` — this file

---

## Phase 1 — Storage Layer ✓

Foundation for all other components. Everything writes here.

- [x] `storage/db.py`
  - [x] SQLite connection management (path from env or default `./data/trading.db`)
  - [x] Schema creation: `sessions`, `cycle_snapshots`, `trades`, `reasoning_traces`
  - [x] `init_db()` — idempotent, safe to call on every startup
- [x] `storage/repository.py`
  - [x] `create_session()` → session_id UUID; `close_session(session_id, summary)`
  - [x] `save_cycle_snapshot(session_id, asset, strategy, account_bal, positions, market_data)`
  - [x] `save_trade(session_id, cycle_id, asset, direction, size, entry_price, stop_loss, take_profit, broker_ref)` → status: PROPOSED
  - [x] `update_trade_status(trade_id, status)` — APPROVED / REJECTED / EXECUTED / FAILED
  - [x] `save_reasoning_trace(session_id, cycle_id, prompt_tokens, output_tokens, reasoning, tool_calls)`
  - [x] `get_open_trades()` → used by session start and monitor
  - [x] `get_session_summary(session_id)` → trade counts (P&L/win-rate filled at session end from broker data)
- [x] Unit tests — `tests/unit/test_repository.py` — 12 tests, all passing

---

## Phase 2 — Broker Wrapper ✓

Thin wrapper re-exporting `CapitalClient`. Validates all needed API calls work against demo.

- [x] `broker/capital_client.py` — re-exports `CapitalClient` from `capital-com-client`; import confirmed working
- [x] `pyproject.toml` — `capital-com-mcp-server` declared as dependency (installed from GitHub checkout in CI)
- [x] `tests/integration/conftest.py` — loads .env from local or capital-mcp-server fallback
- [x] Integration tests — `tests/integration/test_broker.py` — 7 tests passing against demo API
  - [x] authenticate, ping, get_account_info, get_prices, get_positions, get_client_sentiment, get_historical_prices
  - [ ] `@pytest.mark.trade` — create + confirm + close (written, run manually when needed)
- [ ] **Human smoke test SM-02 (partial)** — confirm prices returned are plausible live values

---

## Phase 3 — Risk Preflight ✓

Must be solid before any execution path is wired up. Unit-test exhaustively.

- [x] `risk/preflight.py`
  - [x] `validate_entry_proposal(proposal, strategy_config, global_config, open_positions_count, margin_pct) -> PreflightResult`
  - [x] `validate_monitor_decision(decision, position, strategy_config, global_config, margin_pct) -> PreflightResult`
  - [x] `PreflightResult` — dataclass: `passed: bool`, `violations: list[str]`
  - [x] Checks: stop_loss present and within max_pct, size within min/max, R:R ratio, margin floor, max positions, trailing stop bounds, contra_indicators required
  - [x] Trailing stop ratchet check — LONG stop can only move up, SHORT stop can only move down
- [x] Unit tests — `tests/unit/test_preflight.py` — 43 tests, all passing (55 total unit tests)

---

## Phase 4 — Strategy Loader + Prompt Config ✓

Defines the pluggable strategy interface. Both strategies must load and validate correctly.

- [x] `strategy/loader.py`
  - [x] `load_strategy(name: str, config_dir: Path) -> Strategy` — loads YAML + MD pair
  - [x] `list_strategies(config_dir: Path) -> list[str]` — discovers all valid strategy names; excludes `_base` and `scan`
  - [x] `Strategy` dataclass: name, description, config (dict), prompt (str)
  - [x] Schema validation on YAML load — fails loudly if any required field missing
  - [x] `load_base_prompt(config_dir: Path) -> str` — loads `_base.md`
  - [x] `load_scan_prompt(config_dir: Path) -> str` — loads `scan.md`
- [x] `config/strategies/_base.md` — proposal format, hard rules, contra_indicators contract
- [x] `config/strategies/scan.md` — ranking criteria: trend, ATR, spread/ATR ratio, sentiment, session
- [x] `config/strategies/momentum.md` — trend-following entry logic, trailing stop guidance, contra_indicators
- [x] `config/strategies/mean_reversion.md` — counter-trend entry logic, fixed stop/TP, contra_indicators
- [x] Unit tests — `tests/unit/test_strategy_loader.py` — 22 tests, all passing (77 total unit tests)

---

## ~~Phase 5 — Agent Layer~~ — Removed

The agent layer (claude_client, prompt_builder, output_parser) has been removed from scope. The monitor is a rule engine — no Anthropic API calls at runtime. Claude Code is the only reasoning engine, operating during the entry flow only.

---

## Phase 5 — Monitor (Rule Engine) ✓

Autonomous position management subprocess. Evaluates strategy YAML rules mechanically — no AI calls.

- [x] `monitor/monitor.py`
  - [x] `evaluate_position(position, price_data, strategy_config, session_end_time)` — pure rule engine, no I/O
  - [x] Rules evaluated in order: hard stop → trailing stop ratchet → take profit → time exit → HOLD
  - [x] `run_cycle` — fetches positions + prices, evaluates, executes ADJUST/CLOSE, writes cycle_snapshot
  - [x] `run_loop` — main loop with configurable interval; sleeps in 1s increments for responsive SIGTERM
  - [x] ADJUST via `CapitalClient.update_position`; CLOSE via `CapitalClient.close_position`
  - [x] Writes `cycle_snapshot` to DB on every position every cycle
  - [x] Writes `audit.jsonl` entry on every ADJUST or CLOSE
  - [x] Graceful shutdown on SIGTERM — finishes current cycle then exits
  - [x] CLI entry point: `python -m cfd_trading.monitor.monitor --session-id ... --db-path ...`
- [x] `storage/db.py` + `storage/repository.py` — `strategy` column added to trades; `get_trade_by_broker_ref`, `update_trade_stop_loss` added
- [x] Unit tests — `tests/unit/test_monitor_rules.py` — 25 tests, all passing (102 total unit tests)
  - [x] Hard stop LONG/SHORT — triggers at and below stop level
  - [x] Trailing stop ratchet LONG/SHORT — fires when profitable, skips when not, skipped when disabled
  - [x] Take profit LONG/SHORT — triggers at and past target
  - [x] Time exit — triggers within window, skips outside window, skips when no session end set
  - [x] Rule priority: hard stop > trailing stop > take profit > time exit
  - [x] HOLD when no conditions met; handles missing price data gracefully
- [ ] Integration tests — `tests/integration/test_monitor.py`
  - [ ] `@pytest.mark.trade` — open a demo position, run one monitor cycle, verify snapshot written to DB
- [ ] **Human smoke test SM-06** — observe monitor log for at least 2 cycles, verify cycle snapshots in DB

---

## Phase 6 — MCP Tools + Server ✓

The FastMCP server that Claude Code talks to.

- [x] `tools/_state.py` — `SessionState` dataclass, `get/set/clear/require_state` helpers
- [x] `tools/session_tools.py`
  - [x] `start_session` — authenticate, check positions, load config, start monitor subprocess, return session summary
  - [x] `end_session(close_positions: bool)` — stop monitor, optionally close all, write summary to DB
  - [x] `get_session_status` — positions, P&L, monitor alive, session duration
- [x] `tools/scan_tools.py`
  - [x] `scan_markets(watchlist: str | None)` — fetch ATR + trend slope + spread for each instrument, return ranked list
  - [x] `analyze_instrument(epic: str, strategy: str)` — fetch 60x1min + sentiment + open positions, return structured context dict
- [x] `tools/trade_tools.py`
  - [x] `validate_proposal(proposal_json: str)` — run preflight, return pass/fail + violations
  - [x] `execute_trade(proposal_json: str)` — create_position + confirm_deal + log to DB, return deal details
- [x] `server.py` — register all 7 tools with FastMCP
- [x] Unit tests — `tests/unit/test_tools.py` — 27 tests, all passing (129 total unit tests)
  - [x] Each tool returns expected structure with mocked CapitalClient
  - [x] `validate_proposal` returns violations correctly for all rejection paths
  - [x] `execute_trade` does not call Capital.com if preflight fails
  - [x] `execute_trade` logs trade to DB on success
- [ ] Integration tests — `tests/integration/test_tools.py`
  - [ ] `@pytest.mark.integration` — `start_session`, `scan_markets`, `analyze_instrument`, `get_session_status`
  - [ ] `@pytest.mark.trade` — full cycle: start → scan → analyze → validate → execute → end
- [ ] **Human smoke tests SM-01 through SM-08**

---

## Phase 7 — GitHub Actions ✓

Automated integration tests on every push.

- [x] `.github/workflows/ci.yml`
  - [x] Trigger: push/PR to any branch
  - [x] Python 3.12; checks out `capital-mcp-server` and `capital-com-client` siblings via `GH_PAT`; installs in dep order with `--no-deps` to avoid private git URL resolution
  - [x] `unit-tests` job: `pytest tests/unit/ -v` (always, no secrets needed)
  - [x] `integration-tests` job: `pytest tests/integration/ -m integration -v` (runs after unit-tests; uses demo API secrets)
  - [x] `@pytest.mark.trade` excluded from CI (not in `-m integration` filter)
  - [x] Required secrets: `GH_PAT` (private repo checkout), `CAPITAL_BASE_URL`, `CAPITAL_API_KEY`, `CAPITAL_IDENTIFIER`, `CAPITAL_API_KEY_PASSWORD`
  - [x] `anthropic` dependency removed — monitor is a pure rule engine, no API calls at runtime
  - [x] Both unit and integration CI jobs passing green
- [x] Add container build + push job — `publish.yml` builds and pushes to `ghcr.io/cdr74/cfd-trading` on every push to main; `mcp-start.sh` now pulls pre-built images

---

## Phase 8 — Container Deployment + MCP Wiring ✓

Both MCP servers run as Podman containers serving streamable-HTTP over HTTPS, wired to Claude Desktop.

- [x] `Containerfile` — multi-repo build context (parent `trading/` dir); installs full dep chain without internet access
- [x] `podman-compose.dev.yml` — dev compose (builds from source, sets `MCP_TRANSPORT=streamable-http`, mounts `../certs`)
- [x] `server.py` — `MCP_HOST`, `MCP_PORT`, `MCP_TRANSPORT`, `SSL_CERTFILE`, `SSL_KEYFILE` env vars wired; HTTPS via uvicorn when SSL vars set
- [x] Claude Desktop config (`claude_desktop_config.json`) — both servers wired as streamable-HTTP endpoints
  - `cfd-trading`: `https://localhost:8089/mcp`
  - `capital-mcp-server`: `https://localhost:8088/mcp`
- [x] TLS certs generated with mkcert in `trading/certs/`; mkcert root CA imported into Windows Trusted Root store for Claude Desktop trust
- [x] Both containers verified running and responding on `https://.../mcp`
- [x] `mcp-start.sh` pulls pre-built images from ghcr.io; `publish.yml` builds and pushes on every push to main
- [x] Run full end-to-end smoke tests — SM-01 through SM-11 all passed

---

## Phase 9 — Scan / Analysis Improvements ✓

- [x] Remove session labeling from `scan_markets` — `spread_pct_of_atr` is the real liquidity signal; Capital.com CFDs trade 24/7 so session-based deprioritisation was misleading
  - [x] Removed `_current_session_label()` from `scan_tools.py`
  - [x] Removed `"session"` key from `scan_markets` response JSON
  - [x] Removed "Session alignment" criterion from `config/strategies/scan.md`
- [x] Add computed indicators to `analyze_instrument` response
  - [x] `EMA_9`, `EMA_21` — from 60 × 1-min bars (bid close)
  - [x] `zscore` — z-score of latest close relative to 20-bar window (mu, sigma, z)
  - [x] `momentum.md` updated to reference EMA_9/21 cross in entry logic and signal_basis
  - [x] `mean_reversion.md` updated to reference z-score threshold (|z| ≥ 2.0) in entry logic and signal_basis
- [x] Vol-scaled position sizing in `analyze_instrument`
  - [x] `target_risk_pct` added to `momentum.yaml` (1.0%) and `mean_reversion.yaml` (0.5%)
  - [x] `target_risk_pct` added to strategy loader required fields
  - [x] `analyze_instrument` fetches account balance and computes `suggested_size = target_risk_pct/100 × balance / ATR`
  - [x] Result exposed as `"account": {"available_balance": ..., "suggested_size": ...}` in response
- [x] Unit tests — 136 passing (7 new tests covering EMA, z-score, and account sizing)

---

## Phase 10 — Backtesting Framework

See `docs/SYSTEM_DESIGN.md` §3.10 and `docs/BACKTESTING.md` for full design decisions, rationale, and MT5 constraints. Do not re-derive these — they were established through empirical testing.

### 10.1 Data layer — Windows-side fetch script

- [x] `backtest/fetch_ohlc.py` (Windows Python, not WSL2)
  - [x] Symbol map: `GOLD→XAUUSD`, `XBRUSD→BRENTOIL`, all others match watchlist epics exactly
  - [x] Bulk fetch: **4×30-day** `copy_rates_range` calls per instrument — MT5 caps at ~100k rows/call; 30-day windows (≤43,200 rows) stay safely under the cap. 60-day windows fail for dense instruments (DE40, GOLD).
  - [x] Writes to `C:\Users\chris\dev\trading-data\trading.db` — accessible from WSL2 at `/mnt/c/Users/chris/dev/trading-data/trading.db`
  - [x] SQLite table: `ohlc_bars (epic TEXT, resolution TEXT, ts INTEGER, open REAL, high REAL, low REAL, close REAL, volume INTEGER, PRIMARY KEY (epic, resolution, ts))`
  - [x] Epic column stores watchlist name (e.g. `GOLD`), not MT5 symbol — translate at write time
  - [x] Upsert on conflict (ignore duplicates from overlapping windows)
  - [x] Daily incremental fetch: one call per instrument covering yesterday → today
  - [x] Run: 1.1M bars loaded across 11 instruments (2026-05-11)

### 10.2 Data layer — WSL2-side schema integration

- [x] Add `ohlc_bars` table to `storage/db.py` `init_db()` — idempotent, same SQLite file
- [x] Add `get_bars(epic, resolution, from_ts, to_ts)` to `storage/repository.py`
- [x] Unit tests for `get_bars`

### 10.3 Backtest mode flag

- [x] `BACKTEST_MODE=true` env var — when set, any call path that would invoke `CapitalClient` raises `RuntimeError("Live API disabled in backtest mode")` immediately
- [x] Add guard to `tools/_state.py` or broker wrapper — enforced at the client call level, not per-tool

### 10.4 Rule-based entry signals

- [x] `backtest/signals.py`
  - [x] `momentum_signal(bars) -> "LONG" | "SHORT" | None` — EMA_9 crosses above/below EMA_21 in last bar; confirm trend_slope direction
  - [x] `mean_reversion_signal(bars) -> "LONG" | "SHORT" | None` — `|z_score| >= 2.0`; direction opposite to z_score sign
  - [x] Reuse `_compute_ema`, `_compute_zscore`, `_compute_atr` from `scan_tools.py` (already extracted as pure functions)
  - [x] Unit tests for both signals — uptrend bars produce LONG momentum signal; z>2 produces SHORT mean-reversion signal

### 10.5 Backtest engine

- [x] `backtest/engine.py`
  - [x] `run_backtest(epic, strategy, bars, strategy_config, risk_config) -> BacktestResult`
  - [x] Walk bars chronologically; at each bar compute indicators and check entry signal
  - [x] On signal: simulate MARKET entry at next bar open; run preflight; record position
  - [x] Per-bar position management: call `evaluate_position()` from `monitor/monitor.py` — same rule engine as live
  - [x] Track: entry price, stop_loss, take_profit, exit price, exit reason, P&L
  - [x] `BacktestResult` dataclass: trades list, win_rate, profit_factor, max_drawdown_pct, stop_out_rate, signal_frequency
  - [x] Unit tests with synthetic bar sequences covering: momentum entry → trailing stop exit; mean reversion entry → take profit exit; hard stop triggered

### 10.6 Backtest runner / reporting

- [x] `backtest/run.py` — CLI entry point: `python -m cfd_trading.backtest.run --strategy momentum --epic EURUSD`
  - [x] `--epic EPIC` / `--all-epics`; `--strategy NAME` / `--all-strategies`; `--resolution` (default M1)
  - [x] Reads bars from `BACKTEST_DB_PATH` (default `/mnt/c/Users/chris/dev/trading-data/trading.db`)
  - [x] Loads strategy config from `CONFIG_DIR/strategies/` and risk from `CONFIG_DIR/risk.yaml`
  - [x] Prints summary table: total_trades, win_rate, profit_factor, max_drawdown_pct, stop_out_rate, signal_frequency
  - [x] Sets `BACKTEST_MODE=true` at startup — no Capital.com or Anthropic API calls possible
  - [x] Unit tests — `tests/unit/test_run.py` — 11 tests; **190 unit tests passing** (up from 179)

### 10.7 Backtest signal improvements (research-driven, M1-specific)

*All three steps address cost/edge issues identified by RESEARCH.md (2026-05-12): H ≈ 0.50 at M1, ITSM operates at 30-min scale, 0.02% gap below spread breakeven.*

From `docs/RESEARCH.md` (2026-05-12): At M1 resolution, H ≈ 0.50 (random walk). ITSM operates at 30-min scale.
These improvements address the three cost/edge issues identified in research.

- [x] **Step 1 — EMA gap filter 0.02% → 0.05%** (done 2026-05-12)
  - `signals.py`: `_MIN_EMA_GAP_PCT = 0.0005` (was 0.0002)
  - Rationale: 0.02% = 1.6 pts at 8,000 — barely above a 1-pt spread. 0.05% = 4 pts = 4× spread (positive signal/cost ratio). See `docs/RESEARCH.md` §Cost and Viability Thresholds.

- [x] **Step 2 — ATR(14) ≥ 4× spread gate + 5-bar hold cap + spread fill-price costs** (done 2026-05-13)
  - `backtest/spreads.py`: per-instrument spread lookup table (Capital.com 2026-05 typical values). `spread_points(epic, price)` resolves absolute (FX, indices, commodities) and percentage-based (crypto) spreads.
  - `signals.py`: `MeanReversionSignalState` gains `spread_pts`, `max_hold_bars` params. ATR gate: `atr < 4 × spread_pts` → skip entry. Hold cap: exit after 5 bars if still in trade. `notify_entry/notify_exit` for bar counting.
  - `engine.py`: entry fill = `open ± spread/2`, exit fill = `close ∓ spread/2`. `spread_pts` passed to `MeanReversionSignalState`. `notify_entry/notify_exit` called at all open/close paths.
  - `run.py`: imports `spread_points`, computes per-epic spread from `bars[0].close`, passes to engine.
  - 18 new unit tests (233 total). `test_spreads.py` (8), `TestATRGate` (3), `TestHoldCap` (4), engine spread/hold cap tests (4 new + 1 updated).

- [x] **Step 3 — 30-min directional bias signal (ITSM architecture)**
  - Rolling 30-bar M1 buffer in `MomentumSignalState._m30_buf` (deque maxlen=30).
  - M30 bullish/bearish defined by OLS slope of the 30 closes (`_trend_slope`).
  - Hard gate: LONG blocked when M30 bearish; SHORT blocked when M30 bullish.
  - Permissive while buffer warming up (<30 bars). Disable via `signal_kwargs={"m30_gate": False}`.
  - 5 new unit tests in `TestM30Gate` (238 total).
  - **Post-backtest finding (2026-05-13):** M30 gate is self-defeating at M1 — crossovers happen at trend reversals when the 30-bar look-back still reflects prior direction. Gate effectively blocked all signals. Disabled in `run.py` (`m30_gate=False`). Needs true M30 bars from MT5 to be useful.

- [x] **Step 4 — Multi-resolution backtesting via in-process aggregation**
  - `backtest/aggregate.py`: `aggregate_bars(bars, period_minutes)` — groups M1 bars by UTC time bucket, merges OHLC. Handles M5/M15/M30/M60.
  - `run.py`: always fetches M1 from DB, aggregates to `--resolution` target in-process. M30 gate disabled for all resolutions pending true M30 data.
  - 15 new unit tests in `test_aggregate.py` (253 total).
  - Done: ran `--all-strategies --all-epics --resolution M15`. Results flat-to-negative across all 11 instruments × 3 strategies → triggered the audit phase.

### 10.8 Audit instrumentation (added 2026-05-14)

Trade-log persistence and gross-vs-net audit fields, added to support
Phase A of `/AUDIT_PLAN.md`. Source of truth for the audit work is
`/AUDIT_PLAN.md` and `/audit/A1_inventory.md`, **not** this file.

- [x] `Trade` dataclass gains `entry_mid`, `exit_mid`, `spread_at_entry`, `resolution` — enables gross-vs-net cost decomposition. Engine populates the first three; `run.py` stamps `resolution` after engine returns.
- [x] `run.py --output PATH` writes one Parquet file per run via `_write_trade_log`. `pyarrow>=16.0` added to runtime deps.
- [x] Stale BACKTESTING.md §9.2 fixed — code does deduct spread; doc had said otherwise.
- [x] Unit tests: `TestAuditFields` in `test_engine.py` (6 tests) and 3 parquet-writer tests in `test_run.py`. **302 passing.**
- [x] First augmented run: `/audit/trades_M15.parquet` (2,756 trades).

> **Forward work moves to `/AUDIT_PLAN.md`.** The audit owns timeframe,
> universe, and strategy decisions from here; this TODO file is now
> historical for the implementation phases.

---

## Deferred (v2+)

- [ ] Persistent monitor daemon — survives Claude Code session end
- [ ] AutoGate — replace ManualGate with automated approval + circuit breaker
- [ ] breakout strategy — add as third pluggable strategy
- [ ] sentiment strategy — add as fourth pluggable strategy
- [ ] Alpha Vantage MCP for macro context
- [ ] Proactive monitor alert if top-ranked asset changes
- [ ] Web UI / dashboard for trade history
- [ ] Migrate SQLite → Postgres (RDS) for AWS deployment

### Broker / Instrument Generalization Refactor

See `docs/SYSTEM_DESIGN.md` §3.9 for the full analysis. The logic layer (preflight, strategy, storage, monitor rules) is already generic. The tools layer and monitor I/O are tightly coupled to Capital.com response shapes.

- [ ] Define `BrokerClient` Protocol + normalized data types in `broker/protocol.py`
  - [ ] `Position`, `OHLCBar`, `AccountInfo`, `Sentiment`, `OrderRequest`, `ExecutionResult` dataclasses
  - [ ] `BrokerClient` Protocol with typed method signatures
- [ ] Wrap `CapitalClient` in an adapter that implements the Protocol and translates all Capital.com response shapes into the normalized types
- [ ] Refactor `session_tools.py`, `scan_tools.py`, `trade_tools.py` to work against normalized types only
- [ ] Refactor `monitor/monitor.py` I/O to work against normalized types only
- [ ] Move Capital.com-specific execution quirks into the adapter (create→confirm two-step, stop_distance vs stop_level for trailing stops)
- [ ] Make `get_sentiment()` return `None` when not available rather than erroring — tools handle absence gracefully
- [ ] Resolve LONG/SHORT vs BUY/SELL inconsistency — use LONG/SHORT throughout; adapter translates to broker strings
