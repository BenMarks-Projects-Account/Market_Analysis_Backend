import json
from pathlib import Path
from datetime import datetime

RESULTS_DIR = Path(__file__).parent / 'results'


def generate_mock_report() -> str:
    """Create a mocked analysis JSON file in `results/` and return the filename.

    This is intentionally small and deterministic for testing the dashboard flow.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    filename = f"analysis_{ts}.json"

    report = [
        {
            'spread_type': 'put_credit',
            'short_strike': 95,
            'long_strike': 90,
            'underlying_price': 97.2,
            'max_profit_per_share': 0.45,
            'max_loss_per_share': 4.55,
            'p_win_used': 0.72,
            'return_on_risk': 0.099,
            'ev_per_share': 0.03,
            'kelly_fraction': 0.012,
            'break_even': 94.55,
            'dte': 28,
            'expected_move': 3.12,
            'iv_rv_ratio': 1.18,
            'trade_quality_score': 0.67
        },
        {
            'spread_type': 'call_credit',
            'short_strike': 120,
            'long_strike': 125,
            'underlying_price': 118.4,
            'max_profit_per_share': 0.38,
            'max_loss_per_share': 4.62,
            'p_win_used': 0.68,
            'return_on_risk': 0.082,
            'ev_per_share': -0.01,
            'kelly_fraction': -0.002,
            'break_even': 120.38,
            'dte': 35,
            'expected_move': 4.1,
            'iv_rv_ratio': 0.95,
            'trade_quality_score': 0.58
        }
    ]

    file_path = RESULTS_DIR / filename
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)

    return filename
