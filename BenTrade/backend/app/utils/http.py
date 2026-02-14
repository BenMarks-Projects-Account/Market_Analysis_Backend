from typing import Any

import httpx


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
) -> dict[str, Any]:
    try:
        response = await client.request(method, url, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise UpstreamError(
            f"Network error calling upstream: {url}",
            details={"url": url, "exception": str(exc)},
        ) from exc

    if response.status_code >= 400:
        raise UpstreamError(
            f"Upstream returned HTTP {response.status_code}",
            details={
                "url": str(response.url),
                "status_code": response.status_code,
                "body": response.text,
            },
        )

    try:
        return response.json()
    except ValueError as exc:
        raise UpstreamError(
            "Upstream returned invalid JSON",
            details={"url": str(response.url), "body": response.text[:1000]},
        ) from exc
