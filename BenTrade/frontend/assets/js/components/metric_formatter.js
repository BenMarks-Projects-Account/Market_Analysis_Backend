/* ===================================================================
   Metric Format Registry
   Maps metric names to display format types so values render correctly.
   Pattern: window.BenTradeComponents.formatMetric(name, value)
   =================================================================== */
(function() {
    'use strict';

    window.BenTradeComponents = window.BenTradeComponents || {};

    // Format types:
    //   "decimal_pct"  — value is a decimal (0.6876), display as percentage (68.76%)
    //   "raw_pct"      — value is already a percentage (15.59), display with % suffix
    //   "ratio"        — value is a ratio (1.6688), display as plain number (1.67)
    //   "score"        — 0-100 score, display as integer (85)
    //   "multiplier"   — value like 12.5x, display with x suffix
    //   "currency"     — dollar value, display with $ and commas
    //   "count"        — integer count, display with commas
    //   "string"       — display as-is with snake_case → Title Case
    //   "auto"         — infer based on value (default fallback)

    var METRIC_FORMATS = {
        // Business Quality
        roic: "decimal_pct",
        gross_margin: "decimal_pct",
        op_margin: "decimal_pct",
        net_margin: "decimal_pct",
        fcf_yield: "decimal_pct",
        rev_stability: "decimal_pct",

        // Operational Health
        sga_efficiency: "decimal_pct",
        debt_to_ebitda: "multiplier",
        interest_coverage: "multiplier",
        current_ratio: "ratio",
        cash_conversion: "ratio",
        altman_z: "ratio",

        // Capital Allocation
        roic_wacc_spread: "decimal_pct",
        share_trend: "decimal_pct",
        payout_ratio: "decimal_pct",
        dividend_sustain: "decimal_pct",
        rd_intensity: "decimal_pct",
        wacc_est: "decimal_pct",
        insider_activity: "score",
        insider_net: "string",
        insider_score: "score",

        // Growth
        revenue_cagr_3y: "decimal_pct",
        revenue_cagr_5y: "decimal_pct",
        fcf_growth: "decimal_pct",
        eps_growth_yoy: "raw_pct",
        margin_trend: "decimal_pct",

        // Valuation
        ev_ebitda: "multiplier",
        pe_ratio: "multiplier",
        pfcf: "multiplier",
        accruals_ratio: "decimal_pct",

        // Analyst counts
        analyst_strong_buy: "count",
        analyst_buy: "count",
        analyst_hold: "count",
        analyst_sell: "count",
        analyst_strong_sell: "count"
    };

    function formatMetric(name, value) {
        if (value === null || value === undefined || value === "") {
            return "\u2014";
        }

        var format = METRIC_FORMATS[name] || "auto";

        switch (format) {
            case "decimal_pct":
                return _fmtDecimalPct(value);
            case "raw_pct":
                return _fmtRawPct(value);
            case "ratio":
                return _fmtRatio(value);
            case "multiplier":
                return _fmtMultiplier(value);
            case "score":
                return _fmtScore(value);
            case "currency":
                return _fmtCurrency(value);
            case "count":
                return _fmtCount(value);
            case "string":
                return _fmtString(value);
            case "auto":
            default:
                return _fmtAuto(name, value);
        }
    }

    function _fmtDecimalPct(v) {
        var n = Number(v);
        if (isNaN(n)) return "\u2014";
        return (n * 100).toFixed(2) + "%";
    }

    function _fmtRawPct(v) {
        var n = Number(v);
        if (isNaN(n)) return "\u2014";
        return n.toFixed(2) + "%";
    }

    function _fmtRatio(v) {
        var n = Number(v);
        if (isNaN(n)) return "\u2014";
        return n.toFixed(2);
    }

    function _fmtMultiplier(v) {
        var n = Number(v);
        if (isNaN(n)) return "\u2014";
        return n.toFixed(2) + "x";
    }

    function _fmtScore(v) {
        var n = Number(v);
        if (isNaN(n)) return "\u2014";
        return Math.round(n).toString();
    }

    function _fmtCurrency(v) {
        var n = Number(v);
        if (isNaN(n)) return "\u2014";
        if (Math.abs(n) >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
        if (Math.abs(n) >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
        if (Math.abs(n) >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
        return "$" + n.toLocaleString();
    }

    function _fmtCount(v) {
        var n = Number(v);
        if (isNaN(n)) return "\u2014";
        return Math.round(n).toLocaleString();
    }

    function _fmtString(v) {
        if (v === null || v === undefined) return "\u2014";
        return String(v)
            .split("_")
            .map(function(w) { return w.charAt(0).toUpperCase() + w.slice(1); })
            .join(" ");
    }

    function _fmtAuto(name, v) {
        if (typeof v === 'string') return v;
        var n = Number(v);
        if (isNaN(n)) return String(v);
        // Heuristic: if abs < 1, likely a decimal ratio → show as pct
        if (Math.abs(n) < 1) return (n * 100).toFixed(2) + "%";
        // Large numbers → currency
        if (Math.abs(n) >= 1e6) return _fmtCurrency(n);
        return n.toFixed(2);
    }

    function formatMetricLabel(name) {
        if (!name) return "";
        return name
            .split("_")
            .map(function(w) { return w.charAt(0).toUpperCase() + w.slice(1); })
            .join(" ");
    }

    window.BenTradeComponents.formatMetric = formatMetric;
    window.BenTradeComponents.formatMetricLabel = formatMetricLabel;
    window.BenTradeComponents.METRIC_FORMATS = METRIC_FORMATS;
})();
