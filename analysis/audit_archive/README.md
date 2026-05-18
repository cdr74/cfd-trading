# Audit Archive (read-only, historical)

Reproducibility artifacts for the Phase A strategy audit. **The narrative,
findings, verdicts and conclusion live in `cfd-trading/docs/STRATEGY_AUDIT.md`**
— this folder only preserves the means to re-derive the numbers. The audit is
CLOSED (kill-criterion triggered); these files are not maintained.

## Contents

- `trades_M15.parquet` — the fidelity-true re-baseline trade log (19,061
  trades; momentum M30, MR/ORB M15; 2023-05-16 → 2026-05-14).
- `a2_slicing.py` — session boundaries + per-cell slicing helpers (imported by
  the others).
- `a3b_zexit_sim.py` — MR z-invalidation + price-persistence read-only sims.
- `a3b_stopcap_sim.py` — MR stop-cap probe; also emits the per-trade ATR×2.5
  rows used for the FX+indices step-2 slice.
- `a3b_*_sweep.csv` / `a3b_*_by_cell.csv` / `a3b_stopcap_atrx25_trades.csv` —
  sweep outputs.
- `ranked_cells.csv` / `ranked_cells_thin.csv` — per-cell expectancy ranking.
- `build_trade_inspection_nb.py` — regenerates the (deleted) inspection
  notebook from the parquet if ever needed.

Deleted as derivable/bulky: 9 `heatmap_*.png`, `trade_inspection.ipynb`.

## Re-run

```bash
cd ~/dev/trading/cfd-trading && source .venv/bin/activate
# scripts read trades_M15.parquet from their own dir and the OHLC DB at
# /mnt/c/Users/chris/dev/trading-data/trading.db (hardcoded)
python ~/dev/trading/cfd-trading/analysis/audit_archive/a3b_stopcap_sim.py
```

ORB Deflated-Sharpe / bootstrap / OOS figures were computed read-only from
`trades_M15.parquet` with the method documented in `STRATEGY_AUDIT.md §5`.
