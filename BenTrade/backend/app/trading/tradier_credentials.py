"""
Tradier Credential Resolver
============================

Single helper that resolves Tradier API credentials based on purpose and
account mode.  All market-data calls always use LIVE credentials.
Execution calls route to PAPER or LIVE based on the Trade Ticket toggle.

Resolver rules:
  DATA      → always LIVE credentials (accountMode ignored)
  EXECUTION → PAPER creds when accountMode == "paper" (default)
              LIVE  creds when accountMode == "live"

Credential resolution does NOT gate execution — the single
TRADIER_EXECUTION_ENABLED flag in config.py controls dry-run vs real.

Inputs:
  - TRADIER_API_KEY_LIVE, TRADIER_ACCOUNT_ID_LIVE, TRADIER_ENV_LIVE
  - TRADIER_API_KEY_PAPER, TRADIER_ACCOUNT_ID_PAPER, TRADIER_ENV_PAPER

Output:
  TradierCredentials(api_key, account_id, env, base_url, mode_label)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

Purpose = Literal["DATA", "EXECUTION"]
AccountMode = Literal["paper", "live"]


@dataclass(frozen=True)
class TradierCredentials:
    api_key: str
    account_id: str
    env: str  # "live" | "sandbox" | "paper"
    base_url: str
    mode_label: str  # human-readable: "LIVE-DATA", "PAPER-EXEC", "LIVE-EXEC"


def get_tradier_base_url(env: str) -> str:
    """Derive Tradier base URL from the env value.

    Formula: env in ("sandbox", "paper") → sandbox URL, else → production URL.
    """
    if env.lower() in ("sandbox", "paper"):
        return "https://sandbox.tradier.com/v1"
    return "https://api.tradier.com/v1"


def resolve_tradier_credentials(
    *,
    purpose: Purpose,
    account_mode: AccountMode | None = None,
    # Credential sets — injected from Settings so this function is pure
    live_api_key: str,
    live_account_id: str,
    live_env: str,
    paper_api_key: str,
    paper_account_id: str,
    paper_env: str,
) -> TradierCredentials:
    """Resolve the correct Tradier credential set.

    Credential resolution routes by account_mode only.
    Execution gating (dry-run vs real) is handled by the caller
    using TRADIER_EXECUTION_ENABLED — this function never blocks.
    """

    # ── DATA calls always use LIVE ────────────────────────────
    if purpose == "DATA":
        return TradierCredentials(
            api_key=live_api_key,
            account_id=live_account_id,
            env=live_env,
            base_url=get_tradier_base_url(live_env),
            mode_label="LIVE-DATA",
        )

    # ── EXECUTION calls route by accountMode ──────────────────
    mode = (account_mode or "paper").lower()

    if mode == "live":
        return TradierCredentials(
            api_key=live_api_key,
            account_id=live_account_id,
            env=live_env,
            base_url=get_tradier_base_url(live_env),
            mode_label="LIVE-EXEC",
        )

    # Default / "paper"
    return TradierCredentials(
        api_key=paper_api_key,
        account_id=paper_account_id,
        env=paper_env,
        base_url=get_tradier_base_url(paper_env),
        mode_label="PAPER-EXEC",
    )


def get_tradier_context(settings, account_type: str = "live") -> TradierCredentials:
    """Single shared resolver — returns Tradier context for a given account type.

    Used by BOTH:
      - Active Trades fetch (positions / orders / balances)
      - Trade execution (when runtime creds are not passed)
      - /api/tradier/auth_check health endpoint

    Inputs:
      settings     — app.config.Settings (or any namespace with TRADIER_* attrs)
      account_type — "live" | "paper"

    Output:
      TradierCredentials(api_key, account_id, env, base_url, mode_label)

    IMPORTANT: Tradier LIVE and PAPER environments use completely separate
    tokens.  A LIVE token WILL NOT authenticate against sandbox.tradier.com
    and vice-versa.  This function NEVER falls back across environments.

    Raises ValueError when credentials are missing for the requested mode.
    """
    mode = (account_type or "live").lower().strip()

    if mode == "paper":
        api_key = getattr(settings, "TRADIER_API_KEY_PAPER", "") or ""
        account_id = getattr(settings, "TRADIER_ACCOUNT_ID_PAPER", "") or ""
        env = getattr(settings, "TRADIER_ENV_PAPER", "sandbox") or "sandbox"
        label = "PAPER"
        env_var_key = "TRADIER_API_KEY_PAPER"
        env_var_acct = "TRADIER_ACCOUNT_ID_PAPER"
    else:
        # LIVE: also accept legacy single-set vars for backwards compat
        api_key = (
            getattr(settings, "TRADIER_API_KEY_LIVE", "")
            or getattr(settings, "TRADIER_TOKEN", "")
            or ""
        )
        account_id = (
            getattr(settings, "TRADIER_ACCOUNT_ID_LIVE", "")
            or getattr(settings, "TRADIER_ACCOUNT_ID", "")
            or ""
        )
        env = (
            getattr(settings, "TRADIER_ENV_LIVE", "")
            or getattr(settings, "TRADIER_ENV", "live")
            or "live"
        )
        label = "LIVE"
        env_var_key = "TRADIER_API_KEY_LIVE"
        env_var_acct = "TRADIER_ACCOUNT_ID_LIVE"

    base_url = get_tradier_base_url(env)
    token_prefix = api_key[:6] if len(api_key) >= 6 else "(short/empty)"

    # ── Safe debug log (never print full token) ───────────────
    logger.info(
        "[tradier] ctx=%s base=%s acct=%s tokenPresent=%s tokenPrefix=%s",
        label, base_url, account_id or "(empty)", bool(api_key), token_prefix,
    )

    # ── Fail-fast with a clear message ────────────────────────
    if not api_key:
        raise ValueError(
            f"No Tradier API key configured for {label} mode. "
            f"Set {env_var_key} in .env"
        )
    if not account_id:
        raise ValueError(
            f"No Tradier account ID configured for {label} mode. "
            f"Set {env_var_acct} in .env"
        )

    return TradierCredentials(
        api_key=api_key,
        account_id=account_id,
        env=env,
        base_url=base_url,
        mode_label=label,
    )


def log_tradier_request(
    *,
    creds: TradierCredentials,
    method: str,
    path: str,
    status: int | None = None,
    error: str | None = None,
) -> None:
    """Log a single Tradier HTTP request with safe credential context.

    Example output:
      [tradier] ctx=PAPER base=https://sandbox.tradier.com/v1 acct=VA12345
                tokenPresent=true tokenPrefix=abc123 authHeader=true
                path=/v1/accounts/VA12345/positions status=200
    """
    token_prefix = creds.api_key[:6] if len(creds.api_key) >= 6 else "(short)"
    logger.info(
        "[tradier] ctx=%s base=%s acct=%s tokenPresent=%s tokenPrefix=%s "
        "authHeader=true method=%s path=%s status=%s%s",
        creds.mode_label,
        creds.base_url,
        creds.account_id,
        bool(creds.api_key),
        token_prefix,
        method,
        path,
        status if status is not None else "pending",
        f" error={error}" if error else "",
    )


def log_execution_context(
    creds: TradierCredentials,
    *,
    tradier_execution_enabled: bool,
) -> None:
    """Log execution credential context (safe — never logs full API key)."""
    masked_key = creds.api_key[-4:] if len(creds.api_key) >= 4 else "????"
    masked_acct = creds.account_id[-4:] if len(creds.account_id) >= 4 else "????"
    logger.info(
        "event=tradier_execution_context mode_label=%s env=%s "
        "account_id_last4=%s api_key_last4=%s "
        "tradier_execution_enabled=%s",
        creds.mode_label,
        creds.env,
        masked_acct,
        masked_key,
        tradier_execution_enabled,
    )
