/**
 * BenTrade — Centralized Tooltip Dictionary.
 *
 * Single source of truth for ALL tooltip content across the application.
 * Every metric, KPI label, regime component, strategy chip, and admin
 * indicator that shows a tooltip MUST have an entry here.
 *
 * Entry shapes:
 *   METRIC entry (used by metric-tooltip system):
 *     { label, short, formula?, why?, notes? }
 *
 *   RICH entry (used by ben-tip system for regime/strategy chips):
 *     { title, body, impact?, conditions?:string[], risk? }
 *
 * The tooltip rendering layer auto-detects the shape and renders
 * the appropriate template.
 *
 * Adding a new tooltip:
 *   1. Choose a stable key: "section.metric_name" or "metric_name"
 *   2. Add the entry to the appropriate section below.
 *   3. In HTML/JS, add data-metric="key" or data-ben-tip="key" to the element.
 *   4. The MutationObserver auto-binds it — no per-page init needed.
 *
 * Dependencies: none (loaded before tooltip.js and ben_tooltip.js)
 */
window.BenTradeTooltipDictionary = (function () {
  'use strict';

  /* ================================================================
   *  METRIC GLOSSARY  (data-metric system)
   *  Input fields → formula → UI output traceability
   * ================================================================ */

  var METRICS = {

    /* ── Moving Averages / Technical ── */
    ema_20: {
      label: 'EMA 20',
      short: 'Exponential moving average over 20 periods with more weight on recent prices.',
      formula: 'EMA_t = α·P_t + (1−α)·EMA_{t−1}, α = 2/(n+1)',
      why: 'Tracks trend shifts faster than SMA and helps dynamic support/resistance reads.',
      notes: 'Use with SMA and RSI for confirmation.',
    },
    sma_20: {
      label: 'SMA 20',
      short: 'Simple moving average of the last 20 closes.',
      formula: 'SMA_n = (1/n)·Σ P_i',
      why: 'Smooths noise to reveal the near-term trend baseline.',
      notes: 'Crosses with EMA/SMA50 are useful context.',
    },
    sma_50: {
      label: 'SMA 50',
      short: 'Simple moving average of the last 50 closes.',
      formula: 'SMA_n = (1/n)·Σ P_i',
      why: 'Represents a medium-term trend anchor.',
      notes: 'Price above SMA50 often indicates stronger trend regime.',
    },
    rsi_14: {
      label: 'RSI 14',
      short: 'Momentum oscillator over 14 periods on a 0–100 scale.',
      formula: 'RSI = 100 − 100/(1 + RS)',
      why: 'Helps spot momentum exhaustion and overbought/oversold zones.',
      notes: 'Interpret with trend; strong trends can pin RSI.',
    },

    /* ── Volatility ── */
    realized_vol_20d: {
      label: 'Realized Vol 20d',
      short: 'Annualized volatility estimated from recent daily returns.',
      formula: 'RV ≈ stdev(returns)·√252',
      why: 'Baseline for comparing implied volatility richness.',
      notes: 'Short windows are sensitive to recent shocks.',
    },
    iv: {
      label: 'Implied Volatility',
      short: 'Market-implied forward volatility from option prices.',
      formula: 'Solved from option model price (e.g., Black-Scholes)',
      why: 'Drives option premium and expected move estimates.',
      notes: 'Often quoted annualized.',
    },
    expected_move_1w: {
      label: 'Expected Move (1w)',
      short: 'Estimated one-standard-deviation move over roughly one week.',
      formula: 'EM ≈ S·IV·√(T)',
      why: 'Sets practical strike-distance and risk framing.',
      notes: 'Directional bias is not implied by EM alone.',
    },
    iv_rv_ratio: {
      label: 'IV/RV Ratio',
      short: 'Ratio of implied volatility to realized volatility.',
      formula: 'IV/RV = IV ÷ RV',
      why: 'Quick gauge for option richness versus observed movement.',
      notes: '>1 can favor selling volatility, context dependent.',
    },
    iv_rank: {
      label: 'IV Rank',
      short: 'Current IV percentile within its lookback high-low range.',
      formula: 'IV Rank = (IV − IV_low)/(IV_high − IV_low)',
      why: 'Context for whether options are relatively rich/cheap.',
      notes: 'Depends on selected lookback period.',
    },

    /* ── Core Trade Metrics ── */
    pop: {
      label: 'POP',
      short: 'Probability of finishing with profit by expiry under model assumptions.',
      formula: 'Model-derived probability from distribution assumptions',
      why: 'Useful for hit-rate expectation, not payoff magnitude.',
      notes: 'Combine with EV and max loss, not standalone.',
    },
    ev: {
      label: 'Expected Value',
      short: 'Probability-weighted expected outcome per trade.',
      formula: 'EV = Σ p_i · payoff_i',
      why: 'Positive EV supports long-run edge if assumptions hold.',
      notes: 'Sensitive to probability and tail-loss estimates.',
    },
    ev_to_risk: {
      label: 'EV to Risk',
      short: 'Expected value normalized by risk capital.',
      formula: 'EV/Risk = EV ÷ MaxLoss',
      why: 'Compares trade efficiency across different sizes.',
      notes: 'Higher is generally better.',
    },
    return_on_risk: {
      label: 'Return on Risk',
      short: 'Maximum potential return divided by maximum risk.',
      formula: 'RoR = MaxProfit ÷ MaxLoss',
      why: 'Simple reward-to-risk comparison.',
      notes: 'Does not include probability weighting.',
    },
    break_even: {
      label: 'Break-even',
      short: 'Underlying price at expiry where P&L is zero.',
      formula: 'Depends on structure (e.g., short strike − credit)',
      why: 'Defines the margin of safety boundary.',
      notes: 'Break-even can shift with assignment/fees in practice.',
    },
    max_profit: {
      label: 'Max Profit',
      short: 'Best-case payoff if the trade resolves optimally.',
      formula: 'Structure-specific payoff cap',
      why: 'Upper bound for reward and RoR calculation.',
      notes: 'Often net credit for short spreads.',
    },
    max_loss: {
      label: 'Max Loss',
      short: 'Worst-case payoff if the trade moves fully against you.',
      formula: 'Structure-specific loss cap',
      why: 'Primary capital-at-risk constraint.',
      notes: 'Include quantity/contract multiplier when sizing.',
    },
    credit: {
      label: 'Credit Received',
      short: 'Net premium collected when opening a credit strategy.',
      formula: 'Credit = short leg premium − long leg premium',
      why: 'Defines max profit for credit spreads and income trades.',
      notes: 'Per-contract value; multiply by quantity for total.',
    },
    spread_width: {
      label: 'Spread Width',
      short: 'Distance in dollars between the short and long strikes.',
      formula: 'Width = |short strike − long strike|',
      why: 'Determines max loss (= width − credit) for vertical spreads.',
      notes: 'Wider spreads collect more credit but risk more capital.',
    },
    open_interest: {
      label: 'Open Interest',
      short: 'Total number of outstanding option contracts at this strike.',
      formula: 'Reported by exchange daily',
      why: 'Proxy for liquidity depth and institutional participation.',
      notes: 'Higher OI generally means tighter bid-ask spreads.',
    },
    volume: {
      label: 'Volume',
      short: 'Number of contracts traded in the current session.',
      formula: 'Reported by exchange in real-time',
      why: 'Indicates current-day liquidity and activity.',
      notes: 'Spikes may signal institutional interest or events.',
    },

    /* ── Greeks ── */
    delta: {
      label: 'Delta',
      short: 'Approximate change in position value for a $1 underlying move.',
      formula: 'Δ ≈ ∂Price/∂S',
      why: 'Measures directional exposure.',
      notes: 'Portfolio delta aggregates net directional bias.',
    },
    gamma: {
      label: 'Gamma',
      short: 'Rate of change of delta with respect to price.',
      formula: 'Γ ≈ ∂²Price/∂S²',
      why: 'Indicates how quickly directional risk can change.',
      notes: 'Higher gamma implies more convexity and re-hedge need.',
    },
    theta: {
      label: 'Theta',
      short: 'Sensitivity of option value to time decay.',
      formula: 'Θ ≈ ∂Price/∂t',
      why: 'Shows daily carry from time passing.',
      notes: 'Sign and magnitude vary by structure.',
    },
    vega: {
      label: 'Vega',
      short: 'Sensitivity of option value to implied volatility changes.',
      formula: 'Vega ≈ ∂Price/∂IV',
      why: 'Captures volatility exposure independent of direction.',
      notes: 'Can dominate around events/earnings.',
    },

    /* ── Time / Expiration ── */
    dte: {
      label: 'DTE',
      short: 'Days remaining until expiration.',
      formula: 'DTE = expiry date − current date',
      why: 'Controls decay pace and assignment/event risk window.',
      notes: 'Shorter DTE usually means faster theta and gamma changes.',
    },

    /* ── Position / P&L ── */
    mark: {
      label: 'Mark',
      short: 'Current estimated fill/mid value of the position.',
      formula: 'Often midpoint of bid/ask',
      why: 'Used for live valuation and close simulation.',
      notes: 'Executable price can differ in fast markets.',
    },
    unrealized_pnl: {
      label: 'Unrealized P&L',
      short: 'Open-trade profit or loss based on current mark.',
      formula: 'Unrealized P&L = current value − open basis',
      why: 'Tracks live performance before closing.',
      notes: 'Can change materially intraday.',
    },
    unrealized_pnl_pct: {
      label: 'Unrealized P&L %',
      short: 'Unrealized P&L expressed as a percentage of risk/basis.',
      formula: 'P&L% = Unrealized P&L ÷ basis',
      why: 'Normalizes performance across trades.',
      notes: 'Check denominator definition in context.',
    },

    /* ── Scoring / Ranking ── */
    kelly_fraction: {
      label: 'Kelly Fraction',
      short: 'Model-based position fraction maximizing long-run growth.',
      formula: 'f* ≈ edge ÷ odds (simplified)',
      why: 'Guides conservative sizing relative to edge.',
      notes: 'Often scaled down in practice.',
    },
    trade_quality_score: {
      label: 'Trade Quality Score',
      short: 'Composite quality metric from multiple trade signals.',
      formula: 'Weighted composite of POP, EV, RoR, liquidity, etc.',
      why: 'Ranks opportunities quickly on one scale.',
      notes: 'Always inspect underlying components.',
    },
    composite_score: {
      label: 'Composite Score',
      short: 'Overall ranking score for candidate comparison.',
      formula: 'Model-specific weighted score',
      why: 'Helps prioritize review order.',
      notes: 'Interpret relative to peer candidates.',
    },
    rank_score: {
      label: 'Rank Score',
      short: 'Normalized rank-oriented score used for sorting.',
      formula: 'Model-specific normalization of quality metrics',
      why: 'Provides fast shortlist ordering.',
      notes: 'Useful for scanner triage.',
    },

    /* ── Stock Strategy Scores ── */
    trend_score: {
      label: 'Trend Score',
      short: 'Composite trend-strength reading from price action and moving averages.',
      formula: 'Weighted composite of EMA/SMA alignment, slope, and price position',
      why: 'Quantifies directional conviction for entry timing.',
      notes: 'Higher values indicate stronger, more aligned trend.',
    },
    momentum_score: {
      label: 'Momentum Score',
      short: 'Composite momentum reading from RSI, rate-of-change, and volume.',
      formula: 'Weighted blend of RSI, ROC, and volume trend signals',
      why: 'Captures whether price movement has follow-through.',
      notes: 'Can diverge from trend during reversals.',
    },
    pullback_score: {
      label: 'Pullback Score',
      short: 'Measures depth and quality of a pullback within a prevailing trend.',
      formula: 'Based on retracement depth, RSI dip, and support proximity',
      why: 'Higher values suggest a better risk/reward entry on a dip.',
      notes: 'Most useful when trend score is also strong.',
    },
    catalyst_score: {
      label: 'Catalyst Score',
      short: 'Proximity and strength of upcoming fundamental or technical catalysts.',
      formula: 'Heuristic from earnings, events, volume spikes, and breakout setups',
      why: 'Flags candidates with near-term price-moving events.',
      notes: 'Interpret alongside volatility context.',
    },
    volatility_score: {
      label: 'Volatility Score',
      short: 'Composite volatility regime classification score.',
      formula: 'Blended from realized vol, ATR, and range compression metrics',
      why: 'Helps match strategy type to current vol environment.',
      notes: 'Low score can indicate compression (potential breakout).',
    },

    /* ── Spread Quality ── */
    short_strike_z: {
      label: 'Short Strike Z',
      short: 'Distance of short strike from spot in sigma units.',
      formula: 'Z ≈ (strike − spot) ÷ expected move',
      why: 'Summarizes cushion to short strike.',
      notes: 'Higher absolute cushion usually lowers POP risk.',
    },
    bid_ask_spread_pct: {
      label: 'Bid/Ask Spread %',
      short: 'Relative width of quote spread versus mid.',
      formula: '(ask − bid) ÷ mid',
      why: 'Proxy for liquidity and slippage risk.',
      notes: 'Lower is typically better.',
    },
    strike_distance_pct: {
      label: 'Strike Distance %',
      short: 'Percent distance between relevant strike and spot.',
      formula: '|strike − spot| ÷ spot',
      why: 'Quick measure of moneyness and buffer.',
      notes: 'Use with expected move and DTE.',
    },

    /* ── Market Regime (metric system entries) ── */
    market_regime: {
      label: 'Market Regime',
      short: 'Qualitative trend/volatility state label.',
      formula: 'Rule-based classification from trend and vol inputs',
      why: 'Helps align strategy type with conditions.',
      notes: 'Regime tags are model abstractions.',
    },

    /* ── Portfolio / Risk Management ── */
    risk_remaining: {
      label: 'Risk Remaining',
      short: 'Unused risk budget under active policy constraints.',
      formula: 'Risk Remaining = Risk Cap − Risk Used',
      why: 'Prevents over-allocation of portfolio risk.',
      notes: 'Track alongside hard/soft warnings.',
    },
    estimated_risk: {
      label: 'Estimated Risk',
      short: 'Approximate capital at risk for a position.',
      formula: 'Structure-specific risk estimate',
      why: 'Sizing and concentration control anchor.',
      notes: 'May be approximate when data is partial.',
    },

    /* ── Performance ── */
    win_rate: {
      label: 'Win Rate',
      short: 'Fraction of closed trades with positive realized P&L.',
      formula: 'Win Rate = wins ÷ closed trades',
      why: 'Evaluates hit-rate behavior by strategy.',
      notes: 'Should be interpreted with payoff asymmetry.',
    },
    total_pnl: {
      label: 'Total P&L',
      short: 'Aggregate realized profit/loss over selected period.',
      formula: 'Total P&L = Σ realized P&L',
      why: 'Primary performance outcome summary.',
      notes: 'Range selection materially changes interpretation.',
    },
    avg_pnl: {
      label: 'Average P&L',
      short: 'Mean realized P&L per trade in selected set.',
      formula: 'Avg P&L = Total P&L ÷ trade count',
      why: 'Normalizes returns by activity level.',
      notes: 'Outliers can skew mean values.',
    },
    max_drawdown: {
      label: 'Max Drawdown',
      short: 'Largest peak-to-trough decline in cumulative P&L.',
      formula: 'MDD = max(peak − trough)',
      why: 'Key downside risk and pain metric.',
      notes: 'Essential for survivability assessment.',
    },

    /* ── Home Dashboard / Macro KPIs ── */
    spy_price: {
      label: 'SPY Price',
      short: 'Current price of the SPDR S&P 500 ETF.',
      formula: 'Last trade or mid from market data provider',
      why: 'Primary benchmark for broad market direction and portfolio context.',
      notes: 'Source: Tradier or fallback provider.',
    },
    vix_level: {
      label: 'VIX Level',
      short: 'CBOE Volatility Index — market-implied 30-day S&P 500 volatility.',
      formula: 'Derived from SPX option prices by CBOE',
      why: 'Measures market fear/greed; drives premium-selling attractiveness.',
      notes: 'VIX > 20 often signals elevated premium selling opportunities.',
    },
    ten_year_yield: {
      label: '10Y Yield',
      short: 'US 10-Year Treasury yield — key interest rate proxy.',
      formula: 'Reported by Treasury/FRED',
      why: 'Rising yields can pressure growth equities and alter regime.',
      notes: 'Watch rate-of-change more than absolute level.',
    },
    fed_funds: {
      label: 'Fed Funds',
      short: 'Federal Funds effective rate — overnight interbank lending rate.',
      formula: 'Set by Federal Reserve Open Market Committee',
      why: 'Anchors risk-free rate assumptions and carry costs.',
      notes: 'Changes signal major macro policy shifts.',
    },
    cpi_yoy: {
      label: 'CPI YoY',
      short: 'Consumer Price Index year-over-year change — headline inflation measure.',
      formula: 'CPI YoY = (CPI_current / CPI_prior_year − 1) × 100',
      why: 'Inflation outlook drives Fed policy and regime classification.',
      notes: 'Watch core CPI for less volatile signal.',
    },
    capital_at_risk: {
      label: 'Capital at Risk',
      short: 'Total capital currently deployed across open positions.',
      formula: 'Σ max_loss per open trade',
      why: 'Portfolio-level drawdown exposure under worst-case.',
      notes: 'Compare to risk budget limits.',
    },
    risk_utilization: {
      label: 'Risk Utilization',
      short: 'Percentage of available risk budget currently in use.',
      formula: 'Risk Used ÷ Risk Cap × 100',
      why: 'Guards against over-concentration and supports sizing discipline.',
      notes: 'Approaching 100% means no room for new trades.',
    },
    total_risk_used: {
      label: 'Total Risk Used',
      short: 'Aggregate max-loss across all open positions.',
      formula: 'Σ max_loss for all open trades',
      why: 'Portfolio-level risk snapshot for capital management.',
      notes: 'Include pending orders if applicable.',
    },
    max_trade_pct: {
      label: 'Max Trade %',
      short: 'Largest single-trade allocation as percent of risk budget.',
      formula: 'max(trade risk) ÷ risk cap × 100',
      why: 'Flags concentration risk in any one position.',
      notes: 'Policy typically caps single-trade at 5-15% of budget.',
    },
    max_symbol_pct: {
      label: 'Max Symbol %',
      short: 'Largest per-symbol exposure as percent of risk budget.',
      formula: 'max(Σ risk per symbol) ÷ risk cap × 100',
      why: 'Prevents correlated-loss blowup from one underlying.',
      notes: 'Common cap: 20-30% per symbol.',
    },
    open_trades: {
      label: 'Open Trades',
      short: 'Count of currently active positions.',
      formula: 'Count of trades with status = open',
      why: 'Workload and diversification indicator.',
      notes: 'More trades require more monitoring bandwidth.',
    },
    avg_open: {
      label: 'Average Open',
      short: 'Average number of days positions have been open.',
      formula: 'Avg(current date − open date) across open trades',
      why: 'Indicates holding period patterns and theta capture.',
      notes: 'Longer open times may signal stuck positions.',
    },

    /* ── Stock Scanner / Strategy KPIs ── */
    candidates: {
      label: 'Candidates',
      short: 'Number of symbols passing initial scanner filters.',
      formula: 'Count after pre-filter stage',
      why: 'Shows scanner breadth — how many names made it through screening.',
      notes: 'Lower count in strict mode is expected.',
    },
    universe: {
      label: 'Universe',
      short: 'Total number of symbols in the scan universe.',
      formula: 'Size of configured watchlist or index constituents',
      why: 'Context for candidate pass-rate (candidates ÷ universe).',
      notes: 'Larger universes may need stricter filters.',
    },
    lastScan: {
      label: 'Last Scan',
      short: 'Timestamp of the most recent scanner run.',
      formula: 'Recorded at scan completion',
      why: 'Confirms data freshness for decision-making.',
      notes: 'Stale scans may not reflect current market conditions.',
    },
    dataStatus: {
      label: 'Data Status',
      short: 'Health indicator for underlying data feeds.',
      formula: 'Composite of provider availability and staleness checks',
      why: 'Warns when data quality may compromise scan results.',
      notes: 'Red/yellow/green status levels.',
    },

    /* ── Data Health / Admin ── */
    data_provider_status: {
      label: 'Provider Status',
      short: 'Operational health of a market data provider.',
      formula: 'Based on last successful response time and error rate',
      why: 'Ensures data pipelines are functional before trading decisions.',
      notes: 'Check individual provider cards for detail.',
    },
    data_staleness: {
      label: 'Data Staleness',
      short: 'Time since last successful data refresh.',
      formula: 'Current time − last update timestamp',
      why: 'Stale data increases risk of incorrect pricing.',
      notes: 'Alerts typically fire after 5-15 minutes depending on source.',
    },
    api_latency: {
      label: 'API Latency',
      short: 'Round-trip time for the most recent API call.',
      formula: 'Response timestamp − request timestamp',
      why: 'Monitors backend responsiveness.',
      notes: 'High latency may delay scanner results.',
    },

    /* ── Session Stats ── */
    session_scans: {
      label: 'Session Scans',
      short: 'Number of scanner runs performed this session.',
      formula: 'Count of scan invocations since page load',
      why: 'Tracks scanning activity and API usage.',
      notes: 'Resets on page refresh.',
    },
    session_trades_reviewed: {
      label: 'Trades Reviewed',
      short: 'Number of trade candidates reviewed this session.',
      formula: 'Count of trade card expansions/views',
      why: 'Measures engagement and workflow throughput.',
      notes: 'Resets on page refresh.',
    },

    /* ── Active Trades Dashboard ── */
    trade_source: {
      label: 'Source',
      short: 'Origin of trade data (paper or live brokerage).',
      formula: 'Configured platform connection',
      why: 'Confirms whether active trades are paper or real.',
      notes: 'Paper and live should never mix in P&L calculations.',
    },
    trade_mode: {
      label: 'Mode',
      short: 'Current trading mode — paper, hybrid, or live.',
      formula: 'Set in platform settings',
      why: 'Controls which execution path is used.',
      notes: 'Change requires confirmation to prevent accidental live trades.',
    },

    /* ── Stock Analysis ── */
    price_change: {
      label: 'Change',
      short: 'Dollar price change from prior session close.',
      formula: 'Current price − prior close',
      why: 'Quick sense of the day\'s direction.',
      notes: 'Use with percent change for context.',
    },
    price_change_pct: {
      label: 'Change %',
      short: 'Percentage price change from prior session close.',
      formula: '(Current − prior close) ÷ prior close × 100',
      why: 'Normalizes move magnitude across different price levels.',
      notes: 'Gaps can distort intraday interpretation.',
    },
    range_high: {
      label: 'Range High',
      short: 'Highest price in the selected lookback range.',
      formula: 'max(high) over range',
      why: 'Defines resistance context and breakout levels.',
      notes: 'Time-frame dependent.',
    },
    range_low: {
      label: 'Range Low',
      short: 'Lowest price in the selected lookback range.',
      formula: 'min(low) over range',
      why: 'Defines support context and breakdown levels.',
      notes: 'Time-frame dependent.',
    },

    /* ── Trade Lifecycle ── */
    trade_status: {
      label: 'Trade Status',
      short: 'Current lifecycle phase: open, closing, closed, expired.',
      formula: 'Derived from position state and expiration',
      why: 'Determines available actions and reporting bucket.',
      notes: 'Transitions may update on next data refresh.',
    },
    days_held: {
      label: 'Days Held',
      short: 'Calendar days the position has been open.',
      formula: 'Today − open date',
      why: 'Theta decay accelerates near expiration; longer holds may mean adjustment is needed.',
      notes: 'Compare to DTE for time management.',
    },
    profit_target_pct: {
      label: 'Profit Target %',
      short: 'Percentage of max profit at which the position auto-closes.',
      formula: 'Set by management rules (e.g., 50% of credit)',
      why: 'Locks in gains and frees capital for new trades.',
      notes: 'Common values: 50-75% of credit.',
    },
    stop_loss_pct: {
      label: 'Stop Loss %',
      short: 'Loss threshold at which the position is exited.',
      formula: 'Set by management rules (e.g., 200% of credit)',
      why: 'Caps adverse outcomes and protects portfolio-level equity.',
      notes: 'Tighter stops reduce tail risk but increase whipsaw exits.',
    },

    /* ── Key aliases (config keys that differ from primary dictionary keys) ── */
    expected_value: {
      label: 'Expected Value',
      short: 'Probability-weighted expected outcome per trade.',
      formula: 'EV = Σ p_i · payoff_i',
      why: 'Positive EV supports long-run edge if assumptions hold.',
      notes: 'Sensitive to probability and tail-loss estimates.',
    },
    expected_move: {
      label: 'Expected Move',
      short: 'Estimated one-standard-deviation move over the option\'s timeframe.',
      formula: 'EM ≈ S·IV·√(T)',
      why: 'Sets practical strike-distance and risk framing.',
      notes: 'Directional bias is not implied by EM alone.',
    },
    rsi14: {
      label: 'RSI 14',
      short: 'Momentum oscillator over 14 periods on a 0–100 scale.',
      formula: 'RSI = 100 − 100/(1 + RS)',
      why: 'Helps spot momentum exhaustion and overbought/oversold zones.',
      notes: 'Interpret with trend; strong trends can pin RSI.',
    },
    sma20: {
      label: 'SMA 20',
      short: 'Simple moving average of the last 20 closes.',
      formula: 'SMA_n = (1/n)·Σ P_i',
      why: 'Smooths noise to reveal the near-term trend baseline.',
      notes: 'Price near SMA-20 often acts as dynamic support/resistance.',
    },
    sma50: {
      label: 'SMA 50',
      short: 'Simple moving average of the last 50 closes.',
      formula: 'SMA_n = (1/n)·Σ P_i',
      why: 'Represents a medium-term trend anchor.',
      notes: 'Price above SMA50 often indicates stronger trend regime.',
    },
    ema20: {
      label: 'EMA 20',
      short: 'Exponential moving average over 20 periods with more weight on recent prices.',
      formula: 'EMA_t = α·P_t + (1−α)·EMA_{t−1}, α = 2/(n+1)',
      why: 'Tracks trend shifts faster than SMA and helps dynamic support/resistance reads.',
      notes: 'Use with SMA and RSI for confirmation.',
    },

    /* ── Strategy-specific metrics: Iron Condor ── */
    theta_capture: {
      label: 'Theta Capture',
      short: 'Ratio of daily theta decay captured relative to premium collected.',
      formula: 'Theta Capture ≈ |daily θ| ÷ credit received',
      why: 'Higher values indicate more efficient time-decay extraction.',
      notes: 'Best compared across similar DTE ranges.',
    },
    symmetry_score: {
      label: 'Symmetry',
      short: 'Balance between put and call wings of an iron condor.',
      formula: 'Score from delta/width/credit balance between wings',
      why: 'Asymmetric condors carry hidden directional bias.',
      notes: 'Perfect symmetry = 100; lower scores indicate wing imbalance.',
    },
    expected_move_ratio: {
      label: 'EM Ratio',
      short: 'Wing width relative to the expected move.',
      formula: 'EM Ratio = wing distance ÷ expected move',
      why: 'Reveals how much of the expected range your condor covers.',
      notes: 'Ratio > 1 typically means wings are outside expected move.',
    },
    tail_risk_score: {
      label: 'Tail Risk',
      short: 'Estimated exposure to extreme/fat-tail price moves.',
      formula: 'Heuristic from wing distance, DTE, and IV skew',
      why: 'Higher scores warn of exposure to gap events.',
      notes: 'Interpret relative to position size and portfolio concentration.',
    },

    /* ── Strategy-specific metrics: Butterfly ── */
    peak_profit_at_center: {
      label: 'Peak Profit',
      short: 'Maximum payoff if the underlying pins exactly at the center strike.',
      formula: 'Peak = center strike payoff − debit paid',
      why: 'Defines the best-case reward for butterfly strategies.',
      notes: 'Probability of exact pin is low; use with prob-touch.',
    },
    probability_of_touch_center: {
      label: 'Prob Touch Center',
      short: 'Probability the underlying touches the center strike before expiry.',
      formula: 'Model-derived touch probability from distribution',
      why: 'Proxy for how likely the butterfly reaches high-profit zone.',
      notes: 'Higher than probability of expiring at center.',
    },
    cost_efficiency: {
      label: 'Cost Efficiency',
      short: 'Peak profit relative to debit paid.',
      formula: 'Cost Efficiency = peak profit ÷ debit',
      why: 'Measures leverage of the butterfly payoff structure.',
      notes: 'Higher values mean more reward per dollar risked.',
    },
    payoff_slope: {
      label: 'Payoff Slope',
      short: 'Steepness of the payoff curve approaching center strike.',
      formula: 'Slope of profit/loss per dollar of underlying movement',
      why: 'Steeper slopes mean profits accumulate faster near center.',
      notes: 'Also means losses accumulate faster away from center.',
    },
    gamma_peak_score: {
      label: 'Gamma Peak',
      short: 'Magnitude of gamma exposure at the center strike.',
      formula: 'Peak gamma from option pricing model',
      why: 'High gamma near expiry creates rapid delta shifts.',
      notes: 'Monitor closely as DTE decreases.',
    },

    /* ── Strategy-specific metrics: Calendar Spread ── */
    iv_term_structure_score: {
      label: 'IV Term Structure',
      short: 'Richness of front-month IV relative to back-month IV.',
      formula: 'Score from IV_front ÷ IV_back ratio and percentile',
      why: 'Calendars profit when front IV is elevated vs. back month.',
      notes: 'Inverted term structure (backwardation) is favorable for calendars.',
    },
    vega_exposure: {
      label: 'Vega Exposure',
      short: 'Net vega across both legs of the calendar.',
      formula: 'Net Vega = vega_back − vega_front',
      why: 'Positive net vega means position benefits from vol expansion.',
      notes: 'Key risk factor if volatility contracts after entry.',
    },
    theta_structure: {
      label: 'Theta Structure',
      short: 'Net theta advantage from the front/back month differential.',
      formula: 'Net Theta = |theta_front| − |theta_back|',
      why: 'Positive net theta means the position earns from time decay.',
      notes: 'Advantage is greatest when front month decays faster.',
    },
    move_risk_score: {
      label: 'Move Risk',
      short: 'Sensitivity to large underlying moves that shift away from calendar center.',
      formula: 'Heuristic from expected move, strike distance, and DTE gap',
      why: 'Large moves can collapse both legs and eliminate the spread\'s edge.',
      notes: 'Higher score = more risk from directional moves.',
    },

    /* ── Strategy-specific metrics: Income (CSP / Covered Call) ── */
    annualized_yield_on_collateral: {
      label: 'Annualised Yield',
      short: 'Projected annualized return on the capital/collateral allocated.',
      formula: 'Yield = (premium ÷ collateral) × (365 ÷ DTE)',
      why: 'Normalizes income across different timeframes for comparison.',
      notes: 'Assumes repeated execution at similar terms.',
    },
    premium_per_day: {
      label: 'Premium / Day',
      short: 'Daily theta-equivalent income from the position.',
      formula: 'Premium/Day = credit received ÷ DTE',
      why: 'Compares income efficiency across different DTE options.',
      notes: 'Does not account for early assignment risk.',
    },
    downside_buffer: {
      label: 'Downside Buffer',
      short: 'Percentage the underlying can drop before the position becomes unprofitable.',
      formula: 'Buffer = (spot − break-even) ÷ spot',
      why: 'Quantifies the margin of safety for income strategies.',
      notes: 'Larger buffer = more conservative but lower yield.',
    },
    assignment_risk_score: {
      label: 'Assignment Risk',
      short: 'Estimated probability of early assignment.',
      formula: 'Heuristic from moneyness, DTE, and dividend proximity',
      why: 'Early assignment disrupts the planned trade and may require capital.',
      notes: 'Higher score means greater assignment concern.',
    },

    /* ── Strategy-specific metrics: Debit Spread ── */
    conviction_score: {
      label: 'Conviction',
      short: 'Composite confidence rating for the trade thesis.',
      formula: 'Weighted blend of technical, volatility, and momentum signals',
      why: 'Higher conviction justifies larger position sizing.',
      notes: 'Always cross-reference with risk limits.',
    },

    /* ── Liquidity (SHARED) ── */
    liquidity_score: {
      label: 'Liquidity',
      short: 'Composite liquidity grade from bid-ask width, open interest, and volume.',
      formula: 'Weighted score from spread %, OI, and volume',
      why: 'Low liquidity means wider slippage and harder fills.',
      notes: 'Prefer scores above 50 for reliable execution.',
    },

    /* ── Stock Strategy Scanner Metrics ── */
    reset_score: {
      label: 'Reset',
      short: 'How well the stock has "reset" after a pullback — RSI/indicators returning to neutral.',
      formula: 'Score from RSI recovery, volume normalization, and range stabilization',
      why: 'A proper reset after pullback reduces the chance of catching a falling knife.',
      notes: 'Works best combined with strong trend score.',
    },
    pullback_from_20d_high: {
      label: 'PB from 20D High',
      short: 'Percentage drop from the 20-day high.',
      formula: '(20d high − current) ÷ 20d high × 100',
      why: 'Quantifies pullback depth for potential entry sizing.',
      notes: 'Deeper pullbacks offer more reward but may signal trend change.',
    },
    distance_to_sma20: {
      label: 'Dist to SMA-20',
      short: 'Percentage distance from current price to the 20-day SMA.',
      formula: '(price − SMA20) ÷ SMA20 × 100',
      why: 'Measures reversion potential and trend extension.',
      notes: 'Negative = below SMA (potential bounce zone in uptrend).',
    },
    breakout_score: {
      label: 'Breakout',
      short: 'Composite breakout quality from price, volume, and range expansion.',
      formula: 'Weighted score from proximity to highs, volume surge, and compression release',
      why: 'Higher scores indicate a more convincing breakout setup.',
      notes: 'False breakouts are common; confirm with volume.',
    },
    volume_score: {
      label: 'Volume Score',
      short: 'Relative volume activity compared to typical trading levels.',
      formula: 'Score from volume ÷ avg volume ratio and trend',
      why: 'Above-average volume validates price moves.',
      notes: 'Distinguish between accumulation and distribution volume.',
    },
    base_quality_score: {
      label: 'Base Quality',
      short: 'Strength and duration of a consolidation base before breakout.',
      formula: 'Score from consolidation length, tightness, and volume pattern',
      why: 'Longer, tighter bases tend to produce stronger breakouts.',
      notes: 'Use with breakout proximity for timing.',
    },
    breakout_proximity_55: {
      label: '55D High Prox',
      short: 'How close the current price is to the 55-day high.',
      formula: '(price ÷ 55d high) × 100',
      why: 'Prices near 55-day highs may be primed for breakout continuation.',
      notes: 'Values near 100% indicate tight proximity.',
    },
    vol_spike_ratio: {
      label: 'Vol Spike',
      short: 'Current volume relative to recent average — spike detection.',
      formula: 'Today\'s volume ÷ avg volume (20d)',
      why: 'Volume spikes often precede or confirm significant moves.',
      notes: 'Ratio > 2 typically indicates meaningful activity.',
    },
    compression_score: {
      label: 'Compression',
      short: 'Degree of price range compression (Bollinger Band squeeze).',
      formula: 'Score from BB width percentile and ATR compression',
      why: 'Compressed ranges often precede volatility expansion.',
      notes: 'Lower compression = tighter range = higher breakout potential.',
    },
    dist_sma20: {
      label: 'Dist SMA-20',
      short: 'Percentage distance from current price to the 20-day SMA.',
      formula: '(price − SMA20) ÷ SMA20 × 100',
      why: 'Measures mean-reversion potential or trend extension.',
      notes: 'Extreme values may indicate stretched conditions.',
    },
    oversold_score: {
      label: 'Oversold',
      short: 'Composite oversold reading from RSI, Z-score, and support proximity.',
      formula: 'Weighted blend of RSI depth, statistical Z, and range position',
      why: 'Higher scores suggest stronger mean-reversion potential.',
      notes: 'Oversold can persist in strong downtrends.',
    },
    stabilization_score: {
      label: 'Stabilize',
      short: 'Signs of selling exhaustion and price stabilization after a drop.',
      formula: 'Score from volume decline, range narrowing, and RSI hooks',
      why: 'Stabilization before bounce reduces catching-a-falling-knife risk.',
      notes: 'Best when preceded by clear oversold conditions.',
    },
    room_score: {
      label: 'Room',
      short: 'Upside room before hitting resistance levels.',
      formula: 'Score from distance to SMA, prior highs, and Fibonacci levels',
      why: 'More room = better risk/reward for mean-reversion entry.',
      notes: 'Limited room may justify smaller position sizes.',
    },
    rsi2: {
      label: 'RSI 2',
      short: 'Ultra-short momentum oscillator (2-period RSI) on 0–100 scale.',
      formula: 'RSI = 100 − 100/(1 + RS), n=2',
      why: 'Highly sensitive to short-term overbought/oversold conditions.',
      notes: 'Mean-reversion signal; not suitable for trend following.',
    },
    zscore_20: {
      label: 'Z-Score 20D',
      short: 'How many standard deviations current price is from 20-day mean.',
      formula: 'Z = (price − SMA20) ÷ stdev(20d)',
      why: 'Statistical measure of price dislocation.',
      notes: 'Z < −2 is typically extreme oversold territory.',
    },
    drawdown_20: {
      label: 'DD from 20D Hi',
      short: 'Percentage drawdown from the 20-day high.',
      formula: '(20d high − current) ÷ 20d high × 100',
      why: 'Quantifies how far the stock has fallen from recent peak.',
      notes: 'Use with stabilization score for timing.',
    },
    expansion_score: {
      label: 'Expansion',
      short: 'Magnitude of current volatility expansion from compressed levels.',
      formula: 'Score from ATR expansion, BB width change, and range breakout',
      why: 'Captures the transition from compression to expansion.',
      notes: 'Higher scores indicate more convincing expansion.',
    },
    confirmation_score: {
      label: 'Confirm',
      short: 'Corroborating signals that support the expansion thesis.',
      formula: 'Weighted blend of volume confirmation, momentum alignment, and trend support',
      why: 'Reduces false signal risk from noise-driven expansions.',
      notes: 'Strong confirmation + expansion = higher-confidence setup.',
    },
    risk_score: {
      label: 'Risk Score',
      short: 'Composite risk assessment for the setup.',
      formula: 'Blended from volatility regime, position size implications, and tail exposure',
      why: 'Helps calibrate position sizing and stop placement.',
      notes: 'Higher risk score may warrant smaller position size.',
    },
    atr_ratio_10: {
      label: 'ATR Ratio',
      short: 'Current ATR relative to recent average ATR.',
      formula: 'ATR_current ÷ ATR_avg(lookback)',
      why: 'Ratio > 1 means volatility is expanding; < 1 means contracting.',
      notes: 'Use with compression score for expansion timing.',
    },
    rv_ratio: {
      label: 'RV Ratio',
      short: 'Short-term realized vol vs. longer-term realized vol.',
      formula: 'RV_short ÷ RV_long',
      why: 'Rising ratio signals recent volatility pickup.',
      notes: 'Can precede IV expansion if market is re-pricing risk.',
    },
    bb_width_percentile_180: {
      label: 'BB Width %ile',
      short: 'Current Bollinger Band width as percentile of last 180 days.',
      formula: 'Percentile rank of BB width over 180-day lookback',
      why: 'Low percentile = extreme compression = breakout setup potential.',
      notes: 'Typically look for sub-20th percentile as trigger zone.',
    },
    atr_pct: {
      label: 'ATR %',
      short: 'Average True Range expressed as a percentage of price.',
      formula: 'ATR ÷ price × 100',
      why: 'Normalizes volatility across different price levels.',
      notes: 'Useful for comparing vol regimes between stocks.',
    },

    /* ── Contextual / Page-level metrics ── */
    expiration: {
      label: 'Expiration',
      short: 'Option contract expiration date.',
      formula: 'Calendar date when the option ceases to exist',
      why: 'Determines time decay schedule and event exposure window.',
      notes: 'Earlier expirations have faster theta decay.',
    },
    symbol: {
      label: 'Symbol',
      short: 'Ticker symbol of the underlying security.',
      why: 'Identifies the specific stock or ETF being traded.',
    },
    regime: {
      label: 'Regime',
      short: 'Current market regime classification (Bullish, Cautious, Bearish, etc.).',
      formula: 'Rule-based composite from trend, volatility, breadth, and macro signals',
      why: 'Drives strategy selection and risk policy adjustments.',
      notes: 'Regime changes may trigger strategy rotations.',
    },
    total_active_trades: {
      label: 'Total Active',
      short: 'Total number of currently open/active trade positions.',
      formula: 'Count of all trades with open status',
      why: 'Tracks portfolio workload and diversification breadth.',
      notes: 'Compare to max position limits.',
    },
    strategy_bucket: {
      label: 'Strategy Bucket',
      short: 'Category grouping of trades by strategy type.',
      why: 'Organizes positions for concentration and performance review.',
      notes: 'E.g., Credit Put, Credit Call, Iron Condor, etc.',
    },
    index_price: {
      label: 'Index Price',
      short: 'Current price of a major market index ETF.',
      formula: 'Last trade or quote mid from market data provider',
      why: 'Benchmark context for portfolio positioning and regime assessment.',
      notes: 'Common indices: SPY, QQQ, IWM, DIA.',
    },
    net_credit: {
      label: 'Net Credit / Debit',
      short: 'Net premium collected (credit) or paid (debit) to open the position.',
      formula: 'Σ leg premiums (positive = credit, negative = debit)',
      why: 'Defines cash flow and max profit for credit strategies.',
      notes: 'Per-contract value; multiply by quantity for total.',
    },

    /* ── Session Stats ── */
    total_candidates: {
      label: 'Total Candidates',
      short: 'Total option candidates evaluated across all scanner runs this session.',
      formula: 'Cumulative count of candidates across session scans',
      why: 'Tracks how many opportunities the scanner has surfaced.',
    },
    accepted_trades: {
      label: 'Accepted Trades',
      short: 'Number of candidates that passed all quality filters.',
      formula: 'Count of trades/ideas meeting all threshold criteria',
      why: 'Measures scanner selectivity and opportunity flow.',
    },
    rejected_count: {
      label: 'Rejected',
      short: 'Number of candidates filtered out during scanning.',
      formula: 'Total candidates − accepted',
      why: 'Context for scanner strictness and market pickiness.',
    },
    acceptance_rate: {
      label: 'Acceptance Rate',
      short: 'Percentage of candidates that pass all scanner filters.',
      formula: 'Accepted ÷ total candidates × 100',
      why: 'Gauge of market richness and filter calibration.',
    },
    best_score: {
      label: 'Best Score',
      short: 'Highest composite/quality score among accepted candidates.',
      formula: 'max(composite score) across accepted set',
      why: 'Quick indicator of top opportunity quality this session.',
    },
    avg_quality_score: {
      label: 'Avg Quality Score',
      short: 'Mean composite quality score across accepted candidates.',
      formula: 'avg(composite score) across accepted set',
      why: 'Overall session quality indicator.',
    },
    avg_return_on_risk: {
      label: 'Avg Return on Risk',
      short: 'Mean return-on-risk across accepted candidates.',
      formula: 'avg(RoR) across accepted set',
      why: 'Summarizes reward quality of the session\'s opportunity set.',
    },
    session_runs: {
      label: 'Session Runs',
      short: 'Number of scanner runs performed this session.',
      formula: 'Count of scan invocations since page load',
      why: 'Tracks scanning activity in the current session.',
    },

    /* ── Dashboard / Table contextual headers ── */
    strategy_name: {
      label: 'Strategy',
      short: 'Name of the options or stock strategy type.',
      why: 'Identifies the trade structure and risk profile.',
    },
    trade_count: {
      label: 'Trades',
      short: 'Number of trades in this group or category.',
      formula: 'Count of trades matching the group criteria',
      why: 'Context for statistical reliability of group metrics.',
    },
    thesis: {
      label: 'Thesis',
      short: 'Narrative trade thesis or setup rationale.',
      why: 'Provides the reasoning behind the trade idea.',
    },
  };


  /* ================================================================
   *  RICH TOOLTIPS  (data-ben-tip system)
   *  Used for market regime components and strategy chips
   * ================================================================ */

  var RICH = {

    /* ── Market Regime Components ── */
    regime_trend: {
      title: 'Trend Strength',
      body: 'Measures directional market bias using moving-average alignment (EMA20, EMA50, SMA200). Strong upward alignment signals bullish regime support, while weak or inverted structure increases downside and mean-reversion risk.',
      impact: 'Higher trend strength favors premium-selling strategies and directional trades with the trend.',
    },
    regime_volatility: {
      title: 'Volatility Environment',
      body: 'Evaluates implied volatility level relative to normal conditions (primarily via VIX). Elevated volatility increases option premiums and risk, while low volatility compresses pricing but often supports trend persistence.',
      impact: 'Higher volatility favors premium selling; very low volatility may favor debit structures or directional plays.',
    },
    regime_breadth: {
      title: 'Market Breadth',
      body: 'Tracks how many sectors or components participate in the market move. Broad participation signals healthy institutional support, while narrow leadership increases fragility and reversal risk.',
      impact: 'Strong breadth improves confidence in trend continuation and premium strategies.',
    },
    regime_rates: {
      title: 'Interest Rate Pressure',
      body: 'Monitors the 10-year Treasury yield as a proxy for financial conditions. Rising yields can pressure equities (especially growth), while stable or falling rates generally support risk assets.',
      impact: 'Stable or falling rates support bullish structures; rapidly rising rates increase regime risk.',
    },
    regime_momentum: {
      title: 'Momentum Quality',
      body: 'Uses RSI positioning to evaluate whether price movement is sustainably trending or becoming stretched. Mid-range RSI typically indicates healthy continuation, while extremes increase reversal probability.',
      impact: 'Healthy momentum supports trend trades; overbought/oversold conditions increase mean-reversion risk.',
    },

    /* ── Strategy Chips ── */
    put_credit_spread: {
      title: 'Put Credit Spread',
      body: 'A bullish defined-risk premium strategy that sells an out-of-the-money put while buying a further OTM put for protection. Profits when price stays above the short strike and volatility contracts.',
      conditions: [
        'Bullish to neutral trend',
        'Elevated implied volatility',
        'Stable or rising market',
      ],
    },
    covered_call: {
      title: 'Covered Call',
      body: 'Owns shares while selling an out-of-the-money call to generate income. Caps upside but provides partial downside cushion through collected premium.',
      conditions: [
        'Neutral to moderately bullish market',
        'Elevated implied volatility',
        'Low expectation of explosive upside',
      ],
    },
    call_debit: {
      title: 'Call Debit Spread',
      body: 'A defined-risk bullish strategy that buys a call and sells a higher-strike call. Requires upward price movement to profit and benefits from directional momentum.',
      conditions: [
        'Strong bullish trend',
        'Lower or rising volatility',
        'Momentum expansion phases',
      ],
    },
    short_gamma: {
      title: 'Short Gamma Exposure',
      body: 'Represents strategies that benefit from price stability but are harmed by large directional moves. Short gamma positions collect premium but carry tail risk during volatility expansion.',
      conditions: [
        'Range-bound markets',
        'Declining volatility',
        'High liquidity environments',
      ],
      risk: 'Vulnerable to sharp breakouts and volatility spikes.',
    },
    debit_butterfly: {
      title: 'Debit Butterfly',
      body: 'A low-cost, defined-risk neutral strategy that profits if price pins near a target level at expiration. Requires precise price location and typically underperforms in strong trends.',
      conditions: [
        'Low volatility',
        'Range-bound markets',
        'Event pinning scenarios',
      ],
      risk: 'Low probability of max profit; sensitive to directional drift.',
    },
    iron_condor: {
      title: 'Iron Condor',
      body: 'A neutral premium-selling strategy combining a put credit spread and a call credit spread. Profits when the underlying stays within a defined range through expiration.',
      conditions: [
        'Range-bound markets',
        'Elevated implied volatility',
        'Low momentum / mean-reverting conditions',
      ],
    },
    calendar_spread: {
      title: 'Calendar Spread',
      body: 'Sells a near-term option and buys a longer-dated option at the same strike. Profits from time decay and potential IV expansion in the back month.',
      conditions: [
        'Low near-term volatility',
        'Stable underlying price',
        'Positive term structure',
      ],
    },
    call_credit_spread: {
      title: 'Call Credit Spread',
      body: 'A bearish defined-risk premium strategy that sells an OTM call while buying a further OTM call. Profits when price stays below the short strike.',
      conditions: [
        'Bearish to neutral trend',
        'Elevated implied volatility',
        'Resistance overhead or negative momentum',
      ],
    },
    put_debit: {
      title: 'Put Debit Spread',
      body: 'A defined-risk bearish strategy that buys a put and sells a lower-strike put. Requires downward price movement to profit.',
      conditions: [
        'Bearish trend or breakdown',
        'Lower or rising volatility',
        'Negative momentum',
      ],
    },
  };


  /* ================================================================
   *  PUBLIC API
   * ================================================================ */

  /**
   * Look up a metric-style tooltip entry.
   * @param {string} key
   * @returns {{ label:string, short:string, formula?:string, why?:string, notes?:string } | undefined}
   */
  function getMetric(key) {
    return METRICS[key];
  }

  /**
   * Look up a rich-style tooltip entry (regime/strategy).
   * @param {string} key
   * @returns {{ title:string, body:string, impact?:string, conditions?:string[], risk?:string } | undefined}
   */
  function getRich(key) {
    return RICH[key];
  }

  /**
   * Get the full metrics glossary object (for backward compatibility
   * with BenTradeMetrics.glossary consumers).
   */
  function allMetrics() {
    return METRICS;
  }

  /**
   * Get the full rich tooltip object (for backward compatibility
   * with BenTradeBenTooltip TIPS consumers).
   */
  function allRich() {
    return RICH;
  }

  /**
   * Register or override a metric entry at runtime.
   */
  function registerMetric(key, entry) {
    METRICS[key] = entry;
  }

  /**
   * Register or override a rich tooltip entry at runtime.
   */
  function registerRich(key, entry) {
    RICH[key] = entry;
  }

  return {
    getMetric: getMetric,
    getRich: getRich,
    allMetrics: allMetrics,
    allRich: allRich,
    registerMetric: registerMetric,
    registerRich: registerRich,
  };
})();
