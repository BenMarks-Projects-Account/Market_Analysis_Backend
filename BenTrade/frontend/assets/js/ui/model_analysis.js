/**
 * BenTrade — Shared Model Analysis UI module.
 *
 * Single source of truth for:
 *  1) parseModelAnalysisResponse(raw)  — normalize any backend shape
 *  2) renderModelAnalysisHtml(result)  — unified HTML renderer
 *
 * Consumed by: home.js, strategy_dashboard_shell.js, stock_scanner.js
 *
 * Depends on: BenTradeUtils.format (for escapeHtml, num, toNumber)
 */
window.BenTradeModelAnalysis = (function(){
  'use strict';

  /* ── Utility refs ── */
  var fmtLib = window.BenTradeUtils && window.BenTradeUtils.format
    ? window.BenTradeUtils.format
    : {};
  var esc = fmtLib.escapeHtml || function(v){
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  };
  var toNumber = fmtLib.toNumber || function(v){
    if(v == null) return null;
    var n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  var fmtNum = fmtLib.num || function(v, d){
    if(v == null) return '—';
    var n = Number(v);
    if(!Number.isFinite(n)) return '—';
    return n.toFixed(d != null ? d : 2);
  };

  /* ================================================================
   *  parseModelAnalysisResponse
   *  Normalizes ANY backend shape into a stable contract.
   *  Input: raw API response, model_evaluation dict, or legacy shape
   *  Output: NormalizedModelAnalysis object (never undefined fields)
   * ================================================================ */
  function parseModelAnalysisResponse(raw){
    if(!raw || typeof raw !== 'object'){
      return _emptyResult('idle');
    }

    /* Detect wrapper shapes */
    var status = String(raw.status || '').toLowerCase();
    var me = raw.model_evaluation || raw;

    /* Pass-through for running / error */
    if(status === 'running'){
      return _emptyResult('running');
    }
    if(status === 'error'){
      return {
        status: 'error',
        recommendation: 'ERROR',
        score_0_100: null,
        confidence_0_1: null,
        thesis: String(raw.summary || me.summary || 'Model analysis failed').trim(),
        model_calculations: _emptyCalcs(),
        engine_calculations: null,
        cross_check_deltas: {},
        edge_assessment: _emptyEdge(),
        key_drivers: [],
        risk_review: _emptyRisk(),
        execution_assessment: null,
        data_quality_flags: [],
        missing_data: [],
        finishedAt: null,
      };
    }

    /* Success / available — extract all sections */
    var rec = String(me.model_recommendation || me.recommendation || 'UNKNOWN').toUpperCase();
    var score = toNumber(me.score_0_100);
    var conf = toNumber(me.confidence_0_1 != null ? me.confidence_0_1 : me.confidence);
    var thesis = String(me.thesis || me.summary || '').trim();

    /* Model calculations */
    var mc = (me.model_calculations && typeof me.model_calculations === 'object')
      ? me.model_calculations : {};
    var modelCalcs = {
      expected_value_est: toNumber(mc.expected_value_est),
      return_on_risk_est: toNumber(mc.return_on_risk_est),
      probability_est: toNumber(mc.probability_est),
      breakeven_est: toNumber(mc.breakeven_est),
      max_profit_per_share: toNumber(mc.max_profit_per_share),
      max_loss_per_share: toNumber(mc.max_loss_per_share),
      assumptions: Array.isArray(mc.assumptions) ? mc.assumptions : (mc.notes ? [String(mc.notes)] : []),
    };

    /* Engine calculations */
    var ec = raw.engine_calculations || me.engine_calculations || null;
    var engineCalcs = (ec && typeof ec === 'object') ? ec : null;

    /* Cross-check deltas */
    var ccd = (me.cross_check_deltas && typeof me.cross_check_deltas === 'object')
      ? me.cross_check_deltas : {};

    /* Edge assessment */
    var ea = (me.edge_assessment && typeof me.edge_assessment === 'object')
      ? me.edge_assessment : _emptyEdge();

    /* Key drivers — support both key_drivers and legacy key_factors */
    var drivers = [];
    var rawDrivers = me.key_drivers || me.key_factors || [];
    if(Array.isArray(rawDrivers)){
      drivers = rawDrivers.map(function(d){
        if(typeof d === 'string') return { factor: d, impact: 'neutral', evidence: '' };
        return {
          factor: String(d.factor || d.name || ''),
          impact: String(d.impact || 'neutral'),
          evidence: String(d.evidence || d.detail || ''),
        };
      });
    }

    /* Risk review */
    var rr = (me.risk_review && typeof me.risk_review === 'object') ? me.risk_review : {};
    var riskReview = {
      primary_risks: Array.isArray(rr.primary_risks) ? rr.primary_risks.map(String)
        : (rr.primary_risk ? [String(rr.primary_risk)] : []),
      liquidity_risks: Array.isArray(rr.liquidity_risks) ? rr.liquidity_risks.map(String) : [],
      assignment_risk: rr.assignment_risk || null,
      volatility_risk: rr.volatility_risk || null,
      event_risk: Array.isArray(rr.event_risk) ? rr.event_risk.map(String) : [],
      tail_scenario: rr.tail_scenario || null,
      data_quality_flag: rr.data_quality_flag || null,
    };

    /* Execution assessment */
    var exec = me.execution_assessment || me.execution_notes || null;
    var execAssessment = null;
    if(exec && typeof exec === 'object'){
      execAssessment = {
        fill_quality: exec.fill_quality || exec.fill_probability || null,
        slippage_risk: exec.slippage_risk || null,
        recommended_limit: toNumber(exec.recommended_limit),
        entry_notes: exec.entry_notes || exec.spread_concern || null,
      };
    }

    /* Data quality */
    var dqFlags = Array.isArray(me.data_quality_flags) ? me.data_quality_flags : [];
    var missingData = Array.isArray(me.missing_data) ? me.missing_data : [];

    /* Legacy stock scanner fields — fold in */
    if(!drivers.length && Array.isArray(me.key_factors)){
      drivers = me.key_factors.map(function(f){ return { factor: String(f), impact: 'neutral', evidence: '' }; });
    }
    if(!riskReview.primary_risks.length && Array.isArray(me.risks)){
      riskReview.primary_risks = me.risks.map(String);
    }

    return {
      status: status === 'success' || status === 'available' ? 'success' : (rec !== 'UNKNOWN' ? 'success' : 'idle'),
      recommendation: rec,
      score_0_100: score,
      confidence_0_1: conf,
      thesis: thesis,
      model_calculations: modelCalcs,
      engine_calculations: engineCalcs,
      cross_check_deltas: ccd,
      edge_assessment: ea,
      key_drivers: drivers,
      risk_review: riskReview,
      execution_assessment: execAssessment,
      data_quality_flags: dqFlags,
      missing_data: missingData,
      engine_gate_status: raw.engine_gate_status || null,
      finishedAt: Date.now(),
    };
  }

  function _emptyResult(status){
    return {
      status: status || 'idle',
      recommendation: 'N/A',
      score_0_100: null,
      confidence_0_1: null,
      thesis: '',
      model_calculations: _emptyCalcs(),
      engine_calculations: null,
      cross_check_deltas: {},
      edge_assessment: _emptyEdge(),
      key_drivers: [],
      risk_review: _emptyRisk(),
      execution_assessment: null,
      data_quality_flags: [],
      missing_data: [],
      finishedAt: null,
    };
  }

  function _emptyCalcs(){
    return {
      expected_value_est: null,
      return_on_risk_est: null,
      probability_est: null,
      breakeven_est: null,
      max_profit_per_share: null,
      max_loss_per_share: null,
      assumptions: [],
    };
  }

  function _emptyEdge(){
    return { premium_vs_risk: null, volatility_context: null, liquidity_quality: null, tail_risk_profile: null };
  }

  function _emptyRisk(){
    return { primary_risks: [], liquidity_risks: [], assignment_risk: null, volatility_risk: null, event_risk: [], tail_scenario: null, data_quality_flag: null };
  }

  /* ================================================================
   *  renderModelAnalysisHtml
   *  Unified HTML renderer for model analysis cards.
   *  Input: NormalizedModelAnalysis (from parseModelAnalysisResponse)
   *  Output: HTML string
   * ================================================================ */
  function renderModelAnalysisHtml(parsed){
    if(!parsed) return '';

    /* ── Running state ── */
    if(parsed.status === 'running'){
      return '<div class="model-analysis-card model-analysis-running" style="font-size:12px;padding:10px 12px;border:1px solid rgba(0,220,255,0.2);border-radius:8px;margin:6px 0;background:rgba(0,220,255,0.03);">'
        + '<div style="display:flex;align-items:center;gap:8px;">'
        + '<span class="home-scan-spinner" aria-hidden="true"></span>'
        + '<span style="color:var(--accent-cyan,#00dcff);font-weight:600;">Running model analysis\u2026</span>'
        + '</div>'
        + '<div style="font-size:10px;color:var(--muted);margin-top:4px;">Evaluating risk/reward, liquidity, tail risk\u2026</div>'
        + '</div>';
    }

    /* ── Error state ── */
    if(parsed.status === 'error'){
      var errMsg = parsed.thesis || 'Model analysis failed';
      return '<div class="model-analysis-card model-analysis-error" style="font-size:12px;color:#ff6b6b;padding:10px 12px;border:1px solid rgba(255,107,107,0.25);border-radius:8px;margin:6px 0;">'
        + '<div style="display:flex;align-items:center;gap:6px;">'
        + '<span>\u26A0</span>'
        + '<span>' + esc(errMsg) + '</span>'
        + '</div>'
        + '</div>';
    }

    /* ── Idle / not run ── */
    if(parsed.status === 'idle' || parsed.status === 'not_run'){
      return '';
    }

    /* ── Success ── */
    var rec = parsed.recommendation;
    var mc = parsed.model_calculations;
    var ec = parsed.engine_calculations;
    var ccd = parsed.cross_check_deltas;
    var ea = parsed.edge_assessment;
    var confVal = parsed.confidence_0_1;
    var confPct = confVal !== null ? (confVal * 100).toFixed(0) + '%' : '';
    var modelScore = parsed.score_0_100;

    /* Recommendation styles — shared by options (TAKE/PASS) and stock strategies (BUY/PASS) */
    var recStyles = {
      'TAKE':    { color: '#00dc78', glow: '0 0 12px rgba(0,220,120,0.35)', border: 'rgba(0,220,120,0.4)' },
      'ACCEPT':  { color: '#00dc78', glow: '0 0 12px rgba(0,220,120,0.35)', border: 'rgba(0,220,120,0.4)' },
      'BUY':     { color: '#00dc78', glow: '0 0 12px rgba(0,220,120,0.35)', border: 'rgba(0,220,120,0.4)' },
      'PASS':    { color: '#ff5a5a', glow: '0 0 12px rgba(255,90,90,0.35)',  border: 'rgba(255,90,90,0.4)' },
      'REJECT':  { color: '#ff5a5a', glow: '0 0 12px rgba(255,90,90,0.35)',  border: 'rgba(255,90,90,0.4)' },
      'WATCH':   { color: '#ffc83c', glow: '0 0 12px rgba(255,200,60,0.35)', border: 'rgba(255,200,60,0.4)' },
      'NEUTRAL': { color: '#b4b4c8', glow: 'none',                          border: 'rgba(180,180,200,0.3)' },
    };
    var style = recStyles[rec] || recStyles['NEUTRAL'];

    var html = '<div class="model-analysis-card model-analysis-success" style="font-size:12px;padding:10px 12px;border:1px solid ' + style.border + ';border-radius:8px;margin:6px 0;box-shadow:' + style.glow + ';">';

    /* ── Recommendation Banner ── */
    html += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">';
    html += '<span style="font-size:18px;font-weight:800;color:' + style.color + ';letter-spacing:0.5px;text-shadow:' + style.glow + ';">' + esc(rec) + '</span>';
    if(modelScore !== null){
      var mScoreColor = modelScore >= 60 ? '#00dc78' : modelScore >= 40 ? '#ffc83c' : '#ff5a5a';
      html += '<span style="font-size:16px;font-weight:700;color:' + mScoreColor + ';">' + modelScore + '<span style="font-size:11px;font-weight:400;color:var(--muted);">/100</span></span>';
    }
    if(confPct){
      html += '<span style="font-size:10px;font-weight:600;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:4px;padding:2px 6px;color:var(--text-secondary,#aaa);">' + esc(confPct) + ' conf</span>';
    }

    /* Engine gate status badge — shows scanner accept/reject independently of LLM recommendation */
    var egs = parsed.engine_gate_status;
    if(egs && typeof egs === 'object'){
      var egsPassed = egs.passed;
      var egsColor = egsPassed ? '#00dc78' : '#ff5a5a';
      var egsLabel = egsPassed ? 'ENGINE: ACCEPTED' : 'ENGINE: REJECTED';
      var egsBg = egsPassed ? 'rgba(0,220,120,0.08)' : 'rgba(255,90,90,0.08)';
      var egsBorder = egsPassed ? 'rgba(0,220,120,0.25)' : 'rgba(255,90,90,0.25)';
      html += '<span style="font-size:9px;font-weight:700;letter-spacing:0.5px;background:' + egsBg + ';border:1px solid ' + egsBorder + ';border-radius:4px;padding:2px 6px;color:' + egsColor + ';">' + egsLabel + '</span>';
    }

    /* Timestamp */
    if(parsed.finishedAt){
      var d = new Date(parsed.finishedAt);
      var timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      html += '<span style="margin-left:auto;font-size:9px;color:var(--muted);">Analyzed ' + esc(timeStr) + '</span>';
    }
    html += '</div>';

    /* ── Thesis ── */
    if(parsed.thesis){
      html += '<div style="color:var(--text-secondary,#ccc);line-height:1.5;margin-bottom:8px;font-style:italic;">' + esc(parsed.thesis) + '</div>';
    }

    /* ── Model Calculations ── */
    if(mc){
      var hasAny = mc.expected_value_est != null || mc.return_on_risk_est != null || mc.probability_est != null || mc.breakeven_est != null || mc.max_profit_per_share != null || mc.max_loss_per_share != null;
      if(hasAny){
        html += '<div style="margin-bottom:8px;padding:6px 8px;background:rgba(255,255,255,0.03);border-radius:6px;border:1px solid rgba(255,255,255,0.06);">';
        html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px;">Model Calculations</div>';
        html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 12px;font-size:11px;">';
        if(mc.max_profit_per_share != null) html += '<span style="color:var(--text-secondary,#bbb);">Max Profit: <b style="color:#00dc78;">' + fmtNum(mc.max_profit_per_share, 2) + '</b></span>';
        if(mc.max_loss_per_share != null) html += '<span style="color:var(--text-secondary,#bbb);">Max Loss: <b style="color:#ff5a5a;">' + fmtNum(mc.max_loss_per_share, 2) + '</b></span>';
        if(mc.expected_value_est != null) html += '<span style="color:var(--text-secondary,#bbb);">EV Est: <b style="color:' + (mc.expected_value_est >= 0 ? '#00dc78' : '#ff5a5a') + ';">' + fmtNum(mc.expected_value_est, 2) + '</b></span>';
        if(mc.return_on_risk_est != null) html += '<span style="color:var(--text-secondary,#bbb);">ROR Est: <b>' + (typeof mc.return_on_risk_est === 'number' ? (mc.return_on_risk_est * 100).toFixed(1) + '%' : mc.return_on_risk_est) + '</b></span>';
        if(mc.probability_est != null) html += '<span style="color:var(--text-secondary,#bbb);">Prob Est: <b>' + (typeof mc.probability_est === 'number' ? (mc.probability_est * 100).toFixed(1) + '%' : mc.probability_est) + '</b></span>';
        if(mc.breakeven_est != null) html += '<span style="color:var(--text-secondary,#bbb);">Breakeven: <b>' + fmtNum(mc.breakeven_est, 2) + '</b></span>';
        html += '</div>';

        /* Assumptions */
        if(mc.assumptions && mc.assumptions.length){
          html += '<div style="font-size:10px;color:var(--muted);margin-top:3px;font-style:italic;">Assumptions: ' + esc(mc.assumptions.join('; ')) + '</div>';
        }
        html += '</div>';
      }
    }

    /* ── Engine vs Model Comparison ── */
    if(ec && typeof ec === 'object' && mc){
      var comparisons = [
        { label: 'Max Profit', engine: ec.max_profit_per_share, model: mc.max_profit_per_share },
        { label: 'Max Loss', engine: ec.max_loss_per_share, model: mc.max_loss_per_share },
        { label: 'EV/Share', engine: ec.ev_per_share, model: mc.expected_value_est },
        { label: 'ROR', engine: ec.return_on_risk, model: mc.return_on_risk_est, isPct: true },
        { label: 'Breakeven', engine: ec.breakeven, model: mc.breakeven_est },
        { label: 'POP', engine: ec.pop_proxy, model: mc.probability_est, isPct: true },
      ].filter(function(c){ return c.engine != null || c.model != null; });

      if(comparisons.length){
        html += '<div style="margin-bottom:8px;padding:6px 8px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.15);">';
        html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px;">Engine vs Model</div>';
        html += '<div style="display:grid;grid-template-columns:auto 1fr 1fr;gap:2px 10px;font-size:10px;">';
        html += '<span style="color:var(--muted);font-weight:600;"></span><span style="color:var(--muted);font-weight:600;">Engine</span><span style="color:var(--muted);font-weight:600;">Model</span>';
        for(var i = 0; i < comparisons.length; i++){
          var c = comparisons[i];
          var eStr = _fmtCompVal(c.engine, c.isPct);
          var mStr = _fmtCompVal(c.model, c.isPct);
          var deltaStyle = '';
          if(c.engine != null && c.model != null && c.engine !== 0){
            var delta = Math.abs((c.model - c.engine) / c.engine);
            if(delta > 0.10) deltaStyle = 'color:#ffc83c;font-weight:600;';
          }
          html += '<span style="color:var(--text-secondary,#bbb);">' + esc(c.label) + '</span>';
          html += '<span style="color:var(--text-secondary,#bbb);">' + eStr + '</span>';
          html += '<span style="' + (deltaStyle || 'color:var(--text-secondary,#bbb);') + '">' + mStr + '</span>';
        }
        html += '</div>';
        html += '</div>';
      }
    } else if(ec && typeof ec === 'object'){
      /* Engine only — no model calculations */
      var engineItems = [
        { label: 'Max Profit', val: ec.max_profit_per_share },
        { label: 'Max Loss', val: ec.max_loss_per_share },
        { label: 'EV/Share', val: ec.ev_per_share },
        { label: 'ROR', val: ec.return_on_risk, isPct: true },
        { label: 'Breakeven', val: ec.breakeven },
        { label: 'POP', val: ec.pop_proxy, isPct: true },
      ].filter(function(e){ return e.val != null; });

      if(engineItems.length){
        html += '<div style="margin-bottom:8px;padding:6px 8px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.15);">';
        html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px;">Engine Calculations</div>';
        html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 12px;font-size:11px;">';
        for(var j = 0; j < engineItems.length; j++){
          var ei = engineItems[j];
          var fmtV = ei.isPct ? (ei.val * 100).toFixed(1) + '%' : fmtNum(ei.val, 2);
          html += '<span style="color:var(--text-secondary,#bbb);">' + esc(ei.label) + ': <b>' + fmtV + '</b></span>';
        }
        html += '</div>';
        html += '</div>';
      }
    }

    /* ── Cross-Check Deltas ── */
    if(ccd && typeof ccd === 'object'){
      var alertEntries = Object.entries(ccd).filter(function(kv){ return kv[1] && typeof kv[1] === 'object' && kv[1].note; });
      if(alertEntries.length){
        html += '<div style="margin-bottom:6px;font-size:10px;">';
        html += '<span style="color:var(--muted);font-weight:600;">Cross-check alerts: </span>';
        var alerts = alertEntries.map(function(kv){
          var metric = kv[0], info = kv[1];
          var dp = info.delta_pct != null ? ' (' + (info.delta_pct * 100).toFixed(0) + '%)' : '';
          return metric + dp + ': ' + info.note;
        });
        html += '<span style="color:#ffc83c;">' + esc(alerts.join(' | ')) + '</span>';
        html += '</div>';
      }
    }

    /* ── Edge Assessment ── */
    if(ea){
      var edgeColorMap = { positive: '#00dc78', favorable: '#00dc78', high: '#00dc78', low: '#00dc78',
                           neutral: '#b4b4c8', medium: '#ffc83c', moderate: '#ffc83c',
                           negative: '#ff5a5a', unfavorable: '#ff5a5a', elevated: '#ff5a5a' };
      var edgeItems = [
        { label: 'Premium/Risk', val: ea.premium_vs_risk },
        { label: 'Vol Context', val: ea.volatility_context },
        { label: 'Liquidity', val: ea.liquidity_quality },
        { label: 'Tail Risk', val: ea.tail_risk_profile },
      ].filter(function(item){ return !!item.val; });

      if(edgeItems.length){
        html += '<div style="margin-bottom:8px;display:flex;flex-wrap:wrap;gap:6px;">';
        for(var k = 0; k < edgeItems.length; k++){
          var eItem = edgeItems[k];
          var eColor = edgeColorMap[String(eItem.val).toLowerCase()] || '#b4b4c8';
          html += '<span style="font-size:10px;padding:2px 7px;border-radius:4px;border:1px solid ' + eColor + '44;color:' + eColor + ';background:' + eColor + '0d;">' + esc(eItem.label) + ': ' + esc(eItem.val) + '</span>';
        }
        html += '</div>';
      }
    }

    /* ── Key Drivers ── */
    var drivers = parsed.key_drivers;
    if(drivers && drivers.length){
      html += '<div style="margin-bottom:8px;">';
      html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:3px;">Key Drivers</div>';
      var impactColors = { positive: '#00dc78', negative: '#ff5a5a', neutral: '#b4b4c8' };
      var driverLimit = Math.min(drivers.length, 5);
      for(var di = 0; di < driverLimit; di++){
        var d = drivers[di];
        if(d.factor){
          var ic = impactColors[d.impact] || '#b4b4c8';
          html += '<div style="font-size:11px;color:var(--text-secondary,#bbb);line-height:1.5;padding-left:8px;border-left:2px solid ' + ic + ';margin-bottom:3px;">';
          html += '<span style="color:' + ic + ';font-weight:600;">' + esc(d.factor) + '</span>';
          if(d.evidence) html += '<div style="font-size:10px;color:var(--muted);margin-top:1px;">' + esc(d.evidence) + '</div>';
          html += '</div>';
        } else {
          html += '<div style="font-size:11px;color:var(--text-secondary,#bbb);line-height:1.4;padding-left:8px;border-left:2px solid #b4b4c8;margin-bottom:3px;">' + esc(String(d.factor || d)) + '</div>';
        }
      }
      html += '</div>';
    }

    /* ── Risk Review ── */
    var rr = parsed.risk_review;
    if(rr){
      var hasRiskContent = (rr.primary_risks && rr.primary_risks.length) || rr.assignment_risk || rr.volatility_risk || rr.tail_scenario;
      if(hasRiskContent){
        html += '<div style="margin-bottom:8px;padding:6px 8px;background:rgba(255,90,90,0.04);border-radius:6px;border:1px solid rgba(255,90,90,0.1);">';
        html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:3px;">Risk Review</div>';
        if(rr.primary_risks && rr.primary_risks.length){
          for(var ri = 0; ri < rr.primary_risks.length; ri++){
            html += '<div style="font-size:11px;color:#ff8a8a;line-height:1.4;">\u2022 ' + esc(rr.primary_risks[ri]) + '</div>';
          }
        }
        if(rr.liquidity_risks && rr.liquidity_risks.length){
          for(var li = 0; li < rr.liquidity_risks.length; li++){
            html += '<div style="font-size:11px;color:#ffc83c;line-height:1.4;">\u2022 Liquidity: ' + esc(rr.liquidity_risks[li]) + '</div>';
          }
        }
        var riskLevelColor = { low: '#00dc78', medium: '#ffc83c', high: '#ff5a5a' };
        var riskBadges = [];
        if(rr.assignment_risk) riskBadges.push({ label: 'Assignment', val: rr.assignment_risk });
        if(rr.volatility_risk) riskBadges.push({ label: 'Volatility', val: rr.volatility_risk });
        if(riskBadges.length){
          html += '<div style="display:flex;gap:6px;margin-top:4px;">';
          for(var bi = 0; bi < riskBadges.length; bi++){
            var b = riskBadges[bi];
            var bc = riskLevelColor[b.val] || '#b4b4c8';
            html += '<span style="font-size:10px;padding:1px 6px;border-radius:3px;border:1px solid ' + bc + '44;color:' + bc + ';">' + esc(b.label) + ': ' + esc(b.val) + '</span>';
          }
          html += '</div>';
        }
        if(rr.event_risk && rr.event_risk.length){
          html += '<div style="font-size:10px;color:var(--muted);margin-top:3px;">Events: ' + esc(rr.event_risk.join(', ')) + '</div>';
        }
        if(rr.tail_scenario && !(rr.primary_risks && rr.primary_risks.length)){
          html += '<div style="font-size:11px;color:#ffc83c;line-height:1.4;">\u2022 Tail: ' + esc(rr.tail_scenario) + '</div>';
        }
        if(rr.data_quality_flag){
          html += '<div style="font-size:10px;color:var(--muted);margin-top:3px;">\u26A0 ' + esc(rr.data_quality_flag) + '</div>';
        }
        html += '</div>';
      }
    }

    /* ── Execution Assessment ── */
    var execA = parsed.execution_assessment;
    if(execA){
      var hasExec = execA.fill_quality || execA.slippage_risk || execA.recommended_limit != null || execA.entry_notes;
      if(hasExec){
        html += '<div style="margin-bottom:8px;">';
        html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:3px;">Execution</div>';
        var execParts = [];
        if(execA.fill_quality) execParts.push('Fill: ' + execA.fill_quality);
        if(execA.slippage_risk) execParts.push('Slippage: ' + execA.slippage_risk);
        if(execA.recommended_limit != null) execParts.push('Limit: ' + fmtNum(execA.recommended_limit, 2));
        if(execParts.length) html += '<div style="font-size:11px;color:var(--text-secondary,#bbb);">' + esc(execParts.join(' \u00B7 ')) + '</div>';
        if(execA.entry_notes) html += '<div style="font-size:10px;color:var(--muted);margin-top:2px;">' + esc(execA.entry_notes) + '</div>';
        html += '</div>';
      }
    }

    /* ── Data Quality / Missing Data ── */
    var hasDQ = (parsed.data_quality_flags && parsed.data_quality_flags.length) || (parsed.missing_data && parsed.missing_data.length);
    if(hasDQ){
      html += '<div style="font-size:10px;color:var(--muted);padding-top:4px;border-top:1px solid rgba(255,255,255,0.06);">';
      if(parsed.data_quality_flags && parsed.data_quality_flags.length){
        html += '<div>\u26A0 Data flags: ' + esc(parsed.data_quality_flags.join(', ')) + '</div>';
      }
      if(parsed.missing_data && parsed.missing_data.length){
        html += '<div>Missing: ' + esc(parsed.missing_data.join(', ')) + '</div>';
      }
      html += '</div>';
    }

    html += '</div>';
    return html;
  }

  /* ── Helpers ── */
  function _fmtCompVal(v, isPct){
    if(v == null) return '<span style="color:var(--muted);">\u2014</span>';
    return isPct ? (v * 100).toFixed(1) + '%' : fmtNum(v, 2);
  }

  /* ================================================================
   *  PUBLIC API
   * ================================================================ */
  return {
    parse: parseModelAnalysisResponse,
    render: renderModelAnalysisHtml,
  };
})();
