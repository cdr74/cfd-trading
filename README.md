# CFD Trading System

**Version:** 1.0 — In Development  
**Date:** April 2026  
**Repos:** `github.com/cdr74/cfd-trading` (this repo) · `github.com/cdr74/capital-mcp-server` (broker MCP)  
**Deployment:** WSL2 on Windows 11 (local) — designed to be portable to AWS later

> This README is the single source of truth for all design decisions. Update it as decisions evolve. Hand it to Claude Code at the start of each implementation session.

---

## 1. Objective

An AI-driven CFD trading system using Anthropic Claude as the core reasoning and strategy engine, integrated with Capital.com via a direct Python client. The system supports intraday trading with a human-confirmed, multi-turn conversational entry flow and autonomous position management during an active session.

---

## 2. Architecture Overview

### 2.1 Component Map

| Component | Technology | Role |
|-----------|-----------|------|
| Claude Code / Claude Desktop | Anthropic Claude Code | Human-facing conversational UI — drives the session phase by phase, presents proposals, holds conversation context |
| cfd-trading MCP server | Python, FastMCP (this repo) | Exposes session, scan, and trade tools to Claude Code; houses preflight, storage, monitor subprocess management |
| Capital.com client | Python (`CapitalClient` from capital-mcp-server, imported directly) | Market data, trade execution, account/position queries |
| Monitor process | Python (`monitor.py`) | Autonomous position management loop; runs as subprocess during active session only (v1) |
| State / Audit | SQLite + JSONL | Trade history, cycle snapshots, reasoning traces, audit log |
| Strategy config | YAML + Markdown | Risk bounds (YAML) and prompt modules (MD) per strategy — pluggable, no code changes needed to add a strategy |

### 2.2 Integration Flow

Two parallel flows share the same `CapitalClient` and SQLite database:

```
Entry Flow (manual, conversational)          Monitor Flow (autonomous, subprocess)
─────────────────────────────────            ──────────────────────────────────────
Human → Claude Code (outer Claude)           monitor.py (background, every 60s)
  │                                            │
  ├─ calls scan_markets tool                   ├─ CapitalClient.get_positions()
  │    └─ CapitalClient fetches data           ├─ CapitalClient.get_prices()
  │    └─ returns ranked instruments           ├─ Anthropic SDK (inner Claude call)
  │                                            │    └─ HOLD / ADJUST / CLOSE
  ├─ Claude reasons, human selects            ├─ preflight.py (within risk bounds?)
  │                                            ├─ CapitalClient.update/close_position()
  ├─ calls analyze_instrument tool            └─ repository.py (audit log)
  │    └─ CapitalClient fetches deeper data
  │    └─ returns structured context
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

**Key distinction:** In the entry flow, the outer Claude (Claude Code) does all reasoning. No inner Anthropic API call is made. In the monitor flow, an inner Anthropic API call is made inside `monitor.py` because no human is present.

---

## 3. Session Lifecycle

### 3.1 Session Phases

```
start_session
  └─ authenticate with Capital.com
  └─ check for open positions
  └─ load config (risk.yaml + watchlist.yaml + strategies)
  └─ start monitor.py subprocess
  └─ determine active trading session (London / NY / Asia)

SCAN PHASE (conversational, multi-turn)
  └─ scan_markets → fetch ATR, trend slope, spread for watchlist
  └─ Claude presents top instruments with rationale
  └─ human selects instrument (or requests re-scan)

STRATEGY PHASE (conversational, multi-turn)
  └─ analyze_instrument(epic, strategy) → fetch 60x1min + sentiment + positions
  └─ Claude proposes strategy based on current conditions
  └─ human agrees, modifies, or redirects

TRADE PHASE (conversational, single approval gate)
  └─ Claude produces proposal JSON (action, direction, size, SL, TP, reasoning)
  └─ validate_proposal → preflight check vs risk.yaml
  └─ Claude presents to human with full rationale
  └─ human approves → execute_trade → Capital.com create_position

MONITOR PHASE (autonomous, background)
  └─ monitor.py runs every MONITOR_INTERVAL_SECONDS (default 60)
  └─ fetches positions + prices → calls Claude → HOLD / ADJUST / CLOSE
  └─ all actions within risk bounds, no human gate
  └─ all decisions written to reasoning_traces

end_session(close_positions: bool)
  └─ stop monitor subprocess
  └─ if close_positions=True: close all open positions via Capital.com
  └─ if close_positions=False: leave open — stop losses already registered at broker
  └─ generate session summary (duration, trades, P&L, win rate, max drawdown)
  └─ write summary to SQLite + print to Claude Code
```

### 3.2 Session End — Position Handling

v1 is session-bound: the monitor runs only while Claude Code is active. When the session ends:
- **close_positions=True**: all positions closed via Capital.com before exit
- **close_positions=False**: positions remain open; stop losses already registered at Capital.com protect them without any monitoring process running

A persistent background daemon (surviving Claude Code exit) is out of scope for v1.

---

## 4. Key Design Decisions

### 4.1 Interface & Trigger

| Decision | Value |
|----------|-------|
| Primary interface | Claude Code (or Claude Desktop) — conversational UI |
| Entry trigger | Human initiates session; multi-turn conversation drives scan → strategy → trade |
| Entry execution | Human-confirmed — blocking approval gate before any order placed |
| Monitor execution | Autonomous within strategy risk bounds — no human gate for adjustments |
| Trading timeframe | Intraday (minute-level candles, 1-min / 5-min bars) |
| Session scope | v1: monitor lives with session; positions can stay open with registered SL |
| Deployment | WSL2 on Windows 11 (local). Portable to AWS later. |

### 4.2 Capital.com Integration

| Decision | Value |
|----------|-------|
| Broker access | `CapitalClient` imported directly from `capital-mcp-server` package (local path dep) |
| No MCP-over-stdio | cfd-trading does NOT call the capital-mcp-server via MCP protocol internally |
| Primary market data | `CapitalClient.get_prices`, `get_historical_prices` |
| Account state | Always fetched live — never trusted from cache |
| Execution | `CapitalClient.create_position`, `update_position`, `close_position` |
| Demo / sandbox | `CAPITAL_BASE_URL=https://demo-api-capital.backend-capital.com` for all testing |

### 4.3 Claude Usage Pattern

| Phase | Which Claude | How |
|-------|-------------|-----|
| Scan, strategy selection, trade proposal | Outer Claude (Claude Code session) | Claude Code calls MCP tools, reasons over returned data, presents to human |
| Monitor (autonomous) | Inner Claude (Anthropic SDK call inside monitor.py) | `monitor.py` calls Anthropic API directly, parses structured JSON response |

This avoids calling Claude from inside a Claude tool during the entry flow. The `agent/` layer (claude_client, prompt_builder, output_parser) is used exclusively by `monitor.py`.

### 4.4 State Persistence

| Decision | Value |
|----------|-------|
| Strategy config | YAML + Markdown files, Git-versioned under `config/strategies/` |
| Live account state | Always fetched live from Capital.com — never trusted from cache |
| Trade history | SQLite — `trading.db` (gitignored, lives on Linux FS in WSL2) |
| Audit / reasoning | SQLite (`reasoning_traces` table) + JSONL sidecar for easy grep |
| File location | Keep `trading.db` on Linux FS (`~/` or `/home/...`) not `/mnt/c/` — I/O performance |
| Migration path | SQLite → Postgres (RDS) when moving to AWS — minimal schema change |

### 4.5 Claude Output Schema

Claude always responds with a single JSON object. The orchestrator parses and validates before any action. Two schemas exist: entry (full trade proposal) and monitor (position review).

**Entry Cycle Output:**
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

**Key rules:** `contra_indicators` and `stop_loss` are always required. `action: NONE` is a valid explicit output. Full prompt + response logged to `reasoning_traces` on every cycle.

### 4.6 Dynamic Risk Management

| Decision | Value |
|----------|-------|
| Position monitoring | Claude-driven, every 60s (configurable per strategy) |
| Monitor autonomy | Auto-execute ADJUST/CLOSE within risk bounds — no human gate |
| Trailing stop rule | Ratchet-only — can only move in profitable direction |
| Hard stop | Always present. Max % defined in `risk.yaml`. Enforced mechanically, not by Claude. |
| Take profit | Dynamic — Claude can adjust. Min R:R ratio enforced (strategy YAML) |
| Preflight location | `cfd_trading/risk/preflight.py` — validates both entry proposals and monitor decisions |

### 4.7 Strategy Pluggability

Each strategy is a self-contained pair of files in `config/strategies/`:
- `<name>.yaml` — risk bounds (max size, stop loss %, trailing stop params, R:R ratio, etc.)
- `<name>.md` — prompt module injected into Claude's context for this strategy

Adding a new strategy requires no code changes — drop two files, restart. `strategy/loader.py` discovers all YAML files at runtime and validates them against a fixed schema on load. Misconfigured strategies fail loudly at startup.

---

## 5. Repository Structure

```
cfd-trading/
├── config/
│   ├── risk.yaml                    # global hard limits
│   ├── watchlist.yaml               # asset universe (forex, indices, commodities, crypto)
│   └── strategies/
│       ├── _base.md                 # output schema + hard rules, injected into all prompts
│       ├── scan.md                  # market scan prompt (used by scan_markets + monitor)
│       ├── momentum.yaml / .md      # trend-following strategy
│       └── mean_reversion.yaml / .md  # range-bound strategy
├── data/                            # gitignored
│   ├── trading.db
│   └── audit.jsonl
├── src/cfd_trading/
│   ├── server.py                    # FastMCP entry point — wires all tools
│   ├── tools/
│   │   ├── session_tools.py         # start_session, end_session, get_session_status
│   │   ├── scan_tools.py            # scan_markets, analyze_instrument
│   │   └── trade_tools.py           # validate_proposal, execute_trade
│   ├── monitor/
│   │   └── monitor.py               # subprocess: CapitalClient + Anthropic SDK
│   ├── agent/                       # used by monitor.py only
│   │   ├── claude_client.py         # Anthropic SDK wrapper
│   │   ├── prompt_builder.py        # assembles system + user message from strategy files
│   │   └── output_parser.py         # parses + validates Claude JSON against schema
│   ├── strategy/
│   │   └── loader.py                # discovers + validates strategy YAML+MD pairs
│   ├── broker/
│   │   └── capital_client.py        # re-exports CapitalClient from capital-mcp-server
│   ├── risk/
│   │   └── preflight.py             # validates Claude output vs strategy YAML bounds
│   └── storage/
│       ├── db.py                    # SQLite init + schema
│       └── repository.py            # CRUD: trades, cycle_snapshots, reasoning_traces
├── tests/
│   ├── unit/                        # preflight, output_parser, prompt_builder, strategy loader
│   └── integration/                 # against Capital.com demo API
├── pyproject.toml
├── .env.example
├── CLAUDE.md                        # session instructions for Claude Code
├── README.md                        # this file — source of truth for all design decisions
└── TODO.md                          # implementation progress tracking
```

---

## 6. MCP Tools Exposed to Claude Code

| Tool | Parameters | Does |
|------|-----------|------|
| `start_session` | — | Authenticate, check open positions, load config, start monitor subprocess |
| `scan_markets` | `watchlist?` | Fetch ATR + trend + spread for each instrument → return ranked proposals |
| `analyze_instrument` | `epic, strategy` | Fetch 60x1min + sentiment + positions → return structured context dict |
| `validate_proposal` | `proposal_json` | Preflight check vs risk.yaml → pass/fail + specific violations |
| `execute_trade` | `proposal_json` | `create_position` + confirm + log to DB → return deal details |
| `get_session_status` | — | Current positions, unrealised P&L, monitor alive, session duration |
| `end_session` | `close_positions: bool` | Stop monitor, optionally close all, write session summary to DB |

---

## 7. Configuration Files

### 7.1 config/risk.yaml — Global Hard Limits

```yaml
global:
  max_loss_pct_per_trade: 5.0   # hard ceiling — never exceed
  margin_floor_pct: 20.0        # halt all trading below this
  max_open_positions: 3
  session_end_close: true
```

### 7.2 config/watchlist.yaml — Asset Universe

```yaml
forex:     [EURUSD, GBPUSD, USDJPY, EURGBP]
indices:   [US500, DE40, UK100]
commodities: [GOLD, XBRUSD]
crypto:    [BTCUSD, ETHUSD]
```

### 7.3 Strategy YAML Schema (example: momentum)

```yaml
name: momentum
entry:
  min_size: 0.1 / max_size: 5.0
risk:
  stop_loss:    { type: HARD, default_pct: 2.0, max_pct: 5.0 }
  trailing_stop: { enabled: true, min_distance_pct: 0.5, max_distance_pct: 3.0 }
  take_profit:  { dynamic: true, min_rr_ratio: 1.5, max_pct: 10.0 }
  position_scaling: { enabled: true, max_adds: 2, max_total_size: 10.0 }
  time_exit:    { enabled: true, close_minutes_before_session_end: 30 }
```

### 7.4 Environment Variables (.env)

```
CAPITAL_BASE_URL=https://demo-api-capital.backend-capital.com
CAPITAL_API_KEY=
CAPITAL_IDENTIFIER=
CAPITAL_API_KEY_PASSWORD=
ANTHROPIC_API_KEY=
MONITOR_INTERVAL_SECONDS=60
LOG_LEVEL=INFO
AUDIT_LOG_PATH=./data/audit.jsonl
```

---

## 8. SQLite Schema

```sql
cycle_snapshots:
  id, ts, asset, strategy, account_bal, positions (JSON), market_data (JSON)

trades:
  id, cycle_id, ts, asset, direction, size, entry_price, stop_loss, take_profit,
  status, broker_ref
  -- status: PROPOSED | APPROVED | REJECTED | EXECUTED | FAILED

reasoning_traces:
  id, cycle_id, ts, prompt_tokens, output_tokens, reasoning, tool_calls (JSON)
  -- captured for ALL cycles including monitor — critical for post-trade debugging
  -- full prompt (system + user) stored, not just Claude's response
```

---

## 9. Implementation Order

| # | Component | Notes |
|---|-----------|-------|
| 1 | `pyproject.toml` + project scaffold | Installable package, capital-mcp-server as local path dependency |
| 2 | `storage/db.py` + `repository.py` | SQLite schema, CRUD — all other components depend on this |
| 3 | `broker/capital_client.py` | Validate all needed calls against Capital.com demo API |
| 4 | `risk/preflight.py` | Unit-test in isolation against mock strategy YAML — must be solid before execution |
| 5 | `strategy/loader.py` + all config files | Pluggable strategy interface; validate with both momentum + mean_reversion |
| 6 | `agent/` | claude_client, prompt_builder, output_parser — for monitor use only |
| 7 | `monitor/monitor.py` | Autonomous loop; integration test on demo API |
| 8 | `tools/` + `server.py` | All 7 MCP tools; wire FastMCP |
| 9 | Claude Desktop / Code MCP config | Wire cfd-trading MCP + capital-mcp-server via `wsl -e`; end-to-end test |

---

## 10. Open Items

| Item | Priority | Status |
|------|----------|--------|
| Implement `storage/` — DB schema + CRUD | High | Not started |
| Implement `broker/capital_client.py` + smoke test | High | Not started |
| Implement `risk/preflight.py` + unit tests | High | Not started |
| Define `_base.md` output contract + hard rules | High | Not started |
| Define `scan.md` prompt module | High | Not started |
| Implement `strategy/loader.py` + YAML schema | High | Not started |
| Implement `agent/` layer | High | Not started |
| Implement `monitor/monitor.py` | High | Not started |
| Implement all 7 MCP tools + `server.py` | High | Not started |
| Wire MCP config for Claude Desktop | High | Not started |
| Tune momentum + mean_reversion prompt modules on demo | Medium | Not started |
| Define context window budget per cycle (token estimate) | Medium | Not started |
| v2: persistent monitor daemon (survives session end) | Low | Deferred |
| v2: replace ManualGate with AutoGate + circuit breaker | Low | Deferred |
| Alpha Vantage MCP for macro context | Low | Deferred |
| Web UI / dashboard for trade history | Low | Deferred |

---

## 11. Starting a Claude Code Implementation Session

At the start of each session, reference this file and use the following prompt:

```
You are the implementation engineer for the CFD Trading System.
README.md defines all design decisions — treat it as the source of truth.
Do not deviate from agreed decisions without flagging the conflict and proposing a change.

We are implementing step [N] of the implementation order: [component name].

Update README.md whenever a design decision changes.
```
