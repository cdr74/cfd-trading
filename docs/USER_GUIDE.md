# User Guide

How to set up, start, and operate the CFD trading system.  
For architecture and design decisions see `docs/SYSTEM_DESIGN.md`. For strategy details see `docs/CFD_STRATEGY_CATALOG.md`.

---

## Contents

1. [Prerequisites](#1-prerequisites)
2. [Initial Setup](#2-initial-setup)
3. [Starting the System](#3-starting-the-system)
4. [Running a Trading Session](#4-running-a-trading-session)
5. [Running Backtests](#5-running-backtests)
6. [Troubleshooting](#6-troubleshooting)
7. [Local Development](#7-local-development)

---

## 1. Prerequisites

| Requirement | Notes |
|-------------|-------|
| Windows 11 with WSL2 | Ubuntu recommended |
| Podman | Installed in WSL2 — `sudo apt install podman podman-compose` |
| Claude Desktop | Windows app; configured to connect to both MCP servers |
| Capital.com demo account | API key + identifier + password required |
| mkcert | One-time TLS setup — see §2 |

The two MCP servers run as Podman containers in WSL2. Claude Desktop (Windows) connects to them over HTTPS using mcp-remote.

---

## 2. Initial Setup

### 2.1 TLS Certificates (one-time)

Certificates live in `~/dev/trading/certs/` and are mounted read-only into both containers.

```bash
sudo apt install mkcert
mkcert -install
mkdir -p ~/dev/trading/certs
cd ~/dev/trading/certs
mkcert -key-file key.pem -cert-file cert.pem localhost 127.0.0.1 ::1
```

**Trust the CA on Windows** (required once for Claude Desktop — run PowerShell as Administrator):

```powershell
Copy-Item "\\wsl$\Ubuntu\home\chris\.local\share\mkcert\rootCA.pem" "$env:TEMP\mkcert-rootCA.crt"
certutil -addstore -f "ROOT" "$env:TEMP\mkcert-rootCA.crt"
```

Restart Claude Desktop after importing. The cert expires 2028-07-20 — regenerate and re-import before then.

### 2.2 Credentials

Copy `.env.example` to `.env` and fill in the Capital.com demo credentials:

```bash
cp .env.example .env
# edit .env — fill CAPITAL_BASE_URL, CAPITAL_API_KEY, CAPITAL_IDENTIFIER, CAPITAL_API_KEY_PASSWORD
```

`.env` is gitignored. Never commit it.

---

## 3. Starting the System

### 3.1 Start containers

```bash
cd ~/dev/trading/cfd-trading/integration-test
./mcp-start.sh     # pulls latest images from ghcr.io and starts both containers
./mcp-status.sh    # full health check: containers, endpoints, credentials, Desktop config
```

`mcp-status.sh` must show **All checks passed** before opening Claude Desktop.

### 3.2 Fix Claude Desktop config (if needed)

Claude Desktop occasionally overwrites `claude_desktop_config.json` on launch, removing the `mcpServers` block:

```bash
cd ~/dev/trading/cfd-trading/integration-test
./mcp-fix-config.sh   # restores both HTTPS MCP server entries
```

Then restart Claude Desktop. The script sets:
- `cfd-trading`: `https://localhost:8089/mcp`
- `capital-mcp-server`: `https://localhost:8088/mcp`
- `NODE_OPTIONS: --use-system-ca` on each entry so mcp-remote trusts the mkcert CA

### 3.3 Stop containers

```bash
cd ~/dev/trading/cfd-trading/integration-test
./mcp-stop.sh
```

### 3.4 Check logs

```bash
podman logs -f cfd-trading          # production container
podman logs -f capital-mcp-server   # broker MCP container
```

---

## 4. Running a Trading Session

All steps happen inside Claude Desktop. Use the conversational interface — Claude calls the MCP tools automatically as you work through the flow.

### Step 1 — Start the session

Ask Claude:
> "Start a trading session"

Claude calls `start_session`, which:
- Authenticates with Capital.com
- Checks for any existing open positions
- Loads strategy and risk configuration
- Starts the monitor subprocess in the background

### Step 2 — Scan markets

Ask Claude:
> "Scan the markets" or "What looks good today?"

Claude calls `scan_markets`, which fetches ATR, trend slope, and spread for all watchlist instruments and returns a ranked list. Claude presents the top candidates with rationale.

You can also specify instruments directly:
> "Scan EURUSD and GOLD"

### Step 3 — Analyse an instrument

After selecting an instrument, ask Claude to analyse it with a strategy:
> "Analyse EURUSD with the momentum strategy"

Claude calls `analyze_instrument`, which fetches 60×1min bars + client sentiment + current positions and computes:
- `EMA_9`, `EMA_21` and their gap
- Z-score of latest close vs 20-bar window
- ATR, trend slope, spread/ATR ratio
- Vol-scaled position size suggestion

Claude presents the indicators and proposes whether conditions support entry.

### Step 4 — Review the proposal

If Claude recommends entry, it produces:

1. A conversational summary explaining the rationale, signal basis, and contra-indicators
2. A proposal JSON block containing the full trade specification

Review both. You can ask follow-up questions, request modifications to the stop or size, or decline entirely.

### Step 5 — Validate and execute

When satisfied, ask Claude to proceed:
> "Looks good, go ahead" or "Execute that trade"

Claude calls `validate_proposal` (preflight check against `risk.yaml`) and, if it passes, calls `execute_trade`. Capital.com confirms the deal and Claude reports the fill details.

### Step 6 — Monitor runs automatically

Once a position is open, the monitor subprocess checks it every 60 seconds. It evaluates four rules in order (hard stop → trailing stop ratchet → take profit → time exit) and acts autonomously. No further action is needed from you.

Check status any time:
> "What's the current status?" or "How's the position doing?"

Claude calls `get_session_status` and reports positions, unrealised P&L, and monitor health.

### Step 7 — End the session

When you're done:
> "End the session and close all positions"  
> or: "End the session but leave positions open"

`end_session(close_positions=True)` closes all open positions then stops the monitor.  
`end_session(close_positions=False)` stops the monitor and leaves positions open with their registered stop losses at Capital.com.

Claude prints the session summary: duration, trade count, P&L, win rate, max drawdown.

---

## 5. Running Backtests

Backtests run against historical 1-min bars stored locally (populated separately via MetaTrader 5 on Windows).

```bash
cd ~/dev/trading/cfd-trading
source .venv/bin/activate

# Single strategy + instrument
BACKTEST_DB_PATH=/mnt/c/Users/chris/dev/trading-data/trading.db \
  python -m cfd_trading.backtest.run --strategy momentum --epic EURUSD

# Full matrix — all strategies × all 11 watchlist instruments
BACKTEST_DB_PATH=/mnt/c/Users/chris/dev/trading-data/trading.db \
  python -m cfd_trading.backtest.run --all-strategies --all-epics
```

See `docs/BACKTESTING.md` for full detail: data setup, engine design, test suite, and how to read the results.

---

## 6. Troubleshooting

### Tools missing from Claude Desktop

Claude Desktop sometimes overwrites its config on launch. Run:
```bash
cd ~/dev/trading/cfd-trading/integration-test
./mcp-fix-config.sh
```
Then restart Claude Desktop.

### `UNABLE_TO_VERIFY_LEAF_SIGNATURE` error

The mkcert root CA has not been imported into the Windows Trusted Root store. Follow the PowerShell step in §2.1.

### Container fails to start

```bash
podman ps -a          # check if container exited
podman logs cfd-trading   # check for startup errors
```

Common cause: `.env` file missing or credentials wrong. Check that `CAPITAL_BASE_URL`, `CAPITAL_API_KEY`, `CAPITAL_IDENTIFIER`, and `CAPITAL_API_KEY_PASSWORD` are set.

### `start_session` fails — authentication error

Capital.com demo sessions expire. Verify the API key and password are still valid by logging into the Capital.com demo web platform. Generate a new API key if needed and update `.env`.

### Monitor not running

Check `get_session_status` — it reports `monitor_alive: true/false`. If false, the subprocess exited unexpectedly. Check `podman logs -f cfd-trading` for stack traces.

---

## 7. Local Development

### 7.1 Python environment

The local `.venv` is used for running tests only (not serving — that's the container).

```bash
# capital-com-client is a private repo — install from local clone first
pip install -e ~/dev/trading/capital-com-client/
pip install -e ".[dev]"
```

### 7.2 Running unit tests

```bash
cd ~/dev/trading/cfd-trading
source .venv/bin/activate
pytest tests/unit/ -v            # 190 tests, should run in < 30s
```

### 7.3 Running integration tests (needs demo credentials)

```bash
pytest tests/integration/ -m integration -v
pytest tests/integration/ -m trade -v    # creates real demo positions — use sparingly
```

### 7.4 Building and running the dev container

For testing changes before pushing (builds from source rather than pulling the published image):

```bash
cd ~/dev/trading/cfd-trading
podman-compose -f podman-compose.dev.yml up --build -d
podman logs -f cfd-trading-dev
```

After any change to `server.py` or `tools/`, rebuild the container — the running image does not pick up source changes automatically.

### 7.5 Smoke tests

After any significant change or deployment, run the full smoke test suite. See `integration-test/SMOKE_TESTS.md` for the step-by-step checklist (SM-01 through SM-11).
