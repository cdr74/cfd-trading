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

After each major component is implemented, a human smoke test verifies the system behaves correctly end-to-end. These tests cannot be automated — they require human judgement and observation.

Each smoke test section lives in the relevant module's docstring or in a dedicated `docs/smoke_tests/` file. The format is always:

```
PRECONDITIONS  — what must be true before starting
STEPS          — exact actions to take, numbered
EXPECTED       — what you should see at each step
PASS CRITERIA  — what constitutes a successful test
CLEANUP        — how to restore state after the test
```

**Core smoke tests to define as each component is built:**

### SM-01 — Session Start
Verify `start_session` authenticates, detects open positions, and starts the monitor subprocess correctly.

### SM-02 — Market Scan
Verify `scan_markets` returns a ranked list of instruments with plausible ATR and trend data. Confirm the output makes sense given current market conditions.

### SM-03 — Strategy Analysis
Verify `analyze_instrument` returns structured context that Claude can reason over. Confirm Claude's strategy recommendation is coherent.

### SM-04 — Proposal + Preflight
Verify `validate_proposal` correctly accepts a valid proposal and rejects one that violates risk bounds. Confirm Claude's proposal JSON matches the schema in README.md.

### SM-05 — Trade Execution (demo only)
Verify `execute_trade` opens a real position on the demo account. Confirm the deal appears in Capital.com and in the local SQLite DB.

### SM-06 — Monitor Cycle
Verify `monitor.py` runs at the configured interval. Confirm it fetches live positions, evaluates rules, and writes a cycle snapshot to the DB. Observe at least one HOLD decision in the logs.

### SM-07 — Session End (close)
Verify `end_session(close_positions=True)` stops the monitor, closes all open positions, and writes the session summary.

### SM-08 — Session End (leave open)
Verify `end_session(close_positions=False)` stops the monitor, leaves positions open at Capital.com with stop losses registered, and writes the session summary.

---

## Session Startup Checklist

At the start of every implementation session:

1. Read `README.md` — confirm you understand the current design state
2. Read `TODO.md` — identify what is in progress and what is next
3. Check `git status` — understand what has already been changed
4. Ask if anything is unclear before writing a single line of code
