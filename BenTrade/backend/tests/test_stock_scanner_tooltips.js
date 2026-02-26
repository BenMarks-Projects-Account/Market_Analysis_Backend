/**
 * Unit tests for stock scanner tooltip integration.
 *
 * Verifies that:
 *  - attachMetricTooltips is called after rendering candidates
 *  - Metric labels have data-metric attributes (via mapper + label inference)
 *  - Glossary entries exist for all stock_buy core/detail metrics
 *  - Action buttons have title attributes
 *  - Tooltip label fallback map covers scanner metric labels
 *
 * Run:  node tests/test_stock_scanner_tooltips.js
 */
'use strict';

const fs = require('fs');
const path = require('path');

/* ── Minimal browser shim ── */
const _store = {};
const _localStorage = {};
global.sessionStorage = {
  getItem(key){ return _store[key] || null; },
  setItem(key, value){ _store[key] = String(value); },
  removeItem(key){ delete _store[key]; },
  clear(){ Object.keys(_store).forEach(k => delete _store[k]); },
};
global.localStorage = {
  getItem(key){ return _localStorage[key] || null; },
  setItem(key, value){ _localStorage[key] = String(value); },
  removeItem(key){ delete _localStorage[key]; },
  clear(){ Object.keys(_localStorage).forEach(k => delete _localStorage[k]); },
};
global.location = { hash: '' };

/* ── Fake DOM ── */
class FakeElement {
  constructor(tag, id){
    this.tagName = (tag || 'DIV').toUpperCase();
    this.id = id || '';
    this.className = '';
    this.style = {};
    this.textContent = '';
    this._innerHTML = '';
    this.dataset = {};
    this.disabled = false;
    this.children = [];
    this._listeners = {};
    this._attributes = {};
    this.parentElement = null;
    this.ownerDocument = null;
  }
  get innerHTML(){ return this._innerHTML; }
  set innerHTML(v){ this._innerHTML = v; }
  querySelector(sel){ return _queryParsedHTML(this._innerHTML, sel, this); }
  querySelectorAll(sel){ return _queryAllParsedHTML(this._innerHTML, sel, this); }
  setAttribute(k, v){ this._attributes[k] = String(v); }
  getAttribute(k){ return this._attributes[k] ?? null; }
  hasAttribute(k){ return k in this._attributes; }
  addEventListener(type, fn, opts){
    if(!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push(fn);
  }
  removeEventListener(){}
  closest(sel){ return null; }
  appendChild(child){
    child.parentElement = this;
    this.children.push(child);
    if(child._innerHTML != null) this._innerHTML += (child._outerHTML || child._innerHTML);
  }
  matches(sel){ return false; }
  get _outerHTML(){
    return '<' + this.tagName.toLowerCase() + (this.className ? ' class="' + this.className + '"' : '') + '>' + this._innerHTML + '</' + this.tagName.toLowerCase() + '>';
  }
  get classList(){
    const self = this;
    return {
      _classes: new Set((self.className || '').split(/\s+/).filter(Boolean)),
      add(c){ this._classes.add(c); self.className = Array.from(this._classes).join(' '); },
      remove(c){ this._classes.delete(c); self.className = Array.from(this._classes).join(' '); },
      toggle(c){ if(this._classes.has(c)){ this.remove(c); return false; } this.add(c); return true; },
      contains(c){ return this._classes.has(c); },
    };
  }
}

/* Query helpers */
function _queryParsedHTML(html, sel, parentEl){
  if(!html) return null;
  const attrMatch = sel.match(/^\.?([\w-]+)\[data-([\w-]+)="(\d+)"\]$/);
  if(attrMatch){
    const cls = attrMatch[1]; const attr = attrMatch[2]; const val = attrMatch[3];
    const re = new RegExp('<[^>]*class="[^"]*' + cls + '[^"]*"[^>]*data-' + attr + '="' + val + '"');
    if(!re.test(html)) return null;
    const el = new FakeElement('DIV'); el.className = cls; el.dataset[attr] = val; el._innerHTML = html; return el;
  }
  if(sel === '.trade-actions'){
    const idx = html.indexOf('class="trade-actions"');
    if(idx === -1) return null;
    const el = new FakeElement('DIV'); el.className = 'trade-actions'; el._innerHTML = html.substring(html.lastIndexOf('<div', idx)); return el;
  }
  if(sel === '[data-model-output]'){
    if(!html.includes('data-model-output')) return null;
    const el = new FakeElement('DIV'); el._attributes['data-model-output'] = ''; return el;
  }
  if(sel.startsWith('#')){
    const id = sel.slice(1);
    if(_elements[id]) return _elements[id];
    if(html.includes('id="' + id + '"')){ const el = new FakeElement('DIV', id); _elements[id] = el; return el; }
    return null;
  }
  return null;
}
function _queryAllParsedHTML(html, sel){
  if(!html) return [];
  if(sel === 'details.trade-card-collapse'){
    const count = (html.match(/<details/g) || []).length;
    return Array.from({length: count}, () => { const e = new FakeElement('DETAILS'); e.className = 'trade-card-collapse'; e.dataset = {tradeKey:''}; return e; });
  }
  if(sel === '[data-metric], .metric-label, .statLabel, .detail-label, th'){
    /* Extract data-metric elements for tooltip binding test */
    const matches = [];
    const re = /data-metric="([\w_]+)"/g;
    let m;
    while((m = re.exec(html)) !== null){
      const el = new FakeElement('SPAN');
      el._attributes['data-metric'] = m[1];
      el.textContent = m[1];
      matches.push(el);
    }
    /* Also match .metric-label and .detail-label */
    const labelRe = /class="(metric-label|detail-label)"[^>]*>([^<]+)</g;
    while((m = labelRe.exec(html)) !== null){
      const el = new FakeElement('SPAN');
      el.className = m[1];
      el.textContent = m[2];
      matches.push(el);
    }
    return matches;
  }
  return [];
}

const _elements = {};
function _makeEl(tag, id){ const el = new FakeElement(tag, id); _elements[id] = el; return el; }
const _fakeDoc = {
  getElementById(id){ return _elements[id] || null; },
  createElement(tag){ return new FakeElement(tag); },
  body: new FakeElement('BODY'),
  addEventListener(){},
};

global.window = global;
global.document = _fakeDoc;
try { global.navigator = { clipboard: { writeText: async () => {} }, maxTouchPoints: 0 }; } catch(e){}
global.MutationObserver = class { constructor(){} observe(){} disconnect(){} };

const _origDebug = console.debug;
console.debug = () => {};

/* ── Load modules ── */
const frontendBase = path.resolve(__dirname, '..', '..', 'frontend', 'assets', 'js');
function loadModule(relPath){
  const p = path.join(frontendBase, relPath);
  eval(fs.readFileSync(p, 'utf-8'));
}

window.BenTradeUtils = window.BenTradeUtils || {};
window.BenTradeUI = window.BenTradeUI || {};
window.BenTradeDebug = { isEnabled(){ return false; } };
window.BenTradeMetrics = window.BenTradeMetrics || {};

loadModule('utils/format.js');
loadModule('metrics/glossary.js');
loadModule('config/strategy_card_config.js');
loadModule('models/option_trade_card_model.js');
loadModule('ui/trade_card.js');
loadModule('ui/tooltip.js');

/* Mock external deps */
window.BenTradeScanResultsCache = {
  _data: {},
  save(id, payload){ this._data[id] = { payload, ts: new Date().toISOString() }; },
  load(id){ return this._data[id] || null; },
  clear(id){ delete this._data[id]; },
  getTimestamp(id){ return this._data[id]?.ts || null; },
  formatTimestamp(id){ return this._data[id]?.ts ? 'Today 12:00' : 'N/A'; },
};
window.BenTradeNotes = { attachNotes(){} };
window.BenTradeSessionStatsStore = { recordRun(){}, recordReject(){} };
window.BenTradeSourceHealthStore = { fetchSourceHealth(){ return Promise.resolve(); } };
window.BenTradeSymbolUniverseSelector = null;
window.BenTradeExecutionModal = null;
window.BenTradePages = window.BenTradePages || {};
window.BenTradeApi = {
  modelAnalyzeStock(){ return Promise.resolve({}); },
  postLifecycleEvent(){ return Promise.resolve(); },
};

/* Track attachMetricTooltips calls */
let tooltipCallCount = 0;
let lastTooltipScope = null;
const _originalAttach = window.attachMetricTooltips;
window.attachMetricTooltips = function(rootEl){
  tooltipCallCount++;
  lastTooltipScope = rootEl;
  /* Don't call the real one — no real DOM to bind */
};

loadModule('pages/stock_scanner.js');

/* ── Test data ── */
const MOCK_CANDIDATE = {
  symbol: 'SPY', price: 450.25, composite_score: 82,
  trend_score: 78, momentum_score: 71, volatility_score: 65,
  pullback_score: 55, catalyst_score: 40,
  signals: ['trend_up'], trend: 'bullish', thesis: ['Good setup'],
  sparkline: [0, 1.2, 2.5],
  metrics: { rsi14: 62.5, rv20: 0.15, iv_rv_ratio: 1.2, price_change_1d: 0.005, price_change_20d: 0.032, low_52w: 380.5, high_52w: 465.2, ema20: 445.0, sma50: 440.0 },
};
const MOCK_PAYLOAD = { as_of: '2026-02-25T12:00:00Z', candidates: [MOCK_CANDIDATE], notes: [] };

function buildRootEl(){
  const root = new FakeElement('DIV', 'stockScannerRoot');
  root.ownerDocument = _fakeDoc;
  _makeEl('BUTTON', 'stockScannerRefreshBtn').textContent = 'Run Scan';
  _makeEl('BUTTON', 'stockScannerClearBtn');
  _makeEl('SPAN', 'stockScannerLastRun');
  _makeEl('DIV', 'stockScannerError');
  _makeEl('DIV', 'stockScannerMeta');
  _makeEl('DIV', 'stockScannerList');
  _makeEl('DIV', 'stockScannerSymbols');
  _makeEl('DIV', 'tradeCountsBar');
  root.querySelector = function(sel){
    const idMatch = sel.match(/#(\w+)/);
    if(idMatch) return _elements[idMatch[1]] || null;
    return null;
  };
  return root;
}

/* ── Test harness ── */
let passed = 0, failed = 0;
function assert(cond, msg){
  if(!cond){ failed++; console.error('  FAIL:', msg); }
  else { passed++; console.log('  PASS:', msg); }
}

/* ================================================================
 * Test 1: attachMetricTooltips is called after rendering
 * ================================================================ */
console.log('\n--- attachMetricTooltips called after render ---');
{
  tooltipCallCount = 0;
  lastTooltipScope = null;
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  assert(tooltipCallCount >= 1, 'attachMetricTooltips was called (count: ' + tooltipCallCount + ')');
  assert(lastTooltipScope === _elements['stockScannerList'], 'scope was listEl');
}

/* ================================================================
 * Test 2: Core metrics have data-metric attributes from mapper
 * ================================================================ */
console.log('\n--- Core metrics have data-metric attributes ---');
{
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const html = _elements['stockScannerList'].innerHTML;

  /* The mapper sets dataMetric to each metric's key */
  assert(html.includes('data-metric="rank_score"'), 'rank_score data-metric present');
  assert(html.includes('data-metric="trend_score"'), 'trend_score data-metric present');
  assert(html.includes('data-metric="momentum_score"'), 'momentum_score data-metric present');
  assert(html.includes('data-metric="pullback_score"'), 'pullback_score data-metric present');
  assert(html.includes('data-metric="catalyst_score"'), 'catalyst_score data-metric present');
  assert(html.includes('data-metric="iv_rv_ratio"'), 'iv_rv_ratio data-metric present');
}

/* ================================================================
 * Test 3: Detail fields have data-metric attributes from mapper
 * ================================================================ */
console.log('\n--- Detail fields have data-metric attributes ---');
{
  const html = _elements['stockScannerList'].innerHTML;

  assert(html.includes('data-metric="rsi14"'), 'rsi14 data-metric present');
  assert(html.includes('data-metric="volatility_score"'), 'volatility_score data-metric present');
  assert(html.includes('data-metric="ema20"'), 'ema20 data-metric present');
  assert(html.includes('data-metric="sma50"'), 'sma50 data-metric present');
}

/* ================================================================
 * Test 4: Extra scanner detail has data-metric for RV20
 * ================================================================ */
console.log('\n--- Extra scanner details have data-metric ---');
{
  const html = _elements['stockScannerList'].innerHTML;
  assert(html.includes('data-metric="realized_vol_20d"'), 'RV20 has data-metric="realized_vol_20d"');
}

/* ================================================================
 * Test 5: Glossary has entries for all stock_buy metrics
 * ================================================================ */
console.log('\n--- Glossary covers stock_buy metrics ---');
{
  const glossary = window.BenTradeMetrics.glossary;

  assert(glossary.rank_score, 'glossary has rank_score');
  assert(glossary.trend_score, 'glossary has trend_score');
  assert(glossary.momentum_score, 'glossary has momentum_score');
  assert(glossary.pullback_score, 'glossary has pullback_score');
  assert(glossary.catalyst_score, 'glossary has catalyst_score');
  assert(glossary.volatility_score, 'glossary has volatility_score');
  assert(glossary.iv_rv_ratio, 'glossary has iv_rv_ratio');
  assert(glossary.rsi_14, 'glossary has rsi_14');
  assert(glossary.ema_20, 'glossary has ema_20');
  assert(glossary.sma_50, 'glossary has sma_50');
  assert(glossary.realized_vol_20d, 'glossary has realized_vol_20d');
  assert(glossary.composite_score, 'glossary has composite_score');

  /* Verify new entries have required fields */
  ['trend_score', 'momentum_score', 'pullback_score', 'catalyst_score', 'volatility_score'].forEach(key => {
    const entry = glossary[key];
    assert(entry.label, key + ' has label');
    assert(entry.short, key + ' has short description');
    assert(entry.formula, key + ' has formula');
    assert(entry.why, key + ' has why');
  });
}

/* ================================================================
 * Test 6: Label fallback map covers scanner labels
 * ================================================================ */
console.log('\n--- Tooltip label fallback map ---');
{
  /* Access the internal LABEL_FALLBACK_MAP via the tooltip module.
     We'll test it indirectly via inferMetricFromLabel simulation. */
  const tooltip = window.BenTradeUI.Tooltip;

  /* Verify the tooltip module exported correctly */
  assert(typeof tooltip.attachMetricTooltips === 'function', 'tooltip module exports attachMetricTooltips');

  /* We can test the label map by checking the source directly */
  const tooltipSrc = fs.readFileSync(path.join(frontendBase, 'ui', 'tooltip.js'), 'utf-8');

  const expectedMappings = {
    'trend': 'trend_score',
    'trend score': 'trend_score',
    'momentum': 'momentum_score',
    'momentum score': 'momentum_score',
    'pullback': 'pullback_score',
    'pullback score': 'pullback_score',
    'catalyst': 'catalyst_score',
    'catalyst score': 'catalyst_score',
    'volatility': 'volatility_score',
    'volatility score': 'volatility_score',
    'ema-20': 'ema_20',
    'sma-50': 'sma_50',
  };

  Object.entries(expectedMappings).forEach(([label, metricId]) => {
    const pattern = "'" + label + "': '" + metricId + "'";
    assert(tooltipSrc.includes(pattern), 'fallback map has: ' + label + ' → ' + metricId);
  });
}

/* ================================================================
 * Test 7: Action buttons have title attributes
 * ================================================================ */
console.log('\n--- Action buttons have title attributes ---');
{
  const html = _elements['stockScannerList'].innerHTML;

  assert(html.includes('title="Run model analysis on this trade"'), 'model-analysis button has title');
  assert(html.includes('title="Open execution modal"'), 'execute button has title');
  assert(html.includes('title="Reject this trade"'), 'reject button has title');
  assert(html.includes('title="Send to Testing Workbench"'), 'workbench button has title');
  assert(html.includes('title="Send to Data Workbench"'), 'data-workbench button has title');
  assert(html.includes('title="Copy trade key to clipboard"'), 'copy trade key button has title');
}

/* ================================================================
 * Test 8: Tooltip not clipped (overflow:visible in CSS)
 * ================================================================ */
console.log('\n--- Tooltip not clipped by card containers ---');
{
  const cssSrc = fs.readFileSync(
    path.resolve(__dirname, '..', '..', 'frontend', 'assets', 'css', 'app.css'), 'utf-8'
  );

  /* .metric-tooltip should be position:fixed with high z-index */
  assert(cssSrc.includes('.metric-tooltip{'), 'metric-tooltip CSS exists');
  assert(cssSrc.includes('position: fixed'), 'metric-tooltip is position:fixed');
  assert(cssSrc.includes('z-index: 4000'), 'metric-tooltip has z-index:4000');

  /* .trade-card should have overflow:visible */
  const tradeCardBlock = cssSrc.match(/\.trade-card\{[^}]+\}/);
  assert(tradeCardBlock && tradeCardBlock[0].includes('overflow:visible'), 'trade-card has overflow:visible');
}

/* ================================================================
 * Test 9: No duplicate label fallback entries
 * ================================================================ */
console.log('\n--- No duplicate label fallback entries ---');
{
  const tooltipSrc = fs.readFileSync(path.join(frontendBase, 'ui', 'tooltip.js'), 'utf-8');

  /* Extract all entries from the LABEL_FALLBACK_MAP object */
  const mapMatch = tooltipSrc.match(/const LABEL_FALLBACK_MAP\s*=\s*\{([\s\S]*?)\};/);
  assert(mapMatch, 'LABEL_FALLBACK_MAP found in source');

  if(mapMatch){
    const entries = mapMatch[1].match(/'([^']+)':/g) || [];
    const keys = entries.map(e => e.replace(/[':]/g, ''));
    const unique = new Set(keys);
    assert(keys.length === unique.size, 'no duplicate keys (' + keys.length + ' entries, ' + unique.size + ' unique)');
  }
}

/* ── Summary ── */
console.debug = _origDebug;
console.log('\n========================================');
console.log('  ' + passed + ' passed, ' + failed + ' failed');
console.log('========================================\n');

process.exit(failed > 0 ? 1 : 0);
