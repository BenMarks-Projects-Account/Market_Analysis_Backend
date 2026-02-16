window.BenTradeMetrics = window.BenTradeMetrics || {};

window.BenTradeMetrics.glossary = {
  ema_20: {
    label: 'EMA 20',
    short: 'Exponential moving average over 20 periods with more weight on recent prices.',
    formula: 'EMA_t = α·P_t + (1−α)·EMA_{t−1}, α = 2/(n+1)',
    why: 'Tracks trend shifts faster than SMA and helps dynamic support/resistance reads.',
    notes: 'Use with SMA and RSI for confirmation.'
  },
  sma_20: {
    label: 'SMA 20',
    short: 'Simple moving average of the last 20 closes.',
    formula: 'SMA_n = (1/n)·Σ P_i',
    why: 'Smooths noise to reveal the near-term trend baseline.',
    notes: 'Crosses with EMA/SMA50 are useful context.'
  },
  sma_50: {
    label: 'SMA 50',
    short: 'Simple moving average of the last 50 closes.',
    formula: 'SMA_n = (1/n)·Σ P_i',
    why: 'Represents a medium-term trend anchor.',
    notes: 'Price above SMA50 often indicates stronger trend regime.'
  },
  rsi_14: {
    label: 'RSI 14',
    short: 'Momentum oscillator over 14 periods on a 0–100 scale.',
    formula: 'RSI = 100 − 100/(1 + RS)',
    why: 'Helps spot momentum exhaustion and overbought/oversold zones.',
    notes: 'Interpret with trend; strong trends can pin RSI.'
  },
  realized_vol_20d: {
    label: 'Realized Vol 20d',
    short: 'Annualized volatility estimated from recent daily returns.',
    formula: 'RV ≈ stdev(returns)·√252',
    why: 'Baseline for comparing implied volatility richness.',
    notes: 'Short windows are sensitive to recent shocks.'
  },
  iv: {
    label: 'Implied Volatility',
    short: 'Market-implied forward volatility from option prices.',
    formula: 'Solved from option model price (e.g., Black-Scholes)',
    why: 'Drives option premium and expected move estimates.',
    notes: 'Often quoted annualized.'
  },
  expected_move_1w: {
    label: 'Expected Move (1w)',
    short: 'Estimated one-standard-deviation move over roughly one week.',
    formula: 'EM ≈ S·IV·√(T)',
    why: 'Sets practical strike-distance and risk framing.',
    notes: 'Directional bias is not implied by EM alone.'
  },
  iv_rv_ratio: {
    label: 'IV/RV Ratio',
    short: 'Ratio of implied volatility to realized volatility.',
    formula: 'IV/RV = IV ÷ RV',
    why: 'Quick gauge for option richness versus observed movement.',
    notes: '>1 can favor selling volatility, context dependent.'
  },
  pop: {
    label: 'POP',
    short: 'Probability of finishing with profit by expiry under model assumptions.',
    formula: 'Model-derived probability from distribution assumptions',
    why: 'Useful for hit-rate expectation, not payoff magnitude.',
    notes: 'Combine with EV and max loss, not standalone.'
  },
  ev: {
    label: 'Expected Value',
    short: 'Probability-weighted expected outcome per trade.',
    formula: 'EV = Σ p_i · payoff_i',
    why: 'Positive EV supports long-run edge if assumptions hold.',
    notes: 'Sensitive to probability and tail-loss estimates.'
  },
  ev_to_risk: {
    label: 'EV to Risk',
    short: 'Expected value normalized by risk capital.',
    formula: 'EV/Risk = EV ÷ MaxLoss',
    why: 'Compares trade efficiency across different sizes.',
    notes: 'Higher is generally better.'
  },
  return_on_risk: {
    label: 'Return on Risk',
    short: 'Maximum potential return divided by maximum risk.',
    formula: 'RoR = MaxProfit ÷ MaxLoss',
    why: 'Simple reward-to-risk comparison.',
    notes: 'Does not include probability weighting.'
  },
  break_even: {
    label: 'Break-even',
    short: 'Underlying price at expiry where P&L is zero.',
    formula: 'Depends on structure (e.g., short strike − credit)',
    why: 'Defines the margin of safety boundary.',
    notes: 'Break-even can shift with assignment/fees in practice.'
  },
  max_profit: {
    label: 'Max Profit',
    short: 'Best-case payoff if the trade resolves optimally.',
    formula: 'Structure-specific payoff cap',
    why: 'Upper bound for reward and RoR calculation.',
    notes: 'Often net credit for short spreads.'
  },
  max_loss: {
    label: 'Max Loss',
    short: 'Worst-case payoff if the trade moves fully against you.',
    formula: 'Structure-specific loss cap',
    why: 'Primary capital-at-risk constraint.',
    notes: 'Include quantity/contract multiplier when sizing.'
  },
  delta: {
    label: 'Delta',
    short: 'Approximate change in position value for a $1 underlying move.',
    formula: 'Δ ≈ ∂Price/∂S',
    why: 'Measures directional exposure.',
    notes: 'Portfolio delta aggregates net directional bias.'
  },
  gamma: {
    label: 'Gamma',
    short: 'Rate of change of delta with respect to price.',
    formula: 'Γ ≈ ∂²Price/∂S²',
    why: 'Indicates how quickly directional risk can change.',
    notes: 'Higher gamma implies more convexity and re-hedge need.'
  },
  theta: {
    label: 'Theta',
    short: 'Sensitivity of option value to time decay.',
    formula: 'Θ ≈ ∂Price/∂t',
    why: 'Shows daily carry from time passing.',
    notes: 'Sign and magnitude vary by structure.'
  },
  vega: {
    label: 'Vega',
    short: 'Sensitivity of option value to implied volatility changes.',
    formula: 'Vega ≈ ∂Price/∂IV',
    why: 'Captures volatility exposure independent of direction.',
    notes: 'Can dominate around events/earnings.'
  },
  dte: {
    label: 'DTE',
    short: 'Days remaining until expiration.',
    formula: 'DTE = expiry date − current date',
    why: 'Controls decay pace and assignment/event risk window.',
    notes: 'Shorter DTE usually means faster theta and gamma changes.'
  },
  mark: {
    label: 'Mark',
    short: 'Current estimated fill/mid value of the position.',
    formula: 'Often midpoint of bid/ask',
    why: 'Used for live valuation and close simulation.',
    notes: 'Executable price can differ in fast markets.'
  },
  unrealized_pnl: {
    label: 'Unrealized P&L',
    short: 'Open-trade profit or loss based on current mark.',
    formula: 'Unrealized P&L = current value − open basis',
    why: 'Tracks live performance before closing.',
    notes: 'Can change materially intraday.'
  },
  unrealized_pnl_pct: {
    label: 'Unrealized P&L %',
    short: 'Unrealized P&L expressed as a percentage of risk/basis.',
    formula: 'P&L% = Unrealized P&L ÷ basis',
    why: 'Normalizes performance across trades.',
    notes: 'Check denominator definition in context.'
  },
  kelly_fraction: {
    label: 'Kelly Fraction',
    short: 'Model-based position fraction maximizing long-run growth.',
    formula: 'f* ≈ edge ÷ odds (simplified)',
    why: 'Guides conservative sizing relative to edge.',
    notes: 'Often scaled down in practice.'
  },
  trade_quality_score: {
    label: 'Trade Quality Score',
    short: 'Composite quality metric from multiple trade signals.',
    formula: 'Weighted composite of POP, EV, RoR, liquidity, etc.',
    why: 'Ranks opportunities quickly on one scale.',
    notes: 'Always inspect underlying components.'
  },
  composite_score: {
    label: 'Composite Score',
    short: 'Overall ranking score for candidate comparison.',
    formula: 'Model-specific weighted score',
    why: 'Helps prioritize review order.',
    notes: 'Interpret relative to peer candidates.'
  },
  rank_score: {
    label: 'Rank Score',
    short: 'Normalized rank-oriented score used for sorting.',
    formula: 'Model-specific normalization of quality metrics',
    why: 'Provides fast shortlist ordering.',
    notes: 'Useful for scanner triage.'
  },
  iv_rank: {
    label: 'IV Rank',
    short: 'Current IV percentile within its lookback high-low range.',
    formula: 'IV Rank = (IV − IV_low)/(IV_high − IV_low)',
    why: 'Context for whether options are relatively rich/cheap.',
    notes: 'Depends on selected lookback period.'
  },
  short_strike_z: {
    label: 'Short Strike Z',
    short: 'Distance of short strike from spot in sigma units.',
    formula: 'Z ≈ (strike − spot) ÷ expected move',
    why: 'Summarizes cushion to short strike.',
    notes: 'Higher absolute cushion usually lowers POP risk.'
  },
  bid_ask_spread_pct: {
    label: 'Bid/Ask Spread %',
    short: 'Relative width of quote spread versus mid.',
    formula: '(ask − bid) ÷ mid',
    why: 'Proxy for liquidity and slippage risk.',
    notes: 'Lower is typically better.'
  },
  strike_distance_pct: {
    label: 'Strike Distance %',
    short: 'Percent distance between relevant strike and spot.',
    formula: '|strike − spot| ÷ spot',
    why: 'Quick measure of moneyness and buffer.',
    notes: 'Use with expected move and DTE.'
  },
  market_regime: {
    label: 'Market Regime',
    short: 'Qualitative trend/volatility state label.',
    formula: 'Rule-based classification from trend and vol inputs',
    why: 'Helps align strategy type with conditions.',
    notes: 'Regime tags are model abstractions.'
  },
  risk_remaining: {
    label: 'Risk Remaining',
    short: 'Unused risk budget under active policy constraints.',
    formula: 'Risk Remaining = Risk Cap − Risk Used',
    why: 'Prevents over-allocation of portfolio risk.',
    notes: 'Track alongside hard/soft warnings.'
  },
  estimated_risk: {
    label: 'Estimated Risk',
    short: 'Approximate capital at risk for a position.',
    formula: 'Structure-specific risk estimate',
    why: 'Sizing and concentration control anchor.',
    notes: 'May be approximate when data is partial.'
  },
  win_rate: {
    label: 'Win Rate',
    short: 'Fraction of closed trades with positive realized P&L.',
    formula: 'Win Rate = wins ÷ closed trades',
    why: 'Evaluates hit-rate behavior by strategy.',
    notes: 'Should be interpreted with payoff asymmetry.'
  },
  total_pnl: {
    label: 'Total P&L',
    short: 'Aggregate realized profit/loss over selected period.',
    formula: 'Total P&L = Σ realized P&L',
    why: 'Primary performance outcome summary.',
    notes: 'Range selection materially changes interpretation.'
  },
  avg_pnl: {
    label: 'Average P&L',
    short: 'Mean realized P&L per trade in selected set.',
    formula: 'Avg P&L = Total P&L ÷ trade count',
    why: 'Normalizes returns by activity level.',
    notes: 'Outliers can skew mean values.'
  },
  max_drawdown: {
    label: 'Max Drawdown',
    short: 'Largest peak-to-trough decline in cumulative P&L.',
    formula: 'MDD = max(peak − trough)',
    why: 'Key downside risk and pain metric.',
    notes: 'Essential for survivability assessment.'
  }
};
