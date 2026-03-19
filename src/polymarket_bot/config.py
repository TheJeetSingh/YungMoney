from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_mode: str
    chain_id: int
    clob_host: str
    gamma_host: str
    signature_type: int
    private_key: str
    funder_address: str
    api_key: str
    secret: str
    passphrase: str
    bankroll_usd: float
    quote_refresh_ms: int
    max_inventory: float
    exit_deadline_sec: int
    max_daily_drawdown_pct: float
    max_consecutive_post_fails: int
    stale_data_seconds: int
    market_tag: str

    @property
    def is_live(self) -> bool:
        return self.bot_mode.lower() == "live"


def _get_env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else default


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        bot_mode=_get_env("BOT_MODE", "paper"),
        chain_id=int(_get_env("CHAIN_ID", "137")),
        clob_host=_get_env("CLOB_HOST", "https://clob.polymarket.com"),
        gamma_host=_get_env("GAMMA_HOST", "https://gamma-api.polymarket.com"),
        signature_type=int(_get_env("SIGNATURE_TYPE", "1")),
        private_key=_get_env("PRIVATE_KEY"),
        funder_address=_get_env("FUNDER_ADDRESS"),
        api_key=_get_env("API_KEY"),
        secret=_get_env("SECRET"),
        passphrase=_get_env("PASSPHRASE"),
        bankroll_usd=float(_get_env("BANKROLL_USD", "500")),
        quote_refresh_ms=int(_get_env("QUOTE_REFRESH_MS", "500")),
        max_inventory=float(_get_env("MAX_INVENTORY", "100")),
        exit_deadline_sec=int(_get_env("EXIT_DEADLINE_SEC", "240")),
        max_daily_drawdown_pct=float(_get_env("MAX_DAILY_DRAWDOWN_PCT", "0.20")),
        max_consecutive_post_fails=int(_get_env("MAX_CONSECUTIVE_POST_FAILS", "8")),
        stale_data_seconds=int(_get_env("STALE_DATA_SECONDS", "5")),
        market_tag=_get_env("MARKET_TAG", "crypto"),
    )


def validate_settings(settings: Settings) -> None:
    if settings.signature_type != 1:
        raise ValueError("This build is configured for POLY_PROXY only (SIGNATURE_TYPE=1).")
    if settings.chain_id != 137:
        raise ValueError("CHAIN_ID must be 137 for Polygon mainnet.")
    if settings.bot_mode.lower() not in {"paper", "live"}:
        raise ValueError("BOT_MODE must be either 'paper' or 'live'.")
    if settings.is_live:
        if not settings.private_key:
            raise ValueError("PRIVATE_KEY is required for live mode.")
        if not settings.funder_address:
            raise ValueError("FUNDER_ADDRESS is required for live mode.")
