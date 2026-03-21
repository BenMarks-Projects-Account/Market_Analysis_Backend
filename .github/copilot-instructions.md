You are working on the BenTrade application.

BenTrade is an options trading analysis platform focused on:
- High-probability, risk-defined options strategies
- Expected value (EV) and probability-based trade selection
- Moderate, consistent income rather than aggressive speculation
- Primarily index ETFs (SPY, QQQ, IWM, DIA, XSP, RUT, NDX)

Non-negotiables (must follow):
1) Data integrity is the top priority.
   - All calculations must be traceable from API inputs → normalized objects → UI outputs.
   - Never fabricate or “fill in” market values.
   - Null/undefined is preferred over incorrect numbers.
   - Any derived field must list its input fields and formula in code comments.

2) Canonical trade structure (single source of truth).
   - All strategies must map into ONE normalized trade object shape.
   - Per-contract values are the standard for UI metrics.
   - Do not introduce duplicate strategy names or aliases.

3) Data source policy.
   - Tradier is source of truth for: option chains, option quotes, execution-critical pricing.
   - Other providers (Yahoo/Finnhub/FRED) are for underlying analysis / macro / fallback only.
   - If data from non-Tradier could change trade acceptance, treat it as non-authoritative unless explicitly approved.

4) Scanner contract and explainability (REQUIRED for any scanner work).
   - Every scanner run MUST produce a filter trace:
     - preset name used
     - resolved thresholds (final numeric values)
     - ordered stage_counts (candidates remaining after each stage)
     - rejection reason counts (taxonomy must be stable)
     - data-quality counts (missing/invalid bid/ask/mid/OI/volume/IV/delta/credit/width/etc.)
   - Never silently drop candidates. Every rejection must map to a reason code.
   - Strict / Balanced / Wide presets MUST resolve to meaningfully different thresholds and be verifiable via trace.
   - Preset resolution must be centralized in one function/module (no scattered defaults).

5) Filter/order correctness.
   - Validate quote integrity before any liquidity/spread/EV gates.
   - Do not treat missing fields as 0 unless explicitly stated; missing must be tracked separately as data-quality failures.

6) Frontend philosophy (UI consistency).
   - TradeCard is the single UI primitive for displaying a trade.
   - Card action footer buttons must remain visible when collapsed and expanded.
   - Tooltips must use the app-standard TooltipProvider pattern (no one-off tooltip systems).
   - Provide a Data Workbench entry point (modal/route) when asked to diagnose data.

7) Simplicity over complexity.
   - Remove unused/obsolete code.
   - Prefer one clear path over multiple legacy paths.
   - Avoid adding new frameworks or major architecture changes unless explicitly requested.

8) Stability during cleanup.
   - Make small, testable steps.
   - Preserve existing working features.
   - Prefer additive instrumentation before changing strategy logic/thresholds.

9)Testing Scope and Execution Rules

These rules are mandatory for all implementation work unless the prompt explicitly overrides them.

### Mandatory testing limits
- Run only the narrowest targeted tests relevant to the files changed.
- Do not run the full suite unless explicitly asked.
- Do not retry or expand testing just because unrelated failures appear.
- Do not chase unrelated regressions, flaky tests, collection errors, or legacy failures.

### Out-of-scope failures
The following are out of scope by default unless the task explicitly asks for them:
- pre-existing failing tests
- pre-existing collection/import errors
- flaky or intermittent failures
- failures in unrelated modules
- broad regression cleanup

If any of these appear, report them and stop. Do not broaden the task.

### Preferred validation behavior
- Use the smallest test command that proves the requested change.
- Add or update the narrowest possible automated test if coverage is needed.
- Stop once the requested behavior is validated.
- Report targeted results cleanly without converting the task into a repo-wide stabilization effort.

### Reporting format
Include:
- exact tests run
- whether targeted tests passed
- any unrelated failures encountered
- a note that unrelated failures were not addressed because they were outside scope

Anchor docs (work must conform to these standards):
- docs/architecture/bentrade_decision_system_current_state.md — durable current-state reference for the full decision system (scanners, engines, prompts, workflows, gaps, target direction). Read before any architecture-level work.
- docs/standards/scanner-contract.md — required scanner output fields and filter trace schema.
- docs/standards/rejection-taxonomy.md — stable rejection reason codes; never rename, only add.
- docs/standards/presets.md — Strict / Balanced / Wide preset philosophy, required knobs, verification rule.
- docs/standards/ui-tradecard-spec.md — TradeCard as single primitive, footer visibility, tooltip rules.
- docs/standards/data-quality-rules.md — quote integrity, missing-field policy, source-of-truth summary.
- docs/standards/canonical-contract.md — canonical trade structure and strategy IDs.

When finishing a task:
- Provide a concise summary of changes and where they live (files/modules).
- List risks/assumptions explicitly.
- Do NOT claim tests pass unless you actually ran them in this environment.
- Only update README.md / Architecture.md if the task explicitly changes contracts/architecture.