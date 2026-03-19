from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Quote:
    side: str
    token_id: str
    price: float
    size: float
    order_id: str = ""


@dataclass
class MarketInfo:
    question: str
    condition_id: str
    up_token: str
    down_token: str
    neg_risk: bool = False
    tick_size: str = "0.01"


@dataclass
class BookState:
    best_bid: float = 0.0
    best_ask: float = 1.0
    last_update_ts: float = 0.0
    bids: list[tuple[float, float]] = field(default_factory=list)
    asks: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class SideState:
    position: float = 0.0
    cost_basis: float = 0.0
    open_bid: Quote | None = None
    open_ask: Quote | None = None


@dataclass
class CandleState:
    candle_start: int = 0
    candle_end: int = 0
    market: MarketInfo | None = None
    active: bool = False
    ml_direction: str = ""
    ml_confidence: float = 0.0


@dataclass
class HealthSnapshot:
    ws_ok: bool
    stale_data: bool
    consecutive_post_fails: int
    extra: dict[str, Any] = field(default_factory=dict)
