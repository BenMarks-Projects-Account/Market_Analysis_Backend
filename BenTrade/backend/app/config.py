import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from pydantic import BaseModel


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
    ENABLE_LIVE_TRADING: bool = os.getenv("ENABLE_LIVE_TRADING", "false").lower() == "true"
    LIVE_TRADING_RUNTIME_ENABLED: bool = os.getenv("LIVE_TRADING_RUNTIME_ENABLED", "false").lower() == "true"

    TRADIER_ACCOUNT_ID: str = _cfg("TRADIER_ACCOUNT_ID", default="")
    TRADIER_TOKEN: str = _cfg("TRADIER_TOKEN", "TRADIER_API_KEY", "TRAIDER_API_KEY", default="")
    TRADIER_ENV: str = _cfg("TRADIER_ENV", default="live").lower()
    TRADIER_BASE_URL: str = ""

    FINNHUB_KEY: str = _cfg("FINNHUB_KEY", "FINNHUB_API_KEY", default="")
    FINNHUB_BASE_URL: str = _cfg("FINNHUB_BASE_URL", default="https://finnhub.io/api/v1")

    FRED_KEY: str = _cfg("FRED_KEY", "FRED_API_KEY", default="")
    FRED_BASE_URL: str = _cfg("FRED_BASE_URL", default="https://api.stlouisfed.org/fred")

    FRED_VIX_SERIES_ID: str = "VIXCLS"

    QUOTE_CACHE_TTL_SECONDS: int = 10
    CHAIN_CACHE_TTL_SECONDS: int = 60
    CANDLES_CACHE_TTL_SECONDS: int = 1800
    FRED_CACHE_TTL_SECONDS: int = 43200

    HTTP_TIMEOUT_SECONDS: float = 15.0

    TRADING_CONFIRMATION_TTL_SECONDS: int = int(os.getenv("TRADING_CONFIRMATION_TTL_SECONDS", "300"))
    TRADING_CONFIRMATION_SECRET: str = os.getenv("TRADING_CONFIRMATION_SECRET", "")
    TRADING_CONTRACT_MULTIPLIER: int = int(os.getenv("TRADING_CONTRACT_MULTIPLIER", "100"))
    LIVE_DATA_MAX_AGE_SECONDS: int = int(os.getenv("LIVE_DATA_MAX_AGE_SECONDS", "30"))
    TRADIER_DRY_RUN_LIVE: bool = os.getenv("TRADIER_DRY_RUN_LIVE", "true").lower() == "true"

    MAX_WIDTH_DEFAULT: float = float(os.getenv("MAX_WIDTH_DEFAULT", "10"))
    MAX_LOSS_PER_SPREAD_DEFAULT: float = float(os.getenv("MAX_LOSS_PER_SPREAD_DEFAULT", "500"))
    MIN_CREDIT_DEFAULT: float = float(os.getenv("MIN_CREDIT_DEFAULT", "0.2"))

    DTE_MIN: int = int(os.getenv("DTE_MIN", "3"))
    DTE_MAX: int = int(os.getenv("DTE_MAX", "14"))
    MAX_EXPIRATIONS_PER_SYMBOL: int = int(os.getenv("MAX_EXPIRATIONS_PER_SYMBOL", "6"))
    VALIDATION_MODE: bool = os.getenv("VALIDATION_MODE", "false").lower() == "true"

    def model_post_init(self, __context) -> None:
        self.TRADIER_BASE_URL = (
            "https://sandbox.tradier.com/v1"
            if self.TRADIER_ENV == "sandbox"
            else "https://api.tradier.com/v1"
        )


_settings = Settings()


def get_settings() -> Settings:
    return _settings


def set_live_runtime_enabled(enabled: bool) -> Settings:
    _settings.LIVE_TRADING_RUNTIME_ENABLED = enabled
    return _settings
