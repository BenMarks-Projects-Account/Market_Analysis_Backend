from common.utils import analyze_trade_with_model, generate_mock_report
import json
from pathlib import Path

# Create a fresh mock report and pick the first trade
fname = generate_mock_report()
print('Generated report:', fname)
# tests live in tests/ — results are one level up in the backend folder
res_path = Path(__file__).resolve().parent.parent / 'results' / fname
trades = json.load(open(res_path, 'r', encoding='utf-8'))
trade = trades[0]
# Force negative EV to trigger hard-gate short-circuit
trade['ev_per_share'] = -2.0

evaluated = analyze_trade_with_model(trade, fname)
print('Evaluated result:', evaluated)

# Print last entries of corresponding model file
model_path = Path(__file__).parent / 'results' / ('model_' + fname)
if model_path.exists():
    arr = json.load(open(model_path, 'r', encoding='utf-8'))
    print('Model file total entries:', len(arr))
    print('Last entry keys:', list(arr[-1].keys()))
else:
    print('Model file not created')
