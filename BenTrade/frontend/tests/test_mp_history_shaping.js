/**
 * Tests for Market Picture History shaping logic.
 *
 * Run with:
 *   cd BenTrade/frontend
 *   node tests/test_mp_history_shaping.js
 *
 * Tests the _shapeHistoryEngineSeries logic extracted here for testability.
 */

'use strict';

/* ── Replicate the constants from home.js ── */
var ENGINE_HISTORY_SERIES = [
  { key: 'breadth_participation',   label: 'Breadth & Participation',      color: 'rgba(0,234,255,0.9)'   },
  { key: 'volatility_options',      label: 'Volatility & Options',         color: 'rgba(255,199,88,0.9)'  },
  { key: 'cross_asset_macro',       label: 'Cross-Asset Macro',            color: 'rgba(126,247,184,0.9)' },
  { key: 'flows_positioning',       label: 'Flows & Positioning',          color: 'rgba(255,79,102,0.9)'  },
  { key: 'liquidity_conditions',    label: 'Liquidity & Financial Conds',  color: 'rgba(181,126,255,0.9)' },
  { key: 'news_sentiment',          label: 'News & Sentiment',             color: 'rgba(255,156,68,0.9)'  },
];

/**
 * _shapeHistoryEngineSeries — exact copy from home.js for testability.
 *
 * Plotted-score rule:
 *   avg(engine_score, model_score) if BOTH are numbers
 *   engine_score alone if model_score is null/missing
 *   null if engine_score is also null
 */
function _shapeHistoryEngineSeries(entries, daysBack){
  daysBack = daysBack || 14;
  var now = Date.now();
  var cutoff = now - daysBack * 86400000;

  var filtered = [];
  for(var i = 0; i < entries.length; i++){
    var e = entries[i];
    var ts = e.captured_at ? new Date(e.captured_at).getTime() : 0;
    if(ts >= cutoff && ts <= now) filtered.push(e);
  }
  filtered.sort(function(a, b){
    return new Date(a.captured_at).getTime() - new Date(b.captured_at).getTime();
  });

  var series = [];
  for(var s = 0; s < ENGINE_HISTORY_SERIES.length; s++){
    var def = ENGINE_HISTORY_SERIES[s];
    var points = [];
    for(var j = 0; j < filtered.length; j++){
      var snap = filtered[j];
      var engines = snap.engines || [];
      var eng = null;
      for(var k = 0; k < engines.length; k++){
        if(engines[k].key === def.key){ eng = engines[k]; break; }
      }
      var eScore = eng ? (typeof eng.engine_score === 'number' ? eng.engine_score : null) : null;
      var mScore = eng ? (typeof eng.model_score === 'number' ? eng.model_score : null) : null;

      var plotted;
      if(eScore !== null && mScore !== null){
        plotted = (eScore + mScore) / 2;
      } else if(eScore !== null){
        plotted = eScore;
      } else {
        plotted = null;
      }

      points.push({
        ts: new Date(snap.captured_at).getTime(),
        plotted_score: plotted,
        engine_score: eScore,
        model_score: mScore,
        had_model: mScore !== null,
      });
    }
    series.push({
      key: def.key,
      label: def.label,
      color: def.color,
      points: points,
    });
  }

  var hasEnoughPoints = filtered.length >= 2;

  // ── Regime bands: contiguous time spans sharing the same regime label ──
  var regimeBands = [];
  var curBand = null;
  for(var ri = 0; ri < filtered.length; ri++){
    var rSnap = filtered[ri];
    var rTs = new Date(rSnap.captured_at).getTime();
    var rLabel = String(rSnap.consumer_regime_label || rSnap.regime_state || 'NEUTRAL').toUpperCase();
    if(rLabel !== 'RISK_ON' && rLabel !== 'RISK_OFF') rLabel = 'NEUTRAL';

    if(!curBand || curBand.regime !== rLabel){
      if(curBand) curBand.tEnd = rTs;
      curBand = { tStart: rTs, tEnd: rTs, regime: rLabel };
      regimeBands.push(curBand);
    } else {
      curBand.tEnd = rTs;
    }
  }

  // ── Posture change markers ──
  function _derivePosture(regimeLabel, regimeScore){
    if(regimeLabel === 'RISK_ON') return regimeScore >= 75 ? 'aggressive' : 'constructive';
    if(regimeLabel === 'RISK_OFF') return 'defensive';
    return 'selective';
  }
  var postureMarkers = [];
  var prevStockPosture = null;
  var prevOptionsPosture = null;
  for(var pi2 = 0; pi2 < filtered.length; pi2++){
    var pSnap = filtered[pi2];
    var pTs = new Date(pSnap.captured_at).getTime();
    var pLabel = String(pSnap.consumer_regime_label || pSnap.regime_state || 'NEUTRAL').toUpperCase();
    if(pLabel !== 'RISK_ON' && pLabel !== 'RISK_OFF') pLabel = 'NEUTRAL';
    var pScore = typeof pSnap.consumer_regime_score === 'number' ? pSnap.consumer_regime_score : 50;
    var stockP = _derivePosture(pLabel, pScore);
    var optionsP = _derivePosture(pLabel, pScore);
    if(stockP !== prevStockPosture || optionsP !== prevOptionsPosture){
      postureMarkers.push({ ts: pTs, stock: stockP, options: optionsP });
      prevStockPosture = stockP;
      prevOptionsPosture = optionsP;
    }
  }

  return { series: series, regimeBands: regimeBands, postureMarkers: postureMarkers, tooFew: !hasEnoughPoints };
}


/* ═══════════════════════════════════════════════════════════════
 * TEST HELPERS
 * ═══════════════════════════════════════════════════════════════ */

var _pass = 0;
var _fail = 0;

function assert(cond, msg){
  if(cond){ _pass++; }
  else{ _fail++; console.error('  FAIL:', msg); }
}

function assertEqual(actual, expected, msg){
  if(actual === expected){ _pass++; }
  else{ _fail++; console.error('  FAIL:', msg, '| expected:', expected, '| got:', actual); }
}

function assertClose(actual, expected, eps, msg){
  if(Math.abs(actual - expected) < (eps || 0.01)){ _pass++; }
  else{ _fail++; console.error('  FAIL:', msg, '| expected:', expected, '| got:', actual); }
}

function makeSnap(hoursAgo, enginesArr, regimeLabel, regimeScore){
  var ts = new Date(Date.now() - hoursAgo * 3600000).toISOString();
  return {
    captured_at: ts,
    consumer_regime_label: regimeLabel || 'NEUTRAL',
    consumer_regime_score: typeof regimeScore === 'number' ? regimeScore : 50,
    engines: enginesArr || ENGINE_HISTORY_SERIES.map(function(def){
      return { key: def.key, engine_score: 60, model_score: null };
    }),
  };
}


/* ═══════════════════════════════════════════════════════════════
 * TESTS
 * ═══════════════════════════════════════════════════════════════ */

console.log('Test: 2-week filtering');
(function(){
  var entries = [
    makeSnap(24 * 20),   // 20 days ago — outside window
    makeSnap(24 * 10),   // 10 days ago — inside window
    makeSnap(24 * 3),    // 3 days ago — inside window
    makeSnap(1),          // 1 hour ago — inside window
  ];
  var result = _shapeHistoryEngineSeries(entries, 14);
  assertEqual(result.tooFew, false, 'should not be tooFew with 3 entries in window');
  // Each engine series should have 3 points (the 20-day-ago snap is filtered out)
  assertEqual(result.series[0].points.length, 3, 'breadth should have 3 points');
  assertEqual(result.series[5].points.length, 3, 'news should have 3 points');
})();

console.log('Test: tooFew when < 2 entries');
(function(){
  var entries = [makeSnap(1)];
  var result = _shapeHistoryEngineSeries(entries, 14);
  assertEqual(result.tooFew, true, 'should be tooFew with only 1 entry');
})();

console.log('Test: tooFew when 0 entries');
(function(){
  var result = _shapeHistoryEngineSeries([], 14);
  assertEqual(result.tooFew, true, 'should be tooFew with 0 entries');
})();

console.log('Test: plotted_score uses engine_score when model_score is null');
(function(){
  var entries = [
    makeSnap(48, [{ key: 'breadth_participation', engine_score: 72, model_score: null }]),
    makeSnap(24, [{ key: 'breadth_participation', engine_score: 65, model_score: null }]),
  ];
  var result = _shapeHistoryEngineSeries(entries, 14);
  var bp = result.series[0]; // breadth_participation
  assertEqual(bp.points[0].plotted_score, 72, 'plotted should equal engine_score when model null');
  assertEqual(bp.points[0].had_model, false, 'had_model should be false');
  assertEqual(bp.points[1].plotted_score, 65, 'second point plotted should equal engine_score');
})();

console.log('Test: plotted_score averages engine + model when both present');
(function(){
  var entries = [
    makeSnap(48, [{ key: 'breadth_participation', engine_score: 70, model_score: 80 }]),
    makeSnap(24, [{ key: 'breadth_participation', engine_score: 60, model_score: 40 }]),
  ];
  var result = _shapeHistoryEngineSeries(entries, 14);
  var bp = result.series[0];
  assertClose(bp.points[0].plotted_score, 75, 0.01, 'avg(70,80) should be 75');
  assertEqual(bp.points[0].had_model, true, 'had_model should be true');
  assertClose(bp.points[1].plotted_score, 50, 0.01, 'avg(60,40) should be 50');
})();

console.log('Test: plotted_score null when both engine and model are null');
(function(){
  var entries = [
    makeSnap(48, [{ key: 'breadth_participation', engine_score: null, model_score: null }]),
    makeSnap(24, [{ key: 'breadth_participation', engine_score: null, model_score: null }]),
  ];
  var result = _shapeHistoryEngineSeries(entries, 14);
  var bp = result.series[0];
  assertEqual(bp.points[0].plotted_score, null, 'plotted should be null when both null');
})();

console.log('Test: six series always returned');
(function(){
  var entries = [makeSnap(48), makeSnap(24)];
  var result = _shapeHistoryEngineSeries(entries, 14);
  assertEqual(result.series.length, 6, 'should always have 6 series');
  assertEqual(result.series[0].key, 'breadth_participation', 'first key');
  assertEqual(result.series[1].key, 'volatility_options', 'second key');
  assertEqual(result.series[2].key, 'cross_asset_macro', 'third key');
  assertEqual(result.series[3].key, 'flows_positioning', 'fourth key');
  assertEqual(result.series[4].key, 'liquidity_conditions', 'fifth key');
  assertEqual(result.series[5].key, 'news_sentiment', 'sixth key');
})();

console.log('Test: chronological order (ascending by timestamp)');
(function(){
  // Feed entries in reverse order
  var entries = [makeSnap(1), makeSnap(48), makeSnap(24)];
  var result = _shapeHistoryEngineSeries(entries, 14);
  var pts = result.series[0].points;
  assert(pts[0].ts < pts[1].ts, 'first point should be earliest');
  assert(pts[1].ts < pts[2].ts, 'second point should be before third');
})();

console.log('Test: missing engine for a key produces null plotted_score');
(function(){
  // Only include breadth_participation, omit all others
  var entries = [
    makeSnap(48, [{ key: 'breadth_participation', engine_score: 70, model_score: null }]),
    makeSnap(24, [{ key: 'breadth_participation', engine_score: 65, model_score: null }]),
  ];
  var result = _shapeHistoryEngineSeries(entries, 14);
  // volatility_options (index 1) has no data
  var vol = result.series[1];
  assertEqual(vol.points[0].plotted_score, null, 'missing engine should produce null');
  assertEqual(vol.points[0].engine_score, null, 'missing engine_score should be null');
})();


/* ═══════════════════════════════════════════════════════════════
 * REGIME BAND TESTS
 * ═══════════════════════════════════════════════════════════════ */

console.log('Test: single regime produces one band');
(function(){
  var entries = [
    makeSnap(48, null, 'RISK_ON', 80),
    makeSnap(24, null, 'RISK_ON', 82),
    makeSnap(1,  null, 'RISK_ON', 78),
  ];
  var result = _shapeHistoryEngineSeries(entries, 14);
  assertEqual(result.regimeBands.length, 1, 'should have 1 band');
  assertEqual(result.regimeBands[0].regime, 'RISK_ON', 'band should be RISK_ON');
})();

console.log('Test: regime change produces multiple bands');
(function(){
  var entries = [
    makeSnap(72, null, 'RISK_ON', 80),
    makeSnap(48, null, 'RISK_ON', 78),
    makeSnap(24, null, 'NEUTRAL', 52),
    makeSnap(12, null, 'NEUTRAL', 50),
    makeSnap(1,  null, 'RISK_OFF', 30),
  ];
  var result = _shapeHistoryEngineSeries(entries, 14);
  assertEqual(result.regimeBands.length, 3, 'should have 3 bands');
  assertEqual(result.regimeBands[0].regime, 'RISK_ON', 'first band');
  assertEqual(result.regimeBands[1].regime, 'NEUTRAL', 'second band');
  assertEqual(result.regimeBands[2].regime, 'RISK_OFF', 'third band');
})();

console.log('Test: unknown regime labels normalize to NEUTRAL');
(function(){
  var entries = [
    makeSnap(48, null, 'MIXED', 55),
    makeSnap(24, null, 'BULLISH', 65),
  ];
  var result = _shapeHistoryEngineSeries(entries, 14);
  assertEqual(result.regimeBands.length, 1, 'unknown labels → single NEUTRAL band');
  assertEqual(result.regimeBands[0].regime, 'NEUTRAL', 'should normalize to NEUTRAL');
})();

console.log('Test: empty entries produce no bands');
(function(){
  var result = _shapeHistoryEngineSeries([], 14);
  assertEqual(result.regimeBands.length, 0, 'no bands with no entries');
})();

/* ═══════════════════════════════════════════════════════════════
 * POSTURE MARKER TESTS
 * ═══════════════════════════════════════════════════════════════ */

console.log('Test: constant regime produces single posture marker (initial)');
(function(){
  var entries = [
    makeSnap(48, null, 'RISK_ON', 80),
    makeSnap(24, null, 'RISK_ON', 82),
  ];
  var result = _shapeHistoryEngineSeries(entries, 14);
  assertEqual(result.postureMarkers.length, 1, 'should have 1 marker (initial state)');
  assertEqual(result.postureMarkers[0].stock, 'aggressive', 'RISK_ON+80 → aggressive');
  assertEqual(result.postureMarkers[0].options, 'aggressive', 'options also aggressive');
})();

console.log('Test: posture change from aggressive to selective');
(function(){
  var entries = [
    makeSnap(72, null, 'RISK_ON', 80),
    makeSnap(48, null, 'RISK_ON', 78),
    makeSnap(24, null, 'NEUTRAL', 52),
  ];
  var result = _shapeHistoryEngineSeries(entries, 14);
  assertEqual(result.postureMarkers.length, 2, 'should have 2 markers');
  assertEqual(result.postureMarkers[0].stock, 'aggressive', 'initial: aggressive');
  assertEqual(result.postureMarkers[1].stock, 'selective', 'change: selective');
})();

console.log('Test: RISK_ON score < 75 → constructive posture');
(function(){
  var entries = [
    makeSnap(48, null, 'RISK_ON', 65),
    makeSnap(24, null, 'RISK_ON', 60),
  ];
  var result = _shapeHistoryEngineSeries(entries, 14);
  assertEqual(result.postureMarkers.length, 1, 'single marker');
  assertEqual(result.postureMarkers[0].stock, 'constructive', 'RISK_ON+65 → constructive');
})();

console.log('Test: RISK_OFF → defensive posture');
(function(){
  var entries = [
    makeSnap(48, null, 'RISK_OFF', 25),
    makeSnap(24, null, 'RISK_OFF', 20),
  ];
  var result = _shapeHistoryEngineSeries(entries, 14);
  assertEqual(result.postureMarkers[0].stock, 'defensive', 'RISK_OFF → defensive');
})();

console.log('Test: aggressive → constructive within RISK_ON (score drop)');
(function(){
  var entries = [
    makeSnap(72, null, 'RISK_ON', 80),
    makeSnap(48, null, 'RISK_ON', 70),  // drops below 75
  ];
  var result = _shapeHistoryEngineSeries(entries, 14);
  assertEqual(result.postureMarkers.length, 2, 'score drop creates new marker');
  assertEqual(result.postureMarkers[0].stock, 'aggressive', 'initial: aggressive');
  assertEqual(result.postureMarkers[1].stock, 'constructive', 'change: constructive');
})();

console.log('Test: full posture cycle: aggressive → selective → defensive → constructive');
(function(){
  var entries = [
    makeSnap(96, null, 'RISK_ON', 80),
    makeSnap(72, null, 'NEUTRAL', 50),
    makeSnap(48, null, 'RISK_OFF', 25),
    makeSnap(24, null, 'RISK_ON', 65),
  ];
  var result = _shapeHistoryEngineSeries(entries, 14);
  assertEqual(result.postureMarkers.length, 4, 'should have 4 markers');
  assertEqual(result.postureMarkers[0].stock, 'aggressive', 'start: aggressive');
  assertEqual(result.postureMarkers[1].stock, 'selective', 'to selective');
  assertEqual(result.postureMarkers[2].stock, 'defensive', 'to defensive');
  assertEqual(result.postureMarkers[3].stock, 'constructive', 'to constructive');
})();

/* ═══════════════════════════════════════════════════════════════
 * SUMMARY
 * ═══════════════════════════════════════════════════════════════ */

console.log('\n' + (_pass + _fail) + ' assertions: ' + _pass + ' passed, ' + _fail + ' failed');
if(_fail > 0) process.exit(1);
else console.log('All tests passed.');
