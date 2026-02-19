You are working on the BenTrade application.

BenTrade is an options trading analysis platform focused on:
- High-probability, risk-defined options strategies
- Expected value (EV) and probability-based trade selection
- Moderate, consistent income rather than aggressive speculation
- Primarily index ETFs (SPY, QQQ, IWM, etc.)

Core design principles:
1) Data integrity is the top priority.
   - All calculations must be traceable from API inputs to UI outputs.
   - No fabricated or placeholder metrics.
   - Null is preferred over incorrect values.

2) Canonical trade structure.
   - All trades must flow through a single normalized trade object.
   - Per-contract values are the standard for UI metrics.
   - Avoid duplicate strategy names or aliases.

3) Tradier is the source of truth for:
   - Option chains
   - Quotes
   - Execution-critical pricing
   Other providers (Yahoo, Finnhub, FRED) are for:
   - Underlying analysis
   - Macro indicators
   - Fallback data only

4) Simplicity over complexity.
   - Remove unused, duplicate, or obsolete code.
   - Prefer one clear data path over multiple legacy paths.
   - Avoid adding new frameworks or major architectural changes.

5) Stability during cleanup.
   - Do not break existing working features.
   - Make changes in small, testable steps.
   - All changes must end with tests passing.

6) Frontend philosophy.
   - One consistent trade card design.
   - Metrics must match backend contract exactly.
   - “N/A” is better than incorrect numbers.

7) Strategy philosophy.
   - Focus on risk-defined strategies:
     - credit spreads
     - debit spreads
     - iron condors
     - butterflies
     - income (CSP/covered call)
   - Avoid unrealistic trades with:
     - extreme spreads
     - illiquid contracts
     - mathematically inconsistent metrics.

When cleaning or refactoring:
- Prefer removing unused code over rewriting.
- Prefer consolidation over abstraction.
- Always preserve the core EV + probability logic.

At the end of each task:
- Output a summary of changes.
- List any risks or assumptions.
- Confirm tests pass.
- Update Readme.md and Architecture.md if necessary.