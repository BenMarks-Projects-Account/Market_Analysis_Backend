from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class PlaybookService:
    def __init__(self, regime_service: Any, signal_service: Any) -> None:
        self.regime_service = regime_service
        self.signal_service = signal_service

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _confidence(base_score: float, signal_score: float, lane: str) -> float:
        base = PlaybookService._clamp(base_score / 100.0, 0.0, 1.0)
        adjustment = PlaybookService._clamp((signal_score - 50.0) / 50.0, -1.0, 1.0) * 0.10
        conf = base + adjustment
        if lane == "secondary":
            conf -= 0.10
        elif lane == "avoid":
            conf = (1.0 - base) - adjustment + 0.12
        return PlaybookService._clamp(conf, 0.0, 1.0)

    @staticmethod
    def _why_from_regime(regime: dict[str, Any], fallback: str) -> list[str]:
        out: list[str] = []
        components = regime.get("components") if isinstance(regime.get("components"), dict) else {}
        for key in ("trend", "volatility", "breadth", "rates", "momentum"):
            comp = components.get(key) if isinstance(components, dict) else None
            if isinstance(comp, dict):
                sigs = comp.get("signals")
                if isinstance(sigs, list) and sigs:
                    text = str(sigs[0] or "").strip()
                    if text:
                        out.append(text)
        if not out:
            out.append(fallback)
        return out[:3]

    def _build_templates(self, label: str) -> dict[str, list[tuple[str, str, str]]]:
        if label == "RISK_ON":
            return {
                "primary": [
                    ("put_credit_spread", "Put Credit Spreads", "Bullish premium capture in constructive tape"),
                    ("covered_call", "Covered Calls", "Income overlay with risk-on drift"),
                ],
                "secondary": [
                    ("call_debit", "Call Debit Spreads", "Selective directional upside expression"),
                    ("iron_condor", "Iron Condor (Wide)", "Range premium harvesting with wider risk bands"),
                ],
                "avoid": [
                    ("put_debit", "Put Debit Spreads", "Bearish structures de-emphasized in risk-on"),
                    ("aggressive_short_calls", "Aggressive Short Calls", "Uncapped upside risk in trending tape"),
                ],
                "notes": ["Market in risk-on regime; favor premium selling strategies."],
            }
        if label == "RISK_OFF":
            return {
                "primary": [
                    ("put_debit", "Put Debit Spreads", "Defined-risk bearish protection preferred"),
                    ("cash_secured_put_far_otm", "Cash-Secured Puts (Far OTM)", "Only deep cushion entries with strict sizing"),
                ],
                "secondary": [
                    ("calendar", "Calendar Spreads", "Time-structure expression with lower directional dependency"),
                    ("hedges", "Hedges", "Portfolio defense and convexity"),
                ],
                "avoid": [
                    ("short_put_spreads_near_spot", "Short Put Spreads Near Spot", "Assignment and gap risk elevated"),
                    ("iron_condor_tight", "Iron Condors (Tight Wings)", "Wing breach risk elevated in stress"),
                ],
                "notes": ["Risk-off backdrop; prioritize protection and conservative premium structures."],
            }
        return {
            "primary": [
                ("iron_condor", "Iron Condors", "Neutral premium selling favored in range conditions"),
                ("credit_spreads_wider", "Credit Spreads (Wider Strikes)", "Maintain risk distance while collecting premium"),
            ],
            "secondary": [
                ("calendar", "Calendar Spreads", "Term-structure opportunities in mixed trend"),
                ("butterflies", "Butterflies", "Defined-risk mean reversion / pin scenarios"),
            ],
            "avoid": [
                ("aggressive_directional_debit_spreads", "Aggressive Directional Debit Spreads", "Lower conviction for one-way directional bets"),
            ],
            "notes": ["Neutral regime; prefer balanced structures with wider risk controls."],
        }

    async def get_playbook(self) -> dict[str, Any]:
        regime_payload = await self.regime_service.get_regime()
        signal_payload = await self.signal_service.get_symbol_signals(symbol="SPY", range_key="6mo")

        regime_label = str(regime_payload.get("regime_label") or "NEUTRAL").upper()
        regime_score = float(regime_payload.get("regime_score") or 50.0)
        signal_score = float((signal_payload.get("composite") or {}).get("score") or 50.0)

        templates = self._build_templates(regime_label)

        def build_lane(name: str) -> list[dict[str, Any]]:
            rows = templates.get(name) or []
            out: list[dict[str, Any]] = []
            for strategy, label, fallback in rows:
                out.append({
                    "strategy": strategy,
                    "label": label,
                    "confidence": round(self._confidence(regime_score, signal_score, name), 2),
                    "why": self._why_from_regime(regime_payload, fallback),
                })
            return out

        notes = list(templates.get("notes") or [])
        notes.append(f"SPY composite signal score: {signal_score:.1f}")

        return {
            "as_of": self._now_iso(),
            "regime": {
                "label": regime_label,
                "score": round(regime_score, 2),
            },
            "playbook": {
                "primary": build_lane("primary"),
                "secondary": build_lane("secondary"),
                "avoid": build_lane("avoid"),
                "notes": notes,
            },
        }
