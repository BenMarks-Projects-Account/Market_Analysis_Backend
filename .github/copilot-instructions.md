You are working on the BenTrade application.
 
BenTrade is a personal institutional-grade trading platform for options income and stock swing trading. It combines quantitative scanning, market intelligence, and AI-assisted analysis to surface high-probability trades and manage active positions.
 
Core trading focus:
- Options income: high-probability, risk-defined strategies (credit spreads, iron condors, butterflies, calendars/diagonals) on liquid index ETFs (SPY, QQQ, IWM, DIA) with ~196-stock universe
- Stock swing trading: pullback swings, momentum breakouts, mean reversion, volatility expansion across the stock universe
- Expected value (EV) and probability-based trade selection
- Moderate, consistent income rather than aggressive speculation
- Portfolio-level risk management with position sizing, concentration limits, and Greek budgets
 
Platform architecture:
- Market Intelligence Runner: 6 engines (Volatility, Flows & Positioning, Breadth, Cross-Asset Macro, Liquidity, News Sentiment) producing regime classification (RISK_ON / NEUTRAL / RISK_OFF)
- Stock Opportunity Runner: 4 scanners (pullback swing, momentum breakout, mean reversion, volatility expansion) → technical scoring → LLM analysis (TMC + strategy prompts) → ranked candidates
- Options Opportunity Runner: V2 scanner with 11 scanner keys across 4 families (vertical spreads, iron condors, butterflies, calendars/diagonals) → Phase A-F pipeline → credibility gate → ranked candidates
- Active Trade Pipeline: 7-stage position analysis (load → market context → build packets → deterministic engine → LLM analysis → normalize → complete) with HOLD/REDUCE/CLOSE/URGENT_REVIEW recommendations
- Trade Management Center (TMC): unified control surface for all workflows — stock scanning, options scanning, position analysis, and portfolio balancing
- Trading execution: Tradier integration with preview → confirm → submit flow, multi-leg order builder, dual account support (live + paper)
 
Data sources:
- Tradier: source of truth for option chains, option quotes, stock quotes, account positions, order execution, and Greeks. Dual credential support for live and paper (sandbox) accounts.
- FRED (Federal Reserve Economic Data): macro data — Treasury yields (DGS2, DGS10, DGS30), fed funds rate (DFF), credit spreads (BAMLC0A0CM, BAMLH0A0HYM2), oil (DCOILWTICO), USD index (DTWEXBGS), copper (PCOPPUSDM), CBOE SKEW index
- Finnhub: news headlines and sentiment, earnings calendar, economic event calendar (FOMC, CPI, NFP)
- Polygon.io: historical OHLCV bars for stock scanning and technical indicators (replaced Yahoo Finance for reliability)
- Yahoo Finance (via yfinance): sector/industry classification (fallback only)
 
Non-negotiables (must follow):
 
1) Data integrity is the top priority.
   - All calculations must be traceable from API inputs → normalized objects → UI outputs.
   - Never fabricate or "fill in" market values.
   - Null/undefined is preferred over incorrect numbers.
   - Any derived field must list its input fields and formula in code comments.
   - Observation dates are the authoritative staleness indicator for FRED data, not fetched_at timestamps.
   - Every engine metric should be tagged with its provenance (direct, derived, proxy, proxy_of_proxy) via SIGNAL_PROVENANCE.
 
2) Canonical trade structure (single source of truth).
   - All strategies must map into ONE normalized trade object shape.
   - Per-contract values are the standard for UI metrics.
   - Do not introduce duplicate strategy names or aliases.
   - Strategy IDs are stable: put_credit_spread, call_credit_spread, put_debit, call_debit, iron_condor, butterfly_debit, iron_butterfly, calendar_call_spread, calendar_put_spread, diagonal_call_spread, diagonal_put_spread.
 
3) Data source policy.
   - Tradier is source of truth for: option chains, option quotes, stock quotes, execution-critical pricing, account positions, Greeks.
   - FRED is source of truth for: macro economic indicators (yields, credit spreads, commodities, USD).
   - Finnhub is source of truth for: news sentiment, economic event calendar, earnings dates.
   - Polygon.io is source of truth for: historical OHLCV bars, technical indicator computation inputs.
   - Yahoo Finance is used for: sector/industry classification only (fallback, non-authoritative for pricing).
   - If data from a non-authoritative source could change trade acceptance, treat it as non-authoritative unless explicitly approved.
 
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
   - Delta pre-filtering on short strikes (0.05-0.40 range) is the primary construction filter for income strategies.
   - Generation caps must distribute budget per-expiration to prevent FIFO DTE bias.
 
6) Frontend philosophy (UI consistency).
   - The Trade Management Center (TMC) is the primary user interface for all workflows.
   - TradeCard is the single UI primitive for displaying a trade.
   - Card action footer buttons must remain visible when collapsed and expanded.
   - Tooltips must use the app-standard TooltipProvider pattern (no one-off tooltip systems).
   - Provide a Data Workbench entry point (modal/route) when asked to diagnose data.
   - Account mode selection (live/paper) must be available in TMC for position-related workflows.
 
7) Simplicity over complexity.
   - Remove unused/obsolete code.
   - Prefer one clear path over multiple legacy paths.
   - Avoid adding new frameworks or major architecture changes unless explicitly requested.
   - The TMC workflow model replaces standalone pipeline dashboards — do not build new standalone UIs.
 
8) Stability during cleanup.
   - Make small, testable steps.
   - Preserve existing working features.
   - Prefer additive instrumentation before changing strategy logic/thresholds.
 
9) Model analysis and LLM prompts.
   - Every system prompt must include the anti-injection security preamble as the first content.
   - LLM prompts must not ask for fields the model cannot compute from available data — remove fields rather than allow fabrication.
   - Conviction below 60 on EXECUTE/BUY recommendations must be coerced to PASS.
   - Default conviction/score on parse failure is 10 (not 50) — low defaults must be distinguishable from real scores.
   - Engine confidence (0-100 scale) must be normalized to 0-1 before use in regime weighting.
 
10) Active trade and portfolio management.
    - Active trade analysis must support multi-leg strategy reconstruction from flat Tradier positions (iron condors, butterflies, verticals, singles).
    - Both option and equity positions must flow through the analysis pipeline.
    - Event calendar must flag expiration-specific conflicts (position expires through FOMC/CPI).
    - Portfolio context (concentration, delta contribution, risk budget) must be available to the analysis engine and LLM.
    - Close order generation should accompany CLOSE/REDUCE recommendations when possible.
    - Greeks must be refreshed from live chain data, not stale entry-time values.
 
11) Testing scope and execution rules.
 
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
 
12) Documentation maintenance.
    - Update docs/architecture/bentrade_decision_system_current_state.md whenever a change affects system architecture, pipeline stages, data flow, or engine/scanner behavior.
    - Update README.md whenever a change affects setup instructions, environment variables, or user-facing features.
    - Architecture doc updates should reflect the current state of the system — rewrite affected sections to be accurate, not append change logs.
    - Include the date and fix/feature ID in architecture doc updates for traceability.
 
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
   - Update architecture docs if the change affects system behavior, data flow, or pipeline stages.