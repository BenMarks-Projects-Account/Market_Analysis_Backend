import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Transient HTTP status codes eligible for automatic retry
_TRANSIENT_STATUSES = {502, 503, 504}


class UpstreamError(Exception):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    data: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    retries: int = 2,
    backoff_ms: int = 300,
) -> dict[str, Any]:
    """Make an HTTP request and return parsed JSON.

    Automatic retry logic:
      - Retries up to *retries* times on transient errors (502/503/504)
        or network-level exceptions (timeouts, connection resets).
      - Waits *backoff_ms* milliseconds between retries (doubles each attempt).
      - Non-transient HTTP errors (401/403/404/429/500 etc.) are raised immediately.
    """
    last_exc: Exception | None = None

    for attempt in range(1 + retries):
        # Log outgoing request (redact auth headers)
        if json_body is not None:
            logger.info(
                "[http] %s %s json=%s",
                method, url, json_body,
            )
        elif data is not None:
            logger.info(
                "[http] %s %s data=%s",
                method, url, data,
            )
        else:
            logger.info("[http] %s %s params=%s", method, url, params)

        # === DIAGNOSTIC: Final HTTP callsite ===
        print(f"HTTP REQUEST: {method} {url}")
        print(f"HTTP BODY: {json_body or data}")

        try:
            response = await client.request(
                method, url, params=params, headers=headers,
                data=data, json=json_body,
            )
        except httpx.HTTPError as exc:
            last_exc = UpstreamError(
                f"Network error calling upstream: {url}",
                details={"url": url, "exception": str(exc)},
            )
            last_exc.__cause__ = exc
            if attempt < retries:
                wait = (backoff_ms / 1000) * (2 ** attempt)
                logger.warning(
                    "[http] transient network error url=%s attempt=%d/%d wait=%.1fs err=%s",
                    url, attempt + 1, 1 + retries, wait, exc,
                )
                await asyncio.sleep(wait)
                continue
            raise last_exc from exc

        # Log response status and body
        logger.info(
            "[http] %s %s → %d body=%s",
            method, url, response.status_code,
            (response.text or "")[:500],
        )

        # ── Non-2xx responses ──────────────────────────────────
        if response.status_code >= 400:
            logger.warning(
                "[http] upstream error %d url=%s full_body=%s",
                response.status_code, url,
                response.text[:2000] if response.text else "",
            )
            err = UpstreamError(
                f"Upstream returned HTTP {response.status_code}",
                details={
                    "url": str(response.url),
                    "status_code": response.status_code,
                    "body": response.text[:2000] if response.text else "",
                },
            )
            # Retry only on transient server errors
            if response.status_code in _TRANSIENT_STATUSES and attempt < retries:
                wait = (backoff_ms / 1000) * (2 ** attempt)
                logger.warning(
                    "[http] transient %d url=%s attempt=%d/%d wait=%.1fs body=%s",
                    response.status_code, url, attempt + 1, 1 + retries, wait,
                    (response.text or "")[:200],
                )
                last_exc = err
                await asyncio.sleep(wait)
                continue
            raise err

        # ── Parse JSON ─────────────────────────────────────────
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(
                "Upstream returned invalid JSON",
                details={"url": str(response.url), "body": response.text[:1000]},
            ) from exc

    # Should never reach here, but safety net
    raise last_exc or UpstreamError(f"request_json exhausted retries for {url}")
