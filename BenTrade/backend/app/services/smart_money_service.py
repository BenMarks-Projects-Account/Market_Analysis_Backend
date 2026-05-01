"""Smart Money analysis service for on-demand evaluator.

Fetches institutional ownership (13F), insider transactions (Form 4),
mutual fund holdings, and congressional trades from FMP Ultimate endpoints.
Computes signals, scoring contributions, and a template-based synthesis.

Data sources (all FMP Ultimate tier):
  - /institutional-ownership/extract-analytics/holder  (13F per-holder)
  - /institutional-ownership/symbol-positions-summary   (13F summary)
  - /insider-trading/search                             (Form 4 per-symbol)
  - /insider-trading/statistics                         (insider stats)
  - /funds/disclosure-holders-latest                    (mutual fund holders)
  - /senate-trades                                      (per-symbol)
  - /house-trades                                       (per-symbol)
  - /shares-float                                       (float shares)
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _current_13f_quarter() -> tuple[int, int]:
    """Return the most recent quarter with likely-complete 13F data.

    13F filings are due 45 days after quarter end.  We pick the quarter
    whose filing deadline has passed (with a small buffer).
    """
    now = datetime.now(timezone.utc)
    # Quarters and their filing deadlines:
    # Q1 (Jan-Mar) → due May 15
    # Q2 (Apr-Jun) → due Aug 14
    # Q3 (Jul-Sep) → due Nov 14
    # Q4 (Oct-Dec) → due Feb 14 (next year)
    year = now.year
    month = now.month
    day = now.day

    if month >= 6 or (month == 5 and day >= 20):
        # Q1 data available (deadline ~May 15)
        return (year, 1)
    if month >= 9 or (month == 8 and day >= 20):
        return (year, 2)
    if month >= 12 or (month == 11 and day >= 20):
        return (year, 3)
    # Otherwise use previous year Q4 or Q3
    if month >= 3 or (month == 2 and day >= 20):
        return (year - 1, 4)
    return (year - 1, 3)


def _prev_quarter(year: int, quarter: int) -> tuple[int, int]:
    """Return the quarter before the given one."""
    if quarter == 1:
        return (year - 1, 4)
    return (year, quarter - 1)


# ── Signal computation helpers ──────────────────────────────────────


def _classify_insider_role(title: str | None) -> str:
    """Map insider title to a role category."""
    if not title:
        return "other"
    t = title.upper()
    if "CEO" in t or "CHIEF EXECUTIVE" in t:
        return "ceo"
    if "CFO" in t or "CHIEF FINANCIAL" in t:
        return "cfo"
    if "COO" in t or "CHIEF OPERATING" in t:
        return "officer"
    if "PRESIDENT" in t or "SVP" in t or "VP" in t or "OFFICER" in t:
        return "officer"
    if "DIRECTOR" in t:
        return "director"
    if "10%" in t or "OWNER" in t:
        return "10pct_owner"
    return "other"


def _classify_transaction_type(tx_type: str | None) -> str:
    """Map FMP transactionType code to a readable category."""
    if not tx_type:
        return "unknown"
    t = tx_type.upper()
    # FMP codes: P-Purchase, S-Sale, A-Grant/Award, M-Option Exercise, etc.
    if "P" in t and "S" not in t:
        return "buy"
    if "S" in t and "P" not in t:
        return "sell"
    if t.startswith("M") or "EXERCISE" in t or "CONVERSION" in t:
        return "option_exercise"
    if t.startswith("A") or "AWARD" in t or "GRANT" in t:
        return "grant"
    if "PURCHASE" in t:
        return "buy"
    if "SALE" in t or "SELL" in t:
        return "sell"
    return "other"


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.split("T")[0], "%Y-%m-%d").replace(
            tzinfo=timezone.utc,
        )
    except (ValueError, AttributeError):
        return None


def _compute_insider_signals(
    transactions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute insider trading signals from raw Form 4 transactions.

    Returns dict with:
      - transaction_table_90d, transaction_table_180d (lists)
      - cluster_buy, cluster_sell (bool)
      - net_value_90d, net_value_180d (float)
      - officer_activity (list of CEO/CFO transactions)
      - score_contribution (int, for scoring integration)
    """
    now = datetime.now(timezone.utc)
    cutoff_90 = now - timedelta(days=90)
    cutoff_180 = now - timedelta(days=180)

    table_90: list[dict] = []
    table_180: list[dict] = []
    buy_value_90 = 0.0
    sell_value_90 = 0.0
    buy_value_180 = 0.0
    sell_value_180 = 0.0
    officer_txns: list[dict] = []

    # For cluster detection: buyers/sellers within trailing 30 days
    cluster_window = now - timedelta(days=30)
    recent_buyers: set[str] = set()
    recent_sellers: set[str] = set()

    for tx in transactions:
        tx_date = _parse_date(
            tx.get("transactionDate") or tx.get("filingDate"),
        )
        if not tx_date:
            continue

        name = tx.get("reportingName") or tx.get("reportingCik") or "Unknown"
        role_raw = tx.get("typeOfOwner") or tx.get("reportingRelation") or ""
        role = _classify_insider_role(role_raw)
        tx_type_raw = tx.get("transactionType") or ""
        tx_type = _classify_transaction_type(tx_type_raw)
        shares = abs(tx.get("securitiesTransacted", 0) or 0)
        price = tx.get("price", 0) or 0
        value = shares * price

        row = {
            "name": name,
            "role": role,
            "role_raw": role_raw,
            "type": tx_type,
            "shares": shares,
            "price": round(price, 2),
            "value": round(value, 2),
            "date": tx_date.strftime("%Y-%m-%d"),
        }

        if tx_date >= cutoff_180:
            table_180.append(row)
            if tx_type == "buy":
                buy_value_180 += value
            elif tx_type == "sell":
                sell_value_180 += value

        if tx_date >= cutoff_90:
            table_90.append(row)
            if tx_type == "buy":
                buy_value_90 += value
            elif tx_type == "sell":
                sell_value_90 += value

        # Officer activity
        if role in ("ceo", "cfo") and tx_type in ("buy", "sell"):
            officer_txns.append(row)

        # Cluster detection (30-day window)
        if tx_date >= cluster_window:
            if tx_type == "buy" and name:
                recent_buyers.add(name)
            elif tx_type == "sell" and name:
                recent_sellers.add(name)

    cluster_buy = len(recent_buyers) >= 3
    cluster_sell = len(recent_sellers) >= 3
    net_90 = buy_value_90 - sell_value_90
    net_180 = buy_value_180 - sell_value_180

    # Score contribution
    score = 0
    if cluster_buy:
        score += 15
    if cluster_sell:
        score -= 10
    # Net insider flow (90d) — simple ±5 based on direction
    if net_90 > 0:
        score += 5
    elif net_90 < -100_000:  # meaningful selling
        score -= 5

    # Classify signal
    if cluster_buy:
        signal = "cluster_buying"
    elif cluster_sell:
        signal = "cluster_selling"
    elif net_90 > 0:
        signal = "net_buying"
    elif net_90 < 0:
        signal = "net_selling"
    else:
        signal = "neutral"

    return {
        "signal": signal,
        "transaction_table_90d": sorted(
            table_90, key=lambda r: r["date"], reverse=True,
        ),
        "transaction_table_180d": sorted(
            table_180, key=lambda r: r["date"], reverse=True,
        ),
        "buy_count_90d": sum(1 for r in table_90 if r["type"] == "buy"),
        "sell_count_90d": sum(1 for r in table_90 if r["type"] == "sell"),
        "buy_value_90d": round(buy_value_90, 2),
        "sell_value_90d": round(sell_value_90, 2),
        "net_value_90d": round(net_90, 2),
        "net_value_180d": round(net_180, 2),
        "cluster_buy": cluster_buy,
        "cluster_sell": cluster_sell,
        "cluster_buy_count": len(recent_buyers),
        "cluster_sell_count": len(recent_sellers),
        "officer_activity": officer_txns[:10],
        "score_contribution": score,
    }


def _compute_institutional_summary(
    summary_rows: list[dict[str, Any]] | None,
    holders: list[dict[str, Any]] | None,
    float_data: list[dict[str, Any]] | None,
    year: int,
    quarter: int,
) -> dict[str, Any]:
    """Compute institutional ownership summary metrics.

    Inputs:
      summary_rows: from /institutional-ownership/symbol-positions-summary
      holders: from /institutional-ownership/extract-analytics/holder
      float_data: from /shares-float
    """
    result: dict[str, Any] = {
        "total_pct": None,
        "holder_count": None,
        "net_flow_shares": None,
        "net_flow_direction": None,
        "top10_concentration_pct": None,
        "quarter": f"Q{quarter} {year}",
        "top_holders": [],
        "score_contribution": 0,
    }

    # Float shares for percentage calculations
    float_shares = None
    if float_data and isinstance(float_data, list) and float_data:
        f = float_data[0]
        float_shares = f.get("floatShares") or f.get("freeFloat")
        if float_shares:
            float_shares = float(float_shares)

    # Summary row (usually a single-item list)
    if summary_rows and isinstance(summary_rows, list) and summary_rows:
        s = summary_rows[0]
        result["holder_count"] = s.get("investorsHolding")
        total_invested = s.get("totalInvested")
        last_invested = s.get("lastTotalInvested")
        if total_invested is not None and last_invested is not None:
            try:
                net = int(total_invested) - int(last_invested)
                result["net_flow_shares"] = net
                result["net_flow_direction"] = "buying" if net > 0 else "selling" if net < 0 else "flat"
            except (ValueError, TypeError):
                pass
        # ownership percentage
        ownership_pct = s.get("ownershipPercent")
        if ownership_pct is not None:
            try:
                result["total_pct"] = round(float(ownership_pct) * 100, 2)
            except (ValueError, TypeError):
                pass

    # Top holders
    if holders and isinstance(holders, list):
        top20: list[dict] = []
        top10_shares = 0
        total_holder_shares = 0
        for i, h in enumerate(holders[:50]):
            shares = h.get("shares") or h.get("securitiesOwned") or 0
            try:
                shares = int(shares)
            except (ValueError, TypeError):
                shares = 0

            change_str = "unchanged"
            change_pct = h.get("changePercentage") or h.get("change")
            if change_pct is not None:
                try:
                    cp = float(change_pct)
                    if cp > 100:
                        change_str = "new"
                    elif cp > 0:
                        change_str = "increased"
                    elif cp < -99:
                        change_str = "exited"
                    elif cp < 0:
                        change_str = "decreased"
                except (ValueError, TypeError):
                    pass

            pct_of_portfolio = h.get("weightInPortfolio") or h.get("portfolioPercent")
            if pct_of_portfolio is not None:
                try:
                    pct_of_portfolio = round(float(pct_of_portfolio) * 100, 3)
                except (ValueError, TypeError):
                    pct_of_portfolio = None

            reported_date = h.get("filingDate") or h.get("date") or ""

            if i < 20:
                top20.append({
                    "name": h.get("investorName") or h.get("holderName") or "Unknown",
                    "shares": shares,
                    "pct_of_portfolio": pct_of_portfolio,
                    "change": change_str,
                    "change_pct": round(float(change_pct), 2) if change_pct else None,
                    "reported_date": reported_date.split("T")[0] if reported_date else "",
                })

            total_holder_shares += shares
            if i < 10:
                top10_shares += shares

        result["top_holders"] = top20

        # Top-10 concentration vs total from all holders
        if float_shares and float_shares > 0:
            result["top10_concentration_pct"] = round(
                (top10_shares / float_shares) * 100, 2,
            )
        elif total_holder_shares > 0:
            result["top10_concentration_pct"] = round(
                (top10_shares / total_holder_shares) * 100, 2,
            )

    # Scoring: ±10 points based on net institutional flow
    if result["net_flow_direction"] == "buying":
        result["score_contribution"] = 10
    elif result["net_flow_direction"] == "selling":
        result["score_contribution"] = -10

    return result


def _compute_congressional(
    senate: list[dict[str, Any]] | None,
    house: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Process congressional trades for a specific symbol."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=180)
    trades: list[dict] = []

    for source, label in [(senate, "Senate"), (house, "House")]:
        if not source:
            continue
        for tx in source:
            tx_date = _parse_date(
                tx.get("transactionDate") or tx.get("disclosureDate"),
            )
            if tx_date and tx_date < cutoff:
                continue
            trades.append({
                "chamber": label,
                "name": tx.get("firstName", "") + " " + tx.get("lastName", ""),
                "party": tx.get("party") or tx.get("owner") or "",
                "type": tx.get("type") or tx.get("transactionType") or "",
                "amount": tx.get("amount") or "",
                "date_traded": (tx.get("transactionDate") or "").split("T")[0],
                "date_disclosed": (tx.get("disclosureDate") or "").split("T")[0],
            })

    trades.sort(key=lambda r: r.get("date_disclosed", ""), reverse=True)
    return {
        "trades": trades[:30],
        "total_count": len(trades),
    }


def _compute_mutual_fund_summary(
    data: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Summarize mutual fund / ETF holder disclosure data."""
    if not data or not isinstance(data, list):
        return {"holders": [], "total_count": 0}

    holders: list[dict] = []
    for h in data[:20]:
        holders.append({
            "name": h.get("holderName") or h.get("investorName") or "Unknown",
            "shares": h.get("shares") or 0,
            "value": h.get("value") or 0,
            "change": h.get("change") or 0,
            "date": (h.get("filingDate") or h.get("date") or "").split("T")[0],
        })

    return {
        "holders": holders,
        "total_count": len(data),
    }


def _generate_synthesis(
    institutional: dict[str, Any],
    insider: dict[str, Any],
    congressional: dict[str, Any],
) -> str:
    """Generate a template-based smart money summary.

    Factual, data-grounded. No speculation about motivations.
    """
    parts: list[str] = []

    # Institutional sentence
    if institutional.get("total_pct") is not None:
        inst_part = f"Institutional ownership is {institutional['total_pct']:.1f}%"
        if institutional.get("holder_count"):
            inst_part += f" across {institutional['holder_count']} holders"
        flow = institutional.get("net_flow_direction")
        if flow == "buying":
            inst_part += " with net institutional buying last quarter"
        elif flow == "selling":
            inst_part += " with net institutional selling last quarter"
        inst_part += "."

        # Notable holders
        top = institutional.get("top_holders", [])
        new_or_increased = [
            h["name"] for h in top[:5]
            if h.get("change") in ("new", "increased")
        ]
        if new_or_increased:
            names = ", ".join(new_or_increased[:3])
            inst_part += f" Notable position additions from {names}."
        parts.append(inst_part)
    else:
        parts.append("Limited institutional coverage data available.")

    # Insider sentence
    signal = insider.get("signal", "neutral")
    if signal == "cluster_buying":
        cnt = insider.get("cluster_buy_count", 3)
        net = abs(insider.get("net_value_90d", 0))
        s = f"Cluster insider buying detected ({cnt} insiders buying"
        if net > 0:
            s += f", ${net:,.0f} net"
        s += " in the last 30 days)."
    elif signal == "cluster_selling":
        cnt = insider.get("cluster_sell_count", 3)
        s = f"Cluster insider selling detected ({cnt} insiders selling in the last 30 days)."
    elif signal == "net_buying":
        net = insider.get("net_value_90d", 0)
        s = f"Net insider buying of ${abs(net):,.0f} over trailing 90 days."
    elif signal == "net_selling":
        net = insider.get("net_value_90d", 0)
        s = f"Net insider selling of ${abs(net):,.0f} over trailing 90 days."
    else:
        s = "No significant insider activity in the trailing 90 days."

    # Officer highlight
    officers = insider.get("officer_activity", [])
    if officers:
        officer_names = set()
        for o in officers[:3]:
            if o.get("role") in ("ceo", "cfo"):
                officer_names.add(o["role"].upper())
        if officer_names:
            s += f" Notable: {', '.join(sorted(officer_names))} transacted."
    parts.append(s)

    # Congressional sentence
    cong_count = congressional.get("total_count", 0)
    if cong_count > 0:
        parts.append(
            f"{cong_count} congressional trade(s) disclosed in the trailing 180 days.",
        )
    else:
        parts.append("No significant congressional activity.")

    return " ".join(parts)


# ── Main service function ───────────────────────────────────────────


async def get_smart_money_data(
    fmp_client: Any,
    symbol: str,
) -> dict[str, Any]:
    """Fetch and compute all smart money data for a symbol.

    Returns the full data payload ready for the frontend panel.
    Fetches all FMP endpoints in parallel (~5-10 calls).
    """
    symbol = symbol.upper().strip()
    year, quarter = _current_13f_quarter()
    prev_year, prev_quarter = _prev_quarter(year, quarter)

    # Fire all FMP calls concurrently
    (
        holders,
        summary,
        float_data,
        insider_txns,
        insider_stats,
        mutual_funds,
        senate,
        house,
    ) = await asyncio.gather(
        fmp_client.get_institutional_holders(symbol, year, quarter),
        fmp_client.get_institutional_positions_summary(symbol, year, quarter),
        fmp_client.get_shares_float(symbol),
        fmp_client.get_insider_trading_by_symbol(symbol),
        fmp_client.get_insider_trade_statistics(symbol),
        fmp_client.get_mutual_fund_holders(symbol),
        fmp_client.get_senate_trades(symbol),
        fmp_client.get_house_trades(symbol),
        return_exceptions=True,
    )

    # Treat exceptions as None
    def _safe(val: Any) -> Any:
        return None if isinstance(val, BaseException) else val

    holders = _safe(holders)
    summary = _safe(summary)
    float_data = _safe(float_data)
    insider_txns = _safe(insider_txns)
    insider_stats = _safe(insider_stats)
    mutual_funds = _safe(mutual_funds)
    senate = _safe(senate)
    house = _safe(house)

    # Compute sections
    institutional = _compute_institutional_summary(
        summary, holders, float_data, year, quarter,
    )
    insider = _compute_insider_signals(insider_txns or [])
    congressional = _compute_congressional(senate, house)
    mutual_fund = _compute_mutual_fund_summary(mutual_funds)

    # Total score contribution
    total_score = (
        institutional.get("score_contribution", 0)
        + insider.get("score_contribution", 0)
    )

    # Synthesis
    synthesis = _generate_synthesis(institutional, insider, congressional)

    return {
        "symbol": symbol,
        "synthesis": synthesis,
        "institutional": institutional,
        "insider": insider,
        "congressional": congressional,
        "mutual_funds": mutual_fund,
        "score_contribution": total_score,
        "_source": "fmp",
        "_fetched_at": datetime.now(timezone.utc).isoformat(),
    }
