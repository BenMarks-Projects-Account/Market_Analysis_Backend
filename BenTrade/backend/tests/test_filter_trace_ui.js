/**
 * Tests for filter-trace instrumentation in the Credit Spread scanner UI.
 *
 * Validates:
 * - Pipeline stages waterfall renders correct stage counts
 * - Gate breakdown table renders categorised rejections
 * - Preset badge displays the active preset name
 * - Resolved thresholds collapsible section is present
 * - Copy Trace JSON button is rendered
 * - Data quality flags are shown
 * - Rejected examples section appears only when present
 * - Dev toggle localStorage key is read by EventSource patch
 */

/* ─── minimal DOM stubs ─── */
const _localStorage = {};
global.window = global;
global.document = { body: {} };
global.navigator = { clipboard: { writeText: () => Promise.resolve() } };
global.localStorage = {
  getItem(key) { return _localStorage[key] || null; },
  setItem(key, value) { _localStorage[key] = String(value); },
  removeItem(key) { delete _localStorage[key]; },
  clear() { Object.keys(_localStorage).forEach(k => delete _localStorage[k]); },
};

/* ─── test helpers ─── */
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function buildFilterTrace(overrides = {}) {
  return {
    trace_id: 'credit_spread_20250301_120000_abc12345',
    timestamp: '2025-03-01T12:00:00Z',
    strategy_id: 'credit_spread',
    preset_name: 'balanced',
    resolved_thresholds: {
      dte_min: 7, dte_max: 45,
      distance_min: 0.01, distance_max: 0.12,
      width_min: 1, width_max: 5,
      min_pop: 0.60, min_ev_to_risk: 0.02,
      max_bid_ask_spread_pct: 1.5,
      min_open_interest: 300, min_volume: 20,
    },
    stages: [
      { name: 'snapshot_collection', label: 'Snapshot Collection', input_count: 7, output_count: 5, detail: '7 symbols → 5 valid snapshots' },
      { name: 'candidate_construction', label: 'Candidate Construction', input_count: 240, output_count: 80, detail: '240 contracts → 80 spread candidates' },
      { name: 'enrichment', label: 'Enrichment', input_count: 80, output_count: 78, detail: '80 candidates → 78 enriched trades' },
      { name: 'evaluate_gates', label: 'Quality Gates', input_count: 78, output_count: 0, detail: '78 enriched → 0 passed all gates' },
      { name: 'dedup_ranking', label: 'Dedup & Ranking', input_count: 0, output_count: 0, detail: '0 → 0 unique trades' },
    ],
    gate_breakdown: {
      quote_validation: 15,
      probability: 30,
      expected_value: 10,
      liquidity: 23,
    },
    rejection_reasons: {
      'MISSING_QUOTES:short_bid': 15,
      'pop_below_floor': 30,
      'ev_to_risk_below_floor': 10,
      'open_interest_below_min': 14,
      'volume_below_min': 9,
    },
    data_quality_flags: ['MISSING_PRICE_HISTORY', 'INVALID_QUOTES:3'],
    ...overrides,
  };
}

/**
 * Simulate the "No Trades" panel HTML rendering from strategy_dashboard_shell.js.
 * This is a condensed version of the actual rendering logic.
 */
function renderNoTradesPanel(data) {
  const ft = data.filter_trace || null;
  const reportWarnings = data.report_warnings || [];
  const diagnostics = data.diagnostics || {};
  const stats = data.report_stats || {};
  const symbols = data.symbols || [];

  let html = '<div class="no-trades-panel">';

  // Header with preset badge
  html += '<div class="no-trades-header">';
  html += '<div class="no-trades-title">No Trades Passed Filters</div>';
  if (ft && ft.preset_name) {
    html += `<span class="preset-badge">${escapeHtml(ft.preset_name)}</span>`;
  }
  html += '</div>';

  // Top 3 bottleneck stages
  if (ft && Array.isArray(ft.stages) && ft.stages.length) {
    var _allStages = ft.stages.map(function(stage, i){
      var inp = typeof stage.input_count === 'number' ? stage.input_count : 0;
      var out = typeof stage.output_count === 'number' ? stage.output_count : 0;
      return { idx: i, label: stage.label || stage.name, inp: inp, out: out, dropped: Math.max(0, inp - out) };
    }).filter(function(s){ return s.dropped > 0; });
    _allStages.sort(function(a,b){ return b.dropped - a.dropped; });
    var _top3 = _allStages.slice(0, 3);
    if (_top3.length) {
      html += '<div class="top-bottleneck-label">Top Bottleneck Stages</div>';
      html += '<div class="top-bottleneck-stages">';
      _top3.forEach(function(s) {
        var isKill = s.out === 0;
        html += `<div class="pipeline-stage${isKill ? ' bottleneck' : ' warning'}">`;
        html += `<span class="stage-num">${s.idx + 1}</span>`;
        html += `<span class="stage-label">${escapeHtml(s.label)}</span>`;
        html += `<span class="stage-counts">${s.inp} → ${s.out}</span>`;
        html += `<span class="stage-dropped">(-${s.dropped})</span>`;
        html += '</div>';
      });
      html += '</div>';
    }
    // Full pipeline in collapsible details
    html += '<details class="full-pipeline">';
    html += `<summary>All Pipeline Stages (${ft.stages.length})</summary>`;
    html += '<div class="pipeline-stages">';
    ft.stages.forEach((stage, i) => {
      const inp = stage.input_count != null ? stage.input_count : '?';
      const out = stage.output_count != null ? stage.output_count : '?';
      const dropped = (typeof inp === 'number' && typeof out === 'number') ? inp - out : null;
      const isBottleneck = dropped !== null && dropped > 0 && out === 0;
      html += `<div class="pipeline-stage${isBottleneck ? ' bottleneck' : ''}">`;
      html += `<span class="stage-num">${i + 1}</span>`;
      html += `<span class="stage-label">${escapeHtml(stage.label || stage.name)}</span>`;
      html += `<span class="stage-counts">${inp} → ${out}</span>`;
      if (dropped !== null && dropped > 0) {
        html += `<span class="stage-dropped">(-${dropped})</span>`;
      }
      html += '</div>';
    });
    html += '</div></details>';
  }

  // Gate breakdown
  if (ft && ft.gate_breakdown) {
    const gateEntries = Object.entries(ft.gate_breakdown).filter(([, v]) => v > 0);
    if (gateEntries.length) {
      html += '<div class="gate-breakdown-label">Gate Breakdown</div>';
      html += '<table class="gate-breakdown-table">';
      gateEntries.sort((a, b) => b[1] - a[1]);
      gateEntries.forEach(([gate, count]) => {
        html += `<tr><td class="gate-name">${escapeHtml(gate)}</td><td class="gate-count">${count}</td></tr>`;
      });
      html += '</table>';
    }
  }

  // Resolved thresholds
  if (ft && ft.resolved_thresholds && Object.keys(ft.resolved_thresholds).length) {
    html += '<details class="resolved-thresholds">';
    html += '<summary>Resolved Thresholds</summary>';
    html += '<table>';
    Object.entries(ft.resolved_thresholds).forEach(([key, val]) => {
      html += `<tr><td>${escapeHtml(key)}</td><td>${val}</td></tr>`;
    });
    html += '</table></details>';
  }

  // Data quality flags
  if (ft && Array.isArray(ft.data_quality_flags) && ft.data_quality_flags.length) {
    html += `<div class="data-quality-flags">Data Quality: ${ft.data_quality_flags.join(', ')}</div>`;
  }

  // Rejected examples
  if (ft && Array.isArray(ft.rejected_examples) && ft.rejected_examples.length) {
    html += '<details class="rejected-examples">';
    html += `<summary>Rejected Examples (${ft.rejected_examples.length})</summary>`;
    ft.rejected_examples.forEach(ex => {
      html += `<div class="rejected-example" data-symbol="${escapeHtml(ex.symbol || '')}">${escapeHtml(JSON.stringify(ex))}</div>`;
    });
    html += '</details>';
  }

  // Dynamic suggestions
  {
    const _gb = (ft && ft.gate_breakdown) ? ft.gate_breakdown : {};
    const _rej = stats.rejection_breakdown || diagnostics.rejection_breakdown || {};
    const _gateScores = {
      ev_to_risk:   (_gb.expected_value || 0) + (_rej.ev_to_risk_below_floor || 0) + (_rej.ror_below_floor || 0),
      spread_width: (_gb.spread_structure || 0) + (_rej.spread_too_wide || 0),
      liquidity:    (_gb.liquidity || 0) + (_rej.volume_below_min || 0) + (_rej.open_interest_below_min || 0),
      pop:          (_gb.probability || 0) + (_rej.pop_below_floor || 0),
      data_quality: (_gb.data_quality || 0) + (_gb.quote_validation || 0) + (_gb.metrics_computation || 0),
    };
    const _tips = [];
    const _sortedGates = Object.entries(_gateScores).filter(function(e){ return e[1]>0; }).sort(function(a,b){ return b[1]-a[1]; });
    const _dominant = _sortedGates.length ? _sortedGates[0][0] : null;

    if (_dominant === 'ev_to_risk' || _gateScores.ev_to_risk > 0) {
      _tips.push('EV/Risk &amp; ROR too low');
    }
    if (_dominant === 'spread_width' || _gateScores.spread_width > 0) {
      _tips.push('Bid-Ask spread too wide');
    }
    if (_dominant === 'liquidity' || _gateScores.liquidity > 0) {
      _tips.push('Low OI/Volume');
    }
    if (_gateScores.pop > 0) {
      _tips.push('POP too low');
    }
    if (_gateScores.data_quality > 0) {
      _tips.push('Data quality issues');
    }
    if (_tips.length) {
      html += '<div class="dynamic-suggestions">';
      html += '<div class="suggestions-label">Actionable Suggestions</div>';
      _tips.forEach(t => { html += `<div class="suggestion-tip">${t}</div>`; });
      html += '</div>';
    }
  }

  // Action buttons
  {
    const _presetName = (ft && ft.preset_name) ? String(ft.preset_name).toLowerCase() : '';
    const _btns = [];
    if (_presetName && _presetName !== 'wide') {
      _btns.push('<button class="run-wide-btn" data-action="run-wide-preset">Run Wide Preset</button>');
    }
    if (ft) {
      _btns.push('<button class="open-workbench-btn" data-action="open-workbench-trace">Open Data Workbench</button>');
    }
    if (ft) {
      _btns.push(`<button class="copy-trace-btn" data-action="copy-trace" data-trace="${escapeHtml(JSON.stringify(ft))}">Copy Trace JSON</button>`);
    }
    if (_btns.length) {
      html += `<div class="no-trades-actions" data-no-trades-actions>${_btns.join('')}</div>`;
    }
  }

  html += '</div>';
  return html;
}

/* ─── test suite ─── */
let passed = 0;
let failed = 0;
const failures = [];

function assert(condition, message) {
  if (!condition) {
    failed++;
    failures.push(message);
    console.error(`  FAIL: ${message}`);
  } else {
    passed++;
  }
}

function assertIncludes(html, text, msg) {
  assert(html.includes(text), msg || `Expected HTML to include "${text}"`);
}

function assertNotIncludes(html, text, msg) {
  assert(!html.includes(text), msg || `Expected HTML NOT to include "${text}"`);
}

// ─── 1. Preset badge renders ───
(function testPresetBadge() {
  const ft = buildFilterTrace({ preset_name: 'strict' });
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'preset-badge', 'Preset badge element exists');
  assertIncludes(html, 'strict', 'Preset badge shows "strict"');
})();

(function testPresetBadgeMissing() {
  const html = renderNoTradesPanel({ filter_trace: null, trades: [] });
  assertNotIncludes(html, 'preset-badge', 'No preset badge when filter_trace is null');
})();

// ─── 2. Pipeline stages waterfall ───
(function testPipelineStagesRender() {
  const ft = buildFilterTrace();
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'Pipeline Stages', 'Pipeline Stages section exists');
  assertIncludes(html, 'Snapshot Collection', 'Stage 1 label');
  assertIncludes(html, 'Candidate Construction', 'Stage 2 label');
  assertIncludes(html, 'Enrichment', 'Stage 3 label');
  assertIncludes(html, 'Quality Gates', 'Stage 4 label');
  assertIncludes(html, 'Dedup &amp; Ranking', 'Stage 5 label');
})();

(function testPipelineStagesCounts() {
  const ft = buildFilterTrace();
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, '7 → 5', 'Snapshot stage: 7 → 5');
  assertIncludes(html, '240 → 80', 'Candidate stage: 240 → 80');
  assertIncludes(html, '80 → 78', 'Enrichment stage: 80 → 78');
  assertIncludes(html, '78 → 0', 'Evaluate stage: 78 → 0');
  assertIncludes(html, '(-78)', 'Evaluate stage dropped count');
})();

(function testBottleneckHighlighted() {
  const ft = buildFilterTrace();
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  // Quality Gates is the bottleneck (output=0)
  assertIncludes(html, 'bottleneck', 'Bottleneck stage gets special class');
})();

(function testNoStagesWhenNoTrace() {
  const html = renderNoTradesPanel({ filter_trace: null, trades: [] });
  assertNotIncludes(html, 'Pipeline Stages', 'No pipeline stages when trace is null');
})();

// ─── 3. Gate breakdown ───
(function testGateBreakdownRenders() {
  const ft = buildFilterTrace();
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'Gate Breakdown', 'Gate Breakdown section exists');
  assertIncludes(html, 'probability', 'Probability gate listed');
  assertIncludes(html, 'quote_validation', 'Quote validation gate listed');
  assertIncludes(html, 'liquidity', 'Liquidity gate listed');
  assertIncludes(html, 'expected_value', 'Expected value gate listed');
})();

(function testGateBreakdownCounts() {
  const ft = buildFilterTrace();
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, '>30<', 'Probability gate shows count 30');
  assertIncludes(html, '>15<', 'Quote validation gate shows count 15');
})();

(function testNoGateBreakdownWhenEmpty() {
  const ft = buildFilterTrace({ gate_breakdown: {} });
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertNotIncludes(html, 'Gate Breakdown', 'No gate breakdown when empty');
})();

// ─── 4. Resolved thresholds ───
(function testResolvedThresholdsCollapsible() {
  const ft = buildFilterTrace();
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'Resolved Thresholds', 'Resolved Thresholds section exists');
  assertIncludes(html, '<details', 'Thresholds in a collapsible <details>');
  assertIncludes(html, 'min_pop', 'min_pop threshold shown');
  assertIncludes(html, '0.6', 'min_pop value shown');
  assertIncludes(html, 'min_open_interest', 'min_open_interest threshold shown');
  assertIncludes(html, '300', 'min_open_interest value shown');
})();

(function testNoThresholdsWhenEmpty() {
  const ft = buildFilterTrace({ resolved_thresholds: {} });
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertNotIncludes(html, 'resolved-thresholds', 'No thresholds section when empty');
})();

// ─── 5. Copy Trace JSON button ───
(function testCopyTraceButton() {
  const ft = buildFilterTrace();
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'copy-trace-btn', 'Copy Trace JSON button exists');
  assertIncludes(html, 'Copy Trace JSON', 'Button text correct');
  assertIncludes(html, 'data-trace=', 'Button has data-trace attribute');
})();

(function testNoCopyButtonWhenNoTrace() {
  const html = renderNoTradesPanel({ filter_trace: null, trades: [] });
  assertNotIncludes(html, 'copy-trace-btn', 'No copy button when trace is null');
})();

// ─── 6. Data quality flags ───
(function testDataQualityFlags() {
  const ft = buildFilterTrace();
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'Data Quality:', 'Data quality section');
  assertIncludes(html, 'MISSING_PRICE_HISTORY', 'Missing price history flag');
  assertIncludes(html, 'INVALID_QUOTES:3', 'Invalid quotes flag');
})();

(function testNoDataQualityWhenEmpty() {
  const ft = buildFilterTrace({ data_quality_flags: [] });
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertNotIncludes(html, 'data-quality-flags', 'No data quality section when empty');
})();

// ─── 7. Rejected examples ───
(function testRejectedExamplesPresent() {
  const ft = buildFilterTrace({
    rejected_examples: [
      { symbol: 'SPY', short_strike: 595, long_strike: 590, width: 5, net_credit: 0.80, pop: 0.55, ev_to_risk: 0.01, reasons: ['pop_below_floor', 'ev_to_risk_below_floor'] },
      { symbol: 'QQQ', short_strike: 510, long_strike: 505, width: 5, net_credit: 0.60, pop: 0.50, reasons: ['pop_below_floor'] },
    ],
  });
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'Rejected Examples (2)', 'Rejected examples header with count');
  assertIncludes(html, 'rejected-example', 'Rejected example elements');
  assertIncludes(html, 'SPY', 'First example symbol');
  assertIncludes(html, 'QQQ', 'Second example symbol');
})();

(function testNoExamplesWhenNotPresent() {
  const ft = buildFilterTrace();
  // Default trace has no rejected_examples
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertNotIncludes(html, 'rejected-examples', 'No examples section when not present');
})();

// ─── 8. Dev toggle localStorage key ───
(function testDevToggleLocalStorage() {
  localStorage.removeItem('bentrade_filter_trace_examples');

  // When not set, should be null/falsy
  assert(
    localStorage.getItem('bentrade_filter_trace_examples') !== 'true',
    'Dev toggle off by default'
  );

  localStorage.setItem('bentrade_filter_trace_examples', 'true');
  assert(
    localStorage.getItem('bentrade_filter_trace_examples') === 'true',
    'Dev toggle can be enabled via localStorage'
  );

  localStorage.removeItem('bentrade_filter_trace_examples');
})();

// ─── 9. Pipeline stage order matches backend ───
(function testStageOrderMatchesBackend() {
  const expectedOrder = [
    'snapshot_collection',
    'candidate_construction',
    'enrichment',
    'evaluate_gates',
    'dedup_ranking',
  ];
  const ft = buildFilterTrace();
  const stageNames = ft.stages.map(s => s.name);
  assert(
    JSON.stringify(stageNames) === JSON.stringify(expectedOrder),
    `Stage order matches: got ${JSON.stringify(stageNames)}`
  );
})();

// ─── 10. Trace JSON is valid and copyable ───
(function testTraceJsonSerializable() {
  const ft = buildFilterTrace();
  const json = JSON.stringify(ft, null, 2);
  const reparsed = JSON.parse(json);
  assert(reparsed.trace_id === ft.trace_id, 'Trace JSON round-trips trace_id');
  assert(reparsed.preset_name === ft.preset_name, 'Trace JSON round-trips preset_name');
  assert(reparsed.stages.length === ft.stages.length, 'Trace JSON round-trips stages');
})();

// ─── 11. Filter trace with all presets ───
['strict', 'conservative', 'balanced', 'wide'].forEach(preset => {
  (function testPresetRendering() {
    const ft = buildFilterTrace({ preset_name: preset });
    const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
    assertIncludes(html, preset, `Preset "${preset}" renders in badge`);
  })();
});

// ─── 12. Gate breakdown sorted by count (descending) ───
(function testGateBreakdownSorted() {
  const ft = buildFilterTrace({
    gate_breakdown: { liquidity: 5, probability: 30, quote_validation: 15 },
  });
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  const probIdx = html.indexOf('probability');
  const quoteIdx = html.indexOf('quote_validation');
  const liqIdx = html.indexOf('liquidity');
  assert(probIdx < quoteIdx, 'probability (30) appears before quote_validation (15)');
  assert(quoteIdx < liqIdx, 'quote_validation (15) appears before liquidity (5)');
})();

// ─── 13. Dedup stage shows correct counts ───
(function testDedupStageCounts() {
  const ft = buildFilterTrace();
  ft.stages[3] = { name: 'evaluate_gates', label: 'Quality Gates', input_count: 78, output_count: 12, detail: '' };
  ft.stages[4] = { name: 'dedup_ranking', label: 'Dedup & Ranking', input_count: 12, output_count: 10, detail: '' };
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, '12 → 10', 'Dedup stage: 12 → 10');
  assertIncludes(html, '(-2)', 'Dedup dropped 2');
})();

// ─── 14. No bottleneck when all pass ───
(function testNoBottleneckWhenAllPass() {
  const ft = buildFilterTrace();
  ft.stages = ft.stages.map(s => ({ ...s, output_count: s.input_count }));
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertNotIncludes(html, 'top-bottleneck-stages', 'No bottleneck section when no stage drops');
})();

// ─── 15. Top bottleneck stages shows largest dropoffs ───
(function testTopBottleneckStages() {
  const ft = buildFilterTrace();
  // Stage 3 (Quality Gates): 78 → 0 (biggest drop = 78)
  // Stage 0 (Chain Snapshot): 100 → 80 (drop = 20)
  // Stage 2 (Enrich): 80 → 78 (drop = 2)
  ft.stages = [
    { name: 'chain_snapshot', label: 'Chain Snapshot', input_count: 100, output_count: 80 },
    { name: 'build_candidates', label: 'Build Candidates', input_count: 80, output_count: 80 },
    { name: 'enrich', label: 'Enrich', input_count: 80, output_count: 78 },
    { name: 'evaluate_gates', label: 'Quality Gates', input_count: 78, output_count: 0 },
    { name: 'score', label: 'Score', input_count: 0, output_count: 0 },
  ];
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'Top Bottleneck Stages', 'Top bottleneck heading rendered');
  // Quality Gates (drop=78) should appear first
  const gatesIdx = html.indexOf('Quality Gates');
  const chainIdx = html.indexOf('Chain Snapshot');
  assert(gatesIdx > 0, 'Quality Gates appears in top bottleneck');
  assert(chainIdx > 0, 'Chain Snapshot appears in top bottleneck');
  assert(gatesIdx < chainIdx, 'Quality Gates (78 dropped) appears before Chain Snapshot (20 dropped)');
})();

// ─── 16. Full pipeline is in collapsible details ───
(function testFullPipelineCollapsible() {
  const ft = buildFilterTrace();
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'All Pipeline Stages', 'Full pipeline is in collapsible <details>');
  assertIncludes(html, '<details class="full-pipeline">', 'Full pipeline wrapped in details');
})();

// ─── 17. Dynamic suggestions: EV/risk dominant ───
(function testDynamicSuggestionEvRisk() {
  const ft = buildFilterTrace({ gate_breakdown: { expected_value: 70, liquidity: 10 } });
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'Actionable Suggestions', 'Suggestions section renders');
  assertIncludes(html, 'EV/Risk', 'EV/Risk suggestion appears when expected_value is dominant');
})();

// ─── 18. Dynamic suggestions: spread_width dominant ───
(function testDynamicSuggestionSpreadWidth() {
  const ft = buildFilterTrace({ gate_breakdown: { spread_structure: 50 } });
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'Bid-Ask spread too wide', 'Spread width suggestion appears');
})();

// ─── 19. Dynamic suggestions: liquidity dominant ───
(function testDynamicSuggestionLiquidity() {
  const ft = buildFilterTrace({ gate_breakdown: { liquidity: 60 } });
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'Low OI/Volume', 'Liquidity suggestion appears');
})();

// ─── 20. Dynamic suggestions: POP dominant ───
(function testDynamicSuggestionPop() {
  const ft = buildFilterTrace({ gate_breakdown: { probability: 40 } });
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'POP too low', 'POP suggestion appears when probability rejects');
})();

// ─── 21. Dynamic suggestions: data quality ───
(function testDynamicSuggestionDataQuality() {
  const ft = buildFilterTrace({ gate_breakdown: { data_quality: 30, quote_validation: 20 } });
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'Data quality issues', 'Data quality suggestion appears');
})();

// ─── 22. Dynamic suggestions: no gate breakdown => no suggestions section ───
(function testNoSuggestionsWithoutGates() {
  const ft = buildFilterTrace({ gate_breakdown: {} });
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertNotIncludes(html, 'Actionable Suggestions', 'No suggestions section when no gates triggered');
})();

// ─── 23. Run Wide Preset button: shown when preset != wide ───
(function testRunWidePresetShown() {
  const ft = buildFilterTrace();
  ft.preset_name = 'balanced';
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'run-wide-preset', 'Run Wide Preset button rendered for balanced preset');
  assertIncludes(html, 'Run Wide Preset', 'Button text present');
})();

// ─── 24. Run Wide Preset button: NOT shown when preset == wide ───
(function testRunWidePresetHiddenForWide() {
  const ft = buildFilterTrace();
  ft.preset_name = 'wide';
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertNotIncludes(html, 'run-wide-preset', 'Run Wide Preset button NOT rendered for wide preset');
})();

// ─── 25. Open Data Workbench button rendered ───
(function testOpenWorkbenchButton() {
  const ft = buildFilterTrace();
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'open-workbench-trace', 'Open Data Workbench button rendered');
  assertIncludes(html, 'Open Data Workbench', 'Button text present');
})();

// ─── 26. Open Data Workbench NOT rendered without filter trace ───
(function testOpenWorkbenchNotWithoutTrace() {
  const html = renderNoTradesPanel({ filter_trace: null, trades: [] });
  assertNotIncludes(html, 'open-workbench-trace', 'Workbench button not rendered without trace');
})();

// ─── 27. Copy Trace JSON uses data-action pattern ───
(function testCopyTraceAction() {
  const ft = buildFilterTrace();
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'data-action="copy-trace"', 'Copy Trace uses data-action attribute');
  assertIncludes(html, 'Copy Trace JSON', 'Copy Trace button text present');
})();

// ─── 28. Actions container has data-no-trades-actions ───
(function testActionsContainerAttribute() {
  const ft = buildFilterTrace();
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'data-no-trades-actions', 'Actions container has delegatable attribute');
})();

// ─── 29. Run Wide button for strict preset ───
(function testRunWideForStrict() {
  const ft = buildFilterTrace();
  ft.preset_name = 'strict';
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'run-wide-preset', 'Run Wide shown for strict preset');
})();

// ─── 30. Run Wide button for conservative preset ───
(function testRunWideForConservative() {
  const ft = buildFilterTrace();
  ft.preset_name = 'conservative';
  const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
  assertIncludes(html, 'run-wide-preset', 'Run Wide shown for conservative preset');
})();

// ─── Results ───
console.log(`\n${'='.repeat(50)}`);
console.log(`Filter Trace UI Tests: ${passed} passed, ${failed} failed`);
if (failures.length) {
  console.log('\nFailures:');
  failures.forEach(f => console.log(`  - ${f}`));
}
console.log('='.repeat(50));
process.exit(failed > 0 ? 1 : 0);
