/**
 * Unit tests for stock_scanner.js — TradeCard alignment refactor.
 *
 * Verifies that the Stock Scanner page renders candidates using the
 * canonical renderFullCard (with <details>/<summary> collapse) and that
 * action buttons are always visible outside the <details> element.
 *
 * Run:  node tests/test_stock_scanner_card_alignment.js
 */
'use strict';

const fs = require('fs');
const path = require('path');

/* ── Minimal DOM shim (jsdom-lite) ── */
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

/* Minimal DOM element implementation */
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
  set innerHTML(v){
    this._innerHTML = v;
    /* When innerHTML is set, create a lightweight child-map for querySelector */
    this._parsedChildren = null;
  }
  querySelector(sel){
    return _queryParsedHTML(this._innerHTML, sel, this);
  }
  querySelectorAll(sel){
    return _queryAllParsedHTML(this._innerHTML, sel, this);
  }
  setAttribute(k, v){ this._attributes[k] = String(v); }
  getAttribute(k){ return this._attributes[k] ?? null; }
  addEventListener(type, fn, opts){
    if(!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push({ fn, capture: opts === true || (opts && opts.capture) });
  }
  removeEventListener(){}
  closest(sel){
    let el = this;
    while(el){
      if(_matchesSel(el, sel)) return el;
      el = el.parentElement;
    }
    return null;
  }
  appendChild(child){
    child.parentElement = this;
    this.children.push(child);
    if(child._innerHTML != null) this._innerHTML += child._outerHTML || child._innerHTML;
  }
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

/* Parse selector to match against HTML string and return FakeElement proxies */
function _matchesSel(el, sel){
  if(!el || !el.tagName) return false;
  if(sel.startsWith('#')) return el.id === sel.slice(1);
  if(sel.startsWith('.')) return (el.className || '').includes(sel.slice(1));
  if(sel.startsWith('[')) {
    const attr = sel.replace(/[\[\]]/g, '').split('=')[0];
    return el._attributes?.[attr] != null;
  }
  return el.tagName === sel.toUpperCase();
}

function _queryParsedHTML(html, sel, parentEl){
  if(!html) return null;

  /* Handle compound selectors like .trade-card[data-idx="0"] */
  const attrMatch = sel.match(/^\.?([\w-]+)\[data-([\w-]+)="(\d+)"\]$/);
  if(attrMatch){
    const cls = attrMatch[1];
    const attr = attrMatch[2];
    const val = attrMatch[3];
    const re = new RegExp('<[^>]*class="[^"]*' + cls + '[^"]*"[^>]*data-' + attr + '="' + val + '"[\\s\\S]*?(?=<div class="' + cls + '"[^>]*data-' + attr + '="|$)');
    const m = html.match(re);
    if(!m) return null;
    const el = new FakeElement('DIV');
    el.className = cls;
    el.dataset[attr] = val;
    el._innerHTML = m[0];
    el.ownerDocument = _fakeDoc;
    el.parentElement = parentEl;
    return el;
  }

  /* .trade-actions */
  if(sel === '.trade-actions'){
    const idx = html.indexOf('class="trade-actions"');
    if(idx === -1) return null;
    const start = html.lastIndexOf('<div', idx);
    /* Find matching closing </div> — simplistic: grab everything up to closing </div> patterns */
    const afterActionsTag = html.substring(start);
    const el = new FakeElement('DIV');
    el.className = 'trade-actions';
    el._innerHTML = afterActionsTag;
    el.ownerDocument = _fakeDoc;
    el.parentElement = parentEl;
    return el;
  }

  /* details.trade-card-collapse */
  if(sel === 'details.trade-card-collapse'){
    if(!html.includes('trade-card-collapse')) return null;
    const el = new FakeElement('DETAILS');
    el.className = 'trade-card-collapse';
    el._innerHTML = html;
    el.ownerDocument = _fakeDoc;
    return el;
  }

  /* [data-model-output] */
  if(sel === '[data-model-output]'){
    if(!html.includes('data-model-output')) return null;
    const el = new FakeElement('DIV');
    el.className = 'trade-model-output';
    el._attributes['data-model-output'] = '';
    el.ownerDocument = _fakeDoc;
    return el;
  }

  /* ID selector */
  if(sel.startsWith('#')){
    const id = sel.slice(1);
    if(_elements[id]) return _elements[id];
    if(html.includes('id="' + id + '"')){
      const el = new FakeElement('DIV', id);
      _elements[id] = el;
      return el;
    }
    return null;
  }

  return null;
}

function _queryAllParsedHTML(html, sel, parentEl){
  if(!html) return [];

  /* details.trade-card-collapse */
  if(sel === 'details.trade-card-collapse'){
    const count = (html.match(/<details[^>]*class="[^"]*trade-card-collapse/g) || []).length;
    const results = [];
    for(let i = 0; i < count; i++){
      const el = new FakeElement('DETAILS');
      el.className = 'trade-card-collapse';
      el.dataset = { tradeKey: '' };
      el.ownerDocument = _fakeDoc;
      results.push(el);
    }
    return results;
  }

  /* [data-copy-trade-key] */
  if(sel === '[data-copy-trade-key]'){
    const count = (html.match(/data-copy-trade-key/g) || []).length;
    const results = [];
    for(let i = 0; i < count; i++){
      const el = new FakeElement('BUTTON');
      el._attributes['data-copy-trade-key'] = 'test';
      results.push(el);
    }
    return results;
  }

  return [];
}

/* Fake document */
const _elements = {};
function _makeEl(tag, id){
  const el = new FakeElement(tag, id);
  _elements[id] = el;
  return el;
}

const _fakeDoc = {
  getElementById(id){
    return _elements[id] || null;
  },
  createElement(tag){
    const el = new FakeElement(tag);
    return el;
  },
};

/* ── Set up global window ── */
global.window = global;
global.document = _fakeDoc;
try { global.navigator = { clipboard: { writeText: async () => {} } }; } catch(e) { /* read-only in some Node versions */ }
global.console = global.console || { log(){}, warn(){}, debug(){}, error(){} };

/* Suppress debug logs during tests */
const _origDebug = console.debug;
console.debug = () => {};

/* ── Load real modules ── */
const frontendBase = path.resolve(__dirname, '..', '..', 'frontend', 'assets', 'js');

function loadModule(relPath){
  const p = path.join(frontendBase, relPath);
  const src = fs.readFileSync(p, 'utf-8');
  eval(src);
}

/* Dependencies in load order */
window.BenTradeUtils = window.BenTradeUtils || {};
window.BenTradeDebug = { isEnabled(){ return false; } };
loadModule('utils/format.js');
loadModule('config/strategy_card_config.js');
loadModule('models/option_trade_card_model.js');
loadModule('ui/trade_card.js');

/* Mock external dependencies */
window.BenTradeScanResultsCache = {
  _data: {},
  save(id, payload){ this._data[id] = { payload, ts: new Date().toISOString() }; },
  load(id){ return this._data[id] || null; },
  clear(id){ delete this._data[id]; },
  getTimestamp(id){ return this._data[id]?.ts || null; },
  formatTimestamp(id){ return this._data[id]?.ts ? 'Today 12:00' : 'N/A'; },
};

window.BenTradeNotes = { attachNotes(){ } };
window.BenTradeSessionStatsStore = { recordRun(){}, recordReject(){} };
window.BenTradeSourceHealthStore = { fetchSourceHealth(){ return Promise.resolve(); } };
window.BenTradeSymbolUniverseSelector = null;
window.BenTradeExecutionModal = null;
window.BenTradePages = window.BenTradePages || {};

/* Mock API */
window.BenTradeApi = {
  modelAnalyzeStock(symbol, row, provider){
    return Promise.resolve({
      recommendation: 'BUY',
      confidence: 0.85,
      summary: 'Test model result',
      key_factors: ['Strong trend'],
      risks: ['Volatility'],
      time_horizon: '1W',
      trade_ideas: [],
    });
  },
  postLifecycleEvent(){ return Promise.resolve(); },
};

/* ── Load the stock scanner module ── */
loadModule('pages/stock_scanner.js');

/* ── Build fake root element with required child elements ── */
function buildRootEl(){
  const root = new FakeElement('DIV', 'stockScannerRoot');
  root.ownerDocument = _fakeDoc;

  const refreshBtn = _makeEl('BUTTON', 'stockScannerRefreshBtn');
  refreshBtn.textContent = 'Run Scan';
  const clearBtn = _makeEl('BUTTON', 'stockScannerClearBtn');
  const lastRunEl = _makeEl('SPAN', 'stockScannerLastRun');
  const errorEl = _makeEl('DIV', 'stockScannerError');
  const metaEl = _makeEl('DIV', 'stockScannerMeta');
  const listEl = _makeEl('DIV', 'stockScannerList');
  const symbolsEl = _makeEl('DIV', 'stockScannerSymbols');
  const countsBar = _makeEl('DIV', 'tradeCountsBar');

  root.querySelector = function(sel){
    const idMatch = sel.match(/#(\w+)/);
    if(idMatch) return _elements[idMatch[1]] || null;
    return null;
  };

  return root;
}

/* ── Test harness ── */
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

/* ── Test data ── */
const MOCK_CANDIDATE = {
  symbol: 'SPY',
  price: 450.25,
  composite_score: 82,
  trend_score: 78,
  momentum_score: 71,
  volatility_score: 65,
  pullback_score: 55,
  catalyst_score: 40,
  signals: ['trend_up', 'momentum_positive'],
  trend: 'bullish',
  thesis: ['Strong uptrend', 'Low volatility environment'],
  sparkline: [0, 1.2, 2.5, 1.8, 3.1],
  metrics: {
    rsi14: 62.5,
    rv20: 0.15,
    iv_rv_ratio: 1.2,
    price_change_1d: 0.005,
    price_change_20d: 0.032,
    low_52w: 380.5,
    high_52w: 465.2,
    ema20: 445.0,
    sma50: 440.0,
  },
};

const MOCK_PAYLOAD = {
  as_of: '2026-02-25T12:00:00Z',
  candidates: [MOCK_CANDIDATE],
  notes: [],
  source_status: 'healthy',
};

/* ================================================================
 * Test 1: candidateToTradeShape flattens correctly
 * ================================================================ */
console.log('\n--- candidateToTradeShape transformation ---');
{
  /* We need to access the internal function. Since it's in a closure,
     we test it indirectly by checking what the mapper receives.
     Instead, we'll test the shape by intercepting renderFullCard. */

  let capturedTrade = null;
  const origRenderFullCard = window.BenTradeTradeCard.renderFullCard;
  window.BenTradeTradeCard.renderFullCard = function(rawTrade, idx, opts){
    capturedTrade = rawTrade;
    return origRenderFullCard(rawTrade, idx, opts);
  };

  /* Pre-seed cache so initStockScanner renders on mount */
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);

  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  assert(capturedTrade !== null, 'renderFullCard was called');
  assert(capturedTrade.symbol === 'SPY', 'symbol mapped: ' + capturedTrade.symbol);
  assert(capturedTrade.strategy_id === 'stock_buy', 'strategy_id set to stock_buy');
  assert(capturedTrade.trade_key === 'SPY|NA|stock_buy|NA|NA|NA', 'trade_key generated: ' + capturedTrade.trade_key);
  assert(capturedTrade.underlying_price === 450.25, 'underlying_price from price: ' + capturedTrade.underlying_price);
  assert(capturedTrade.composite_score === 82, 'composite_score: ' + capturedTrade.composite_score);
  assert(capturedTrade.trend_score === 78, 'trend_score: ' + capturedTrade.trend_score);
  assert(capturedTrade.momentum_score === 71, 'momentum_score: ' + capturedTrade.momentum_score);
  assert(capturedTrade.rsi14 === 62.5, 'rsi14 flattened from metrics: ' + capturedTrade.rsi14);
  assert(capturedTrade.iv_rv_ratio === 1.2, 'iv_rv_ratio flattened from metrics: ' + capturedTrade.iv_rv_ratio);
  assert(capturedTrade.ema20 === 445.0, 'ema20 flattened: ' + capturedTrade.ema20);
  assert(capturedTrade.sma50 === 440.0, 'sma50 flattened: ' + capturedTrade.sma50);

  window.BenTradeTradeCard.renderFullCard = origRenderFullCard;
}

/* ================================================================
 * Test 2: Card HTML uses <details>/<summary> structure
 * ================================================================ */
console.log('\n--- Card renders with <details>/<summary> ---');
{
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  assert(html.includes('<details'), 'HTML contains <details> element');
  assert(html.includes('trade-card-collapse'), 'details has trade-card-collapse class');
  assert(html.includes('<summary'), 'HTML contains <summary> element');
  assert(html.includes('trade-summary'), 'summary has trade-summary class');
  assert(!html.includes('is-collapsed'), 'no legacy is-collapsed class');
  assert(!html.includes('▸') && !html.includes('▾'), 'no legacy text chevrons');
  assert(html.includes('<svg'), 'uses SVG chevron');
}

/* ================================================================
 * Test 3: Action buttons are OUTSIDE <details> (always visible)
 * ================================================================ */
console.log('\n--- Action buttons outside <details> ---');
{
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  /* In the canonical card: actions are AFTER </details> */
  const detailsEnd = html.indexOf('</details>');
  assert(detailsEnd > -1, '</details> found in HTML');

  const afterDetails = html.substring(detailsEnd);
  assert(afterDetails.includes('trade-actions'), 'trade-actions div is after </details>');
  assert(afterDetails.includes('data-action="model-analysis"'), 'model-analysis button after </details>');
  assert(afterDetails.includes('data-action="execute"'), 'execute button after </details>');
  assert(afterDetails.includes('data-action="reject"'), 'reject button after </details>');
  assert(afterDetails.includes('data-action="workbench"'), 'workbench button after </details>');
  assert(afterDetails.includes('data-action="data-workbench"'), 'data-workbench button after </details>');
}

/* ================================================================
 * Test 4: Standard 5-button set is present
 * ================================================================ */
console.log('\n--- Standard 5-button action set ---');
{
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  assert(html.includes('Run Model Analysis'), 'Run Model Analysis button label');
  assert(html.includes('Execute Trade'), 'Execute Trade button label');
  assert(html.includes('Reject'), 'Reject button label');
  assert(html.includes('Send to Testing Workbench'), 'Send to Testing Workbench button label');
  assert(html.includes('Send to Data Workbench'), 'Send to Data Workbench button label');
}

/* ================================================================
 * Test 5: Open in Stock Analysis button is injected
 * ================================================================ */
console.log('\n--- Open in Stock Analysis injected ---');
{
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();

  /* Intercept appendChild to verify the "Open in Stock Analysis" button is created */
  let injectedOpenAnalysis = false;
  const origCreateElement = _fakeDoc.createElement;
  _fakeDoc.createElement = function(tag){
    const el = origCreateElement(tag);
    const origSetInnerHTML = Object.getOwnPropertyDescriptor(FakeElement.prototype, 'innerHTML').set;
    let _innerCapture = '';
    Object.defineProperty(el, 'innerHTML', {
      get(){ return _innerCapture; },
      set(v){
        _innerCapture = v;
        if(String(v).includes('data-action="open-analysis"')) injectedOpenAnalysis = true;
      },
    });
    return el;
  };

  window.BenTradePages.initStockScanner(root);
  _fakeDoc.createElement = origCreateElement;

  assert(injectedOpenAnalysis, 'Open in Stock Analysis button was created and injected');
}

/* ================================================================
 * Test 6: Stock-specific extra sections inside <details>
 * ================================================================ */
console.log('\n--- Scanner-specific sections in expandable body ---');
{
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  /* Extra sections should be BEFORE </details> (inside the body) */
  const detailsEnd = html.indexOf('</details>');
  const beforeDetails = html.substring(0, detailsEnd);

  assert(beforeDetails.includes('SCANNER DETAILS'), 'SCANNER DETAILS section inside details');
  assert(beforeDetails.includes('Signals'), 'Signals row inside details');
  assert(beforeDetails.includes('trend_up'), 'Signal values present');
  assert(beforeDetails.includes('Trend'), 'Trend row inside details');
  assert(beforeDetails.includes('bullish'), 'Trend value present');
  assert(beforeDetails.includes('NOTES'), 'NOTES section inside details');
  assert(beforeDetails.includes('scannerIdeaNotes-0'), 'Notes placeholder for idx 0');
}

/* ================================================================
 * Test 7: Thesis and sparkline rendered in extra sections
 * ================================================================ */
console.log('\n--- Thesis and sparkline content ---');
{
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;
  const detailsEnd = html.indexOf('</details>');
  const beforeDetails = html.substring(0, detailsEnd);

  assert(beforeDetails.includes('Strong uptrend'), 'Thesis item 1 rendered');
  assert(beforeDetails.includes('Low volatility environment'), 'Thesis item 2 rendered');
  assert(beforeDetails.includes('Sparkline'), 'Sparkline section present');
}

/* ================================================================
 * Test 8: Mapper resolves stock_buy config metrics
 * ================================================================ */
console.log('\n--- Mapper resolves stock_buy metrics ---');
{
  const mapper = window.BenTradeOptionTradeCardModel;
  const tradeObj = {
    symbol: 'SPY',
    strategy_id: 'stock_buy',
    trade_key: 'SPY|NA|stock_buy|NA|NA|NA',
    price: 450.25,
    underlying_price: 450.25,
    composite_score: 82,
    trend_score: 78,
    momentum_score: 71,
    volatility_score: 65,
    pullback_score: 55,
    catalyst_score: 40,
    rsi14: 62.5,
    iv_rv_ratio: 1.2,
    ema20: 445.0,
    sma50: 440.0,
  };

  const model = mapper.map(tradeObj, 'stock_buy');
  assert(model !== null, 'mapper returned a model');
  assert(model.strategyId === 'stock_buy', 'strategyId: ' + model.strategyId);
  assert(model.symbol === 'SPY', 'symbol: ' + model.symbol);

  /* Check that core metrics resolved from the flattened root fields */
  const rankMetric = model.coreMetrics.find(m => m.key === 'rank_score');
  assert(rankMetric && rankMetric.value === 82, 'rank_score resolved to composite_score: ' + (rankMetric?.value));

  const trendMetric = model.coreMetrics.find(m => m.key === 'trend_score');
  assert(trendMetric && trendMetric.value === 78, 'trend_score resolved: ' + (trendMetric?.value));

  const momentumMetric = model.coreMetrics.find(m => m.key === 'momentum_score');
  assert(momentumMetric && momentumMetric.value === 71, 'momentum_score resolved: ' + (momentumMetric?.value));

  const ivRvMetric = model.coreMetrics.find(m => m.key === 'iv_rv_ratio');
  assert(ivRvMetric && ivRvMetric.value === 1.2, 'iv_rv_ratio resolved: ' + (ivRvMetric?.value));

  /* Detail fields */
  const rsiDetail = model.detailFields.find(m => m.key === 'rsi14');
  assert(rsiDetail && rsiDetail.value === 62.5, 'rsi14 detail resolved: ' + (rsiDetail?.value));
}

/* ================================================================
 * Test 9: No old/legacy markup patterns
 * ================================================================ */
console.log('\n--- No legacy markup patterns ---');
{
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  assert(!html.includes('data-action="toggle"'), 'no legacy toggle action');
  assert(!html.includes('data-action="run-model"'), 'no legacy run-model action (now model-analysis)');
  assert(!html.includes('data-action="send-workbench"'), 'no legacy send-workbench action (now workbench)');
  assert(!html.includes('trade-collapsible'), 'no legacy trade-collapsible class');
  assert(!html.includes('id="tradeBody-'), 'no legacy tradeBody- IDs');
  assert(!html.includes('id="chev-'), 'no legacy chev- IDs');
  assert(!html.includes('id="runBtn-'), 'no legacy runBtn- IDs');
}

/* ================================================================
 * Test 10: Model output renderer produces correct HTML
 * ================================================================ */
console.log('\n--- Model output HTML renderer ---');
{
  /* Test via model-analysis action flow:
     We intercept renderFullCard to access the _renderModelOutputHtml
     via the renderCandidates flow after a model run.
     Instead, test the actual model output indirectly by checking state. */

  /* Since _renderModelOutputHtml is internal, we verify its output
     through the runModelAnalysis flow (tested separately).
     Here we just verify the card has the model-output slot. */
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  assert(html.includes('trade-model-output'), 'model output container present');
  assert(html.includes('data-model-output'), 'data-model-output attribute present');
  assert(html.includes('display:none'), 'model output hidden by default');
}

/* ================================================================
 * Test 11: trade-card has data-idx attribute
 * ================================================================ */
console.log('\n--- Card has data-idx attribute ---');
{
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  assert(html.includes('data-idx="0"'), 'card has data-idx="0"');
  assert(html.includes('class="trade-card"'), 'card has trade-card class');
}

/* ================================================================
 * Test 12: trade-key is set on card and details
 * ================================================================ */
console.log('\n--- Trade key on card elements ---');
{
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  assert(html.includes('data-trade-key="SPY|NA|stock_buy|NA|NA|NA"'), 'trade key present on card elements');
}

/* ================================================================
 * Test 13: Multiple candidates render correctly
 * ================================================================ */
console.log('\n--- Multiple candidates ---');
{
  const multiPayload = {
    as_of: '2026-02-25T12:00:00Z',
    candidates: [
      { ...MOCK_CANDIDATE, symbol: 'SPY' },
      { ...MOCK_CANDIDATE, symbol: 'QQQ', composite_score: 75, price: 380.50 },
      { ...MOCK_CANDIDATE, symbol: 'IWM', composite_score: 68, price: 210.30 },
    ],
    notes: [],
    source_status: 'healthy',
  };

  window.BenTradeScanResultsCache.save('stockScanner', multiPayload);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  assert(html.includes('data-idx="0"'), '1st card data-idx=0');
  assert(html.includes('data-idx="1"'), '2nd card data-idx=1');
  assert(html.includes('data-idx="2"'), '3rd card data-idx=2');

  /* Count <details> elements — should be 3 */
  const detailsCount = (html.match(/<details/g) || []).length;
  assert(detailsCount === 3, '3 details elements for 3 candidates (got: ' + detailsCount + ')');

  /* Each should have trade-actions */
  const actionsCount = (html.match(/class="trade-actions"/g) || []).length;
  assert(actionsCount === 3, '3 trade-actions sections (got: ' + actionsCount + ')');
}

/* ================================================================
 * Test 14: Empty candidates shows fallback message
 * ================================================================ */
console.log('\n--- Empty candidates fallback ---');
{
  window.BenTradeScanResultsCache.save('stockScanner', { as_of: null, candidates: [] });
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  assert(html.includes('No scanner candidates returned') || html.includes('No scan yet'), 'empty state message shown');
}

/* ================================================================
 * Test 15: Rejected candidates are filtered
 * ================================================================ */
console.log('\n--- Rejected candidates filtered ---');
{
  localStorage.clear();
  localStorage.setItem('bentrade_scanner_rejected_v1', JSON.stringify(['SPY|stock_scanner']));

  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  /* SPY should be filtered out since it's rejected */
  assert(!html.includes('data-idx="0"') || html.includes('No scanner candidates'), 'rejected SPY is filtered');

  localStorage.removeItem('bentrade_scanner_rejected_v1');
}

/* ================================================================
 * Test 16: Copy trade key button present
 * ================================================================ */
console.log('\n--- Copy trade key button ---');
{
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  localStorage.removeItem('bentrade_scanner_rejected_v1');
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  assert(html.includes('data-copy-trade-key'), 'copy trade key button present');
}

/* ================================================================
 * Test 17: Metrics with missing data show N/A not errors
 * ================================================================ */
console.log('\n--- Missing metrics handled gracefully ---');
{
  const sparseCandidate = {
    symbol: 'TEST',
    price: null,
    composite_score: null,
    trend_score: undefined,
    signals: [],
    metrics: {},
  };
  window.BenTradeScanResultsCache.save('stockScanner', {
    as_of: null,
    candidates: [sparseCandidate],
    notes: [],
  });
  const root = buildRootEl();

  /* Should not throw */
  let threw = false;
  try {
    window.BenTradePages.initStockScanner(root);
  } catch(e){
    threw = true;
    console.error('  ERROR:', e.message);
  }
  assert(!threw, 'sparse candidate does not throw');

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;
  assert(html.includes('trade-card'), 'card still renders with sparse data');
}

/* ================================================================
 * Test 18: Extra scanner metrics (1D/20D change, 52W range, RV20)
 * ================================================================ */
console.log('\n--- Extra scanner metrics in detail section ---');
{
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  localStorage.removeItem('bentrade_scanner_rejected_v1');
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;
  const detailsEnd = html.indexOf('</details>');
  const beforeDetails = html.substring(0, detailsEnd);

  assert(beforeDetails.includes('1D Change'), '1D Change row present');
  assert(beforeDetails.includes('20D Change'), '20D Change row present');
  assert(beforeDetails.includes('52W Range'), '52W Range row present');
  assert(beforeDetails.includes('RV20'), 'RV20 row present');
  assert(beforeDetails.includes('380.5'), '52W low value present');
  assert(beforeDetails.includes('465.2'), '52W high value present');
}

/* ── Summary ── */
console.debug = _origDebug;
console.log('\n========================================');
console.log(`  ${passed} passed, ${failed} failed`);
console.log('========================================\n');

process.exit(failed > 0 ? 1 : 0);
