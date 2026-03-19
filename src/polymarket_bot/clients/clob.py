from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from polymarket_bot.types import Quote

LOG = logging.getLogger(__name__)


@dataclass
class OrderResult:
    ok: bool
    order_id: str = ""
    status: str = ""
    error: str = ""
    raw: dict[str, Any] | None = None


class ClobExecutionClient:
    def __init__(self, clob_client: Any | None, paper: bool) -> None:
        self._client = clob_client
        self.paper = paper

    def place_post_only(self, quote: Quote) -> OrderResult:
        if self.paper:
            oid = f"paper_{quote.side}_{int(time.time() * 1000)}"
            return OrderResult(ok=True, order_id=oid, status="live")
        if self._client is None:
            return OrderResult(ok=False, error="Live mode requires initialized ClobClient")
        try:
            side = BUY if quote.side.upper() == "BUY" else SELL
            order = OrderArgs(
                token_id=quote.token_id,
                price=round(float(quote.price), 4),
                size=float(round(quote.size, 4)),
                side=side,
            )
            signed = self._client.create_order(order)
            try:
                # Prefer post-only submission when supported by installed client version.
                resp = self._client.post_order(signed, OrderType.GTC, True)
            except TypeError:
                resp = self._client.post_order(signed, OrderType.GTC)
            oid = resp.get("orderID") or resp.get("id") or ""
            status = resp.get("status", "")
            return OrderResult(ok=bool(oid or status in {"live", "matched"}), order_id=oid, status=status, raw=resp)
        except Exception as exc:
            LOG.error("post_only_failed: %s", exc)
            return OrderResult(ok=False, error=str(exc))

    def cancel_order(self, order_id: str) -> bool:
        if not order_id:
            return True
        if self.paper or order_id.startswith("paper_"):
            return True
        if self._client is None:
            return False
        for method_name in ("cancel_order", "cancel"):
            method = getattr(self._client, method_name, None)
            if method is None:
                continue
            try:
                method(order_id)
                return True
            except Exception:
                continue
        return False

    def cancel_market_orders(self, condition_id: str, token_id: str) -> bool:
        if self.paper:
            return True
        if self._client is None:
            return False
        payload = {"market": condition_id, "asset_id": token_id}
        for method_name in ("cancel_market_orders",):
            method = getattr(self._client, method_name, None)
            if method is None:
                continue
            try:
                method(payload)
                return True
            except Exception:
                continue
        return False

    def cancel_all(self) -> bool:
        if self.paper:
            return True
        if self._client is None:
            return False
        for method_name in ("cancel_all", "cancel_all_orders"):
            method = getattr(self._client, method_name, None)
            if method is None:
                continue
            try:
                method()
                return True
            except Exception:
                continue
        return False

    def get_open_orders(self) -> list[dict[str, Any]]:
        if self.paper or self._client is None:
            return []
        method = getattr(self._client, "get_open_orders", None)
        if method is None:
            return []
        try:
            return method() or []
        except Exception:
            return []

    def get_trades(self) -> list[dict[str, Any]]:
        if self.paper or self._client is None:
            return []
        method = getattr(self._client, "get_trades", None)
        if method is None:
            return []
        try:
            trades = method() or []
            if isinstance(trades, list):
                return trades
            return []
        except Exception:
            return []

    def get_collateral_balance(self) -> float | None:
        if self.paper or self._client is None:
            return None
        method = getattr(self._client, "get_balance_allowance", None)
        if method is None:
            return None
        try:
            payload = method({"asset_type": "COLLATERAL"})
            balance = payload.get("balance") if isinstance(payload, dict) else None
            if balance is None:
                return None
            return float(balance)
        except Exception:
            return None
