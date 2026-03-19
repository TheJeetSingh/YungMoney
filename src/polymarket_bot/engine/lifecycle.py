from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from polymarket_bot.clients.clob import ClobExecutionClient
from polymarket_bot.clients.market_data import MarketDataClient
from polymarket_bot.config import Settings
from polymarket_bot.risk.controls import CircuitBreaker, DrawdownGuard, check_health
from polymarket_bot.strategy.avellaneda import AvellanedaStoikov, QuoteDecision, QuoteParams
from polymarket_bot.types import CandleState, Quote, SideState

LOG = logging.getLogger(__name__)

CANDLE_SECONDS = 300


@dataclass
class EngineState:
    candle: CandleState
    up_state: SideState
    down_state: SideState
    bankroll: float
    sigma_logit_up: float = 0.1
    sigma_logit_down: float = 0.1
    last_mid_up: float | None = None
    last_mid_down: float | None = None


class LifecycleEngine:
    def __init__(
        self,
        *,
        settings: Settings,
        market_data: MarketDataClient,
        execution: ClobExecutionClient,
    ) -> None:
        self.settings = settings
        self.market_data = market_data
        self.execution = execution
        self.strategy = AvellanedaStoikov(
            QuoteParams(max_inventory=settings.max_inventory),
        )
        self.state = EngineState(
            candle=CandleState(),
            up_state=SideState(),
            down_state=SideState(),
            bankroll=settings.bankroll_usd,
        )
        self.breaker = CircuitBreaker(settings.max_consecutive_post_fails)
        self.drawdown = DrawdownGuard(settings.bankroll_usd, settings.max_daily_drawdown_pct)
        self._shutdown = False

    async def run(self) -> None:
        LOG.info("engine_started mode=%s", self.settings.bot_mode)
        lifecycle_task = asyncio.create_task(self._candle_loop())
        quote_task = asyncio.create_task(self._quote_loop())
        inventory_task = asyncio.create_task(self._inventory_loop())
        try:
            await asyncio.gather(lifecycle_task, quote_task, inventory_task)
        finally:
            await self.market_data.stop_market_ws()
            self.execution.cancel_all()

    async def _candle_loop(self) -> None:
        last_candle = 0
        while not self._shutdown:
            now = int(time.time())
            candle_start = (now // CANDLE_SECONDS) * CANDLE_SECONDS
            candle_end = candle_start + CANDLE_SECONDS
            if candle_start != last_candle:
                last_candle = candle_start
                await self._on_new_candle(candle_start, candle_end)
            await asyncio.sleep(0.5)

    async def _on_new_candle(self, candle_start: int, candle_end: int) -> None:
        self.execution.cancel_all()
        market = self.market_data.find_active_btc_5m_market()
        if market is None:
            LOG.warning("no_active_market")
            self.state.candle = CandleState(candle_start=candle_start, candle_end=candle_end, active=False)
            return
        self.state.candle = CandleState(
            candle_start=candle_start,
            candle_end=candle_end,
            market=market,
            active=True,
            ml_direction="UP",
            ml_confidence=0.0,
        )
        self.state.up_state = SideState()
        self.state.down_state = SideState()
        self.state.sigma_logit_up = 0.1
        self.state.sigma_logit_down = 0.1
        self.state.last_mid_up = None
        self.state.last_mid_down = None
        self.market_data.refresh_books(market)
        self.state.candle.ml_direction, self.state.candle.ml_confidence = self._derive_direction_bias()
        await self.market_data.start_market_ws(market)
        LOG.info(
            "new_candle market=%s start=%s",
            market.question,
            datetime.fromtimestamp(candle_start, tz=timezone.utc).isoformat(),
        )

    async def _quote_loop(self) -> None:
        refresh_seconds = self.settings.quote_refresh_ms / 1000.0
        while not self._shutdown:
            await asyncio.sleep(refresh_seconds)
            candle = self.state.candle
            if not candle.active or candle.market is None:
                continue

            now = time.time()
            elapsed = now - candle.candle_start
            if elapsed >= self.settings.exit_deadline_sec:
                await self._flatten_positions()
                continue

            self.market_data.refresh_books(candle.market)
            health = check_health(
                ws_ok=self.market_data.books.ws_connected,
                last_data_ts=min(
                    self.market_data.books.up.last_update_ts or now,
                    self.market_data.books.down.last_update_ts or now,
                ),
                stale_data_seconds=self.settings.stale_data_seconds,
                consecutive_post_fails=self.breaker.consecutive_post_fails,
            )
            if health.stale_data or self.breaker.paused:
                self.execution.cancel_all()
                LOG.warning("quote_loop_paused stale=%s breaker=%s", health.stale_data, self.breaker.paused)
                continue

            if self.drawdown.should_pause(self.state.bankroll):
                self.execution.cancel_all()
                LOG.error("daily_drawdown_guard_triggered bankroll=%s", self.state.bankroll)
                continue

            await self._quote_side("up")
            await self._quote_side("down")

    async def _quote_side(self, side_name: str) -> None:
        candle = self.state.candle
        if candle.market is None:
            return
        side_state = self.state.up_state if side_name == "up" else self.state.down_state
        book = self.market_data.books.up if side_name == "up" else self.market_data.books.down
        mid = self._mid_price(book.best_bid, book.best_ask)
        if not (0 < mid < 1):
            return

        self._update_sigma(side_name, mid)
        sigma = self.state.sigma_logit_up if side_name == "up" else self.state.sigma_logit_down
        time_remaining = max(0.01, (self.settings.exit_deadline_sec - (time.time() - candle.candle_start)) / CANDLE_SECONDS)
        decision = self.strategy.compute_quotes(
            mid_price=mid,
            inventory=side_state.position,
            sigma_logit=sigma,
            time_remaining=time_remaining,
            ml_direction=candle.ml_direction,
            ml_confidence=candle.ml_confidence,
        )
        if decision is None:
            return

        token_id = candle.market.up_token if side_name == "up" else candle.market.down_token
        await self._replace_quotes(side_state, token_id, decision)
        if self.execution.paper:
            self._simulate_paper_fills(side_state, book, decision)

    async def _replace_quotes(self, state: SideState, token_id: str, decision: QuoteDecision) -> None:
        if state.open_bid:
            self.execution.cancel_order(state.open_bid.order_id)
        if state.open_ask:
            self.execution.cancel_order(state.open_ask.order_id)

        bid_quote = Quote(side="BUY", token_id=token_id, price=decision.bid_price, size=decision.bid_size)
        ask_quote = Quote(side="SELL", token_id=token_id, price=decision.ask_price, size=decision.ask_size)

        bid_result = self.execution.place_post_only(bid_quote)
        ask_result = self.execution.place_post_only(ask_quote)
        if bid_result.ok and ask_result.ok:
            self.breaker.mark_post_success()
            bid_quote.order_id = bid_result.order_id
            ask_quote.order_id = ask_result.order_id
            state.open_bid = bid_quote
            state.open_ask = ask_quote
            return

        self.breaker.mark_post_failure()
        state.open_bid = None
        state.open_ask = None
        LOG.error("quote_post_failed bid_ok=%s ask_ok=%s", bid_result.ok, ask_result.ok)

    def _simulate_paper_fills(self, state: SideState, book, decision: QuoteDecision) -> None:
        if state.open_bid and book.best_ask <= decision.bid_price:
            fill_cost = decision.bid_price * decision.bid_size
            state.position += decision.bid_size
            state.cost_basis += fill_cost
            self.state.bankroll -= fill_cost
            state.open_bid = None
        if state.open_ask and state.position > 0 and book.best_bid >= decision.ask_price:
            sell_size = min(decision.ask_size, state.position)
            avg = state.cost_basis / state.position if state.position > 0 else decision.ask_price
            pnl = (decision.ask_price - avg) * sell_size
            state.position -= sell_size
            state.cost_basis = max(0.0, state.cost_basis - avg * sell_size)
            self.state.bankroll += decision.ask_price * sell_size + pnl
            state.open_ask = None

    async def _inventory_loop(self) -> None:
        while not self._shutdown:
            await asyncio.sleep(2)
            if not self.state.candle.active:
                continue
            if abs(self.state.up_state.position) > self.settings.max_inventory:
                if self.state.up_state.open_bid:
                    self.execution.cancel_order(self.state.up_state.open_bid.order_id)
                    self.state.up_state.open_bid = None
            if abs(self.state.down_state.position) > self.settings.max_inventory:
                if self.state.down_state.open_bid:
                    self.execution.cancel_order(self.state.down_state.open_bid.order_id)
                    self.state.down_state.open_bid = None

    async def _flatten_positions(self) -> None:
        candle = self.state.candle
        if candle.market is None:
            return
        self.execution.cancel_all()
        self.state.up_state.open_bid = None
        self.state.up_state.open_ask = None
        self.state.down_state.open_bid = None
        self.state.down_state.open_ask = None
        if self.execution.paper:
            self.state.up_state.position = 0.0
            self.state.up_state.cost_basis = 0.0
            self.state.down_state.position = 0.0
            self.state.down_state.cost_basis = 0.0
        self.state.candle.active = False
        LOG.info("positions_flattened market=%s", candle.market.question)

    @staticmethod
    def _mid_price(best_bid: float, best_ask: float) -> float:
        if best_bid <= 0 or best_ask <= 0:
            return 0.0
        return (best_bid + best_ask) / 2.0

    def _derive_direction_bias(self) -> tuple[str, float]:
        up_mid = self._mid_price(self.market_data.books.up.best_bid, self.market_data.books.up.best_ask)
        down_mid = self._mid_price(self.market_data.books.down.best_bid, self.market_data.books.down.best_ask)
        if up_mid <= 0 or down_mid <= 0:
            return "UP", 0.05
        if up_mid == down_mid:
            return "UP", 0.05
        direction = "UP" if up_mid > down_mid else "DOWN"
        confidence = min(abs(up_mid - down_mid), 0.2)
        return direction, confidence

    def _update_sigma(self, side_name: str, mid: float) -> None:
        alpha = 0.1
        safe_mid = max(1e-6, min(1 - 1e-6, mid))
        logit = math.log(safe_mid / (1 - safe_mid))
        if side_name == "up":
            if self.state.last_mid_up is None:
                self.state.last_mid_up = logit
                return
            ret = abs(logit - self.state.last_mid_up)
            self.state.sigma_logit_up = alpha * ret + (1 - alpha) * self.state.sigma_logit_up
            self.state.last_mid_up = logit
        else:
            if self.state.last_mid_down is None:
                self.state.last_mid_down = logit
                return
            ret = abs(logit - self.state.last_mid_down)
            self.state.sigma_logit_down = alpha * ret + (1 - alpha) * self.state.sigma_logit_down
            self.state.last_mid_down = logit
