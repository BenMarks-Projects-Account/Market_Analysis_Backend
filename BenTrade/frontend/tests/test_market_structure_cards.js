/**
 * Tests for Market Structure index cards — correction-state classification.
 *
 * Run with:
 *   cd BenTrade/frontend
 *   node tests/test_market_structure_cards.js
 */

'use strict';

var passed = 0;
var failed = 0;

function assert(cond, msg){
  if(!cond){
    failed++;
    console.error('FAIL:', msg);
  } else {
    passed++;
  }
}

function assertEqual(actual, expected, msg){
  if(actual !== expected){
    failed++;
    console.error('FAIL:', msg, '— expected:', expected, 'got:', actual);
  } else {
    passed++;
  }
}

/* ── Mirror of classifyDrawdownState from home.js ── */
function classifyDrawdownState(last, high52w){
  if(last === null || high52w === null || high52w <= 0) return null;
  var drawdownPct = ((last - high52w) / high52w) * 100;
  var label, tone;
  if(drawdownPct >= -4.9){
    label = 'Near High'; tone = 'bullish';
  } else if(drawdownPct >= -9.9){
    label = 'Pullback'; tone = 'neutral';
  } else if(drawdownPct >= -19.9){
    label = 'Correction'; tone = 'riskoff';
  } else {
    label = 'Bear Market'; tone = 'bearish';
  }
  return { drawdownPct: drawdownPct, label: label, tone: tone };
}

/* ── INDEX_META completeness ── */
var INDEX_SYMBOLS = ['SPY', 'QQQ', 'IWM', 'DIA', 'IWB', 'MDY'];
var INDEX_META = {
  SPY: { name: 'S&P 500', descriptor: 'Large-cap benchmark' },
  DIA: { name: 'Dow Jones', descriptor: 'Blue-chip price leadership' },
  QQQ: { name: 'Nasdaq Composite', descriptor: 'Growth-heavy market barometer' },
  IWM: { name: 'Russell 2000', descriptor: 'Small-cap risk appetite gauge' },
  IWB: { name: 'Russell 1000', descriptor: 'Large/mid-cap breadth proxy' },
  MDY: { name: 'S&P MidCap 400', descriptor: 'Mid-cap domestic cycle read' },
};

/* ── Tests ── */

// Near High
(function(){
  var r = classifyDrawdownState(540, 545);
  assert(r !== null, 'NearHigh: result not null');
  assertEqual(r.label, 'Near High', 'NearHigh: label');
  assertEqual(r.tone, 'bullish', 'NearHigh: tone');
})();

// At High
(function(){
  var r = classifyDrawdownState(545, 545);
  assertEqual(r.label, 'Near High', 'AtHigh: label');
  assertEqual(r.drawdownPct, 0, 'AtHigh: drawdown is 0');
})();

// Pullback -5%
(function(){
  var r = classifyDrawdownState(95, 100);
  assertEqual(r.label, 'Pullback', 'Pullback: -5% label');
  assertEqual(r.tone, 'neutral', 'Pullback: tone');
})();

// Correction -10%
(function(){
  var r = classifyDrawdownState(90, 100);
  assertEqual(r.label, 'Correction', 'Correction: -10% label');
  assertEqual(r.tone, 'riskoff', 'Correction: tone');
})();

// Bear Market -20%
(function(){
  var r = classifyDrawdownState(80, 100);
  assertEqual(r.label, 'Bear Market', 'Bear: -20% label');
  assertEqual(r.tone, 'bearish', 'Bear: tone');
})();

// Null inputs
(function(){
  assertEqual(classifyDrawdownState(null, 545), null, 'Null last');
  assertEqual(classifyDrawdownState(540, null), null, 'Null high');
  assertEqual(classifyDrawdownState(540, 0), null, 'Zero high');
})();

// Above high (new high just set)
(function(){
  var r = classifyDrawdownState(550, 545);
  assertEqual(r.label, 'Near High', 'AboveHigh: label');
  assert(r.drawdownPct > 0, 'AboveHigh: positive drawdown');
})();

// INDEX_META completeness
(function(){
  INDEX_SYMBOLS.forEach(function(sym){
    var meta = INDEX_META[sym];
    assert(meta, 'INDEX_META exists for ' + sym);
    assert(meta.name, 'INDEX_META.name for ' + sym);
    assert(meta.descriptor, 'INDEX_META.descriptor for ' + sym);
  });
})();

// All 6 symbols present
assertEqual(INDEX_SYMBOLS.length, 6, 'INDEX_SYMBOLS has 6 entries');

/* ── Summary ── */
console.log('\n' + passed + ' passed, ' + failed + ' failed');
if(failed > 0) process.exit(1);
