# CFD Trading System

AI-driven intraday CFD trading using Claude Code and Capital.com. Claude handles the full entry flow — scan markets, analyse instruments, propose and execute trades. A rule-based monitor manages open positions autonomously between entry cycles.

> ⚠️ **Decision point (2026-05-18): no strategy has a validated edge.** The Phase A strategy audit closed on the kill-criterion — `mean_reversion` dropped (non-viable on retail CFD), `momentum` & `ORB` unvalidated (no edge survived Deflated-Sharpe / out-of-sample). The *infrastructure* is complete and works; what is missing is a deployable strategy. **Do not trade this live.** Authoritative record + the next-phase strategy debate: [`docs/STRATEGY_AUDIT.md`](docs/STRATEGY_AUDIT.md).

**Repos:** `github.com/cdr74/cfd-trading` (this) · `github.com/cdr74/capital-mcp-server` · `github.com/cdr74/capital-com-client`  
**Deployment:** WSL2 on Windows 11 (local) — portable to AWS

---

## Quick Start

```bash
cd ~/dev/trading/cfd-trading/integration-test
./mcp-start.sh     # pull latest images and start both containers
./mcp-status.sh    # verify containers, endpoints, credentials, Desktop config
```

If tools are missing from Claude Desktop:
```bash
./mcp-fix-config.sh   # restore mcpServers block
# then restart Claude Desktop
```

MCP endpoints: `https://localhost:8089/mcp` (cfd-trading) · `https://localhost:8088/mcp` (capital-mcp-server)

---

## Documentation

| Document | Contents |
|----------|---------|
| [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) | Setup, starting the system, running a session step-by-step, backtests, troubleshooting |
| [`docs/STRATEGY_AUDIT.md`](docs/STRATEGY_AUDIT.md) | **Authoritative.** Strategy audit record + kill-criterion conclusion (Part 1) and the next-phase strategy debate / carried-forward guardrails / D1 cost anchor (Part 2) |
| [`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md) | Architecture, design decisions, MCP tools, config schemas, SQLite schema, implementation status |
| [`docs/CFD_STRATEGY_CATALOG.md`](docs/CFD_STRATEGY_CATALOG.md) | Algorithm design and mathematical definitions for all strategies (S1 momentum, S2 mean reversion, S3 breakout deferred) |
| [`docs/BACKTESTING.md`](docs/BACKTESTING.md) | Backtesting framework: data layer, entry signals, engine, test suite *(rebuilt 2026-05-15 — engine shares one deterministic exit path with the live monitor; pre-2026-05-15 results were invalidated and superseded by a clean re-baseline)* |
| [`docs/GLOSSARY.md`](docs/GLOSSARY.md) | Every abbreviation and term used across the docs/config/code, each with a full explanation of how *this system* uses it — single source of truth |
| [`CLAUDE.md`](CLAUDE.md) | Claude Code collaboration rules: design decisions, testing standards, session startup checklist |
| [`TODO.md`](TODO.md) | Phase-by-phase implementation status |
| [`integration-test/SMOKE_TESTS.md`](integration-test/SMOKE_TESTS.md) | End-to-end smoke test checklist (SM-01 through SM-11) |

---

## Status

**Infrastructure: complete and tested** — 329 unit tests passing, both MCP servers run as Podman containers, end-to-end smoke tests (SM-01–SM-11) passed, backtesting framework rebuilt and operational (`docs/BACKTESTING.md`).

**Strategy: none deployable.** The Phase A audit closed 2026-05-18 on the kill-criterion — no validated edge in any strategy (`mean_reversion` dropped; `momentum` & `ORB` unvalidated). The project is at a strategic decision point; the next phase is the strategy debate in [`docs/STRATEGY_AUDIT.md`](docs/STRATEGY_AUDIT.md) Part 2. No new strategy is built before that concludes.
