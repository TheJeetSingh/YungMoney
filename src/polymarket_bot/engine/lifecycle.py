from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from polymarket_bot.clients.clob import ClobExecutionClient
from polymarket_bot.clients.market_data import MarketDataClient
from polymarket_bot.config import Settings
from polymarket_bot.ml.predictor import LegacySegmentPredictor
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
        predictor: LegacySegmentPredictor | None = None,
    ) -> None:
        self.settings = settings
        self.market_data = market_data
        self.execution = execution
        self.predictor = predictor or LegacySegmentPredictor(Path("hw-utils-archive/models"))
        self.strategy = AvellanedaStoikov(
            QuoteParams(max_inventory=settings.max_inventory),
        )
        resolved_start_bankroll = settings.bankroll_usd
        live_balance = execution.get_collateral_balance()
        if live_balance is not None:
            resolved_start_bankroll = live_balance
            LOG.info("resolved_live_collateral_balance=%s", round(live_balance, 4))
        elif execution.paper:
            resolved_start_bankroll = settings.bankroll_usd
        self.state = EngineState(
            candle=CandleState(),
            up_state=SideState(),
            down_state=SideState(),
            bankroll=resolved_start_bankroll,
        )
        self.breaker = CircuitBreaker(settings.max_consecutive_post_fails)
        self.drawdown = DrawdownGuard(resolved_start_bankroll, settings.max_daily_drawdown_pct)
        self._shutdown = False
        self._seen_trade_ids: set[str] = set()
        self._min_equity_floor = resolved_start_bankroll * settings.min_equity_floor_pct
        self._status_log_seconds = max(1, settings.status_log_seconds)
        self._last_status_log_ts = 0.0
        self._candle_buy_count = 0
        self._candle_sell_count = 0
        self._candle_start_bankroll = resolved_start_bankroll
        self._last_risk_log_ts = 0.0

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
        previous_candle = self.state.candle
        if previous_candle.active and previous_candle.market is not None:
            candle_pnl = self.state.bankroll - self._candle_start_bankroll
            LOG.info(
                "CANDLE RESULT | market='%s' | buys=%s sells=%s | pnl=%s | bankroll=%s",
                previous_candle.market.question,
                self._candle_buy_count,
                self._candle_sell_count,
                round(candle_pnl, 4),
                round(self.state.bankroll, 4),
            )
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
        self._candle_buy_count = 0
        self._candle_sell_count = 0
        self._candle_start_bankroll = self.state.bankroll
        self.market_data.refresh_books(market)
        prediction = self.predictor.predict_for_candle(candle_start)
        self.state.candle.ml_direction = prediction.direction
        self.state.candle.ml_confidence = prediction.confidence
        await self.market_data.start_market_ws(market)
        LOG.info(
            "NEW CANDLE | market='%s' | start=%s | model=%s | confidence=%s",
            market.question,
            datetime.fromtimestamp(candle_start, tz=timezone.utc).isoformat(),
            self.state.candle.ml_direction,
            round(self.state.candle.ml_confidence, 4),
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

            # Prefer WS book updates; fall back to REST refresh only when stale/disconnected.
            now = time.time()
            up_age = now - self.market_data.books.up.last_update_ts if self.market_data.books.up.last_update_ts else 999
            down_age = now - self.market_data.books.down.last_update_ts if self.market_data.books.down.last_update_ts else 999
            if (not self.market_data.books.ws_connected) or up_age > 2 or down_age > 2:
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
                self._log_risk_stop("daily drawdown hit")
                continue
            if self.state.bankroll < self._min_equity_floor:
                self.execution.cancel_all()
                self._log_risk_stop(f"equity floor hit floor={round(self._min_equity_floor, 4)}")
                continue

            await self._quote_side("up")
            await self._quote_side("down")
            self._log_live_trade_events(candle.market.up_token, candle.market.down_token)
            self._maybe_log_status(candle)

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
            bankroll=self.state.bankroll,
            inventory=side_state.position,
            sigma_logit=sigma,
            time_remaining=time_remaining,
            ml_direction=candle.ml_direction,
            ml_confidence=candle.ml_confidence,
        )
        if decision is None:
            return

        token_id = candle.market.up_token if side_name == "up" else candle.market.down_token
        await self._replace_quotes(
            side_name,
            side_state,
            token_id,
            candle.market.tick_size if candle.market else "0.01",
            decision,
        )
        if self.execution.paper:
            self._simulate_paper_fills(side_name, side_state, book, decision)

    async def _replace_quotes(
        self,
        side_name: str,
        state: SideState,
        token_id: str,
        tick_size: str,
        decision: QuoteDecision,
    ) -> None:
        if state.open_bid:
            self.execution.cancel_order(state.open_bid.order_id)
        if state.open_ask:
            self.execution.cancel_order(state.open_ask.order_id)

        bid_price = self._quantize_price(decision.bid_price, tick_size, "down")
        ask_price = self._quantize_price(decision.ask_price, tick_size, "up")
        max_affordable_size = self._max_affordable_size(bid_price)
        bid_size = min(decision.bid_size, max_affordable_size)
        ask_size = decision.ask_size
        if bid_size < 1:
            # No buying power for this side right now.
            state.open_bid = None
            state.open_ask = None
            return
        bid_quote = Quote(side="BUY", token_id=token_id, price=bid_price, size=bid_size)
        ask_quote = Quote(side="SELL", token_id=token_id, price=ask_price, size=ask_size)

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

    def _simulate_paper_fills(self, side_name: str, state: SideState, book, decision: QuoteDecision) -> None:
        if not self._book_is_sane(book):
            return
        if state.open_bid and book.best_ask <= state.open_bid.price:
            fill_cost = state.open_bid.price * state.open_bid.size
            if fill_cost > self.state.bankroll:
                return
            state.position += state.open_bid.size
            state.cost_basis += fill_cost
            self.state.bankroll -= fill_cost
            self._candle_buy_count += 1
            LOG.info(
                "ACTION BUY | %s | price=%s size=%s cost=%s | bankroll=%s",
                side_name.upper(),
                state.open_bid.price,
                state.open_bid.size,
                round(fill_cost, 4),
                round(self.state.bankroll, 4),
            )
            state.open_bid = None
        if state.open_ask and state.position > 0 and book.best_bid >= state.open_ask.price:
            sell_size = min(state.open_ask.size, state.position)
            avg = state.cost_basis / state.position if state.position > 0 else state.open_ask.price
            pnl = (state.open_ask.price - avg) * sell_size
            state.position -= sell_size
            state.cost_basis = max(0.0, state.cost_basis - avg * sell_size)
            self.state.bankroll += state.open_ask.price * sell_size
            self._candle_sell_count += 1
            LOG.info(
                "ACTION SELL | %s | sold=%s avg_buy=%s size=%s spread=%s pnl=%s | bankroll=%s",
                side_name.upper(),
                state.open_ask.price,
                round(avg, 4),
                sell_size,
                round(state.open_ask.price - avg, 4),
                round(pnl, 4),
                round(self.state.bankroll, 4),
            )
            state.open_ask = None

    async def _inventory_loop(self) -> None:
        while not self._shutdown:
            await asyncio.sleep(2)
            if not self.state.candle.active:
                continue
            if abs(self.state.up_state.position) > self.settings.max_inventory:
                LOG.warning("RISK overfill_detected side=UP position=%s", round(self.state.up_state.position, 4))
                if self.state.up_state.open_bid:
                    self.execution.cancel_order(self.state.up_state.open_bid.order_id)
                    self.state.up_state.open_bid = None
            if abs(self.state.down_state.position) > self.settings.max_inventory:
                LOG.warning("RISK overfill_detected side=DOWN position=%s", round(self.state.down_state.position, 4))
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
        LOG.info("RISK positions_flattened market='%s'", candle.market.question)

    @staticmethod
    def _mid_price(best_bid: float, best_ask: float) -> float:
        if best_bid <= 0 or best_ask <= 0:
            return 0.0
        return (best_bid + best_ask) / 2.0

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

    def _log_live_trade_events(self, up_token: str, down_token: str) -> None:
        if self.execution.paper:
            return
        for trade in self.execution.get_trades():
            trade_id = str(trade.get("id") or trade.get("tradeID") or trade.get("trade_id") or "")
            if not trade_id or trade_id in self._seen_trade_ids:
                continue
            self._seen_trade_ids.add(trade_id)
            token_id = str(trade.get("asset_id") or trade.get("token_id") or "")
            if token_id not in {up_token, down_token}:
                continue
            side = str(trade.get("side", ""))
            price = trade.get("price", "")
            size = trade.get("size", "")
            label = "UP" if token_id == up_token else "DOWN"
            LOG.info(
                "LIVE_TRADE %s side=%s price=%s size=%s trade_id=%s",
                label,
                side,
                price,
                size,
                trade_id,
            )

    def _maybe_log_status(self, candle: CandleState) -> None:
        now = time.time()
        if now - self._last_status_log_ts < self._status_log_seconds:
            return
        self._last_status_log_ts = now
        elapsed = int(now - candle.candle_start)
        remaining = max(0, self.settings.exit_deadline_sec - elapsed)
        up_bid = round(self.market_data.books.up.best_bid, 4)
        up_ask = round(self.market_data.books.up.best_ask, 4)
        down_bid = round(self.market_data.books.down.best_bid, 4)
        down_ask = round(self.market_data.books.down.best_ask, 4)
        up_mid = round(self._mid_price(self.market_data.books.up.best_bid, self.market_data.books.up.best_ask), 4)
        down_mid = round(self._mid_price(self.market_data.books.down.best_bid, self.market_data.books.down.best_ask), 4)
        up_buy = self.market_data.fetch_price(candle.market.up_token, "buy")
        up_sell = self.market_data.fetch_price(candle.market.up_token, "sell")
        down_buy = self.market_data.fetch_price(candle.market.down_token, "buy")
        down_sell = self.market_data.fetch_price(candle.market.down_token, "sell")
        up_buy_str = f"{up_buy:.4f}" if up_buy is not None else "n/a"
        up_sell_str = f"{up_sell:.4f}" if up_sell is not None else "n/a"
        down_buy_str = f"{down_buy:.4f}" if down_buy is not None else "n/a"
        down_sell_str = f"{down_sell:.4f}" if down_sell is not None else "n/a"
        LOG.info(
            "STATUS | t_left=%ss | model=%s conf=%s | UI UP buy/sell=%s/%s DOWN buy/sell=%s/%s | BOOK UP bid/ask/mid=%s/%s/%s DOWN bid/ask/mid=%s/%s/%s | inv_up=%s inv_down=%s | bankroll=%s",
            remaining,
            candle.ml_direction,
            round(candle.ml_confidence, 3),
            up_buy_str,
            up_sell_str,
            down_buy_str,
            down_sell_str,
            up_bid,
            up_ask,
            up_mid,
            down_bid,
            down_ask,
            down_mid,
            round(self.state.up_state.position, 4),
            round(self.state.down_state.position, 4),
            round(self.state.bankroll, 4),
        )

    @staticmethod
    def _quantize_price(price: float, tick_size: str, direction: str) -> float:
        try:
            tick = float(tick_size)
        except Exception:
            tick = 0.01
        if tick <= 0:
            tick = 0.01
        steps = price / tick
        if direction == "up":
            q = math.ceil(steps) * tick
        else:
            q = math.floor(steps) * tick
        q = max(0.01, min(0.99, q))
        decimals = max(2, len(str(tick).split(".")[-1]) if "." in str(tick) else 2)
        return round(q, min(decimals, 4))

    @staticmethod
    def _book_is_sane(book) -> bool:
        if book.best_bid <= 0 or book.best_ask <= 0:
            return False
        if book.best_ask < book.best_bid:
            return False
        # Ignore obviously broken one-tick anomalies in paper simulation.
        if book.best_ask <= 0.01 and book.best_bid >= 0.4:
            return False
        return True

    def _max_affordable_size(self, buy_price: float) -> float:
        if buy_price <= 0:
            return 0.0
        # Keep 5% buffer so paper/live never overdraws bankroll.
        return max(0.0, (self.state.bankroll * 0.95) / buy_price)

    def _log_risk_stop(self, reason: str) -> None:
        now = time.time()
        if now - self._last_risk_log_ts < 5:
            return
        self._last_risk_log_ts = now
        LOG.error("RISK STOP | %s | bankroll=%s", reason, round(self.state.bankroll, 4))
