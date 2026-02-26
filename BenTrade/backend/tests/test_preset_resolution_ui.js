/**
 * Tests for preset resolution across all four filter levels (strict / conservative / balanced / wide).
 *
 * Validates:
 * - profiles.js credit_spread profiles include min_ror
 * - defaults.js fallback presets match profiles.js
 * - Orchestrator includes preset key in payload
 * - Strategy dashboard EventSource URL includes preset
 * - Preset ordering in profiles: strict > conservative > balanced > wide
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
  // Execute in current global scope
  const fn = new Function(src);
  fn();
}

// Load profiles.js first (it defines BenTradeScannerProfiles)
loadModule('strategies/profiles.js');
// Then defaults.js (it reads BenTradeScannerProfiles)
loadModule('strategies/defaults.js');

const profiles = window.BenTradeScannerProfiles;
const defaults = window.BenTradeStrategyDefaults;

/* ═══════════════════════════════════════════════════════════════════
   ① profiles.js credit_spread completeness
   ═══════════════════════════════════════════════════════════════════ */

group('profiles.js credit_spread completeness', () => {
  const LEVELS = ['strict', 'conservative', 'balanced', 'wide'];

  test('all four levels have profiles', () => {
    for (const level of LEVELS) {
      const p = profiles.getProfile('credit_spread', level);
      assert.ok(p, `Missing profile for level: ${level}`);
    }
  });

  test('all levels include min_ror', () => {
    for (const level of LEVELS) {
      const p = profiles.getProfile('credit_spread', level);
      assert.ok('min_ror' in p, `${level} profile missing min_ror`);
      assert.ok(typeof p.min_ror === 'number', `${level} min_ror should be a number`);
      assert.ok(p.min_ror > 0, `${level} min_ror should be positive`);
    }
  });

  test('all levels include min_pop', () => {
    for (const level of LEVELS) {
      const p = profiles.getProfile('credit_spread', level);
      assert.ok('min_pop' in p, `${level} profile missing min_pop`);
    }
  });

  test('all levels include min_ev_to_risk', () => {
    for (const level of LEVELS) {
      const p = profiles.getProfile('credit_spread', level);
      assert.ok('min_ev_to_risk' in p, `${level} profile missing min_ev_to_risk`);
    }
  });

  test('all levels include min_open_interest', () => {
    for (const level of LEVELS) {
      const p = profiles.getProfile('credit_spread', level);
      assert.ok('min_open_interest' in p, `${level} missing min_open_interest`);
    }
  });

  test('all levels include min_volume', () => {
    for (const level of LEVELS) {
      const p = profiles.getProfile('credit_spread', level);
      assert.ok('min_volume' in p, `${level} missing min_volume`);
    }
  });

  test('all levels include max_bid_ask_spread_pct', () => {
    for (const level of LEVELS) {
      const p = profiles.getProfile('credit_spread', level);
      assert.ok('max_bid_ask_spread_pct' in p, `${level} missing max_bid_ask_spread_pct`);
    }
  });
});

/* ═══════════════════════════════════════════════════════════════════
   ② Preset ordering: strict > conservative > balanced > wide
   ═══════════════════════════════════════════════════════════════════ */

group('Preset ordering (strict is tightest, wide is loosest)', () => {
  const s = profiles.getProfile('credit_spread', 'strict');
  const c = profiles.getProfile('credit_spread', 'conservative');
  const b = profiles.getProfile('credit_spread', 'balanced');
  const w = profiles.getProfile('credit_spread', 'wide');

  test('min_pop: strict ≥ conservative ≥ balanced ≥ wide', () => {
    assert.ok(s.min_pop >= c.min_pop, `strict (${s.min_pop}) < conservative (${c.min_pop})`);
    assert.ok(c.min_pop >= b.min_pop, `conservative (${c.min_pop}) < balanced (${b.min_pop})`);
    assert.ok(b.min_pop >= w.min_pop, `balanced (${b.min_pop}) < wide (${w.min_pop})`);
  });

  test('min_ev_to_risk: strict ≥ conservative ≥ balanced ≥ wide', () => {
    assert.ok(s.min_ev_to_risk >= c.min_ev_to_risk);
    assert.ok(c.min_ev_to_risk >= b.min_ev_to_risk);
    assert.ok(b.min_ev_to_risk >= w.min_ev_to_risk);
  });

  test('min_ror: strict ≥ conservative ≥ balanced ≥ wide', () => {
    assert.ok(s.min_ror >= c.min_ror, `strict (${s.min_ror}) < conservative (${c.min_ror})`);
    assert.ok(c.min_ror >= b.min_ror, `conservative (${c.min_ror}) < balanced (${b.min_ror})`);
    assert.ok(b.min_ror >= w.min_ror, `balanced (${b.min_ror}) < wide (${w.min_ror})`);
  });

  test('min_open_interest: strict ≥ conservative ≥ balanced ≥ wide', () => {
    assert.ok(s.min_open_interest >= c.min_open_interest);
    assert.ok(c.min_open_interest >= b.min_open_interest);
    assert.ok(b.min_open_interest >= w.min_open_interest);
  });

  test('min_volume: strict ≥ conservative ≥ balanced ≥ wide', () => {
    assert.ok(s.min_volume >= c.min_volume);
    assert.ok(c.min_volume >= b.min_volume);
    assert.ok(b.min_volume >= w.min_volume);
  });

  test('max_bid_ask_spread_pct: strict ≤ conservative ≤ balanced ≤ wide', () => {
    assert.ok(s.max_bid_ask_spread_pct <= c.max_bid_ask_spread_pct);
    assert.ok(c.max_bid_ask_spread_pct <= b.max_bid_ask_spread_pct);
    assert.ok(b.max_bid_ask_spread_pct <= w.max_bid_ask_spread_pct);
  });

  test('strict is tighter than balanced on ≥3 evaluate dimensions', () => {
    let tighterCount = 0;
    if (s.min_pop > b.min_pop) tighterCount++;
    if (s.min_ev_to_risk > b.min_ev_to_risk) tighterCount++;
    if (s.min_ror > b.min_ror) tighterCount++;
    if (s.min_open_interest > b.min_open_interest) tighterCount++;
    if (s.min_volume > b.min_volume) tighterCount++;
    if (s.max_bid_ask_spread_pct < b.max_bid_ask_spread_pct) tighterCount++;
    assert.ok(tighterCount >= 3, `Expected ≥3 tighter dims, got ${tighterCount}`);
  });

  test('wide is looser than balanced on ≥3 evaluate dimensions', () => {
    let looserCount = 0;
    if (w.min_pop < b.min_pop) looserCount++;
    if (w.min_ev_to_risk < b.min_ev_to_risk) looserCount++;
    if (w.min_ror < b.min_ror) looserCount++;
    if (w.min_open_interest < b.min_open_interest) looserCount++;
    if (w.min_volume < b.min_volume) looserCount++;
    if (w.max_bid_ask_spread_pct > b.max_bid_ask_spread_pct) looserCount++;
    assert.ok(looserCount >= 3, `Expected ≥3 looser dims, got ${looserCount}`);
  });

  test('each level produces unique threshold values', () => {
    const serialize = (p) => JSON.stringify(Object.keys(p).sort().map(k => [k, p[k]]));
    const sigs = new Set([serialize(s), serialize(c), serialize(b), serialize(w)]);
    assert.strictEqual(sigs.size, 4, 'All four levels should produce unique thresholds');
  });
});

/* ═══════════════════════════════════════════════════════════════════
   ③ defaults.js alignment with profiles.js
   ═══════════════════════════════════════════════════════════════════ */

group('defaults.js alignment with profiles.js', () => {
  test('getStrategyDefaults uses profiles.js when loaded', () => {
    const strict = defaults.getStrategyDefaults('credit_spread', 'strict');
    const profileStrict = profiles.getProfile('credit_spread', 'strict');
    // They should have the same numeric values
    assert.strictEqual(strict.min_pop, profileStrict.min_pop, 'min_pop should match profile');
    assert.strictEqual(strict.min_ror, profileStrict.min_ror, 'min_ror should match profile');
    assert.strictEqual(strict.min_open_interest, profileStrict.min_open_interest, 'min_open_interest should match profile');
  });

  test('getStrategyDefaults strict differs from balanced', () => {
    const strict = defaults.getStrategyDefaults('credit_spread', 'strict');
    const balanced = defaults.getStrategyDefaults('credit_spread', 'balanced');
    assert.notStrictEqual(strict.min_pop, balanced.min_pop, 'strict min_pop should differ from balanced');
    assert.notStrictEqual(strict.min_ror, balanced.min_ror, 'strict min_ror should differ from balanced');
  });

  test('getPresetNames returns all four levels', () => {
    const names = defaults.getPresetNames('credit_spread');
    assert.ok(names.includes('strict'), 'Missing strict');
    assert.ok(names.includes('conservative'), 'Missing conservative');
    assert.ok(names.includes('balanced'), 'Missing balanced');
    assert.ok(names.includes('wide'), 'Missing wide');
  });

  test('all four presets return unique defaults', () => {
    const sigs = new Set();
    for (const level of ['strict', 'conservative', 'balanced', 'wide']) {
      const d = defaults.getStrategyDefaults('credit_spread', level);
      sigs.add(JSON.stringify([d.min_pop, d.min_ror, d.min_open_interest]));
    }
    assert.strictEqual(sigs.size, 4, 'All four presets should produce unique defaults');
  });
});

/* ═══════════════════════════════════════════════════════════════════
   ④ Orchestrator preset key inclusion
   ═══════════════════════════════════════════════════════════════════ */

group('Orchestrator preset key in payload', () => {
  // Load the orchestrator source to inspect OPTION_SCANNER_DEFS and runScannerSuite
  const orchPath = path.resolve(__dirname, '..', '..', 'frontend', 'assets', 'js', 'stores', 'scannerOrchestrator.js');
  const orchSrc = fs.readFileSync(orchPath, 'utf-8');

  test('orchestrator source sets scanPayload.preset = effectiveLevel', () => {
    assert.ok(
      orchSrc.includes('scanPayload.preset = effectiveLevel'),
      'Expected scanPayload.preset = effectiveLevel in orchestrator'
    );
  });

  test('orchestrator source still merges profile params', () => {
    assert.ok(
      orchSrc.includes('getProfile'),
      'Orchestrator should still call getProfile'
    );
  });
});

/* ═══════════════════════════════════════════════════════════════════
   ⑤ Strategy dashboard shell includes min_ror in credit-spread form
   ═══════════════════════════════════════════════════════════════════ */

group('Strategy dashboard shell credit-spread form', () => {
  const shellPath = path.resolve(__dirname, '..', '..', 'frontend', 'assets', 'js', 'pages', 'strategy_dashboard_shell.js');
  const shellSrc = fs.readFileSync(shellPath, 'utf-8');

  test('credit-spread form definition includes min_ror field', () => {
    assert.ok(
      shellSrc.includes("key: 'min_ror'"),
      "Expected credit-spread form to include min_ror field"
    );
  });

  test('credit-spread form definition includes min_pop field', () => {
    assert.ok(
      shellSrc.includes("key: 'min_pop'"),
      "Expected credit-spread form to include min_pop field"
    );
  });
});

/* ═══════════════════════════════════════════════════════════════════
   ⑥ SSE endpoint reads min_ror from query params
   ═══════════════════════════════════════════════════════════════════ */

group('routes_strategies.py reads min_ror', () => {
  const routesPath = path.resolve(__dirname, '..', 'app', 'api', 'routes_strategies.py');
  const routesSrc = fs.readFileSync(routesPath, 'utf-8');

  test('min_ror is listed in float query param parsing', () => {
    assert.ok(
      routesSrc.includes('"min_ror"'),
      'Expected "min_ror" in the SSE endpoint query param parsing'
    );
  });
});

/* ═══════════════════════════════════════════════════════════════════
   ⑦ R8 Calibration: exact threshold values
   ═══════════════════════════════════════════════════════════════════ */

group('R8 Calibration — exact recalibrated threshold values', () => {
  test('strict unchanged: min_pop=0.70, min_ev_to_risk=0.03, min_ror=0.03', () => {
    const s = profiles.getProfile('credit_spread', 'strict');
    assert.strictEqual(s.min_pop, 0.70);
    assert.strictEqual(s.min_ev_to_risk, 0.03);
    assert.strictEqual(s.min_ror, 0.03);
    assert.strictEqual(s.max_bid_ask_spread_pct, 1.0);
    assert.strictEqual(s.min_open_interest, 1000);
    assert.strictEqual(s.min_volume, 100);
  });

  test('conservative calibrated: min_pop=0.60, min_ev_to_risk=0.012', () => {
    const c = profiles.getProfile('credit_spread', 'conservative');
    assert.strictEqual(c.min_pop, 0.60);
    assert.strictEqual(c.min_ev_to_risk, 0.012);
    assert.strictEqual(c.min_ror, 0.01);
    assert.strictEqual(c.max_bid_ask_spread_pct, 1.5);
    assert.strictEqual(c.min_open_interest, 200);
    assert.strictEqual(c.min_volume, 10);
  });

  test('balanced calibrated: min_pop=0.55, min_ev_to_risk=0.008, min_ror=0.005', () => {
    const b = profiles.getProfile('credit_spread', 'balanced');
    assert.strictEqual(b.min_pop, 0.55);
    assert.strictEqual(b.min_ev_to_risk, 0.008);
    assert.strictEqual(b.min_ror, 0.005);
    assert.strictEqual(b.max_bid_ask_spread_pct, 2.0);
    assert.strictEqual(b.min_open_interest, 100);
    assert.strictEqual(b.min_volume, 5);
  });

  test('wide calibrated: min_pop=0.45, min_ev_to_risk=0.005, min_ror=0.002', () => {
    const w = profiles.getProfile('credit_spread', 'wide');
    assert.strictEqual(w.min_pop, 0.45);
    assert.strictEqual(w.min_ev_to_risk, 0.005);
    assert.strictEqual(w.min_ror, 0.002);
    assert.strictEqual(w.max_bid_ask_spread_pct, 3.0);
    assert.strictEqual(w.min_open_interest, 25);
    assert.strictEqual(w.min_volume, 1);
  });

  test('defaults.js balanced matches profiles.js balanced', () => {
    const profBal = profiles.getProfile('credit_spread', 'balanced');
    const defBal  = defaults.getStrategyDefaults('credit_spread', 'balanced');
    assert.strictEqual(defBal.min_pop, profBal.min_pop, 'min_pop mismatch');
    assert.strictEqual(defBal.min_ev_to_risk, profBal.min_ev_to_risk, 'min_ev_to_risk mismatch');
    assert.strictEqual(defBal.min_ror, profBal.min_ror, 'min_ror mismatch');
    assert.strictEqual(defBal.min_open_interest, profBal.min_open_interest, 'min_open_interest mismatch');
    assert.strictEqual(defBal.min_volume, profBal.min_volume, 'min_volume mismatch');
  });
});

/* ═══════════════════════════════════════════════════════════════════
   ⑧ Preset-diff UI section in dashboard shell
   ═══════════════════════════════════════════════════════════════════ */

group('Preset-diff UI section', () => {
  const shellPath = path.resolve(__dirname, '..', '..', 'frontend', 'assets', 'js', 'pages', 'strategy_dashboard_shell.js');
  const shellSrc = fs.readFileSync(shellPath, 'utf-8');

  test('dashboard shell contains Preset Comparison summary', () => {
    assert.ok(
      shellSrc.includes('Preset Comparison'),
      'Expected "Preset Comparison" text in dashboard shell'
    );
  });

  test('preset comparison reads profiles via getProfile', () => {
    assert.ok(
      shellSrc.includes("getProfile?.(_sId, 'strict')"),
      'Expected strict profile lookup in Preset Comparison'
    );
    assert.ok(
      shellSrc.includes("getProfile?.(_sId, 'wide')"),
      'Expected wide profile lookup in Preset Comparison'
    );
  });

  test('preset comparison includes all quality-gate keys', () => {
    const keys = ['min_pop', 'min_ev_to_risk', 'min_ror', 'max_bid_ask_spread_pct', 'min_open_interest', 'min_volume'];
    for (const k of keys) {
      assert.ok(shellSrc.includes(`key:'${k}'`), `Missing key '${k}' in preset diff`);
    }
  });
});

/* ═══════════════════════════════════════════════════════════════════
   Summary
   ═══════════════════════════════════════════════════════════════════ */

console.log(`\n${'═'.repeat(50)}`);
console.log(`Preset resolution tests: ${passed} passed, ${failed} failed`);
if (errors.length) {
  console.log('\nFailures:');
  errors.forEach(({ name, error }) => console.error(`  • ${name}: ${error.message}`));
}
console.log('═'.repeat(50));
process.exit(failed > 0 ? 1 : 0);
