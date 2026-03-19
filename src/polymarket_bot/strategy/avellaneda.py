from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class QuoteParams:
    min_spread: float = 0.02
    min_price: float = 0.05
    max_price: float = 0.95
    gamma: float = 0.10
    k: float = 1.5
    max_inventory: float = 100.0
    ml_bias_factor: float = 0.05


@dataclass(frozen=True)
class QuoteDecision:
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float


class AvellanedaStoikov:
    def __init__(self, params: QuoteParams) -> None:
        self.params = params

    @staticmethod
    def logit(value: float) -> float:
        safe_value = max(1e-6, min(1 - 1e-6, value))
        return math.log(safe_value / (1.0 - safe_value))

    @staticmethod
    def sigmoid(value: float) -> float:
        return 1.0 / (1.0 + math.exp(-value))

    def compute_quotes(
        self,
        *,
        mid_price: float,
        bankroll: float,
        inventory: float,
        sigma_logit: float,
        time_remaining: float,
        ml_direction: str = "",
        ml_confidence: float = 0.0,
    ) -> QuoteDecision | None:
        p = self.params
        if not (p.min_price <= mid_price <= p.max_price):
            return None

        logit_mid = self.logit(mid_price)
        ml_bias = 0.0
        if ml_direction == "UP":
            ml_bias = p.ml_bias_factor * ml_confidence
        elif ml_direction == "DOWN":
            ml_bias = -p.ml_bias_factor * ml_confidence

        inventory_adjust = inventory * p.gamma * (sigma_logit**2) * max(time_remaining, 0.01)
        reservation = logit_mid - inventory_adjust + ml_bias

        inventory_spread = p.gamma * (sigma_logit**2) * max(time_remaining, 0.01)
        arrival_spread = (1.0 / p.gamma) * math.log(1.0 + p.gamma / p.k)
        half_spread = max((inventory_spread + arrival_spread) / 2.0, self._min_logit_half_spread())

        bid_price = self.sigmoid(reservation - half_spread)
        ask_price = self.sigmoid(reservation + half_spread)
        bid_price = max(p.min_price, min(p.max_price, round(bid_price, 4)))
        ask_price = max(p.min_price, min(p.max_price, round(ask_price, 4)))
        if ask_price <= bid_price:
            ask_price = min(p.max_price, round(bid_price + p.min_spread, 4))
        if (ask_price - bid_price) < p.min_spread:
            ask_price = min(p.max_price, round(bid_price + p.min_spread, 4))
            if ask_price <= bid_price:
                bid_price = max(p.min_price, round(ask_price - p.min_spread, 4))

        inv_scale = max(0.2, 1.0 - abs(inventory) / max(p.max_inventory, 1.0))
        # Legacy sizing behavior from hw-utils-archive: bankroll-proportional size with floor/cap.
        base_size = max(5.0, min(30.0, bankroll * 0.05 / max(mid_price, 0.01)))
        size = max(5.0, round(base_size * inv_scale, 2))
        bid_size = size
        ask_size = size
        if inventory > p.max_inventory * 0.5:
            ask_size = round(size * 1.5, 2)
            bid_size = max(5.0, round(size * 0.5, 2))
        elif inventory < -p.max_inventory * 0.5:
            bid_size = round(size * 1.5, 2)
            ask_size = max(5.0, round(size * 0.5, 2))

        return QuoteDecision(
            bid_price=bid_price,
            ask_price=ask_price,
            bid_size=bid_size,
            ask_size=ask_size,
        )

    def _min_logit_half_spread(self) -> float:
        return self.logit(0.5 + self.params.min_spread / 2.0) - self.logit(0.5)
