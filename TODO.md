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

## Phase 1 — Storage Layer

Foundation for all other components. Everything writes here.

- [ ] `storage/db.py`
  - [ ] SQLite connection management (path from env or default `./data/trading.db`)
  - [ ] Schema creation: `cycle_snapshots`, `trades`, `reasoning_traces`
  - [ ] `init_db()` — idempotent, safe to call on every startup
- [ ] `storage/repository.py`
  - [ ] `save_cycle_snapshot(cycle_id, asset, strategy, account_bal, positions, market_data)`
  - [ ] `save_trade(cycle_id, asset, direction, size, entry_price, stop_loss, take_profit, broker_ref)` → status: PROPOSED
  - [ ] `update_trade_status(trade_id, status)` — APPROVED / REJECTED / EXECUTED / FAILED
  - [ ] `save_reasoning_trace(cycle_id, prompt_tokens, output_tokens, reasoning, tool_calls)`
  - [ ] `get_open_trades()` → used by session start and monitor
  - [ ] `get_session_summary(session_id)` → P&L, win rate, max drawdown
- [ ] Unit tests — `tests/unit/test_repository.py`
  - [ ] All CRUD operations against in-memory SQLite (`":memory:"`)
  - [ ] Status transition tests
  - [ ] Summary calculation tests

---

## Phase 2 — Broker Wrapper

Thin wrapper re-exporting `CapitalClient`. Validates all needed API calls work against demo.

- [ ] `broker/capital_client.py`
  - [ ] Confirm `capital-mcp-server` importable as local path dependency
  - [ ] Add path dependency to `pyproject.toml`: `capital-mcp-server @ file:///home/chris/dev/capital-mcp-server`
  - [ ] Re-export `CapitalClient` — confirm import works end-to-end
- [ ] Integration smoke test — `tests/integration/test_broker.py`
  - [ ] `@pytest.mark.integration` — authenticate, ping, get_account_info, get_prices("EURUSD"), get_positions, get_client_sentiment
  - [ ] `@pytest.mark.trade` — create_position + confirm_deal + close_position (EURUSD, minimal size)
- [ ] **Human smoke test SM-02 (partial)** — confirm prices returned are plausible live values

---

## Phase 3 — Risk Preflight

Must be solid before any execution path is wired up. Unit-test exhaustively.

- [ ] `risk/preflight.py`
  - [ ] `validate_entry_proposal(proposal: dict, strategy_config: dict, global_config: dict) -> PreflightResult`
  - [ ] `validate_monitor_decision(decision: dict, position: dict, strategy_config: dict, global_config: dict) -> PreflightResult`
  - [ ] `PreflightResult` — dataclass: `passed: bool`, `violations: list[str]`
  - [ ] Checks: stop_loss present and within max_pct, size within min/max, R:R ratio met, margin floor not breached, max open positions not exceeded, trailing stop distance within bounds
  - [ ] Trailing stop ratchet check — can only move in profitable direction
- [ ] Unit tests — `tests/unit/test_preflight.py`
  - [ ] Valid proposal passes all checks
  - [ ] Missing stop_loss is rejected
  - [ ] Size above max_size is rejected
  - [ ] R:R ratio below minimum is rejected
  - [ ] Trailing stop moving against profit direction is rejected
  - [ ] Margin floor breach halts execution
  - [ ] Each violation produces a specific, readable message

---

## Phase 4 — Strategy Loader + Prompt Config

Defines the pluggable strategy interface. Both strategies must load and validate correctly.

- [ ] `strategy/loader.py`
  - [ ] `load_strategy(name: str, config_dir: Path) -> Strategy` — loads YAML + MD pair
  - [ ] `list_strategies(config_dir: Path) -> list[str]` — discovers all valid strategy names
  - [ ] `Strategy` dataclass: name, description, yaml config, md prompt text
  - [ ] Schema validation on YAML load — fail loudly if required fields missing
  - [ ] `load_base_prompt(config_dir: Path) -> str` — loads `_base.md`
  - [ ] `load_scan_prompt(config_dir: Path) -> str` — loads `scan.md`
- [ ] `config/strategies/_base.md` — define output schema contract and hard rules for Claude
- [ ] `config/strategies/scan.md` — define market scan prompt and ranking criteria
- [ ] `config/strategies/momentum.md` — momentum-specific reasoning instructions
- [ ] `config/strategies/mean_reversion.md` — mean reversion-specific reasoning instructions
- [ ] Unit tests — `tests/unit/test_strategy_loader.py`
  - [ ] Both strategies load without error
  - [ ] Missing YAML raises clear error
  - [ ] Missing MD raises clear error
  - [ ] Invalid YAML (missing required field) raises clear error
  - [ ] `list_strategies()` returns both names, excludes `_base` and `scan`

---

## Phase 5 — Agent Layer (Monitor Use Only)

Used exclusively by `monitor.py`. Not involved in the entry flow.

- [ ] `agent/claude_client.py`
  - [ ] `call_claude(system_prompt: str, user_prompt: str, model: str) -> str`
  - [ ] Anthropic SDK wrapper — handles API errors, logs token usage
  - [ ] Returns raw response text — parsing is done by `output_parser.py`
- [ ] `agent/prompt_builder.py`
  - [ ] `build_monitor_prompt(positions, prices, strategy: Strategy, base_prompt: str) -> tuple[str, str]`
  - [ ] Returns (system_prompt, user_prompt) ready for `claude_client`
  - [ ] Assembles: base rules + strategy MD + current positions + price data
- [ ] `agent/output_parser.py`
  - [ ] `parse_entry_response(raw: str) -> dict` — validates against entry schema
  - [ ] `parse_monitor_response(raw: str) -> dict` — validates against monitor schema
  - [ ] Raises `OutputParseError` with message on schema violation
  - [ ] `contra_indicators` and `stop_loss` required — reject if absent
- [ ] Unit tests — `tests/unit/test_agent.py`
  - [ ] `output_parser` accepts valid entry JSON
  - [ ] `output_parser` rejects JSON missing `contra_indicators`
  - [ ] `output_parser` rejects JSON missing `stop_loss`
  - [ ] `output_parser` rejects malformed JSON
  - [ ] `prompt_builder` produces non-empty system + user prompts
  - [ ] `prompt_builder` includes strategy name in output

---

## Phase 6 — Monitor

Autonomous position management subprocess. Runs only during an active session.

- [ ] `monitor/monitor.py`
  - [ ] Reads `MONITOR_INTERVAL_SECONDS` from env (default 60)
  - [ ] Main loop: fetch positions → if none, sleep and continue
  - [ ] For each open position: fetch current price, build monitor prompt, call Claude
  - [ ] Parse + validate response via `output_parser`
  - [ ] Run `preflight.validate_monitor_decision` — skip execution if fails, log violation
  - [ ] Execute: HOLD (log only), ADJUST (`update_position`), CLOSE (`close_position`)
  - [ ] Write `reasoning_trace` to DB on every cycle — including HOLD decisions
  - [ ] Write `audit.jsonl` sidecar entry on every action
  - [ ] Graceful shutdown on SIGTERM — finish current cycle, then exit
- [ ] Integration tests — `tests/integration/test_monitor.py`
  - [ ] `@pytest.mark.trade` — open a demo position, run one monitor cycle, verify trace written to DB
- [ ] **Human smoke test SM-06** — observe monitor log output for at least 2 cycles, confirm reasoning traces appear in DB

---

## Phase 7 — MCP Tools + Server

The FastMCP server that Claude Code talks to.

- [ ] `tools/session_tools.py`
  - [ ] `start_session` — authenticate, check positions, load config, start monitor subprocess, return session summary
  - [ ] `end_session(close_positions: bool)` — stop monitor, optionally close all, write summary to DB
  - [ ] `get_session_status` — positions, P&L, monitor alive, session duration
- [ ] `tools/scan_tools.py`
  - [ ] `scan_markets(watchlist: str | None)` — fetch ATR + trend slope + spread for each instrument, return ranked list
  - [ ] `analyze_instrument(epic: str, strategy: str)` — fetch 60x1min + sentiment + open positions, return structured context dict
- [ ] `tools/trade_tools.py`
  - [ ] `validate_proposal(proposal_json: str)` — run preflight, return pass/fail + violations
  - [ ] `execute_trade(proposal_json: str)` — create_position + confirm_deal + log to DB, return deal details
- [ ] `server.py` — register all tools with FastMCP, wire startup logging
- [ ] Unit tests — `tests/unit/test_tools.py`
  - [ ] Each tool returns expected structure with mocked CapitalClient
  - [ ] `validate_proposal` returns violations correctly
  - [ ] `execute_trade` does not call Capital.com if preflight fails
- [ ] Integration tests — `tests/integration/test_tools.py`
  - [ ] `@pytest.mark.integration` — `start_session`, `scan_markets`, `analyze_instrument`, `get_session_status`
  - [ ] `@pytest.mark.trade` — full cycle: start → scan → analyze → validate → execute → end
- [ ] **Human smoke tests SM-01 through SM-08**

---

## Phase 8 — GitHub Actions

Automated integration tests on every push.

- [ ] `.github/workflows/integration.yml`
  - [ ] Trigger: push to any branch
  - [ ] Python 3.12, install dependencies including local capital-mcp-server path
  - [ ] Run: `pytest tests/unit/ -v` (always)
  - [ ] Run: `pytest tests/integration/ -m integration -v` (always, using demo API secrets)
  - [ ] Exclude `@pytest.mark.trade` from CI
  - [ ] Required secrets: `CAPITAL_BASE_URL`, `CAPITAL_API_KEY`, `CAPITAL_IDENTIFIER`, `CAPITAL_API_KEY_PASSWORD`, `ANTHROPIC_API_KEY`
- [ ] Verify workflow passes on first push after Phase 7 complete

---

## Phase 9 — MCP Config Wiring

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
