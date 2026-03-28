"""Regenerate managed_ev_validation.md with fixed probability model."""
import sys
import os
from datetime import date

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.scanner_v2.managed_ev import compute_managed_ev

CANDIDATES = [
    {
        "label": "SPY Put Credit Spread (15-delta, 35 DTE)",
        "strategy_class": "income",
        "pop": 0.85,
        "max_profit": 120.00,
        "max_loss": 880.00,
        "net_credit": 1.20,
        "net_debit": None,
        "width": 10.00,
        "dte": 35,
        "iv": 0.22,
        "underlying_price": 550.00,
        "short_strike": 540.00,
        "long_strike": 530.00,
        "scanner_key": "put_credit_spread",
        "binary_ev": -30.00,
    },
    {
        "label": "QQQ Call Credit Spread (20-delta, 30 DTE)",
        "strategy_class": "income",
        "pop": 0.80,
        "max_profit": 150.00,
        "max_loss": 850.00,
        "net_credit": 1.50,
        "net_debit": None,
        "width": 10.00,
        "dte": 30,
        "iv": 0.25,
        "underlying_price": 470.00,
        "short_strike": 480.00,
        "long_strike": 490.00,
        "scanner_key": "call_credit_spread",
        "binary_ev": -50.00,
    },
    {
        "label": "IWM Iron Condor (30 DTE)",
        "strategy_class": "income",
        "pop": 0.72,
        "max_profit": 180.00,
        "max_loss": 820.00,
        "net_credit": 1.80,
        "net_debit": None,
        "width": 10.00,
        "dte": 30,
        "iv": 0.28,
        "underlying_price": 210.00,
        "short_strike": 200.00,
        "long_strike": 190.00,
        "scanner_key": "iron_condor",
        "binary_ev": -100.00,
    },
    {
        "label": "SPY Call Debit Spread (45-delta, 21 DTE)",
        "strategy_class": "directional",
        "pop": 0.45,
        "max_profit": 300.00,
        "max_loss": 200.00,
        "net_credit": None,
        "net_debit": 2.00,
        "width": 5.00,
        "dte": 21,
        "iv": 0.22,
        "underlying_price": 550.00,
        "short_strike": 555.00,
        "long_strike": 550.00,
        "scanner_key": "call_debit",
        "binary_ev": 25.00,
    },
    {
        "label": "QQQ Put Debit Spread (40-delta, 14 DTE)",
        "strategy_class": "directional",
        "pop": 0.40,
        "max_profit": 350.00,
        "max_loss": 150.00,
        "net_credit": None,
        "net_debit": 1.50,
        "width": 5.00,
        "dte": 14,
        "iv": 0.30,
        "underlying_price": 470.00,
        "short_strike": 465.00,
        "long_strike": 470.00,
        "scanner_key": "put_debit",
        "binary_ev": 50.00,
    },
]


def main():
    results = []
    for c in CANDIDATES:
        r = compute_managed_ev(
            strategy_class=c["strategy_class"],
            pop=c["pop"],
            max_profit=c["max_profit"],
            max_loss=c["max_loss"],
            net_credit=c["net_credit"],
            net_debit=c["net_debit"],
            width=c["width"],
            dte=c["dte"],
            iv=c["iv"],
            underlying_price=c["underlying_price"],
            short_strike=c["short_strike"],
            long_strike=c["long_strike"],
            scanner_key=c["scanner_key"],
        )
        results.append((c, r))

    today = date.today().isoformat()

    lines = []
    lines.append("# Managed EV Validation Report\n")
    lines.append(f"Generated: {today}")
    lines.append("Model: three_outcome_v1")
    lines.append("Probability model: POP-decay (income) / touch (directional)\n")
    lines.append("## Summary\n")
    lines.append("This report compares the binary (hold-to-expiration) EV model with")
    lines.append("the three-outcome managed EV model across 5 example candidates.\n")
    lines.append("**Probability model fix (v1.1):** Income profit targets now use the")
    lines.append("POP-decay model instead of touch probability.  Touch probability")
    lines.append("models a price barrier event, but income profit targets are theta-")
    lines.append("driven (time decay).  Stop losses remain touch probability for both")
    lines.append("strategy types.  Race-to-barrier conditioning replaces the crude")
    lines.append("x0.5 ordering correction.\n")

    # Summary table
    lines.append("| # | Strategy | Binary EV | Managed EV | POP | p_target | p_stop | p_expiry |")
    lines.append("|---|----------|-----------|------------|-----|----------|--------|----------|")
    for i, (c, r) in enumerate(results, 1):
        ev_m = r["ev_managed"]
        ev_m_str = f"${ev_m:.2f}" if ev_m is not None else "N/A"
        p_t = r["p_profit_target"]
        p_s = r["p_stop_loss"]
        p_e = r["p_expiration"]
        lines.append(
            f"| {i} | {c['label']} | ${c['binary_ev']:.2f} | "
            f"{ev_m_str} | {c['pop']:.2f} | "
            f"{p_t:.4f} | {p_s:.4f} | {p_e:.4f} |"
        )
    lines.append("")

    # Sanity checks
    lines.append("## Sanity Checks\n")
    all_ok = True
    for i, (c, r) in enumerate(results, 1):
        checks = []
        p_t = r["p_profit_target"]
        p_s = r["p_stop_loss"]
        p_e = r["p_expiration"]
        ev_m = r["ev_managed"]

        if p_t is not None and p_s is not None and p_e is not None:
            prob_sum = p_t + p_s + p_e
            ok_sum = abs(prob_sum - 1.0) < 0.01
            checks.append(f"prob_sum={prob_sum:.6f} {'PASS' if ok_sum else 'FAIL'}")
            if not ok_sum:
                all_ok = False

        if c["strategy_class"] == "income":
            if p_t is not None and p_s is not None:
                ok_income = p_t > p_s
                checks.append(f"p_target > p_stop: {p_t:.4f} > {p_s:.4f} {'PASS' if ok_income else 'FAIL'}")
                if not ok_income:
                    all_ok = False
            if ev_m is not None:
                ok_ev = ev_m > c["binary_ev"]
                checks.append(f"ev_managed > binary_ev: {ev_m:.2f} > {c['binary_ev']:.2f} {'PASS' if ok_ev else 'FAIL'}")
                if not ok_ev:
                    all_ok = False

        lines.append(f"**Candidate {i} ({c['label']}):** {' | '.join(checks)}")

    lines.append(f"\n**Overall: {'ALL CHECKS PASS' if all_ok else 'SOME CHECKS FAILED'}**\n")
    lines.append("---\n")

    # Detailed sections
    for i, (c, r) in enumerate(results, 1):
        lines.append(f"## Candidate {i}: {c['label']}\n")
        lines.append("### Binary EV Fields\n")
        lines.append("| Field | Value |")
        lines.append("|-------|-------|")
        lines.append(f"| POP | {c['pop']:.4f} |")
        lines.append(f"| max_profit | ${c['max_profit']:.2f} |")
        lines.append(f"| max_loss | ${c['max_loss']:.2f} |")
        lines.append(f"| binary EV | ${c['binary_ev']:.2f} |")
        lines.append(f"| net_credit | ${c['net_credit']}" if c["net_credit"] else "| net_credit | $None |")
        lines.append(f"| net_debit | ${c['net_debit']}" if c["net_debit"] else "| net_debit | $None |")
        lines.append(f"| width | ${c['width']:.2f} |")
        lines.append(f"| DTE | {c['dte']} |")
        lines.append(f"| IV | {c['iv']:.4f} |")
        lines.append(f"| underlying | ${c['underlying_price']:.2f} |")
        lines.append("")

        lines.append("### Managed EV Fields\n")
        lines.append("| Field | Value |")
        lines.append("|-------|-------|")
        for k in [
            "ev_managed", "ev_managed_per_day",
            "managed_profit_target", "managed_stop_loss",
            "p_profit_target", "p_stop_loss", "p_expiration",
            "managed_expected_ror", "ev_model",
        ]:
            v = r[k]
            if k in ("ev_managed", "ev_managed_per_day", "managed_profit_target", "managed_stop_loss"):
                lines.append(f"| {k} | ${v:.2f} |" if v is not None else f"| {k} | None |")
            elif k in ("p_profit_target", "p_stop_loss", "p_expiration", "managed_expected_ror"):
                lines.append(f"| {k} | {v:.4f} |" if v is not None else f"| {k} | None |")
            else:
                lines.append(f"| {k} | {v} |")

        p_t = r["p_profit_target"] or 0
        p_s = r["p_stop_loss"] or 0
        p_e = r["p_expiration"] or 0
        lines.append(f"| probability_sum | {p_t + p_s + p_e:.6f} |")
        lines.append("")

        lines.append("### Management Policy\n")
        lines.append("| Parameter | Value |")
        lines.append("|-----------|-------|")
        pol = r["management_policy_used"]
        if pol:
            for pk, pv in pol.items():
                lines.append(f"| {pk} | {pv} |")
        lines.append("")

        ev_m = r["ev_managed"]
        ev_m_str = f"${ev_m:.2f}" if ev_m is not None else "N/A"
        lines.append(f"**Binary vs Managed:** binary=${c['binary_ev']:.2f}, managed={ev_m_str}\n")
        lines.append("---\n")

    out_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "diagnostics",
        "managed_ev_validation.md",
    )
    with open(out_path, "w") as f:
        f.write("\n".join(lines))

    print(f"Report written to {out_path}")
    print("\n=== QUICK RESULTS ===")
    for i, (c, r) in enumerate(results, 1):
        ev_m = r["ev_managed"]
        p_t = r["p_profit_target"]
        p_s = r["p_stop_loss"]
        p_e = r["p_expiration"]
        print(
            f"  {i}. {c['label']}: "
            f"ev_managed=${ev_m:.2f}, "
            f"p_target={p_t:.4f}, p_stop={p_s:.4f}, p_expiry={p_e:.4f}, "
            f"sum={p_t + p_s + p_e:.6f}"
        )


if __name__ == "__main__":
    main()
