/**
 * Tests for data-quality gate hardening in the Credit Spread scanner UI.
 *
 * Validates:
 * - "Data Quality (Missing Fields)" gate label renders in gate breakdown
 * - Missing field counts section renders with counts and percentages
 * - Data quality mode badge renders with correct color
 * - profiles.js and defaults.js include data_quality_mode for credit_spread
 * - DQ waived row renders for lenient mode traces
 * - Missing field counts hidden when no fields missing
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

/* ─── test harness ─── */
const assert = require('assert');
let passed = 0, failed = 0, errors = [];

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  ✓ ${name}`);
  } catch (e) {
    failed++;
    errors.push({ name, error: e });
    console.error(`  ✗ ${name}: ${e.message}`);
  }
}

function group(name, fn) {
  console.log(`\n${name}`);
  fn();
}

/* ─── Load modules under test ─── */
const path = require('path');
const fs = require('fs');

function loadModule(relPath) {
  const abs = path.resolve(__dirname, '..', '..', 'frontend', 'assets', 'js', relPath);
  const src = fs.readFileSync(abs, 'utf-8');
  const fn = new Function(src);
  fn();
}

loadModule('strategies/profiles.js');
loadModule('strategies/defaults.js');

/* ─── HTML helpers ─── */
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
    data_quality_mode: 'balanced',
    resolved_thresholds: {
      dte_min: 7, dte_max: 45,
      min_pop: 0.60, min_ev_to_risk: 0.02, min_ror: 0.01,
      min_open_interest: 300, min_volume: 20,
    },
    stages: [
      { name: 'snapshot_collection', label: 'Snapshot Collection', input_count: 7, output_count: 5, detail: '' },
      { name: 'candidate_construction', label: 'Candidate Construction', input_count: 240, output_count: 80, detail: '' },
      { name: 'enrichment', label: 'Enrichment', input_count: 80, output_count: 78, detail: '' },
      { name: 'evaluate_gates', label: 'Quality Gates', input_count: 78, output_count: 0, detail: '' },
      { name: 'dedup_ranking', label: 'Dedup & Ranking', input_count: 0, output_count: 0, detail: '' },
    ],
    gate_breakdown: {
      quote_validation: 15,
      data_quality: 8,
      probability: 30,
      liquidity: 25,
    },
    rejection_reasons: {},
    data_quality_flags: ['INVALID_QUOTES:3'],
    missing_field_counts: {
      open_interest: 12,
      volume: 15,
      bid: 3,
      ask: 2,
      quote_rejected: 5,
      dq_waived: 0,
      total_enriched: 78,
    },
    ...overrides,
  };
}

/**
 * Simulates the rendering logic from strategy_dashboard_shell.js
 * (condensed to test the new data quality sections).
 */
function renderNoTradesPanel(data) {
  const ft = data.filter_trace || null;
  let html = '<div class="no-trades-panel">';

  // Preset badge
  if (ft && ft.preset_name) {
    html += `<span class="preset-badge">${escapeHtml(ft.preset_name)}</span>`;
  }

  // Gate breakdown
  if (ft && ft.gate_breakdown) {
    const gateLabels = {
      quote_validation: 'Quote Validation',
      metrics_computation: 'Metrics Computation',
      probability: 'Probability (POP)',
      expected_value: 'Expected Value (EV/Risk)',
      return_on_risk: 'Return on Risk',
      spread_structure: 'Spread Structure',
      liquidity: 'Liquidity (OI/Volume)',
      data_quality: 'Data Quality (Missing Fields)',
      other: 'Other',
    };
    const gateEntries = Object.entries(ft.gate_breakdown).filter(([, v]) => v > 0);
    if (gateEntries.length) {
      html += '<div class="gate-breakdown-label">Gate Breakdown</div>';
      html += '<table class="gate-breakdown-table">';
      gateEntries.sort((a, b) => b[1] - a[1]);
      gateEntries.forEach(([gate, count]) => {
        const label = gateLabels[gate] || gate.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        html += `<tr class="gate-row" data-gate="${escapeHtml(gate)}"><td class="gate-name">${escapeHtml(label)}</td><td class="gate-count">${count}</td></tr>`;
      });
      html += '</table>';
    }
  }

  // Resolved thresholds
  if (ft && ft.resolved_thresholds && Object.keys(ft.resolved_thresholds).length) {
    const _thresholdLabels = {
      dte_min:'DTE Min', dte_max:'DTE Max', min_pop:'Min POP',
      min_ev_to_risk:'Min EV/Risk', min_ror:'Min ROR',
      max_bid_ask_spread_pct:'Max Bid-Ask Spread %',
      min_open_interest:'Min Open Interest', min_volume:'Min Volume',
    };
    html += '<details class="resolved-thresholds"><summary>Resolved Thresholds</summary><table>';
    Object.entries(ft.resolved_thresholds).forEach(([key, val]) => {
      const label = _thresholdLabels[key] || key;
      html += `<tr><td class="threshold-label">${escapeHtml(label)}</td><td>${val}</td></tr>`;
    });
    html += '</table></details>';
  }

  // Data quality flags
  if (ft && Array.isArray(ft.data_quality_flags) && ft.data_quality_flags.length) {
    html += `<div class="data-quality-flags">Data Quality: ${ft.data_quality_flags.join(', ')}</div>`;
  }

  // Data quality mode badge
  if (ft && ft.data_quality_mode) {
    const dqModeColors = { strict: '#ff5e5e', balanced: '#ffbb33', lenient: '#4ade80' };
    const dqColor = dqModeColors[ft.data_quality_mode] || '#9fefff';
    html += `<div class="dq-mode-badge">Data Quality Mode: <span class="dq-mode-value" style="color:${dqColor};">${escapeHtml(ft.data_quality_mode)}</span></div>`;
  }

  // Missing field counts
  if (ft && ft.missing_field_counts && typeof ft.missing_field_counts === 'object') {
    const mfc = ft.missing_field_counts;
    const total = mfc.total_enriched || 0;
    const hasMissing = (mfc.open_interest || 0) + (mfc.volume || 0) + (mfc.bid || 0) + (mfc.ask || 0) + (mfc.quote_rejected || 0) > 0;
    if (hasMissing && total > 0) {
      html += '<details class="missing-field-counts"><summary>Missing Field Counts</summary><table>';
      const rows = [
        ['Open Interest', mfc.open_interest],
        ['Volume', mfc.volume],
        ['Bid', mfc.bid],
        ['Ask', mfc.ask],
        ['Quote Rejected', mfc.quote_rejected],
      ];
      rows.forEach(([label, count]) => {
        if (count > 0) {
          const pct = ((count / total) * 100).toFixed(1);
          html += `<tr class="mfc-row"><td>${label}</td><td class="mfc-count">${count}</td><td class="mfc-pct">${pct}%</td></tr>`;
        }
      });
      if (mfc.dq_waived > 0) {
        html += `<tr class="mfc-row mfc-waived"><td>DQ Waived (lenient)</td><td>${mfc.dq_waived}</td><td>—</td></tr>`;
      }
      html += '</table></details>';
    }
  }

  html += '</div>';
  return html;
}

/* ═══════ TEST SUITES ═══════ */

group('1. profiles.js — data_quality_mode in credit_spread profiles', () => {
  const profiles = window.BenTradeScannerProfiles;

  test('profiles module loaded', () => {
    assert.ok(profiles, 'BenTradeScannerProfiles defined');
  });

  test('strict profile has data_quality_mode: strict', () => {
    const cfg = profiles.getProfile('credit_spread', 'strict');
    assert.strictEqual(cfg.data_quality_mode, 'strict');
  });

  test('conservative profile has data_quality_mode: balanced', () => {
    const cfg = profiles.getProfile('credit_spread', 'conservative');
    assert.strictEqual(cfg.data_quality_mode, 'balanced');
  });

  test('balanced profile has data_quality_mode: balanced', () => {
    const cfg = profiles.getProfile('credit_spread', 'balanced');
    assert.strictEqual(cfg.data_quality_mode, 'balanced');
  });

  test('wide profile has data_quality_mode: lenient', () => {
    const cfg = profiles.getProfile('credit_spread', 'wide');
    assert.strictEqual(cfg.data_quality_mode, 'lenient');
  });

  test('all 4 levels have data_quality_mode', () => {
    ['strict', 'conservative', 'balanced', 'wide'].forEach(level => {
      const cfg = profiles.getProfile('credit_spread', level);
      assert.ok(cfg.data_quality_mode, `${level} has data_quality_mode`);
    });
  });
});

group('2. defaults.js — data_quality_mode in credit_spread fallbacks', () => {
  const defaults = window.BenTradeStrategyDefaults;

  test('defaults module loaded', () => {
    assert.ok(defaults, 'BenTradeStrategyDefaults defined');
  });

  test('all credit_spread presets include data_quality_mode via getStrategyDefaults', () => {
    ['strict', 'conservative', 'balanced', 'wide'].forEach(level => {
      const cfg = defaults.getStrategyDefaults('credit_spread', level);
      assert.ok(cfg.data_quality_mode, `${level} preset has data_quality_mode`);
    });
  });

  test('wide defaults uses lenient', () => {
    const cfg = defaults.getStrategyDefaults('credit_spread', 'wide');
    assert.strictEqual(cfg.data_quality_mode, 'lenient');
  });

  test('strict defaults uses strict', () => {
    const cfg = defaults.getStrategyDefaults('credit_spread', 'strict');
    assert.strictEqual(cfg.data_quality_mode, 'strict');
  });
});

group('3. Gate breakdown — data_quality gate label', () => {
  test('data_quality gate renders "Data Quality (Missing Fields)" label', () => {
    const ft = buildFilterTrace();
    const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
    assert.ok(html.includes('Data Quality (Missing Fields)'), 'DQ gate label rendered');
  });

  test('data_quality gate shows count', () => {
    const ft = buildFilterTrace();
    const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
    assert.ok(html.includes('>8<'), 'DQ gate count rendered');
  });

  test('data_quality gate absent when no DQ rejections', () => {
    const ft = buildFilterTrace({
      gate_breakdown: { probability: 30, liquidity: 25 },
    });
    const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
    assert.ok(!html.includes('Data Quality (Missing Fields)'), 'No DQ gate when absent');
  });
});

group('4. Data quality mode badge', () => {
  test('DQ mode badge renders for balanced', () => {
    const ft = buildFilterTrace({ data_quality_mode: 'balanced' });
    const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
    assert.ok(html.includes('dq-mode-badge'), 'DQ mode badge element exists');
    assert.ok(html.includes('balanced'), 'Shows balanced mode');
  });

  test('DQ mode badge renders for strict with red color', () => {
    const ft = buildFilterTrace({ data_quality_mode: 'strict' });
    const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
    assert.ok(html.includes('#ff5e5e'), 'Strict uses red color');
  });

  test('DQ mode badge renders for lenient with green color', () => {
    const ft = buildFilterTrace({ data_quality_mode: 'lenient' });
    const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
    assert.ok(html.includes('#4ade80'), 'Lenient uses green color');
  });

  test('No DQ mode badge when mode absent', () => {
    const ft = buildFilterTrace({ data_quality_mode: null });
    const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
    assert.ok(!html.includes('dq-mode-badge'), 'No badge when mode is null');
  });
});

group('5. Missing field counts section', () => {
  test('Missing field counts section renders when data present', () => {
    const html = renderNoTradesPanel({ filter_trace: buildFilterTrace(), trades: [] });
    assert.ok(html.includes('Missing Field Counts'), 'Section header rendered');
    assert.ok(html.includes('missing-field-counts'), 'Section element exists');
  });

  test('Shows OI missing count and percentage', () => {
    const html = renderNoTradesPanel({ filter_trace: buildFilterTrace(), trades: [] });
    // 12 out of 78 = 15.4%
    assert.ok(html.includes('>12<'), 'OI count shown');
    assert.ok(html.includes('15.4%'), 'OI percentage shown');
  });

  test('Shows volume missing count', () => {
    const html = renderNoTradesPanel({ filter_trace: buildFilterTrace(), trades: [] });
    assert.ok(html.includes('>15<'), 'Volume count shown');
  });

  test('Shows bid missing count', () => {
    const html = renderNoTradesPanel({ filter_trace: buildFilterTrace(), trades: [] });
    assert.ok(html.includes('Bid'), 'Bid label shown');
  });

  test('Shows ask missing count', () => {
    const html = renderNoTradesPanel({ filter_trace: buildFilterTrace(), trades: [] });
    assert.ok(html.includes('Ask'), 'Ask label shown');
  });

  test('Shows quote rejected count', () => {
    const html = renderNoTradesPanel({ filter_trace: buildFilterTrace(), trades: [] });
    assert.ok(html.includes('Quote Rejected'), 'Quote Rejected label shown');
  });

  test('Hidden when no missing fields', () => {
    const ft = buildFilterTrace({
      missing_field_counts: {
        open_interest: 0, volume: 0, bid: 0, ask: 0,
        quote_rejected: 0, dq_waived: 0, total_enriched: 78,
      },
    });
    const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
    assert.ok(!html.includes('Missing Field Counts'), 'Section hidden when all zero');
  });

  test('Hidden when total_enriched is 0', () => {
    const ft = buildFilterTrace({
      missing_field_counts: {
        open_interest: 5, volume: 3, bid: 0, ask: 0,
        quote_rejected: 0, dq_waived: 0, total_enriched: 0,
      },
    });
    const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
    assert.ok(!html.includes('Missing Field Counts'), 'Section hidden when total is 0');
  });

  test('Hidden when missing_field_counts not present', () => {
    const ft = buildFilterTrace();
    delete ft.missing_field_counts;
    const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
    assert.ok(!html.includes('Missing Field Counts'), 'Section hidden when key absent');
  });
});

group('6. DQ waived row in missing field counts', () => {
  test('DQ waived row shown when count > 0', () => {
    const ft = buildFilterTrace({
      data_quality_mode: 'lenient',
      missing_field_counts: {
        open_interest: 10, volume: 8, bid: 0, ask: 0,
        quote_rejected: 0, dq_waived: 6, total_enriched: 50,
      },
    });
    const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
    assert.ok(html.includes('DQ Waived (lenient)'), 'Waived row shown');
    assert.ok(html.includes('mfc-waived'), 'Waived row has waived class');
  });

  test('DQ waived row hidden when count is 0', () => {
    const ft = buildFilterTrace();  // default dq_waived = 0
    const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
    assert.ok(!html.includes('DQ Waived'), 'Waived row not shown when 0');
  });
});

group('7. Resolved thresholds include min_ror', () => {
  test('min_ror renders in thresholds', () => {
    const ft = buildFilterTrace();
    const html = renderNoTradesPanel({ filter_trace: ft, trades: [] });
    assert.ok(html.includes('Min ROR'), 'Min ROR label rendered');
  });
});

group('8. Collapsible sections use <details>', () => {
  test('Missing field counts wraps in <details>', () => {
    const html = renderNoTradesPanel({ filter_trace: buildFilterTrace(), trades: [] });
    // Check that "missing-field-counts" appears inside a <details> element
    const idx = html.indexOf('missing-field-counts');
    const detailsIdx = html.lastIndexOf('<details', idx);
    assert.ok(detailsIdx >= 0, 'Missing fields in a <details> element');
  });
});

/* ─── Results ─── */
console.log(`\n${'='.repeat(50)}`);
console.log(`Data Quality UI Tests: ${passed} passed, ${failed} failed`);
if (errors.length) {
  console.log('\nFailures:');
  errors.forEach(({ name, error }) => console.log(`  - ${name}: ${error.message}`));
}
console.log('='.repeat(50));
process.exit(failed > 0 ? 1 : 0);
