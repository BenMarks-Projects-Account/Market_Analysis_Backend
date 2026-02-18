/**
 * BenTrade — Shared formatting utilities.
 *
 * Canonical home for toNumber, num, dollars, money, pct, signedPct,
 * signed, escapeHtml.  Every page should delegate here instead of
 * defining local copies.
 *
 * Rule: null-in → "N/A" out.  No coercion to 0.
 */
window.BenTradeUtils = window.BenTradeUtils || {};

window.BenTradeUtils.format = (function(){
  'use strict';

  /** Convert any value to a finite Number, or null.  Never returns NaN. */
  function toNumber(value){
    if(value === null || value === undefined || value === '') return null;
    if(typeof value === 'boolean') return null;
    var n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  /** Plain number: "1234.56" or "N/A". */
  function num(value, digits){
    var n = toNumber(value);
    if(n === null) return 'N/A';
    return n.toFixed(digits != null ? digits : 2);
  }

  /** Dollar amount (unsigned): "$1234.56" or "N/A". */
  function dollars(value, digits){
    var n = toNumber(value);
    if(n === null) return 'N/A';
    return '$' + n.toFixed(digits != null ? digits : 2);
  }

  /** Signed dollar amount: "+$1.23" / "-$0.45" / "N/A". */
  function money(value){
    var n = toNumber(value);
    if(n === null) return 'N/A';
    var sign = n >= 0 ? '+' : '-';
    return sign + '$' + Math.abs(n).toFixed(2);
  }

  /** Percentage (unsigned): "75.0%" or "N/A".  Input is decimal (0.75). */
  function pct(value, digits){
    var n = toNumber(value);
    if(n === null) return 'N/A';
    return (n * 100).toFixed(digits != null ? digits : 1) + '%';
  }

  /** Signed percentage: "+25.0%" / "-5.0%" / "N/A".  Input is decimal. */
  function signedPct(value, digits){
    var n = toNumber(value);
    if(n === null) return 'N/A';
    var d = digits != null ? digits : 1;
    return (n >= 0 ? '+' : '') + (n * 100).toFixed(d) + '%';
  }

  /** Signed number: "+1.23" / "-0.45" / "N/A". */
  function signed(value, digits){
    var n = toNumber(value);
    if(n === null) return 'N/A';
    var text = n.toFixed(digits != null ? digits : 2);
    return n > 0 ? '+' + text : text;
  }

  /** HTML-escape a value for safe insertion into innerHTML. */
  function escapeHtml(value){
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  return {
    toNumber: toNumber,
    num: num,
    dollars: dollars,
    money: money,
    pct: pct,
    signedPct: signedPct,
    signed: signed,
    escapeHtml: escapeHtml,
  };
})();
