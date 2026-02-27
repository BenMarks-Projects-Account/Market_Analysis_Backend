# UI TradeCard Spec

> **Status:** Authoritative standard — all trade display must conform.

---

## 1. TradeCard Is the Canonical Component

- `TradeCard` is the **single UI primitive** for displaying any trade across the app.
- Scanner dashboards, the Opportunity Engine, and detail views all use TradeCard (or its building blocks).
- Do not create parallel card components for trade display.

---

## 2. Action Footer Buttons

- The card action footer (Accept, Reject, Analyze, etc.) MUST remain **visible in both collapsed and expanded states**.
- Buttons must not be hidden behind scroll, overflow, or accordion collapse.

---

## 3. Tooltip Provider Rules

- All tooltips MUST use the **app-standard `TooltipProvider`** pattern.
- Do not introduce one-off tooltip systems, inline title attributes for rich content, or custom hover popups.
- Tooltip content should be concise (1–2 sentences max).

---

## 4. Data Workbench Button

- Every scanner result view MUST include a **Data Workbench** entry point (button or link).
- Clicking it opens a diagnostic modal/route that shows raw data, filter trace, and data-quality flags for the current scan.
- This is required for debugging and explainability; it is not optional.

---

## 5. Loading State Consistency

- When a scanner is running, the UI MUST show:
  - A **spinner** (standard app spinner component).
  - A **log area** displaying scanner progress (e.g., "Scanning credit spreads… 3/7 symbols done").
- Style and placement must be consistent across all scanner views.
- Do not use different loading patterns for different scanners.

---

## 6. Overlay Mount Point (Fullscreen-Safe)

> Added 2026-02-27 — resolves tooltips/modals invisible in browser fullscreen.

### Root cause

The app enters fullscreen via `.shell.requestFullscreen()`. In fullscreen mode, only
DOM children of the fullscreen element (`.shell`) are rendered. Any element appended
to `document.body` is **outside** the fullscreen subtree and becomes invisible.

### Rule

All overlay components — tooltips, modals, popovers, toasts — **MUST** mount to the
overlay root provided by `BenTradeOverlayRoot.get()` instead of `document.body`.

```js
// ✅ Correct — always visible, including fullscreen
const root = window.BenTradeOverlayRoot.get();
root.appendChild(myOverlayEl);

// ❌ Wrong — invisible when .shell is fullscreen
document.body.appendChild(myOverlayEl);
```

### How it works

| Layer | Detail |
|---|---|
| DOM anchor | `<div id="overlay-root">` lives inside `.shell` in `index.html` |
| Utility | `assets/js/utils/overlayRoot.js` — `BenTradeOverlayRoot.get()` |
| Fallback chain | `#overlay-root` → `document.fullscreenElement` → `document.body` |
| Auto-rehome | A `fullscreenchange` listener re-parents known stray overlays into `#overlay-root` on fullscreen entry |
| CSS | `#overlay-root` is zero-dimension, `overflow:visible`, `pointer-events:none`; children restore `pointer-events:auto` |

### z-index scale (global overlays)

| Layer | z-index |
|---|---|
| Tooltips (`.metric-tooltip`) | 4000 |
| Modals (`#modal`, `.active-modal`) | 2800–3000 |
| Data Workbench modal (`.dwb-modal-overlay`) | 5000 |
| Toasts (`#strategyDefaultsToast`) | 9999 |

### Debug

Set `window.__BEN_DEBUG_OVERLAYS = true` in the console to log:
- Fullscreen enter/exit transitions
- Overlay re-parenting events
- Tooltip/modal open events with parent + fullscreen state

### Components currently using this pattern

- `tooltip.js` — metric tooltip
- `app.js` — execution modal
- `data_workbench_modal.js` — Data Workbench modal
- `strategy_dashboard_shell.js` — defaults-applied toast

### Components that already mount in-shell (no change needed)

- `boot_choice_modal.js` — appends to `scope` (host element)
- `home_loading_overlay.js` — appends to `scope`
- `active_trades.js` — modal is inline HTML inside `#view`

---

## Cross-References

- Canonical trade shape: [docs/standards/canonical-contract.md](canonical-contract.md)
- Scanner contract (output format): [docs/standards/scanner-contract.md](scanner-contract.md)
- Overlay root utility: `BenTrade/frontend/assets/js/utils/overlayRoot.js`
