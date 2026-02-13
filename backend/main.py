from flask import Flask, request, jsonify
from quant_analysis import calculate_expected_value
from agent import run_agent
import os

app = Flask(__name__)

@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.json
    # Assume data has stock data or parameters
    # Calculate quant metrics
    ev = calculate_expected_value(data)
    # Feed to agent
    response = run_agent(ev)
    return jsonify({'response': response})

if __name__ == '__main__':
    app.run(debug=True)