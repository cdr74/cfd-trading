# CFD Trading System — System Design

Technical reference for architecture, design decisions, configuration, and implementation status.  
For operational instructions see `docs/USER_GUIDE.md`. For algorithm definitions see `docs/CFD_STRATEGY_CATALOG.md`.

> **Abbreviations & terms:** see [`docs/GLOSSARY.md`](GLOSSARY.md) — single source of truth for every acronym used in this repo.

---

## Contents

1. [Architecture](#1-architecture)
2. [Session Lifecycle](#2-session-lifecycle)
3. [Design Decisions](#3-design-decisions)
4. [Repository Structure](#4-repository-structure)
5. [MCP Tools](#5-mcp-tools)
6. [Configuration Reference](#6-configuration-reference)
7. [SQLite Schema](#7-sqlite-schema)
8. [Implementation Status](#8-implementation-status)
9. [Deferred — v2+](#9-deferred--v2)

---

## 1. Architecture

### 1.1 Component Map

| Component | Technology | Role |
|-----------|-----------|------|
| Claude Code / Claude Desktop | Anthropic Claude Code | Human-facing conversational UI — drives the session phase by phase, presents proposals, holds conversation context |
| cfd-trading MCP server | Python, FastMCP (this repo) | Exposes session, scan, and trade tools to Claude Code; houses preflight, storage, monitor subprocess management |
| Capital.com client | Python (`CapitalClient` from `capital-com-client`) | Market data, trade execution, account/position queries |
| Monitor process | Python (`monitor.py`) | Autonomous position management loop; runs as subprocess during active session only (v1) |
| State / Audit | SQLite + JSONL | Trade history, cycle snapshots, reasoning traces, audit log |
| Strategy config | YAML + Markdown | Risk bounds (YAML) and prompt modules (MD) per strategy — pluggable, no code changes needed |

### 1.2 Integration Flow

Two parallel flows share the same `CapitalClient` and SQLite database:

```
Entry Flow (manual, conversational)          Monitor Flow (autonomous, subprocess)
─────────────────────────────────            ──────────────────────────────────────
Human → Claude Code (outer Claude)           monitor.py (background, every 60s)
  │                                            │
  ├─ calls scan_markets tool                   ├─ CapitalClient.get_positions()
  │    └─ CapitalClient fetches data           ├─ CapitalClient.get_prices()
  │    └─ returns ranked instruments           ├─ rule engine (strategy YAML)
  │                                            │    └─ HOLD / ADJUST / CLOSE
  ├─ Claude reasons, human selects            ├─ CapitalClient.update/close_position()
  │                                            └─ repository.py (audit log)
  ├─ calls analyze_instrument tool
  │    └─ returns structured context
  │       (EMA_9/21, z-score, ATR, sentiment, suggested_size)
  │
  ├─ Claude reasons, proposes strategy
  │    human agrees / modifies
  │
  ├─ calls validate_proposal tool
  │    └─ preflight.py checks vs risk.yaml
  │
  ├─ Claude presents to human → approved
  │
  └─ calls execute_trade tool
       └─ CapitalClient.create_position()
       └─ repository.py (trade log)
```

**Key distinction:** In the entry flow, Claude Code does all reasoning. In the monitor flow, `monitor.py` evaluates strategy YAML rules mechanically — no AI call is made.

---

## 2. Session Lifecycle

### 2.1 Session Phases

```
start_session
  └─ authenticate with Capital.com
  └─ check for open positions
  └─ load config (risk.yaml + watchlist.yaml + strategies)
  └─ start monitor.py subprocess

SCAN PHASE (conversational, multi-turn)
  └─ scan_markets → fetch ATR, trend slope, spread for watchlist
  └─ Claude presents top instruments with rationale
  └─ human selects instrument (or requests re-scan)

STRATEGY PHASE (conversational, multi-turn)
  └─ analyze_instrument(epic, strategy) → fetch 60×1min + sentiment + positions
  └─ Claude proposes strategy based on current conditions
  └─ human agrees, modifies, or redirects

TRADE PHASE (conversational, single approval gate)
  └─ Claude produces proposal JSON (action, direction, size, SL, TP, reasoning)
  └─ validate_proposal → preflight check vs risk.yaml
  └─ Claude presents to human with full rationale
  └─ human approves → execute_trade → Capital.com create_position

MONITOR PHASE (autonomous, background)
  └─ monitor.py runs every MONITOR_INTERVAL_SECONDS (default 60)
  └─ evaluates YAML rules → HOLD / ADJUST / CLOSE
  └─ all actions within risk bounds, no human gate
  └─ all decisions written to cycle_snapshots + audit.jsonl

end_session(close_positions: bool)
  └─ stop monitor subprocess
  └─ if close_positions=True: close all open positions via Capital.com
  └─ if close_positions=False: leave open — stop losses already registered at broker
  └─ generate session summary → write to SQLite + print to Claude Code
```

### 2.2 Session End — Position Handling

v1 is session-bound: the monitor runs only while Claude Code is active. When the session ends:

- **`close_positions=True`** — all positions closed via Capital.com before exit
- **`close_positions=False`** — positions remain open; stop losses registered at Capital.com protect them without any monitoring process running

A persistent background daemon (surviving Claude Code exit) is out of scope for v1.

---

## 3. Design Decisions

### 3.1 Interface and Trigger

| Decision | Value |
|----------|-------|
| Primary interface | Claude Code (or Claude Desktop) — conversational UI |
| Entry trigger | Human initiates session; multi-turn conversation drives scan → strategy → trade |
| Entry execution | Human-confirmed — blocking approval gate before any order placed |
| Monitor execution | Autonomous within strategy risk bounds — no human gate for adjustments |
| Trading timeframe | Intraday (1-min bars; 5-min planned for breakout strategy) |
| Session scope | v1: monitor lives with session; positions can stay open with registered SL |
| Deployment | WSL2 on Windows 11 (local); portable to AWS later |

### 3.2 Capital.com Integration

| Decision | Value |
|----------|-------|
| Broker access | `CapitalClient` imported directly from `capital-mcp-server` package |
| No MCP-over-stdio | cfd-trading does NOT call capital-mcp-server via MCP protocol internally |
| Market data | `CapitalClient.get_prices`, `get_historical_prices` |
| Account state | Always fetched live — never trusted from cache |
| Execution | `CapitalClient.create_position`, `update_position`, `close_position` |
| Demo / sandbox | `CAPITAL_BASE_URL=https://demo-api-capital.backend-capital.com` for all testing |

### 3.3 Claude Usage

| Phase | Which Claude | How |
|-------|-------------|-----|
| Scan, strategy selection, trade proposal | Claude Code (active session) | Calls MCP tools, reasons over returned data, presents conversational summary + proposal JSON to human |
| Monitor (autonomous) | None — rule engine only | `monitor.py` evaluates YAML rules mechanically; zero Anthropic API calls |

No Anthropic API key is required at runtime. The original `agent/` layer (claude_client, prompt_builder, output_parser) was removed from scope — the monitor is a pure rule engine.

### 3.4 Proposal Presentation Format

Claude Code presents trade proposals in two parts:

1. **Conversational summary** — plain-language rationale: what it sees, why it's proposing this trade, what could go wrong (`contra_indicators`), and the key risk parameters.
2. **Proposal JSON** — the structured contract (see §3.6) in a code block, passed directly to `validate_proposal` then `execute_trade` if approved.

The JSON is the authoritative record. The conversation is the presentation layer. Both are always present.

### 3.5 State Persistence

| Decision | Value |
|----------|-------|
| Strategy config | YAML + Markdown files, Git-versioned under `config/strategies/` |
| Live account state | Always fetched live from Capital.com — never trusted from cache |
| Trade history | SQLite — `trading.db` (gitignored, lives on Linux FS in WSL2) |
| Audit / reasoning | SQLite (`reasoning_traces` table) + JSONL sidecar for easy grep |
| File location | Keep `trading.db` on Linux FS (`~/` or `/home/...`) not `/mnt/c/` — I/O performance |
| Migration path | SQLite → Postgres (RDS) when moving to AWS |

### 3.6 Proposal JSON Schema

```json
{
  "cycle_id": "string",
  "timestamp": "ISO8601",
  "asset": "EURUSD",
  "strategy": "momentum",
  "decision": {
    "action": "OPEN | CLOSE | MODIFY | NONE",
    "direction": "LONG | SHORT | null",
    "size": 1.5,
    "entry_type": "MARKET | LIMIT | STOP",
    "entry_level": null,
    "stop_loss": {
      "type": "HARD | TRAILING",
      "value": 1.0780,
      "pct_from_entry": 2.1
    },
    "trailing_stop": {
      "enabled": true,
      "initial_distance_pct": 1.2,
      "update_interval_min": 1
    },
    "take_profit": {
      "initial_value": 1.0870,
      "dynamic": true
    },
    "time_exit": {
      "latest_close": "session_end - 30min"
    }
  },
  "reasoning": {
    "market_context": "string",
    "signal_basis": "string",
    "risk_considerations": "string",
    "contra_indicators": "string"
  },
  "data_used": {
    "candles": "60x1min EURUSD",
    "sentiment": "62% long",
    "positions_open": 0
  }
}
```

`contra_indicators` and `stop_loss` are always required. `action: NONE` is a valid explicit output — Claude may decline to trade.

### 3.7 Monitor Rule Engine

Rules are evaluated in priority order every `MONITOR_INTERVAL_SECONDS` (default 60 seconds):

| Priority | Rule | Condition | Action |
|----------|------|-----------|--------|
| 1 | Hard stop | Price crosses `stop_loss.value` | CLOSE |
| 2 | Trailing stop | momentum: ATR₁₄@entry × 1.5 fixed, **ratchet-only**; intraday_continuation: **dynamic Chandelier** (ATR₁₄ recomputed each completed bar, *may loosen* — see §3.7.1); MR & ORB: disabled | ADJUST (per-strategy trail mode) |
| 3 | Take profit | Price reaches `take_profit.initial_value` | CLOSE |
| 4 | **Signal exit** | Deterministic signal reversal for the position's strategy (see below) | CLOSE |
| 5 | Time exit | `session_end - close_minutes_before_session_end` | CLOSE |
| 6 | Default | None of the above | HOLD |

All decisions are written to `cycle_snapshots` (DB) and `audit.jsonl` (ADJUST/CLOSE only).

**Rule 4 — Signal exit (added & implemented 2026-05-15).**
Previously the monitor did no indicator math and signal-based bail-out was left to
Claude in the scan/analyze conversation — which runs on no fixed cadence, so a
losing trade could sit until the hard stop. Rule 4 makes signal-bail a deterministic,
every-60s monitor rule. Per-strategy predicate:

| Strategy | Signal-exit predicate | Source |
|---|---|---|
| `mean_reversion` | `|z| ≤ zscore_exit_threshold` (z returned to midline → take the reversion) | exists in signal state today (`check_exit`) |
| `momentum` | EMA-fast crosses back through EMA-slow against the open position (trend over) | **new predicate** — must be written (today returns `None`) |
| `orb` | none — the breakout is one-shot per session; no reversal to bail on | `None` (correct as-is) |

The signal logic is **streaming/stateful** (incremental per-bar deques + ADX/EMA
state). It is promoted out of the backtest package into a **shared module
(`strategy/signal_engine.py`) imported by both `monitor.py` and the backtest engine** —
one implementation, so live and backtest cannot drift. The monitor therefore changes
from fetching one price bar per cycle to: on start/restart, **back-fill a warm-up
window** per open-position epic at the strategy's bar resolution and replay it to
build the signal state; each cycle, fetch the new bar(s), `update()` the state, then
evaluate the rules. State is kept per `dealId` for the monitor process lifetime and
deterministically re-seeded on restart.

**3c implementation resolutions (decided & implemented 2026-05-15):**

- **O1 — bar resolution is a strategy property.** Each strategy YAML carries
  `resolution:` (momentum **`M30`**, mean_reversion `M1`, orb `M15` — the
  catalog-intended horizons). The live monitor and the backtest runner both read it
  as the single source of truth; the backtest CLI `--resolution` remains an explicit
  experiment override. (Momentum was moved to M30 on 2026-05-15: the ITSM effect is a
  ~30-min phenomenon, M1 data is only ~3.5 months deep, and the old M1 strict-default
  yielded ~44 trades/3-yr — a resolution/parameter mismatch, not an algorithm fault.
  M30 over the 3-yr re-baseline fires ~1,770 trades. See `RESEARCH.md` and
  `CFD_STRATEGY_CATALOG.md` §5.2.)
- **O2 — `entry_atr` is captured during the O3 warm-up backfill** (collapsed into
  O3; *no* trades-DB schema change, *no* execution-tool change). The monitor's
  first-sighting/restart backfill replays a window that starts *before* the
  position's `entry_ts`; `signal_state.atr` is snapshotted at the entry bar. Because
  this is the same shared `signal_engine` the backtest uses, the value is
  bit-identical to the backtest's `entry_atr` — exact parity, zero extra hot-path
  cost (it is the already-required backfill replay), restart-deterministic.
- **O3 — only momentum & mean_reversion get live signal state.** ORB's
  `check_exit()` is always `None` and ORB trailing is disabled, so the monitor
  passes `signal_state=None` for ORB and skips warm-up/session wiring for it; ORB
  exits via the price/time rules (hard stop / TP / time-exit) exactly as before.

The time-exit rule reads "now" from an injectable parameter (default = real UTC clock,
so live is unchanged). The backtest injects bar time — see §3.10. Same code path,
same priority order, both contexts.

#### 3.7.1 — Dynamic Chandelier trail (`intraday_continuation` / D3-BR3)

> **Status:** architecture decided 2026-05-19 (strategy-debate Part 2 → Fork A,
> sub-design concluded); **not yet implemented**. Full record:
> `docs/STRATEGY_AUDIT.md` Part 2. The first deliberate change to the shared
> exit path since the 2026-05-15 rebuild — accepted with eyes open: production
> exit logic is modified before D3 has shown any edge.

Per-bar volatility-recomputed trailing for the `intraday_continuation` strategy.
The rule engine gains a **per-strategy trail mode** — this is the first
non-ratchet trail; it *may move away from price* when volatility expands:

- **Formula** (`k_trail = 1.5`, the existing trail multiple — no new tunable):
  - LONG  `stop = max_high_since_entry − 1.5 · ATR₁₄(closed bars)`
  - SHORT `stop = min_low_since_entry  + 1.5 · ATR₁₄(closed bars)`
- **Completed bars only.** ATR₁₄ uses the engine's existing Wilder primitive
  over *closed* M15 bars — never the in-progress bar or a live tick. This is
  what keeps the bar-stepped backtest and the polled live monitor on identical
  inputs.
- **Stateless replay.** The stop is a pure function of the completed-bar series
  plus entry — re-derived in full every evaluation, never accumulated as mutable
  state. A missed or slow monitor poll cannot desync; live and backtest converge
  by replay.
- **Trigger logic unchanged.** Only the stop *level* changes from frozen to
  recomputed. The existing intrabar-touch convention and the hard-stop→trail
  activation gating are reused verbatim — no new activation threshold.
- **One shared pure function.** The Chandelier computation lives in
  `strategy/signal_engine.py` (the existing shared module imported by both
  `monitor.py` and the backtest engine) — parity by construction, not
  coincidence. The golden-trace parity test (`tests/unit/test_parity.py`) is
  extended to assert the **full per-bar stop series**, not just the final exit.
  A backtest-only dynamic trail was explicitly rejected as fidelity-false (it
  reproduces the §3.10 defect that invalidated the prior audit).

### 3.8 Strategy Pluggability

Each strategy is a self-contained pair of files in `config/strategies/`:
- `<name>.yaml` — risk bounds (size, stop %, trailing stop params, R:R ratio, time exit)
- `<name>.md` — prompt module injected into Claude's context for this strategy

Adding a new strategy requires no code changes — drop two files, restart. `strategy/loader.py` discovers all YAML files at runtime and validates them against a required-fields schema on load. Misconfigured strategies fail loudly at startup.

### 3.9 Broker Generalization — Deferred

The logic layer (`risk/preflight.py`, `strategy/loader.py`, `storage/`, `monitor/monitor.py`) is entirely generic. The tools layer is tightly coupled to Capital.com response shapes — field names, the two-step create→confirm execution flow, and trailing stop as `stop_distance` in points rather than price level.

The planned fix is a `BrokerClient` Protocol in `broker/protocol.py` with normalized data types, and a thin Capital.com adapter. Estimated effort: 1–2 days. Deferred to v2.

### 3.10 Backtesting Architecture

> **Status:** Redesigned **and implemented** 2026-05-15 after the time-exit fidelity
> defect (the engine never passed `session_end_time`, so intraday close never fired
> and momentum/ORB held multi-day/multi-week positions). Engine + monitor now share
> this exit path (Phases 3–5 done; live==backtest parity test in
> `tests/unit/test_parity.py`). All prior backtest results were invalidated/deleted
> and a clean re-baseline regenerated. The subsequent strategy audit
> (`docs/STRATEGY_AUDIT.md`) closed 2026-05-18 on the Phase A kill-criterion —
> MR dropped (non-viable); momentum & ORB unvalidated (no edge survived
> Deflated-Sharpe). System is pre-pivot; no strategy is deploy-ready.

After the 2026-05-15 redesign the **entire exit path is deterministic and shared** —
hard stop, trailing, TP, **signal-exit (§3.7 rule 4)**, and time-exit all run through
one code path used by both the live monitor and the backtest. Only the *entry*
decision is non-replayable (Claude's reasoning), and is approximated.

**Fidelity contract:**

| Layer | Live | Backtest |
|-------|------|---------|
| Entry | Claude reasons over `analyze_instrument` | Deterministic proxy: EMA crossover / z-score / ORB — acknowledged approximation of Claude's entry |
| Exit | mechanical monitor: hard stop → trailing → TP → signal-exit → time-exit | **identical rules, same shared code** (`evaluate_position` + `signal_engine`) |
| Session | Human sets `session_end_time` at `start_session`; monitor closes 30 min before | Global `session_close_utc` (default **21:00 UTC**) per simulated UTC day; same time-exit |
| Data | Capital.com API | Local SQLite `ohlc_bars` (MT5 fetch on Windows) |

The earlier "mechanical-monitor floor / MR scores worse than live" caveat is
**superseded**: the z-midline reversion exit (and momentum's cross-back) are now
first-class deterministic monitor rules present in *both* live and backtest, not a
discretionary Claude action the backtest omits. The only residual divergence is the
entry approximation and any ad-hoc human CLOSE outside the defined rules. The former
backtest-only `check_exit()` hold-cap hack is dropped; its useful half (z-midline) is
promoted into the shared `signal_engine`.

**Backtestable time-exit.** `evaluate_position()` gains an optional injected
`now: datetime` (default `None` → `datetime.now(UTC)`, so live behaviour is unchanged).
The backtest passes the current bar's UTC timestamp; each simulated UTC day's
`session_end_time` = that date at `session_close_utc`, and the existing per-strategy
`time_exit.close_minutes_before_session_end` (30) flattens before it. No position is
ever carried overnight or over a weekend. A unit test asserts the live path and the
backtest path return identical `(action, reason, stop)` on shared fixtures — the
anti-drift guarantee.

**Post-rebuild change — dynamic Chandelier trail (decided 2026-05-19, pre-code).**
The one deliberate addition to this shared exit path since the rebuild: the
`intraday_continuation` strategy's per-bar volatility-recomputed trail (§3.7.1).
It is built into the shared `signal_engine` and mirrored in the live monitor; the
parity test is extended to assert the full per-bar stop series. Backtest-only was
rejected as fidelity-false. System remains pre-pivot — no strategy is
deploy-ready; see `docs/STRATEGY_AUDIT.md` Part 2.

OHLC data comes from MetaTrader 5 on the Capital.com demo account. The
`BACKTEST_MODE=true` env var blocks all live API calls at the `CapitalClient` level
during backtest runs.

See `docs/BACKTESTING.md` for full data layer detail, engine design, test suite, and results guide.

---

## 4. Repository Structure

```
cfd-trading/
├── config/
│   ├── risk.yaml                    # global hard limits
│   ├── watchlist.yaml               # asset universe (forex, indices, commodities, crypto)
│   └── strategies/
│       ├── _base.md                 # proposal schema + hard rules for Claude Code context
│       ├── scan.md                  # market scan prompt
│       ├── momentum.yaml / .md      # S1 — trend-following (resolution: M30)
│       ├── mean_reversion.yaml / .md  # S2 — range-bound (resolution: M1)
│       └── orb.yaml / .md           # S5 — Opening Range Breakout (resolution: M15)
├── data/                            # gitignored
│   ├── trading.db
│   └── audit.jsonl
├── docs/
│   ├── SYSTEM_DESIGN.md             # this file
│   ├── USER_GUIDE.md                # operational guide
│   ├── CFD_STRATEGY_CATALOG.md      # algorithm design and math
│   ├── BACKTESTING.md               # backtesting framework, tests, results
│   ├── RESEARCH.md                  # empirical parameter research (M30/ORB basis)
│   └── GLOSSARY.md                  # abbreviations & terms (single source of truth)
├── src/cfd_trading/
│   ├── server.py                    # FastMCP entry point
│   ├── backtest/
│   │   ├── engine.py                # walks bars, calls signal_engine + monitor rule engine
│   │   ├── run.py                   # CLI entry point
│   │   ├── aggregate.py             # M1 → M15/M30/… in-process aggregation
│   │   ├── sessions.py              # per-instrument UTC session-open table
│   │   ├── spreads.py               # per-instrument typical spread (pts)
│   │   └── tune_momentum_gap.py     # EMA-gap sweep utility
│   ├── broker/
│   │   └── capital_client.py        # re-exports CapitalClient; BACKTEST_MODE guard
│   ├── monitor/
│   │   └── monitor.py               # rule engine subprocess — no AI calls
│   ├── risk/
│   │   └── preflight.py             # validates entry proposals vs YAML bounds
│   ├── storage/
│   │   ├── db.py                    # SQLite init + schema
│   │   └── repository.py            # CRUD + get_bars() for backtesting
│   ├── strategy/
│   │   ├── loader.py                # discovers + validates strategy YAML+MD pairs
│   │   └── signal_engine.py         # SHARED streaming signals + signal-exit
│   │                                #   (imported by monitor AND backtest — no drift)
│   └── tools/
│       ├── session_tools.py         # start_session, end_session, get_session_status
│       ├── scan_tools.py            # scan_markets, analyze_instrument
│       └── trade_tools.py           # validate_proposal, execute_trade
├── tests/
│   ├── unit/                        # 329 tests — preflight, monitor, tools, backtest,
│   │                                #   signal_engine, parity (live↔backtest)
│   └── integration/                 # against Capital.com demo API
├── backtest/                        # Windows-side MT5 scripts (not in src/ — run on Windows Python)
│   ├── fetch_ohlc.py                # MT5 bar fetch → ohlc_bars
│   └── probe_history.py             # MT5 native-history depth probe
├── integration-test/
│   ├── mcp-start.sh / mcp-stop.sh
│   ├── mcp-status.sh / mcp-fix-config.sh
│   └── SMOKE_TESTS.md
├── pyproject.toml
├── .env.example
├── CLAUDE.md                        # Claude Code collaboration rules
├── README.md                        # project intro and doc index
└── TODO.md                          # implementation progress tracking
```

---

## 5. MCP Tools

| Tool | Parameters | Does |
|------|-----------|------|
| `start_session` | — | Authenticate, check open positions, load config, start monitor subprocess |
| `scan_markets` | `watchlist?` | Fetch ATR + trend slope + spread/ATR for each instrument → return ranked list |
| `analyze_instrument` | `epic, strategy` | Fetch 60×1min + sentiment + positions → return EMA_9/21, z-score, ATR, vol-scaled size suggestion |
| `validate_proposal` | `proposal_json` | Preflight check vs risk.yaml → pass/fail + specific violations |
| `execute_trade` | `proposal_json` | `create_position` + confirm + log to DB → return deal details |
| `get_session_status` | — | Current positions, unrealised P&L, monitor alive, session duration |
| `end_session` | `close_positions: bool` | Stop monitor, optionally close all positions, write session summary to DB |

---

## 6. Configuration Reference

### 6.1 config/risk.yaml — Global Hard Limits

```yaml
global:
  max_loss_pct_per_trade: 5.0   # hard ceiling — never exceed
  margin_floor_pct: 20.0        # halt all trading below this
  max_open_positions: 3
  session_end_close: true
```

### 6.2 config/watchlist.yaml — Asset Universe

```yaml
forex:       [EURUSD, GBPUSD, USDJPY, EURGBP]
indices:     [US500, DE40, UK100]
commodities: [GOLD, XBRUSD]
crypto:      [BTCUSD, ETHUSD]
```

### 6.3 Strategy YAML Schema

```yaml
name: momentum
description: Trend-following strategy targeting breakouts with trailing stop management
resolution: M30                  # bar resolution — single source of truth for the
                                 # live monitor AND the backtest (momentum M30,
                                 # mean_reversion M1, orb M15). --resolution overrides
                                 # only for backtest experiments.
entry:
  min_size: 0.1
  max_size: 5.0
risk:
  target_risk_pct: 1.0           # % of account balance to risk per trade
  stop_loss:
    type: HARD
    default_pct: 2.0
    max_pct: 5.0
  trailing_stop:
    enabled: true
    atr_multiplier: 1.5          # distance = ATR14@entry × 1.5, fixed for the
                                 # trade, ratchet-only (resolved 2026-05-15 —
                                 # superseded the old min/max_distance_pct fields).
                                 # MR & ORB set trailing_stop.enabled: false instead.
    update_interval_min: 1
  take_profit:
    dynamic: true
    min_rr_ratio: 1.5
    max_pct: 10.0
  position_scaling:
    enabled: true
    max_adds: 2
    max_total_size: 10.0
  time_exit:
    enabled: true
    close_minutes_before_session_end: 30
```

`suggested_size` in `analyze_instrument` is computed as `target_risk_pct / 100 × account_balance / ATR`. Claude should adjust for the actual stop distance in the proposal.

### 6.4 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CAPITAL_BASE_URL` | — | Demo or live Capital.com API URL |
| `CAPITAL_API_KEY` | — | Capital.com API key |
| `CAPITAL_IDENTIFIER` | — | Capital.com login email |
| `CAPITAL_API_KEY_PASSWORD` | — | Capital.com API password |
| `MCP_TRANSPORT` | `stdio` | Set to `streamable-http` in container |
| `MCP_HOST` | `127.0.0.1` | Set to `0.0.0.0` in container |
| `MCP_PORT` | `8000` | Set to `8089` in container |
| `SSL_CERTFILE` | — | Path to TLS cert (enables HTTPS when set with `SSL_KEYFILE`) |
| `SSL_KEYFILE` | — | Path to TLS private key |
| `CONFIG_DIR` | `/app/config` | Path to `config/` directory |
| `DB_PATH` | `/app/data/trading.db` | Live trading SQLite database |
| `AUDIT_LOG_PATH` | `/app/data/audit.jsonl` | Audit log for ADJUST/CLOSE decisions |
| `MONITOR_INTERVAL_SECONDS` | `60` | Monitor cycle interval |
| `LOG_LEVEL` | `INFO` | Logging level |
| `BACKTEST_MODE` | — | Set to `true` to block all live API calls |
| `BACKTEST_DB_PATH` | `/mnt/c/Users/chris/dev/trading-data/trading.db` | SQLite DB with `ohlc_bars` for backtesting |

---

## 7. SQLite Schema

```sql
sessions (
  id          TEXT PRIMARY KEY,   -- UUID
  started_at  TEXT NOT NULL,
  ended_at    TEXT,               -- NULL while active
  status      TEXT NOT NULL,      -- ACTIVE | CLOSED
  summary     TEXT                -- JSON session summary
)

cycle_snapshots (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT NOT NULL REFERENCES sessions(id),
  ts          TEXT NOT NULL,
  asset       TEXT NOT NULL,
  strategy    TEXT NOT NULL,
  account_bal REAL,
  positions   TEXT,               -- JSON
  market_data TEXT                -- JSON
)

trades (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT NOT NULL REFERENCES sessions(id),
  cycle_id    TEXT NOT NULL,
  ts          TEXT NOT NULL,
  asset       TEXT NOT NULL,
  strategy    TEXT,
  direction   TEXT NOT NULL,
  size        REAL NOT NULL,
  entry_price REAL,
  stop_loss   REAL,
  take_profit REAL,
  status      TEXT NOT NULL,      -- PROPOSED | APPROVED | REJECTED | EXECUTED | FAILED
  broker_ref  TEXT
)

reasoning_traces (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id    TEXT NOT NULL REFERENCES sessions(id),
  cycle_id      TEXT NOT NULL,
  ts            TEXT NOT NULL,
  prompt_tokens INTEGER,
  output_tokens INTEGER,
  reasoning     TEXT,
  tool_calls    TEXT              -- JSON
)

ohlc_bars (
  epic        TEXT    NOT NULL,   -- watchlist name (EURUSD, GOLD, etc.)
  resolution  TEXT    NOT NULL,   -- "M1" for 1-min bars
  ts          INTEGER NOT NULL,   -- Unix timestamp (seconds UTC)
  open        REAL    NOT NULL,
  high        REAL    NOT NULL,
  low         REAL    NOT NULL,
  close       REAL    NOT NULL,
  volume      INTEGER NOT NULL,
  PRIMARY KEY (epic, resolution, ts)
)
```

---

## 8. Implementation Status

| Phase | Component | Status | Notes |
|-------|-----------|--------|-------|
| 0 | Project scaffold | **Done** | pyproject.toml, config stubs, .env.example |
| 1 | `storage/db.py` + `repository.py` | **Done** | SQLite schema + CRUD; `ohlc_bars` for backtesting; 20 unit tests |
| 2 | `broker/capital_client.py` | **Done** | Re-exports CapitalClient; `BACKTEST_MODE` guard; 4 unit tests + 7 integration tests |
| 3 | `risk/preflight.py` | **Done** | 43 unit tests covering all validation rules |
| 4 | `strategy/loader.py` + all config files | **Done** | Pluggable strategy interface; 22 unit tests |
| 5 | `monitor/monitor.py` | **Done** | Rule-based engine — no AI calls; 25 unit tests |
| 6 | `tools/` + `server.py` | **Done** | 7 MCP tools with FastMCP; streamable-HTTP + HTTPS; 27 unit tests |
| 7 | GitHub Actions CI | **Done** | Unit tests always; integration tests on push with demo secrets; `publish.yml` builds + pushes container image |
| 8 | Container deployment + MCP wiring | **Done** | Podman container on port 8089; Claude Desktop wired; SM-01–SM-11 smoke tests passed |
| 9 | Scan/analysis improvements | **Done** | Removed session labels; added EMA_9/21 + z-score to `analyze_instrument`; vol-scaled `suggested_size` via `target_risk_pct` |
| 10 | Backtesting framework | **Rebuilt (2026-05-15)** | `fetch_ohlc.py` (Windows/MT5); engine + shared `signal_engine` + CLI runner. The old engine never wired the intraday time-exit (all prior results invalidated/deleted). Rebuilt so the engine and the live monitor share ONE deterministic exit path — hard stop → ATR trailing → TP → signal-exit → time-exit (§3.7/§3.10); global `--session-close-utc` session model; resolution is a strategy YAML property. 329 unit tests incl. live↔backtest parity (`tests/unit/test_parity.py`). Clean 3-yr re-baseline regenerated. |

---

## 9. Deferred — v2+

| Item | Notes |
|------|-------|
| Persistent monitor daemon | Survives Claude Code session end — requires OS-level process management |
| AutoGate | Replace manual approval gate with automated circuit breaker |
| Breakout strategy (S3) | Donchian channel; needs 5-min bar fetch + `breakout.yaml` + `breakout.md` |
| Sentiment strategy (S4) | Overlay — fold into momentum and breakout prompt modules |
| Broker generalization | `BrokerClient` Protocol + Capital.com adapter — see §3.9 |
| Alpha Vantage MCP | Macro context for regime filtering |
| Web UI / dashboard | Trade history visualisation |
| SQLite → Postgres (RDS) | AWS deployment migration |
| Strategy parameter tuning | Tune EMA windows and z-score thresholds per instrument on demo data |
| S0 random baseline | Statistical control for strategy promotion gate |
| p-value promotion gate | Demo → live threshold: E_net > 0 across 30+ trades, p < 0.05 |
