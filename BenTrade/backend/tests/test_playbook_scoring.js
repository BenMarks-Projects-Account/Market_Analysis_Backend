/**
 * Unit tests for playbookScoring.js
 *
 * Run with Node.js:  node tests/test_playbook_scoring.js
 *
 * We simulate the browser environment by setting up a minimal window object,
 * then eval-ing the module source.
 */
'use strict';

const fs = require('fs');
const path = require('path');

/* ── Minimal browser shim ── */
global.window = global;
global.window.BenTradeUtils = {
  format: {
    toNumber: function(v){ var n = Number(v); return Number.isFinite(n) ? n : null; },
    normalizeScore: function(v){ return v; },
    formatScore: function(v){ return String(v); },
    escapeHtml: function(v){ return String(v); },
  },
};

/* ── Load module ── */
const modulePath = path.resolve(__dirname, '..', '..', 'frontend', 'assets', 'js', 'stores', 'playbookScoring.js');
const src = fs.readFileSync(modulePath, 'utf-8');
eval(src);

const pb = window.BenTradePlaybookScoring;
let passed = 0;
let failed = 0;

function assert(condition, msg){
  if(!condition){
    failed++;
    console.error('  FAIL:', msg);
  } else {
    passed++;
    console.log('  PASS:', msg);
  }
}

function assertClose(a, b, tolerance, msg){
  assert(Math.abs(a - b) <= tolerance, msg + ` (got ${a}, expected ~${b})`);
}

/* ================================================================
   Test 1: normalizePlaybook — enriched playbook with all 3 lanes
   ================================================================ */
console.log('\n--- normalizePlaybook (enriched) ---');
{
  const enriched = {
    playbook: {
      primary: [
        { strategy: 'put_credit_spread', label: 'Put Credit Spread', confidence: 0.8, why: ['test'] },
        { strategy: 'covered_call', label: 'Covered Call', confidence: 0.7, why: [] },
      ],
      secondary: [
        { strategy: 'iron_condor', label: 'Iron Condor', confidence: 0.5, why: [] },
      ],
      avoid: [
        { strategy: 'put_debit', label: 'Put Debit', confidence: 0.3, why: [] },
      ],
    },
  };
  const result = pb.normalizePlaybook(enriched, null);
  assert(result.primary.size === 2, 'primary has 2 entries');
  assert(result.primary.has('putcreditspread'), 'primary contains putcreditspread');
  assert(result.primary.has('coveredcall'), 'primary contains coveredcall');
  assert(result.secondary.size === 1, 'secondary has 1 entry');
  assert(result.secondary.has('ironcondor'), 'secondary contains ironcondor');
  assert(result.avoid.size === 1, 'avoid has 1 entry');
  assert(result.avoid.has('putdebit'), 'avoid contains putdebit');
}

/* ================================================================
   Test 2: normalizePlaybook — regime fallback (no secondary)
   ================================================================ */
console.log('\n--- normalizePlaybook (regime fallback) ---');
{
  const regime = {
    suggested_playbook: {
      primary: ['put_credit_spread', 'covered_call'],
      avoid: ['short_gamma'],
      notes: ['test note'],
    },
  };
  const result = pb.normalizePlaybook(null, regime);
  assert(result.primary.size === 2, 'regime primary has 2');
  assert(result.secondary.size === 0, 'regime has no secondary');
  assert(result.avoid.size === 1, 'regime avoid has 1');
  assert(result.avoid.has('shortgamma'), 'avoid contains shortgamma');
}

/* ================================================================
   Test 3: computeAdjustedScore — Primary strategy (no penalty)
   ================================================================ */
console.log('\n--- computeAdjustedScore: primary ---');
{
  const playbook = pb.normalizePlaybook({
    playbook: {
      primary: [{ strategy: 'put_credit_spread' }],
      secondary: [{ strategy: 'iron_condor' }],
      avoid: [{ strategy: 'put_debit' }],
    },
  }, null);

  const result = pb.computeAdjustedScore({ score: 80, strategy: 'put_credit_spread' }, playbook);
  assert(result.lane === 'primary', 'lane is primary');
  assert(result.multiplier === 1.0, 'no penalty for primary');
  assertClose(result.adjustedScore, 80, 0.01, 'adjusted == base');
  assertClose(result.baseScore, 80, 0.01, 'base is 80');
}

/* ================================================================
   Test 4: computeAdjustedScore — Avoid strategy (-40%)
   ================================================================ */
console.log('\n--- computeAdjustedScore: avoid ---');
{
  const playbook = pb.normalizePlaybook({
    playbook: {
      primary: [{ strategy: 'put_credit_spread' }],
      secondary: [],
      avoid: [{ strategy: 'debit_spreads' }],
    },
  }, null);

  const result = pb.computeAdjustedScore({ score: 85, strategy: 'debit_spreads' }, playbook);
  assert(result.lane === 'avoid', 'lane is avoid');
  assert(result.multiplier === 0.60, 'multiplier is 0.60');
  assertClose(result.adjustedScore, 51, 0.1, '85 * 0.60 = 51.0');
}

/* ================================================================
   Test 5: computeAdjustedScore — Not in any lane (-15%)
   ================================================================ */
console.log('\n--- computeAdjustedScore: neutral (not in playbook) ---');
{
  const playbook = pb.normalizePlaybook({
    playbook: {
      primary: [{ strategy: 'put_credit_spread' }],
      secondary: [{ strategy: 'iron_condor' }],
      avoid: [{ strategy: 'put_debit' }],
    },
  }, null);

  const result = pb.computeAdjustedScore({ score: 90, strategy: 'stock_buy' }, playbook);
  assert(result.lane === 'neutral', 'lane is neutral');
  assert(result.multiplier === 0.85, 'multiplier is 0.85 (not primary penalty)');
  assertClose(result.adjustedScore, 76.5, 0.1, '90 * 0.85 = 76.5');
}

/* ================================================================
   Test 6: computeAdjustedScore — Secondary strategy (no penalty)
   ================================================================ */
console.log('\n--- computeAdjustedScore: secondary ---');
{
  const playbook = pb.normalizePlaybook({
    playbook: {
      primary: [{ strategy: 'put_credit_spread' }],
      secondary: [{ strategy: 'iron_condor' }],
      avoid: [{ strategy: 'put_debit' }],
    },
  }, null);

  const result = pb.computeAdjustedScore({ score: 82, strategy: 'iron_condor' }, playbook);
  assert(result.lane === 'secondary', 'lane is secondary');
  assert(result.multiplier === 1.0, 'no penalty for secondary');
  assertClose(result.adjustedScore, 82, 0.01, 'adjusted == base for secondary');
}

/* ================================================================
   Test 7: Alias matching — credit_spread scanner ID matches
   ================================================================ */
console.log('\n--- Alias matching ---');
{
  const playbook = pb.normalizePlaybook({
    playbook: {
      primary: [{ strategy: 'put_credit_spread' }],
      secondary: [{ strategy: 'calendar' }],
      avoid: [{ strategy: 'aggressive_directional_debit_spreads' }],
    },
  }, null);

  // credit_spread should match put_credit_spread via aliases
  const cs = pb.computeAdjustedScore({ score: 75, strategy: 'credit_spread' }, playbook);
  assert(cs.lane === 'primary', 'credit_spread aliases to primary via put_credit_spread');

  // calendars should match calendar via aliases
  const cal = pb.computeAdjustedScore({ score: 70, strategy: 'calendars' }, playbook);
  assert(cal.lane === 'secondary', 'calendars aliases to calendar in secondary');

  // debit_spreads should match aggressive_directional_debit_spreads via aliases  
  const ds = pb.computeAdjustedScore({ score: 88, strategy: 'debit_spreads' }, playbook);
  assert(ds.lane === 'avoid', 'debit_spreads aliases to avoid lane');
}

/* ================================================================
   Test 8: sortByPlaybook — correct ordering
   ================================================================ */
console.log('\n--- sortByPlaybook ---');
{
  const playbook = pb.normalizePlaybook({
    playbook: {
      primary: [{ strategy: 'put_credit_spread' }],
      secondary: [{ strategy: 'iron_condor' }],
      avoid: [{ strategy: 'put_debit' }],
    },
  }, null);

  const opportunities = [
    { strategy: 'put_debit',         score: 95, key_metrics: { liquidity: 80 }, ror: 0.5 },
    { strategy: 'stock_buy',         score: 88, key_metrics: { liquidity: 70 }, ror: 0.3 },
    { strategy: 'iron_condor',       score: 82, key_metrics: { liquidity: 90 }, ror: 0.4 },
    { strategy: 'put_credit_spread', score: 80, key_metrics: { liquidity: 85 }, ror: 0.6 },
  ];

  const sorted = pb.sortByPlaybook(opportunities, playbook);

  // Expected order:
  // 1. iron_condor: secondary, score 82 * 1.0 = 82.0
  // 2. put_credit_spread: primary, score 80 * 1.0 = 80.0
  // 3. stock_buy: neutral, score 88 * 0.85 = 74.8
  // 4. put_debit: avoid, score 95 * 0.60 = 57.0

  assert(sorted.length === 4, '4 items returned');
  assert(sorted[0].strategy === 'iron_condor', '1st: iron_condor (82.0 adj)');
  assert(sorted[1].strategy === 'put_credit_spread', '2nd: put_credit_spread (80.0 adj)');
  assert(sorted[2].strategy === 'stock_buy', '3rd: stock_buy (74.8 adj)');
  assert(sorted[3].strategy === 'put_debit', '4th: put_debit (57.0 adj)');

  // Verify _pb metadata is attached
  assert(sorted[0]._pb.lane === 'secondary', '1st has secondary lane');
  assert(sorted[3]._pb.lane === 'avoid', '4th has avoid lane');
}

/* ================================================================
   Test 9: Tie-breaking — same adjusted score, primary beats secondary
   ================================================================ */
console.log('\n--- Tie-breaking ---');
{
  const playbook = pb.normalizePlaybook({
    playbook: {
      primary: [{ strategy: 'put_credit_spread' }],
      secondary: [{ strategy: 'iron_condor' }],
      avoid: [],
    },
  }, null);

  const opportunities = [
    { strategy: 'iron_condor',       score: 80, key_metrics: { liquidity: 95 }, ror: 0.8 },
    { strategy: 'put_credit_spread', score: 80, key_metrics: { liquidity: 85 }, ror: 0.6 },
  ];

  const sorted = pb.sortByPlaybook(opportunities, playbook);
  // Both 80 * 1.0 = 80, within TIE_EPSILON → primary wins
  assert(sorted[0].strategy === 'put_credit_spread', 'primary beats secondary in tie');
}

/* ================================================================
   Test 10: Empty playbook — no penalties applied
   ================================================================ */
console.log('\n--- Empty playbook ---');
{
  const playbook = pb.normalizePlaybook(null, null);
  const result = pb.computeAdjustedScore({ score: 75, strategy: 'anything' }, playbook);
  assert(result.multiplier === 1.0, 'no penalty when playbook is empty');
  assertClose(result.adjustedScore, 75, 0.01, 'adjusted == base with no playbook');
}

/* ================================================================
   Test 11: reasonSummary
   ================================================================ */
console.log('\n--- reasonSummary ---');
{
  const summary1 = pb.reasonSummary({ baseScore: 80, adjustedScore: 48, multiplier: 0.6, lane: 'avoid', reasons: ['Avoid strategy: -40%'] });
  assert(summary1.includes('80.0%'), 'summary shows base');
  assert(summary1.includes('48.0%'), 'summary shows adjusted');
  assert(summary1.includes('Avoid'), 'summary mentions avoid');

  const summary2 = pb.reasonSummary({ baseScore: 80, adjustedScore: 80, multiplier: 1.0, lane: 'primary', reasons: ['Primary strategy'] });
  assert(summary2 === 'Primary strategy', 'primary summary is clean');
}

/* ================================================================
   Test 12: Score clamping (0-100)
   ================================================================ */
console.log('\n--- Score clamping ---');
{
  const playbook = pb.normalizePlaybook({
    playbook: { primary: [{ strategy: 'x' }], secondary: [], avoid: [] },
  }, null);
  const r = pb.computeAdjustedScore({ score: 150, strategy: 'y' }, playbook);
  assert(r.adjustedScore <= 100, 'clamped to 100 max');
  const r2 = pb.computeAdjustedScore({ score: -10, strategy: 'y' }, playbook);
  assert(r2.adjustedScore >= 0, 'clamped to 0 min');
}

/* ── Summary ── */
console.log(`\n========================================`);
console.log(`  ${passed} passed, ${failed} failed`);
console.log(`========================================\n`);
process.exit(failed > 0 ? 1 : 0);
