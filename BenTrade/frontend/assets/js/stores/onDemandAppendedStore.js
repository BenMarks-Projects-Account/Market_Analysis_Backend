/**
 * On-Demand Evaluator appended-analyses store (Phase 1).
 *
 * Browser-side ephemeral store (no persistence) that holds user-pasted
 * deep-research narratives alongside the current CE analysis. The PDF
 * export collects these via list() and sends them to the backend so the
 * PDF can include them without the backend having to know about their
 * existence otherwise.
 *
 * Phase 1 behavior:
 *   - reset(symbol, jobId)       wipes entries and re-binds to new CE run
 *   - append(title, bodyMd)      appends a new entry (timestamp now)
 *   - list()                     returns a shallow copy of entries
 *   - getSymbol() / getJobId()   current bindings
 *   - clear()                    wipes entries only (keeps bindings)
 *
 * Phase 1 note: the Evaluator UI currently has a single-slot paste
 * (_narrativeRawMarkdown). On each paste we clear() then append(). This
 * keeps the payload list-shaped for when multi-entry UI lands.
 */
(function () {
  'use strict';

  let _symbol = null;
  let _jobId = null;
  const _entries = [];

  function reset(symbol, jobId) {
    _symbol = symbol ? String(symbol).toUpperCase() : null;
    _jobId = jobId || null;
    _entries.length = 0;
  }

  function append(title, bodyMd) {
    const t = (title == null ? '' : String(title)).trim();
    const b = (bodyMd == null ? '' : String(bodyMd)).trim();
    if (!b) return null;
    const entry = {
      timestamp: new Date().toISOString(),
      title: t || 'Untitled analysis',
      body_md: b,
    };
    _entries.push(entry);
    return entry;
  }

  function list() {
    return _entries.slice();
  }

  function getSymbol() {
    return _symbol;
  }

  function getJobId() {
    return _jobId;
  }

  function clear() {
    _entries.length = 0;
  }

  window.BenTradeOnDemandAppendedStore = {
    reset: reset,
    append: append,
    list: list,
    getSymbol: getSymbol,
    getJobId: getJobId,
    clear: clear,
  };
})();
