"""Institutional 13F engine — deterministic scoring for smart money positioning.

Computes per-stock, per-sector, and overall institutional positioning
signals from 13F holder data weighted by filer tier.

Scoring logic
-------------
Per-stock score:
    weighted_net_delta = sum(shares_delta × filer_tier_weight) across
                         all tracked filers for the most recent quarter
    stock_score = percentile_rank(weighted_net_delta / outstanding_shares)
                  across the universe, scaled 0-100

Sector aggregation:
    sector_score = median(stock_score for symbols in sector)
    sector_momentum = sector_score_this_quarter - sector_score_prior_quarter

Overall pillar:
    overall_score = weighted_average(sector_scores, weights=sector_market_cap)

Classification:
    > 65 → "bullish"     < 35 → "bearish"     else → "neutral"
"""

from __future__ import annotations

import logging
import statistics
from typing import Any

logger = logging.getLogger(__name__)

# ── Label bands ─────────────────────────────────────────────────────

_LABEL_BANDS: list[tuple[float, str, str]] = [
    (80, "Strong Institutional Buying", "Strong Buy"),
    (65, "Moderate Institutional Buying", "Buying"),
    (50, "Neutral Institutional Flow", "Neutral"),
    (35, "Moderate Institutional Selling", "Selling"),
    (0, "Strong Institutional Selling", "Strong Sell"),
]


def _score_to_labels(score: float) -> tuple[str, str]:
    """Return (full_label, short_label) for a score value."""
    for threshold, full, short in _LABEL_BANDS:
        if score >= threshold:
            return full, short
    return _LABEL_BANDS[-1][1], _LABEL_BANDS[-1][2]


def _classify(score: float) -> str:
    if score > 65:
        return "bullish"
    if score < 35:
        return "bearish"
    return "neutral"


# ── Percentile helper ───────────────────────────────────────────────

def _percentile_rank(values: list[float], target: float) -> float:
    """Compute percentile rank of target within values (0-100 scale)."""
    if not values:
        return 50.0
    below = sum(1 for v in values if v < target)
    equal = sum(1 for v in values if v == target)
    n = len(values)
    return ((below + 0.5 * equal) / n) * 100


# ── Per-stock scoring ───────────────────────────────────────────────

def compute_stock_scores(
    universe: list[str],
    holdings_data: dict[str, list[dict[str, Any]]],
    float_data: dict[str, dict[str, Any]],
    filer_weights: dict[str, float],
) -> dict[str, dict[str, Any]]:
    """Compute weighted 13F score per stock.

    Parameters
    ----------
    universe : list[str]
        The full symbol list.
    holdings_data : dict[str, list[dict]]
        Per-symbol list of 13F holder records.
        Each record: {investorName, cik, shares, value, change,
                      changePercentage, ...}
    float_data : dict[str, dict]
        Per-symbol float data: {outstandingShares, floatShares, ...}
    filer_weights : dict[str, float]
        CIK → weight mapping (tier1=3.0, tier2=1.0).

    Returns
    -------
    dict[symbol, {score, raw_delta, weighted_delta, outstanding_shares,
                   tier1_activity, holder_count}]
    """
    raw_deltas: dict[str, float] = {}
    stock_details: dict[str, dict[str, Any]] = {}

    for symbol in universe:
        holders = holdings_data.get(symbol, [])
        outstanding = 0
        float_info = float_data.get(symbol)
        if float_info and isinstance(float_info, dict):
            outstanding = int(float_info.get("outstandingShares", 0) or 0)

        weighted_delta = 0.0
        tier1_buys = 0
        tier1_sells = 0
        holder_count = len(holders)

        for holder in holders:
            cik = str(holder.get("cik", "")).zfill(10)
            change_shares = float(holder.get("change", 0) or 0)
            weight = filer_weights.get(cik, 0.0)
            if weight <= 0:
                continue
            weighted_delta += change_shares * weight
            if weight >= 3.0:
                if change_shares > 0:
                    tier1_buys += 1
                elif change_shares < 0:
                    tier1_sells += 1

        # Normalize by outstanding shares
        if outstanding > 0:
            normalized = weighted_delta / (outstanding * 0.01)
        else:
            normalized = 0.0

        raw_deltas[symbol] = normalized
        stock_details[symbol] = {
            "raw_delta": round(normalized, 4),
            "weighted_delta": round(weighted_delta, 2),
            "outstanding_shares": outstanding,
            "tier1_buys": tier1_buys,
            "tier1_sells": tier1_sells,
            "holder_count": holder_count,
        }

    # Percentile ranking across universe
    all_deltas = list(raw_deltas.values())
    for symbol in universe:
        delta = raw_deltas.get(symbol, 0.0)
        score = _percentile_rank(all_deltas, delta)
        stock_details.setdefault(symbol, {})["score"] = round(score, 2)

    return stock_details


# ── Sector aggregation ──────────────────────────────────────────────

def compute_sector_heatmap(
    stock_scores: dict[str, dict[str, Any]],
    sector_map: dict[str, str],
    market_caps: dict[str, float],
    prior_sector_scores: dict[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    """Aggregate stock scores into sector-level heatmap.

    Parameters
    ----------
    stock_scores : dict[symbol, {..., score}]
    sector_map : dict[symbol, GICS_sector_name]
    market_caps : dict[symbol, market_cap_float]
    prior_sector_scores : dict[sector, prior_quarter_score] | None

    Returns
    -------
    dict[sector, {score, momentum, net_flow_dollars, unique_funds_buying,
                   symbol_count}]
    """
    sector_scores: dict[str, list[float]] = {}
    sector_flow: dict[str, float] = {}
    sector_buyers: dict[str, set[str]] = {}
    sector_counts: dict[str, int] = {}

    for symbol, details in stock_scores.items():
        sector = sector_map.get(symbol, "Unknown")
        score = details.get("score", 50.0)
        sector_scores.setdefault(sector, []).append(score)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

        weighted_delta = details.get("weighted_delta", 0.0)
        sector_flow[sector] = sector_flow.get(sector, 0.0) + weighted_delta

        if details.get("tier1_buys", 0) > 0:
            sector_buyers.setdefault(sector, set()).add(symbol)

    heatmap: dict[str, dict[str, Any]] = {}
    for sector, scores in sector_scores.items():
        median_score = round(statistics.median(scores), 2) if scores else 50.0
        prior = (prior_sector_scores or {}).get(sector, 50.0)
        momentum = round(median_score - prior, 2)

        heatmap[sector] = {
            "score": median_score,
            "momentum": momentum,
            "net_flow_weighted": round(sector_flow.get(sector, 0.0), 2),
            "unique_funds_buying": len(sector_buyers.get(sector, set())),
            "symbol_count": sector_counts.get(sector, 0),
        }

    return heatmap


# ── Notable moves ───────────────────────────────────────────────────

def compute_notable_moves(
    stock_scores: dict[str, dict[str, Any]],
    holdings_data: dict[str, list[dict[str, Any]]],
    sector_map: dict[str, str],
    filer_weights: dict[str, float],
    tier1_ciks: set[str],
) -> dict[str, Any]:
    """Surface top new positions, exits, increases, decreases, and consensus.

    Returns
    -------
    dict with: top_new_positions, top_exits, top_increased_stakes,
               top_decreased_stakes, consensus_buys, consensus_sells
    """
    new_positions: list[dict[str, Any]] = []
    exits: list[dict[str, Any]] = []
    increased: list[dict[str, Any]] = []
    decreased: list[dict[str, Any]] = []
    # Per-symbol: which tier1 funds are buying/selling
    tier1_buyers: dict[str, list[str]] = {}
    tier1_sellers: dict[str, list[str]] = {}

    for symbol, holders in holdings_data.items():
        sector = sector_map.get(symbol, "Unknown")
        for holder in holders:
            cik = str(holder.get("cik", "")).zfill(10)
            weight = filer_weights.get(cik, 0.0)
            if weight <= 0:
                continue

            change = float(holder.get("change", 0) or 0)
            shares = int(holder.get("shares", 0) or 0)
            name = holder.get("investorName", "Unknown")
            change_pct = float(holder.get("changePercentage", 0) or 0)
            filing_date = holder.get("dateReported", "")
            weighted_score = abs(change * weight)

            entry = {
                "symbol": symbol,
                "sector": sector,
                "top_filer": name,
                "shares": shares,
                "change": int(change),
                "change_pct": round(change_pct, 2),
                "filing_date": str(filing_date).split("T")[0] if filing_date else "",
                "weighted_score": round(weighted_score, 2),
            }

            is_tier1 = cik in tier1_ciks

            if change > 0 and shares == change:
                # New position
                new_positions.append(entry)
            elif change < 0 and shares == 0:
                # Fully exited
                exits.append(entry)
            elif change > 0:
                increased.append(entry)
            elif change < 0:
                decreased.append(entry)

            if is_tier1:
                if change > 0:
                    tier1_buyers.setdefault(symbol, []).append(name)
                elif change < 0:
                    tier1_sellers.setdefault(symbol, []).append(name)

    # Sort by weighted_score desc, take top 5
    new_positions.sort(key=lambda x: x["weighted_score"], reverse=True)
    exits.sort(key=lambda x: x["weighted_score"], reverse=True)
    increased.sort(key=lambda x: x["weighted_score"], reverse=True)
    decreased.sort(key=lambda x: x["weighted_score"], reverse=True)

    # Consensus: 3+ tier-1 funds same direction
    consensus_buys = [
        {"symbol": sym, "sector": sector_map.get(sym, "Unknown"),
         "fund_count": len(funds), "funds": funds[:5]}
        for sym, funds in tier1_buyers.items()
        if len(funds) >= 3
    ]
    consensus_sells = [
        {"symbol": sym, "sector": sector_map.get(sym, "Unknown"),
         "fund_count": len(funds), "funds": funds[:5]}
        for sym, funds in tier1_sellers.items()
        if len(funds) >= 3
    ]

    return {
        "top_new_positions": new_positions[:5],
        "top_exits": exits[:5],
        "top_increased_stakes": increased[:5],
        "top_decreased_stakes": decreased[:5],
        "consensus_buys": sorted(
            consensus_buys, key=lambda x: x["fund_count"], reverse=True,
        ),
        "consensus_sells": sorted(
            consensus_sells, key=lambda x: x["fund_count"], reverse=True,
        ),
    }


# ── Main compute ────────────────────────────────────────────────────

def compute_13f_scores(
    universe: list[str],
    holdings_data: dict[str, list[dict[str, Any]]],
    float_data: dict[str, dict[str, Any]],
    sector_map: dict[str, str],
    market_caps: dict[str, float],
    filer_weights: dict[str, float],
    tier1_ciks: set[str],
    prior_sector_scores: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute full 13F pillar output.

    Returns
    -------
    dict with: score, label, short_label, confidence_score,
               classification, sector_heatmap, notable_moves,
               stock_scores (top 20), diagnostics, warnings
    """
    warnings: list[str] = []

    # Data quality checks
    symbols_with_data = sum(1 for s in universe if s in holdings_data and holdings_data[s])
    coverage_pct = (symbols_with_data / len(universe) * 100) if universe else 0

    if coverage_pct < 10:
        warnings.append(f"Low 13F coverage: {coverage_pct:.0f}% of universe has holder data")

    # Step 1: per-stock scores
    stock_scores = compute_stock_scores(
        universe, holdings_data, float_data, filer_weights,
    )

    # Step 2: sector heatmap
    sector_heatmap = compute_sector_heatmap(
        stock_scores, sector_map, market_caps, prior_sector_scores,
    )

    # Step 3: notable moves
    notable_moves = compute_notable_moves(
        stock_scores, holdings_data, sector_map, filer_weights, tier1_ciks,
    )

    # Step 4: overall score = market-cap-weighted sector average
    total_cap = 0.0
    weighted_sum = 0.0
    for sector, info in sector_heatmap.items():
        # Sum market caps for all symbols in this sector
        sector_cap = sum(
            market_caps.get(sym, 0)
            for sym, sec in sector_map.items()
            if sec == sector
        )
        weighted_sum += info["score"] * sector_cap
        total_cap += sector_cap

    overall_score = round(weighted_sum / total_cap, 2) if total_cap > 0 else 50.0
    classification = _classify(overall_score)
    label, short_label = _score_to_labels(overall_score)

    # Confidence based on data coverage and filer match rate
    confidence = min(100.0, coverage_pct * 1.2)
    if len(sector_heatmap) < 5:
        confidence *= 0.7
        warnings.append(f"Only {len(sector_heatmap)} sectors represented")

    # Top 20 stocks by absolute score deviation from 50 (most interesting)
    top_stocks = sorted(
        [
            {"symbol": sym, "score": d["score"], **{k: d[k] for k in
             ("tier1_buys", "tier1_sells", "holder_count", "raw_delta")
             if k in d}}
            for sym, d in stock_scores.items()
        ],
        key=lambda x: abs(x["score"] - 50),
        reverse=True,
    )[:20]

    return {
        "score": overall_score,
        "label": label,
        "short_label": short_label,
        "confidence_score": round(confidence, 2),
        "classification": classification,
        "pillars": {
            "sector_positioning": {
                "score": overall_score,
                "submetrics": [
                    {"name": sec, "value": info["score"], "score": info["score"]}
                    for sec, info in sorted(
                        sector_heatmap.items(),
                        key=lambda x: x[1]["momentum"],
                        reverse=True,
                    )
                ],
                "explanation": f"Sector-weighted 13F positioning across {len(sector_heatmap)} sectors",
                "warnings": [],
            },
        },
        "sector_heatmap": dict(sorted(
            sector_heatmap.items(),
            key=lambda x: x[1]["momentum"],
            reverse=True,
        )),
        "notable_moves": notable_moves,
        "top_stocks": top_stocks,
        "summary": _build_summary(overall_score, classification, sector_heatmap, notable_moves),
        "trader_takeaway": _build_takeaway(classification, notable_moves),
        "warnings": warnings,
        "diagnostics": {
            "universe_size": len(universe),
            "symbols_with_data": symbols_with_data,
            "coverage_pct": round(coverage_pct, 2),
            "sectors_covered": len(sector_heatmap),
            "tier1_filers_matched": sum(
                1 for cik in tier1_ciks if cik in filer_weights
            ),
            "total_filers_tracked": len(filer_weights),
            "consensus_buys_count": len(notable_moves.get("consensus_buys", [])),
            "consensus_sells_count": len(notable_moves.get("consensus_sells", [])),
        },
    }


def _build_summary(
    score: float,
    classification: str,
    heatmap: dict[str, dict[str, Any]],
    notable: dict[str, Any],
) -> str:
    """Build human-readable summary."""
    top_sector = max(heatmap.items(), key=lambda x: x[1]["momentum"])[0] if heatmap else "N/A"
    bottom_sector = min(heatmap.items(), key=lambda x: x[1]["momentum"])[0] if heatmap else "N/A"
    consensus_buys = len(notable.get("consensus_buys", []))

    parts = [f"13F institutional positioning is {classification} (score: {score:.0f}/100)."]
    if top_sector != "N/A":
        parts.append(f"Strongest sector momentum: {top_sector}.")
    if bottom_sector != "N/A" and bottom_sector != top_sector:
        parts.append(f"Weakest: {bottom_sector}.")
    if consensus_buys > 0:
        parts.append(f"{consensus_buys} consensus buy signal(s) from 3+ tier-1 funds.")

    return " ".join(parts)


def _build_takeaway(
    classification: str,
    notable: dict[str, Any],
) -> str:
    """Build trader-actionable takeaway."""
    if classification == "bullish":
        return "Smart money is accumulating. Consider aligning swing entries with sectors showing strongest institutional buying."
    if classification == "bearish":
        return "Smart money is distributing. Caution on new longs; consider tightening stops on existing positions."
    return "Institutional flows are balanced. No strong directional bias from 13F data."
