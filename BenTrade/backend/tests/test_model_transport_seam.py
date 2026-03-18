"""Steps 11-12 — Shared model-transport seam, TransportResult, and full migration tests.

Validates:
    A. _model_transport() routing path returns TransportResult
    B. _model_transport() falls back to legacy when routing disabled
    C. _model_transport() falls back to legacy when routing fails
    D. _model_transport() legacy path with retry
    E. _model_transport() raises on network failure
    F. _model_transport() strips think tags on both paths
    G. All 10 migrated analyze_* functions delegate to _model_transport()
    H. analyze_trade remains non-migrated (uses legacy direct HTTP)
    I. Result shape + trace metadata preserved for each migrated function
    J. routing_enabled=False → legacy for all migrated functions
    K. Task type inventory — all 10 expected task_types are wired
    L. TransportResult dataclass contract
    M. finish_reason extraction on legacy path
    N. transport_path and provider metadata
    O. analyze_stock_idea delegation and trace
    P. analyze_stock_strategy delegation, retry-with-fix, and fallback
    Q. analyze_tmc_final_decision delegation, retry-with-fix, and fallback
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

from common.model_analysis import (
    LocalModelUnavailableError,
    TransportResult,
    _model_transport,
    _strip_think_tags,
)


# ── Helpers ────────────────────────────────────────────────

_VALID_JSON_RESPONSE = json.dumps({
    "label": "BULLISH",
    "score": 72.5,
    "confidence": 0.85,
    "summary": "Test summary from model.",
    "positive_contributors": ["signal_a"],
    "negative_contributors": [],
    "conflicting_signals": [],
    "trader_takeaway": "Stay long.",
})

_REGIME_JSON_RESPONSE = json.dumps({
    "executive_summary": "Markets showing risk-on behavior.",
    "regime_breakdown": "Bull regime with low vol.",
    "primary_fit": "Bullish momentum strategies.",
    "avoid_rationale": "Avoid short vol.",
    "change_triggers": ["VIX spike above 25"],
    "confidence": 0.80,
    "risk_regime_label": "RISK_ON",
    "trend_label": "UPTREND",
    "vol_regime_label": "LOW_VOL",
    "key_drivers": ["Low VIX", "Strong breadth"],
})

_NEWS_JSON_RESPONSE = json.dumps({
    "label": "BULLISH",
    "score": 65.0,
    "confidence": 0.75,
    "summary": "News is positive.",
    "top_signals": [{"headline": "Market rallies", "impact": "positive", "weight": 0.8}],
    "conflicting_themes": [],
    "macro_alignment": "supportive",
    "trader_takeaway": "Sentiment supports upside.",
})

_STOCK_IDEA_JSON_RESPONSE = json.dumps({
    "recommendation": "BUY",
    "confidence": 0.8,
    "summary": "Good setup.",
    "key_factors": ["Strong trend"],
    "risks": ["Earnings risk"],
    "time_horizon": "1W",
    "trade_ideas": [],
})

_STOCK_STRATEGY_JSON_RESPONSE = json.dumps({
    "recommendation": "BUY",
    "score": 75,
    "confidence": 80,
    "summary": "Strong strategy setup.",
    "key_drivers": [{"factor": "trend", "impact": "positive", "evidence": "RSI above 60"}],
    "risk_review": {
        "primary_risks": ["Gap risk"],
        "volatility_risk": "moderate",
        "timing_risk": "low",
        "data_quality_flag": False,
    },
    "engine_vs_model": {
        "engine_score": 72,
        "model_score": 75,
        "agreement": True,
        "notes": "Close alignment.",
    },
    "data_quality": {"provider": "tradier", "warnings": []},
})

_TMC_DECISION_JSON_RESPONSE = json.dumps({
    "decision": "EXECUTE",
    "conviction": 78,
    "decision_summary": "Trade setup confirmed.",
    "factors_considered": [
        {"category": "trade_setup", "factor": "trend", "assessment": "favorable",
         "weight": "high", "detail": "Strong uptrend"},
    ],
    "market_alignment": {"overall": "supportive", "detail": "Risk-on environment"},
    "risk_assessment": {
        "primary_risks": ["Earnings in 5 days"],
        "biggest_concern": "Near-term event risk",
        "risk_reward_verdict": "Acceptable",
    },
    "what_would_change_my_mind": "VIX spike above 25.",
    "engine_comparison": {
        "engine_score": 72,
        "model_score": 78,
        "agreement": True,
        "reasoning": "Good alignment.",
    },
})


def _make_transport_result(content: str = _VALID_JSON_RESPONSE, path: str = "routed",
                           finish_reason: str | None = None, provider: str | None = "local_llm"):
    """Build a TransportResult for mocking _model_transport returns."""
    return TransportResult(
        content=content,
        transport_path=path,
        finish_reason=finish_reason,
        provider=provider,
    )


def _make_legacy_ok(content: str = _VALID_JSON_RESPONSE, finish_reason: str = "stop"):
    """Build a mock response object for successful legacy HTTP POST."""
    resp = MagicMock()
    resp.status_code = 200
    resp.content = content.encode()
    resp.elapsed = MagicMock(total_seconds=MagicMock(return_value=0.5))
    resp.json.return_value = {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
    }
    resp.text = content
    resp.raise_for_status = MagicMock()
    return resp


def _make_routing_result(content: str = _VALID_JSON_RESPONSE, success: bool = True):
    """Build (legacy_result, trace) pair from execute_routed_model."""
    legacy_result = {
        "status": "success" if success else "error",
        "content": content if success else None,
        "error": None if success else "provider_failed",
    }

    @dataclass
    class _FakeTrace:
        selected_provider: str = "local_llm"
        timing_ms: float = 42.5
        request_id: str = "test-req-1"
        execution_mode: str = "local_distributed"

    return legacy_result, _FakeTrace()


def _make_engine_result(**overrides) -> dict[str, Any]:
    """Minimal engine_result for Market Picture functions."""
    base = {
        "raw_inputs": {
            "positioning": {"put_call_ratio": 0.9},
            "crowding": {"short_interest_pct": 5.0},
            "squeeze": {"squeeze_active": False},
            "flow": {"dark_pool_pct": 40.0},
            "stability": {"regime_duration_days": 30},
            "rates": {"fed_funds_rate": 5.25},
            "conditions": {"financial_conditions_index": 99.5},
            "credit": {"ig_spread_bps": 120},
            "dollar": {"dxy": 104.5},
            "dollar_commodity": {"gold_1mo_return": 0.02},
            "defensive_growth": {"xlp_vs_xlk": -0.03},
            "coherence": {"correlation_score": 0.65},
            "breadth": {"advance_decline_ratio": 1.5},
            "participation": {"new_highs_vs_lows": 200},
            "sector_rotation": {"sector_dispersion": 0.12},
            "volume": {"relative_volume": 1.1},
            "momentum": {"rsi_14d": 55.0},
            "volatility": {"vix": 15.5},
            "term_structure": {"vix_contango_pct": 5.0},
            "skew": {"skew_index": 130},
            "realized": {"rv_20d": 12.0},
            "implied": {"iv_rank": 35},
        },
        "pillar_scores": {
            "positioning": 60, "crowding": 50, "squeeze": 40,
            "flow": 55, "stability": 65,
            "rates": 50, "conditions": 60, "credit": 55,
            "dollar": 45, "coherence": 70,
            "breadth": 60, "participation": 55,
            "sector_rotation": 50, "volume": 65, "momentum": 58,
            "volatility": 30, "term_structure": 45,
            "skew": 40, "realized": 35, "implied": 42,
        },
        "pillar_weights": {
            "positioning": 0.25, "crowding": 0.20, "squeeze": 0.15,
            "flow": 0.20, "stability": 0.20,
        },
        "warnings": [],
        "missing_inputs": [],
    }
    base.update(overrides)
    return base


def _make_regime_data() -> dict[str, Any]:
    return {
        "trend_data": {"sma_50": 500, "sma_200": 480, "rsi": 55},
        "volatility_data": {"vix": 15.5, "vix_sma20": 16.0},
        "breadth_data": {"adv_dec_ratio": 1.5},
        "rate_data": {"us_10y": 4.2, "us_2y": 4.5},
        "flow_data": {"put_call_ratio": 0.9},
    }


def _make_stock_candidate(**overrides) -> dict[str, Any]:
    """Minimal stock candidate for stock_strategy tests."""
    base = {
        "symbol": "AAPL",
        "composite_score": 72,
        "thesis": "Bullish momentum breakout",
        "metrics": {"rsi": 62, "volume_ratio": 1.5},
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════════════
# L. TransportResult dataclass contract (Step 12)
# ═══════════════════════════════════════════════════════════════════════════

class TestTransportResultContract:
    """Verify TransportResult shape, defaults, and immutability."""

    def test_fields_present(self):
        tr = TransportResult(content="hello")
        assert tr.content == "hello"
        assert tr.transport_path == "legacy"
        assert tr.finish_reason is None
        assert tr.provider is None

    def test_routed_defaults(self):
        tr = TransportResult(content="x", transport_path="routed", provider="bedrock")
        assert tr.transport_path == "routed"
        assert tr.provider == "bedrock"
        assert tr.finish_reason is None

    def test_legacy_with_finish_reason(self):
        tr = TransportResult(content="y", transport_path="legacy", finish_reason="stop")
        assert tr.finish_reason == "stop"
        assert tr.provider is None

    def test_frozen(self):
        tr = TransportResult(content="z")
        with pytest.raises(AttributeError):
            tr.content = "modified"

    def test_equality(self):
        a = TransportResult(content="a", transport_path="routed")
        b = TransportResult(content="a", transport_path="routed")
        assert a == b

    def test_repr(self):
        tr = TransportResult(content="hi")
        assert "TransportResult" in repr(tr)
        assert "hi" in repr(tr)


# ═══════════════════════════════════════════════════════════════════════════
# A. _model_transport — routing path returns TransportResult
# ═══════════════════════════════════════════════════════════════════════════

class TestModelTransportRouting:

    def test_routing_success_returns_transport_result(self):
        result, trace = _make_routing_result("Hello from model")
        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            return_value=(result, trace),
        ):
            tr = _model_transport(
                task_type="test_task",
                payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                log_prefix="TEST",
            )
        assert isinstance(tr, TransportResult)
        assert tr.content == "Hello from model"
        assert tr.transport_path == "routed"
        assert tr.provider == "local_llm"
        assert tr.finish_reason is None

    def test_routing_success_strips_think_tags(self):
        content_with_think = "<think>internal reasoning</think>Clean response"
        result, trace = _make_routing_result(content_with_think)
        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            return_value=(result, trace),
        ):
            tr = _model_transport(
                task_type="test_task",
                payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                log_prefix="TEST",
            )
        assert "<think>" not in tr.content
        assert "Clean response" in tr.content

    def test_routing_extracts_system_prompt(self):
        result, trace = _make_routing_result("ok")
        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            return_value=(result, trace),
        ) as mock_route:
            _model_transport(
                task_type="regime_analysis",
                payload={
                    "messages": [
                        {"role": "system", "content": "You are an analyst."},
                        {"role": "user", "content": "Analyze this."},
                    ],
                    "max_tokens": 2500,
                    "temperature": 0.0,
                },
                log_prefix="MODEL_REGIME",
            )
            call_kwargs = mock_route.call_args[1]
            assert call_kwargs["system_prompt"] == "You are an analyst."
            assert len(call_kwargs["messages"]) == 1
            assert call_kwargs["messages"][0]["role"] == "user"
            assert call_kwargs["task_type"] == "regime_analysis"


# ═══════════════════════════════════════════════════════════════════════════
# B. _model_transport — falls back to legacy when routing disabled
# ═══════════════════════════════════════════════════════════════════════════

class TestModelTransportDisabledFallback:

    def test_routing_disabled_uses_legacy(self):
        from app.services.model_routing_integration import RoutingDisabledError

        resp = _make_legacy_ok("legacy response text")
        resp.json.return_value = {
            "choices": [{"message": {"content": "legacy response text"}, "finish_reason": "stop"}],
        }

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RoutingDisabledError("disabled"),
        ), patch(
            "requests.post",
            return_value=resp,
        ):
            tr = _model_transport(
                task_type="test_task",
                payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                log_prefix="TEST",
                model_url="http://localhost:1234/v1/chat/completions",
            )
        assert tr.content == "legacy response text"
        assert tr.transport_path == "legacy"
        assert tr.provider is None


# ═══════════════════════════════════════════════════════════════════════════
# C. _model_transport — falls back to legacy when routing fails
# ═══════════════════════════════════════════════════════════════════════════

class TestModelTransportRoutingFailureFallback:

    def test_routing_error_result_falls_back(self):
        """Routing returns error status → falls through to legacy."""
        error_result = {"status": "error", "content": None, "error": "provider_crashed"}

        @dataclass
        class _FT:
            selected_provider: str = "local_llm"
            timing_ms: float = 10.0
            request_id: str = "r1"

        resp = _make_legacy_ok("fallback response")
        resp.json.return_value = {
            "choices": [{"message": {"content": "fallback response"}, "finish_reason": "stop"}],
        }

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            return_value=(error_result, _FT()),
        ), patch(
            "requests.post",
            return_value=resp,
        ):
            tr = _model_transport(
                task_type="test_task",
                payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                log_prefix="TEST",
                model_url="http://localhost:1234/v1/chat/completions",
            )
        assert tr.content == "fallback response"
        assert tr.transport_path == "legacy"

    def test_routing_exception_falls_back(self):
        """Routing raises unexpected exception → falls through to legacy."""
        resp = _make_legacy_ok("fallback response")
        resp.json.return_value = {
            "choices": [{"message": {"content": "fallback response"}, "finish_reason": "stop"}],
        }

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RuntimeError("unexpected"),
        ), patch(
            "requests.post",
            return_value=resp,
        ):
            tr = _model_transport(
                task_type="test_task",
                payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                log_prefix="TEST",
                model_url="http://localhost:1234/v1/chat/completions",
            )
        assert tr.content == "fallback response"
        assert tr.transport_path == "legacy"


# ═══════════════════════════════════════════════════════════════════════════
# D. _model_transport — legacy path with retry
# ═══════════════════════════════════════════════════════════════════════════

class TestModelTransportLegacyRetry:

    def test_legacy_retries_on_request_exception(self):
        from requests.exceptions import ConnectionError as ReqConnError
        from app.services.model_routing_integration import RoutingDisabledError

        fail_resp = ReqConnError("refused")
        ok_resp = _make_legacy_ok("retry success")
        ok_resp.json.return_value = {
            "choices": [{"message": {"content": "retry success"}, "finish_reason": "stop"}],
        }

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RoutingDisabledError("off"),
        ), patch(
            "requests.post",
            side_effect=[fail_resp, ok_resp],
        ):
            tr = _model_transport(
                task_type="test_task",
                payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                log_prefix="TEST",
                model_url="http://localhost:1234/v1/chat/completions",
                retries=1,
            )
        assert tr.content == "retry success"
        assert tr.transport_path == "legacy"


# ═══════════════════════════════════════════════════════════════════════════
# E. _model_transport — raises on network failure
# ═══════════════════════════════════════════════════════════════════════════

class TestModelTransportNetworkFailure:

    def test_all_retries_exhausted_raises(self):
        from requests.exceptions import ConnectionError as ReqConnError
        from app.services.model_routing_integration import RoutingDisabledError

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RoutingDisabledError("off"),
        ), patch(
            "requests.post",
            side_effect=ReqConnError("refused"),
        ):
            with pytest.raises(LocalModelUnavailableError, match="unavailable"):
                _model_transport(
                    task_type="test_task",
                    payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                    log_prefix="TEST",
                    model_url="http://localhost:1234/v1/chat/completions",
                    retries=0,
                )

    def test_non_network_error_raises_runtime(self):
        from app.services.model_routing_integration import RoutingDisabledError

        resp = _make_legacy_ok()
        resp.raise_for_status = MagicMock(side_effect=ValueError("bad json"))

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RoutingDisabledError("off"),
        ), patch(
            "requests.post",
            return_value=resp,
        ):
            with pytest.raises(RuntimeError, match="transport failed"):
                _model_transport(
                    task_type="test_task",
                    payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                    log_prefix="TEST",
                    model_url="http://localhost:1234/v1/chat/completions",
                )


# ═══════════════════════════════════════════════════════════════════════════
# F. _model_transport — think tag stripping on legacy path
# ═══════════════════════════════════════════════════════════════════════════

class TestModelTransportThinkTags:

    def test_legacy_path_strips_think_tags(self):
        from app.services.model_routing_integration import RoutingDisabledError

        content = "<think>reasoning here</think>Clean output"
        resp = _make_legacy_ok(content)
        resp.json.return_value = {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        }

        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RoutingDisabledError("off"),
        ), patch(
            "requests.post",
            return_value=resp,
        ):
            tr = _model_transport(
                task_type="test_task",
                payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                log_prefix="TEST",
                model_url="http://localhost:1234/v1/chat/completions",
            )
        assert "<think>" not in tr.content
        assert "Clean output" in tr.content
        assert tr.transport_path == "legacy"


# ═══════════════════════════════════════════════════════════════════════════
# M. finish_reason extraction on legacy path (Step 12)
# ═══════════════════════════════════════════════════════════════════════════

class TestFinishReasonExtraction:
    """Verify finish_reason is captured on legacy path, None on routed."""

    def test_legacy_stop_finish_reason(self):
        from app.services.model_routing_integration import RoutingDisabledError

        resp = _make_legacy_ok("ok", finish_reason="stop")
        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RoutingDisabledError("off"),
        ), patch("requests.post", return_value=resp):
            tr = _model_transport(
                task_type="test", payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                log_prefix="T", model_url="http://localhost:1234/v1/chat/completions",
            )
        assert tr.finish_reason == "stop"

    def test_legacy_length_finish_reason(self):
        from app.services.model_routing_integration import RoutingDisabledError

        resp = _make_legacy_ok("truncated...", finish_reason="length")
        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RoutingDisabledError("off"),
        ), patch("requests.post", return_value=resp):
            tr = _model_transport(
                task_type="test", payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                log_prefix="T", model_url="http://localhost:1234/v1/chat/completions",
            )
        assert tr.finish_reason == "length"

    def test_routed_finish_reason_is_none(self):
        result, trace = _make_routing_result("ok")
        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            return_value=(result, trace),
        ):
            tr = _model_transport(
                task_type="test", payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                log_prefix="T",
            )
        assert tr.finish_reason is None

    def test_legacy_missing_finish_reason_is_none(self):
        from app.services.model_routing_integration import RoutingDisabledError

        resp = _make_legacy_ok("ok")
        # Remove finish_reason from response
        resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RoutingDisabledError("off"),
        ), patch("requests.post", return_value=resp):
            tr = _model_transport(
                task_type="test", payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                log_prefix="T", model_url="http://localhost:1234/v1/chat/completions",
            )
        assert tr.finish_reason is None


# ═══════════════════════════════════════════════════════════════════════════
# N. transport_path and provider metadata (Step 12)
# ═══════════════════════════════════════════════════════════════════════════

class TestTransportPathAndProvider:

    def test_routed_path_has_provider(self):
        result, trace = _make_routing_result("content")
        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            return_value=(result, trace),
        ):
            tr = _model_transport(
                task_type="test", payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                log_prefix="T",
            )
        assert tr.transport_path == "routed"
        assert tr.provider == "local_llm"

    def test_legacy_path_has_no_provider(self):
        from app.services.model_routing_integration import RoutingDisabledError

        resp = _make_legacy_ok("content")
        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RoutingDisabledError("off"),
        ), patch("requests.post", return_value=resp):
            tr = _model_transport(
                task_type="test", payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                log_prefix="T", model_url="http://localhost:1234/v1/chat/completions",
            )
        assert tr.transport_path == "legacy"
        assert tr.provider is None


# ═══════════════════════════════════════════════════════════════════════════
# G. All 10 migrated analyze_* functions delegate to _model_transport
# ═══════════════════════════════════════════════════════════════════════════

class TestAnalyzeFunctionsUseModelTransport:

    @pytest.fixture(autouse=True)
    def _patch_transport(self):
        """Patch _model_transport so no real HTTP/routing occurs."""
        self._transport_mock = MagicMock()
        patcher = patch("common.model_analysis._model_transport", self._transport_mock)
        patcher.start()
        yield
        patcher.stop()

    # ── analyze_regime ──────────────────────────────────────────

    def test_regime_delegates_to_transport(self):
        self._transport_mock.return_value = _make_transport_result(_REGIME_JSON_RESPONSE)
        from common.model_analysis import analyze_regime

        result = analyze_regime(
            regime_data=_make_regime_data(),
            model_url="http://test:1234/v1/chat/completions",
        )
        self._transport_mock.assert_called_once()
        kw = self._transport_mock.call_args[1]
        assert kw["task_type"] == "regime_analysis"
        assert kw["log_prefix"] == "MODEL_REGIME"
        assert kw["model_url"] == "http://test:1234/v1/chat/completions"

    def test_regime_result_has_trace(self):
        self._transport_mock.return_value = _make_transport_result(_REGIME_JSON_RESPONSE)
        from common.model_analysis import analyze_regime

        result = analyze_regime(regime_data=_make_regime_data())
        assert "_trace" in result
        assert result["_trace"]["model_regime_input_mode"] == "raw_only"
        assert result["_trace"]["transport_path"] == "routed"

    def test_regime_result_shape(self):
        self._transport_mock.return_value = _make_transport_result(_REGIME_JSON_RESPONSE)
        from common.model_analysis import analyze_regime

        result = analyze_regime(regime_data=_make_regime_data())
        assert result.get("risk_regime_label") == "RISK_ON"
        assert isinstance(result.get("confidence"), float)

    # ── analyze_news_sentiment ──────────────────────────────────

    def test_news_sentiment_delegates_to_transport(self):
        self._transport_mock.return_value = _make_transport_result(_NEWS_JSON_RESPONSE)
        from common.model_analysis import analyze_news_sentiment

        result = analyze_news_sentiment(
            items=[{"headline": "Market rallies", "source": "reuters"}],
            macro_context={"gdp_growth": 2.5},
        )
        self._transport_mock.assert_called_once()
        kw = self._transport_mock.call_args[1]
        assert kw["task_type"] == "news_sentiment"
        assert kw["log_prefix"] == "MODEL_NEWS"

    def test_news_sentiment_result_has_trace(self):
        self._transport_mock.return_value = _make_transport_result(_NEWS_JSON_RESPONSE)
        from common.model_analysis import analyze_news_sentiment

        result = analyze_news_sentiment(
            items=[{"headline": "test"}],
            macro_context={},
        )
        assert "_trace" in result
        assert "json_parse_method" in result["_trace"]
        assert result["_trace"]["transport_path"] == "routed"

    # ── analyze_breadth_participation ───────────────────────────

    def test_breadth_delegates_to_transport(self):
        self._transport_mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_breadth_participation

        result = analyze_breadth_participation(engine_result=_make_engine_result())
        self._transport_mock.assert_called_once()
        kw = self._transport_mock.call_args[1]
        assert kw["task_type"] == "breadth_participation"
        assert kw["log_prefix"] == "MODEL_BREADTH"

    def test_breadth_result_has_trace(self):
        self._transport_mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_breadth_participation

        result = analyze_breadth_participation(engine_result=_make_engine_result())
        assert "_trace" in result
        assert "excluded_derived_fields" in result["_trace"]
        assert result["_trace"]["transport_path"] == "routed"

    # ── analyze_volatility_options ──────────────────────────────

    def test_volatility_delegates_to_transport(self):
        self._transport_mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_volatility_options

        result = analyze_volatility_options(engine_result=_make_engine_result())
        self._transport_mock.assert_called_once()
        kw = self._transport_mock.call_args[1]
        assert kw["task_type"] == "volatility_options"
        assert kw["log_prefix"] == "MODEL_VOL"

    def test_volatility_result_has_trace(self):
        self._transport_mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_volatility_options

        result = analyze_volatility_options(engine_result=_make_engine_result())
        assert "_trace" in result
        assert "excluded_derived_fields" in result["_trace"]
        assert result["_trace"]["input_mode"] == "raw_only"
        assert result["_trace"]["transport_path"] == "routed"

    # ── analyze_cross_asset_macro ───────────────────────────────

    def test_cross_asset_delegates_to_transport(self):
        self._transport_mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_cross_asset_macro

        result = analyze_cross_asset_macro(engine_result=_make_engine_result())
        self._transport_mock.assert_called_once()
        kw = self._transport_mock.call_args[1]
        assert kw["task_type"] == "cross_asset_macro"
        assert kw["log_prefix"] == "MODEL_CROSS_ASSET"

    def test_cross_asset_result_has_trace(self):
        self._transport_mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_cross_asset_macro

        result = analyze_cross_asset_macro(engine_result=_make_engine_result())
        assert "_trace" in result
        assert "excluded_derived_fields" in result["_trace"]
        assert result["_trace"]["transport_path"] == "routed"

    # ── analyze_flows_positioning ───────────────────────────────

    def test_flows_delegates_to_transport(self):
        self._transport_mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_flows_positioning

        result = analyze_flows_positioning(engine_result=_make_engine_result())
        self._transport_mock.assert_called_once()
        kw = self._transport_mock.call_args[1]
        assert kw["task_type"] == "flows_positioning"
        assert kw["log_prefix"] == "MODEL_FLOWS_POS"

    def test_flows_result_has_trace(self):
        self._transport_mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_flows_positioning

        result = analyze_flows_positioning(engine_result=_make_engine_result())
        assert "_trace" in result
        assert "excluded_derived_fields" in result["_trace"]
        assert result["_trace"]["transport_path"] == "routed"

    # ── analyze_liquidity_conditions ────────────────────────────

    def test_liquidity_delegates_to_transport(self):
        self._transport_mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_liquidity_conditions

        result = analyze_liquidity_conditions(engine_result=_make_engine_result())
        self._transport_mock.assert_called_once()
        kw = self._transport_mock.call_args[1]
        assert kw["task_type"] == "liquidity_conditions"
        assert kw["log_prefix"] == "MODEL_LIQ_COND"

    def test_liquidity_result_has_trace(self):
        self._transport_mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_liquidity_conditions

        result = analyze_liquidity_conditions(engine_result=_make_engine_result())
        assert "_trace" in result
        assert "excluded_derived_fields" in result["_trace"]
        assert result["_trace"]["transport_path"] == "routed"


# ═══════════════════════════════════════════════════════════════════════════
# O. analyze_stock_idea delegation and trace (Step 12)
# ═══════════════════════════════════════════════════════════════════════════

class TestStockIdeaDelegation:
    """Verify analyze_stock_idea delegates to _model_transport."""

    @pytest.fixture(autouse=True)
    def _patch_transport(self):
        self._mock = MagicMock()
        p = patch("common.model_analysis._model_transport", self._mock)
        p.start()
        yield
        p.stop()

    def test_stock_idea_delegates_to_transport(self):
        self._mock.return_value = _make_transport_result(_STOCK_IDEA_JSON_RESPONSE)
        from common.model_analysis import analyze_stock_idea

        result = analyze_stock_idea(
            symbol="AAPL",
            idea={"price": 200, "thesis": "momentum"},
            source="local_llm",
            model_url="http://test:1234/v1/chat/completions",
        )
        self._mock.assert_called_once()
        kw = self._mock.call_args[1]
        assert kw["task_type"] == "stock_idea"
        assert kw["log_prefix"] == "MODEL_STOCK_IDEA"

    def test_stock_idea_result_has_trace(self):
        self._mock.return_value = _make_transport_result(_STOCK_IDEA_JSON_RESPONSE)
        from common.model_analysis import analyze_stock_idea

        result = analyze_stock_idea(
            symbol="AAPL",
            idea={"price": 200},
            source="local_llm",
        )
        assert "_trace" in result
        assert result["_trace"]["transport_path"] == "routed"
        assert result["_trace"]["finish_reason"] is None

    def test_stock_idea_coercion(self):
        self._mock.return_value = _make_transport_result(_STOCK_IDEA_JSON_RESPONSE)
        from common.model_analysis import analyze_stock_idea

        result = analyze_stock_idea(
            symbol="SPY",
            idea={"price": 500},
            source="local_llm",
        )
        assert result.get("recommendation") == "BUY"
        assert isinstance(result.get("confidence"), float)

    def test_stock_idea_bad_json_raises(self):
        self._mock.return_value = _make_transport_result("not valid json at all")
        from common.model_analysis import analyze_stock_idea

        with pytest.raises((ValueError, RuntimeError)):
            analyze_stock_idea(
                symbol="SPY",
                idea={"price": 500},
                source="local_llm",
            )


# ═══════════════════════════════════════════════════════════════════════════
# P. analyze_stock_strategy delegation, retry-with-fix, and fallback (Step 12)
# ═══════════════════════════════════════════════════════════════════════════

class TestStockStrategyDelegation:
    """Verify analyze_stock_strategy delegates to _model_transport and
    handles retry-with-fix + fallback correctly."""

    @pytest.fixture(autouse=True)
    def _patch_transport(self):
        self._mock = MagicMock()
        p = patch("common.model_analysis._model_transport", self._mock)
        p.start()
        yield
        p.stop()

    @pytest.fixture(autouse=True)
    def _patch_prompts(self):
        """Provide minimal prompts so we don't need the full prompt module."""
        with patch("common.stock_strategy_prompts.STOCK_STRATEGY_SYSTEM_PROMPT", "You are a stock analyst."), \
             patch("common.stock_strategy_prompts.build_stock_strategy_user_prompt", return_value="Analyze AAPL"):
            yield

    def test_strategy_delegates_to_transport(self):
        self._mock.return_value = _make_transport_result(_STOCK_STRATEGY_JSON_RESPONSE)
        from common.model_analysis import analyze_stock_strategy

        result = analyze_stock_strategy(
            strategy_id="stock_pullback_swing",
            candidate=_make_stock_candidate(),
            model_url="http://test:1234/v1/chat/completions",
        )
        # Primary call
        assert self._mock.call_count >= 1
        kw = self._mock.call_args_list[0][1]
        assert kw["task_type"] == "stock_strategy"
        assert kw["log_prefix"] == "MODEL_STOCK_STRATEGY"

    def test_strategy_result_has_trace(self):
        self._mock.return_value = _make_transport_result(_STOCK_STRATEGY_JSON_RESPONSE)
        from common.model_analysis import analyze_stock_strategy

        result = analyze_stock_strategy(
            strategy_id="stock_pullback_swing",
            candidate=_make_stock_candidate(),
        )
        assert "_trace" in result
        assert result["_trace"]["transport_path"] == "routed"

    def test_strategy_result_shape(self):
        self._mock.return_value = _make_transport_result(_STOCK_STRATEGY_JSON_RESPONSE)
        from common.model_analysis import analyze_stock_strategy

        result = analyze_stock_strategy(
            strategy_id="stock_pullback_swing",
            candidate=_make_stock_candidate(),
        )
        assert result.get("recommendation") == "BUY"
        assert isinstance(result.get("score"), int)
        assert "timestamp" in result

    def test_strategy_retry_with_fix_on_bad_json(self):
        """On parse failure, a second _model_transport call is made with fix messages."""
        bad = _make_transport_result("not json at all")
        good = _make_transport_result(_STOCK_STRATEGY_JSON_RESPONSE)
        self._mock.side_effect = [bad, good]

        from common.model_analysis import analyze_stock_strategy

        result = analyze_stock_strategy(
            strategy_id="stock_pullback_swing",
            candidate=_make_stock_candidate(),
        )
        assert self._mock.call_count == 2
        # Second call is the retry-fix
        kw2 = self._mock.call_args_list[1][1]
        assert kw2["task_type"] == "stock_strategy_fix"
        assert result.get("recommendation") == "BUY"

    def test_strategy_fallback_on_total_failure(self):
        """On total parse failure (primary + fix both bad), returns PASS fallback."""
        bad = _make_transport_result("garbage")
        self._mock.return_value = bad  # both calls return garbage

        from common.model_analysis import analyze_stock_strategy

        result = analyze_stock_strategy(
            strategy_id="stock_pullback_swing",
            candidate=_make_stock_candidate(),
        )
        assert result.get("recommendation") == "PASS"
        assert result.get("_fallback") is True


# ═══════════════════════════════════════════════════════════════════════════
# Q. analyze_tmc_final_decision delegation, retry-with-fix, fallback (Step 12)
# ═══════════════════════════════════════════════════════════════════════════

class TestTmcFinalDecisionDelegation:
    """Verify analyze_tmc_final_decision delegates to _model_transport."""

    @pytest.fixture(autouse=True)
    def _patch_transport(self):
        self._mock = MagicMock()
        p = patch("common.model_analysis._model_transport", self._mock)
        p.start()
        yield
        p.stop()

    @pytest.fixture(autouse=True)
    def _patch_prompts(self):
        with patch("common.tmc_final_decision_prompts.TMC_FINAL_DECISION_SYSTEM_PROMPT", "You decide trades."), \
             patch("common.tmc_final_decision_prompts.build_tmc_final_decision_prompt", return_value="Decide on AAPL"):
            yield

    def test_tmc_delegates_to_transport(self):
        self._mock.return_value = _make_transport_result(_TMC_DECISION_JSON_RESPONSE)
        from common.model_analysis import analyze_tmc_final_decision

        result = analyze_tmc_final_decision(
            candidate=_make_stock_candidate(),
            model_url="http://test:1234/v1/chat/completions",
        )
        assert self._mock.call_count >= 1
        kw = self._mock.call_args_list[0][1]
        assert kw["task_type"] == "tmc_final_decision"
        assert kw["log_prefix"] == "TMC_FINAL_DECISION"

    def test_tmc_result_has_trace(self):
        self._mock.return_value = _make_transport_result(_TMC_DECISION_JSON_RESPONSE)
        from common.model_analysis import analyze_tmc_final_decision

        result = analyze_tmc_final_decision(candidate=_make_stock_candidate())
        assert "_trace" in result
        assert result["_trace"]["transport_path"] == "routed"

    def test_tmc_result_shape(self):
        self._mock.return_value = _make_transport_result(_TMC_DECISION_JSON_RESPONSE)
        from common.model_analysis import analyze_tmc_final_decision

        result = analyze_tmc_final_decision(candidate=_make_stock_candidate())
        assert result.get("decision") == "EXECUTE"
        assert isinstance(result.get("conviction"), int)
        assert "timestamp" in result

    def test_tmc_retry_with_fix(self):
        bad = _make_transport_result("not json")
        good = _make_transport_result(_TMC_DECISION_JSON_RESPONSE)
        self._mock.side_effect = [bad, good]

        from common.model_analysis import analyze_tmc_final_decision

        result = analyze_tmc_final_decision(candidate=_make_stock_candidate())
        assert self._mock.call_count == 2
        kw2 = self._mock.call_args_list[1][1]
        assert kw2["task_type"] == "tmc_final_decision_fix"
        assert result.get("decision") == "EXECUTE"

    def test_tmc_fallback_on_total_failure(self):
        self._mock.return_value = _make_transport_result("garbage")

        from common.model_analysis import analyze_tmc_final_decision

        result = analyze_tmc_final_decision(candidate=_make_stock_candidate())
        assert result.get("decision") == "PASS"
        assert result.get("_fallback") is True


# ═══════════════════════════════════════════════════════════════════════════
# H. analyze_trade remains non-migrated
# ═══════════════════════════════════════════════════════════════════════════

class TestNonMigratedFunctionsUnchanged:
    """analyze_trade still does NOT call _model_transport — it delegates
    to common.utils._analyze_trade_with_model_legacy()."""

    def test_analyze_trade_does_not_use_transport(self):
        from common.model_analysis import analyze_trade

        with patch("common.model_analysis._model_transport") as mock_transport, \
             patch("common.utils._analyze_trade_with_model_legacy", return_value={"result": "ok"}) as mock_legacy:
            try:
                result = analyze_trade(
                    trade_data={"symbol": "SPY"},
                    model_url="http://test:1234/v1/chat/completions",
                )
            except Exception:
                pass
            mock_transport.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# I. Result coercion produces expected shape
# ═══════════════════════════════════════════════════════════════════════════

class TestResultCoercionShape:
    """Verify the migrated functions produce correctly coerced output."""

    @pytest.fixture(autouse=True)
    def _patch_transport(self):
        self._mock = MagicMock()
        p = patch("common.model_analysis._model_transport", self._mock)
        p.start()
        yield
        p.stop()

    def test_breadth_coercion_produces_label_score(self):
        self._mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_breadth_participation

        result = analyze_breadth_participation(engine_result=_make_engine_result())
        assert "label" in result
        assert "score" in result
        assert isinstance(result["label"], str)

    def test_volatility_coercion_produces_label_score(self):
        self._mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_volatility_options

        result = analyze_volatility_options(engine_result=_make_engine_result())
        assert "label" in result
        assert "score" in result

    def test_cross_asset_coercion_produces_label_score(self):
        self._mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_cross_asset_macro

        result = analyze_cross_asset_macro(engine_result=_make_engine_result())
        assert "label" in result
        assert "score" in result

    def test_flows_coercion_produces_label_score(self):
        self._mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_flows_positioning

        result = analyze_flows_positioning(engine_result=_make_engine_result())
        assert "label" in result
        assert "score" in result

    def test_liquidity_coercion_produces_label_score(self):
        self._mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
        from common.model_analysis import analyze_liquidity_conditions

        result = analyze_liquidity_conditions(engine_result=_make_engine_result())
        assert "label" in result
        assert "score" in result

    def test_regime_coercion_produces_regime_label(self):
        self._mock.return_value = _make_transport_result(_REGIME_JSON_RESPONSE)
        from common.model_analysis import analyze_regime

        result = analyze_regime(regime_data=_make_regime_data())
        assert "risk_regime_label" in result
        assert "confidence" in result


# ═══════════════════════════════════════════════════════════════════════════
# J. routing_enabled=False → legacy for all migrated functions
# ═══════════════════════════════════════════════════════════════════════════

class TestRoutingDisabledLegacy:
    """When routing is disabled, _model_transport still works via legacy path
    and all migrated functions produce valid output."""

    def test_disabled_routing_produces_valid_output(self):
        from app.services.model_routing_integration import RoutingDisabledError

        resp = _make_legacy_ok(_VALID_JSON_RESPONSE)
        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RoutingDisabledError("off"),
        ), patch(
            "requests.post",
            return_value=resp,
        ):
            tr = _model_transport(
                task_type="breadth_participation",
                payload={"messages": [{"role": "user", "content": "test"}], "max_tokens": 2500},
                log_prefix="MODEL_BREADTH",
                model_url="http://localhost:1234/v1/chat/completions",
            )
        assert tr.content == _VALID_JSON_RESPONSE
        assert tr.transport_path == "legacy"

    def test_model_url_resolved_lazily(self):
        """When model_url=None, _model_transport resolves it via get_model_endpoint."""
        from app.services.model_routing_integration import RoutingDisabledError

        resp = _make_legacy_ok(_VALID_JSON_RESPONSE)
        with patch(
            "app.services.model_routing_integration.execute_routed_model",
            side_effect=RoutingDisabledError("off"),
        ), patch(
            "app.services.model_router.get_model_endpoint",
            return_value="http://auto:1234/v1/chat/completions",
        ) as mock_ep, patch(
            "requests.post",
            return_value=resp,
        ):
            tr = _model_transport(
                task_type="test_task",
                payload={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                log_prefix="TEST",
                model_url=None,
            )
        mock_ep.assert_called_once()
        assert tr.content == _VALID_JSON_RESPONSE


# ═══════════════════════════════════════════════════════════════════════════
# K. Task type inventory — all 10 expected task_types are wired
# ═══════════════════════════════════════════════════════════════════════════

class TestTaskTypeInventory:
    """Each migrated function passes a unique, well-known task_type."""

    EXPECTED_TASK_TYPES = {
        "regime_analysis",
        "news_sentiment",
        "breadth_participation",
        "volatility_options",
        "cross_asset_macro",
        "flows_positioning",
        "liquidity_conditions",
        "stock_idea",
        "stock_strategy",
        "tmc_final_decision",
    }

    @pytest.fixture(autouse=True)
    def _patch_transport(self):
        self._mock = MagicMock(return_value=_make_transport_result(_VALID_JSON_RESPONSE))
        p = patch("common.model_analysis._model_transport", self._mock)
        p.start()
        yield
        p.stop()

    @pytest.fixture(autouse=True)
    def _patch_prompts(self):
        with patch("common.stock_strategy_prompts.STOCK_STRATEGY_SYSTEM_PROMPT", "You are a stock analyst."), \
             patch("common.stock_strategy_prompts.build_stock_strategy_user_prompt", return_value="Analyze AAPL"), \
             patch("common.tmc_final_decision_prompts.TMC_FINAL_DECISION_SYSTEM_PROMPT", "You decide trades."), \
             patch("common.tmc_final_decision_prompts.build_tmc_final_decision_prompt", return_value="Decide on AAPL"):
            yield

    def test_all_task_types_registered(self):
        from common.model_analysis import (
            analyze_breadth_participation,
            analyze_cross_asset_macro,
            analyze_flows_positioning,
            analyze_liquidity_conditions,
            analyze_volatility_options,
            analyze_stock_idea,
            analyze_stock_strategy,
            analyze_tmc_final_decision,
        )

        engine = _make_engine_result()
        funcs = [
            (analyze_breadth_participation, {"engine_result": engine}),
            (analyze_volatility_options, {"engine_result": engine}),
            (analyze_cross_asset_macro, {"engine_result": engine}),
            (analyze_flows_positioning, {"engine_result": engine}),
            (analyze_liquidity_conditions, {"engine_result": engine}),
            (analyze_stock_idea, {"symbol": "SPY", "idea": {"p": 1}, "source": "test"}),
            (analyze_stock_strategy, {"strategy_id": "stock_pullback_swing",
                                      "candidate": _make_stock_candidate()}),
            (analyze_tmc_final_decision, {"candidate": _make_stock_candidate()}),
        ]

        task_types_seen = set()
        for fn, kwargs in funcs:
            self._mock.reset_mock()
            # stock_strategy and tmc need richer JSON responses
            if fn.__name__ == "analyze_stock_strategy":
                self._mock.return_value = _make_transport_result(_STOCK_STRATEGY_JSON_RESPONSE)
            elif fn.__name__ == "analyze_tmc_final_decision":
                self._mock.return_value = _make_transport_result(_TMC_DECISION_JSON_RESPONSE)
            elif fn.__name__ == "analyze_stock_idea":
                self._mock.return_value = _make_transport_result(_STOCK_IDEA_JSON_RESPONSE)
            else:
                self._mock.return_value = _make_transport_result(_VALID_JSON_RESPONSE)
            fn(**kwargs)
            kw = self._mock.call_args_list[0][1]
            task_types_seen.add(kw["task_type"])

        # Add regime and news (different signatures)
        self._mock.reset_mock()
        self._mock.return_value = _make_transport_result(_REGIME_JSON_RESPONSE)
        from common.model_analysis import analyze_regime
        analyze_regime(regime_data=_make_regime_data())
        task_types_seen.add(self._mock.call_args[1]["task_type"])

        self._mock.reset_mock()
        self._mock.return_value = _make_transport_result(_NEWS_JSON_RESPONSE)
        from common.model_analysis import analyze_news_sentiment
        analyze_news_sentiment(items=[{"headline": "t"}], macro_context={})
        task_types_seen.add(self._mock.call_args[1]["task_type"])

        assert task_types_seen == self.EXPECTED_TASK_TYPES
