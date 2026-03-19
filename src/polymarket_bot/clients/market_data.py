from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass

import requests
import websockets

from polymarket_bot.types import BookState, MarketInfo

LOG = logging.getLogger(__name__)

MARKET_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
TARGET_SLUG_PREFIX = "btc-updown-5m"
TARGET_QUESTION_PATTERN = re.compile(r"bitcoin up or down.*(5m|5 min|5-minute)", re.IGNORECASE)


@dataclass
class TokenBooks:
    up: BookState
    down: BookState
    ws_connected: bool = False


class MarketDataClient:
    def __init__(self, gamma_host: str, clob_host: str, market_tag: str) -> None:
        self.gamma_host = gamma_host.rstrip("/")
        self.clob_host = clob_host.rstrip("/")
        self.market_tag = market_tag
        self.session = requests.Session()
        self._market_cache: MarketInfo | None = None
        self._market_cache_ts = 0.0
        self.books = TokenBooks(up=BookState(), down=BookState(), ws_connected=False)
        self._ws_task: asyncio.Task | None = None
        self._stop = False

    def find_active_btc_5m_market(self) -> MarketInfo | None:
        now = time.time()
        if self._market_cache and now - self._market_cache_ts < 20:
            return self._market_cache
        try:
            resp = self.session.get(
                f"{self.gamma_host}/markets",
                params={
                    "tag": self.market_tag,
                    "active": "true",
                    "closed": "false",
                    "limit": 100,
                    "order": "startDate",
                    "ascending": "false",
                },
                timeout=8,
            )
            resp.raise_for_status()
            markets = resp.json()
            for market in markets:
                question = market.get("question", "")
                slug = str(market.get("slug", "")).lower()
                if not self._is_target_market(question, slug):
                    continue
                tokens = market.get("clobTokenIds", [])
                outcomes = market.get("outcomes", [])
                if isinstance(tokens, str):
                    tokens = json.loads(tokens) if tokens else []
                if isinstance(outcomes, str):
                    outcomes = json.loads(outcomes) if outcomes else []
                if len(tokens) < 2:
                    continue
                up_idx, down_idx = self._resolve_up_down_indices(outcomes)
                result = MarketInfo(
                    question=question,
                    condition_id=market.get("conditionId", ""),
                    up_token=str(tokens[up_idx]),
                    down_token=str(tokens[down_idx]),
                    neg_risk=bool(market.get("negRisk", False)),
                    tick_size=str(market.get("minimum_tick_size") or "0.01"),
                )
                self._market_cache = result
                self._market_cache_ts = now
                LOG.info(
                    "MARKET_SELECTED question='%s' up_token=%s down_token=%s tick=%s",
                    result.question,
                    result.up_token,
                    result.down_token,
                    result.tick_size,
                )
                return result
        except Exception as exc:
            LOG.warning("market_discovery_failed: %s", exc)
        return None

    def fetch_book(self, token_id: str) -> BookState:
        try:
            response = self.session.get(
                f"{self.clob_host}/book",
                params={"token_id": token_id},
                timeout=3,
            )
            response.raise_for_status()
            payload = response.json()
            bids = [(float(level["price"]), float(level["size"])) for level in payload.get("bids", [])]
            asks = [(float(level["price"]), float(level["size"])) for level in payload.get("asks", [])]
            bids.sort(key=lambda x: x[0], reverse=True)
            asks.sort(key=lambda x: x[0])
            best_bid = bids[0][0] if bids else 0.0
            best_ask = asks[0][0] if asks else 1.0
            return BookState(
                best_bid=best_bid,
                best_ask=best_ask,
                bids=bids,
                asks=asks,
                last_update_ts=time.time(),
            )
        except Exception as exc:
            LOG.warning("fetch_book_failed token=%s error=%s", token_id, exc)
            return BookState()

    def refresh_books(self, market: MarketInfo) -> TokenBooks:
        self.books.up = self.fetch_book(market.up_token)
        self.books.down = self.fetch_book(market.down_token)
        return self.books

    async def start_market_ws(self, market: MarketInfo) -> None:
        if self._ws_task and not self._ws_task.done():
            return
        self._stop = False
        self._ws_task = asyncio.create_task(self._run_market_ws(market))

    async def stop_market_ws(self) -> None:
        self._stop = True
        if self._ws_task:
            await asyncio.wait([self._ws_task], timeout=1.0)

    async def _run_market_ws(self, market: MarketInfo) -> None:
        subscribe = {
            "assets_ids": [market.up_token, market.down_token],
            "type": "market",
        }
        while not self._stop:
            try:
                async with websockets.connect(MARKET_WS, ping_interval=15, ping_timeout=15) as ws:
                    await ws.send(json.dumps(subscribe))
                    self.books.ws_connected = True
                    async for raw in ws:
                        self._handle_ws_message(raw, market)
                        if self._stop:
                            break
            except Exception as exc:
                self.books.ws_connected = False
                LOG.warning("market_ws_error: %s", exc)
                await asyncio.sleep(2)
            finally:
                self.books.ws_connected = False

    def _handle_ws_message(self, raw: str, market: MarketInfo) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return

        if isinstance(message, list):
            for item in message:
                self._apply_book_delta(item, market)
            return
        self._apply_book_delta(message, market)

    def _apply_book_delta(self, item: dict, market: MarketInfo) -> None:
        asset_id = str(item.get("asset_id", ""))
        bids = item.get("bids")
        asks = item.get("asks")
        if asset_id not in {market.up_token, market.down_token}:
            return
        target = self.books.up if asset_id == market.up_token else self.books.down

        if isinstance(bids, list):
            target.bids = self._normalize_levels(bids, side="bids")
            target.best_bid = target.bids[0][0] if target.bids else target.best_bid
        if isinstance(asks, list):
            target.asks = self._normalize_levels(asks, side="asks")
            target.best_ask = target.asks[0][0] if target.asks else target.best_ask
        target.last_update_ts = time.time()

    @staticmethod
    def _normalize_levels(levels: list[dict], side: str) -> list[tuple[float, float]]:
        normalized = []
        for level in levels:
            try:
                normalized.append((float(level["price"]), float(level["size"])))
            except (TypeError, KeyError, ValueError):
                continue
        if side == "bids":
            normalized.sort(key=lambda x: x[0], reverse=True)
        else:
            normalized.sort(key=lambda x: x[0])
        return normalized

    @staticmethod
    def _is_target_market(question: str, slug: str) -> bool:
        if slug.startswith(TARGET_SLUG_PREFIX):
            return True
        return bool(TARGET_QUESTION_PATTERN.search(question or ""))

    @staticmethod
    def _resolve_up_down_indices(outcomes: list) -> tuple[int, int]:
        if not outcomes:
            return 0, 1
        normalized = [str(item).strip().lower() for item in outcomes]
        try:
            up_idx = normalized.index("up")
            down_idx = normalized.index("down")
            return up_idx, down_idx
        except ValueError:
            # Fallback to first two outcomes if labels are unexpected.
            return 0, 1 if len(outcomes) > 1 else 0
