# BenTrade — Pre-Market Futures & Leading Indicators Design
## Integration Plan for Overnight/Pre-Market Edge

**Date**: 2026-03-24
**Goal**: Give BenTrade actionable pre-market intelligence from futures markets before the 9:30 AM open, replacing proxies with real data and adding overnight gap analysis.

---

## What We're Adding

### Tier 1 — Highest Impact (do first)
| Data | Source | Replaces | Impact |
|------|--------|----------|--------|
| ES futures (S&P 500) | Polygon | Nothing (new) | Gap analysis for SPY, overnight regime signal |
| NQ futures (Nasdaq 100) | Polygon | Nothing (new) | Gap analysis for QQQ |
| RTY futures (Russell 2000) | Polygon | Nothing (new) | Gap analysis for IWM |
| YM futures (Dow) | Polygon | Nothing (new) | Gap analysis for DIA |
| VIX futures (/VX front month) | Polygon or CBOE | Fabricated vix_2nd_month/3rd_month proxies | Real term structure for vol engine Pillar 2 |

### Tier 2 — High Value (do second)
| Data | Source | Replaces | Impact |
|------|--------|----------|--------|
| Crude oil futures (/CL) | Polygon | FRED DCOILWTICO (7-day stale) | Real-time oil for cross-asset engine |
| Dollar index futures (DX) | Polygon | FRED DTWEXBGS (1-3 day stale) | Real-time USD for cross-asset engine |
| 10Y Treasury futures (/ZN) | Polygon | FRED DGS10 (1-3 day stale) | Real-time rates for cross-asset + liquidity engines |

### Tier 3 — Nice to Have (later)
| Data | Source | Impact |
|------|--------|--------|
| European indices (DAX, FTSE) | Polygon/Finnhub | Global risk context pre-market |
| Asia indices (Nikkei, HSI) | Polygon/Finnhub | Overnight global sentiment |
| SOFR futures | Polygon | Replace VIX+rate proxy in liquidity engine |

---

## Architecture

### New Component: Pre-Market Intelligence Service

```
┌─────────────────────────────────────────────────────────────────┐
│              PRE-MARKET INTELLIGENCE SERVICE                     │
│                                                                  │
│  Runs: 6:00 AM ET → 9:30 AM ET (pre-market window)             │
│  Also: available 24/5 for overnight monitoring                   │
│                                                                  │
│  ┌──────────────────────┐     ┌──────────────────────┐          │
│  │  FUTURES DATA LAYER  │     │  GAP ANALYSIS ENGINE │          │
│  │                      │     │                      │          │
│  │  ES, NQ, RTY, YM     │────→│  Prior close vs now  │          │
│  │  /VX (VIX futures)   │     │  Gap direction/size  │          │
│  │  /CL (crude)         │     │  Gap classification  │          │
│  │  DX (dollar)         │     │  Overnight range     │          │
│  │  /ZN (10Y bond)      │     │  Volume profile      │          │
│  └──────────────────────┘     └──────────┬───────────┘          │
│                                          │                       │
│  ┌───────────────────────────────────────┼──────────────────┐   │
│  │         OVERNIGHT REGIME SIGNAL       ▼                  │   │
│  │                                                          │   │
│  │  Inputs:                                                 │   │
│  │    ES gap + direction                                    │   │
│  │    VIX futures term structure (contango/backwardation)    │   │
│  │    Overnight volume (conviction of the move)             │   │
│  │    Cross-asset confirmation (bonds, oil, dollar)         │   │
│  │                                                          │   │
│  │  Output:                                                 │   │
│  │    overnight_signal: BULLISH / NEUTRAL / BEARISH         │   │
│  │    gap_risk: LARGE_GAP_UP / SMALL / FLAT / LARGE_GAP_DN │   │
│  │    conviction: HIGH / MODERATE / LOW                     │   │
│  │    vix_term_structure: CONTANGO / FLAT / BACKWARDATION   │   │
│  │    cross_asset_confirmation: CONFIRMING / MIXED / DIVERGING│  │
│  └──────────────────────────────────────────────────────────┘   │
│                                          │                       │
│                                          ▼                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │         FEEDS INTO EXISTING MI ENGINES                   │   │
│  │                                                          │   │
│  │  Volatility Engine:                                      │   │
│  │    - Real VIX futures → Pillar 2 (replaces heuristic)    │   │
│  │    - VIX term structure → contango/backwardation score   │   │
│  │                                                          │   │
│  │  Cross-Asset Macro Engine:                               │   │
│  │    - Live oil → replaces 7-day stale FRED                │   │
│  │    - Live USD → replaces 1-3 day stale FRED              │   │
│  │    - Live 10Y → replaces 1-3 day stale FRED              │   │
│  │                                                          │   │
│  │  Regime Service:                                         │   │
│  │    - Overnight signal feeds into regime classification   │   │
│  │    - Gap risk modifies regime confidence                 │   │
│  │                                                          │   │
│  │  Active Trade Pipeline:                                  │   │
│  │    - "Your SPY PCS is under pressure — ES down 1.2%"    │   │
│  │    - Gap analysis in position risk assessment            │   │
│  │                                                          │   │
│  │  TMC Dashboard:                                          │   │
│  │    - Pre-market briefing panel                           │   │
│  │    - "ES -0.8%, NQ -1.2%, VIX futures in backwardation"│   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Layer Design

### Polygon Futures Tickers

Check your Polygon plan for futures support. The tickers are typically:

| Futures Contract | Polygon Ticker | Trading Hours (ET) |
|-----------------|----------------|-------------------|
| E-mini S&P 500 | `ES` or `I:ESc1` (front month) | Sun 6pm - Fri 5pm |
| E-mini Nasdaq 100 | `NQ` or `I:NQc1` | Sun 6pm - Fri 5pm |
| E-mini Russell 2000 | `RTY` or `I:RTYc1` | Sun 6pm - Fri 5pm |
| Mini Dow | `YM` or `I:YMc1` | Sun 6pm - Fri 5pm |
| VIX Futures | `VX` or `I:VXc1` / `I:VXc2` | Nearly 24h |
| Crude Oil | `CL` or `I:CLc1` | Nearly 24h |
| Dollar Index | `DX` or `I:DXc1` | Nearly 24h |
| 10Y Treasury Note | `ZN` or `I:ZNc1` | Nearly 24h |

NOTE: Polygon's exact ticker format for futures may differ. The `I:` prefix is for indices. Futures may use `/ES`, `ESc1`, or `O:ES` depending on the API version. A discovery prompt should check the actual Polygon API for the correct format.

### Futures Data Client

```python
# NEW: app/clients/futures_client.py

class FuturesClient:
    """Fetch futures data from Polygon for pre-market analysis."""
    
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._base_url = "https://api.polygon.io"
        self._cache = {}  # TTL cache per ticker
        self._cache_ttl = 30  # 30 seconds during pre-market
    
    async def get_snapshot(self, ticker: str) -> dict:
        """Get current price, change, volume for a futures contract.
        
        Returns:
            {
                "ticker": "ES",
                "last": 5245.50,
                "change": -15.25,
                "change_pct": -0.29,
                "volume": 145000,
                "high": 5268.00,
                "low": 5238.75,
                "open": 5260.75,
                "prev_close": 5260.75,
                "timestamp": "2026-03-24T06:30:00Z",
            }
        """
        # GET /v2/snapshot/locale/us/markets/futures/tickers/{ticker}
        # Or GET /v3/quotes/{ticker} depending on API version
        pass
    
    async def get_bars(self, ticker: str, timeframe: str = "5min", limit: int = 60) -> list:
        """Get recent bars for overnight range analysis.
        
        Returns list of OHLCV bars for the overnight session.
        """
        # GET /v2/aggs/ticker/{ticker}/range/5/minute/{from}/{to}
        pass
    
    async def get_vix_term_structure(self) -> dict:
        """Get VIX futures front and second month for term structure.
        
        Returns:
            {
                "front_month": {"ticker": "VXc1", "last": 26.15, "expiry": "2026-04-15"},
                "second_month": {"ticker": "VXc2", "last": 24.80, "expiry": "2026-05-20"},
                "spread": -1.35,
                "structure": "backwardation",  # or "contango" or "flat"
                "contango_pct": -5.2,  # (M2-M1)/M1 × 100
            }
        """
        pass
```

---

## Gap Analysis Engine

The gap is the difference between the prior session's close and where futures are trading now. This is the most actionable pre-market signal.

```python
# NEW: app/services/pre_market_intelligence.py

class GapAnalysis:
    """Compute overnight gap and classify its significance."""
    
    # Gap classification thresholds (percentage of underlying)
    LARGE_GAP = 0.01    # ±1.0%
    MEDIUM_GAP = 0.005   # ±0.5%
    
    @staticmethod
    def classify_gap(prior_close: float, current: float) -> dict:
        if prior_close <= 0:
            return {"gap_pct": 0, "classification": "unknown"}
        
        gap_pct = (current - prior_close) / prior_close
        
        if gap_pct > LARGE_GAP:
            classification = "large_gap_up"
        elif gap_pct > MEDIUM_GAP:
            classification = "gap_up"
        elif gap_pct > -MEDIUM_GAP:
            classification = "flat"
        elif gap_pct > -LARGE_GAP:
            classification = "gap_down"
        else:
            classification = "large_gap_down"
        
        return {
            "gap_pct": round(gap_pct, 4),
            "gap_points": round(current - prior_close, 2),
            "classification": classification,
            "prior_close": prior_close,
            "current": current,
        }
```

### Overnight Regime Signal

Combines futures gaps with VIX term structure and cross-asset confirmation:

```python
def compute_overnight_signal(
    *,
    es_gap: dict,    # S&P 500 futures gap
    nq_gap: dict,    # Nasdaq gap
    rty_gap: dict,   # Russell gap
    vix_structure: dict,  # VIX term structure
    oil_change_pct: float | None = None,
    dollar_change_pct: float | None = None,
    bond_change_pct: float | None = None,
) -> dict:
    """Compute overnight regime signal from futures data.
    
    Returns:
        {
            "signal": "BULLISH" | "NEUTRAL" | "BEARISH",
            "conviction": "HIGH" | "MODERATE" | "LOW",
            "gap_risk": "LARGE_GAP_UP" | "GAP_UP" | "FLAT" | "GAP_DOWN" | "LARGE_GAP_DOWN",
            "vix_term_structure": "CONTANGO" | "FLAT" | "BACKWARDATION",
            "cross_asset_confirmation": "CONFIRMING" | "MIXED" | "DIVERGING",
            "details": { ... },
        }
    """
    # Score direction from futures (weighted by market cap representation)
    es_score = _gap_to_score(es_gap["gap_pct"])    # Weight: 0.40
    nq_score = _gap_to_score(nq_gap["gap_pct"])    # Weight: 0.30
    rty_score = _gap_to_score(rty_gap["gap_pct"])   # Weight: 0.20
    
    # VIX term structure signal
    # Contango (M2 > M1) = complacent/bullish
    # Backwardation (M2 < M1) = fear/bearish
    vix_score = 0
    structure = vix_structure.get("structure", "flat")
    if structure == "contango":
        vix_score = 0.6  # Mildly bullish
    elif structure == "backwardation":
        vix_score = -0.8  # Bearish — fear premium
    
    # Composite directional score (-1 to +1)
    direction_score = (
        es_score * 0.40 +
        nq_score * 0.30 +
        rty_score * 0.20 +
        vix_score * 0.10
    )
    
    # Cross-asset confirmation
    confirming_count = 0
    diverging_count = 0
    
    if oil_change_pct is not None:
        if (direction_score > 0 and oil_change_pct > 0.005) or \
           (direction_score < 0 and oil_change_pct < -0.005):
            confirming_count += 1
        elif abs(oil_change_pct) > 0.005:
            diverging_count += 1
    
    if dollar_change_pct is not None:
        # Dollar typically inverse to equities
        if (direction_score > 0 and dollar_change_pct < -0.002) or \
           (direction_score < 0 and dollar_change_pct > 0.002):
            confirming_count += 1
        elif abs(dollar_change_pct) > 0.002:
            diverging_count += 1
    
    if bond_change_pct is not None:
        # Bonds typically inverse to equities (risk-on/risk-off)
        if (direction_score > 0 and bond_change_pct < 0) or \
           (direction_score < 0 and bond_change_pct > 0):
            confirming_count += 1
        elif abs(bond_change_pct) > 0.001:
            diverging_count += 1
    
    # Classify
    if confirming_count >= 2:
        cross_asset = "CONFIRMING"
    elif diverging_count >= 2:
        cross_asset = "DIVERGING"
    else:
        cross_asset = "MIXED"
    
    # Final signal
    if direction_score > 0.3:
        signal = "BULLISH"
    elif direction_score < -0.3:
        signal = "BEARISH"
    else:
        signal = "NEUTRAL"
    
    # Conviction
    agreement = abs(es_score) > 0.3 and abs(nq_score) > 0.3 and \
                (es_score * nq_score > 0)  # Same direction
    if agreement and cross_asset == "CONFIRMING":
        conviction = "HIGH"
    elif agreement or cross_asset == "CONFIRMING":
        conviction = "MODERATE"
    else:
        conviction = "LOW"
    
    return {
        "signal": signal,
        "conviction": conviction,
        "direction_score": round(direction_score, 3),
        "gap_risk": es_gap["classification"],  # Use ES as primary
        "vix_term_structure": structure.upper(),
        "cross_asset_confirmation": cross_asset,
        "details": {
            "es": es_gap,
            "nq": nq_gap,
            "rty": rty_gap,
            "vix": vix_structure,
            "oil_change_pct": oil_change_pct,
            "dollar_change_pct": dollar_change_pct,
            "bond_change_pct": bond_change_pct,
        },
    }


def _gap_to_score(gap_pct: float) -> float:
    """Convert gap percentage to -1/+1 score."""
    if gap_pct > 0.02: return 1.0
    if gap_pct > 0.01: return 0.7
    if gap_pct > 0.005: return 0.4
    if gap_pct > -0.005: return 0.0
    if gap_pct > -0.01: return -0.4
    if gap_pct > -0.02: return -0.7
    return -1.0
```

---

## Integration Points

### 1. Volatility Engine — Replace Fabricated Term Structure

Currently `vix_2nd_month` and `vix_3rd_month` are fabricated from VIX spot vs 20-day average. With real VIX futures:

```python
# In volatility_options_data_provider.py or volatility_options_engine.py:

# BEFORE (fabricated):
vix_2nd_month = vix_spot * (1 + heuristic_from_20d_avg)  # PROXY

# AFTER (real data):
vix_futures = await futures_client.get_vix_term_structure()
vix_2nd_month = vix_futures["front_month"]["last"]   # DIRECT
vix_3rd_month = vix_futures["second_month"]["last"]   # DIRECT
term_structure = vix_futures["structure"]              # DIRECT

# Update SIGNAL_PROVENANCE:
# vix_2nd_month: type changes from "proxy" to "direct"
# vix_3rd_month: type changes from "proxy" to "direct"
# This drops the vol engine's proxy count from 5+ to 3
```

### 2. Cross-Asset Engine — Replace Stale FRED with Live Futures

```python
# In cross_asset_macro_data_provider.py:

# BEFORE:
oil_price = fred_client.get_latest("DCOILWTICO")  # 7 days stale
usd_index = fred_client.get_latest("DTWEXBGS")    # 1-3 days stale
ten_year = fred_client.get_latest("DGS10")         # 1-3 days stale

# AFTER (during market/pre-market hours):
from app.utils.market_hours import market_status

if market_status() in ("open", "extended"):
    # Use live futures
    oil = await futures_client.get_snapshot("CL")
    oil_price = oil["last"]  # Real-time
    
    usd = await futures_client.get_snapshot("DX")
    usd_index = usd["last"]  # Real-time
    
    tn = await futures_client.get_snapshot("ZN")
    # Convert bond futures price to approximate yield
    ten_year_yield = _bond_futures_to_yield(tn["last"])
else:
    # Off-hours: fall back to FRED (still valid for prior close)
    oil_price = fred_client.get_latest("DCOILWTICO")
    usd_index = fred_client.get_latest("DTWEXBGS")
    ten_year = fred_client.get_latest("DGS10")
```

### 3. MI Runner — Pre-Market Mode

```python
# In MI Runner, add pre-market awareness:

status = market_status()

if status in ("extended",):  # Pre-market (4 AM - 9:30 AM ET)
    # Load futures data
    pre_market = await pre_market_service.build_briefing()
    
    # Include overnight signal in MI output
    mi_output["pre_market"] = {
        "overnight_signal": pre_market["signal"],
        "gap_risk": pre_market["gap_risk"],
        "vix_term_structure": pre_market["vix_term_structure"],
        "es_gap_pct": pre_market["details"]["es"]["gap_pct"],
        "nq_gap_pct": pre_market["details"]["nq"]["gap_pct"],
    }
    
    # Feed into regime classification
    # A BEARISH overnight signal with HIGH conviction should shift
    # the regime toward RISK_OFF even if yesterday's close was RISK_ON
```

### 4. Active Trade Pipeline — Overnight Risk Alert

```python
# In Stage 3 (build_packets), add overnight exposure:

if pre_market_data:
    for trade in trades:
        underlying = trade["underlying"]
        futures_map = {"SPY": "es", "QQQ": "nq", "IWM": "rty", "DIA": "ym"}
        
        if underlying in futures_map:
            futures_key = futures_map[underlying]
            gap = pre_market_data["details"].get(futures_key, {})
            
            packet["overnight_exposure"] = {
                "futures_gap_pct": gap.get("gap_pct", 0),
                "gap_classification": gap.get("classification", "unknown"),
                "impact": _assess_overnight_impact(trade, gap),
            }
```

### 5. TMC Pre-Market Briefing Panel

```
┌─────────────────────────────────────────────────┐
│  PRE-MARKET BRIEFING  (6:45 AM ET)              │
│                                                  │
│  Overnight Signal: ▼ BEARISH (HIGH conviction)   │
│                                                  │
│  ES: 5,245 (-0.8%)  NQ: 18,320 (-1.2%)         │
│  RTY: 2,080 (-0.5%)  YM: 39,400 (-0.6%)        │
│                                                  │
│  VIX Futures: BACKWARDATION (fear premium)        │
│  /VX Front: 28.5  /VX M2: 26.8  Spread: -1.7    │
│                                                  │
│  Cross-Asset: CONFIRMING                         │
│  Oil: -1.1%  Dollar: +0.4%  10Y: +3bp           │
│                                                  │
│  ⚠️ ACTIVE POSITION ALERTS:                      │
│  • SPY PCS 640/635: ES gap -0.8% pressures short │
│  • QQQ IC 470/480/500/510: NQ -1.2% threatens    │
│    call side                                      │
│                                                  │
│  [Run Full Refresh with Pre-Market Data]          │
└─────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase A: Data Foundation (do first)
1. Check Polygon plan for futures data access
2. Build FuturesClient with snapshot + bars + VIX term structure
3. Build GapAnalysis engine
4. Build overnight signal computation
5. Add pre-market briefing endpoint

### Phase B: Engine Integration
6. Replace vol engine vix_2nd/3rd_month with real VIX futures
7. Replace cross-asset stale FRED with live futures (market hours only)
8. Update SIGNAL_PROVENANCE (proxies become direct)
9. Feed overnight signal into regime classification

### Phase C: Position Awareness
10. Add overnight exposure to active trade packets
11. Flag positions at risk from overnight gaps
12. Include in active trade model prompt

### Phase D: TMC Display
13. Pre-market briefing panel in TMC
14. Position alerts from overnight gaps
15. "Run Full Refresh with Pre-Market Data" button

---

## What Changes in the Audit Findings

| Current Finding | Status After Integration |
|----------------|------------------------|
| vix_2nd_month: "FABRICATED HEURISTIC" | ✅ Replaced with real VIX futures — type becomes "direct" |
| vix_3rd_month: "FABRICATED HEURISTIC" | ✅ Replaced with real VIX futures — type becomes "direct" |
| Oil WTI: 7 days stale (FRED) | ✅ Real-time during market hours via /CL futures |
| USD index: 1-3 days stale (FRED) | ✅ Real-time during market hours via DX futures |
| 10Y yield: 1-3 days stale (FRED) | ✅ Real-time during market hours via /ZN futures |
| Vol engine proxy_count: 5+ | ✅ Drops to 3 (vix_rank and option_richness remain proxies) |
| No pre-market intelligence | ✅ Full overnight gap analysis + regime signal |
| No position-level overnight risk | ✅ Active trades flagged when futures gap threatens position |

---

## First Step: Polygon Futures Discovery

Before building anything, we need to verify Polygon supports futures on your plan. Here's a discovery prompt:

```
Check your Polygon.io plan and API access:
1. Try: GET https://api.polygon.io/v3/reference/tickers?type=INDEX&search=ES&apiKey=YOUR_KEY
2. Try: GET https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/ES?apiKey=YOUR_KEY
3. Try: GET https://api.polygon.io/v2/aggs/ticker/I:SPX/range/5/minute/2026-03-23/2026-03-24?apiKey=YOUR_KEY
4. Check your plan at https://polygon.io/dashboard — does it include "Indices" or "Futures"?

If futures aren't on your plan:
- Alternative: Use Yahoo Finance yfinance for delayed futures (free, 15-min delay)
  - ES=F, NQ=F, RTY=F, YM=F, CL=F, GC=F, ^VIX
- Alternative: Use Finnhub for some futures data (check plan)
- Alternative: Use CBOE delayed data for VIX futures term structure

Report: which tickers work, what data you get back, and what your plan includes.
```
