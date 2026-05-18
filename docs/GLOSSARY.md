# Glossary — Abbreviations & Terms

Single source of truth for every abbreviation used across this repo's docs, config,
and code. Each entry explains what the term *is* and how *this system* uses it —
not a generic dictionary definition. Where a value is given it reflects the **code
and `config/strategies/*.yaml` as they currently are** (the authoritative source);
if another doc disagrees, the code wins.

**Companion to:** `docs/SYSTEM_DESIGN.md`, `docs/CFD_STRATEGY_CATALOG.md`,
`docs/BACKTESTING.md`, `docs/RESEARCH.md`, `docs/USER_GUIDE.md`.

---

## Contents

1. [Strategies & Signals](#1-strategies--signals)
2. [Indicators & Math](#2-indicators--math)
3. [Backtest & Performance Metrics](#3-backtest--performance-metrics)
4. [Bar Resolutions & Time](#4-bar-resolutions--time)
5. [Markets, Sessions & Instruments](#5-markets-sessions--instruments)
6. [System & Infrastructure](#6-system--infrastructure)
7. [Project & Process Terms](#7-project--process-terms)

---

## 1. Strategies & Signals

| Term | Meaning & how this system uses it |
|------|-----------------------------------|
| **CFD** | *Contract for Difference.* A leveraged derivative that tracks an underlying price without owning it. Every instrument this system trades on Capital.com is a CFD, which is why spread and margin (not commission) dominate the cost model. |
| **FX** | *Foreign Exchange* (forex). The currency-pair asset class in `watchlist.yaml` — EURUSD, GBPUSD, USDJPY, EURGBP. Treated as a session-bound asset class (London open 08:00 UTC) for ORB. |
| **S0** | *Random baseline (control).* Coin-flip direction at `min_size`. Not implemented — defined in `CFD_STRATEGY_CATALOG.md` §4 as the statistical noise floor a real strategy must beat (p < 0.05) before live promotion. |
| **S1 / momentum** | *EMA-crossover momentum* (trend-following). Implemented: `momentum.yaml` + `momentum.md` + `MomentumSignalState`. Runs on **M30** bars. Entry = pending EMA9/EMA21 crossover confirmed within `confirm_bars` (default 6) by gap + ADX + slope + M30-bias gates. |
| **S2 / MR** | *Mean Reversion* — z-score reversion (counter-trend). Implemented: `mean_reversion.yaml` + `mean_reversion.md` + `MeanReversionSignalState`. Runs on **M1** bars; fades moves beyond ±2σ of a rolling 20-bar mean in a non-trending regime. |
| **S3** | *Donchian channel breakout.* **Deferred** — no `breakout.yaml`/`.md` written. Documented for intent only in `CFD_STRATEGY_CATALOG.md` §7. |
| **S4** | *Sentiment overlay.* Not a standalone strategy — a reasoning modifier on S1/S3 using Capital.com client long/short %. To be folded into prompt modules; no YAML pair. |
| **S5 / ORB** | *Opening Range Breakout.* Implemented: `orb.yaml` + `orb.md` + `ORBSignalState`. Runs on **M15** bars. See **ORB** and **OR** below. |
| **MR** | Shorthand for **S2 / mean reversion** throughout the docs and metrics tables. |
| **ORB** | *Opening Range Breakout* — strategy S5. The first `or_bars` M15 bars of a session (default 2 = 30 min) define a high/low band; the first break of that band sets the trade direction (one trade per session). Research basis: Zarattini & Aziz (2024). |
| **OR** | *Opening Range* — the high/low band ORB builds from the session's first bars. `OR high = max(high)`, `OR low = min(low)` over the collection bars. The opposite OR boundary is the stop; TP = entry ± `OR_width × rr_ratio` (2.0). |
| **ITSM** | *Intraday Time-Series Momentum.* The academic effect (Gao, Han, Li & Zhou, JFE 2018) that the first half-hour return predicts the last half-hour return at ~30-min resolution. The reason momentum runs on **M30**, not M1 (see `RESEARCH.md`). |
| **TP** | *Take Profit.* The price level that closes a position in profit. Momentum: `ATR×min_rr_ratio` from entry (R:R ≥ 1.5). ORB: `entry ± OR_width × 2.0`. MR: fixed at entry, R:R 2.0. Monitor rule priority 3. |
| **SL** | *Stop Loss.* The hard price level that closes a losing position. `risk.stop_loss.default_pct` / `max_pct` in each strategy YAML. Monitor rule priority 1 — evaluated before everything else. |
| **R:R** (RR) | *Reward-to-Risk ratio.* Take-profit distance ÷ stop distance. Enforced at entry via `take_profit.min_rr_ratio` (momentum 1.5, MR 2.0, ORB 2.0). Sets the break-even win rate = `1 / (1 + R:R)`. |
| **R / 1R / R-multiple** | *Risk multiple.* 1R = one stop distance in price units. Trade P&L expressed in R makes results comparable across instruments regardless of price level. Basis of the **AvgR** backtest metric. |
| **P&L / PnL** | *Profit and Loss.* In the backtest, computed in price **points** (no contract size/sizing); `Trade.pnl_points` is net of spread via the fill-price model. |
| **bps** | *Basis points.* 1 bp = 0.01%. Used in `RESEARCH.md` for per-trade edge ("+2 to +6 bps"). |
| **LONG / SHORT** | Position direction. Maps to broker side **BUY / SELL**. `signal_engine` returns the literal strings `"LONG"`/`"SHORT"`/`None`; `notify_entry()` records the `"BUY"`/`"SELL"` side. |
| **NONE** | An explicit, valid decision output meaning *do not trade* — not an error. Claude is encouraged to output `action: NONE` when the noise regime is unfavourable. |

---

## 2. Indicators & Math

| Term | Meaning & how this system uses it |
|------|-----------------------------------|
| **EMA** | *Exponential Moving Average.* `EMA(p) = SMA(first p bars)` then `α·price + (1−α)·prev`, `α = 2/(p+1)`. Momentum uses EMA**9** (fast) and EMA**21** (slow); their crossover is the raw signal, their gap the noise filter, and their cross-back the momentum signal-exit. |
| **SMA** | *Simple Moving Average.* The unweighted seed for the EMA recursion (first `p` bars). |
| **WMA** | *Weighted Moving Average.* The catalog describes EMA as an "exponential WMA, span=N" — same thing as EMA above; the weighting is exponential. |
| **ATR** | *Average True Range.* Wilder-smoothed 14-bar volatility. Drives momentum stop/TP sizing (stop ≈ ATR₁₄@entry × 1.5, fixed for the trade) and vol-scaled position sizing. Computed incrementally inside `_ADXState` in `strategy/signal_engine.py` (so live and backtest ATR are bit-identical). |
| **ADX** | *Average Directional Index.* Wilder-smoothed trend-strength index over 14 bars (threshold **25**). Regime gate: momentum is *suppressed when ADX < 25* (no trend); mean reversion is *suppressed when ADX ≥ 25* (trending). Permissive while warming up. |
| **DX** | *Directional Movement Index.* The per-bar value ADX smooths: `DX = |+DI − −DI| / (+DI + −DI) × 100`. When ATR = 0 (flat market) DX is forced to 0. |
| **+DI / −DI (DI)** | *Plus / Minus Directional Indicator.* `+DI = smoothed(+DM)/ATR × 100`, similarly −DI. Their imbalance produces DX → ADX. |
| **+DM / −DM (DM)** | *Plus / Minus Directional Movement.* Per-bar up-move vs. down-move used to build the DI lines (Wilder's method). |
| **TR** | *True Range.* `max(high−low, |high−prevClose|, |low−prevClose|)`. The raw input Wilder-smooths into ATR. |
| **OHLC / OHLCV** | *Open, High, Low, Close (, Volume).* The bar shape. Stored in the `ohlc_bars` SQLite table; `OHLCBar` is the dataclass `signal_engine` consumes. |
| **GBM** | *Geometric Brownian Motion.* `dS = μ·S·dt + σ·S·dW` — the noise model in `CFD_STRATEGY_CATALOG.md` §2 explaining why most intraday signals fail (signal must beat dominant σ). |
| **σ / SD / sigma** | *Standard deviation.* MR entry threshold is ±2σ of the 20-bar close window; the catalog's exit zone and Bollinger/VWAP bands in `RESEARCH.md` are all expressed in σ. |
| **μ / mu** | *Mean* (rolling mean of close over the window) — and the drift term in the GBM equation. MR z-score numerator is `close − μ`. |
| **z / z-score / zₜ** | *Standardised deviation:* `z = (close − μ) / σ`. MR signal: SHORT if z ≥ +2.0, LONG if z ≤ −2.0. MR signal-exit fires when `|z| ≤ zscore_exit_threshold` (**0.5** in code). |
| **OLS** | *Ordinary Least Squares.* The regression behind "trend slope" (22-bar window) and the M30 directional-bias gate (rolling 30-bar slope). |
| **R² / R2** | *Coefficient of determination.* Cited from ITSM research (R² = 1.6–3.3% for first-half-hour → last-half-hour prediction). Not computed in code. |
| **H (Hurst)** | *Hurst exponent.* Measures trend-persistence vs. mean-reversion of a series. `RESEARCH.md` notes H ≈ 0.494–0.515 at M1 (≈ random walk) — the structural reason neither pure strategy has strong M1 edge. |
| **VWAP** | *Volume-Weighted Average Price.* The intraday institutional fair-value anchor recommended in `RESEARCH.md` as a future MR/directional filter. **Not yet implemented** — documented as a research direction only. |
| **BB** | *Bollinger Bands.* Mean ± kσ band. Referenced in `RESEARCH.md` (BB(10, 1.5) suggested for M1) — research context, not implemented; MR uses a raw z-score, not BB. |
| **RSI** | *Relative Strength Index.* Momentum oscillator suggested as a confirmation filter in `RESEARCH.md`. **Not implemented** — research context only. |
| **Donchian channel** | Highest-high / lowest-low band over k bars. The S3 breakout primitive — deferred, defined for intent in catalog §7 only. |
| **E / E_net** | *Expectancy / Net Expectancy.* `E = P(win)·avg(win) − P(loss)·avg(loss)`; `E_net = E − spread − slippage`. The canonical go/no-go metric (catalog §3): `E_net > 0` over ≥ 30 trades before live. |
| **P(win) / P(loss)** | Probability (empirical frequency) of a winning / losing trade. Inputs to expectancy; tracked per strategy per asset. |

---

## 3. Backtest & Performance Metrics

| Term | Meaning & how this system uses it |
|------|-----------------------------------|
| **PF** | *Profit Factor* = gross profit ÷ gross loss. < 1.0 loses money; 1.0–1.2 marginal; > 1.3 meaningful edge; `inf` = no losing trades (usually a tiny sample). Primary backtest health metric. |
| **AvgR** | *Average R per trade* = `net_pnl_pts / (n × avg_entry × stop_pct)`. Instrument-price-agnostic expectancy in risk multiples. > +0.10R with 30+ trades = strong; ≤ 0.00R = no edge. |
| **MaxDD / MaxDD% / DD** | *Maximum Drawdown.* Largest peak-to-trough cumulative-P&L drop, as % of average entry price (same normalisation as AvgR, so comparable across instruments). |
| **Win%** | Fraction of completed trades with `pnl_points > 0`. Must be read with PF and R:R — a 40% Win% with PF 2.0 is still profitable. Break-even Win% = `1/(1+R:R)`. |
| **Stop%** | Fraction of trades closed by the **hard stop**. > 50–60% suggests the stop is too tight or the signal fires into adverse conditions. |
| **Sig/wk** | *Signals per week* — entry frequency over the data's wall-clock span (gap-aware, not bar count). < 1 = too selective for that instrument; > 15 = likely noise. |
| **net_pnl_pts** | Sum of per-trade `pnl_points` in raw price units, **net of spread** (spread is embedded in fill prices). The un-normalised basis for AvgR. |
| **entry_mid / exit_mid** | Un-spread-adjusted prices (`next_bar.open` / `bar.close`). With `spread_at_entry` they allow gross-vs-net cost decomposition per trade. |
| **BacktestResult / Trade** | The engine's output dataclasses (`backtest/engine.py`). `BacktestResult` = aggregate metrics; `Trade` = per-trade record (entry/exit, levels, `exit_reason`, `pnl_points`, `resolution`). |
| **End of data** | An `exit_reason`: a position still open when bars run out is closed at the last close. Included in all metrics; expected only for the final partial day. |

---

## 4. Bar Resolutions & Time

| Term | Meaning & how this system uses it |
|------|-----------------------------------|
| **M1** | 1-minute bars. The data fetched/stored (`ohlc_bars`) and aggregated up in-process. **Mean reversion** runs natively at M1 (`mean_reversion.yaml resolution: M1`). |
| **M5 / M15 / M30 / M60** | Aggregated 5/15/30/60-minute bars (`backtest/aggregate.py`, `aggregate_bars`). **ORB** runs at **M15**; **momentum** runs at **M30** (its YAML `resolution:` — the single source of truth shared by the live monitor and backtest). |
| **H1** | 1-hour bars. A supported `fetch_ohlc.py` resolution (deeper MT5 history than M1); equivalent to M60. |
| **resolution** | A first-class **strategy property** — each `*.yaml` carries `resolution:` (momentum M30, mean_reversion M1, orb M15). The live monitor and backtest both read it; the backtest `--resolution` CLI flag is an explicit experiment override only. |
| **UTC** | *Coordinated Universal Time.* All `ohlc_bars` timestamps, session open/close times, and the backtest daily-close model (`--session-close-utc`, default **21:00**) are UTC. The monitor's time-exit clock is injectable (real UTC live; bar-time in backtest). |
| **ET** | *US Eastern Time.* Used in `RESEARCH.md` time-of-day tables (e.g. "avoid 11 AM–2 PM ET"). Operator guidance, not coded. |
| **GMT** | *Greenwich Mean Time.* Effectively UTC; used in `RESEARCH.md` liquidity-window notes (London open 8–9 AM GMT). |
| **DST** | *Daylight Saving Time.* Session open times are fixed UTC, so European instruments drift ±1h around March/October clock changes — a documented, accepted backtest approximation (~2 weeks/year). |

---

## 5. Markets, Sessions & Instruments

| Term | Meaning & how this system uses it |
|------|-----------------------------------|
| **NYSE** | *New York Stock Exchange.* Session model for **US500** — open 14:30 UTC (`backtest/sessions.py`). |
| **LSE** | *London Stock Exchange.* Session model for **UK100** — open 08:00 UTC. |
| **Xetra** | Deutsche Börse's electronic trading venue. Session model for **DE40** — open 08:00 UTC. The 08:00 auction is the order-flow imbalance ORB exploits on European indices. |
| **ICE** | *Intercontinental Exchange.* Venue context for Brent (**XBRUSD**); modelled with the 08:00 UTC London open for ORB. |
| **GOLD → XAUUSD** | Watchlist epic `GOLD` is symbol `XAUUSD` in MT5. `fetch_ohlc.py` translates at write time; the `ohlc_bars.epic` column always stores the watchlist name `GOLD`. |
| **XBRUSD → BRENTOIL** | Watchlist epic `XBRUSD` (Brent crude) is `BRENTOIL` in MT5. Same write-time translation as GOLD. |
| **Instrument epics** | The 11-name universe in `config/watchlist.yaml`: FX `EURUSD GBPUSD USDJPY EURGBP`; indices `US500 DE40 UK100`; commodities `GOLD XBRUSD`; crypto `BTCUSD ETHUSD`. "Epic" = Capital.com's instrument identifier. |
| **NFP** | *Non-Farm Payrolls.* A high-impact US economic release. No news-calendar API exists, so the operator must manually avoid running MR around NFP (catalog §6.2). |
| **FOMC** | *Federal Open Market Committee.* US rate-decision events — same manual-avoidance caveat as NFP. |

---

## 6. System & Infrastructure

| Term | Meaning & how this system uses it |
|------|-----------------------------------|
| **MCP** | *Model Context Protocol.* How Claude Code/Desktop reaches this system's tools. `server.py` exposes 7 MCP tools via FastMCP over streamable-HTTP. Note: cfd-trading imports `CapitalClient` directly — it does **not** call capital-mcp-server over MCP internally. |
| **API** | *Application Programming Interface.* Usually the Capital.com REST API (market data, execution) — blocked entirely during backtests by `BACKTEST_MODE=true`. |
| **REST** | *Representational State Transfer.* The HTTP style of the Capital.com client API. |
| **CLI** | *Command-Line Interface.* `python -m cfd_trading.backtest.run …` — the backtest entry point (`backtest/run.py`). |
| **HTTP / HTTPS** | Hypertext Transfer Protocol (Secure). MCP endpoints are served over HTTPS: `https://localhost:8089/mcp` (cfd-trading), `:8088` (capital-mcp-server). |
| **TLS** | *Transport Layer Security.* The HTTPS layer. Local certs via `mkcert` in `~/dev/trading/certs/`, mounted read-only into both containers (`SSL_CERTFILE`/`SSL_KEYFILE`). |
| **CA** | *Certificate Authority.* The `mkcert` root CA must be trusted on Windows for Claude Desktop; `NODE_OPTIONS=--use-system-ca` lets mcp-remote trust it. |
| **WSL2** | *Windows Subsystem for Linux 2.* The runtime host. Containers run via **Podman** (not Docker) inside WSL2; keep `trading.db` on the Linux FS for I/O speed. |
| **MT5** | *MetaTrader 5.* The historical-bar source. `fetch_ohlc.py`/`probe_history.py` run on **Windows** Python (MT5 uses Windows IPC) and write `ohlc_bars` to the shared SQLite DB. |
| **IPC** | *Inter-Process Communication.* Why the MT5 fetch scripts must run on Windows, not WSL2. |
| **SQLite** | The embedded SQL database (`trading.db`): live trade/session/reasoning tables plus the `ohlc_bars` backtest store. Migration target is Postgres/RDS on AWS (deferred). |
| **SQL** | *Structured Query Language.* The query language for the SQLite store. |
| **JSON / JSONL** | *JavaScript Object Notation (Lines).* The proposal contract is JSON; the audit sidecar (`audit.jsonl`) is one JSON object per line for easy `grep`. |
| **YAML** | *"YAML Ain't Markup Language."* Strategy risk bounds and config (`risk.yaml`, `watchlist.yaml`, `strategies/*.yaml`). The YAML files are ground truth over the catalog. |
| **MD** | *Markdown.* Strategy prompt modules (`*.md`) injected into Claude's context, and all `docs/`. |
| **CI** | *Continuous Integration.* GitHub Actions: unit tests always; integration tests on push with demo secrets; `publish.yml` builds/pushes the container image. |
| **AWS / RDS** | *Amazon Web Services / Relational Database Service.* The deferred v2 deployment + SQLite→Postgres migration target. |
| **UUID** | *Universally Unique Identifier.* The `sessions.id` primary key. |
| **JFE** | *Journal of Financial Economics.* The journal of the Gao et al. (2018) ITSM paper underpinning the M30 momentum design. |

---

## 7. Project & Process Terms

| Term | Meaning & how this system uses it |
|------|-----------------------------------|
| **v1 / v2** | Version scope markers. v1 = session-bound monitor, single broker, manual approval gate. v2 = deferred items (persistent daemon, broker generalisation, AutoGate, S3/S4, AWS). |
| **TBD** | *To Be Determined.* Placeholder for unset values (e.g. S3 risk bounds in catalog §11) — flags "not yet decided", distinct from a real value. |
| **SM-01 … SM-11** | The human smoke-test IDs in `integration-test/SMOKE_TESTS.md` — infra checks (SM-01/02) → read-only broker calls (SM-03–05) → validation (SM-06/07) → live demo trades (SM-08–11). |
| **A1 / A2 / A3b / A4 / A6** | Phases of the **closed** post-Phase-10 strategy audit — see `docs/STRATEGY_AUDIT.md` (superseded the workspace `AUDIT_PLAN.md`/`audit/`, both removed 2026-05-18). A4 = news-proximity, carried into the next-phase strategy debate. |
| **Phase 0 … Phase 10** | The implementation roadmap in `SYSTEM_DESIGN.md` §8 / `TODO.md`. Phase 10 = the 2026-05-15 backtest rebuild (shared deterministic exit path). |
| **signal_engine** | `strategy/signal_engine.py` — the **shared** streaming signal module imported by *both* `monitor.py` and `backtest/engine.py`, so live and backtest entry/exit logic cannot drift. (Supersedes the former `backtest/signals.py`.) |
| **preflight** | `risk/preflight.py` — validates a proposal JSON against `risk.yaml` + the strategy YAML bounds before execution. Hard-rejects out-of-bounds proposals. |
| **monitor** | `monitor/monitor.py` — the autonomous, AI-free rule engine. Evaluates the ordered exit rules (hard stop → trailing → TP → signal-exit → time-exit) every `MONITOR_INTERVAL_SECONDS` (default 60). |
| **signal-exit** | Monitor rule (priority 4): a deterministic per-strategy reversal predicate via `signal_engine` — MR `|z| ≤ 0.5`, momentum EMA cross-back, ORB none. Runs every 60 s; no longer dependent on Claude noticing. |
| **BACKTEST_MODE** | Env var; when `true`, `CapitalClient` raises at instantiation — guarantees no live API call is possible during a backtest. Set automatically by `run.py`. |
| **doc-sync** | The mandatory rule (workspace `CLAUDE.md`): config/transport/endpoint/env/CLI changes must update every file in the corresponding doc-sync table *in the same change*. |
