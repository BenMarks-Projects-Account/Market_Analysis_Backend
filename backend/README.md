# Market Analysis Backend

This backend provides quantitative analysis for stock trading and integrates with AI models via AWS Bedrock using Strands Agents for agentic insights.

## Setup

1. Install dependencies: `pip install -r requirements.txt`
2. Configure model provider: Follow [Strands docs](https://strandsagents.com/latest/user-guide/quickstart/#model-providers) for Bedrock setup.
3. Set AWS credentials in environment or .env file.
4. Run the app: `python main.py`

## Endpoints

- POST /analyze: Accepts JSON with stock data, returns AI-generated insights.

## Architecture

- quant_analysis.py: Quantitative calculations (e.g., expected value).
- agent.py: Agentic layer using Strands Agents with Bedrock.
- main.py: Flask API to tie it together.