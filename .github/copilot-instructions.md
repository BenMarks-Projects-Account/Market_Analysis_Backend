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

When finishing a task:
- Provide a concise summary of changes and where they live (files/modules).
- List risks/assumptions explicitly.
- Do NOT claim tests pass unless you actually ran them in this environment.
- Only update README.md / Architecture.md if the task explicitly changes contracts/architecture.