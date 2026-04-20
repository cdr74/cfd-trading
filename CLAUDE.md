# CLAUDE.md — Collaboration Guide for Claude Code

This file governs how we work together on this project. Read it at the start of every implementation session alongside README.md.

---

## 1. Design Decisions — Always Interactive

**Never assume. Always ask.**

If anything is unclear, ambiguous, or has multiple reasonable approaches, stop and open an interactive Q&A session before writing code. Present the options with their trade-offs, ask for a preference, and only proceed once there is a clear decision.

This applies to:
- Architecture choices (how components connect, data flow, interface design)
- Scope questions (what belongs in v1 vs. deferred)
- Any deviation from what README.md specifies

When a design decision is made in conversation, update README.md before implementing. The README is the source of truth — the code follows it, not the other way around.

If an implementation reveals a conflict with a prior design decision, flag it explicitly rather than silently working around it.

---

## 2. Unit Tests — Written and Run Before Every Commit

Every piece of logic gets a unit test. No exceptions.

**The rule:** Do not commit code unless its unit tests pass. Run the full unit test suite before every `git commit`.

```bash
pytest tests/unit/ -v
```

**What needs unit tests:**
- `risk/preflight.py` — every validation rule, every edge case, every rejection path
- `monitor/monitor.py` — rule evaluation logic for every rule type and priority order
- `strategy/loader.py` — valid strategies load correctly, invalid YAML fails loudly, missing MD file is caught
- `storage/repository.py` — CRUD operations against an in-memory SQLite instance
- `tools/` — each MCP tool with mocked CapitalClient
- Any utility function with non-trivial logic

**Unit test principles:**
- No network calls. Mock `CapitalClient`.
- No file system side effects. Use `tmp_path` fixtures for anything that writes files.
- Tests must be fast — the full unit suite should run in under 30 seconds.
- Test the failure paths as thoroughly as the happy path.

---

## 3. Integration Tests — Capital.com Demo Account, Run in GitHub Actions

Integration tests run against the real Capital.com demo API. They are not run locally before every commit — they run automatically in GitHub Actions on every push to GitHub.

**Markers:**
```python
@pytest.mark.integration  # safe read-only calls against demo API
@pytest.mark.trade        # creates/modifies real demo positions — use sparingly
```

**Run locally when needed:**
```bash
pytest tests/integration/ -m integration -v
pytest tests/integration/ -m trade -v   # only when explicitly testing execution
```

**GitHub Actions workflow** (`.github/workflows/ci.yml`):
- Trigger: push/PR to any branch
- `unit-tests` job: `pytest tests/unit/ -v` — always runs, no credentials needed
- `integration-tests` job: `pytest tests/integration/ -m integration -v` — runs after unit-tests, uses demo API secrets
- Trade-marked tests are excluded from CI — they require manual run
- Secrets required: `CAPITAL_BASE_URL`, `CAPITAL_API_KEY`, `CAPITAL_IDENTIFIER`, `CAPITAL_API_KEY_PASSWORD`, `ANTHROPIC_API_KEY`

**What integration tests cover:**
- `broker/capital_client.py` — authenticate, get_prices, get_positions, create_position + close (trade-marked)
- `monitor/monitor.py` — one full monitor cycle against demo positions (trade-marked)
- `tools/` — each MCP tool called end-to-end against demo API (scan_markets, validate_proposal, etc.)

---

## 4. Human Smoke Tests — Session Verification Checklist

The authoritative smoke test guide is **`SMOKE_TESTS.md`** in the workspace root (`~/dev/trading/SMOKE_TESTS.md`). It covers SM-01 through SM-11 with full steps, pass criteria, and the test matrix.

Run `./mcp-start.sh` and `./mcp-status.sh` from the workspace root before any smoke test session. If the Claude Desktop config check fails, run `./mcp-fix-config.sh` and restart Claude Desktop.

The tests progress from infrastructure checks (SM-01: container health, SM-02: MCP discovery) through read-only broker calls (SM-03 to SM-05: session start, market scan, instrument analysis), proposal validation (SM-06/07), and finally live demo trades (SM-08: execute, SM-09: monitor cycle, SM-10/11: session end variants).

**Demo account required for SM-03 onwards.** All execution tests (SM-08 and above) modify broker state on the demo account.

---

## 5. Running the MCP Server (Development)

The MCP server runs as a Podman container. Build context is the parent `trading/` directory.

```bash
# Start (builds from source)
cd ~/dev/trading/cfd-trading
podman-compose -f podman-compose.dev.yml up --build -d

# Stop
podman-compose -f podman-compose.dev.yml down

# Logs
podman logs -f cfd-trading-dev
```

MCP endpoint: `https://localhost:8089/mcp`

**Important:** After any change to `server.py` or `tools/`, rebuild the container — the running image will not pick up source changes automatically.

**Venv note:** The local `.venv` is used for running tests only, not for serving. Because `capital-com-mcp-server` depends on `capital-com-client @ git+https://...` (a private repo), pip cannot resolve it automatically. Install the local clone manually:

```bash
pip install -e ~/dev/trading/capital-com-client/
pip install -e ".[dev]"
```

---

## Session Startup Checklist

At the start of every implementation session:

1. Read `README.md` — confirm you understand the current design state
2. Read `TODO.md` — identify what is in progress and what is next
3. Check `git status` — understand what has already been changed
4. Verify containers are running: `podman ps` — should show `cfd-trading-dev` and `capital-mcp-server`
5. Ask if anything is unclear before writing a single line of code
