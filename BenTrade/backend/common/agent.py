from strands import Agent
import os

# Assuming model is configured via environment or default to Bedrock
# Strands handles model providers automatically if configured

agent = Agent()

def run_agent(data):
    prompt = f"Analyze this quantitative data for stock trading: {data}. Provide insights."
    response = agent(prompt)
    return response