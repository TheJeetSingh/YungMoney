from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from polymarket_bot.ml.legacy_features import engineer_features, get_segment_id

LOG = logging.getLogger(__name__)

# Legacy model compatibility: these segment models were trained on Kraken 5m OHLCV.
# Polymarket settlement may reference Chainlink, but swapping this feed would change
# feature distributions and invalidate model behavior.
KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"


@dataclass
class Prediction:
    direction: str
    confidence: float
    prob_up: float


class LegacySegmentPredictor:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self.models: dict[int, object] = {}
        self.feature_cols: list[str] = []
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        feature_cols_path = self.model_dir / "feature_cols.json"
        if feature_cols_path.exists():
            with open(feature_cols_path) as handle:
                self.feature_cols = json.load(handle)
        for model_path in sorted(self.model_dir.glob("seg_*.pkl")):
            seg_id = int(model_path.stem.split("_")[1])
            with open(model_path, "rb") as handle:
                data = pickle.load(handle)
            if isinstance(data, dict) and "model" in data:
                self.models[seg_id] = data["model"]
                if not self.feature_cols:
                    self.feature_cols = data.get("feature_cols", [])
            else:
                self.models[seg_id] = data
        self._loaded = True
        LOG.info("legacy_models_loaded count=%s path=%s", len(self.models), self.model_dir)

    def predict_for_candle(self, candle_start: int) -> Prediction:
        self.load()
        if not self.models or not self.feature_cols:
            return Prediction(direction="UP", confidence=0.0, prob_up=0.5)

        candle_dt = datetime.fromtimestamp(candle_start, tz=timezone.utc)
        seg_id = get_segment_id(candle_dt)
        model = self.models.get(seg_id)
        if model is None:
            return Prediction(direction="UP", confidence=0.0, prob_up=0.5)

        df = self._fetch_kraken_5m()
        if df is None or df.empty:
            return Prediction(direction="UP", confidence=0.0, prob_up=0.5)
        df = engineer_features(df)
        if not self.feature_cols:
            return Prediction(direction="UP", confidence=0.0, prob_up=0.5)
        # Keep legacy model shape exactly: add any missing training columns as zeros.
        for col in self.feature_cols:
            if col not in df.columns:
                df[col] = 0.0
        df[self.feature_cols] = df[self.feature_cols].fillna(0).replace([np.inf, -np.inf], 0)
        row = df.iloc[-1:][self.feature_cols]
        try:
            prob_up = float(model.predict_proba(row)[0, 1])
        except Exception as exc:
            LOG.warning("legacy_predict_failed: %s", exc)
            return Prediction(direction="UP", confidence=0.0, prob_up=0.5)
        confidence = abs(prob_up - 0.5) * 2.0
        direction = "UP" if prob_up >= 0.5 else "DOWN"
        return Prediction(direction=direction, confidence=confidence, prob_up=prob_up)

    @staticmethod
    def _fetch_kraken_5m() -> pd.DataFrame | None:
        try:
            response = requests.get(
                KRAKEN_OHLC_URL,
                params={"pair": "XBTUSD", "interval": 5},
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload["result"]["XXBTZUSD"]
            mapped = [
                {
                    "open": float(candle[1]),
                    "high": float(candle[2]),
                    "low": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[6]),
                }
                for candle in rows
            ]
            frame = pd.DataFrame(mapped)
            frame.index = pd.to_datetime([int(candle[0]) for candle in rows], unit="s", utc=True)
            return frame.sort_index()
        except Exception as exc:
            LOG.warning("kraken_ohlc_fetch_failed: %s", exc)
            return None
