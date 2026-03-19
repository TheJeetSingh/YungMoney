#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from polymarket_bot.clients.auth import init_clob_client
from polymarket_bot.clients.market_data import MarketDataClient
from polymarket_bot.config import load_settings, validate_settings


def main() -> None:
    settings = load_settings()
    validate_settings(settings)
    if not settings.is_live:
        raise RuntimeError("Set BOT_MODE=live for smoke test.")

    client, _ = init_clob_client(settings)
    if client is None:
        raise RuntimeError("Could not init CLOB client")

    md = MarketDataClient(settings.gamma_host, settings.clob_host, settings.market_tag)
    market = md.find_active_btc_5m_market()
    if market is None:
        raise RuntimeError("No active BTC 5m market found")

    token = market.up_token
    price = max(0.05, min(0.95, md.fetch_book(token).best_bid))
    order = OrderArgs(token_id=token, price=round(price, 2), size=5.0, side=BUY)
    signed = client.create_order(order)
    response = client.post_order(signed, OrderType.GTC)
    order_id = response.get("orderID", "")
    print("order_response:", response)
    if order_id:
        try:
            client.cancel(order_id)
        except Exception:
            cancel = getattr(client, "cancel_order", None)
            if cancel:
                cancel(order_id)
        print("cancelled:", order_id)


if __name__ == "__main__":
    main()
