"""Portfolio balancing — synthesize active trade analysis + new candidates into a rebalance plan."""

import logging
from typing import Optional
from app.services.close_order_builder import build_close_order

_log = logging.getLogger(__name__)


async def build_rebalance_plan(
    *,
    active_trade_results: dict,
    stock_candidates: list,
    options_candidates: list,
    account_balance: dict,
    risk_policy: dict,
    portfolio_greeks: dict,
    concentration: dict,
    regime_label: str | None = None,
    event_context: dict | None = None,
) -> dict:
    """Build a portfolio rebalance suggestion.
    
    Args:
        active_trade_results: Output from active trade pipeline (recommendations[])
        stock_candidates: Top-N stock candidates from stock opportunity runner
        options_candidates: Top-N options candidates from options opportunity runner
        account_balance: Tradier account balances (equity, buying_power, etc.)
        risk_policy: Dynamic risk policy from build_dynamic_policy()
        portfolio_greeks: Current net portfolio Greeks {delta, gamma, theta, vega}
        concentration: Current concentration analysis {by_underlying, by_strategy, by_expiration}
        regime_label: Current market regime
        event_context: Upcoming events
    
    Returns:
        Rebalance plan dict with close_actions, open_actions, skip_reasons, net_impact
    """
    
    equity = float(account_balance.get("equity") or 0)
    
    # ─── STEP 1: Categorize active trade recommendations ───
    closes = []
    reduces = []
    holds = []
    
    for rec in active_trade_results.get("recommendations", []):
        action = rec.get("recommendation", "HOLD")
        if action in ("CLOSE", "URGENT_REVIEW"):
            closes.append(rec)
        elif action == "REDUCE":
            reduces.append(rec)
        else:
            holds.append(rec)
    
    # ─── STEP 2: Compute risk freed by closes/reduces ───
    risk_freed = 0.0
    delta_freed = 0.0
    close_actions = []
    
    for rec in closes:
        trade_risk = _estimate_trade_risk(rec)
        trade_delta = _estimate_trade_delta(rec)
        risk_freed += trade_risk
        delta_freed += trade_delta
        close_actions.append({
            "action": "CLOSE",
            "symbol": rec.get("symbol"),
            "strategy": rec.get("strategy"),
            "conviction": rec.get("conviction"),
            "reason": rec.get("rationale_summary", "Pipeline recommendation"),
            "risk_freed": round(trade_risk, 2),
            "delta_freed": round(trade_delta, 4),
            "close_order": rec.get("suggested_close_order"),
            "trade_health_score": rec.get("trade_health_score"),
        })
    
    for rec in reduces:
        trade_risk = _estimate_trade_risk(rec) * 0.5  # 50% reduction
        trade_delta = _estimate_trade_delta(rec) * 0.5
        risk_freed += trade_risk
        delta_freed += trade_delta
        close_actions.append({
            "action": "REDUCE",
            "symbol": rec.get("symbol"),
            "strategy": rec.get("strategy"),
            "conviction": rec.get("conviction"),
            "reason": rec.get("rationale_summary", "Pipeline recommendation"),
            "risk_freed": round(trade_risk, 2),
            "delta_freed": round(trade_delta, 4),
            "close_order": rec.get("suggested_close_order"),
            "trade_health_score": rec.get("trade_health_score"),
        })
    
    # ─── STEP 3: Post-adjustment portfolio state ───
    current_risk_used = float(risk_policy.get("max_risk_total", 0)) - float(
        risk_policy.get("risk_remaining", risk_policy.get("max_risk_total", 0))
    )
    # Better: get from the risk snapshot if available
    
    post_adj_risk_used = max(0, current_risk_used - risk_freed)
    risk_budget_available = float(risk_policy.get("max_risk_total", 0)) - post_adj_risk_used
    
    current_delta = portfolio_greeks.get("delta", 0)
    post_adj_delta = current_delta - delta_freed
    
    current_trade_count = len(active_trade_results.get("recommendations", []))
    post_adj_trade_count = current_trade_count - len(closes)
    max_trades = risk_policy.get("max_concurrent_trades", 10)
    open_slots = max(0, max_trades - post_adj_trade_count)
    
    max_risk_per_trade = float(risk_policy.get("max_risk_per_trade", 500))
    
    post_adjustment = {
        "risk_used": round(post_adj_risk_used, 2),
        "risk_budget_available": round(risk_budget_available, 2),
        "risk_freed_by_closes": round(risk_freed, 2),
        "portfolio_delta": round(post_adj_delta, 4),
        "open_trade_count": post_adj_trade_count,
        "open_slots": open_slots,
        "max_risk_per_new_trade": round(max_risk_per_trade, 2),
    }
    
    # ─── STEP 4: Filter and rank new candidates ───
    all_new_candidates = []
    
    # Combine stock and options candidates with source tag
    for cand in (options_candidates or []):
        cand["_source"] = "options"
        cand["_max_loss"] = _extract_max_loss(cand)
        cand["_underlying"] = cand.get("symbol") or cand.get("underlying")
        cand["_strategy"] = cand.get("scanner_key") or cand.get("strategy_id")
        cand["_ev"] = _safe_float(cand.get("math", {}).get("ev"))
        cand["_ror"] = _safe_float(cand.get("math", {}).get("ror"))
        cand["_regime_alignment"] = cand.get("regime_alignment", "neutral")
        cand["_delta"] = _extract_candidate_delta(cand)
        all_new_candidates.append(cand)
    
    for cand in (stock_candidates or []):
        cand["_source"] = "stock"
        cand["_max_loss"] = _extract_stock_risk(cand, max_risk_per_trade)
        cand["_underlying"] = cand.get("symbol")
        cand["_strategy"] = cand.get("scanner_key") or cand.get("strategy")
        cand["_ev"] = None  # Stocks don't have EV in the same way
        cand["_ror"] = None
        cand["_regime_alignment"] = cand.get("regime_alignment", "neutral")
        cand["_delta"] = 1.0  # Long stock = delta 1.0 per share
        all_new_candidates.append(cand)
    
    # Filter through constraints
    open_actions = []
    skip_actions = []
    remaining_risk_budget = risk_budget_available
    remaining_slots = open_slots
    used_underlyings = _get_held_underlyings(holds, reduces)
    
    # Sort candidates: aligned first, then by risk-adjusted return
    all_new_candidates.sort(key=lambda c: (
        {"aligned": 0, "neutral": 1, "misaligned": 2}.get(c.get("_regime_alignment", "neutral"), 1),
        -(c.get("_ror") or 0),
        -(c.get("_ev") or 0),
    ))
    
    for cand in all_new_candidates:
        underlying = cand.get("_underlying")
        max_loss = cand.get("_max_loss", 0)
        strategy = cand.get("_strategy", "")
        cand_delta = cand.get("_delta", 0)
        
        skip_reason = None
        
        # Constraint checks
        if remaining_slots <= 0:
            skip_reason = f"Max concurrent trades reached ({max_trades})"
        elif max_loss > remaining_risk_budget:
            skip_reason = f"Exceeds remaining risk budget (${remaining_risk_budget:.0f} available, ${max_loss:.0f} needed)"
        elif max_loss > max_risk_per_trade:
            skip_reason = f"Exceeds per-trade risk limit (${max_risk_per_trade:.0f})"
        elif underlying in used_underlyings:
            # Check concentration
            underlying_risk = _get_underlying_risk(concentration, underlying)
            max_underlying = float(risk_policy.get("max_risk_per_underlying", equity * 0.02))
            if underlying_risk + max_loss > max_underlying:
                skip_reason = f"Would exceed {underlying} concentration limit (${max_underlying:.0f})"
        elif cand.get("_regime_alignment") == "misaligned":
            skip_reason = f"Strategy {strategy} misaligned with {regime_label} regime"
        
        # Delta check: would this push portfolio delta too far?
        if not skip_reason:
            delta_range = risk_policy.get("target_portfolio_delta_range", (-1.0, 1.0))
            projected_delta = post_adj_delta + cand_delta
            if projected_delta < delta_range[0] or projected_delta > delta_range[1]:
                skip_reason = f"Would push portfolio delta to {projected_delta:.2f} (target: {delta_range[0]} to {delta_range[1]})"
        
        if skip_reason:
            skip_actions.append({
                "symbol": underlying,
                "strategy": strategy,
                "source": cand.get("_source"),
                "skip_reason": skip_reason,
                "max_loss": round(max_loss, 2),
            })
        else:
            # Size the position
            contracts = _size_position(max_loss, max_risk_per_trade, risk_policy)
            
            open_actions.append({
                "action": "OPEN",
                "symbol": underlying,
                "strategy": strategy,
                "source": cand.get("_source"),
                "max_loss": round(max_loss, 2),
                "contracts": contracts,
                "regime_alignment": cand.get("_regime_alignment"),
                "ev": cand.get("_ev"),
                "ror": cand.get("_ror"),
                "delta_impact": round(cand_delta * contracts, 4) if cand_delta else None,
                "candidate_rank": cand.get("rank"),
                "candidate_data": _extract_candidate_summary(cand),
            })
            
            remaining_risk_budget -= max_loss * contracts
            remaining_slots -= 1
            used_underlyings.add(underlying)
            post_adj_delta += (cand_delta or 0) * contracts
    
    # ─── STEP 5: Compute net impact ───
    total_new_risk = sum(a["max_loss"] * a.get("contracts", 1) for a in open_actions)
    total_new_delta = sum(a.get("delta_impact", 0) or 0 for a in open_actions)
    
    net_impact = {
        "risk_before": round(current_risk_used, 2),
        "risk_after_closes": round(post_adj_risk_used, 2),
        "risk_after_opens": round(post_adj_risk_used + total_new_risk, 2),
        "risk_change": round(total_new_risk - risk_freed, 2),
        "delta_before": round(current_delta, 4),
        "delta_after": round(post_adj_delta + total_new_delta, 4),
        "trades_before": current_trade_count,
        "trades_after": post_adj_trade_count + len(open_actions),
        "positions_closed": len(closes),
        "positions_reduced": len(reduces),
        "positions_opened": len(open_actions),
        "positions_held": len(holds),
        "positions_skipped": len(skip_actions),
        "risk_budget_remaining": round(remaining_risk_budget, 2),
    }
    
    return {
        "close_actions": close_actions,
        "hold_positions": [
            {
                "symbol": h.get("symbol"),
                "strategy": h.get("strategy"),
                "trade_health_score": h.get("trade_health_score"),
                "conviction": h.get("conviction"),
            }
            for h in holds
        ],
        "open_actions": open_actions,
        "skip_actions": skip_actions,
        "net_impact": net_impact,
        "post_adjustment_state": post_adjustment,
        "risk_policy_used": {
            "max_risk_per_trade": max_risk_per_trade,
            "max_risk_total": risk_policy.get("max_risk_total"),
            "max_concurrent_trades": max_trades,
            "regime_label": regime_label,
            "regime_multiplier": risk_policy.get("regime_multiplier", 1.0),
            "account_equity": equity,
        },
    }


# ─── Helper functions ───

def _estimate_trade_risk(rec):
    snap = rec.get("position_snapshot", {})
    # Use max_loss if available from the trade, otherwise estimate from cost basis
    return abs(snap.get("max_loss") or snap.get("cost_basis_total") or 0)

def _estimate_trade_delta(rec):
    greeks = rec.get("live_greeks", {})
    return greeks.get("trade_delta", 0) or 0

def _extract_max_loss(cand):
    math = cand.get("math", {})
    return abs(math.get("max_loss") or 0) / 100  # Convert from per-contract cents to dollars

def _extract_stock_risk(cand, max_per_trade):
    # For stocks, risk = position size (can go to zero)
    # Use the per-trade limit as the sizing constraint
    return max_per_trade

def _extract_candidate_delta(cand):
    math = cand.get("math", {})
    # Options: use the net delta from the spread
    # Approximate: short credit spread ≈ negative delta (short put = positive delta)
    scanner_key = cand.get("scanner_key", "")
    if "put_credit" in scanner_key:
        return 0.15  # Approximate positive delta
    elif "call_credit" in scanner_key:
        return -0.15  # Approximate negative delta
    elif "iron_condor" in scanner_key:
        return 0.0  # Delta neutral
    return 0.0

def _safe_float(val):
    if val is None: return None
    try: return float(val)
    except (ValueError, TypeError): return None

def _get_held_underlyings(holds, reduces):
    underlyings = set()
    for h in holds + reduces:
        u = h.get("symbol") or h.get("underlying")
        if u: underlyings.add(u)
    return underlyings

def _get_underlying_risk(concentration, underlying):
    items = concentration.get("by_underlying", {}).get("items", [])
    for item in items:
        if item.get("symbol") == underlying:
            return item.get("risk", 0)
    return 0

def _size_position(max_loss_per_contract, max_risk_per_trade, policy):
    """Determine how many contracts/shares to trade."""
    if max_loss_per_contract <= 0:
        return 1
    contracts = int(max_risk_per_trade / max_loss_per_contract)
    cap = policy.get("default_contracts_cap", 3) or policy.get("suggested_max_contracts", 3)
    return max(1, min(contracts, cap))

def _extract_candidate_summary(cand):
    """Extract key fields for display."""
    return {
        "symbol": cand.get("symbol") or cand.get("underlying"),
        "scanner_key": cand.get("scanner_key"),
        "dte": cand.get("dte"),
        "dte_bucket": cand.get("dte_bucket"),
        "ev": cand.get("math", {}).get("ev"),
        "pop": cand.get("math", {}).get("pop"),
        "max_profit": cand.get("math", {}).get("max_profit"),
        "max_loss": cand.get("math", {}).get("max_loss"),
        "event_risk": cand.get("event_risk"),
    }
