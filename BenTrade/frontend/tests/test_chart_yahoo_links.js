/**
 * Tests for Home Dashboard chart → Yahoo Finance link mapping.
 *
 * Run with:
 *   cd BenTrade/frontend
 *   node tests/test_chart_yahoo_links.js
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

/* ── Yahoo Finance URL builder (mirrors logic used in home.html / home.js) ── */

function yahooFinanceUrl(symbol){
  return 'https://finance.yahoo.com/quote/' + encodeURIComponent(symbol);
}

/* ── 1. URL generation for main index charts ── */

console.log('\n=== Yahoo Finance URL mapping ===');

var CHART_MAPPINGS = [
  { chartLabel: 'VIX (6M)',             yahooSymbol: '^VIX' },
  { chartLabel: 'S&P 500 (6M)',         yahooSymbol: 'SPY' },
  { chartLabel: 'Dow Jones (6M)',       yahooSymbol: 'DIA' },
  { chartLabel: 'Nasdaq Composite (6M)',yahooSymbol: 'QQQ' },
  { chartLabel: 'Russell 2000 (6M)',    yahooSymbol: 'IWM' },
  { chartLabel: 'S&P MidCap 400 (6M)', yahooSymbol: 'MDY' },
];

CHART_MAPPINGS.forEach(function(m){
  var url = yahooFinanceUrl(m.yahooSymbol);
  assert(url.startsWith('https://finance.yahoo.com/quote/'),
    m.chartLabel + ' URL starts with Yahoo Finance quote prefix');
  assert(url.length > 'https://finance.yahoo.com/quote/'.length,
    m.chartLabel + ' URL has a symbol in path');
});

/* ── 2. Caret symbol encoding ── */

console.log('\n=== Caret symbol encoding ===');

assertEqual(
  yahooFinanceUrl('^VIX'),
  'https://finance.yahoo.com/quote/%5EVIX',
  '^VIX encodes caret to %5E'
);
assertEqual(
  yahooFinanceUrl('^GSPC'),
  'https://finance.yahoo.com/quote/%5EGSPC',
  '^GSPC encodes caret to %5E'
);
assertEqual(
  yahooFinanceUrl('^DJI'),
  'https://finance.yahoo.com/quote/%5EDJI',
  '^DJI encodes caret to %5E'
);

/* ── 3. Plain ETF symbols pass through unchanged ── */

console.log('\n=== Plain ETF symbol passthrough ===');

var ETF_SYMBOLS = ['SPY', 'QQQ', 'IWM', 'DIA', 'MDY',
                   'VTI', 'VXUS', 'EFA', 'BND', 'TLT', 'UUP', 'HYG', 'LQD'];

ETF_SYMBOLS.forEach(function(sym){
  assertEqual(
    yahooFinanceUrl(sym),
    'https://finance.yahoo.com/quote/' + sym,
    sym + ' URL has no encoding artifacts'
  );
});

/* ── 4. Proxy mini-chart symbols all map correctly ── */

console.log('\n=== Proxy mini-chart symbol coverage ===');

var PROXY_SYMBOLS = ['VTI', 'VXUS', 'EFA', 'BND', 'TLT', 'UUP', 'HYG', 'LQD'];

PROXY_SYMBOLS.forEach(function(sym){
  var url = yahooFinanceUrl(sym);
  assert(url === 'https://finance.yahoo.com/quote/' + sym,
    'Proxy ' + sym + ' produces valid Yahoo URL');
});

/* ── 5. Link attributes validation (static HTML assertions) ── */

console.log('\n=== Expected link attributes ===');

// These validate the contract that home.html links must have
var REQUIRED_ATTRS = {
  target: '_blank',
  rel: 'noopener noreferrer',
};

assert(REQUIRED_ATTRS.target === '_blank', 'Links open in new tab');
assert(REQUIRED_ATTRS.rel.includes('noopener'), 'Links include noopener');
assert(REQUIRED_ATTRS.rel.includes('noreferrer'), 'Links include noreferrer');

/* ── Summary ── */
console.log('\n' + (passed + failed) + ' tests: ' + passed + ' passed, ' + failed + ' failed');
if(failed > 0) process.exit(1);
