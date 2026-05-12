# CFD Trading System

AI-driven intraday CFD trading using Claude Code and Capital.com. Claude handles the full entry flow — scan markets, analyse instruments, propose and execute trades. A rule-based monitor manages open positions autonomously between entry cycles.

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
| [`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md) | Architecture, design decisions, MCP tools, config schemas, SQLite schema, implementation status |
| [`docs/CFD_STRATEGY_CATALOG.md`](docs/CFD_STRATEGY_CATALOG.md) | Algorithm design and mathematical definitions for all strategies (S1 momentum, S2 mean reversion, S3 breakout deferred) |
| [`docs/BACKTESTING.md`](docs/BACKTESTING.md) | Backtesting framework: data layer, entry signals, engine, test suite, results interpretation |
| [`CLAUDE.md`](CLAUDE.md) | Claude Code collaboration rules: design decisions, testing standards, session startup checklist |
| [`TODO.md`](TODO.md) | Phase-by-phase implementation status |
| [`integration-test/SMOKE_TESTS.md`](integration-test/SMOKE_TESTS.md) | End-to-end smoke test checklist (SM-01 through SM-11) |

---

## Status

All phases complete. 204 unit tests passing. Both MCP servers running as Podman containers. End-to-end smoke tests (SM-01–SM-11) passed. Backtesting framework operational — `docs/BACKTESTING.md` for how to run.
