# Scanner Stage — V2-Forward Integration (Prompt 13)

## Overview

As of Prompt 13, V2 is the **primary scanner architecture**.  All four
implemented families route through V2 by default.  Legacy
(`StrategyService`) is isolated behind a narrow fallback seam and is
a retirement target for Prompt 15.

## Routing Model

```
V2-forward default:
  scanner_key → is_v2_supported(key)?
                 ├─ YES → V2 scanner (default)
                 └─ NO  → legacy StrategyService (fallback)
```

Override mechanism (emergency rollback):
- `set_scanner_version("iron_condor", "v1")` → forces legacy
- `clear_scanner_version_override("iron_condor")` → restores V2 default
- Stored in `_SCANNER_VERSION_OVERRIDES` dict (empty by default)

## V2 Families (all implemented)

| Family | Strategy IDs | Leg Count |
|--------|-------------|-----------|
| `vertical_spreads` | `put_credit_spread`, `call_credit_spread`, `put_debit`, `call_debit` | 2 |
| `iron_condors` | `iron_condor` | 4 |
| `butterflies` | `butterfly_debit`, `iron_butterfly` | 3-4 |
| `calendars` | `calendar_call_spread`, `calendar_put_spread`, `diagonal_call_spread`, `diagonal_put_spread` | 2 |

**Total: 11 options scanner keys + 4 stock scanner keys = 15 pipeline entries.**

## Execution Path Markers

Every scanner execution now carries an `_execution_path` marker:
- `"v2"` — ran through V2 scanner
- `"legacy"` — ran through `StrategyService`
- `"unknown"` — path not determined (e.g. stock scanners, skipped)

These markers appear in:
1. Raw scanner result (`_execution_path` field)
2. Execution record (`record["execution_path"]`)
3. Scanner summary (`scanner_summaries[key]["execution_path"]`)
4. Stage-level routing summary (`routing_summary.v2_scanners`, `.legacy_scanners`)

## Legacy Isolation

Legacy execution is isolated in `_execute_legacy_options_scanner()`:
- Clearly labeled as **RETIREMENT TARGET**
- Has its own `_LEGACY_STRATEGY_MAP` (maps strategy_type → plugin_id)
- Only reached when `should_run_v2()` returns False
- Will only run if:
  - A scanner_key has NO V2 implementation, OR
  - An explicit override forces a key to `v1`

## Manual Verification

Three verification tools available in `scanner_v2/verify.py`:

### `verify_v2_family(scanner_key, symbol, chain, price)`
Runs a single V2 scanner and returns rich diagnostics:
- Phase counts, reject reason counts
- Sample candidates (first 5)
- Diagnostics summary (passed/failed checks)

### `get_v2_routing_report()`
Returns comprehensive routing report including pipeline registry visibility.

### `get_family_verification_summary()`
Reports per-family readiness for legacy deletion:
```python
{
    "iron_condors": {
        "implemented": True,
        "all_keys_routing_v2": True,
        "all_keys_in_pipeline": True,
        "ready_for_legacy_deletion": True,
    },
    ...
}
```

## Key Files

| File | Purpose |
|------|---------|
| `app/services/scanner_v2/migration.py` | V2/legacy routing seam (RETIREMENT TARGET) |
| `app/services/pipeline_scanner_stage.py` | Step 6 handler — scanner execution |
| `app/services/scanner_v2/registry.py` | V2 family registry (source of truth) |
| `app/services/scanner_v2/verify.py` | Manual verification hooks |
| `tests/test_v2_scanner_stage_integration.py` | Integration tests (62 tests) |

## Prompt 15 Deletion Targets

When all families are manually validated, these can be deleted:
1. `_execute_legacy_options_scanner()` in `pipeline_scanner_stage.py`
2. `_LEGACY_STRATEGY_MAP` in `pipeline_scanner_stage.py`
3. Legacy side of `_default_scanner_executor()` (simplify to V2-only)
4. `migration.py` entirely — callers import V2 scanners directly
5. `_SCANNER_VERSION_OVERRIDES` mechanism
6. `strategy_service.py` options plugins (if no other consumers)
