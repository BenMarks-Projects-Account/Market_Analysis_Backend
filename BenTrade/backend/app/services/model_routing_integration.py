"""Model routing integration — controlled migration from legacy to routed execution.

This module provides:
    1. Integration policy — when callers should use routed vs legacy paths.
    2. ``execute_routed_model()`` — compatibility helper for migrated callers.
    3. Legacy result adaptation — converts ``ProviderResult`` → legacy dict shape.

Migration strategy (Step 8):
    • Migrated callers use ``execute_routed_model()`` which handles
      request construction, routing, and result adaptation.
    • Non-migrated callers continue using ``model_request()`` / raw
      ``requests.post()`` unchanged.
    • The integration policy is centralized here — no scattered routing decisions.

Integration policy:
    ┌──────────────────────────────────────┬───────────────────────────────┐
    │ Call type                            │ Routing mode                  │
    ├──────────────────────────────────────┼───────────────────────────────┤
    │ Routine local-preferred analysis     │ local_distributed             │
    │ Market picture model interpretation  │ local_distributed             │
    │ Active trade reassessment            │ local_distributed             │
    │ Final synthesis / TMC decision       │ online_distributed            │
    │ Premium API-triggered final answer   │ premium_override=True         │
    │ Low-level / experimental             │ legacy (model_request)        │
    └──────────────────────────────────────┴───────────────────────────────┘

Safety:
    • ``execute_routed_model()`` never logs prompt content.
    • The trace is returned alongside the result for callers that want it.
    • Legacy-compatible result shape is maintained by ``adapt_to_legacy()``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.services.model_provider_base import ProviderResult
from app.services.model_routing_contract import (
    ExecutionMode,
    ExecutionRequest,
    ExecutionStatus,
    ExecutionTrace,
)

logger = logging.getLogger("bentrade.routing.integration")


# ---------------------------------------------------------------------------
# Routing-disabled exception
# ---------------------------------------------------------------------------

class RoutingDisabledError(RuntimeError):
    """Raised when routing is attempted but ``routing_enabled`` is False.

    Callers that want to fall back to legacy paths should catch this.
    """


def _routing_is_enabled() -> bool:
    """Check the central ``routing_enabled`` toggle.

    Returns True when routing should proceed, False when callers
    must use their legacy path.  A warning is logged on the first
    call where routing is disabled so operators can audit.
    """
    from app.services.model_routing_config import get_routing_config
    return get_routing_config().routing_enabled


# ---------------------------------------------------------------------------
# 1. Integration policy constants
# ---------------------------------------------------------------------------

# Task types that should use online_distributed (cloud-capable fallback).
# NOTE: Only used by the deprecated ``resolve_routing_mode()`` below.
# The authoritative ``resolve_effective_execution_mode()`` does NOT
# consult this set — it relies on UI-selected mode / caller_mode instead.
_ONLINE_DISTRIBUTED_TASKS: frozenset[str] = frozenset({
    "tmc_final_decision",
})

# Task types that warrant premium_override when explicitly requested.
# Used by both ``resolve_routing_mode()`` (deprecated) and
# ``resolve_effective_execution_mode()`` (authoritative).
_PREMIUM_ELIGIBLE_TASKS: frozenset[str] = frozenset({
    "tmc_final_decision",
})

# Default mode for most migrated callers.
DEFAULT_ROUTED_MODE = ExecutionMode.LOCAL_DISTRIBUTED.value


def resolve_routing_mode(
    task_type: str,
    *,
    premium: bool = False,
) -> tuple[str, bool]:
    """Determine routing mode and premium flag for a task type.

    Returns (mode, premium_override).

    Policy:
        • Tasks in _ONLINE_DISTRIBUTED_TASKS → online_distributed.
        • All other migrated tasks → local_distributed.
        • premium=True is only honoured for _PREMIUM_ELIGIBLE_TASKS.

    .. deprecated:: Step 18
        Replaced by :func:`resolve_effective_execution_mode` which adds
        UI-selected mode, caller_mode override, and clear precedence.
        No production code calls this function.  Retained only for
        backward-compatible test references.
    """
    if task_type in _ONLINE_DISTRIBUTED_TASKS:
        mode = ExecutionMode.ONLINE_DISTRIBUTED.value
    else:
        mode = DEFAULT_ROUTED_MODE

    use_premium = premium and task_type in _PREMIUM_ELIGIBLE_TASKS
    return mode, use_premium


def resolve_effective_execution_mode(
    task_type: str,
    *,
    premium: bool = False,
    caller_mode: str | None = None,
) -> tuple[str, bool]:
    """Determine effective execution mode with clear precedence (Step 18).

    Precedence (highest → lowest):
        1. Premium override for eligible tasks → premium_online.
        2. Explicit caller_mode (function-level / caller-forced override).
        3. UI-selected mode from ``execution_mode_state.get_execution_mode()``.
        4. Fallback ``DEFAULT_ROUTED_MODE``.

    Input fields: task_type, premium, caller_mode.
    Derived: (mode, premium_override).

    Returns (mode: str, premium_override: bool).
    """
    from app.services.model_routing_contract import is_valid_mode

    # 1. Premium override
    use_premium = premium and task_type in _PREMIUM_ELIGIBLE_TASKS
    if use_premium:
        return ExecutionMode.PREMIUM_ONLINE.value, True

    # 2. Explicit caller-forced mode
    if caller_mode is not None and is_valid_mode(caller_mode):
        return caller_mode, False

    # 3. UI-selected mode
    from app.services.execution_mode_state import get_execution_mode
    ui_mode = get_execution_mode()
    if ui_mode and is_valid_mode(ui_mode):
        return ui_mode, False

    # 4. Fallback default
    return DEFAULT_ROUTED_MODE, False


# ---------------------------------------------------------------------------
# 2. Routed execution helper
# ---------------------------------------------------------------------------

def execute_routed_model(
    *,
    task_type: str,
    messages: list[dict[str, Any]],
    system_prompt: str | None = None,
    model_name: str | None = None,
    timeout: float = 180.0,
    premium: bool = False,
    metadata: dict[str, Any] | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    execution_mode: str | None = None,
) -> tuple[dict[str, Any], ExecutionTrace]:
    """Execute a model call through the distributed routing system.

    This is the single integration point for migrated callers.  It:
        1. Resolves routing mode via ``resolve_effective_execution_mode()``.
        2. Constructs an ``ExecutionRequest``.
        3. Routes via ``route_and_execute()``.
        4. Adapts the result to legacy-compatible dict shape.
        5. Returns (legacy_result, trace) so callers can inspect both.

    Args:
        task_type:       Semantic label (e.g. "tmc_final_decision").
        messages:        OpenAI-compatible message list.
        system_prompt:   Optional system message (prepended if given).
        model_name:      Model identifier (optional).
        timeout:         Request timeout in seconds.
        premium:         If True and task is premium-eligible, use premium path.
        metadata:        Caller metadata for tracing.
        max_tokens:      Max tokens for the model response.
        temperature:     Temperature for the model response.
        execution_mode:  Explicit mode override (Step 18). If provided and
                         valid, takes precedence over UI-selected mode.
                         Premium override still wins if applicable.

    Returns:
        (legacy_result_dict, ExecutionTrace)

        legacy_result_dict has shape:
            {
                "status": "success" | "error",
                "content": str | None,
                "raw_response": Any,
                "provider": str | None,
                "model_name": str | None,
                "timing_ms": float | None,
                "error": str | None,
                "routed": True,
                "request_id": str,
            }

    Raises:
        RoutingDisabledError: if ``routing_enabled`` is False in the
            central config.  Callers should catch this to fall back
            to their legacy path.
    """
    # ── Kill switch ──────────────────────────────────────────────
    if not _routing_is_enabled():
        logger.info(
            "[integration] routing disabled — task_type=%s bypassed",
            task_type,
        )
        raise RoutingDisabledError(
            f"Routing is disabled via config (task_type={task_type})"
        )

    from app.services.model_router import route_and_execute

    mode, use_premium = resolve_effective_execution_mode(
        task_type, premium=premium, caller_mode=execution_mode,
    )

    # Build prompt list — prepend system_prompt if provided.
    prompt = list(messages)
    effective_system = system_prompt

    # Attach generation params to routing_overrides for provider adapters.
    routing_overrides: dict[str, Any] = {}
    if max_tokens is not None:
        routing_overrides["max_tokens"] = max_tokens
    if temperature is not None:
        routing_overrides["temperature"] = temperature

    request = ExecutionRequest(
        mode=mode,
        model_name=model_name,
        task_type=task_type,
        prompt=prompt,
        system_prompt=effective_system,
        premium_override=use_premium,
        routing_overrides=routing_overrides,
        metadata=dict(metadata or {}),
    )

    logger.info(
        "[integration] executing routed call: task_type=%s mode=%s premium=%s",
        task_type, mode, use_premium,
    )

    provider_result, trace = route_and_execute(request, timeout=timeout)

    legacy = adapt_to_legacy(provider_result, trace)

    if legacy["status"] == "success":
        logger.info(
            "[integration] routed call OK: task_type=%s provider=%s timing_ms=%s",
            task_type, trace.selected_provider, trace.timing_ms,
        )
    else:
        logger.warning(
            "[integration] routed call failed: task_type=%s error=%s",
            task_type, legacy.get("error"),
        )

    return legacy, trace


# ---------------------------------------------------------------------------
# 3. Legacy result adaptation
# ---------------------------------------------------------------------------

def adapt_to_legacy(
    result: ProviderResult | None,
    trace: ExecutionTrace,
) -> dict[str, Any]:
    """Convert a routed execution result to legacy-compatible dict shape.

    The output dict is designed to be drop-in compatible with the dict
    returned by ``model_request()`` / ``_default_model_executor()`` after
    minimal caller-side adaptation.
    """
    if result is not None and result.success:
        return {
            "status": "success",
            "content": result.content,
            "raw_response": result.raw_response,
            "provider": result.provider,
            "model_name": (result.metadata or {}).get("model_name") or result.provider,
            "timing_ms": result.timing_ms or trace.timing_ms,
            "error": None,
            "routed": True,
            "request_id": trace.request_id,
        }

    error_msg = (
        result.error_message if result is not None
        else trace.error_summary or "No provider available"
    )
    return {
        "status": "error",
        "content": None,
        "raw_response": result.raw_response if result else None,
        "provider": trace.selected_provider,
        "model_name": None,
        "timing_ms": trace.timing_ms,
        "error": error_msg,
        "routed": True,
        "request_id": trace.request_id,
    }


# ---------------------------------------------------------------------------
# 4. TMC routed wrapper
# ---------------------------------------------------------------------------

def routed_tmc_final_decision(
    *,
    candidate: dict[str, Any],
    market_picture_context: dict[str, Any] | None = None,
    strategy_id: str | None = None,
    retries: int = 0,
    timeout: int = 180,
    premium: bool = False,
) -> dict[str, Any]:
    """Run TMC final decision via distributed routing (Step 8).

    Wraps the prompt construction from ``analyze_tmc_final_decision()``
    and executes through ``execute_routed_model()`` with
    ``online_distributed`` mode for cloud-capable fallback.

    On routing infrastructure failure, falls back to the legacy
    ``analyze_tmc_final_decision()`` for safety.

    Returns the same output shape as ``analyze_tmc_final_decision()``.
    """
    import logging as _logging
    _log = _logging.getLogger("bentrade.model_analysis")

    symbol = candidate.get("symbol", "???")

    # ── Kill switch — fast path to legacy ────────────────────────
    if not _routing_is_enabled():
        _log.info(
            "[TMC_ROUTED] routing disabled — using legacy path for %s",
            symbol,
        )
        from common.model_analysis import analyze_tmc_final_decision
        return analyze_tmc_final_decision(
            candidate=candidate,
            market_picture_context=market_picture_context,
            strategy_id=strategy_id,
            retries=retries,
            timeout=timeout,
        )

    try:
        from common.tmc_final_decision_prompts import (
            TMC_FINAL_DECISION_SYSTEM_PROMPT,
            build_tmc_final_decision_prompt,
        )
    except ImportError:
        _log.warning("[TMC_ROUTED] prompt module unavailable, using legacy path")
        from common.model_analysis import analyze_tmc_final_decision
        return analyze_tmc_final_decision(
            candidate=candidate,
            market_picture_context=market_picture_context,
            strategy_id=strategy_id,
            retries=retries,
            timeout=timeout,
        )

    user_prompt = build_tmc_final_decision_prompt(
        candidate=candidate,
        market_picture_context=market_picture_context,
        strategy_id=strategy_id,
    )

    messages = [
        {"role": "user", "content": user_prompt},
    ]

    try:
        legacy_result, trace = execute_routed_model(
            task_type="tmc_final_decision",
            messages=messages,
            system_prompt=TMC_FINAL_DECISION_SYSTEM_PROMPT,
            timeout=float(timeout),
            premium=premium,
            execution_mode=ExecutionMode.ONLINE_DISTRIBUTED.value,
            max_tokens=3000,
            temperature=0.0,
            metadata={"symbol": symbol, "strategy_id": strategy_id},
        )
    except Exception as exc:
        _log.warning(
            "[TMC_ROUTED] routing infrastructure unavailable for %s, "
            "falling back to legacy: %s",
            symbol, exc,
        )
        from common.model_analysis import analyze_tmc_final_decision
        return analyze_tmc_final_decision(
            candidate=candidate,
            market_picture_context=market_picture_context,
            strategy_id=strategy_id,
            retries=retries,
            timeout=timeout,
        )

    if legacy_result["status"] != "success":
        _log.warning(
            "[TMC_ROUTED] routed call failed for %s: %s — falling back to legacy",
            symbol, legacy_result.get("error"),
        )
        from common.model_analysis import analyze_tmc_final_decision
        return analyze_tmc_final_decision(
            candidate=candidate,
            market_picture_context=market_picture_context,
            strategy_id=strategy_id,
            retries=retries,
            timeout=timeout,
        )

    # Parse the routed content through the same pipeline as legacy.
    from common.json_repair import extract_and_repair_json
    content = legacy_result.get("content") or ""

    from common.model_sanitize import had_think_tags
    if had_think_tags(content):
        from common.model_sanitize import strip_think_tags
        content = strip_think_tags(content)

    parsed, parse_method = extract_and_repair_json(content)
    from common.model_analysis import _coerce_tmc_final_decision_output
    if parsed is not None:
        normalized = _coerce_tmc_final_decision_output(parsed)
    else:
        normalized = None

    # ── Retry-with-fix on parse failure (inline, no cascade) ─────
    # Previously this fell back to analyze_tmc_final_decision() which
    # called _model_transport() → execute_routed_model() AGAIN,
    # doubling model calls.  Now we do the retry-with-fix here and
    # return a fallback PASS on total failure — no cascading.
    if normalized is None and content:
        _log.warning(
            "[TMC_ROUTED] parse failed for %s, attempting retry-with-fix (no cascade)",
            symbol,
        )
        fix_messages = messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": (
                "Your previous response was not valid JSON. "
                "Please return ONLY the raw JSON object matching the schema "
                "from the system prompt. No commentary, no fences. "
                "Start with { and end with }."
            )},
        ]
        try:
            fix_result, fix_trace = execute_routed_model(
                task_type="tmc_final_decision_fix",
                messages=fix_messages,
                system_prompt=TMC_FINAL_DECISION_SYSTEM_PROMPT,
                timeout=float(timeout),
                premium=premium,
                execution_mode=ExecutionMode.ONLINE_DISTRIBUTED.value,
                max_tokens=3000,
                temperature=0.0,
                metadata={"symbol": symbol, "strategy_id": strategy_id, "fix_attempt": True},
            )
            if fix_result["status"] == "success":
                fix_content = fix_result.get("content") or ""
                if had_think_tags(fix_content):
                    fix_content = strip_think_tags(fix_content)
                parsed2, parse_method2 = extract_and_repair_json(fix_content)
                if parsed2 is not None:
                    normalized = _coerce_tmc_final_decision_output(parsed2)
                    if normalized is not None:
                        parse_method = f"retry_fix+{parse_method2 or 'unknown'}"
                        trace = fix_trace  # use the fix trace for provider info
                        _log.info(
                            "[TMC_ROUTED] retry-with-fix SUCCEEDED symbol=%s method=%s",
                            symbol, parse_method,
                        )
        except Exception as fix_exc:
            _log.warning(
                "[TMC_ROUTED] retry-with-fix failed for %s: %s",
                symbol, fix_exc,
            )

    if normalized is None:
        _log.error(
            "[TMC_ROUTED] ALL PARSE FAILED for %s — returning fallback PASS",
            symbol,
        )
        from common.model_analysis import _build_fallback_tmc_decision
        fallback = _build_fallback_tmc_decision(
            candidate,
            reason="Routed JSON extraction + repair + retry-with-fix all failed",
            raw_text=content,
        )
        fallback["_routed"] = True
        fallback["_request_id"] = trace.request_id
        fallback["_provider"] = trace.selected_provider
        return fallback

    from datetime import datetime, timezone
    normalized["timestamp"] = datetime.now(timezone.utc).isoformat()
    if parse_method and parse_method != "direct":
        normalized.setdefault("_parse_method", parse_method)
    normalized["_routed"] = True
    normalized["_request_id"] = trace.request_id
    normalized["_provider"] = trace.selected_provider

    _log.info(
        "[TMC_ROUTED] OK symbol=%s decision=%s conviction=%s provider=%s",
        symbol,
        normalized.get("decision"),
        normalized.get("conviction"),
        trace.selected_provider,
    )

    return normalized


# ---------------------------------------------------------------------------
# 5. Market Intelligence model interpretation — routed wrapper
# ---------------------------------------------------------------------------

def routed_model_interpretation(
    _http_client: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Sync wrapper for MI runner's model interpretation via routing.

    Matches the ``model_request_fn(http_client, payload)`` signature
    expected by ``MarketIntelligenceDeps``.  The *_http_client* arg is
    accepted for signature compatibility but ignored — routing handles
    transport.

    Returns an OpenAI-compatible response dict with ``choices`` so
    ``_stage_run_model_interpretation`` can parse it unchanged.

    Raises on routing failure so the MI runner can degrade gracefully.
    """
    messages = payload.get("messages", [])
    system_prompt: str | None = None
    user_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg.get("role") == "system":
            system_prompt = msg.get("content")
        else:
            user_messages.append(msg)

    temperature = payload.get("temperature")
    model_name = payload.get("model")

    legacy_result, trace = execute_routed_model(
        task_type="market_picture_interpretation",
        messages=user_messages,
        system_prompt=system_prompt,
        model_name=model_name,
        temperature=temperature,
        metadata={"source": "market_intelligence_runner"},
    )

    if legacy_result["status"] != "success":
        raise RuntimeError(
            f"Routed model interpretation failed: {legacy_result.get('error')}"
        )

    # Re-wrap into OpenAI-compatible shape for MI runner parsing.
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": legacy_result.get("content", ""),
                },
            },
        ],
        "_routed": True,
        "_request_id": trace.request_id,
        "_provider": trace.selected_provider,
    }


async def async_routed_model_interpretation(
    _http_client: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Async wrapper around ``routed_model_interpretation``.

    Matches the async ``model_request_fn(http_client, payload)`` signature
    expected by ``MarketIntelligenceDeps`` when the MI runner ``await``s
    the call.  Offloads the sync routing call to a thread executor.
    """
    import asyncio

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: routed_model_interpretation(_http_client, payload),
    )


async def adaptive_routed_model_interpretation(
    _http_client: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Per-request routing-enabled wrapper for MI runner (Step 14).

    Checks ``routing_enabled`` on every call instead of baking the
    decision at startup.  If routing is enabled, delegates to
    ``async_routed_model_interpretation``.  If disabled, falls back
    to the legacy ``async_model_request`` path.

    This allows the MI runner to pick up routing-enabled changes at
    runtime without requiring a backend restart.

    Matches the ``model_request_fn(http_client, payload)`` signature.
    """
    if _routing_is_enabled():
        return await async_routed_model_interpretation(_http_client, payload)

    # Legacy fallback — import here to avoid circular deps.
    from app.services.model_router import async_model_request
    return await async_model_request(_http_client, payload)
