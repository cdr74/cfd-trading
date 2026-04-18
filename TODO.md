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

- [x] `broker/capital_client.py` — re-exports `CapitalClient`; import confirmed working
- [x] `pyproject.toml` — `capital-com-mcp-server @ file:///home/chris/dev/capital-mcp-server` added
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
  - [x] Python 3.12; checks out `cdr74/capital-mcp-server` sibling repo and installs it before main package
  - [x] `unit-tests` job: `pytest tests/unit/ -v` (always, no secrets needed)
  - [x] `integration-tests` job: `pytest tests/integration/ -m integration -v` (runs after unit-tests; uses demo API secrets)
  - [x] `@pytest.mark.trade` excluded from CI (not in `-m integration` filter)
  - [x] Required secrets: `CAPITAL_BASE_URL`, `CAPITAL_API_KEY`, `CAPITAL_IDENTIFIER`, `CAPITAL_API_KEY_PASSWORD`, `ANTHROPIC_API_KEY`
  - [x] `pyproject.toml` dependency changed from `file://` local path to plain `capital-com-mcp-server` name
- [ ] Verify workflow passes on first push to GitHub

---

## Phase 8 — MCP Config Wiring

Wire both MCP servers so Claude Code / Claude Desktop can talk to them.

- [ ] Locate Claude config: `%APPDATA%\Claude\claude_desktop_config.json` (Windows)
- [ ] Add `cfd-trading` MCP server entry — `wsl -e python -m cfd_trading.server`
- [ ] Add `capital-mcp-server` entry — `wsl -e python -m capital_mcp_server` (already tested)
- [ ] Set all required env vars in MCP config
- [ ] Verify both servers appear as tool providers in Claude Code / Claude Desktop
- [ ] Run full end-to-end human smoke test: complete session from `start_session` to `end_session`

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
