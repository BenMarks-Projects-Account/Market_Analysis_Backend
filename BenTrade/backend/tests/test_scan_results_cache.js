/**
 * Unit tests for scanResultsCache.js
 *
 * Run with Node.js:  node tests/test_scan_results_cache.js
 *
 * Simulates the browser environment by shimming sessionStorage and window.
 */
'use strict';

const fs = require('fs');
const path = require('path');

/* ── Minimal browser shim ── */
const _store = {};
global.window = global;
global.sessionStorage = {
  getItem(key){ return _store[key] || null; },
  setItem(key, value){ _store[key] = String(value); },
  removeItem(key){ delete _store[key]; },
  clear(){ Object.keys(_store).forEach(k => delete _store[k]); },
};
global.console = global.console || { log(){}, warn(){}, debug(){}, error(){} };

/* ── Load module ── */
const modulePath = path.resolve(__dirname, '..', '..', 'frontend', 'assets', 'js', 'stores', 'scanResultsCache.js');
const src = fs.readFileSync(modulePath, 'utf-8');
eval(src);

const cache = window.BenTradeScanResultsCache;

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

/* ================================================================
   Test 1: save + load round-trip
   ================================================================ */
console.log('\n--- save + load round-trip ---');
{
  sessionStorage.clear();
  const payload = { candidates: [{ symbol: 'SPY', composite_score: 85 }], as_of: '2026-02-25T12:00:00Z' };
  const config = { filterLevel: 'balanced' };

  cache.save('stockScanner', payload, config);
  const loaded = cache.load('stockScanner');

  assert(loaded !== null, 'loaded should not be null after save');
  assert(loaded.payload.candidates.length === 1, 'payload should have 1 candidate');
  assert(loaded.payload.candidates[0].symbol === 'SPY', 'candidate symbol should be SPY');
  assert(loaded.config.filterLevel === 'balanced', 'config should preserve filterLevel');
  assert(typeof loaded.timestamp === 'string' && loaded.timestamp.length > 0, 'timestamp should be ISO string');
}

/* ================================================================
   Test 2: load returns null when nothing saved
   ================================================================ */
console.log('\n--- load returns null when empty ---');
{
  sessionStorage.clear();
  const loaded = cache.load('stockScanner');
  assert(loaded === null, 'should return null when no data saved');
}

/* ================================================================
   Test 3: clear removes cached data
   ================================================================ */
console.log('\n--- clear removes cached data ---');
{
  sessionStorage.clear();
  cache.save('stockScanner', { candidates: [] });
  assert(cache.load('stockScanner') !== null, 'should exist after save');
  cache.clear('stockScanner');
  assert(cache.load('stockScanner') === null, 'should be null after clear');
}

/* ================================================================
   Test 4: stale data (TTL exceeded) returns null
   ================================================================ */
console.log('\n--- stale data returns null ---');
{
  sessionStorage.clear();
  // Manually write an entry with an old timestamp
  const oldEntry = {
    payload: { candidates: [{ symbol: 'QQQ' }] },
    timestamp: new Date(Date.now() - 7 * 60 * 60 * 1000).toISOString(), // 7 hours ago
    config: {},
  };
  sessionStorage.setItem(cache._storageKey('stockScanner'), JSON.stringify(oldEntry));

  const loaded = cache.load('stockScanner'); // default TTL = 6h
  assert(loaded === null, 'should return null for data older than 6h');
  // Also check it cleaned up
  assert(sessionStorage.getItem(cache._storageKey('stockScanner')) === null, 'stale entry should be removed from sessionStorage');
}

/* ================================================================
   Test 5: custom TTL
   ================================================================ */
console.log('\n--- custom TTL ---');
{
  sessionStorage.clear();
  const recentEntry = {
    payload: { candidates: [{ symbol: 'IWM' }] },
    timestamp: new Date(Date.now() - 30 * 1000).toISOString(), // 30 seconds ago
    config: {},
  };
  sessionStorage.setItem(cache._storageKey('stockScanner'), JSON.stringify(recentEntry));

  // TTL of 10 seconds → should be stale
  const stale = cache.load('stockScanner', 10 * 1000);
  assert(stale === null, 'should be stale with 10s TTL for 30s-old data');

  // Re-save and try with 60 second TTL
  sessionStorage.setItem(cache._storageKey('stockScanner'), JSON.stringify(recentEntry));
  const fresh = cache.load('stockScanner', 60 * 1000);
  assert(fresh !== null, 'should be fresh with 60s TTL for 30s-old data');
}

/* ================================================================
   Test 6: getTimestamp + formatTimestamp
   ================================================================ */
console.log('\n--- getTimestamp + formatTimestamp ---');
{
  sessionStorage.clear();
  assert(cache.getTimestamp('stockScanner') === null, 'getTimestamp should be null when empty');
  assert(cache.formatTimestamp('stockScanner') === 'N/A', 'formatTimestamp should be N/A when empty');

  cache.save('stockScanner', { candidates: [] });
  const ts = cache.getTimestamp('stockScanner');
  assert(ts !== null, 'getTimestamp should return string after save');
  assert(typeof ts === 'string', 'timestamp should be a string');

  const formatted = cache.formatTimestamp('stockScanner');
  assert(formatted !== 'N/A', 'formatTimestamp should not be N/A after save');
  assert(formatted.startsWith('Today'), 'formatTimestamp should start with Today for recent save');
}

/* ================================================================
   Test 7: different scanner IDs are isolated
   ================================================================ */
console.log('\n--- scanner IDs are isolated ---');
{
  sessionStorage.clear();
  cache.save('stockScanner', { candidates: [{ symbol: 'SPY' }] });
  cache.save('creditScanner', { trades: [{ symbol: 'AAPL' }] });

  const stockResult = cache.load('stockScanner');
  const creditResult = cache.load('creditScanner');

  assert(stockResult !== null, 'stock scanner should have data');
  assert(creditResult !== null, 'credit scanner should have data');
  assert(stockResult.payload.candidates[0].symbol === 'SPY', 'stock data should be SPY');
  assert(creditResult.payload.trades[0].symbol === 'AAPL', 'credit data should be AAPL');

  cache.clear('stockScanner');
  assert(cache.load('stockScanner') === null, 'stock scanner should be cleared');
  assert(cache.load('creditScanner') !== null, 'credit scanner should still exist');
}

/* ================================================================
   Test 8: save with null/invalid payload is no-op
   ================================================================ */
console.log('\n--- save with null payload is no-op ---');
{
  sessionStorage.clear();
  cache.save('stockScanner', null);
  assert(cache.load('stockScanner') === null, 'null payload should not be saved');

  cache.save('stockScanner', 'not an object');
  assert(cache.load('stockScanner') === null, 'non-object payload should not be saved');
}

/* ================================================================
   Test 9: storage key format
   ================================================================ */
console.log('\n--- storage key format ---');
{
  assert(cache._storageKey('stockScanner') === 'bentrade.scanResults.stockScanner.v1', 'key should match expected format');
}

/* ── Summary ── */
console.log(`\n=== ${passed} passed, ${failed} failed ===`);
process.exit(failed > 0 ? 1 : 0);
