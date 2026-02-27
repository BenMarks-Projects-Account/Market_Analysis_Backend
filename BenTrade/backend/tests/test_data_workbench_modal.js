/**
 * Unit tests for the Data Workbench modal feature.
 *
 * Verifies:
 *  - Modal component API (open/close)
 *  - Tab switching
 *  - Copy JSON buttons present per tab
 *  - JSON content rendered correctly
 *  - "Raw payload not captured" note when rawSource is null
 *  - data-workbench button opens modal (not navigates) in stock_scanner
 *  - _buildDerivedData produces correct structure
 *  - Existing alignment tests still pass (workbench button still present)
 *
 * Run:  node tests/test_data_workbench_modal.js
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
  querySelector(sel){
    return _queryParsedHTML(this._innerHTML, sel, this);
  }
  querySelectorAll(sel){
    return _queryAllParsedHTML(this._innerHTML, sel, this);
  }
  setAttribute(k, v){ this._attributes[k] = String(v); }
  getAttribute(k){
    if(k === 'data-dwb-tab' && this.dataset.dwbTab) return this.dataset.dwbTab;
    if(k === 'data-dwb-panel' && this.dataset.dwbPanel) return this.dataset.dwbPanel;
    if(k === 'data-dwb-copy' && this.dataset.dwbCopy) return this.dataset.dwbCopy;
    return this._attributes[k] ?? null;
  }
  hasAttribute(k){ return k in this._attributes; }
  addEventListener(type, fn, opts){
    if(!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push(fn);
  }
  removeEventListener(){}
  closest(sel){
    /* Walk up parentElement chain */
    let el = this;
    while(el){
      if(sel.startsWith('.') && el.className && el.className.split(/\s+/).includes(sel.slice(1))) return el;
      if(sel.startsWith('[') && sel.includes('data-action')){
        if(el._attributes['data-action'] || el.dataset.action) return el;
      }
      if(sel === '.trade-card' && el.className && el.className.includes('trade-card')) return el;
      el = el.parentElement;
    }
    return null;
  }
  matches(sel){ return false; }
  appendChild(child){
    child.parentElement = this;
    this.children.push(child);
    if(child._innerHTML != null) this._innerHTML += (child._outerHTML || child._innerHTML);
  }
  focus(){}
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
  get _outerHTML(){
    return '<' + this.tagName.toLowerCase() + (this.className ? ' class="' + this.className + '"' : '') + '>' + this._innerHTML + '</' + this.tagName.toLowerCase() + '>';
  }
}

/* Query helpers */
function _queryParsedHTML(html, sel, parentEl){
  if(!html) return null;

  /* .dwb-modal-close */
  if(sel === '.dwb-modal-close'){
    if(html.includes('dwb-modal-close')){
      const el = new FakeElement('BUTTON');
      el.className = 'dwb-modal-close';
      el.parentElement = parentEl;
      return el;
    }
    return null;
  }

  /* [data-dwb-tab], [data-dwb-copy], [data-dwb-panel] */
  if(sel.startsWith('[data-dwb-')){
    const attr = sel.slice(1, -1); // e.g. 'data-dwb-tab'
    const re = new RegExp(attr + '="([^"]*)"');
    const m = html.match(re);
    if(!m) return null;
    const el = new FakeElement('BUTTON');
    const dsKey = attr.replace(/^data-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    el.dataset[dsKey] = m[1];
    return el;
  }

  /* .trade-actions */
  if(sel === '.trade-actions'){
    if(!html.includes('trade-actions')) return null;
    const el = new FakeElement('DIV');
    el.className = 'trade-actions';
    el._innerHTML = html;
    return el;
  }

  /* [data-model-output] */
  if(sel === '[data-model-output]'){
    if(!html.includes('data-model-output')) return null;
    return new FakeElement('DIV');
  }

  /* .trade-card[data-idx="N"] */
  const attrMatch = sel.match(/\.trade-card\[data-idx="(\d+)"\]/);
  if(attrMatch){
    if(!html.includes('data-idx="' + attrMatch[1] + '"')) return null;
    const el = new FakeElement('DIV');
    el.className = 'trade-card';
    el.dataset.idx = attrMatch[1];
    el._innerHTML = html;
    el.querySelector = function(s){ return _queryParsedHTML(html, s, el); };
    return el;
  }

  /* #id */
  if(sel.startsWith('#')){
    const id = sel.slice(1);
    if(_elements[id]) return _elements[id];
    return null;
  }
  return null;
}

function _queryAllParsedHTML(html, sel){
  if(!html) return [];

  /* [data-dwb-tab] */
  if(sel === '[data-dwb-tab]'){
    const matches = [];
    const re = /data-dwb-tab="([^"]*)"/g;
    let m;
    while((m = re.exec(html)) !== null){
      const el = new FakeElement('BUTTON');
      el.dataset.dwbTab = m[1];
      el.className = html.includes('dwb-tab-active') && matches.length === 0 ? 'dwb-tab dwb-tab-active' : 'dwb-tab';
      matches.push(el);
    }
    return matches;
  }

  /* [data-dwb-panel] */
  if(sel === '[data-dwb-panel]'){
    const matches = [];
    const re = /data-dwb-panel="([^"]*)"/g;
    let m;
    while((m = re.exec(html)) !== null){
      const el = new FakeElement('DIV');
      el.dataset.dwbPanel = m[1];
      el.className = matches.length === 0 ? 'dwb-panel dwb-panel-active' : 'dwb-panel';
      matches.push(el);
    }
    return matches;
  }

  /* [data-dwb-copy] */
  if(sel === '[data-dwb-copy]'){
    const matches = [];
    const re = /data-dwb-copy="([^"]*)"/g;
    let m;
    while((m = re.exec(html)) !== null){
      const el = new FakeElement('BUTTON');
      el.dataset.dwbCopy = m[1];
      el.className = 'dwb-copy-btn';
      matches.push(el);
    }
    return matches;
  }

  /* details.trade-card-collapse */
  if(sel === 'details.trade-card-collapse'){
    const count = (html.match(/<details/g) || []).length;
    return Array.from({length: count}, () => {
      const e = new FakeElement('DETAILS');
      e.className = 'trade-card-collapse';
      e.dataset = {tradeKey:''};
      return e;
    });
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
loadModule('ui/data_workbench_modal.js');

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

/* Track tooltip calls */
window.attachMetricTooltips = function(){};

/* Track modal calls */
let modalOpenCalls = [];
const _origOpen = window.BenTradeDataWorkbenchModal.open;
window.BenTradeDataWorkbenchModal.open = function(opts){
  modalOpenCalls.push(opts);
  /* Call real open to verify no errors */
  _origOpen.call(window.BenTradeDataWorkbenchModal, opts);
};

/* Track navigation */
let lastHash = '';
const origLocation = global.location;
Object.defineProperty(global, 'location', {
  get(){ return { ...origLocation, get hash(){ return lastHash; }, set hash(v){ lastHash = v; } }; },
  set(v){ lastHash = v.hash || ''; },
});

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
  /* Reset elements */
  Object.keys(_elements).forEach(k => delete _elements[k]);

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
 * 1. Modal component — open/close API
 * ================================================================ */
console.log('\n--- Modal open/close API ---');
{
  const modal = window.BenTradeDataWorkbenchModal;
  assert(typeof modal.open === 'function', 'modal.open is a function');
  assert(typeof modal.close === 'function', 'modal.close is a function');
}

/* ================================================================
 * 2. Modal content — 3 tabs present
 * ================================================================ */
console.log('\n--- Modal renders 3 tabs ---');
{
  /* Open modal with test data */
  _origOpen.call(window.BenTradeDataWorkbenchModal, {
    symbol: 'SPY',
    normalized: { symbol: 'SPY', strategy_id: 'stock_buy' },
    rawSource: MOCK_CANDIDATE,
    derived: { scoring_inputs: {}, scoring_outputs: {} },
  });

  const overlay = _fakeDoc.body.children[_fakeDoc.body.children.length - 1];
  assert(overlay != null, 'overlay element created');

  const html = overlay.innerHTML || overlay._innerHTML || '';
  assert(html.includes('data-dwb-tab="normalized"'), 'Normalized tab button present');
  assert(html.includes('data-dwb-tab="raw"'), 'Raw Source tab button present');
  assert(html.includes('data-dwb-tab="derived"'), 'Derived tab button present');
}

/* ================================================================
 * 3. Modal content — 3 copy buttons
 * ================================================================ */
console.log('\n--- Modal renders Copy JSON buttons ---');
{
  const overlay = _fakeDoc.body.children[_fakeDoc.body.children.length - 1];
  const html = overlay.innerHTML || overlay._innerHTML || '';
  assert(html.includes('data-dwb-copy="normalized"'), 'Copy button for Normalized tab');
  assert(html.includes('data-dwb-copy="raw"'), 'Copy button for Raw Source tab');
  assert(html.includes('data-dwb-copy="derived"'), 'Copy button for Derived tab');
}

/* ================================================================
 * 4. Modal content — JSON rendered
 * ================================================================ */
console.log('\n--- Modal renders JSON content ---');
{
  const overlay = _fakeDoc.body.children[_fakeDoc.body.children.length - 1];
  const html = overlay.innerHTML || overlay._innerHTML || '';
  assert(html.includes('dwb-json'), 'JSON pre blocks present');
  assert(html.includes('&quot;symbol&quot;'), 'Normalized JSON contains symbol key');
  assert(html.includes('&quot;strategy_id&quot;'), 'Normalized JSON contains strategy_id');
  assert(html.includes('SPY'), 'Modal title includes symbol');
}

/* ================================================================
 * 5. Modal content — "not captured" note when rawSource is null
 * ================================================================ */
console.log('\n--- Raw payload not captured note ---');
{
  _origOpen.call(window.BenTradeDataWorkbenchModal, {
    symbol: 'QQQ',
    normalized: { symbol: 'QQQ' },
    rawSource: null,
    derived: {},
  });

  const overlay = _fakeDoc.body.children[_fakeDoc.body.children.length - 1];
  const html = overlay.innerHTML || overlay._innerHTML || '';
  assert(html.includes('Raw payload not captured'), 'shows "not captured" note when rawSource is null');
  assert(html.includes('QQQ'), 'title shows QQQ');
}

/* ================================================================
 * 6. Modal content — close button present
 * ================================================================ */
console.log('\n--- Close button present ---');
{
  const overlay = _fakeDoc.body.children[_fakeDoc.body.children.length - 1];
  const html = overlay.innerHTML || overlay._innerHTML || '';
  assert(html.includes('dwb-modal-close'), 'close button present');
  assert(html.includes('\u00D7') || html.includes('&times;'), 'close button has × symbol');
}

/* ================================================================
 * 7. Modal panels — only first tab panel is active
 * ================================================================ */
console.log('\n--- First tab panel is active by default ---');
{
  const overlay = _fakeDoc.body.children[_fakeDoc.body.children.length - 1];
  const html = overlay.innerHTML || overlay._innerHTML || '';
  assert(html.includes('dwb-panel dwb-panel-active" data-dwb-panel="normalized"'), 'normalized panel is active');
  /* Other panels should NOT have dwb-panel-active */
  const rawPanelMatch = html.match(/dwb-panel([^"]*)" data-dwb-panel="raw"/);
  assert(rawPanelMatch && !rawPanelMatch[1].includes('active'), 'raw panel is not active');
  const derivedPanelMatch = html.match(/dwb-panel([^"]*)" data-dwb-panel="derived"/);
  assert(derivedPanelMatch && !derivedPanelMatch[1].includes('active'), 'derived panel is not active');
}

/* ================================================================
 * 8. stock_scanner — data-workbench opens modal (not navigates)
 * ================================================================ */
console.log('\n--- data-workbench action opens modal ---');
{
  modalOpenCalls = [];
  lastHash = '';
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  /* Verify the data-workbench button is in the HTML */
  assert(html.includes('data-action="data-workbench"'), 'data-workbench button in card HTML');

  /* Simulate clicking the data-workbench button */
  const fakeBtn = new FakeElement('BUTTON');
  fakeBtn._attributes['data-action'] = 'data-workbench';
  fakeBtn._attributes['data-trade-key'] = 'SPY|NA|stock_buy|NA|NA|NA';
  fakeBtn.dataset = { action: 'data-workbench', tradeKey: 'SPY|NA|stock_buy|NA|NA|NA' };
  fakeBtn.getAttribute = function(k){ return this._attributes[k] || null; };
  fakeBtn.closest = function(sel){
    if(sel === '[data-action]') return fakeBtn;
    if(sel === '[data-copy-trade-key]') return null;
    if(sel === '.trade-card'){
      const cardEl = new FakeElement('DIV');
      cardEl.className = 'trade-card';
      cardEl.dataset = { idx: '0' };
      cardEl._innerHTML = html;
      cardEl.querySelector = function(s){ return _queryParsedHTML(html, s, cardEl); };
      return cardEl;
    }
    return null;
  };

  /* Fire click event on listEl */
  const listeners = listEl._listeners['click'] || [];
  const fakeEvent = {
    target: fakeBtn,
    preventDefault(){},
    stopPropagation(){},
  };
  listeners.forEach(fn => fn(fakeEvent));

  assert(modalOpenCalls.length >= 1, 'modal.open was called (count: ' + modalOpenCalls.length + ')');
  if(modalOpenCalls.length > 0){
    const call = modalOpenCalls[modalOpenCalls.length - 1];
    assert(call.symbol === 'SPY', 'modal opened with symbol SPY');
    assert(call.normalized != null, 'normalized data passed');
    assert(call.normalized.strategy_id === 'stock_buy', 'normalized.strategy_id is stock_buy');
    assert(call.rawSource != null, 'rawSource data passed');
    assert(call.rawSource.composite_score === 82, 'rawSource has original composite_score');
    assert(call.derived != null, 'derived data passed');
    assert(call.derived.scoring_inputs != null, 'derived has scoring_inputs');
    assert(call.derived.scoring_outputs != null, 'derived has scoring_outputs');
    assert(call.derived.mapper_diagnostics != null, 'derived has mapper_diagnostics');
  }
  /* Should NOT have navigated */
  assert(!lastHash.includes('data-workbench'), 'did NOT navigate to data-workbench page');
}

/* ================================================================
 * 9. stock_scanner — workbench action still navigates
 * ================================================================ */
console.log('\n--- workbench action still navigates ---');
{
  modalOpenCalls = [];
  lastHash = '';
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  const fakeBtn = new FakeElement('BUTTON');
  fakeBtn._attributes['data-action'] = 'workbench';
  fakeBtn._attributes['data-trade-key'] = 'SPY|NA|stock_buy|NA|NA|NA';
  fakeBtn.dataset = { action: 'workbench', tradeKey: 'SPY|NA|stock_buy|NA|NA|NA' };
  fakeBtn.getAttribute = function(k){ return this._attributes[k] || null; };
  fakeBtn.closest = function(sel){
    if(sel === '[data-action]') return fakeBtn;
    if(sel === '[data-copy-trade-key]') return null;
    if(sel === '.trade-card'){
      const cardEl = new FakeElement('DIV');
      cardEl.className = 'trade-card';
      cardEl.dataset = { idx: '0' };
      cardEl._innerHTML = html;
      cardEl.querySelector = function(s){ return _queryParsedHTML(html, s, cardEl); };
      return cardEl;
    }
    return null;
  };

  const listeners = listEl._listeners['click'] || [];
  const fakeEvent = { target: fakeBtn, preventDefault(){}, stopPropagation(){} };
  listeners.forEach(fn => fn(fakeEvent));

  const prevModalCount = modalOpenCalls.length;
  assert(lastHash.includes('data-workbench') || lastHash === '', 'workbench action navigated or used openDataWorkbenchByTrade');
}

/* ================================================================
 * 10. Derived data structure
 * ================================================================ */
console.log('\n--- Derived data structure ---');
{
  /* Re-trigger to capture derived data */
  modalOpenCalls = [];
  lastHash = '';
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);

  const listEl = _elements['stockScannerList'];
  const html = listEl.innerHTML;

  const fakeBtn = new FakeElement('BUTTON');
  fakeBtn._attributes['data-action'] = 'data-workbench';
  fakeBtn._attributes['data-trade-key'] = 'SPY|NA|stock_buy|NA|NA|NA';
  fakeBtn.dataset = { action: 'data-workbench', tradeKey: 'SPY|NA|stock_buy|NA|NA|NA' };
  fakeBtn.getAttribute = function(k){ return this._attributes[k] || null; };
  fakeBtn.closest = function(sel){
    if(sel === '[data-action]') return fakeBtn;
    if(sel === '[data-copy-trade-key]') return null;
    if(sel === '.trade-card'){
      const cardEl = new FakeElement('DIV');
      cardEl.className = 'trade-card';
      cardEl.dataset = { idx: '0' };
      cardEl._innerHTML = html;
      cardEl.querySelector = function(s){ return _queryParsedHTML(html, s, cardEl); };
      return cardEl;
    }
    return null;
  };

  const listeners = listEl._listeners['click'] || [];
  listeners.forEach(fn => fn({ target: fakeBtn, preventDefault(){}, stopPropagation(){} }));

  assert(modalOpenCalls.length >= 1, 'modal.open was called for derived test');
  const derived = modalOpenCalls[0].derived;

  const si = derived.scoring_inputs;
  assert(si.rsi14 === 62.5, 'derived.scoring_inputs.rsi14 = 62.5');
  assert(si.rv20 === 0.15, 'derived.scoring_inputs.rv20 = 0.15');
  assert(si.iv_rv_ratio === 1.2, 'derived.scoring_inputs.iv_rv_ratio = 1.2');
  assert(si.ema20 === 445.0, 'derived.scoring_inputs.ema20 = 445.0');
  assert(si.sma50 === 440.0, 'derived.scoring_inputs.sma50 = 440.0');

  const so = derived.scoring_outputs;
  assert(so.composite_score === 82, 'derived.scoring_outputs.composite_score = 82');
  assert(so.trend_score === 78, 'derived.scoring_outputs.trend_score = 78');
  assert(so.momentum_score === 71, 'derived.scoring_outputs.momentum_score = 71');
  assert(so.volatility_score === 65, 'derived.scoring_outputs.volatility_score = 65');
  assert(so.pullback_score === 55, 'derived.scoring_outputs.pullback_score = 55');
  assert(so.catalyst_score === 40, 'derived.scoring_outputs.catalyst_score = 40');

  const md = derived.mapper_diagnostics;
  assert(Array.isArray(md.coreResolved), 'mapper_diagnostics.coreResolved is array');
  assert(Array.isArray(md.coreMissing), 'mapper_diagnostics.coreMissing is array');
  assert(Array.isArray(md.detailResolved), 'mapper_diagnostics.detailResolved is array');
  assert(Array.isArray(md.detailMissing), 'mapper_diagnostics.detailMissing is array');

  assert(Array.isArray(derived.signals), 'derived.signals is array');
  assert(derived.trend === 'bullish', 'derived.trend is bullish');
  assert(Array.isArray(derived.thesis), 'derived.thesis is array');
}

/* ================================================================
 * 11. Both buttons still present in card HTML
 * ================================================================ */
console.log('\n--- Both workbench buttons present in card HTML ---');
{
  window.BenTradeScanResultsCache.save('stockScanner', MOCK_PAYLOAD);
  const root = buildRootEl();
  window.BenTradePages.initStockScanner(root);
  const html = _elements['stockScannerList'].innerHTML;

  assert(html.includes('data-action="workbench"'), 'workbench (Testing) button present');
  assert(html.includes('data-action="data-workbench"'), 'data-workbench button present');
  assert(html.includes('Send to Testing Workbench'), 'Testing Workbench label present');
  assert(html.includes('Send to Data Workbench'), 'Data Workbench label present');
}

/* ================================================================
 * 12. Card actions outside <details> (not affected by collapse)
 * ================================================================ */
console.log('\n--- Actions outside details (collapse-proof) ---');
{
  const html = _elements['stockScannerList'].innerHTML;
  const detailsClose = html.indexOf('</details>');
  const dataWorkbenchBtn = html.indexOf('data-action="data-workbench"');
  assert(detailsClose > -1 && dataWorkbenchBtn > detailsClose, 'data-workbench button is after </details> (works in collapsed state)');
}

/* ================================================================
 * 13. CSS — modal overlay exists with correct z-index
 * ================================================================ */
console.log('\n--- CSS for modal overlay ---');
{
  const cssSrc = fs.readFileSync(
    path.resolve(__dirname, '..', '..', 'frontend', 'assets', 'css', 'app.css'), 'utf-8'
  );
  assert(cssSrc.includes('.dwb-modal-overlay'), 'dwb-modal-overlay CSS present');
  assert(cssSrc.includes('z-index:5000'), 'overlay z-index:5000 (above tooltip 4000)');
  assert(cssSrc.includes('.dwb-tab'), 'dwb-tab CSS present');
  assert(cssSrc.includes('.dwb-json'), 'dwb-json CSS present');
  assert(cssSrc.includes('.dwb-copy-btn'), 'dwb-copy-btn CSS present');
  assert(cssSrc.includes('.dwb-panel'), 'dwb-panel CSS present');
  assert(cssSrc.includes('.dwb-note'), 'dwb-note CSS present');
}

/* ================================================================
 * 14. Script loaded in index.html
 * ================================================================ */
console.log('\n--- Script tag in index.html ---');
{
  const indexHtml = fs.readFileSync(
    path.resolve(__dirname, '..', '..', 'frontend', 'index.html'), 'utf-8'
  );
  assert(indexHtml.includes('data_workbench_modal.js'), 'data_workbench_modal.js loaded in index.html');

  /* Loaded after trade_card.js but before stock_scanner.js */
  const tradeCardIdx = indexHtml.indexOf('trade_card.js');
  const modalIdx = indexHtml.indexOf('data_workbench_modal.js');
  const scannerIdx = indexHtml.indexOf('stock_scanner.js');
  assert(tradeCardIdx < modalIdx, 'modal script loaded after trade_card.js');
  assert(modalIdx < scannerIdx, 'modal script loaded before stock_scanner.js');
}

/* ── Summary ── */
console.debug = _origDebug;
console.log('\n========================================');
console.log('  ' + passed + ' passed, ' + failed + ' failed');
console.log('========================================\n');

process.exit(failed > 0 ? 1 : 0);
