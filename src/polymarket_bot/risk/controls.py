from __future__ import annotations

import time
from dataclasses import dataclass

from polymarket_bot.types import HealthSnapshot


@dataclass
class CircuitBreaker:
    max_consecutive_post_fails: int
    consecutive_post_fails: int = 0
    paused: bool = False

    def mark_post_success(self) -> None:
        self.consecutive_post_fails = 0
        self.paused = False

    def mark_post_failure(self) -> None:
        self.consecutive_post_fails += 1
        if self.consecutive_post_fails >= self.max_consecutive_post_fails:
            self.paused = True


@dataclass
class DrawdownGuard:
    start_equity: float
    max_daily_drawdown_pct: float

    def should_pause(self, equity: float) -> bool:
        if self.start_equity <= 0:
            return False
        drawdown = (self.start_equity - equity) / self.start_equity
        return drawdown >= self.max_daily_drawdown_pct


def check_health(
    *,
    ws_ok: bool,
    last_data_ts: float,
    stale_data_seconds: int,
    consecutive_post_fails: int,
) -> HealthSnapshot:
    now = time.time()
    stale_data = (now - last_data_ts) > stale_data_seconds if last_data_ts > 0 else True
    return HealthSnapshot(
        ws_ok=ws_ok,
        stale_data=stale_data,
        consecutive_post_fails=consecutive_post_fails,
        extra={"age_seconds": round(max(0.0, now - last_data_ts), 2) if last_data_ts else None},
    )
