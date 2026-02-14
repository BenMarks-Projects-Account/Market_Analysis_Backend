# tests live in tests/ — results are one level up in the backend folder
from common.utils import analyze_trade_with_model, generate_mock_report
import json
from pathlib import Path

fname = generate_mock_report()
print('Generated report:', fname)
res_path = Path(__file__).resolve().parent.parent / 'results' / fname
trades = json.load(open(res_path, 'r', encoding='utf-8'))
# pick a trade that is unlikely to be forced-reject; set ev positive
trade = trades[1]
trade['ev_per_share'] = 1.23

print('Calling analyze_trade_with_model...')
res = analyze_trade_with_model(trade, fname, retries=1, timeout=6)
print('Result:', res)
model_path = Path(__file__).parent / 'results' / ('model_' + fname)
print('Model file exists:', model_path.exists())
if model_path.exists():
    arr = json.load(open(model_path, 'r', encoding='utf-8'))
    print('Entries:', len(arr))
    print('Last model_evaluation:', arr[-1].get('model_evaluation'))
