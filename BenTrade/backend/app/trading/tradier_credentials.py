"""
Tradier Credential Resolver
============================

Single helper that resolves Tradier API credentials based on purpose and
account mode.  All market-data calls always use LIVE credentials.
Execution calls route to PAPER or LIVE based on the Trade Ticket toggle.

Resolver rules:
  DATA      → always LIVE credentials (accountMode ignored)
  EXECUTION → PAPER creds when accountMode == "paper" (default)
              LIVE  creds when accountMode == "live" AND TRADING_LIVE_ENABLED

Inputs:
  - TRADIER_API_KEY_LIVE, TRADIER_ACCOUNT_ID_LIVE, TRADIER_ENV_LIVE
  - TRADIER_API_KEY_PAPER, TRADIER_ACCOUNT_ID_PAPER, TRADIER_ENV_PAPER
  - TRADING_LIVE_ENABLED (env flag — must be "true" to allow live execution)

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
    trading_live_enabled: bool,
) -> TradierCredentials:
    """Resolve the correct Tradier credential set.

    Raises ValueError if live execution is requested but not enabled.
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
        if not trading_live_enabled:
            raise ValueError(
                "Live execution is blocked: TRADING_LIVE_ENABLED is not set to 'true'. "
                "Set TRADING_LIVE_ENABLED=true in .env to allow live order submission."
            )
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


def log_execution_context(
    creds: TradierCredentials,
    *,
    trade_capability_enabled: bool,
    trading_live_enabled: bool,
) -> None:
    """Log execution credential context (safe — never logs full API key)."""
    masked_key = creds.api_key[-4:] if len(creds.api_key) >= 4 else "????"
    masked_acct = creds.account_id[-4:] if len(creds.account_id) >= 4 else "????"
    logger.info(
        "event=tradier_execution_context mode_label=%s env=%s "
        "account_id_last4=%s api_key_last4=%s "
        "trade_capability_enabled=%s trading_live_enabled=%s",
        creds.mode_label,
        creds.env,
        masked_acct,
        masked_key,
        trade_capability_enabled,
        trading_live_enabled,
    )
