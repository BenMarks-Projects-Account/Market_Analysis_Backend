# BenTrade Frontend Refactor Plan

> Branch: `chore/app-cleanup-phase0`
> Date: 2025-02-17
> Status: **Planning** — no code changes yet

---

## 1. Current Architecture Snapshot

| Layer | Tech | Notes |
|-------|------|-------|
| Rendering | Vanilla JS, template literals → `innerHTML` | No framework, no virtual DOM |
| Modules | IIFE → `window.*` globals | No ES modules, no bundler |
| Routing | Hash-based (`#/route`) via `router.js` | 19 routes, `loadView()` fetches HTML then calls init fn |
| State | localStorage + in-memory stores | `homeCache`, `sessionStats`, `session_state` |
| API | `BenTradeApi` IIFE wrapping `fetch()` | 30+ methods, auto source-health refresh |
| CSS | Single `app.css` | 3-column grid shell (sidebar · main · diagnostic) |

---

## 2. Route / Page Inventory

### 2.1 Route Map (19 routes)

| Route | View HTML | Init Function | Lines | Has Trade Cards |
|-------|-----------|---------------|------:|:---:|
| `home` | `home.html` | `initHome` | 2,097 | Yes |
| `credit-spread` | `credit-spread.view.html` | `initCreditSpreads` | 651 (shell) | **No** ★ |
| `strategy-iron-condor` | `credit-spread.view.html` | `initStrategyIronCondor` | — | **No** ★ |
| `debit-spreads` | `credit-spread.view.html` | `initDebitSpreads` | — | **No** ★ |
| `butterflies` | `credit-spread.view.html` | `initButterflies` | — | **No** ★ |
| `calendar` | `credit-spread.view.html` | `initCalendar` | — | **No** ★ |
| `income` | `credit-spread.view.html` | `initIncome` | — | **No** ★ |
| `active-trade` | `active_trades.html` | `initActiveTrades` | 295 | Yes |
| `trade-testing` | `trade_workbench.html` | `initTradeWorkbench` | 836 | Yes |
| `stock-analysis` | `stock_analysis.html` | `initStockAnalysis` | 696 | — |
| `stock-scanner` | `stock_scanner.html` | `initStockScanner` | 545 | Yes |
| `risk-capital` | `risk_capital.html` | `initRiskCapital` | 334 | — |
| `portfolio-risk` | `portfolio_risk.html` | `initPortfolioRisk` | 169 | — |
| `trade-lifecycle` | `trade_lifecycle.html` | `initTradeLifecycle` | 98 | — |
| `strategy-analytics` | `strategy_analytics.html` | `initStrategyAnalytics` | 186 | — |
| `admin-data-health` | `data_health.html` | `initDataHealth` | 185 | — |
| `admin/data-workbench` | `admin_data_workbench.html` | `initAdminDataWorkbench` | 353 | Yes |

> **★ Strategy dashboards are incomplete.** `strategy_dashboard_shell.js` provides fetch interception and filter forms, but no controller loads reports or renders trade cards. The `#content`, `#reportSelect`, `#fileSelect` elements in `credit-spread.view.html` are dead DOM — nothing wires them up.

### 2.2 Script Load Order (index.html)

```
session_state.js → client.js → tradeKey.js → glossary.js → tooltip.js →
trade_card.js → source_health.js → home_loading_overlay.js → rateLimiter.js →
notes.js → sessionStats.js → homeCache.js → defaults.js →
strategy_dashboard_shell.js → active_trades.js → trade_workbench.js →
stock_analysis.js → stock_scanner.js → home.js → risk_capital.js →
portfolio_risk.js → trade_lifecycle.js → strategy_analytics.js →
data_health.js → admin_data_workbench.js → app.js → router.js
```

### 2.3 Shared / UI Files Already Extracted

| File | Global | Purpose |
|------|--------|---------|
| `ui/trade_card.js` | `BenTradeTradeCard` | Key utilities only (`resolveTradeKey`, `buildTradeKey`, `openDataWorkbenchByTrade`) — **not a renderer** |
| `ui/tooltip.js` | `BenTradeUI.Tooltip` | Metric tooltip with glossary lookup; `MutationObserver` auto-binding |
| `ui/source_health.js` | `BenTradeSourceHealth` / `BenTradeSourceHealthStore` | Provider health dots with TTL cache (45 s) |
| `ui/notes.js` | `BenTradeNotes` | Trade/idea notes in localStorage + lifecycle events |
| `ui/home_loading_overlay.js` | `BenTradeHomeLoadingOverlay` | Loading modal with log, cancel, retry |
| `metrics/glossary.js` | `BenTradeMetrics.glossary` | 30+ metric definitions (label, short, formula, why, notes) |
| `strategies/defaults.js` | `BenTradeStrategyDefaults` | Per-strategy default filter values + "why" explanations |
| `utils/tradeKey.js` | `BenTradeUtils.tradeKey` | Canonical trade key generation with 25+ strategy aliases |
| `utils/rateLimiter.js` | `BenTradeRateLimiter` | Provider-keyed rate limiter with exponential backoff |
| `stores/homeCache.js` | `BenTradeHomeCacheStore` | Home dashboard data store (60 s fresh / 15 min stale) |
| `stores/sessionStats.js` | `BenTradeSessionStatsStore` | Cumulative session run stats, cross-tab sync |
| `state/session_state.js` | `BenTradeSession` | Selected report / underlying / current trades |

---

## 3. Problems & What to Extract

### 3.1 Five Inline Trade-Card Renderers → One Shared Component

Each page builds its own card HTML with slightly different metrics, CSS classes, and field-name fallback chains.

| Page | Function | CSS Class | Key Metrics |
|------|----------|-----------|-------------|
| `home.js` L815-900 | `renderOpportunities()` | `trade-card home-op-card` | Rank, EV, POP, RoR, Model |
| `active_trades.js` L150-220 | `renderCards()` | `trade-card active-trade-card` | Avg Open, Mark, Unrealized P&L, P&L % |
| `stock_scanner.js` L250-320 | inline in `render()` | `trade-card` | Price, Composite, Trend, Momentum, Vol, RSI, RV, IV/RV |
| `trade_workbench.js` L385-450 | `renderTradeCard()` | `trade-card` | Net Credit, RoR, POP, EV |
| `admin_data_workbench.js` L120-187 | `renderTradeCard()` | `trade-card` | Max Profit, Max Loss, POP, RoR, EV, Composite |

**Plan → `ui/trade_card.js` becomes a real renderer:**

```
BenTradeTradeCard.render(trade, {
  variant: 'opportunity' | 'active' | 'scanner' | 'workbench' | 'inspector',
  metrics: ['ev', 'pop', 'ror', ...],   // pick list — each variant has a default set
  actions: ['execute', 'notes', 'inspect', ...],
  cssExtra: 'home-op-card',
})
```

Each variant maps to a default `metrics` list so existing behaviour is preserved without per-page card HTML. The renderer reads every field through the metrics access layer (§ 3.2) and falls back to `null` → `'N/A'` display.

### 3.2 Metrics Access Layer (Computed-first, Null-safe)

**Problem.** Trade objects arrive with inconsistent field names. Every page has its own fallback chain:

```js
// app.js — execution modal
symbol:      row.underlying || row.underlying_symbol || row.symbol
expiration:  row.expiration || row.expiration_date
shortStrike: row.short_strike || row.put_short_strike || row.call_short_strike
maxLoss:     row.max_loss_per_share || row.max_loss || row.estimated_risk || row.risk_amount
maxProfit:   row.max_profit_per_share || row.max_profit || row.estimated_max_profit
credit:      row.net_credit || row.net_debit || row.credit || row.debit || row.premium_received || row.premium_paid

// home.js — opportunity cards
ev:  comp?.expected_value ?? row?.ev_per_contract ?? row?.expected_value ??
     row?.ev_per_share ?? row?.ev ?? row?.edge
pop: comp?.pop ?? row?.p_win_used ?? row?.pop_delta_approx ??
     row?.probability_of_profit ?? row?.pop
ror: comp?.return_on_risk ?? row?.return_on_risk ?? row?.ror
```

**Plan → `utils/tradeAccessor.js` (`BenTradeUtils.tradeAccessor`):**

```js
// Single source of truth for field resolution.
// If backend computed_metrics exists, prefer it; else walk raw fallbacks.

const FIELD_MAP = {
  symbol:       { computed: null,             fallbacks: ['underlying', 'underlying_symbol', 'symbol'] },
  expiration:   { computed: null,             fallbacks: ['expiration', 'expiration_date'] },
  ev:           { computed: 'expected_value', fallbacks: ['ev_per_contract', 'expected_value', 'ev_per_share', 'ev', 'edge'] },
  pop:          { computed: 'pop',            fallbacks: ['p_win_used', 'pop_delta_approx', 'probability_of_profit', 'pop'] },
  ror:          { computed: 'return_on_risk', fallbacks: ['return_on_risk', 'ror'] },
  max_loss:     { computed: 'max_loss',       fallbacks: ['max_loss_per_share', 'max_loss_per_contract', 'max_loss', 'estimated_risk', 'risk_amount'] },
  max_profit:   { computed: 'max_profit',     fallbacks: ['max_profit_per_share', 'max_profit_per_contract', 'max_profit', 'estimated_max_profit'] },
  net_credit:   { computed: 'net_credit',     fallbacks: ['net_credit', 'net_debit', 'credit', 'debit', 'premium_received', 'premium_paid'] },
  short_strike: { computed: null,             fallbacks: ['short_strike', 'put_short_strike', 'call_short_strike'] },
  long_strike:  { computed: null,             fallbacks: ['long_strike', 'put_long_strike', 'call_long_strike'] },
  strategy:     { computed: null,             fallbacks: ['spread_type', 'strategy'] },
};

// Usage:
//   const get = BenTradeUtils.tradeAccessor(trade);
//   get('ev')   → Number | null   (never undefined, never NaN)
//   get('pop')  → Number | null
```

All 5 card renderers + `app.js` modal + `homeCache.normalizeTradeIdea()` replaced with calls to this accessor.

### 3.3 Duplicated Formatting Helpers → `utils/format.js`

| Helper | Duplicate Locations |
|--------|---------------------|
| `toNumber(v)` | `home.js`, `homeCache.js`, `sessionStats.js`, `app.js`, `active_trades.js`, `admin_data_workbench.js`, `trade_workbench.js` |
| `fmtMoney(v)` | `app.js`, `active_trades.js`, `risk_capital.js` |
| `fmtPct(v, digits?)` | `home.js`, `active_trades.js`, `risk_capital.js`, `stock_analysis.js`, `stock_scanner.js`, `trade_workbench.js` |
| `fmt(v, decimals?)` | `home.js`, `portfolio_risk.js`, `strategy_analytics.js`, `stock_analysis.js`, `trade_workbench.js` |
| `fmtNum(v, digits?)` | `admin_data_workbench.js`, `stock_scanner.js` |
| `fmtSigned(v)` | `home.js` |
| `escapeHtml(v)` | `source_health.js`, `notes.js`, `data_health.js` |

**Plan → `utils/format.js` (`BenTradeUtils.format`):**

```js
window.BenTradeUtils = window.BenTradeUtils || {};
BenTradeUtils.format = {
  toNumber(v)           { /* canonical */ },
  money(v)              { /* $1,234.56 */ },
  pct(v, digits=2)      { /* 12.34% */ },
  num(v, digits=2)      { /* 1,234.56 */ },
  signed(v, digits=2)   { /* +1.23 / -0.45 */ },
  escapeHtml(v)         { /* &amp; &lt; etc. */ },
};
```

Each page's local helper deleted and replaced with `const { money, pct, num } = BenTradeUtils.format;` at the top of its IIFE.

### 3.4 Strategy Dashboard Controller — Missing Piece

`strategy_dashboard_shell.js` (651 lines) handles:
- Fetch interception (rewrites `/api/reports` → `/api/strategies/{id}/reports`)
- Filter form building (`buildForm()`)
- EventSource patching for SSE generate progress

It does **not** handle:
1. Loading report list → populating `#reportSelect`
2. Loading report data → parsing trade array
3. Rendering trade cards into `#content`
4. Wiring `#fileSelect` for file-based analysis
5. Handling generate button → SSE stream → progress overlay → reload

**Plan → Build `strategy_dashboard_controller.js`:**

This controller completes the strategy dashboards. It reuses the new shared `TradeCard.render()` and `tradeAccessor` from § 3.1–3.2.

```
mount(strategyId)
  → fetchReports(strategyId) → populate #reportSelect
  → on reportSelect change → fetchReportData(reportId) → parse trades
  → renderTradeList(trades) using TradeCard.render(trade, { variant: 'strategy' })
  → wire #genOverlay + generate button → SSE stream → progress → reload
```

This is the **only net-new feature work** in the refactor. Everything else is extract-and-delete.

---

## 4. Contract Assumptions

### 4.1 Trade Object — Required vs Optional Fields

The unified `tradeAccessor` (§ 3.2) defines what the frontend expects. Backend `computed_metrics` is the preferred source; raw fields are fallback only.

| Field | Required | Type | Canonical Key | Raw Fallbacks |
|-------|:--------:|------|--------------|---------------|
| `symbol` | **Yes** | `string` | `underlying` | `underlying_symbol`, `symbol` |
| `strategy` | **Yes** | `string` | `spread_type` | `strategy` |
| `expiration` | **Yes** | `string` (YYYY-MM-DD) | `expiration` | `expiration_date` |
| `short_strike` | Conditionally | `number` | `short_strike` | `put_short_strike`, `call_short_strike` |
| `long_strike` | Conditionally | `number` | `long_strike` | `put_long_strike`, `call_long_strike` |
| `dte` | Optional | `number` | `dte` | — |
| `ev` | Optional | `number` | `computed_metrics.expected_value` | `ev_per_contract`, `expected_value`, `ev_per_share`, `ev`, `edge` |
| `pop` | Optional | `number` | `computed_metrics.pop` | `p_win_used`, `pop_delta_approx`, `probability_of_profit`, `pop` |
| `ror` | Optional | `number` | `computed_metrics.return_on_risk` | `return_on_risk`, `ror` |
| `max_loss` | Optional | `number` | `computed_metrics.max_loss` | `max_loss_per_share`, `max_loss_per_contract`, `max_loss`, `estimated_risk`, `risk_amount` |
| `max_profit` | Optional | `number` | `computed_metrics.max_profit` | `max_profit_per_share`, `max_profit_per_contract`, `max_profit`, `estimated_max_profit` |
| `net_credit` | Optional | `number` | `computed_metrics.net_credit` | `net_credit`, `net_debit`, `credit`, `debit`, `premium_received`, `premium_paid` |
| `composite_score` | Optional | `number` | `computed_metrics.composite_score` | `trade_quality_score`, `scanner_score`, `score` |

**Rule:** If a metric resolves to `undefined`, `NaN`, or a non-finite number, the accessor returns `null`. UI renders `null` as `'N/A'`.

### 4.2 Active Trade Object (Broker-sourced)

Active trades come from Tradier positions, not analysis reports. They have their own contract:

| Field | Type | Notes |
|-------|------|-------|
| `symbol` | `string` | OCC symbol |
| `avg_open_price` | `number` | Fill price |
| `mark_price` | `number` | Current mark |
| `unrealized_pnl` | `number` | Dollar P&L |
| `unrealized_pnl_pct` | `number` | Percentage P&L |
| `legs[]` | `array` | Individual option legs with `qty`, `price`, `option_symbol` |

These do NOT use `computed_metrics` and should stay on a separate `active` variant in the card renderer.

---

## 5. Performance Hotspots & Fixes

### 5.1 Source Health Auto-Refresh Storm

**Location:** `api/client.js` line 27
**Problem:** Every successful API call (except health endpoints) triggers `BenTradeSourceHealthStore.fetchSourceHealth({ force: true })`. On a home load that makes 25+ API calls, this fires 25+ forced health refreshes.
**Fix:** Remove the auto-trigger from `jsonFetch()`. Instead, refresh source health:
- Once on app start
- Once after each page navigation (in `router.js loadView()`)
- On a 60 s interval timer

**Estimated savings:** ~24 redundant health requests per home load.

### 5.2 Tooltip MutationObserver Overhead

**Location:** `ui/tooltip.js`
**Problem:** `MutationObserver` watches `document.body` for `childList + subtree`. Every DOM change (including innerHTML card rendering) triggers a full re-scan of ALL `[data-metric]`, `.metric-label`, `.statLabel`, `.detail-label`, `th` elements to rebind hover listeners.
**Fix:**
1. Debounce the observer callback (100 ms).
2. Scope observation to `#view` instead of `document.body` — the diagnostic sidebar and nav never change metrics.
3. Use event delegation on `#view` instead of per-element listeners to eliminate rebinding entirely.

### 5.3 homeCache `refreshCore()` — 25+ Sequential API Calls

**Location:** `stores/homeCache.js`
**Problem:** `refreshCore()` fires ~25 API calls in staggered groups. Each call is small, but the waterfall adds latency.
**Fix:**
1. **Batch endpoint** (backend): Add `GET /api/dashboard/home` that returns regime + playbook + SPY + VIX + top picks + portfolio risk in one response. This eliminates ~10 individual calls.
2. **Parallel all remaining calls** using `Promise.allSettled()` — currently some are sequential by accident.
3. **Stale-while-revalidate**: Show cached data immediately, refresh in background, re-render only if data changed (diff check before `innerHTML`).

### 5.4 Full innerHTML Re-render on Every Data Load

**Location:** Every page controller.
**Problem:** Pages set `container.innerHTML = ...` with the full HTML string on every data load. This destroys and recreates the entire DOM subtree, losing scroll position, focus, and hover state.
**Fix (incremental, low-risk):**
1. **Short term:** Before setting innerHTML, compare the new HTML string to `container.innerHTML`. If identical, skip. This is cheap and prevents redundant paints.
2. **Medium term:** For list views (cards), render into a `DocumentFragment`, then `replaceChildren()`. This is a single reflow instead of innerHTML parse → destroy → rebuild.
3. **Long term (if justified):** Keyed list diffing for card lists — only insert/remove/reorder changed cards.

### 5.5 No Request Deduplication in API Client

**Location:** `api/client.js`
**Problem:** Two components calling the same endpoint simultaneously (e.g., source health from sidebar + page) create duplicate in-flight requests.
**Fix:** Add an inflight map to `jsonFetch()`:

```js
const _inflight = new Map();

async function jsonFetch(url, opts) {
  const key = `${opts?.method || 'GET'}:${url}`;
  if (_inflight.has(key)) return _inflight.get(key);
  const promise = fetch(url, opts).then(r => r.json()).finally(() => _inflight.delete(key));
  _inflight.set(key, promise);
  return promise;
}
```

### 5.6 Formatting Helpers Called Fresh Every Render

**Location:** All pages.
**Problem:** `toNumber()`, `fmtPct()`, etc. are pure functions called thousands of times per render with identical inputs (same trade data).
**Impact:** Low per-call cost but adds up during bulk card rendering.
**Fix:** Not worth a full memoization layer. The real win is reducing render frequency (§ 5.4). Once we stop re-rendering unchanged data, the formatting cost becomes negligible.

---

## 6. home.js Decomposition

`home.js` is 2,097 lines — the largest file by far. It should be split into focused modules:

| Extract To | Responsibility | Approx Lines |
|-----------|----------------|-------------:|
| `pages/home_regime.js` | Regime tile + playbook table rendering | ~150 |
| `pages/home_market.js` | SPY chart, VIX chart, index tiles, sector tiles | ~400 |
| `pages/home_opportunities.js` | Opportunity card list (uses shared `TradeCard.render`) | ~200 |
| `pages/home_strategies.js` | Strategy idea rows + run-stats integration | ~300 |
| `pages/home_signals.js` | Scan queue + signal universe rendering | ~200 |
| `pages/home_risk.js` | Risk tiles + macro section | ~150 |
| `pages/home.js` (orchestrator) | `initHome()` → delegates to sub-modules, manages tab state | ~200 |

Each sub-module is an IIFE attached to `window.BenTradeHome.*` and called by the orchestrator.

---

## 7. Dead Code & Unused Paths

| Item | Location | Action |
|------|----------|--------|
| `#fileSelect` in `credit-spread.view.html` | Template | Remove until controller implements file-based loading |
| `#tradeCountsBar` in `credit-spread.view.html` | Template | Remove until controller implements count display |
| `window.BenTradeSession.currentTrades` | `session_state.js` | Verify usage — may be write-only |
| `LABEL_FALLBACK_MAP` in `tooltip.js` | ~40 entries | Audit — some may be orphaned after field normalization |
| Inline `toNumber` / `fmt*` in every page | 7+ files | Delete after `utils/format.js` extraction |

---

## 8. Execution Order

All changes stay within the IIFE + globals pattern. No new frameworks.

| Phase | Work | Files Touched | Risk |
|-------|------|--------------|------|
| **P1** | Extract `utils/format.js` — move all formatting helpers | Create 1 file, edit 10+ pages | Low — pure functions, easy to verify |
| **P2** | Extract `utils/tradeAccessor.js` — canonical field resolution | Create 1 file, edit 5 card renderers + `app.js` + `homeCache.js` | Medium — must verify every fallback chain preserved |
| **P3** | Unify `ui/trade_card.js` into a real renderer with variants | Rewrite 1 file, edit 5 pages (delete inline card HTML) | Medium — visual regression possible, manual QA per page |
| **P4** | Performance fixes (§ 5.1–5.5) | `client.js`, `tooltip.js`, `homeCache.js`, `router.js` | Low-Medium — each fix is independent |
| **P5** | Build `strategy_dashboard_controller.js` | Create 1 file, edit `strategy_dashboard_shell.js` | Higher — net-new feature, needs API integration testing |
| **P6** | Decompose `home.js` | Create 6 files, rewrite 1 | Medium — lots of moves, must preserve init sequence |
| **P7** | Dead code removal + final audit | Various | Low |

Each phase is a single PR, independently shippable, tested by manual page walk-through before merge.

---

## 9. Files Created / Modified Summary

### New Files (7)
```
assets/js/utils/format.js                  — shared formatting helpers
assets/js/utils/tradeAccessor.js            — canonical field resolution
assets/js/pages/strategy_dashboard_controller.js — report loading + card rendering for strategy pages
assets/js/pages/home_regime.js              — regime submodule
assets/js/pages/home_market.js              — market data submodule
assets/js/pages/home_opportunities.js       — opportunity cards submodule
assets/js/pages/home_strategies.js          — strategy rows submodule
```

### Major Edits
```
assets/js/ui/trade_card.js                  — becomes real renderer
assets/js/api/client.js                     — remove auto source-health, add inflight dedup
assets/js/ui/tooltip.js                     — debounce + scope observer + event delegation
assets/js/stores/homeCache.js               — use tradeAccessor, parallel fetch
assets/js/pages/home.js                     — slim to orchestrator
assets/js/pages/strategy_dashboard_shell.js — wire up controller
assets/js/app.js                            — use tradeAccessor in execution modal
index.html                                  — add new script tags
```

### Deletions (per-page inline code)
```
~7 toNumber()    copies
~3 fmtMoney()    copies
~6 fmtPct()      copies
~5 fmt()         copies
~2 fmtNum()      copies
~3 escapeHtml()  copies
~5 inline card HTML renderers (~400 lines total)
```
