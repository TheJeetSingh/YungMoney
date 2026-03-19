from __future__ import annotations

import asyncio
import logging

from polymarket_bot.clients.auth import init_clob_client
from polymarket_bot.clients.clob import ClobExecutionClient
from polymarket_bot.clients.market_data import MarketDataClient
from polymarket_bot.config import load_settings, validate_settings
from polymarket_bot.engine.lifecycle import LifecycleEngine
from polymarket_bot.logging_utils import configure_logging


def _startup_log(settings) -> None:
    logging.getLogger(__name__).info(
        "bot_startup mode=%s signature_type=%s funder=%s bankroll_cfg=%s floor_pct=%s trade_window=%ss-%ss",
        settings.bot_mode,
        settings.signature_type,
        settings.funder_address or "unset",
        settings.bankroll_usd,
        settings.min_equity_floor_pct,
        settings.trade_start_sec,
        settings.trade_stop_sec,
    )


async def _run() -> None:
    settings = load_settings()
    validate_settings(settings)
    configure_logging()
    _startup_log(settings)

    clob_client, _ = init_clob_client(settings)
    execution = ClobExecutionClient(clob_client, paper=not settings.is_live)
    market_data = MarketDataClient(settings.gamma_host, settings.clob_host, settings.market_tag)
    engine = LifecycleEngine(settings=settings, market_data=market_data, execution=execution)
    await engine.run()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
