"""Model source definitions for LLM inference endpoints.

Each source maps to an OpenAI-compatible chat completions endpoint.
Premium Online is a disabled placeholder for future Bedrock/OpenAI/Anthropic.
"""

from __future__ import annotations

MODEL_SOURCES: dict[str, dict] = {
    "local": {
        "name": "Local",
        "endpoint": "http://localhost:1234/v1/chat/completions",
        "enabled": True,
    },
    "model_machine": {
        "name": "Model Machine",
        "endpoint": "http://192.168.1.143:1234/v1/chat/completions",
        "enabled": True,
    },
    "premium_online": {
        "name": "Premium Online",
        "endpoint": None,
        "enabled": False,
    },
}

VALID_SOURCE_KEYS = frozenset(MODEL_SOURCES.keys())
