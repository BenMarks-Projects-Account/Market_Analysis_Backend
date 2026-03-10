/**
 * BenTrade Dashboard Cache — Unit Tests
 *
 * Run in browser console or include after dashboardCache.js loads.
 * Usage: paste/load this file, then call runDashboardCacheTests().
 *
 * Test scenarios:
 *   1. First load populates cache
 *   2. Route unmount/remount reuses cached data (getData returns payload)
 *   3. Manual refresh success overwrites cache
 *   4. Manual refresh failure preserves old cache
 *   5. Invalid refresh payload does not overwrite valid cache (setSafe)
 *   6. No-cache initial load failure shows error state
 *   7. Helper selectors return correct values
 *   8. Deterministic data cache survives even if model key is absent
 */
(function(global) {
  'use strict';

  var _passed = 0;
  var _failed = 0;
  var _errors = [];

  function assert(condition, msg) {
    if (condition) {
      _passed++;
    } else {
      _failed++;
      _errors.push('FAIL: ' + msg);
      console.error('FAIL:', msg);
    }
  }

  function assertEqual(actual, expected, msg) {
    if (actual === expected) {
      _passed++;
    } else {
      _failed++;
      var detail = msg + ' — expected ' + JSON.stringify(expected) + ', got ' + JSON.stringify(actual);
      _errors.push('FAIL: ' + detail);
      console.error('FAIL:', detail);
    }
  }

  function assertNotNull(val, msg) {
    assert(val != null, msg + ' — should not be null/undefined');
  }

  function setup() {
    // Clear any prior state
    var cache = global.BenTradeDashboardCache;
    if (!cache) throw new Error('BenTradeDashboardCache not loaded');
    cache.clearAll();
    return cache;
  }

  // ── Test 1: First load populates cache ────────────────────────

  function test_first_load_populates_cache() {
    var cache = setup();
    var KEY = 'test_breadth';
    var payload = { engine_result: { score: 72, label: 'Constructive' } };

    // Before set
    assertEqual(cache.hasCache(KEY), false, 'T1: no cache before set');
    assertEqual(cache.getStatus(KEY), 'idle', 'T1: status idle before set');
    assertEqual(cache.getData(KEY), null, 'T1: getData null before set');

    // Set
    cache.set(KEY, payload);

    // After set
    assertEqual(cache.hasCache(KEY), true, 'T1: cache exists after set');
    assertEqual(cache.getStatus(KEY), 'ready', 'T1: status ready after set');
    assertNotNull(cache.getData(KEY), 'T1: getData not null');
    assertEqual(cache.getData(KEY).engine_result.score, 72, 'T1: payload score preserved');
    assertNotNull(cache.getLastUpdated(KEY), 'T1: lastUpdated set');
    console.log('  ✓ Test 1: First load populates cache');
  }

  // ── Test 2: Route remount reuses cached data ──────────────────

  function test_route_remount_reuses_cache() {
    var cache = setup();
    var KEY = 'test_breadth';
    var payload = { engine_result: { score: 65, label: 'Neutral' } };

    // Simulate first load
    cache.set(KEY, payload);

    // Simulate route unmount (cache persists — just clear local vars)
    // Simulate route remount
    var data = cache.getData(KEY);
    assertNotNull(data, 'T2: getData returns data after remount');
    assertEqual(data.engine_result.score, 65, 'T2: score preserved across route change');
    assertEqual(data.engine_result.label, 'Neutral', 'T2: label preserved');
    assertEqual(cache.shouldShowCachedData(KEY), true, 'T2: shouldShowCachedData true');
    assertEqual(cache.shouldShowLoader(KEY), false, 'T2: shouldShowLoader false when cached');
    console.log('  ✓ Test 2: Route remount reuses cached data');
  }

  // ── Test 3: Manual refresh success overwrites cache ───────────

  function test_refresh_success_overwrites() {
    var cache = setup();
    var KEY = 'test_breadth';
    var original = { engine_result: { score: 60, label: 'Neutral' } };
    var refreshed = { engine_result: { score: 78, label: 'Strong' } };

    cache.set(KEY, original);
    assertEqual(cache.getData(KEY).engine_result.score, 60, 'T3: original score');

    // Simulate successful refresh
    cache.set(KEY, refreshed);
    assertEqual(cache.getData(KEY).engine_result.score, 78, 'T3: refreshed score');
    assertEqual(cache.getData(KEY).engine_result.label, 'Strong', 'T3: refreshed label');
    assertEqual(cache.getStatus(KEY), 'ready', 'T3: status ready after refresh');
    console.log('  ✓ Test 3: Refresh success overwrites cache');
  }

  // ── Test 4: Refresh failure preserves old cache ───────────────

  function test_refresh_failure_preserves_cache() {
    var cache = setup();
    var KEY = 'test_breadth';
    var good = { engine_result: { score: 70, label: 'Constructive' } };

    cache.set(KEY, good);

    // Simulate failed refresh
    cache.setError(KEY, 'Network timeout');

    // Data should still be there
    assertNotNull(cache.getData(KEY), 'T4: data preserved after error');
    assertEqual(cache.getData(KEY).engine_result.score, 70, 'T4: score preserved');
    assertEqual(cache.hasCache(KEY), true, 'T4: hasCache still true');
    // Error recorded
    var entry = cache.get(KEY);
    assertEqual(entry.lastError, 'Network timeout', 'T4: error recorded');
    console.log('  ✓ Test 4: Refresh failure preserves old cache');
  }

  // ── Test 5: Invalid payload does not overwrite via setSafe ────

  function test_invalid_payload_rejected() {
    var cache = setup();
    var KEY = 'test_breadth';
    var valid = { engine_result: { score: 72, label: 'Constructive' } };

    cache.set(KEY, valid);

    // Try to overwrite with invalid payload (missing engine_result.score)
    var wrote = cache.setSafe(KEY, { engine_result: {} }, ['engine_result.score']);
    assertEqual(wrote, false, 'T5: setSafe rejects invalid payload');
    assertEqual(cache.getData(KEY).engine_result.score, 72, 'T5: original data preserved');

    // Try null
    wrote = cache.setSafe(KEY, null, ['engine_result']);
    assertEqual(wrote, false, 'T5: setSafe rejects null');
    assertEqual(cache.getData(KEY).engine_result.score, 72, 'T5: data still preserved after null');

    // Valid payload should succeed
    wrote = cache.setSafe(KEY, { engine_result: { score: 80 } }, ['engine_result.score']);
    assertEqual(wrote, true, 'T5: setSafe accepts valid payload');
    assertEqual(cache.getData(KEY).engine_result.score, 80, 'T5: new data written');
    console.log('  ✓ Test 5: Invalid payload rejected by setSafe');
  }

  // ── Test 6: No-cache shows correct status ─────────────────────

  function test_no_cache_status() {
    var cache = setup();
    var KEY = 'test_empty';

    assertEqual(cache.getStatus(KEY), 'idle', 'T6: no-cache status is idle');
    assertEqual(cache.shouldShowLoader(KEY), true, 'T6: shouldShowLoader when no cache');
    assertEqual(cache.shouldShowCachedData(KEY), false, 'T6: shouldShowCachedData false');
    assertEqual(cache.shouldShowRefreshOverlay(KEY), false, 'T6: no overlay when no cache');

    // Set error without prior data
    cache.setError(KEY, 'Server down');
    assertEqual(cache.getStatus(KEY), 'error', 'T6: error status with no data');
    assertEqual(cache.shouldShowLoader(KEY), false, 'T6: no loader on error');
    console.log('  ✓ Test 6: No-cache initial state correct');
  }

  // ── Test 7: Helper selectors ──────────────────────────────────

  function test_helper_selectors() {
    var cache = setup();
    var KEY = 'test_helpers';
    var payload = { engine_result: { score: 55 } };

    cache.set(KEY, payload);

    // Status snapshot
    var snap = cache.getStatusSnapshot(KEY);
    assertEqual(snap.dashboard_key, KEY, 'T7: snapshot key');
    assertEqual(snap.status, 'ready', 'T7: snapshot status');
    assertEqual(snap.has_loaded_once, true, 'T7: snapshot loaded');
    assertNotNull(snap.last_loaded_at, 'T7: snapshot timestamp');
    assertEqual(snap.source, 'cache', 'T7: snapshot source');

    // Refreshing state
    cache.setRefreshing(KEY, true);
    assertEqual(cache.isRefreshing(KEY), true, 'T7: isRefreshing true');
    assertEqual(cache.getStatus(KEY), 'refreshing', 'T7: status refreshing');
    assertEqual(cache.shouldShowRefreshOverlay(KEY), true, 'T7: overlay when refreshing with cache');

    var snap2 = cache.getStatusSnapshot(KEY);
    assertEqual(snap2.source, 'network', 'T7: source is network when refreshing');

    cache.setRefreshing(KEY, false);
    assertEqual(cache.isRefreshing(KEY), false, 'T7: isRefreshing false after clear');

    // Validate payload helper
    assertEqual(cache.validatePayload({ a: { b: 1 } }, ['a.b']), true, 'T7: valid nested path');
    assertEqual(cache.validatePayload({ a: {} }, ['a.b']), false, 'T7: missing nested path');
    assertEqual(cache.validatePayload(null, ['a']), false, 'T7: null payload');
    assertEqual(cache.validatePayload({}, []), true, 'T7: no required fields');
    console.log('  ✓ Test 7: Helper selectors correct');
  }

  // ── Test 8: Engine and model cache independent ────────────────

  function test_engine_model_independent() {
    var cache = setup();
    var ENGINE_KEY = 'test_breadth_engine';
    var MODEL_KEY = 'test_breadth_model';

    var engineData = { engine_result: { score: 68 } };
    cache.set(ENGINE_KEY, engineData);

    // Model not set
    assertEqual(cache.hasCache(ENGINE_KEY), true, 'T8: engine cached');
    assertEqual(cache.hasCache(MODEL_KEY), false, 'T8: model not cached');
    assertEqual(cache.getData(ENGINE_KEY).engine_result.score, 68, 'T8: engine data intact');
    assertEqual(cache.getData(MODEL_KEY), null, 'T8: model data null');

    // Set model
    var modelData = { label: 'Bullish', score: 75 };
    cache.set(MODEL_KEY, modelData);
    assertEqual(cache.hasCache(MODEL_KEY), true, 'T8: model now cached');
    assertEqual(cache.getData(MODEL_KEY).score, 75, 'T8: model data intact');

    // Clear model, engine survives
    cache.clear(MODEL_KEY);
    assertEqual(cache.hasCache(ENGINE_KEY), true, 'T8: engine survives model clear');
    assertEqual(cache.hasCache(MODEL_KEY), false, 'T8: model cleared');
    assertEqual(cache.getData(ENGINE_KEY).engine_result.score, 68, 'T8: engine data still intact');
    console.log('  ✓ Test 8: Engine and model cache independent');
  }

  // ── Runner ────────────────────────────────────────────────────

  function runDashboardCacheTests() {
    _passed = 0;
    _failed = 0;
    _errors = [];

    console.log('═══════════════════════════════════════════════');
    console.log('BenTrade Dashboard Cache Tests');
    console.log('═══════════════════════════════════════════════');

    try { test_first_load_populates_cache(); } catch(e) { _failed++; _errors.push('T1 threw: ' + e.message); }
    try { test_route_remount_reuses_cache(); } catch(e) { _failed++; _errors.push('T2 threw: ' + e.message); }
    try { test_refresh_success_overwrites(); } catch(e) { _failed++; _errors.push('T3 threw: ' + e.message); }
    try { test_refresh_failure_preserves_cache(); } catch(e) { _failed++; _errors.push('T4 threw: ' + e.message); }
    try { test_invalid_payload_rejected(); } catch(e) { _failed++; _errors.push('T5 threw: ' + e.message); }
    try { test_no_cache_status(); } catch(e) { _failed++; _errors.push('T6 threw: ' + e.message); }
    try { test_helper_selectors(); } catch(e) { _failed++; _errors.push('T7 threw: ' + e.message); }
    try { test_engine_model_independent(); } catch(e) { _failed++; _errors.push('T8 threw: ' + e.message); }

    console.log('═══════════════════════════════════════════════');
    console.log('Results: ' + _passed + ' passed, ' + _failed + ' failed');
    if (_errors.length > 0) {
      console.log('Failures:');
      _errors.forEach(function(e) { console.log('  ' + e); });
    }
    console.log('═══════════════════════════════════════════════');

    return { passed: _passed, failed: _failed, errors: _errors };
  }

  // Expose globally
  global.runDashboardCacheTests = runDashboardCacheTests;

})(typeof window !== 'undefined' ? window : globalThis);
