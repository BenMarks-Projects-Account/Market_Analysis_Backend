import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from pydantic import BaseModel

_cfg_log = logging.getLogger(__name__)


_DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=_DOTENV_PATH, override=True)
_DOTENV_VALUES = dotenv_values(_DOTENV_PATH)


def _cfg(primary: str, *aliases: str, default: str = "") -> str:
    keys = (primary, *aliases)
    for key in keys:
        value = os.getenv(key)
        if value is not None and str(value).strip() != "":
            return str(value)
    for key in keys:
        value = _DOTENV_VALUES.get(key)
        if value is not None and str(value).strip() != "":
            return str(value)
    return default


class Settings(BaseModel):
    # ── Environment mode ────────────────────────────────────────
    # "development" (default) → forces PAPER routing for all orders.
    # "production"            → respects account_mode from frontend.
    ENVIRONMENT: str = os.getenv("BENTRADE_ENVIRONMENT", "development").lower()

    # ── Single execution gate ───────────────────────────────────
    # ONE boolean: "is BenTrade allowed to send orders to Tradier?"
    # Env sets the default; UI toggle updates at runtime; persisted
    # in runtime_config.json so it survives restarts.
    # When False → every order becomes a DRY RUN (payload logged only).
    TRADIER_EXECUTION_ENABLED: bool = os.getenv("TRADIER_EXECUTION_ENABLED", "false").lower() == "true"

    # ── Dual credential sets (LIVE + PAPER) ─────────────────────
    TRADIER_API_KEY_LIVE: str = _cfg("TRADIER_API_KEY_LIVE", default="")
    TRADIER_ACCOUNT_ID_LIVE: str = _cfg("TRADIER_ACCOUNT_ID_LIVE", default="")
    TRADIER_ENV_LIVE: str = _cfg("TRADIER_ENV_LIVE", default="live").lower()

    TRADIER_API_KEY_PAPER: str = _cfg("TRADIER_API_KEY_PAPER", default="")
    TRADIER_ACCOUNT_ID_PAPER: str = _cfg("TRADIER_ACCOUNT_ID_PAPER", default="")
    TRADIER_ENV_PAPER: str = _cfg("TRADIER_ENV_PAPER", default="sandbox").lower()

    # ── DEPRECATED: legacy single-set vars (fall back for compat) ──
    TRADIER_ACCOUNT_ID: str = _cfg("TRADIER_ACCOUNT_ID", default="")
    TRADIER_TOKEN: str = _cfg("TRADIER_TOKEN", "TRADIER_API_KEY", "TRAIDER_API_KEY", default="")
    TRADIER_ENV: str = _cfg("TRADIER_ENV", default="live").lower()
    TRADIER_BASE_URL: str = ""

    FINNHUB_KEY: str = _cfg("FINNHUB_KEY", "FINNHUB_API_KEY", default="")
    FINNHUB_BASE_URL: str = _cfg("FINNHUB_BASE_URL", default="https://finnhub.io/api/v1")

    FRED_KEY: str = _cfg("FRED_KEY", "FRED_API_KEY", default="")
    FRED_BASE_URL: str = _cfg("FRED_BASE_URL", default="https://api.stlouisfed.org/fred")

    POLYGON_API_KEY: str = _cfg("POLYGON_API_KEY", default="")
    POLYGON_BASE_URL: str = _cfg("POLYGON_BASE_URL", default="https://api.polygon.io")

    # ── AWS Bedrock (premium model execution) ──────────────────
    BEDROCK_ENABLED: bool = os.getenv("BEDROCK_ENABLED", "true").lower() == "true"
    BEDROCK_REGION: str = _cfg("BEDROCK_REGION", "AWS_DEFAULT_REGION", default="us-east-1")
    BEDROCK_MODEL_ID: str = _cfg("BEDROCK_MODEL_ID", default="us.amazon.nova-pro-v1:0")
    BEDROCK_TIMEOUT_SECONDS: float = float(os.getenv("BEDROCK_TIMEOUT_SECONDS", "120"))

    FRED_VIX_SERIES_ID: str = "VIXCLS"

    QUOTE_CACHE_TTL_SECONDS: int = 60
    EXPIRATIONS_CACHE_TTL_SECONDS: int = 300
    CHAIN_CACHE_TTL_SECONDS: int = 300
    CANDLES_CACHE_TTL_SECONDS: int = 1800
    FRED_CACHE_TTL_SECONDS: int = 300

    HTTP_TIMEOUT_SECONDS: float = 15.0
    MODEL_TIMEOUT_SECONDS: float = float(os.getenv("MODEL_TIMEOUT_SECONDS", "180"))

    TRADING_CONFIRMATION_TTL_SECONDS: int = int(os.getenv("TRADING_CONFIRMATION_TTL_SECONDS", "300"))
    TRADING_CONFIRMATION_SECRET: str = os.getenv("TRADING_CONFIRMATION_SECRET", "")
    TRADING_CONTRACT_MULTIPLIER: int = int(os.getenv("TRADING_CONTRACT_MULTIPLIER", "100"))
    LIVE_DATA_MAX_AGE_SECONDS: int = int(os.getenv("LIVE_DATA_MAX_AGE_SECONDS", "30"))

    MAX_WIDTH_DEFAULT: float = float(os.getenv("MAX_WIDTH_DEFAULT", "50"))
    MAX_LOSS_PER_SPREAD_DEFAULT: float = float(os.getenv("MAX_LOSS_PER_SPREAD_DEFAULT", "2000"))
    MIN_CREDIT_DEFAULT: float = float(os.getenv("MIN_CREDIT_DEFAULT", "0.2"))

    DTE_MIN: int = int(os.getenv("DTE_MIN", "3"))
    DTE_MAX: int = int(os.getenv("DTE_MAX", "14"))
    MAX_EXPIRATIONS_PER_SYMBOL: int = int(os.getenv("MAX_EXPIRATIONS_PER_SYMBOL", "6"))
    VALIDATION_MODE: bool = os.getenv("VALIDATION_MODE", "false").lower() == "true"

    # -- Snapshot capture / replay ------------------------------------------
    SNAPSHOT_CAPTURE: bool = os.getenv("SNAPSHOT_CAPTURE", "0") == "1"
    SNAPSHOT_CAPTURE_SYMBOLS: str = os.getenv("SNAPSHOT_CAPTURE_SYMBOLS", "")
    SNAPSHOT_CAPTURE_LIMIT_PER_SYMBOL: int = int(os.getenv("SNAPSHOT_CAPTURE_LIMIT_PER_SYMBOL", "0"))
    OPTION_CHAIN_SOURCE: str = os.getenv("OPTION_CHAIN_SOURCE", "tradier").lower()
    SNAPSHOT_DIR: str = os.getenv("SNAPSHOT_DIR", "")
    SNAPSHOT_MAX_AGE_HOURS: int = int(os.getenv("SNAPSHOT_MAX_AGE_HOURS", "48"))
    SNAPSHOT_RETENTION_DAYS: int = int(os.getenv("SNAPSHOT_RETENTION_DAYS", "7"))

    # ── Experiment flags ───────────────────────────────────────────
    # (Soft-cap and candidate sampling removed — all candidates
    #  proceed directly to enrichment with no truncation.)

    def model_post_init(self, __context) -> None:
        from app.trading.tradier_credentials import get_tradier_base_url

        # ── Back-fill dual-cred vars from legacy single-set vars ──
        # If user hasn't set the new LIVE vars yet, fall back to the
        # old TRADIER_API_KEY / TRADIER_ACCOUNT_ID / TRADIER_ENV.
        if not self.TRADIER_API_KEY_LIVE and self.TRADIER_TOKEN:
            self.TRADIER_API_KEY_LIVE = self.TRADIER_TOKEN
        if not self.TRADIER_ACCOUNT_ID_LIVE and self.TRADIER_ACCOUNT_ID:
            self.TRADIER_ACCOUNT_ID_LIVE = self.TRADIER_ACCOUNT_ID
        if not self.TRADIER_ENV_LIVE or self.TRADIER_ENV_LIVE == "live":
            if self.TRADIER_ENV:
                self.TRADIER_ENV_LIVE = self.TRADIER_ENV

        # Keep legacy fields in sync for existing code paths
        if not self.TRADIER_TOKEN and self.TRADIER_API_KEY_LIVE:
            self.TRADIER_TOKEN = self.TRADIER_API_KEY_LIVE
        if not self.TRADIER_ACCOUNT_ID and self.TRADIER_ACCOUNT_ID_LIVE:
            self.TRADIER_ACCOUNT_ID = self.TRADIER_ACCOUNT_ID_LIVE

        self.TRADIER_BASE_URL = get_tradier_base_url(self.TRADIER_ENV)


# ── Runtime config persistence ─────────────────────────────────
_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "runtime_config.json"


def _load_runtime_config() -> dict:
    """Load persisted runtime config from JSON file."""
    try:
        if _RUNTIME_CONFIG_PATH.exists():
            return json.loads(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        _cfg_log.warning("Failed to load runtime_config.json: %s", exc)
    return {}


def _save_runtime_config(data: dict) -> None:
    """Persist runtime config to JSON file."""
    try:
        _RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RUNTIME_CONFIG_PATH.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        _cfg_log.warning("Failed to save runtime_config.json: %s", exc)


_settings = Settings()

# Restore persisted execution flag on startup
_persisted = _load_runtime_config()
if "tradier_execution_enabled" in _persisted:
    _settings.TRADIER_EXECUTION_ENABLED = bool(_persisted["tradier_execution_enabled"])
    _cfg_log.info(
        "Restored tradier_execution_enabled=%s from runtime_config.json",
        _settings.TRADIER_EXECUTION_ENABLED,
    )


def get_settings() -> Settings:
    return _settings


def set_tradier_execution_enabled(enabled: bool) -> Settings:
    """Set the runtime execution toggle and persist to disk."""
    _settings.TRADIER_EXECUTION_ENABLED = enabled
    rc = _load_runtime_config()
    rc["tradier_execution_enabled"] = enabled
    rc["updated_at"] = datetime.now(timezone.utc).isoformat()
    rc["updated_by"] = "ui-toggle"
    _save_runtime_config(rc)
    _cfg_log.info("tradier_execution_toggled enabled=%s", enabled)
    return _settings
