# End-to-End Smoke Test Guide

Manual tests to verify the full trading system. Run after any deployment or significant change.
Each test builds on the previous — run them in order.

---

## Prerequisites

Before starting, run the status check from the `integration-test` directory:

```bash
cd ~/dev/trading/cfd-trading/integration-test
./mcp-start.sh    # pulls latest images and starts both containers
./mcp-status.sh   # verifies containers, endpoints, credentials, and Desktop config
```

`mcp-status.sh` must show **All checks passed** before proceeding. If the Claude Desktop
config check fails, run:

```bash
./mcp-fix-config.sh   # restores mcpServers block if Claude Desktop wiped it
```

Then restart Claude Desktop. The app sometimes overwrites `claude_desktop_config.json` on
launch, removing the `mcpServers` block — `mcp-fix-config.sh` restores it without touching
other preferences.

**Also confirm:**
- You have a Capital.com **demo** account configured in `cfd-trading/.env`
- Demo account has sufficient balance (default cap: 100,000)
- The mkcert root CA has been imported into the Windows Trusted Root store (one-time — see workspace `CLAUDE.md` TLS section). If not done, mcp-remote will fail with `UNABLE_TO_VERIFY_LEAF_SIGNATURE`.

---

## SM-01 — Container Health

**Purpose:** Verify both MCP servers are reachable before involving Claude.

**Steps:**
1. Run `./mcp-status.sh` — all checks in section 1 (Container status) must be green
2. Confirm both endpoints respond: the script checks `https://localhost:8088/mcp` and `https://localhost:8089/mcp`

**Pass criteria:** Both endpoint checks show ✓. If a container is not running, use `./mcp-start.sh`.

---

## SM-02 — Claude Desktop MCP Discovery

**Purpose:** Verify Claude Desktop sees both MCP servers and their tools.

**Steps:**
1. Run `./mcp-status.sh` — section 3 (Claude Desktop config) must be green
2. Open Claude Desktop
3. Click the hammer/tools icon in the chat input area
4. Verify the tool list includes tools from **cfd-trading**: `start_session`, `end_session`, `get_session_status`, `scan_markets`, `analyze_instrument`, `validate_proposal`, `execute_trade`
5. Verify tools from **capital-mcp-server** are also listed

**Pass criteria:** All 7 cfd-trading tools visible.

**If tools are missing:**
- Check `./mcp-status.sh` section 3 — if the config check fails, run `./mcp-fix-config.sh` then restart Claude Desktop
- Claude Desktop must be restarted after any config change

---

## SM-03 — Session Start

**Purpose:** Verify `start_session` authenticates to Capital.com and initialises the monitor.

**Steps:**
1. In Claude Desktop, ask: *"Start a trading session using the demo account."*
2. Observe Claude call `start_session`
3. Check the response includes: authentication confirmation, current open positions (may be 0), monitor subprocess started

**Expected response structure:**
```
session_id: <uuid>
authenticated: true
open_positions: <n>
monitor_started: true
account_balance: <value>
```

**Pass criteria:** `authenticated: true`, `monitor_started: true`. No error about missing credentials.

**Cleanup if needed:** If session stays open, run SM-11 to close it.

---

## SM-04 — Market Scan

**Purpose:** Verify `scan_markets` returns ranked instruments with plausible values.

**Steps:**
1. Ask: *"Scan the market and show me the top 5 instruments."*
2. Observe Claude call `scan_markets`
3. Review the returned list

**What to check:**
- At least 3–5 instruments returned
- Each instrument has an ATR value > 0
- Spread/ATR ratio is present (lower = better)
- The ranking makes intuitive sense given current market conditions (compare with Capital.com web platform)

**Pass criteria:** Non-empty ranked list with ATR and spread data. Values should be plausible for current session (e.g. ATR for EURUSD at 60 × 1-min bars should be in a reasonable pips range).

---

## SM-05 — Instrument Analysis

**Purpose:** Verify `analyze_instrument` returns structured context Claude can reason over.

**Steps:**
1. Pick the top instrument from SM-04 (e.g. `EURUSD`)
2. Ask: *"Analyse EURUSD using the momentum strategy."*
3. Observe Claude call `analyze_instrument(epic="EURUSD", strategy="momentum")`
4. Review the response

**What to check:**
- 60 × 1-min OHLC bars included
- Sentiment data present (or gracefully absent with a note)
- Open positions for this instrument listed (may be 0)
- Claude's subsequent analysis references the data (trend direction, ATR, contra_indicators)

**Pass criteria:** Structured context dict returned; Claude produces a coherent strategy assessment from it.

---

## SM-06 — Proposal Validation (Pass)

**Purpose:** Verify `validate_proposal` accepts a valid entry proposal.

**Steps:**
1. Ask Claude to construct a valid proposal based on the SM-05 analysis, then validate it
2. Observe Claude call `validate_proposal` with a JSON proposal
3. Verify the proposal includes: `epic`, `direction` (LONG or SHORT), `size`, `stop_loss`, `take_profit`, `strategy`, `contra_indicators`

**Pass criteria:** Response shows `passed: true`, `violations: []`.

---

## SM-07 — Proposal Validation (Fail)

**Purpose:** Verify `validate_proposal` correctly rejects a bad proposal.

**Steps:**
1. Ask Claude to validate a proposal with an intentionally bad stop loss — e.g. stop loss only 0.1% from entry when the strategy minimum is higher
2. Observe `validate_proposal` called
3. Review violations list

**Pass criteria:** Response shows `passed: false` with at least one violation describing the stop loss breach. Claude should refuse to proceed to execution.

---

## SM-08 — Trade Execution (Demo Only)

**Purpose:** Verify `execute_trade` opens a real position on the demo account.

> **Warning:** This creates a real position on Capital.com demo. Confirm you are using demo credentials (`CAPITAL_BASE_URL=https://demo-api-capital.backend-capital.com`) before running.

**Steps:**
1. Use the valid proposal from SM-06
2. Ask: *"Execute this trade on the demo account."*
3. Observe Claude call `execute_trade`
4. Check the response for `deal_reference` and `deal_id`
5. Log in to Capital.com demo web platform and confirm the position appears
6. Check `cfd-trading/data/trading.db` — the trade should be recorded:
   ```bash
   sqlite3 ~/dev/trading/cfd-trading/data/trading.db \
     "SELECT * FROM trades ORDER BY created_at DESC LIMIT 3;"
   ```

**Pass criteria:** Position visible in Capital.com demo AND in the local SQLite DB with status `EXECUTED`.

---

## SM-09 — Monitor Cycle Observation

**Purpose:** Verify the monitor subprocess is running and writing cycle snapshots.

**Steps:**
1. After SM-08 (open position exists), wait 60–90 seconds
2. Ask: *"What is the current session status?"*
3. Observe Claude call `get_session_status`
4. Check monitor is still alive and cycles have run:
   ```bash
   sqlite3 ~/dev/trading/cfd-trading/data/trading.db \
     "SELECT COUNT(*) FROM cycle_snapshots;"
   sqlite3 ~/dev/trading/cfd-trading/data/trading.db \
     "SELECT * FROM cycle_snapshots ORDER BY created_at DESC LIMIT 3;"
   ```
5. Check audit log:
   ```bash
   tail -f ~/dev/trading/cfd-trading/data/audit.jsonl
   ```

**Pass criteria:** `monitor_alive: true` in session status; at least 1 cycle snapshot in DB per position per cycle; audit log shows HOLD decisions (or ADJUST/CLOSE if price moved significantly).

---

## SM-10 — Session End (Leave Positions Open)

**Purpose:** Verify `end_session(close_positions=False)` stops the monitor and leaves positions at broker.

**Steps:**
1. Ask: *"End the session but leave my positions open."*
2. Observe Claude call `end_session(close_positions=false)`
3. Check Capital.com demo — position should still be open
4. Check `get_session_status` returns an error or "no active session"

**Pass criteria:** Monitor stopped; positions remain open at Capital.com; session record closed in DB.

---

## SM-11 — Session End (Close All Positions)

**Purpose:** Verify `end_session(close_positions=True)` closes all demo positions and writes session summary.

**Steps:**
1. Start a new session (SM-03) and open a position (SM-08)
2. Ask: *"End the session and close all open positions."*
3. Observe Claude call `end_session(close_positions=true)`
4. Verify Capital.com demo shows no open positions
5. Check session summary in DB:
   ```bash
   sqlite3 ~/dev/trading/cfd-trading/data/trading.db \
     "SELECT * FROM sessions ORDER BY created_at DESC LIMIT 1;"
   ```

**Pass criteria:** All positions closed at Capital.com; session summary record written with trade counts.

---

## Test Matrix Summary

| ID | What it tests | Requires live broker | Modifies broker state |
|----|--------------|---------------------|----------------------|
| SM-01 | Container health | No | No |
| SM-02 | MCP discovery in Claude Desktop | No | No |
| SM-03 | Session start / auth | Yes (demo) | No |
| SM-04 | Market scan / ATR data | Yes (demo) | No |
| SM-05 | Instrument analysis | Yes (demo) | No |
| SM-06 | Valid proposal validation | No | No |
| SM-07 | Invalid proposal rejection | No | No |
| SM-08 | Trade execution | Yes (demo) | **Yes — opens position** |
| SM-09 | Monitor cycle observation | Yes (demo) | No |
| SM-10 | Session end, leave open | Yes (demo) | No |
| SM-11 | Session end, close all | Yes (demo) | **Yes — closes positions** |
