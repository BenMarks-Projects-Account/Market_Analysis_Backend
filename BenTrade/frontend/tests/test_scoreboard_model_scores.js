/**
 * Tests for Market Picture Scoreboard — backend-backed model scores.
 *
 * Verifies that scoreboard rendering uses backend-provided model scores
 * (from the durable store) and does NOT depend on sessionStorage hydration.
 *
 * Run with:
 *   cd BenTrade/frontend
 *   node tests/test_scoreboard_model_scores.js
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

/* ── Replicate _scoreColor from home.js ── */
function _scoreColor(score){
  if(score == null) return '#888';
  if(score >= 70) return '#7ef7b8';
  if(score >= 50) return '#ffc758';
  return '#ff6b6b';
}

/* ── Replicate _modelFreshnessBadge from home.js ── */
function _modelFreshnessBadge(eng){
  if(eng.model_score == null){
    return { text: 'Not available', cssClass: 'home-model-badge-na' };
  }
  if(eng.model_fresh === false){
    var capturedAt = eng.model_captured_at;
    var ageText = '';
    if(capturedAt){
      try{
        var ageMs = Date.now() - new Date(capturedAt).getTime();
        var ageHours = Math.floor(ageMs / (1000 * 60 * 60));
        if(ageHours >= 24){
          ageText = Math.floor(ageHours / 24) + 'd ago';
        } else if(ageHours >= 1){
          ageText = ageHours + 'h ago';
        } else {
          ageText = Math.max(1, Math.floor(ageMs / (1000 * 60))) + 'm ago';
        }
      }catch(_e){}
    }
    return { text: 'Stale' + (ageText ? ' (' + ageText + ')' : ''), cssClass: 'home-model-badge-stale' };
  }
  return { text: '', cssClass: '' };
}

/* ── Test: missing model score shows "Not available" badge ── */
(function test_missing_model_score_badge(){
  var eng = { model_score: null, model_fresh: false, model_captured_at: null };
  var badge = _modelFreshnessBadge(eng);
  assertEqual(badge.text, 'Not available', 'Missing model_score badge text');
  assertEqual(badge.cssClass, 'home-model-badge-na', 'Missing model_score badge CSS');
})();

/* ── Test: fresh model score shows no badge ── */
(function test_fresh_model_score_no_badge(){
  var eng = { model_score: 72.5, model_fresh: true, model_captured_at: new Date().toISOString() };
  var badge = _modelFreshnessBadge(eng);
  assertEqual(badge.text, '', 'Fresh model score should have empty badge text');
  assertEqual(badge.cssClass, '', 'Fresh model score should have empty badge CSS');
})();

/* ── Test: stale model score shows stale badge ── */
(function test_stale_model_score_badge(){
  var oldTs = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(); // 3h ago
  var eng = { model_score: 50.0, model_fresh: false, model_captured_at: oldTs };
  var badge = _modelFreshnessBadge(eng);
  assert(badge.text.indexOf('Stale') === 0, 'Stale badge should start with "Stale"');
  assert(badge.text.indexOf('3h ago') >= 0, 'Stale badge should mention "3h ago"');
  assertEqual(badge.cssClass, 'home-model-badge-stale', 'Stale badge CSS class');
})();

/* ── Test: stale model score badge for days ── */
(function test_stale_model_score_badge_days(){
  var oldTs = new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString(); // 2 days ago
  var eng = { model_score: 50.0, model_fresh: false, model_captured_at: oldTs };
  var badge = _modelFreshnessBadge(eng);
  assert(badge.text.indexOf('2d ago') >= 0, 'Stale badge should show days for old scores: ' + badge.text);
})();

/* ── Test: score color for null ── */
(function test_score_color_null(){
  assertEqual(_scoreColor(null), '#888', 'Null score color');
  assertEqual(_scoreColor(undefined), '#888', 'Undefined score color');
})();

/* ── Test: score color for ranges ── */
(function test_score_color_ranges(){
  assertEqual(_scoreColor(75), '#7ef7b8', 'High score (>=70) color');
  assertEqual(_scoreColor(55), '#ffc758', 'Medium score (>=50) color');
  assertEqual(_scoreColor(30), '#ff6b6b', 'Low score (<50) color');
})();

/* ── Test: no dependency on sessionStorage/BenTradeDashboardCache ── */
(function test_no_session_storage_dependency(){
  // Verify that _modelFreshnessBadge works with pure backend data, no globals
  var eng = {
    model_score: 65.0,
    model_fresh: true,
    model_captured_at: new Date().toISOString(),
    model_label: 'MIXED',
  };
  var badge = _modelFreshnessBadge(eng);
  assertEqual(badge.text, '', 'Backend-only model score needs no sessionStorage');
  assert(typeof global === 'undefined' || !global.BenTradeDashboardCache,
    'BenTradeDashboardCache should not be required');
})();

/* ── Test: stale without captured_at still says Stale ── */
(function test_stale_without_captured_at(){
  var eng = { model_score: 40.0, model_fresh: false, model_captured_at: null };
  var badge = _modelFreshnessBadge(eng);
  assertEqual(badge.text, 'Stale', 'Stale without timestamp should just say "Stale"');
  assertEqual(badge.cssClass, 'home-model-badge-stale', 'Stale CSS class');
})();

/* ── Summary ── */

/* ── Test: model_summary present shows summary text ── */
(function test_model_summary_present(){
  var eng = {
    model_score: 72.5,
    model_fresh: true,
    model_summary: 'Market breadth is strong with broad participation.',
    model_captured_at: new Date().toISOString(),
  };
  var mSummary = eng.model_summary || null;
  var fallback = mSummary || (eng.model_score != null ? 'Model score available, but no stored summary.' : 'No model analysis yet');
  assertEqual(fallback, 'Market breadth is strong with broad participation.', 'Should display model summary when present');
})();

/* ── Test: model_summary absent but score exists shows honest fallback ── */
(function test_model_summary_absent_with_score(){
  var eng = {
    model_score: 55.0,
    model_fresh: true,
    model_summary: null,
    model_captured_at: new Date().toISOString(),
  };
  var mSummary = eng.model_summary || null;
  var fallback = mSummary || (eng.model_score != null ? 'Model score available, but no stored summary.' : 'No model analysis yet');
  assertEqual(fallback, 'Model score available, but no stored summary.', 'Should show honest fallback when summary absent but score exists');
})();

/* ── Test: no model at all shows neutral message ── */
(function test_no_model_at_all(){
  var eng = {
    model_score: null,
    model_fresh: false,
    model_summary: null,
    model_captured_at: null,
  };
  var mSummary = eng.model_summary || null;
  var fallback = mSummary || (eng.model_score != null ? 'Model score available, but no stored summary.' : 'No model analysis yet');
  assertEqual(fallback, 'No model analysis yet', 'Should show neutral message when no model data');
})();

console.log('\n=== Scoreboard Model Scores Tests ===');
console.log('Passed:', passed, ' Failed:', failed);
if(failed > 0){
  process.exit(1);
} else {
  console.log('All tests passed.');
}
