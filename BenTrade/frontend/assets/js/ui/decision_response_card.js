/**
 * BenTrade — Decision Response Card Renderer v1
 *
 * Renders a Final Decision Response Contract object into a stable,
 * reviewable UI card using BenTrade visual language.
 *
 * Usage:
 *   var html = BenTradeDecisionCard.render(responseObj);
 *   document.getElementById('target').innerHTML = html;
 *
 *   // Or mount directly:
 *   BenTradeDecisionCard.mount(responseObj, document.getElementById('target'));
 *
 * The renderer handles complete, partial, and insufficient_data responses
 * safely — missing optional fields never break layout.
 */
window.BenTradeDecisionCard = (function () {
  'use strict';

  // ── Decision → CSS class mapping ──────────────────────────────────────

  var BADGE_CLASS = {
    approve:           'dr-badge-approve',
    cautious_approve:  'dr-badge-cautious_approve',
    watchlist:         'dr-badge-watchlist',
    reject:            'dr-badge-reject',
    insufficient_data: 'dr-badge-insufficient_data',
  };

  var CONVICTION_CLASS = {
    high:     'dr-conviction-high',
    moderate: 'dr-conviction-moderate',
    low:      'dr-conviction-low',
    none:     'dr-conviction-none',
  };

  var SIZE_CLASS = {
    normal:  'dr-size-normal',
    reduced: 'dr-size-reduced',
    minimal: 'dr-size-minimal',
    none:    'dr-size-none',
  };

  // ── Indicator value → colour class ────────────────────────────────────

  var ALIGNMENT_COLOUR = {
    aligned:   'dr-val-positive',
    good:      'dr-val-positive',
    clear:     'dr-val-positive',
    low:       'dr-val-positive',
    neutral:   'dr-val-neutral',
    acceptable:'dr-val-neutral',
    conditional:'dr-val-caution',
    moderate:  'dr-val-caution',
    elevated:  'dr-val-caution',
    misaligned:'dr-val-negative',
    poor:      'dr-val-negative',
    restricted:'dr-val-negative',
    blocked:   'dr-val-negative',
    high:      'dr-val-negative',
    unknown:   'dr-val-unknown',
  };

  // ── Source label → footer CSS class ───────────────────────────────────

  var SOURCE_CLASS = {
    model:       'dr-source-model',
    manual:      'dr-source-manual',
    placeholder: 'dr-source-placeholder',
  };

  // ── Helpers ───────────────────────────────────────────────────────────

  function esc(str) {
    if (str == null) return '';
    var d = document.createElement('div');
    d.appendChild(document.createTextNode(String(str)));
    return d.innerHTML;
  }

  function safeList(val) {
    return Array.isArray(val) ? val : [];
  }

  function formatLabel(val) {
    if (!val) return '—';
    return String(val).replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  function indicatorColour(val) {
    if (!val) return 'dr-val-unknown';
    return ALIGNMENT_COLOUR[String(val).toLowerCase()] || 'dr-val-unknown';
  }

  function formatTimestamp(iso) {
    if (!iso) return '';
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return esc(iso);
      return d.toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
      });
    } catch (_) {
      return esc(iso);
    }
  }

  // ── Render sub-sections ───────────────────────────────────────────────

  function renderHeader(r) {
    var decision = r.decision || 'insufficient_data';
    var label    = r.decision_label || formatLabel(decision);
    var conv     = r.conviction || 'none';
    var ts       = r.generated_at || '';
    var badgeCls = BADGE_CLASS[decision] || BADGE_CLASS.insufficient_data;
    var convCls  = CONVICTION_CLASS[conv] || CONVICTION_CLASS.none;

    return (
      '<div class="dr-header">' +
        '<div class="dr-header-left">' +
          '<span class="dr-badge ' + badgeCls + '">' + esc(label) + '</span>' +
          '<span class="dr-conviction ' + convCls + '">' + esc(formatLabel(conv)) + ' conviction</span>' +
        '</div>' +
        '<span class="dr-timestamp">' + esc(formatTimestamp(ts)) + '</span>' +
      '</div>'
    );
  }

  function renderIndicators(r) {
    var items = [
      { label: 'Market Alignment', value: r.market_alignment },
      { label: 'Portfolio Fit',    value: r.portfolio_fit },
      { label: 'Policy Alignment', value: r.policy_alignment },
      { label: 'Event Risk',       value: r.event_risk },
    ];
    if (r.time_horizon) {
      items.push({ label: 'Time Horizon', value: r.time_horizon });
    }

    var html = '<div class="dr-indicators">';
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      var cls = indicatorColour(item.value);
      html += (
        '<div class="dr-indicator">' +
          '<span class="dr-indicator-label">' + esc(item.label) + '</span>' +
          '<span class="dr-indicator-value ' + cls + '">' + esc(formatLabel(item.value)) + '</span>' +
        '</div>'
      );
    }
    html += '</div>';
    return html;
  }

  function renderSummary(r) {
    var text = r.summary || '';
    if (!text) return '';
    return '<div class="dr-summary">' + esc(text) + '</div>';
  }

  function renderInsufficientBanner() {
    return (
      '<div class="dr-insufficient-banner">' +
        '<div class="dr-insufficient-text">' +
          '⚠ Insufficient data to render a complete decision. ' +
          'Some sections may be missing or degraded.' +
        '</div>' +
      '</div>'
    );
  }

  function renderReasons(r) {
    var rFor     = safeList(r.reasons_for);
    var rAgainst = safeList(r.reasons_against);
    if (rFor.length === 0 && rAgainst.length === 0) return '';

    var html = '<div class="dr-reasons">';

    // Reasons For
    html += '<div class="dr-reasons-col">';
    html += '<div class="dr-section-title">Reasons For</div>';
    if (rFor.length === 0) {
      html += '<div style="font-size:11px;color:var(--muted);opacity:0.6;">None listed</div>';
    } else {
      html += '<ul class="dr-reason-list">';
      for (var i = 0; i < rFor.length; i++) {
        html += '<li class="dr-reason-item"><span class="dr-reason-dot for"></span>' + esc(rFor[i]) + '</li>';
      }
      html += '</ul>';
    }
    html += '</div>';

    // Reasons Against
    html += '<div class="dr-reasons-col">';
    html += '<div class="dr-section-title">Reasons Against</div>';
    if (rAgainst.length === 0) {
      html += '<div style="font-size:11px;color:var(--muted);opacity:0.6;">None listed</div>';
    } else {
      html += '<ul class="dr-reason-list">';
      for (var j = 0; j < rAgainst.length; j++) {
        html += '<li class="dr-reason-item"><span class="dr-reason-dot against"></span>' + esc(rAgainst[j]) + '</li>';
      }
      html += '</ul>';
    }
    html += '</div>';

    html += '</div>';
    return html;
  }

  function renderKeyRisks(r) {
    var risks = safeList(r.key_risks);
    if (risks.length === 0) return '';

    var html = '<div class="dr-section-title">Key Risks</div>';
    html += '<ul class="dr-risk-list">';
    for (var i = 0; i < risks.length; i++) {
      html += '<li class="dr-risk-item"><span class="dr-risk-icon">⚠</span>' + esc(risks[i]) + '</li>';
    }
    html += '</ul>';
    return html;
  }

  function renderSizeGuidance(r) {
    var size = r.size_guidance || 'normal';
    var cls  = SIZE_CLASS[size] || SIZE_CLASS.normal;
    return (
      '<div class="dr-size-row">' +
        '<span class="dr-size-label">Size Guidance</span>' +
        '<span class="dr-size-value ' + cls + '">' + esc(formatLabel(size)) + '</span>' +
      '</div>'
    );
  }

  function renderNotes(title, notes) {
    var list = safeList(notes);
    if (list.length === 0) return '';
    var html = '<div class="dr-section-title">' + esc(title) + '</div>';
    html += '<ul class="dr-notes">';
    for (var i = 0; i < list.length; i++) {
      html += '<li class="dr-note-item"><span class="dr-note-bullet">›</span>' + esc(list[i]) + '</li>';
    }
    html += '</ul>';
    return html;
  }

  function renderWarnings(r) {
    var flags = safeList(r.warning_flags);
    if (flags.length === 0) return '';
    var html = '<div class="dr-warnings">';
    html += '<div class="dr-warnings-title">⚠ Warnings</div>';
    html += '<ul class="dr-warning-list">';
    for (var i = 0; i < flags.length; i++) {
      html += '<li class="dr-warning-item"><span class="dr-warning-icon">•</span>' + esc(flags[i]) + '</li>';
    }
    html += '</ul>';
    html += '</div>';
    return html;
  }

  function renderFooter(r) {
    var version = (r.response_version || '?');
    var meta    = r.metadata || {};
    var source  = meta.source || 'manual';
    var srcCls  = SOURCE_CLASS[source] || SOURCE_CLASS.manual;
    return (
      '<div class="dr-footer">' +
        '<span>v' + esc(version) + '</span>' +
        '<span class="dr-footer-tag ' + srcCls + '">' + esc(source) + '</span>' +
      '</div>'
    );
  }

  // ── Main render ───────────────────────────────────────────────────────

  function render(response) {
    var r = response || {};
    var decision = r.decision || 'insufficient_data';

    var html = '<div class="dr-card" data-decision="' + esc(decision) + '">';
    html += renderHeader(r);
    html += '<div class="dr-body">';
    html += renderInsufficientBanner();
    html += renderIndicators(r);
    html += renderSummary(r);
    html += renderReasons(r);
    html += renderKeyRisks(r);
    html += renderSizeGuidance(r);
    html += renderNotes('Invalidation Notes', r.invalidation_notes);
    html += renderNotes('Monitoring Notes', r.monitoring_notes);
    html += renderWarnings(r);
    html += '</div>';
    html += renderFooter(r);
    html += '</div>';
    return html;
  }

  function mount(response, container) {
    if (!container) return;
    container.innerHTML = render(response);
  }

  // ── Public API ────────────────────────────────────────────────────────

  return {
    render: render,
    mount:  mount,
  };
})();
