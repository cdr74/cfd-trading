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
| Capital.com client | Python (`CapitalClient` from `capital-com-client`, imported directly) | Market data, trade execution, account/position queries |
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
  │    └─ returns ranked instruments           ├─ rule engine (strategy YAML)
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

**Key distinction:** In the entry flow, Claude Code does all reasoning. In the monitor flow, `monitor.py` evaluates strategy YAML rules mechanically — no AI call is made. Claude Code is the only reasoning engine in the system.

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
| Scan, strategy selection, trade proposal | Claude Code (active session) | Claude Code calls MCP tools, reasons over returned data, presents conversational summary + proposal JSON to human |
| Monitor (autonomous) | No Claude — rule engine only | `monitor.py` evaluates YAML rules mechanically; no Anthropic API calls |

Claude Code is the only reasoning engine. No Anthropic API key is required at runtime. The `agent/` layer is not needed and has been removed from scope.

### 4.3a Proposal Presentation Format

Claude Code presents trade proposals in two parts:

1. **Conversational summary** — plain-language rationale: what it sees, why it's proposing this trade, what could go wrong (`contra_indicators`), and the key risk parameters.
2. **Proposal JSON** — the structured contract (see §4.5) in a code block, passed directly to `validate_proposal` and then `execute_trade` if approved.

The JSON is the authoritative record. The conversation is the presentation layer. Both are always present — neither replaces the other.

### 4.4 State Persistence

| Decision | Value |
|----------|-------|
| Strategy config | YAML + Markdown files, Git-versioned under `config/strategies/` |
| Live account state | Always fetched live from Capital.com — never trusted from cache |
| Trade history | SQLite — `trading.db` (gitignored, lives on Linux FS in WSL2) |
| Audit / reasoning | SQLite (`reasoning_traces` table) + JSONL sidecar for easy grep |
| File location | Keep `trading.db` on Linux FS (`~/` or `/home/...`) not `/mnt/c/` — I/O performance |
| Migration path | SQLite → Postgres (RDS) when moving to AWS — minimal schema change |

### 4.5 Entry Proposal JSON Schema

Claude Code produces a single JSON object as the formal trade proposal. This is passed to `validate_proposal` (preflight) and then `execute_trade` if the human approves.

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

**Key rules:** `contra_indicators` and `stop_loss` are always required. `action: NONE` is a valid explicit output (Claude declines to trade). The JSON is logged to `reasoning_traces` on every entry cycle alongside the conversational summary.

### 4.6 Position Monitoring — Rule-Based Engine

After a trade is entered, the monitor manages the position mechanically from the strategy YAML. No AI call is made. Rules are evaluated in order every `MONITOR_INTERVAL_SECONDS` (default 60):

| Rule | Condition | Action |
|------|-----------|--------|
| Hard stop | Current price crosses `stop_loss.value` | CLOSE — log to DB + audit.jsonl |
| Trailing stop ratchet | Price moved favourably by `trailing_stop.initial_distance_pct` | ADJUST stop level (ratchet only — never widens) |
| Take profit | Current price reaches `take_profit.initial_value` | CLOSE |
| Time exit | `session_end - close_minutes_before_session_end` | CLOSE |
| No condition met | — | HOLD — log cycle snapshot, sleep |

| Decision | Value |
|----------|-------|
| Monitor reasoning | None — pure rule evaluation from strategy YAML |
| Anthropic API calls at runtime | None — no API key required for monitoring |
| Trailing stop rule | Ratchet-only — can only move in profitable direction |
| Hard stop enforcement | Mechanical, always present — `risk.yaml` max % is the ceiling |
| Take profit | Fixed at entry proposal value — no dynamic adjustment in v1 |
| Preflight location | `risk/preflight.py` — validates entry proposals only; monitor enforces rules directly |

### 4.8 Broker and Instrument Generalization — Deferred

The current architecture is intentionally Capital.com-specific for v1. A generalization refactor is planned for a later point. This section records what would need to change and why.

#### What is already generic

The logic layer requires no changes to support a different broker or instrument type:

| Layer | Why it's generic |
|---|---|
| `risk/preflight.py` | Pure dict validation — no broker concepts |
| `strategy/loader.py` + YAML/MD | Strategy bounds (size, stop %, R:R, trailing stop params) apply to any instrument |
| `storage/` | Sessions, trades, snapshots, traces — entirely domain-agnostic |
| Proposal JSON schema | LONG/SHORT, size, stop %, R:R ratio — standard trading concepts |
| Monitor rule evaluation | Hard stop, trailing stop ratchet, take profit, time exit — generic rules |

#### What is tightly coupled to Capital.com

**No broker interface exists.** `broker/capital_client.py` is a naked re-export with no `BrokerClient` Protocol or ABC. Swapping brokers has no single seam to cut.

**Capital.com response shapes are parsed inline throughout the tools.** Each tool unpacks Capital.com JSON directly — position field names (`dealId`, `upl`, `stopLevel`), price field names (`highPrice.bid`, `closePrice.ask`, `snapshotTime`), account structure (`balance.deposit`, `balance.available`). Changing brokers means hunting these across `session_tools.py`, `scan_tools.py`, `trade_tools.py`, and `monitor.py`.

**Two-step create → confirm execution is hardcoded.** `execute_trade` calls `create_position()` then `confirm_deal()`. Other brokers return a fill immediately or use a different confirmation flow.

**Trailing stop handling is Capital.com-specific.** Capital.com requires `stop_distance` (points from entry) for trailing stops, not a price level. This conversion is in the tool layer, not the broker adapter.

**Direction inconsistency: LONG/SHORT vs BUY/SELL.** The proposal schema uses `LONG`/`SHORT`. Capital.com position data uses `BUY`/`SELL`. The mapping is done in `execute_trade`; the monitor reads `BUY`/`SELL` from position data directly.

**Client sentiment is a core signal, not optional.** `scan_markets` and `analyze_instrument` always call `get_client_sentiment()`. Not all brokers provide this data.

#### The refactor — one targeted addition to `broker/`

Define a `BrokerClient` Protocol plus normalized data types in `broker/protocol.py`:

```python
@runtime_checkable
class BrokerClient(Protocol):
    def authenticate(self) -> bool: ...
    def get_positions(self) -> list[Position]: ...           # normalized
    def get_prices(self, symbol: str, ...) -> list[OHLCBar]: ...   # normalized
    def get_account(self) -> AccountInfo: ...                # normalized
    def get_sentiment(self, symbol: str) -> Sentiment | None: ...  # optional
    def create_position(self, order: OrderRequest) -> ExecutionResult: ...
    def update_stop(self, deal_id: str, stop: float) -> bool: ...
    def close_position(self, deal_id: str) -> bool: ...
```

`CapitalClient` gets a thin adapter that translates its response shapes into these normalized types. All tool and monitor code works against the normalized types only. A new broker requires only a new adapter — zero changes to tools, preflight, monitor, or strategy layers.

**Estimated effort:** 1–2 days. The logic is already clean — it is purely a normalization and interface definition exercise.

---

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
│       ├── _base.md                 # proposal schema + hard rules for Claude Code context
│       ├── scan.md                  # market scan prompt injected into Claude Code context
│       ├── momentum.yaml / .md      # trend-following strategy (rules + Claude Code prompt)
│       └── mean_reversion.yaml / .md  # range-bound strategy (rules + Claude Code prompt)
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
│   │   └── monitor.py               # rule engine subprocess — no AI calls
│   ├── strategy/
│   │   └── loader.py                # discovers + validates strategy YAML+MD pairs
│   ├── broker/
│   │   └── capital_client.py        # re-exports CapitalClient from capital-mcp-server
│   ├── risk/
│   │   └── preflight.py             # validates entry proposals vs strategy YAML bounds
│   └── storage/
│       ├── db.py                    # SQLite init + schema
│       └── repository.py            # CRUD: trades, cycle_snapshots, reasoning_traces
├── tests/
│   ├── unit/                        # preflight, strategy loader, monitor rules, tools
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
MONITOR_INTERVAL_SECONDS=60
LOG_LEVEL=INFO
AUDIT_LOG_PATH=./data/audit.jsonl
```

---

## 8. SQLite Schema

```sql
sessions:
  id (UUID), started_at, ended_at (nullable), status (ACTIVE | CLOSED),
  summary (JSON, nullable)
  -- top-level wrapper for all activity in one Claude/app session

cycle_snapshots:
  id, session_id (FK → sessions), ts, asset, strategy,
  account_bal, positions (JSON), market_data (JSON)

trades:
  id, session_id (FK → sessions), cycle_id, ts, asset, direction, size,
  entry_price, stop_loss, take_profit, status, broker_ref
  -- status: PROPOSED | APPROVED | REJECTED | EXECUTED | FAILED

reasoning_traces:
  id, session_id (FK → sessions), cycle_id, ts, prompt_tokens, output_tokens,
  reasoning, tool_calls (JSON)
  -- captured for ALL cycles including monitor — critical for post-trade debugging
  -- full prompt (system + user) stored, not just Claude's response
```

---

## 9. Implementation Order

| # | Component | Status | Notes |
|---|-----------|--------|-------|
| 1 | `pyproject.toml` + project scaffold | **Done** | Installable package, capital-mcp-server as dependency |
| 2 | `storage/db.py` + `repository.py` | **Done** | SQLite schema + CRUD; 12 unit tests |
| 3 | `broker/capital_client.py` | **Done** | Re-exports CapitalClient; 7 integration tests pass against demo API |
| 4 | `risk/preflight.py` | **Done** | 43 unit tests covering all validation rules and edge cases |
| 5 | `strategy/loader.py` + all config files | **Done** | Pluggable strategy interface; _base.md, scan.md, momentum.md, mean_reversion.md all written; 22 unit tests |
| 6 | `monitor/monitor.py` | **Done** | Rule-based engine only — no AI calls; 25 unit tests |
| 7 | `tools/` + `server.py` | **Done** | All 7 MCP tools wired with FastMCP; 27 unit tests; `server.py` supports stdio and streamable-HTTP (with HTTPS) transport via `MCP_TRANSPORT` env var |
| 8 | GitHub Actions CI | **Done** | Unit tests always; integration tests on push using demo API secrets |
| 9 | Container deployment + MCP wiring | **Done** | Podman container; Claude Desktop configured via HTTP endpoint; end-to-end smoke tests pending |

Note: the `agent/` layer (claude_client, prompt_builder, output_parser) has been removed from scope. The monitor uses rule evaluation, not AI calls.

---

## 10. Open Items

| Item | Priority | Status |
|------|----------|--------|
| End-to-end smoke tests (SM-01 through SM-11) | High | Ready to run — see `SMOKE_TESTS.md` in workspace root |
| Integration tests: monitor + tools against demo API | Medium | Written, run in CI; `@pytest.mark.trade` manual only |
| CI: add container build + push job to GitHub Actions | Medium | Not started |
| Tune momentum + mean_reversion prompt modules on demo | Medium | Not started |
| v2: persistent monitor daemon (survives session end) | Low | Deferred |
| v2: replace ManualGate with AutoGate + circuit breaker | Low | Deferred |
| Broker/instrument generalization refactor | Low | Deferred — see §4.8 |
| Alpha Vantage MCP for macro context | Low | Deferred |
| Web UI / dashboard for trade history | Low | Deferred |

---

## 11. Running the MCP Server

The MCP server runs as a **Podman container** exposing a streamable-HTTP endpoint at `https://localhost:8089/mcp`. Claude Desktop connects to it via the URL configured in `claude_desktop_config.json`.

### Workspace helper scripts (recommended)

From the `trading/` workspace root:

```bash
./mcp-start.sh    # pulls latest images from ghcr.io and starts both containers
./mcp-stop.sh     # stops both containers
./mcp-status.sh   # full health check: containers, endpoints, credentials, Desktop config
./mcp-fix-config.sh  # restores mcpServers block if Claude Desktop overwrites the config
```

### Manual start (dev — builds from source, for local changes not yet pushed)

Build context must be the `trading/` parent directory (includes sibling repos):

```bash
cd ~/dev/trading/cfd-trading
podman-compose -f podman-compose.dev.yml up --build -d
```

### Manual stop

```bash
podman-compose down          # production container (cfd-trading)
# or
podman-compose -f podman-compose.dev.yml down   # dev container (cfd-trading-dev)
```

### Logs

```bash
podman logs -f cfd-trading       # production container
podman logs -f cfd-trading-dev   # dev container
```

### Claude Desktop config note

Claude Desktop occasionally overwrites `claude_desktop_config.json` on launch, removing the
`mcpServers` block. Run `./mcp-fix-config.sh` to restore it, then restart Claude Desktop.

The script writes HTTPS URLs (`https://localhost:808x/mcp`) and sets `NODE_OPTIONS: --use-system-ca`
in each entry so that mcp-remote (Node.js) reads the Windows Trusted Root store and trusts the
mkcert certificate. If the mkcert root CA has not yet been imported into Windows, see the TLS
setup section in the workspace `CLAUDE.md`.

### Running locally (stdio mode, for testing only)

```bash
# Activate venv — note: capital-com-client must be installed from local clone
# (the package is private and not on PyPI)
pip install -e ~/dev/trading/capital-com-client/
pip install -e ".[dev]"

python -m cfd_trading.server   # or: cfd-trading  (if entry point is installed)
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_TRANSPORT` | `stdio` | Set to `streamable-http` in container |
| `MCP_HOST` | `127.0.0.1` | Set to `0.0.0.0` in container |
| `MCP_PORT` | `8000` | Set to `8089` in container |
| `SSL_CERTFILE` | — | Path to TLS cert file (enables HTTPS when set with `SSL_KEYFILE`) |
| `SSL_KEYFILE` | — | Path to TLS private key file |
| `CONFIG_DIR` | `/app/config` | Path to the `config/` directory (strategies, risk, watchlist) |
| `DB_PATH` | `/app/data/trading.db` | Path to the SQLite database file |
| `AUDIT_LOG_PATH` | `/app/data/audit.jsonl` | Path to the audit log |
| `CAPITAL_BASE_URL` | — | Demo or live Capital.com API URL |
| `CAPITAL_API_KEY` | — | Capital.com API key |
| `CAPITAL_IDENTIFIER` | — | Capital.com login email |
| `CAPITAL_API_KEY_PASSWORD` | — | Capital.com API password |
| `MONITOR_INTERVAL_SECONDS` | `60` | Monitor cycle interval |
| `LOG_LEVEL` | `INFO` | Logging level |
| `AUDIT_LOG_PATH` | `./data/audit.jsonl` | Audit log path |

---

## 12. Starting a Claude Code Session

At the start of each session:

1. Read `README.md` — confirm you understand the current design state
2. Read `TODO.md` — identify what is in progress and what is next
3. Check `git status` — understand what has already been changed
4. For cross-repo work, start from `~/dev/trading/` (parent workspace)

Reference prompt:

```
You are the implementation engineer for the CFD Trading System.
README.md defines all design decisions — treat it as the source of truth.
Do not deviate from agreed decisions without flagging the conflict and proposing a change.

We are working on: [describe task].

Update README.md whenever a design decision changes.
```
