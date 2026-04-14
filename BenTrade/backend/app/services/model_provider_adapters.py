"""Concrete provider adapters for localhost_llm, network_model_machine,
and bedrock_titan_nova_pro.

Each adapter wraps an existing HTTP call path behind the
``ModelProviderBase`` interface so the future router can call any
provider uniformly.

Adapters live here — provider-specific parsing stays inside the adapter,
never in the router.

Busy-state detection (Step 3):
    LM Studio (used by localhost and model_machine) exposes an
    OpenAI-compatible ``GET /v1/models`` endpoint.  We use that as a
    lightweight reachability probe (short timeout, no inference payload).

    Busy detection signals:
    • HTTP 429 (rate-limited / queue full) → BUSY
    • HTTP 503 (service unavailable / overloaded) → BUSY
    • Slow probe response (> threshold) → DEGRADED (heuristic)
    • Connection refused / timeout → UNAVAILABLE
    • Other HTTP errors → FAILED

    LM Studio does not currently expose an explicit "am I processing a
    request right now" API.  The 429/503 heuristic is the best available
    signal without adding server-side instrumentation.

Bedrock integration (Step 6):
    BedrockTitanNovaProProvider uses the AWS SDK (boto3) Converse API
    to invoke Amazon Nova Pro.  Request/response translation is fully
    encapsulated here — no Bedrock-specific payload shapes leak to callers.

    Probe is config-level only: validates credentials + client creation
    but does NOT make a live inference call.  Bedrock has no free
    lightweight health endpoint, so true runtime health is not probed.
"""

from __future__ import annotations

import logging
import time
from typing import Any

# -- 429 backoff constants for inference calls --
_RATE_LIMIT_MAX_RETRIES: int = 3
_RATE_LIMIT_BACKOFF_BASE: float = 2.0   # seconds (2, 4, 8)
_RATE_LIMIT_BACKOFF_CAP: float = 30.0   # max delay per attempt

import requests as _requests

from app.config import get_settings
from app.services.model_provider_base import (
    PROBE_TIMEOUT_SECONDS,
    ModelProviderBase,
    ProbeResult,
    ProviderResult,
    extract_content_from_openai_response,
    get_provider_endpoint,
)
from app.services.model_routing_contract import (
    ExecutionRequest,
    ExecutionStatus,
    Provider,
    ProviderState,
)

logger = logging.getLogger("bentrade.provider_adapters")

_settings = get_settings()
_DEFAULT_TIMEOUT: float = _settings.MODEL_TIMEOUT_SECONDS

# Probe response time above this threshold (ms) flags DEGRADED.
# Now config-driven; falls back to 2000.0 if config unavailable.
_PROBE_DEGRADED_THRESHOLD_MS: float = 2000.0


def _get_probe_degraded_threshold() -> float:
    """Return the effective probe degraded threshold from routing config."""
    try:
        from app.services.model_routing_config import get_routing_config
        return get_routing_config().probe_degraded_threshold_ms
    except Exception:
        return _PROBE_DEGRADED_THRESHOLD_MS


def _get_probe_timeout() -> float:
    """Return the effective probe timeout from routing config."""
    try:
        from app.services.model_routing_config import get_routing_config
        return get_routing_config().probe_timeout_seconds
    except Exception:
        return PROBE_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Shared LM Studio health probe (localhost + model machine both use it)
# ---------------------------------------------------------------------------

def _lmstudio_probe(
    endpoint: str,
    provider_id: str,
    *,
    timeout: float | None = None,
) -> ProbeResult:
    """Probe an LM Studio instance via ``GET /v1/models``.

    Converts the chat-completions endpoint to the models endpoint
    by replacing the path suffix.

    State mapping:
        HTTP 200               → AVAILABLE (or DEGRADED if slow)
        HTTP 429               → BUSY
        HTTP 503               → BUSY
        HTTP 4xx/5xx (other)   → FAILED
        ConnectionError        → UNAVAILABLE
        ReadTimeout            → UNAVAILABLE (probe timed out)
        Other exception        → FAILED

    Input fields: endpoint (chat completions URL)
    Derived: models_url = endpoint with path replaced by /v1/models
    """
    if timeout is None:
        timeout = _get_probe_timeout()
    degraded_threshold = _get_probe_degraded_threshold()
    # Derive /v1/models URL from the chat completions endpoint.
    # e.g. http://localhost:1234/v1/chat/completions → http://localhost:1234/v1/models
    models_url = endpoint.rsplit("/v1/", 1)[0] + "/v1/models"

    t0 = time.perf_counter()
    try:
        resp = _requests.get(models_url, timeout=timeout)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        # Busy signals: 429 (rate-limited) or 503 (overloaded)
        if resp.status_code in (429, 503):
            reason = f"HTTP {resp.status_code} — server busy or overloaded"
            logger.info("[%s] probe %s → BUSY (%s)", provider_id, models_url, reason)
            return ProbeResult(
                provider=provider_id,
                configured=True,
                state=ProviderState.BUSY.value,
                probe_success=True,
                status_reason=reason,
                raw_probe_data={"status_code": resp.status_code},
                timing_ms=elapsed_ms,
            )

        if resp.status_code >= 400:
            reason = f"HTTP {resp.status_code}"
            logger.warning("[%s] probe %s → FAILED (%s)", provider_id, models_url, reason)
            return ProbeResult(
                provider=provider_id,
                configured=True,
                state=ProviderState.FAILED.value,
                probe_success=True,
                status_reason=reason,
                raw_probe_data={"status_code": resp.status_code},
                timing_ms=elapsed_ms,
            )

        # 2xx — reachable.  Check for slow response → DEGRADED.
        raw_data: Any = None
        try:
            raw_data = resp.json()
        except Exception:
            raw_data = {"body_length": len(resp.content)}

        if elapsed_ms > degraded_threshold:
            reason = f"slow probe response ({elapsed_ms:.0f}ms > {degraded_threshold:.0f}ms threshold)"
            logger.info("[%s] probe %s → DEGRADED (%s)", provider_id, models_url, reason)
            return ProbeResult(
                provider=provider_id,
                configured=True,
                state=ProviderState.DEGRADED.value,
                probe_success=True,
                status_reason=reason,
                raw_probe_data=raw_data,
                timing_ms=elapsed_ms,
            )

        logger.debug("[%s] probe %s → AVAILABLE (%.0fms)", provider_id, models_url, elapsed_ms)
        return ProbeResult(
            provider=provider_id,
            configured=True,
            state=ProviderState.AVAILABLE.value,
            probe_success=True,
            status_reason="healthy",
            raw_probe_data=raw_data,
            timing_ms=elapsed_ms,
        )

    except _requests.ConnectionError as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        reason = f"connection error: {exc}"
        logger.info("[%s] probe %s → UNAVAILABLE (%s)", provider_id, models_url, reason)
        return ProbeResult(
            provider=provider_id,
            configured=True,
            state=ProviderState.UNAVAILABLE.value,
            probe_success=False,
            status_reason=reason,
            timing_ms=elapsed_ms,
        )

    except _requests.ReadTimeout:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        reason = f"probe timed out after {timeout}s"
        logger.info("[%s] probe %s → UNAVAILABLE (%s)", provider_id, models_url, reason)
        return ProbeResult(
            provider=provider_id,
            configured=True,
            state=ProviderState.UNAVAILABLE.value,
            probe_success=False,
            status_reason=reason,
            timing_ms=elapsed_ms,
        )

    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        reason = f"probe exception: {exc}"
        logger.warning("[%s] probe %s → FAILED (%s)", provider_id, models_url, reason)
        return ProbeResult(
            provider=provider_id,
            configured=True,
            state=ProviderState.FAILED.value,
            probe_success=False,
            status_reason=reason,
            timing_ms=elapsed_ms,
        )


# ---------------------------------------------------------------------------
# Shared OpenAI-compatible call logic (localhost + model machine both use it)
# ---------------------------------------------------------------------------

def _openai_compat_call(
    endpoint: str,
    request: ExecutionRequest,
    *,
    timeout: float,
    provider_id: str,
) -> ProviderResult:
    """POST to an OpenAI-compatible chat completions endpoint.

    Replicates the proven logic from model_router.model_request()
    (stream=False, status checks, content extraction) but returns a
    ``ProviderResult`` instead of a raw dict.
    """
    messages: list[dict[str, Any]] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    if request.prompt:
        messages.extend(request.prompt)

    body: dict[str, Any] = {"messages": messages, "stream": False}
    if request.model_name:
        body["model"] = request.model_name

    # Apply generation parameters from routing_overrides
    overrides = request.routing_overrides or {}
    if overrides.get("max_tokens") is not None:
        body["max_tokens"] = overrides["max_tokens"]
    if overrides.get("temperature") is not None:
        body["temperature"] = overrides["temperature"]

    t0 = time.perf_counter()

    for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
        try:
            logger.info(
                "[%s] POST %s (timeout=%ds, task=%s, max_tokens=%s, temperature=%s)",
                provider_id, endpoint, timeout,
                request.task_type, body.get("max_tokens"), body.get("temperature"),
            )
            resp = _requests.post(endpoint, json=body, timeout=timeout)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            logger.info(
                "[%s] HTTP %d (%d bytes, %.1fms)",
                provider_id, resp.status_code, len(resp.content), elapsed_ms,
            )

            # Diagnostic: capture response body on non-2xx errors before raise_for_status
            if resp.status_code >= 400:
                # Summarize the request: message count, total chars, model, params
                msg_summary = f"{len(messages)} messages, ~{sum(len(m.get('content','')) for m in messages)} chars"
                logger.warning(
                    "[%s] HTTP %d response body (task=%s, request=%s, model=%s): %s",
                    provider_id, resp.status_code, request.task_type,
                    msg_summary, body.get("model", "none"),
                    resp.text[:2000],
                )

            # 429 rate-limit → backoff and retry
            if resp.status_code == 429 and attempt < _RATE_LIMIT_MAX_RETRIES:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = min(float(retry_after), _RATE_LIMIT_BACKOFF_CAP)
                    except (ValueError, TypeError):
                        delay = _RATE_LIMIT_BACKOFF_BASE ** attempt
                else:
                    delay = _RATE_LIMIT_BACKOFF_BASE ** attempt
                delay = min(delay, _RATE_LIMIT_BACKOFF_CAP)
                logger.info(
                    "event=llm_rate_limited provider=%s attempt=%d delay=%.1fs",
                    provider_id, attempt + 1, delay,
                )
                time.sleep(delay)
                continue

            resp.raise_for_status()
            data = resp.json()

            content = extract_content_from_openai_response(data)

            return ProviderResult(
                provider=provider_id,
                success=True,
                execution_status=ExecutionStatus.SUCCESS.value,
                raw_response=data,
                content=content,
                timing_ms=elapsed_ms,
                provider_state_observed=ProviderState.AVAILABLE.value,
            )
        except _requests.ReadTimeout:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.error("[%s] TIMED OUT after %.0fms", provider_id, elapsed_ms)
            return ProviderResult(
                provider=provider_id,
                success=False,
                execution_status=ExecutionStatus.TIMEOUT.value,
                error_code="timeout",
                error_message=f"Read timeout after {timeout}s",
                timing_ms=elapsed_ms,
                provider_state_observed=ProviderState.DEGRADED.value,
            )
        except _requests.ConnectionError as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.error("[%s] connection error: %s", provider_id, exc)
            return ProviderResult(
                provider=provider_id,
                success=False,
                execution_status=ExecutionStatus.FAILED.value,
                error_code="connection_error",
                error_message=str(exc),
                timing_ms=elapsed_ms,
                provider_state_observed=ProviderState.UNAVAILABLE.value,
            )
        except _requests.RequestException as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            # If this was a 429 raise_for_status on the final attempt, report as rate_limited
            status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
            if status_code == 429:
                logger.warning(
                    "event=llm_rate_limited_exhausted provider=%s attempts=%d",
                    provider_id, attempt + 1,
                )
                return ProviderResult(
                    provider=provider_id,
                    success=False,
                    execution_status=ExecutionStatus.FAILED.value,
                    error_code="rate_limited",
                    error_message=f"429 after {attempt + 1} attempts",
                    timing_ms=elapsed_ms,
                    provider_state_observed=ProviderState.BUSY.value,
                )
            logger.warning("[%s] request error: %s", provider_id, exc)
            return ProviderResult(
                provider=provider_id,
                success=False,
                execution_status=ExecutionStatus.FAILED.value,
                error_code="request_error",
                error_message=str(exc),
                timing_ms=elapsed_ms,
                provider_state_observed=ProviderState.FAILED.value,
            )

    # Final 429 after all retries exhausted (reached via continue path)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.warning(
        "event=llm_rate_limited_exhausted provider=%s attempts=%d",
        provider_id, _RATE_LIMIT_MAX_RETRIES + 1,
    )
    return ProviderResult(
        provider=provider_id,
        success=False,
        execution_status=ExecutionStatus.FAILED.value,
        error_code="rate_limited",
        error_message=f"429 after {_RATE_LIMIT_MAX_RETRIES + 1} attempts",
        timing_ms=elapsed_ms,
        provider_state_observed=ProviderState.BUSY.value,
    )


# ---------------------------------------------------------------------------
# 1. Localhost LLM adapter
# ---------------------------------------------------------------------------

class LocalhostLLMProvider(ModelProviderBase):
    """Adapter for the local LM Studio instance on localhost:1234."""

    @property
    def provider_id(self) -> str:
        return Provider.LOCALHOST_LLM.value

    @property
    def is_configured(self) -> bool:
        return get_provider_endpoint(self.provider_id) is not None

    def probe(self) -> ProbeResult:
        endpoint = get_provider_endpoint(self.provider_id)
        if not endpoint:
            return ProbeResult(
                provider=self.provider_id,
                configured=False,
                state=ProviderState.UNAVAILABLE.value,
                probe_success=True,
                status_reason="no endpoint configured",
            )
        return _lmstudio_probe(endpoint, self.provider_id)

    def execute(self, request: ExecutionRequest, *, timeout: float | None = None) -> ProviderResult:
        endpoint = get_provider_endpoint(self.provider_id)
        if not endpoint:
            return ProviderResult(
                provider=self.provider_id,
                success=False,
                execution_status=ExecutionStatus.FAILED.value,
                error_code="no_endpoint",
                error_message="localhost_llm has no endpoint configured",
                provider_state_observed=ProviderState.UNAVAILABLE.value,
            )
        return _openai_compat_call(
            endpoint, request,
            timeout=timeout or _DEFAULT_TIMEOUT,
            provider_id=self.provider_id,
        )


# ---------------------------------------------------------------------------
# 2. Network model machine adapter
# ---------------------------------------------------------------------------

class NetworkModelMachineProvider(ModelProviderBase):
    """Adapter for the network model machine (192.168.1.143:1234)."""

    @property
    def provider_id(self) -> str:
        return Provider.NETWORK_MODEL_MACHINE.value

    @property
    def is_configured(self) -> bool:
        return get_provider_endpoint(self.provider_id) is not None

    def probe(self) -> ProbeResult:
        endpoint = get_provider_endpoint(self.provider_id)
        if not endpoint:
            return ProbeResult(
                provider=self.provider_id,
                configured=False,
                state=ProviderState.UNAVAILABLE.value,
                probe_success=True,
                status_reason="no endpoint configured",
            )
        return _lmstudio_probe(endpoint, self.provider_id)

    def execute(self, request: ExecutionRequest, *, timeout: float | None = None) -> ProviderResult:
        endpoint = get_provider_endpoint(self.provider_id)
        if not endpoint:
            return ProviderResult(
                provider=self.provider_id,
                success=False,
                execution_status=ExecutionStatus.FAILED.value,
                error_code="no_endpoint",
                error_message="network_model_machine has no endpoint configured",
                provider_state_observed=ProviderState.UNAVAILABLE.value,
            )
        return _openai_compat_call(
            endpoint, request,
            timeout=timeout or _DEFAULT_TIMEOUT,
            provider_id=self.provider_id,
        )


# ---------------------------------------------------------------------------
# 3. Bedrock Titan Nova Pro adapter
# ---------------------------------------------------------------------------

def _build_bedrock_messages(
    request: ExecutionRequest,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Translate ExecutionRequest into Bedrock Converse API format.

    Returns (messages, system_prompts) where:
        messages      – list of {"role": ..., "content": [{"text": ...}]}
        system_prompts – list of {"text": ...} (may be empty)

    Input fields: request.prompt (list of {"role":..,"content":..}),
                  request.system_prompt (str or None)
    """
    system_prompts: list[dict[str, Any]] = []
    if request.system_prompt:
        system_prompts.append({"text": request.system_prompt})

    messages: list[dict[str, Any]] = []
    if request.prompt:
        for msg in request.prompt:
            role = msg.get("role", "user")
            content_text = msg.get("content", "")
            # Bedrock Converse API expects role to be "user" or "assistant".
            # Map "system" role messages into the system prompt list.
            if role == "system":
                system_prompts.append({"text": content_text})
                continue
            messages.append({
                "role": role,
                "content": [{"text": content_text}],
            })

    # Bedrock requires at least one user message.
    if not messages:
        messages.append({
            "role": "user",
            "content": [{"text": ""}],
        })

    return messages, system_prompts


def _extract_content_from_converse_response(response: dict[str, Any]) -> str | None:
    """Extract text content from a Bedrock Converse API response.

    Input fields: response["output"]["message"]["content"][0]["text"]
    Returns None if the path is missing or malformed.
    """
    output = response.get("output")
    if not isinstance(output, dict):
        return None
    message = output.get("message")
    if not isinstance(message, dict):
        return None
    content_blocks = message.get("content")
    if not content_blocks or not isinstance(content_blocks, list):
        return None
    first = content_blocks[0]
    if not isinstance(first, dict):
        return None
    return first.get("text")


class BedrockTitanNovaProProvider(ModelProviderBase):
    """Adapter for Amazon Bedrock Nova Pro via the Converse API.

    Configuration (from Settings):
        BEDROCK_ENABLED        – Master toggle (default True).
        BEDROCK_REGION         – AWS region for the bedrock-runtime client.
        BEDROCK_MODEL_ID       – Model ID / inference profile ARN.
        BEDROCK_TIMEOUT_SECONDS – Read timeout for the Converse call.

    Credentials: Uses standard boto3 credential chain (env vars,
    ~/.aws/credentials, IAM role, etc.).  Never hardcoded.

    Probe behaviour (Step 6):
        Config-level only — checks that BEDROCK_ENABLED is True and that
        boto3 can create a bedrock-runtime client with valid credentials.
        Does NOT make a live inference call.  Metadata includes
        "probe_type": "config_only" so callers know the limitation.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._client_error: str | None = None
        self._client_initialised = False

    def _ensure_client(self) -> Any:
        """Lazily create the boto3 bedrock-runtime client.

        Returns the client on success, None on failure.
        Sets self._client_error with diagnostic string on failure.
        """
        if self._client_initialised:
            return self._client

        import boto3
        settings = get_settings()
        try:
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=settings.BEDROCK_REGION,
            )
            self._client_error = None
        except Exception as exc:
            self._client = None
            self._client_error = f"boto3 client creation failed: {exc}"
            logger.warning("[bedrock] %s", self._client_error)

        self._client_initialised = True
        return self._client

    @property
    def provider_id(self) -> str:
        return Provider.BEDROCK_TITAN_NOVA_PRO.value

    @property
    def is_configured(self) -> bool:
        """True when BEDROCK_ENABLED and a boto3 client can be created."""
        settings = get_settings()
        if not settings.BEDROCK_ENABLED:
            return False
        return self._ensure_client() is not None

    def probe(self) -> ProbeResult:
        """Config-level readiness probe.

        Does NOT make a live inference call — Bedrock has no free
        lightweight health endpoint.  Instead validates:
            1. BEDROCK_ENABLED is True
            2. boto3 client creation succeeds (implies credentials present)

        Metadata includes "probe_type": "config_only" so consumers
        know this is not a true runtime health check.
        """
        settings = get_settings()
        if not settings.BEDROCK_ENABLED:
            return ProbeResult(
                provider=self.provider_id,
                configured=False,
                state=ProviderState.UNAVAILABLE.value,
                probe_success=True,
                status_reason="BEDROCK_ENABLED=false",
                metadata={"probe_type": "config_only"},
            )

        client = self._ensure_client()
        if client is None:
            return ProbeResult(
                provider=self.provider_id,
                configured=False,
                state=ProviderState.UNAVAILABLE.value,
                probe_success=True,
                status_reason=self._client_error or "boto3 client unavailable",
                metadata={"probe_type": "config_only"},
            )

        return ProbeResult(
            provider=self.provider_id,
            configured=True,
            state=ProviderState.AVAILABLE.value,
            probe_success=True,
            status_reason="configured (config-level probe only — no live inference check)",
            metadata={
                "probe_type": "config_only",
                "region": settings.BEDROCK_REGION,
                "model_id": settings.BEDROCK_MODEL_ID,
            },
        )

    def execute(self, request: ExecutionRequest, *, timeout: float | None = None) -> ProviderResult:
        """Invoke Bedrock Converse API for the given request.

        Translates ExecutionRequest → Converse format, calls the API,
        and normalizes the response into ProviderResult.
        """
        settings = get_settings()
        if not settings.BEDROCK_ENABLED:
            return ProviderResult(
                provider=self.provider_id,
                success=False,
                execution_status=ExecutionStatus.SKIPPED.value,
                error_code="not_configured",
                error_message="Bedrock is disabled (BEDROCK_ENABLED=false)",
                provider_state_observed=ProviderState.UNAVAILABLE.value,
            )

        client = self._ensure_client()
        if client is None:
            return ProviderResult(
                provider=self.provider_id,
                success=False,
                execution_status=ExecutionStatus.FAILED.value,
                error_code="not_configured",
                error_message=self._client_error or "boto3 client unavailable",
                provider_state_observed=ProviderState.UNAVAILABLE.value,
            )

        messages, system_prompts = _build_bedrock_messages(request)
        model_id = request.model_name or settings.BEDROCK_MODEL_ID
        effective_timeout = timeout or settings.BEDROCK_TIMEOUT_SECONDS

        t0 = time.perf_counter()

        for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
            try:
                import botocore.config as _botocore_config

                # Per-call read timeout via botocore config.
                call_config = _botocore_config.Config(
                    read_timeout=int(effective_timeout),
                    retries={"max_attempts": 0},
                )
                scoped_client = client.meta.client_factory(
                    "bedrock-runtime",
                    config=call_config,
                ) if hasattr(client, "meta") and hasattr(client.meta, "client_factory") else client

                kwargs: dict[str, Any] = {
                    "modelId": model_id,
                    "messages": messages,
                }
                if system_prompts:
                    kwargs["system"] = system_prompts

                logger.info(
                    "[bedrock] Converse call → model=%s, messages=%d, system=%d, timeout=%ds",
                    model_id, len(messages), len(system_prompts), int(effective_timeout),
                )

                response = client.converse(**kwargs)
                elapsed_ms = (time.perf_counter() - t0) * 1000

                # Extract content from response.
                content = _extract_content_from_converse_response(response)
                stop_reason = response.get("stopReason", "unknown")
                usage = response.get("usage", {})

                logger.info(
                    "[bedrock] Converse success (%.0fms, stop=%s, input_tokens=%s, output_tokens=%s)",
                    elapsed_ms,
                    stop_reason,
                    usage.get("inputTokens", "?"),
                    usage.get("outputTokens", "?"),
                )

                return ProviderResult(
                    provider=self.provider_id,
                    success=True,
                    execution_status=ExecutionStatus.SUCCESS.value,
                    raw_response=response,
                    content=content,
                    timing_ms=elapsed_ms,
                    provider_state_observed=ProviderState.AVAILABLE.value,
                    metadata={
                        "model_id": model_id,
                        "stop_reason": stop_reason,
                        "usage": usage,
                        "region": settings.BEDROCK_REGION,
                    },
                )

            except Exception as exc:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                exc_type = type(exc).__name__

                # Classify error for retryability and state observation.
                error_code, exec_status, observed_state = _classify_bedrock_error(exc)

                # Throttling → backoff and retry
                if error_code == "throttled" and attempt < _RATE_LIMIT_MAX_RETRIES:
                    delay = min(_RATE_LIMIT_BACKOFF_BASE ** attempt, _RATE_LIMIT_BACKOFF_CAP)
                    logger.info(
                        "event=llm_rate_limited provider=bedrock attempt=%d delay=%.1fs",
                        attempt + 1, delay,
                    )
                    time.sleep(delay)
                    continue

                logger.error(
                    "[bedrock] Converse failed: %s: %s (%.0fms)",
                    exc_type, exc, elapsed_ms,
                )

                return ProviderResult(
                    provider=self.provider_id,
                    success=False,
                    execution_status=exec_status,
                    error_code=error_code,
                    error_message=f"{exc_type}: {exc}",
                    timing_ms=elapsed_ms,
                    provider_state_observed=observed_state,
                    metadata={
                        "model_id": model_id,
                        "region": settings.BEDROCK_REGION,
                        "exception_type": exc_type,
                    },
                )

        # Throttled on all attempts
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.warning(
            "event=llm_rate_limited_exhausted provider=bedrock attempts=%d",
            _RATE_LIMIT_MAX_RETRIES + 1,
        )
        return ProviderResult(
            provider=self.provider_id,
            success=False,
            execution_status=ExecutionStatus.FAILED.value,
            error_code="rate_limited",
            error_message=f"ThrottlingException after {_RATE_LIMIT_MAX_RETRIES + 1} attempts",
            timing_ms=elapsed_ms,
            provider_state_observed=ProviderState.BUSY.value,
            metadata={
                "model_id": model_id,
                "region": settings.BEDROCK_REGION,
            },
        )

def _classify_bedrock_error(
    exc: Exception,
) -> tuple[str, str, str]:
    """Classify a Bedrock exception into (error_code, execution_status, provider_state).

    Handles botocore ClientError codes and common connection failures.
    """
    exc_type = type(exc).__name__

    # botocore ClientError carries an error code in the response.
    if exc_type == "ClientError" and hasattr(exc, "response"):
        error_info = exc.response.get("Error", {})  # type: ignore[attr-defined]
        code = error_info.get("Code", "UnknownError")

        if code == "ThrottlingException":
            return "throttled", ExecutionStatus.FAILED.value, ProviderState.BUSY.value
        if code == "ServiceUnavailableException":
            return "service_unavailable", ExecutionStatus.FAILED.value, ProviderState.UNAVAILABLE.value
        if code == "ModelTimeoutException":
            return "timeout", ExecutionStatus.TIMEOUT.value, ProviderState.DEGRADED.value
        if code in ("AccessDeniedException", "UnauthorizedAccess"):
            return "access_denied", ExecutionStatus.FAILED.value, ProviderState.FAILED.value
        if code == "ValidationException":
            return "validation_error", ExecutionStatus.FAILED.value, ProviderState.AVAILABLE.value
        if code == "ModelNotReadyException":
            return "model_not_ready", ExecutionStatus.FAILED.value, ProviderState.UNAVAILABLE.value
        return f"client_error_{code}", ExecutionStatus.FAILED.value, ProviderState.FAILED.value

    # Connection / timeout errors.
    if "ReadTimeoutError" in exc_type or "ConnectTimeoutError" in exc_type:
        return "timeout", ExecutionStatus.TIMEOUT.value, ProviderState.DEGRADED.value
    if "EndpointConnectionError" in exc_type or "ConnectionError" in exc_type:
        return "connection_error", ExecutionStatus.FAILED.value, ProviderState.UNAVAILABLE.value

    return "request_error", ExecutionStatus.FAILED.value, ProviderState.FAILED.value
