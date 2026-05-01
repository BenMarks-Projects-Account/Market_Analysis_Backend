"""BenTrade — Insider Catalyst Scanner Service
strategy_id: stock_insider_catalyst

Catalyst-driven scanner that ranks stocks by insider buying signal strength.
Fundamentally different from the 4 technical scanners (price/volume-driven):
this scanner uses Form 4 filing data from FMP Ultimate.

Scoring formula:
  signal_score = (sum of insider weights for net buyers)
               + (0.5 × log(1 + total_net_buy_dollars))
               - (sum of insider weights for net sellers)

Role weights (per unique insider, not per transaction):
  CEO: 3, CFO: 3, COO: 2, CTO: 2, CHIEF_OTHER: 2,
  DIRECTOR: 1, OWNER_10PCT: 1, OTHER: 0

Cluster threshold: configurable (default 5 weighted points for net buyers).

Data source: FMP /insider-trading/search (Form 4 filings).
Transaction filter: only open-market buys and sells (no grants, no exercises).
Universe: $250M - $10B market cap, NYSE/NASDAQ/AMEX, actively trading.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import defaultdict
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

# ── Role weight mapping ────────────────────────────────────────────────
# Applied per UNIQUE insider, not per transaction.
ROLE_WEIGHTS: dict[str, int] = {
    "CEO": 3,
    "CFO": 3,
    "COO": 2,
    "CTO": 2,
    "CHIEF_OTHER": 2,
    "DIRECTOR": 1,
    "OWNER_10PCT": 1,
    "OTHER": 0,
}


def compute_insider_signal(
    transactions: list[dict[str, Any]],
    cluster_threshold: int = 5,
) -> dict[str, Any]:
    """Compute insider signal from normalized Form 4 transactions.

    Args:
        transactions: Normalized output from fmp_client.get_insider_transactions().
            Each dict has: insider_name, insider_role, transaction_type,
            transaction_date, shares, price_per_share, total_value, filing_date.
        cluster_threshold: Weighted points required to trigger cluster signal.

    Returns dict with:
        signal_score, cluster_triggered, weighted_insider_points,
        unique_buyers, unique_sellers, net_buy_dollars,
        ceo_participated, cfo_participated, transactions,
        last_transaction_date, rationale.
    """
    # Only open-market buys and sells
    filtered = [
        tx for tx in transactions
        if tx.get("transaction_type") in ("buy", "sell")
    ]

    if not filtered:
        return _empty_signal(transactions)

    # Per-insider net dollar flow
    # Key = insider_name, value = {role, net_dollars}
    insider_flows: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"role": "OTHER", "bought": 0.0, "sold": 0.0},
    )
    for tx in filtered:
        name = tx["insider_name"]
        insider_flows[name]["role"] = tx["insider_role"]
        if tx["transaction_type"] == "buy":
            insider_flows[name]["bought"] += tx["total_value"]
        else:
            insider_flows[name]["sold"] += tx["total_value"]

    # Classify each unique insider as net buyer or net seller
    net_buyers: list[tuple[str, str]] = []   # (name, role)
    net_sellers: list[tuple[str, str]] = []
    total_bought = 0.0
    total_sold = 0.0

    for name, flow in insider_flows.items():
        total_bought += flow["bought"]
        total_sold += flow["sold"]
        net = flow["bought"] - flow["sold"]
        if net > 0:
            net_buyers.append((name, flow["role"]))
        elif net < 0:
            net_sellers.append((name, flow["role"]))

    # Weighted points for buyers and sellers
    buyer_weight = sum(ROLE_WEIGHTS.get(role, 0) for _, role in net_buyers)
    seller_weight = sum(ROLE_WEIGHTS.get(role, 0) for _, role in net_sellers)

    net_buy_dollars = total_bought - total_sold

    # Signal score formula
    # signal_score = buyer_weight + 0.5 * log(1 + max(0, net_buy_dollars)) - seller_weight
    log_component = 0.5 * math.log(1 + max(0, net_buy_dollars)) if net_buy_dollars > 0 else 0.0
    signal_score = buyer_weight + log_component - seller_weight

    cluster_triggered = buyer_weight >= cluster_threshold

    ceo_participated = any(role == "CEO" for _, role in net_buyers)
    cfo_participated = any(role == "CFO" for _, role in net_buyers)

    # Last transaction date
    dates = [tx["transaction_date"] for tx in filtered if tx.get("transaction_date")]
    last_tx_date = max(dates) if dates else ""

    # Rationale
    rationale = _build_rationale(
        net_buyers, net_sellers, buyer_weight, net_buy_dollars, cluster_triggered,
    )

    return {
        "signal_score": round(signal_score, 2),
        "cluster_triggered": cluster_triggered,
        "weighted_insider_points": buyer_weight,
        "unique_buyers": len(net_buyers),
        "unique_sellers": len(net_sellers),
        "net_buy_dollars": round(net_buy_dollars, 2),
        "ceo_participated": ceo_participated,
        "cfo_participated": cfo_participated,
        "transactions": filtered,
        "last_transaction_date": last_tx_date,
        "rationale": rationale,
    }


def _empty_signal(raw_transactions: list[dict]) -> dict[str, Any]:
    """Return a neutral signal when no buy/sell transactions exist."""
    return {
        "signal_score": 0.0,
        "cluster_triggered": False,
        "weighted_insider_points": 0,
        "unique_buyers": 0,
        "unique_sellers": 0,
        "net_buy_dollars": 0.0,
        "ceo_participated": False,
        "cfo_participated": False,
        "transactions": raw_transactions,
        "last_transaction_date": "",
        "rationale": "No open-market insider transactions in the lookback window.",
    }


def _build_rationale(
    net_buyers: list[tuple[str, str]],
    net_sellers: list[tuple[str, str]],
    buyer_weight: int,
    net_buy_dollars: float,
    cluster_triggered: bool,
) -> str:
    """Build a human-readable 1-2 sentence rationale."""
    if not net_buyers and not net_sellers:
        return "No open-market insider transactions in the lookback window."

    parts: list[str] = []

    if cluster_triggered:
        role_names = []
        for _, role in net_buyers:
            if role in ("CEO", "CFO", "COO", "CTO"):
                role_names.append(role)
            elif role == "DIRECTOR":
                role_names.append("director")
            elif role == "OWNER_10PCT":
                role_names.append("10% owner")
        # Summarize
        if role_names:
            unique_roles = []
            seen = set()
            # Count duplicates
            role_counts: dict[str, int] = defaultdict(int)
            for r in role_names:
                role_counts[r] += 1
            for r, cnt in role_counts.items():
                if cnt > 1:
                    unique_roles.append(f"{cnt} {r.lower()}s")
                else:
                    unique_roles.append(r)
            role_str = ", ".join(unique_roles)
        else:
            role_str = f"{len(net_buyers)} insiders"

        dollar_str = _fmt_dollars(abs(net_buy_dollars))
        parts.append(
            f"Cluster buy: {role_str} bought {dollar_str} combined. "
            f"Weighted score: {buyer_weight}."
        )
    elif net_buyers:
        dollar_str = _fmt_dollars(abs(net_buy_dollars))
        parts.append(
            f"{len(net_buyers)} insider(s) net bought {dollar_str}. "
            f"Weighted score: {buyer_weight}."
        )

    if net_sellers and not cluster_triggered:
        parts.append(f"{len(net_sellers)} insider(s) were net sellers.")
    elif net_sellers:
        parts.append(f"Note: {len(net_sellers)} insider(s) were net sellers.")

    return " ".join(parts) if parts else "Minimal insider activity."


def _fmt_dollars(amount: float) -> str:
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount:.0f}"


# ── Insider Catalyst Scanner Service ───────────────────────────────────

class InsiderCatalystService:
    """Standalone catalyst scanner that ranks stocks by insider buying strength.

    Architecturally separate from the 4 technical scanners. Does NOT use
    price/volume data. Pure Form 4 filing analysis.

    Universe: $250M - $10B market cap (FMP stock screener).
    Data: FMP /insider-trading/search per symbol.
    """

    def __init__(
        self,
        fmp_client: Any,
        settings: Any | None = None,
    ) -> None:
        self._fmp = fmp_client
        self._settings = settings or get_settings()
        # Per-symbol signal cache: {symbol: (signal_dict, timestamp)}
        self._signal_cache: dict[str, tuple[dict[str, Any], float]] = {}

    @property
    def _cache_ttl(self) -> int:
        return self._settings.INSIDER_CACHE_TTL_SECONDS

    @property
    def _lookback_days(self) -> int:
        return self._settings.INSIDER_LOOKBACK_DAYS

    @property
    def _cluster_threshold(self) -> int:
        return self._settings.INSIDER_CLUSTER_THRESHOLD

    async def scan(
        self,
        *,
        max_candidates: int = 30,
        concurrency: int = 10,
    ) -> dict[str, Any]:
        """Run the insider catalyst scanner on the full universe.

        Returns dict matching the stock scanner output contract:
            candidates, total_candidates, scan_time_seconds, warnings, strategy_id
        """
        started = time.time()
        warnings: list[str] = []

        # 1. Fetch universe
        universe = await self._fetch_universe(warnings)
        if not universe:
            return self._empty_result(warnings, time.time() - started)

        # 2. Score each symbol (concurrent, semaphore-limited)
        sem = asyncio.Semaphore(concurrency)

        async def _scan_one(symbol: str) -> dict[str, Any] | None:
            async with sem:
                try:
                    return await self._score_symbol(symbol)
                except Exception as exc:
                    logger.debug(
                        "event=insider_scan_symbol_error symbol=%s error=%s",
                        symbol, exc,
                    )
                    return None

        results = await asyncio.gather(
            *[_scan_one(sym) for sym in universe],
            return_exceptions=True,
        )

        # 3. Filter and rank
        candidates: list[dict[str, Any]] = []
        for r in results:
            if isinstance(r, BaseException) or r is None:
                continue
            if r.get("signal_score", 0) > 0:
                candidates.append(r)

        candidates.sort(key=lambda c: -c["signal_score"])
        selected = candidates[:max_candidates]

        for i, cand in enumerate(selected, start=1):
            cand["rank"] = i

        elapsed = time.time() - started
        return {
            "strategy_id": "stock_insider_catalyst",
            "status": "ok",
            "candidates": selected,
            "total_candidates": len(candidates),
            "universe_size": len(universe),
            "scan_time_seconds": round(elapsed, 2),
            "warnings": warnings,
            "config": {
                "lookback_days": self._lookback_days,
                "cluster_threshold": self._cluster_threshold,
                "market_cap_min": self._settings.INSIDER_MARKET_CAP_MIN,
                "market_cap_max": self._settings.INSIDER_MARKET_CAP_MAX,
            },
        }

    async def get_signal(self, symbol: str) -> dict[str, Any]:
        """Get cached insider signal for a symbol.

        Used by the boost enrichment — avoids re-fetching if the
        standalone scanner already computed this symbol's signal.
        """
        symbol = symbol.upper().strip()
        cached = self._signal_cache.get(symbol)
        if cached:
            signal, ts = cached
            if time.time() - ts < self._cache_ttl:
                return signal

        txns = await self._fmp.get_insider_transactions(
            symbol, lookback_days=self._lookback_days,
        )
        signal = compute_insider_signal(txns, self._cluster_threshold)
        signal["symbol"] = symbol
        self._signal_cache[symbol] = (signal, time.time())
        return signal

    async def enrich_with_insider_signal(
        self,
        scanner_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Apply insider boost/penalty to technical scanner results.

        For each candidate:
          - cluster_triggered AND direction LONG: +INSIDER_BOOST_POINTS
          - signal_score < -3 AND direction LONG: -INSIDER_PENALTY_POINTS
          - Otherwise: no change

        Returns the same list with modified scores and insider tags.
        If insider data is unavailable for a symbol, the result is unchanged.
        """
        boost_pts = self._settings.INSIDER_BOOST_POINTS
        penalty_pts = self._settings.INSIDER_PENALTY_POINTS

        for cand in scanner_results:
            symbol = cand.get("symbol")
            if not symbol:
                continue

            try:
                signal = await self.get_signal(symbol)
            except Exception:
                # Graceful degradation: no insider data → no modifier
                continue

            # Only boost LONG-direction technical setups
            direction = cand.get("direction", "long").lower()
            if direction != "long":
                continue

            if signal.get("cluster_triggered"):
                old_score = cand.get("composite_score", 0) or cand.get("setup_quality", 0)
                cand["composite_score"] = old_score + boost_pts
                if "setup_quality" in cand:
                    cand["setup_quality"] = cand["setup_quality"] + boost_pts
                cand["insider_tag"] = "insider_cluster_confirmed"
                cand["insider_signal_score"] = signal["signal_score"]
                cand["insider_boost_applied"] = boost_pts
            elif signal.get("signal_score", 0) < -3:
                old_score = cand.get("composite_score", 0) or cand.get("setup_quality", 0)
                cand["composite_score"] = max(0, old_score - penalty_pts)
                if "setup_quality" in cand:
                    cand["setup_quality"] = max(0, cand["setup_quality"] - penalty_pts)
                cand["insider_tag"] = "insider_selling_warning"
                cand["insider_signal_score"] = signal["signal_score"]
                cand["insider_boost_applied"] = -penalty_pts

        return scanner_results

    # ── Private methods ────────────────────────────────────────────

    async def _fetch_universe(self, warnings: list[str]) -> list[str]:
        """Fetch universe of symbols from FMP stock screener."""
        try:
            rows = await self._fmp.get_stock_screener(
                market_cap_min=self._settings.INSIDER_MARKET_CAP_MIN,
                exchange="nyse,nasdaq,amex",
                limit=5000,
            )
        except Exception as exc:
            warnings.append(f"Universe fetch failed: {exc}")
            return []

        if not rows:
            warnings.append("Empty universe from FMP screener")
            return []

        # Filter by market cap ceiling
        cap_max = self._settings.INSIDER_MARKET_CAP_MAX
        symbols: list[str] = []
        for row in rows:
            mcap = row.get("marketCap") or row.get("market_cap") or 0
            try:
                mcap = float(mcap)
            except (ValueError, TypeError):
                continue
            if mcap <= cap_max:
                sym = row.get("symbol")
                if sym:
                    symbols.append(str(sym).upper())

        logger.info(
            "event=insider_universe_loaded total=%d after_cap_filter=%d",
            len(rows), len(symbols),
        )
        return symbols

    async def _score_symbol(self, symbol: str) -> dict[str, Any]:
        """Fetch insider transactions and compute signal for one symbol."""
        signal = await self.get_signal(symbol)
        return signal

    def _empty_result(
        self, warnings: list[str], elapsed: float,
    ) -> dict[str, Any]:
        return {
            "strategy_id": "stock_insider_catalyst",
            "status": "ok",
            "candidates": [],
            "total_candidates": 0,
            "universe_size": 0,
            "scan_time_seconds": round(elapsed, 2),
            "warnings": warnings,
        }
