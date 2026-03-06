# BenTrade Architecture Overview

> Concise one-page reference. For the full snapshot see [ARCHITECTURE.md](../../ARCHITECTURE.md).

---

## System Layout

```
┌─────────────────────────────────────────────────────┐
│  Frontend (SPA)                                     │
│  BenTrade/frontend/                                 │
│  - index.html + vanilla JS modules                  │
│  - Dashboards per strategy + Home / OE              │
│  - TradeCard as canonical display component         │
└──────────────────────┬──────────────────────────────┘
                       │  REST API (FastAPI)
┌──────────────────────▼──────────────────────────────┐
│  Backend                                            │
│  BenTrade/backend/app/                              │
│                                                     │
│  api/          Routes (reports, strategies, model)   │
│  services/     Strategy plugins, ranking, scanner   │
│  clients/      Tradier, Polygon, FRED, Finnhub      │
│  models/       Pydantic schemas                      │
│  utils/        normalize.py, trade_key.py,           │
│                computed_metrics.py                    │
│  trading/      Paper & live broker integration       │
│  storage/      File-based persistence (JSON/JSONL)   │
│                                                     │
│  common/       Shared quant logic                    │
│    quant_analysis.py  — enrich, CreditSpread metrics │
│    model_analysis.py  — model-level trade analysis   │
└─────────────────────────────────────────────────────┘
```

---

## Data Sources Policy

| Source | Role | Authoritative For |
|---|---|---|
| **Tradier** | Primary | Option chains, quotes (bid/ask/greeks/OI/volume), underlying price |
| Polygon | Secondary | Price history (IV/RV calc, charting) |
| FRED | Secondary | Macro rates, VIX term structure |
| Finnhub | Secondary | Company fundamentals, earnings calendar |

- Tradier is **source of truth** for all execution-critical data.
- Non-Tradier data must not change trade acceptance unless explicitly approved.
- See [data-quality-rules.md](../standards/data-quality-rules.md) for full policy.

---

## Scanner Pipeline

```
Tradier chains ──▸ Plugin.build_candidates()
                        │
                        ▼
                   Plugin.enrich()  ── quant_analysis.enrich_trades_batch()
                        │
                        ▼
                   Plugin.evaluate()  ── gate filters (ordered stages)
                        │
                        ▼
                   Plugin.score()  ── composite ranking
                        │
                        ▼
                   normalize_trade() + apply_metrics_contract()
                        │
                        ▼
                   Scanner output: { accepted_trades, filter_trace, ... }
```

- Every scanner produces a **filter trace** documenting preset, thresholds, stage counts, and rejection reasons.
- See [scanner-contract.md](../standards/scanner-contract.md) for the required output schema.
- Preset definitions: [presets.md](../standards/presets.md).
- Rejection codes: [rejection-taxonomy.md](../standards/rejection-taxonomy.md).

---

## Key Modules

| Module | Path | Purpose |
|---|---|---|
| Strategy Service | `app/services/strategy_service.py` | Orchestrates plugin lifecycle; applies preset defaults |
| Normalize | `app/utils/normalize.py` | Single source of truth for trade output shape |
| Trade Key | `app/utils/trade_key.py` | `canonicalize_strategy_id()` — resolves aliases |
| Computed Metrics | `app/utils/computed_metrics.py` | Stable `computed_metrics` + `metrics_status` |
| Scanner Orchestrator | `frontend/assets/js/stores/scannerOrchestrator.js` | Runs scanners from the frontend with retry/backoff |
| Snapshot Capture | `app/services/snapshot_capture_service.py` | Captures complete market datasets for offline replay |
| Offline Replay | `app/utils/snapshot_offline.py` | `ManifestSnapshotSource` + `OfflineLiveCallGuard` |
| Snapshot Manifest | `app/models/snapshot_manifest.py` | Pydantic schema for snapshot datasets |
| Snapshot API | `app/api/routes_snapshots.py` | Admin endpoints: capture, list, inspect snapshots |

---

## Snapshot Capture & Offline Replay

The manifest-based snapshot system captures complete market datasets (chains, quotes, history, VIX, regime) and replays them offline with zero live API calls.

```
Capture (live market)                    Replay (offline)
  SnapshotCaptureService.capture()         ManifestSnapshotSource.from_trace_id()
    ├── Tradier chains + quotes              ├── get_chain()       → disk
    ├── Polygon bars + closes                ├── get_underlying_price() → disk
    ├── FRED VIX                             ├── get_prices_history()   → disk
    └── Regime classification                └── get_vix()              → disk
         │                                        │
         ▼                                   OfflineLiveCallGuard
    snapshot_manifest.json                     └── 14 live methods → raise error
    + per-symbol data files
```

- `StrategyService.generate()` auto-detects manifest snapshots (by `snapshot_id` or latest for strategy)
- `BaseDataService.get_analysis_inputs()` has a dedicated manifest branch — all data from disk
- See [ARCHITECTURE.md](../../ARCHITECTURE.md) for full details
