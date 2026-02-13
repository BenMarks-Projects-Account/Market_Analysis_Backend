from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import os
import json
from pathlib import Path

app = Flask(__name__)

# Path to results directory
RESULTS_DIR = Path(__file__).parent / 'results'

@app.route('/')
def dashboard():
    """Serve the main dashboard"""
    return send_from_directory('.', 'dashboard.html')

@app.route('/api/reports')
def list_reports():
    """List all available analysis reports"""
    if not RESULTS_DIR.exists():
        return jsonify([])

    files = [f.name for f in RESULTS_DIR.glob('analysis_*.json')]
    files.sort(reverse=True)  # Most recent first
    return jsonify(files)

@app.route('/api/reports/<filename>')
def get_report(filename):
    """Get a specific analysis report"""
    if not filename.startswith('analysis_') or not filename.endswith('.json'):
        return jsonify({'error': 'Invalid filename'}), 400

    file_path = RESULTS_DIR / filename
    if not file_path.exists():
        return jsonify({'error': 'Report not found'}), 404

    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        return jsonify(data)
    except json.JSONDecodeError:
        return jsonify({'error': 'Invalid JSON file'}), 500

@app.route('/analyze', methods=['POST'])
def analyze():
    """Analyze credit spread trades from JSON data"""
    try:
        trades_data = request.json

        if not isinstance(trades_data, list):
            return jsonify({'error': 'Expected array of trade objects'}), 400

        # Import here to avoid circular imports
        from quant_analysis import CreditSpread

        results = []
        for i, trade_data in enumerate(trades_data):
            try:
                # Extract iv_rank_value if provided
                iv_rank_value = trade_data.pop('iv_rank_value', None)

                # Create CreditSpread object
                trade = CreditSpread(**trade_data)

                # Generate summary
                summary = trade.summary(iv_rank_value=iv_rank_value)
                summary['trade_index'] = i

                results.append(summary)

            except Exception as e:
                return jsonify({'error': f'Error processing trade {i}: {str(e)}'}), 400

        return jsonify(results)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/generate')
def generate_report_stream():
    """SSE endpoint that simulates generating a new analysis report and streams progress events."""
    def event_stream():
        try:
            # Step 1: pulling data
            yield f"event: progress\ndata: {json.dumps({'step':'pulling','message':'Pulling current option data...'})}\n\n"
            import time
            time.sleep(0.9)

            # Step 2: calculating
            yield f"event: progress\ndata: {json.dumps({'step':'calculating','message':'Calculating quantitative metrics...'})}\n\n"
            time.sleep(1.0)

            # Step 3: writing report (mocked content)
            yield f"event: progress\ndata: {json.dumps({'step':'writing','message':'Writing report to disk...'})}\n\n"
            time.sleep(0.6)

            # Delegate actual mock report creation to utils.generate_mock_report()
            from utils import generate_mock_report
            filename = generate_mock_report()

            # Final event: done (send filename)
            yield f"event: done\ndata: {json.dumps({'filename': filename})}\n\n"
        except GeneratorExit:
            return
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"

    return Response(stream_with_context(event_stream()), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run(debug=True)